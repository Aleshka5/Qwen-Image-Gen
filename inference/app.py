"""HTTP-слой контейнера инференса: ``POST /v1/generate`` -> SSE, ``GET /healthz``.

Ответ на генерацию — поток событий:

    event: start   {"model": "...", "memory_mode": "offload"}
    event: progress {"stage": 2, "total": 10, "name": "prepare"}
    event: log     {"level": "INFO", "message": "pipeline «denoise» 3/4: ..."}
    ...
    event: done    {"seed": 7, "duration": 4.2, "count": 1, "images": ["<base64 PNG>"]}

или ``event: error`` с ``{"kind": "pipeline"|"internal", "error": "..."}``.
Картинки уезжают base64 потому что событие — единственный канал ответа: второй
запрос за файлом означал бы второй job на той же карте.

Пайплайн не знает про HTTP: его строки ловит :mod:`inference.logstream` и
кладёт в очередь запроса, а SSE-генератор читает очередь. Поэтому длинная
генерация не блокирует отдачу логов: работа идёт в отдельном потоке, а очередь
отдаёт keepalive, пока шаг считается.
"""

from __future__ import annotations

import base64
import contextvars
import io
import json
import logging
import threading
from typing import Any, Callable, Iterator

from flask import Flask, Response, jsonify, request
from PIL import Image, UnidentifiedImageError

from .config import settings
from .logstream import STAGES, STOP, LogStream, Progress, bind, install_handler
from .pipeline.types import GenerationRequest, GenerationResult, PipelineError

log = logging.getLogger(__name__)

#: Как часто поток напоминает о себе, пока шаг считается (секунды).
KEEPALIVE_S = 15.0

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    # nginx и подобные буферизуют ответ по умолчанию: с этим заголовком — нет.
    "X-Accel-Buffering": "no",
}


def _configure_logging() -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    # basicConfig молчит, если у корневого логгера уже есть обработчик (так бывает
    # под pytest и под любым хостом, настроившим logging раньше нас). Уровень
    # ставим явно: иначе INFO пайплайна отфильтруется ещё в логгере-источнике.
    logging.getLogger().setLevel(settings.log_level)


def _default_generator() -> Any:
    """Импорт torch откладывается до первого запроса: /healthz должен отвечать сразу."""
    from .pipeline.generator import QwenImageGenerator

    return QwenImageGenerator()


def _event(name: str, payload: dict[str, Any]) -> str:
    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _decode(raw: bytes, name: str) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"файл «{name}» не читается как изображение") from exc
    return image.convert("RGB")


def _int(form: Any, field: str, *, low: int, high: int) -> int:
    raw = (form.get(field) or "").strip()
    if not raw:
        raise ValueError(f"Поле «{field}» обязательно")
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"Поле «{field}»: ожидается целое число") from exc
    if not low <= value <= high:
        raise ValueError(f"Поле «{field}»: допустим диапазон {low}–{high}")
    return value


