"""Загрузка пайплайна Qwen-Image и сама генерация.

Ограничения Tesla V100 (SM 7.0), из которых вырос этот модуль:
  * нет аппаратного bfloat16 -> веса приводим к float16;
  * нет FlashAttention-2 -> attention-бэкенд только SDPA/math;
  * 32 GB VRAM меньше, чем ~40 GB весов DiT в fp16 -> нужна квантизация
    или offload (см. MEMORY_MODE).
"""

from __future__ import annotations

import gc
import importlib
import inspect
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

import torch
from PIL import Image

from .config import settings
from .offload import CpuHostedTextEncoder

log = logging.getLogger(__name__)

_DTYPES = {"float32": torch.float32, "float16": torch.float16, "bfloat16": torch.bfloat16}


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


class PipelineError(RuntimeError):
    """Ошибка загрузки или конфигурации пайплайна."""


def _resolve_pipeline_class() -> type:
    """Возвращает класс пайплайна: либо явный из PIPELINE_CLASS, либо авто."""
    import diffusers

    if settings.pipeline_class != "auto":
        try:
            return getattr(diffusers, settings.pipeline_class)
        except AttributeError as exc:  # pragma: no cover - конфигурационная ошибка
            raise PipelineError(
                f"diffusers {diffusers.__version__} не содержит класса "
                f"{settings.pipeline_class!r}. Обновите diffusers или поправьте "
                "PIPELINE_CLASS в .env."
            ) from exc
    return diffusers.DiffusionPipeline


def _supports(callable_obj: Any, name: str) -> bool:
    try:
        return name in inspect.signature(callable_obj).parameters
    except (TypeError, ValueError):  # pragma: no cover - C-расширения
        return False


