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

    # --- размещение и точность ------------------------------------------
    # fp16 | int8 | offload  (см. README, раздел "Режимы памяти")
    memory_mode: str = os.getenv("MEMORY_MODE", "int8").strip().lower()
    device: str = os.getenv("DEVICE", "cuda")
    text_encoder_device: str = os.getenv("TEXT_ENCODER_DEVICE", "cpu")
    text_encoder_dtype: str = os.getenv("TEXT_ENCODER_DTYPE", "float16")
    # Часть Qwen3-VL, которая всё же живёт в VRAM (в fp16): vision-башня и первые N из 36 слоёв.
    # Слой — ~0.4 GB VRAM вместо ~0.4 GB RAM в fp16 (~0.8 GB, если остаток в fp32).
    # 24 слоя покрывают DeepStack (8/16/24) и снимают с RAM большую часть энкодера.
    text_encoder_gpu_layers: int = _int("TEXT_ENCODER_GPU_LAYERS", 24)
    text_encoder_gpu_vision: bool = _bool("TEXT_ENCODER_GPU_VISION", True)
    # native — PyTorch SDPA. Имя "sdpa" принимается как алиас (см. generator).
    attention_backend: str = os.getenv("ATTENTION_BACKEND", "native")
    # Запасной путь, если полный кадр не влез даже после выноса DiT в RAM.
    vae_tiling: bool = _bool("VAE_TILING", True)
    vae_slicing: bool = _bool("VAE_SLICING", True)
    # Сторона тайла decode в пикселях. Кратность сжатию VAE выравнивается в generator.
    vae_tile_size: int = _int("VAE_TILE_SIZE", 1536)
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