def _request_from_form() -> GenerationRequest:
    form = request.form
    prompt = (form.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("Поле «prompt» не может быть пустым")
    width = _int(form, "width", low=32, high=8192)
    height = _int(form, "height", low=32, high=8192)
    steps = _int(form, "steps", low=1, high=1000)
    raw_seed = (form.get("seed") or "").strip()
    seed = int(raw_seed) if raw_seed else 0
    raw_cfg = (form.get("true_cfg_scale") or "").strip()
    true_cfg_scale = float(raw_cfg) if raw_cfg else 1.0
    uploads = request.files.getlist("images")
    if len(uploads) > settings.max_images:
        raise ValueError(f"Не больше {settings.max_images} входных изображений")
    images = [_decode(upload.read(), upload.filename or "image") for upload in uploads]
    return GenerationRequest(
        prompt=prompt,
        negative_prompt=(form.get("negative_prompt") or "").strip(),
        width=width,
        height=height,
        steps=steps,
        true_cfg_scale=true_cfg_scale,
        seed=seed,
        images=images,
    )


def _stream(get_generator: Callable[[], Any], generation: GenerationRequest) -> Iterator[str]:
    stream = LogStream()
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["result"] = get_generator().generate(generation)
        except PipelineError as exc:
            box["error"] = ("pipeline", str(exc))
        except Exception as exc:  # noqa: BLE001 - поток обязан закрыться событием
            log.exception("инференс: необработанная ошибка генерации")
            box["error"] = ("internal", f"{type(exc).__name__}: {exc}")
        finally:
            stream.close()

    with bind(stream):
        log.info(
            "инференс: %dx%d, steps=%d, cfg=%.1f, seed=%d, входных фото=%d",
            generation.width,
            generation.height,
            generation.steps,
            generation.true_cfg_scale,
            generation.seed,
            len(generation.images),
        )
        yield _event("start", {"model": settings.model_id, "memory_mode": settings.memory_mode})
        # Стадия 1 — запрос принят; стадию 10 отдаём вместе с done.
        yield _event("progress", {"stage": 1, "total": STAGES, "name": "start"})

        # Контекст копируется внутри bind: логи потока генерации попадут в тот же
        # LogStream, без глобального реестра «чей сейчас запрос».
        context = contextvars.copy_context()
        worker = threading.Thread(target=context.run, args=(run,), name="generate", daemon=True)
        worker.start()
        try:
            while True:
                item = stream.take(timeout=KEEPALIVE_S)
                if item is None:
                    yield ": keepalive\n\n"
                    continue
                if item is STOP:
                    break
                if isinstance(item, Progress):
                    yield _event("progress", item.payload())
                    continue
                level, message = item  # type: ignore[misc]
                yield _event("log", {"level": level, "message": message})
            worker.join(timeout=5.0)
        finally:
            stream.close()

    error = box.get("error")
    if error is not None:
        kind, message = error
        yield _event("error", {"kind": kind, "error": message})
        return

    result: GenerationResult | None = box.get("result")
    if result is None:  # pragma: no cover - страховка от пустого прогона
        yield _event("error", {"kind": "internal", "error": "генерация не вернула результат"})
        return
    images = [base64.b64encode(_png_bytes(image)).decode("ascii") for image in result.images]
    yield _event("progress", {"stage": 10, "total": STAGES, "name": "done"})
    yield _event(
        "done",
        {
            "seed": result.seed,
            "duration": round(result.duration, 2),
            "count": len(images),
            "images": images,
        },
    )


def create_app(generator_factory: Callable[[], Any] | None = None) -> Flask:
    """Приложение инференса. Фабрику подменяют тесты: без неё нужен torch и GPU."""
    _configure_logging()
    install_handler()

    factory = generator_factory or _default_generator
    state: dict[str, Any] = {"generator": None}
    guard = threading.Lock()

    def get_generator() -> Any:
        with guard:
            if state["generator"] is None:
                state["generator"] = factory()
            return state["generator"]

    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = settings.max_content_length

    @app.get("/healthz")
    def healthz():
        try:
            pipeline = get_generator()
        except Exception as exc:  # noqa: BLE001 - о неудаче сообщаем телом ответа
            return (
                jsonify(
                    status="error",
                    model=settings.model_id,
                    memory_mode=settings.memory_mode,
                    loaded=False,
                    load_error=f"{type(exc).__name__}: {exc}",
                ),
                503,
            )
        return jsonify(
            status="ok",
            model=settings.model_id,
            memory_mode=settings.memory_mode,
            loaded=pipeline.is_loaded,
            load_error=pipeline.load_error,
            accepts_images=pipeline.accepts_images,
        )

    @app.post("/v1/generate")
    def generate():
        try:
            generation = _request_from_form()
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        return Response(_stream(get_generator, generation), mimetype="text/event-stream", headers=_SSE_HEADERS)

    @app.errorhandler(Exception)
    def _on_unexpected(exc: Exception):
        log.exception("инференс: необработанная ошибка")
        return jsonify(error=f"Внутренняя ошибка: {type(exc).__name__}"), 500

    if settings.preload_model:
        threading.Thread(
            target=_preload,
            args=(get_generator,),
            name="model-preload",
            daemon=True,
        ).start()

    return app


def _preload(get_generator: Callable[[], Any]) -> None:
    """Прогрев весов в фоне: первый запрос не ждёт загрузку, а /healthz уже отвечает."""
    try:
        get_generator().ensure_loaded()
    except Exception:
        log.exception("Предзагрузка модели не удалась; повтор произойдёт на первом запросе")
