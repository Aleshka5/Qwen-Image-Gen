"""Клиент контейнера инференса: HTTP-запрос и SSE-поток логов обратно.

Torch, CUDA и весов здесь нет — только вызов ``POST /v1/generate`` и построчное
чтение ответа. Логи пайплайна приходят событиями ``log`` и кладутся в тот же
кольцевой буфер, из которого ``GET /api/logs`` кормит страницу, поэтому UI и
протокол браузера не меняются.

Транспорт — stdlib: сервис не тянет requests, как и клиент WebStorage.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from PIL import Image

from .config import settings
from .logbuf import live_logs

log = logging.getLogger(__name__)

#: Сколько живёт снимок ``/healthz``, секунды. UI опрашивает буфер логов часто,
#: а состояние модели меняется раз в загрузку, поэтому запрос на каждый тик не нужен.
HEALTH_TTL = 2.0


class PipelineError(RuntimeError):
    """Инференс не смог выполнить запрос: пайплайн, загрузка весов, недоступность."""


@dataclass(slots=True)
class GenerationRequest:
    prompt: str
    negative_prompt: str
    width: int
    height: int
    steps: int
    true_cfg_scale: float
    seed: int
    images: list[Image.Image]


@dataclass(slots=True)
class GenerationResult:
    images: list[Image.Image]
    seed: int
    duration: float


def _number(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".") or "0"


def _multipart(
    fields: list[tuple[str, str]], files: list[tuple[str, str, bytes]]
) -> tuple[bytes, str]:
    """multipart/form-data без зависимостей: граница держит только ASCII."""
    boundary = f"----qwen{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields:
        chunks.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    for name, filename, payload in files:
        chunks.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
                f'filename="{filename}"\r\nContent-Type: image/png\r\n\r\n'
            ).encode()
            + payload
            + b"\r\n"
        )
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _decode(payload: str) -> Image.Image:
    image = Image.open(io.BytesIO(base64.b64decode(payload)))
    image.load()
    return image.convert("RGB")


def _events(response: Any) -> Iterator[tuple[str, str]]:
    """Разбирает ``text/event-stream`` на пары (event, data). Комментарии — keepalive."""
    name = ""
    data: list[str] = []
    for raw in response:
        line = raw.decode("utf-8").rstrip("\r\n")
        if not line:
            if name:
                yield name, "\n".join(data)
            name, data = "", []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            name = value
        elif field == "data":
            data.append(value)
    if name:  # поток закрылся без завершающей пустой строки
        yield name, "\n".join(data)


def _remote_error(exc: HTTPError) -> str:
    try:
        payload = json.loads(exc.read() or b"{}")
    except (ValueError, OSError):
        return f"инференс ответил {exc.code}"
    message = str(payload.get("error") or "").strip()
    return message or f"инференс ответил {exc.code}"


class InferenceClient:
    """Один инференс-контейнер за HTTP. Состояние модели — снимок ``/healthz``."""

    def __init__(self, base_url: str | None = None, timeout: float | None = None) -> None:
        self.base_url = (base_url or settings.inference_url).rstrip("/")
        self.timeout = float(timeout if timeout is not None else settings.inference_timeout)
        self._guard = threading.Lock()
        self._health: dict[str, Any] = {}
        self._health_at = 0.0

    # ------------------------------------------------------------------ state
    def _snapshot(self, *, refresh: bool = False) -> dict[str, Any]:
        with self._guard:
            fresh = time.monotonic() - self._health_at < HEALTH_TTL
            if fresh and not refresh:
                return self._health
            self._health = self._probe()
            self._health_at = time.monotonic()
            return self._health

    def _probe(self) -> dict[str, Any]:
        try:
            with urlopen(f"{self.base_url}/healthz", timeout=settings.inference_health_timeout) as response:
                payload = json.loads(response.read() or b"{}")
        except HTTPError as exc:  # 503 с телом: загрузка весов упала
            try:
                payload = json.loads(exc.read() or b"{}")
            except ValueError:
                payload = {}
        except (URLError, OSError, ValueError) as exc:
            return {"reachable": False, "loaded": False, "load_error": f"инференс недоступен: {exc}"}
        payload["reachable"] = True
        return payload

    def ensure_loaded(self) -> dict[str, Any]:
        """Проверяет инференс и возвращает его состояние. Веса грузит он сам."""
        health = self._snapshot(refresh=True)
        if health.get("reachable") and not health.get("loaded"):
            log.info("инференс ещё грузит веса: %s", health.get("load_error") or "загрузка идёт")
        return health

    @property
    def is_loaded(self) -> bool:
        return bool(self._snapshot().get("loaded"))

    @property
    def load_error(self) -> str | None:
        error = self._snapshot().get("load_error")
        return str(error) if error else None

    @property
    def accepts_images(self) -> bool:
        return bool(self._snapshot().get("accepts_images"))

    @property
    def memory_mode(self) -> str | None:
        mode = self._snapshot().get("memory_mode")
        return str(mode) if mode else None

    # --------------------------------------------------------------- generate
    def generate(self, request: GenerationRequest) -> GenerationResult:
        images = [
            (f"image-{index}.png", _png_bytes(image))
            for index, image in enumerate(request.images, start=1)
        ]
        body, content_type = _multipart(
            fields=[
                ("prompt", request.prompt),
                ("negative_prompt", request.negative_prompt),
                ("width", str(request.width)),
                ("height", str(request.height)),
                ("steps", str(request.steps)),
                ("true_cfg_scale", _number(request.true_cfg_scale)),
                ("seed", str(request.seed)),
            ],
            files=[("images", name, payload) for name, payload in images],
        )
        outbound = Request(
            f"{self.base_url}/v1/generate",
            data=body,
            method="POST",
            headers={"Content-Type": content_type, "Accept": "text/event-stream"},
        )
        started = time.monotonic()
        try:
            response = urlopen(outbound, timeout=self.timeout)
        except HTTPError as exc:
            raise PipelineError(_remote_error(exc)) from exc
        except (URLError, OSError) as exc:
            raise PipelineError(f"инференс недоступен: {exc}") from exc

        seed = request.seed
        duration: float | None = None
        payloads: list[str] = []
        with response:
            try:
                for event, data in _events(response):
                    if event == "log":
                        entry = json.loads(data)
                        live_logs().push(str(entry.get("level") or "INFO"), str(entry.get("message") or ""))
                    elif event == "done":
                        done = json.loads(data)
                        seed = int(done.get("seed", request.seed))
                        duration = float(done.get("duration") or 0.0)
                        payloads = list(done.get("images") or [])
                    elif event == "error":
                        failure = json.loads(data)
                        raise PipelineError(
                            str(failure.get("error") or "инференс вернул ошибку без описания")
                        )
            except (URLError, OSError) as exc:  # обрыв потока посреди денойза
                raise PipelineError(f"поток инференса оборвался: {exc}") from exc

        if duration is None:
            raise PipelineError("инференс закрыл поток, не прислав результат")
        return GenerationResult(
            images=[_decode(payload) for payload in payloads],
            seed=seed,
            duration=duration or (time.monotonic() - started),
        )


generator = InferenceClient()
