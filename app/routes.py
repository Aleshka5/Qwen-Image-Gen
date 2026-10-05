"""HTTP-слой: веб-форма и JSON API."""

from __future__ import annotations

import io
import logging
import random
import re
import secrets

from flask import Blueprint, jsonify, render_template, request, send_file, url_for

from .config import settings
from .generator import GenerationRequest, PipelineError, generator
from .logbuf import live_logs
from .webstorage import StorageError, save_generated
from .imaging import (
    ASPECT_RATIOS,
    GEN_MAX_SIDE,
    GEN_MIN_SIDE,
    RESOLUTION_PRESETS,
    ValidationError,
    load_uploads,
    output_path,
    parse_custom_size,
    parse_resolution,
    prepare_photo,
    save_outputs,
    size_from_quality,
)
from .maskfill import prepare_mask_fill

log = logging.getLogger(__name__)

bp = Blueprint("web", __name__)

_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_MAX_PROMPT_CHARS = 2000
_SEED_MAX = 2**31 - 1
_USER_ID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_NOT_SAVED_NO_USER = "not saved: no user id"


def _clean_prompt(raw: str | None, *, field: str, required: bool) -> str:
    prompt = (raw or "").strip()
    if not prompt:
        if required:
            raise ValidationError(f"Поле «{field}» не может быть пустым")
        return ""
    if len(prompt) > _MAX_PROMPT_CHARS:
        raise ValidationError(f"Поле «{field}»: не больше {_MAX_PROMPT_CHARS} символов")
    if _CYRILLIC.search(prompt):
        raise ValidationError(f"Поле «{field}» должно быть на английском языке")
    return prompt


