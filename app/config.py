"""Конфигурация сервиса. Все значения читаются из окружения (см. .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

_TRUE = {"1", "true", "yes", "on"}


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in _TRUE


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def _path(name: str, default: str) -> Path:
    path = Path(os.getenv(name, default)).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass(frozen=True)
class Settings:
    """Неизменяемый снимок настроек, создаётся один раз при старте процесса."""

    # --- модель ---------------------------------------------------------
    model_id: str = os.getenv("MODEL_ID", "Qwen/Qwen-Image-2.1")
    model_revision: str | None = os.getenv("MODEL_REVISION") or None
    pipeline_class: str = os.getenv("PIPELINE_CLASS", "auto")

    # --- stages -----------------------------------------------------------
    # The pipeline no longer lives in this process: three HTTP stages own the
    # models and this service only sequences them. See docs/models_inference_api.md.
    output_resolution: int = _int("OUTPUT_RESOLUTION", 1024)
    embedding_url: str = os.getenv("EMBEDDING_URL", "http://qwen-embed:8011").rstrip("/")
    vae_url: str = os.getenv("VAE_URL", "http://qwen-vae:8012").rstrip("/")
    dit_url: str = os.getenv("DIT_URL", "http://qwen-dit:8013").rstrip("/")
    # Generous: the first request after a restart waits for a stage to load its
    # weights (17 GB for the encoder). The DiT stream also idles between steps.
    encoder_timeout: float = _float("ENCODER_TIMEOUT", 600)
    vae_timeout: float = _float("VAE_TIMEOUT", 600)
    dit_timeout: float = _float("DIT_TIMEOUT", 3600)
    health_timeout: float = _float("HEALTH_TIMEOUT", 3)
    # Where the models live. Kept here because /healthz and the UI show the id;
    # the weights themselves are read by the stages, on their own volumes.
    memory_mode: str = os.getenv("MEMORY_MODE", "split").strip().lower()
    preload_model: bool = _bool("PRELOAD_MODEL", True)

    # --- параметры генерации --------------------------------------------
    default_steps: int = _int("DEFAULT_STEPS", 40)
    max_steps: int = _int("MAX_STEPS", 60)
    # 2.1 сэмплируется без classifier-free guidance. Значение > 1 включает её
    # и удваивает KV-кэш — на 32 GB это легко приводит к OOM при нескольких референсах.
    default_true_cfg_scale: float = _float("DEFAULT_TRUE_CFG_SCALE", 1.0)
    default_negative_prompt: str = os.getenv("DEFAULT_NEGATIVE_PROMPT", "")
    # Нативная 16:9 у 2.1 — 2752×1536. Сторона должна быть кратна 32.
    max_side: int = _int("MAX_SIDE", 2752)
    min_side: int = _int("MIN_SIDE", 512)

    # --- входные изображения --------------------------------------------
    max_images: int = _int("MAX_IMAGES", 10)
    max_upload_mb: int = _int("MAX_UPLOAD_MB", 60)
    input_max_side: int = _int("INPUT_MAX_SIDE", 1536)

    # --- сервис ----------------------------------------------------------
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = _int("PORT", 8000)
    output_dir: Path = field(default_factory=lambda: _path("OUTPUT_DIR", "/data/outputs"))
    hf_home: str = os.getenv("HF_HOME", "/data/huggingface")
    request_timeout: int = _int("REQUEST_TIMEOUT", 1800)
    keep_outputs: int = _int("KEEP_OUTPUTS", 200)
    log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()
    # Куда POST /api/generate складывает удачный прогон. Таймаут только на этот вызов.
    webstorage_url: str = os.getenv("WEBSTORAGE_URL", "http://app:8000").rstrip("/")
    webstorage_timeout: float = _float("WEBSTORAGE_TIMEOUT", 60)

    @property
    def max_content_length(self) -> int:
        return self.max_upload_mb * 1024 * 1024


settings = Settings()