class QwenImageGenerator:
    """Потокобезопасная обёртка над пайплайном: одна модель, один GPU, очередь из одного."""

    def __init__(self) -> None:
        self._pipe: Any = None
        self._load_lock = threading.Lock()
        self._gpu_lock = threading.Lock()
        self._accepts_images = False
        self._load_error: str | None = None

    # ------------------------------------------------------------------ load
    @property
    def is_loaded(self) -> bool:
        return self._pipe is not None

    @property
    def accepts_images(self) -> bool:
        return self._accepts_images

    @property
    def load_error(self) -> str | None:
        return self._load_error

    def ensure_loaded(self) -> Any:
        if self._pipe is not None:
            return self._pipe
        with self._load_lock:
            if self._pipe is None:
                try:
                    self._pipe = self._load()
                    self._load_error = None
                except Exception as exc:
                    self._load_error = f"{type(exc).__name__}: {exc}"
                    raise
        return self._pipe

    def _load(self) -> Any:
        started = time.monotonic()
        device = torch.device(settings.device)
        compute_dtype = torch.float16
        log.info(
            "Загружаю %s (mode=%s, device=%s, text_encoder=%s/%s)",
            settings.model_id,
            settings.memory_mode,
            device,
            settings.text_encoder_device,
            settings.text_encoder_dtype,
        )

        pipeline_cls = _resolve_pipeline_class()
        load_kwargs: dict[str, Any] = {
            "torch_dtype": compute_dtype,
            "low_cpu_mem_usage": True,
        }
        if settings.model_revision:
            load_kwargs["revision"] = settings.model_revision

        pipe = pipeline_cls.from_pretrained(settings.model_id, **load_kwargs)
        log.info("Веса прочитаны за %.1f с, класс пайплайна: %s", time.monotonic() - started, type(pipe).__name__)

        if settings.memory_mode == "offload":
            self._setup_offload(pipe)
        else:
            self._setup_split(pipe, device, compute_dtype)

        self._configure_vae(pipe)
        self._configure_attention(pipe)
        self._accepts_images = _supports(pipe.__call__, "image")
        _free_memory()
        log.info("Пайплайн готов за %.1f с (image-вход: %s)", time.monotonic() - started, self._accepts_images)
        return pipe

    def _setup_split(self, pipe: Any, device: torch.device, compute_dtype: torch.dtype) -> None:
        """Энкодер — в RAM, трансформер и VAE — в VRAM (при необходимости в int8)."""
        text_encoder = getattr(pipe, "text_encoder", None)
        if text_encoder is None:
            raise PipelineError("У пайплайна нет компонента text_encoder")

        if settings.memory_mode == "int8":
            self._quantize(pipe)

        for name in ("transformer", "unet", "vae", "controlnet"):
            module = getattr(pipe, name, None)
            if module is not None and isinstance(module, torch.nn.Module):
                module.to(device=device, dtype=compute_dtype)
                log.info("%s -> %s/%s", name, device, compute_dtype)

        weight_dtype = _DTYPES.get(settings.text_encoder_dtype, torch.float32)
        pipe.text_encoder = CpuHostedTextEncoder(
            text_encoder,
            compute_device=device,
            compute_dtype=compute_dtype,
            weight_dtype=weight_dtype,
        )
        log.info("text_encoder закреплён на %s/%s", settings.text_encoder_device, weight_dtype)

    def _quantize(self, pipe: Any) -> None:
        """int8 weight-only для DiT: ~20 GB вместо ~40 GB, вычисления остаются в fp16."""
        try:
            from optimum.quanto import freeze, qint8, quantize
        except ImportError as exc:  # pragma: no cover
            raise PipelineError(
                "MEMORY_MODE=int8 требует optimum-quanto. Установите его или "
                "переключитесь на MEMORY_MODE=offload."
            ) from exc

        backbone = getattr(pipe, "transformer", None) or getattr(pipe, "unet", None)
        if backbone is None:
            raise PipelineError("Не найден transformer/unet для квантизации")

        log.info("Квантизую %s в int8 (это занимает несколько минут)", type(backbone).__name__)
        quantize(backbone, weights=qint8)
        freeze(backbone)
        _free_memory()

    def _setup_offload(self, pipe: Any) -> None:
        """Резервный режим: accelerate сам тасует блоки между RAM и VRAM."""
        pipe.enable_sequential_cpu_offload()
        log.warning("MEMORY_MODE=offload: памяти хватит, но генерация будет заметно медленнее")

    def _configure_vae(self, pipe: Any) -> None:
        vae = getattr(pipe, "vae", None)
        if vae is None:
            return
        if settings.vae_tiling and hasattr(pipe, "enable_vae_tiling"):
            pipe.enable_vae_tiling()
        if settings.vae_slicing and hasattr(pipe, "enable_vae_slicing"):
            pipe.enable_vae_slicing()

    def _configure_attention(self, pipe: Any) -> None:
        """V100 не тянет FlashAttention-2, оставляем SDPA."""
        if settings.attention_backend == "sdpa":
            return
        try:
            module = importlib.import_module("diffusers.models.attention_dispatch")
            module.attention_backend(settings.attention_backend)
        except Exception as exc:  # pragma: no cover - зависит от версии diffusers
            log.warning("Не удалось включить attention-бэкенд %s: %s", settings.attention_backend, exc)

    # ------------------------------------------------------------- generate
    @torch.inference_mode()
    def generate(self, request: GenerationRequest) -> GenerationResult:
        pipe = self.ensure_loaded()

        if request.images and not self._accepts_images:
            raise PipelineError(
                f"{type(pipe).__name__} не принимает изображения на вход — "
                "проверьте MODEL_ID/PIPELINE_CLASS в .env."
            )

        call_kwargs: dict[str, Any] = {
            "prompt": request.prompt,
            "width": request.width,
            "height": request.height,
            "num_inference_steps": request.steps,
        }
        if request.images:
            call_kwargs["image"] = request.images if len(request.images) > 1 else request.images[0]

        optional = {
            "negative_prompt": request.negative_prompt,
            "true_cfg_scale": request.true_cfg_scale,
            "guidance_scale": request.true_cfg_scale,
            "num_images_per_prompt": 1,
        }
        for name, value in optional.items():
            if _supports(pipe.__call__, name):
                call_kwargs[name] = value

        with self._gpu_lock:
            generator = torch.Generator(device="cpu").manual_seed(request.seed)
            call_kwargs["generator"] = generator
            started = time.monotonic()
            try:
                output = pipe(**call_kwargs)
            except torch.cuda.OutOfMemoryError as exc:
                _free_memory()
                raise PipelineError(
                    "Не хватило VRAM. Уменьшите разрешение/число входных фото "
                    "или переключите MEMORY_MODE на offload."
                ) from exc
            duration = time.monotonic() - started
            _free_memory()

        return GenerationResult(images=list(output.images), seed=request.seed, duration=duration)


def _free_memory() -> None:
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


generator = QwenImageGenerator()
