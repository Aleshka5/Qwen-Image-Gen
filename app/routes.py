"""HTTP-слой: веб-форма и JSON API."""

from __future__ import annotations

import logging
import random
import re
import secrets

from flask import Blueprint, jsonify, render_template, request, send_file, url_for

from .config import settings
from .generator import GenerationRequest, PipelineError, generator
from .imaging import (
    RESOLUTION_PRESETS,
    ValidationError,
    load_uploads,
    output_path,
    parse_resolution,
    save_outputs,
)

log = logging.getLogger(__name__)

bp = Blueprint("web", __name__)

_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_MAX_PROMPT_CHARS = 2000
_SEED_MAX = 2**31 - 1


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
        presets=RESOLUTION_PRESETS,
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


@bp.post("/api/generate")
def api_generate():
    form = request.form
    prompt = _clean_prompt(form.get("prompt"), field="prompt", required=True)
    negative = _clean_prompt(form.get("negative_prompt"), field="negative prompt", required=False)
    width, height = parse_resolution(form.get("resolution"))
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
    return jsonify(
        seed=result.seed,
        duration=round(result.duration, 2),
        width=width,
        height=height,
        images=[{"name": n, "url": url_for("web.output", name=n)} for n in names],
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
