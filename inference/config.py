"""Настройки контейнера инференса.

Один в один те, что читал монолит: модель, размещение, точность. Сюда же
переехали параметры HTTP-слоя (порт, размер загрузки), потому что весов и
пайплайна больше нигде нет — Flask-оркестратор эти значения не читает,
он получает их из ``GET /healthz``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

_TRUE = {"1", "true", "yes", "on"}


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in _TRUE


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


@dataclass(frozen=True)
class Settings:
    """Неизменяемый снимок настроек, создаётся один раз при старте процесса."""

    # --- модель ---------------------------------------------------------
    model_id: str = os.getenv("MODEL_ID", "Qwen/Qwen-Image-2.1")
    model_revision: str | None = os.getenv("MODEL_REVISION") or None
    pipeline_class: str = os.getenv("PIPELINE_CLASS", "auto")

    # --- размещение и точность ------------------------------------------
    # fp16 | int8 | offload  (see README, "Memory modes")
    memory_mode: str = os.getenv("MEMORY_MODE", "int8").strip().lower()
    device: str = os.getenv("DEVICE", "cuda")
    text_encoder_device: str = os.getenv("TEXT_ENCODER_DEVICE", "cpu")
    text_encoder_dtype: str = os.getenv("TEXT_ENCODER_DTYPE", "float16")
    # Часть Qwen3-VL, которая всё же живёт в VRAM (в fp16): vision-башня и первые N из 36 слоёв.
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

    # --- входные изображения --------------------------------------------
    max_images: int = _int("MAX_IMAGES", 10)
    max_upload_mb: int = _int("MAX_UPLOAD_MB", 60)

    # --- сервис ----------------------------------------------------------
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = _int("PORT", 8010)
    log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()

    @property
    def max_content_length(self) -> int:
        return self.max_upload_mb * 1024 * 1024


settings = Settings()