def _parse_int(raw: str | None, *, field: str, default: int, low: int, high: int) -> int:
    raw = (raw or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValidationError(f"Поле «{field}»: ожидается целое число") from exc
    if not low <= value <= high:
        raise ValidationError(f"Поле «{field}»: допустим диапазон {low}–{high}")
    return value


def _parse_seed(raw: str | None) -> int:
    raw = (raw or "").strip()
    if not raw or raw == "-1":
        return secrets.randbelow(_SEED_MAX)
    return _parse_int(raw, field="seed", default=0, low=0, high=_SEED_MAX)


def _valid_user_id(raw: str | None) -> str | None:
    value = (raw or "").strip()
    if _USER_ID.fullmatch(value):
        return value
    return None


def _optional_text(raw: str | None) -> str | None:
    value = (raw or "").strip()
    return value or None


def _png_bytes(image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _generation_size(form) -> tuple[int, int]:
    """Пресет (качество + соотношение), свои H/W, либо старое поле resolution."""
    mode = (form.get("size_mode") or "").strip().lower()
    if mode == "custom":
        return parse_custom_size(form.get("width"), form.get("height"))
    if mode == "preset":
        return size_from_quality(form.get("quality"), form.get("aspect"))
    return parse_resolution(form.get("resolution"))


def _parse_cfg(raw: str | None) -> float:
    raw = (raw or "").strip()
    if not raw:
        return settings.default_true_cfg_scale
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValidationError("Поле «CFG»: ожидается число") from exc
    if not 1.0 <= value <= 10.0:
        raise ValidationError("Поле «CFG»: допустим диапазон 1.0–10.0")
    return value


@bp.get("/")
def index() -> str:
    return render_template(
        "index.html",
        aspects=ASPECT_RATIOS,
        gen_min_side=GEN_MIN_SIDE,
        gen_max_side=GEN_MAX_SIDE,
        max_images=settings.max_images,
        default_steps=settings.default_steps,
        max_steps=settings.max_steps,
        default_cfg=settings.default_true_cfg_scale,
        model_id=settings.model_id,
    )


@bp.get("/customize")
def customize() -> str:
    return render_template(
        "customize.html",
        default_steps=settings.default_steps,
        max_steps=settings.max_steps,
        default_cfg=settings.default_true_cfg_scale,
        model_id=settings.model_id,
    )


@bp.get("/mask-fill")
def mask_fill() -> str:
    return render_template(
        "mask_fill.html",
        max_images=settings.max_images,
        default_steps=settings.default_steps,
        max_steps=settings.max_steps,
        default_cfg=settings.default_true_cfg_scale,
        model_id=settings.model_id,
    )


@bp.get("/healthz")
def healthz():
    return jsonify(
        status="ok",
        model=settings.model_id,
        memory_mode=settings.memory_mode,
        loaded=generator.is_loaded,
        load_error=generator.load_error,
    )


@bp.get("/api/config")
def api_config():
    return jsonify(
        model=settings.model_id,
        max_images=settings.max_images,
        max_upload_mb=settings.max_upload_mb,
        max_steps=settings.max_steps,
        accepts_images=generator.accepts_images,
        presets=[
            {"key": p.key, "label": p.label, "width": p.width, "height": p.height}
            for p in RESOLUTION_PRESETS
        ],
    )


@bp.get("/api/logs")
def api_logs():
    raw = (request.args.get("after") or "0").strip()
    try:
        after = int(raw)
    except ValueError:
        after = 0
    if after < 0:
        after = 0
    return jsonify(lines=live_logs().since(after))


@bp.post("/api/generate")
def api_generate():
    form = request.form
    prompt = _clean_prompt(form.get("prompt"), field="prompt", required=True)
    negative = _clean_prompt(form.get("negative_prompt"), field="negative prompt", required=False)
    width, height = _generation_size(form)
    steps = _parse_int(
        form.get("steps"), field="steps", default=settings.default_steps, low=1, high=settings.max_steps
    )
    cfg = _parse_cfg(form.get("true_cfg_scale"))
    seed = _parse_seed(form.get("seed"))
    images = load_uploads(request.files.getlist("images"))

    log.info(
        "Генерация: %dx%d, steps=%d, cfg=%.1f, seed=%d, входных фото=%d",
        width, height, steps, cfg, seed, len(images),
    )

    result = generator.generate(
        GenerationRequest(
            prompt=prompt,
            negative_prompt=negative or settings.default_negative_prompt,
            width=width,
            height=height,
            steps=steps,
            true_cfg_scale=cfg,
            seed=seed,
            images=images,
        )
    )

    names = save_outputs(result.images)
    # Идентификатор читается только здесь. Любое другое значение — «нет пользователя».
    user_id = _valid_user_id(request.headers.get("X-Auth-User-Id"))
    if user_id is None:
        stored = {"saved": False, "storage_error": _NOT_SAVED_NO_USER}
    else:
        try:
            storage_id = save_generated(
                user_id=user_id,
                email=_optional_text(request.headers.get("X-Auth-Email")),
                prompt=prompt,
                negative_prompt=negative,
                seed=result.seed,
                steps=steps,
                true_cfg_scale=cfg,
                width=width,
                height=height,
                duration=result.duration,
                images=[_png_bytes(image) for image in images],
                result_png=output_path(names[0]).read_bytes(),
                result_name=names[0],
            )
        except StorageError as exc:
            stored = {"saved": False, "storage_error": exc.reason}
        else:
            stored = {"saved": True, "storage_id": storage_id}
    return jsonify(
        seed=result.seed,
        duration=round(result.duration, 2),
        width=width,
        height=height,
        images=[{"name": n, "url": url_for("web.output", name=n)} for n in names],
        **stored,
    )


@bp.post("/api/customize")
def api_customize():
    form = request.form
    prompt = _clean_prompt(form.get("system_prompt"), field="system prompt", required=True)
    negative = _clean_prompt(form.get("negative_prompt"), field="negative prompt", required=False)
    frame = prepare_photo(request.files.getlist("image"))
    steps = _parse_int(
        form.get("steps"), field="steps", default=settings.default_steps, low=1, high=settings.max_steps
    )
    cfg = _parse_cfg(form.get("true_cfg_scale"))
    seed = _parse_seed(form.get("seed"))

    log.info(
        "Кастомизация: фото %dx%d, модель %dx%d, steps=%d, cfg=%.1f, seed=%d",
        frame.width,
        frame.height,
        frame.model_width,
        frame.model_height,
        steps,
        cfg,
        seed,
    )

    result = generator.generate(
        GenerationRequest(
            prompt=prompt,
            negative_prompt=negative or settings.default_negative_prompt,
            width=frame.model_width,
            height=frame.model_height,
            steps=steps,
            true_cfg_scale=cfg,
            seed=seed,
            images=[frame.image],
        )
    )

    restored = [frame.restore(image) for image in result.images]
    names = save_outputs(restored)
    return jsonify(
        seed=result.seed,
        duration=round(result.duration, 2),
        width=frame.width,
        height=frame.height,
        images=[{"name": n, "url": url_for("web.output", name=n)} for n in names],
    )


@bp.post("/api/mask-fill")
def api_mask_fill():
    form = request.form
    prompt = _clean_prompt(form.get("prompt"), field="prompt", required=True)
    negative = _clean_prompt(form.get("negative_prompt"), field="negative prompt", required=False)
    steps = _parse_int(
        form.get("steps"), field="steps", default=settings.default_steps, low=1, high=settings.max_steps
    )
    cfg = _parse_cfg(form.get("true_cfg_scale"))
    seed = _parse_seed(form.get("seed"))
    frame = prepare_mask_fill(
        request.files.getlist("image"),
        request.files.getlist("mask"),
        request.files.getlist("images"),
    )

    log.info(
        "Заливка: фото %dx%d, модель %dx%d, steps=%d, cfg=%.1f, seed=%d, условий=%d",
        frame.width,
        frame.height,
        frame.model_width,
        frame.model_height,
        steps,
        cfg,
        seed,
        len(frame.images),
    )

    result = generator.generate(
        GenerationRequest(
            prompt=prompt,
            negative_prompt=negative or settings.default_negative_prompt,
            width=frame.model_width,
            height=frame.model_height,
            steps=steps,
            true_cfg_scale=cfg,
            seed=seed,
            images=list(frame.images),
        )
    )

    restored = [frame.restore(image) for image in result.images]
    names = save_outputs(restored)
    user_id = _valid_user_id(request.headers.get("X-Auth-User-Id"))
    if user_id is None:
        stored = {"saved": False, "storage_error": _NOT_SAVED_NO_USER}
    else:
        try:
            storage_id = save_generated(
                user_id=user_id,
                email=_optional_text(request.headers.get("X-Auth-Email")),
                prompt=prompt,
                negative_prompt=negative,
                seed=result.seed,
                steps=steps,
                true_cfg_scale=cfg,
                width=frame.width,
                height=frame.height,
                duration=result.duration,
                images=[_png_bytes(image) for image in frame.archive],
                result_png=output_path(names[0]).read_bytes(),
                result_name=names[0],
            )
        except StorageError as exc:
            stored = {"saved": False, "storage_error": exc.reason}
        else:
            stored = {"saved": True, "storage_id": storage_id}
    return jsonify(
        seed=result.seed,
        duration=round(result.duration, 2),
        width=frame.width,
        height=frame.height,
        images=[{"name": n, "url": url_for("web.output", name=n)} for n in names],
        **stored,
    )


@bp.get("/outputs/<path:name>")
def output(name: str):
    path = output_path(name)
    if not path.is_file():
        return jsonify(error="Файл не найден"), 404
    return send_file(path, mimetype="image/png")


@bp.app_errorhandler(ValidationError)
def _on_validation_error(exc: ValidationError):
    return jsonify(error=str(exc)), 400


@bp.app_errorhandler(PipelineError)
def _on_pipeline_error(exc: PipelineError):
    log.error("Ошибка пайплайна: %s", exc)
    return jsonify(error=str(exc)), 503


@bp.app_errorhandler(413)
def _on_too_large(_exc):
    return jsonify(error=f"Суммарный размер загрузки больше {settings.max_upload_mb} МБ"), 413
