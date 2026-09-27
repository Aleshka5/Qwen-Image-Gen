"""Загрузка пайплайна Qwen-Image и сама генерация.

Ограничения Tesla V100 (SM 7.0), из которых вырос этот модуль:
  * нет аппаратного bfloat16 -> веса приводим к float16;
  * нет FlashAttention-2 -> attention-бэкенд native (PyTorch SDPA);
  * пайплайн 2.1 в fp16 — ~33 GB весов, DiT из них ~14 GB (7B). Qwen3-VL
    целиком на карту не ставится: vision и первые слои в VRAM, хвост в RAM (fp16).
    int8 DiT (~7 GB) оставляет место под энкодер и KV-кэш референсов.
"""

from __future__ import annotations

import gc
import inspect
import logging
import os
import sys
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
# Старые .env писали "sdpa": в diffusers такого бэкенда нет, это и есть native SDPA.
_ATTENTION_ALIASES = {"sdpa": "native"}


def _attention_backend_name() -> str:
    return _ATTENTION_ALIASES.get(settings.attention_backend, settings.attention_backend)


# Читается при первом импорте diffusers. Поставить до любых импортов пайплайна.
os.environ["DIFFUSERS_ATTN_BACKEND"] = _attention_backend_name()


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


def _vram_note() -> str:
    if not torch.cuda.is_available():
        return ""
    return (
        f", VRAM alloc {torch.cuda.memory_allocated() / 2**30:.2f} GB"
        f" reserved {torch.cuda.memory_reserved() / 2**30:.2f} GB"
    )


def _shape_note(value: Any) -> str:
    if torch.is_tensor(value):
        return f" {tuple(value.shape)} {value.dtype}"
    if isinstance(value, (tuple, list)):
        parts = [_shape_note(item).strip() for item in value if torch.is_tensor(item)]
        return f" -> {'; '.join(parts)}" if parts else ""
    return ""


def _vae_tile_geometry(tile_size: int, compression: int) -> tuple[int, int]:
    """Сторона тайла и шаг, кратные сжатию VAE. Шаг — 3/4 тайла, как 192/256 в diffusers."""
    ratio = compression if isinstance(compression, int) and compression > 0 else 8
    tile = max(ratio, (int(tile_size) // ratio) * ratio)
    stride = max(ratio, ((tile * 3 // 4) // ratio) * ratio)
    if stride >= tile:
        stride = max(ratio, tile - ratio)
    return tile, stride


def _instrument(obj: Any, name: str, label: str) -> None:
    """Логирует каждый вызов шага пайплайна: старт, итог, время и VRAM."""
    original = getattr(obj, name, None)
    if original is None or getattr(original, "_qwen_logged", False):
        return

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        # Кэш аллокатора после денойза дырявый: большой fp32-тензор decode в него не встаёт.
        if label == "vae.decode":
            _free_memory()
        started = time.monotonic()
        detail = _call_detail(name, args, kwargs)
        log.info("pipeline «%s»: start%s", label, detail)
        result = original(*args, **kwargs)
        log.info(
            "pipeline «%s»: done in %.2f s%s%s",
            label,
            time.monotonic() - started,
            _shape_note(result),
            _vram_note(),
        )
        return result

    wrapped._qwen_logged = True  # type: ignore[attr-defined]
    setattr(obj, name, wrapped)


def _call_detail(name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    if name == "encode_prompt":
        prompt = kwargs.get("prompt", args[1] if len(args) > 1 else "")
        images = kwargs.get("image", args[0] if args else None)
        text = prompt if isinstance(prompt, str) else ""
        count = len(images) if isinstance(images, list) else (1 if images is not None else 0)
        preview = text.replace("\n", " ")[:180]
        return f" images={count} prompt={preview!r}"
    if name in {"encode", "decode", "_encode_vae_image"} and args:
        tensor = args[0]
        if torch.is_tensor(tensor):
            return f" in {tuple(tensor.shape)} {tensor.dtype}"
    return ""


def _instrument_pipeline(pipe: Any) -> None:
    for name, label in (
        ("encode_prompt", "encode_prompt"),
        ("prepare_latents", "prepare_latents"),
        ("_encode_vae_image", "vae.encode_image"),
    ):
        _instrument(pipe, name, label)
    vae = getattr(pipe, "vae", None)
    if vae is not None:
        _instrument(vae, "decode", "vae.decode")
    processor = getattr(pipe, "image_processor", None)
    if processor is not None:
        _instrument(processor, "preprocess", "image.preprocess")
        _instrument(processor, "postprocess", "image.postprocess")


class QwenImageGenerator:
    """Потокобезопасная обёртка над пайплайном: одна модель, один GPU, очередь из одного."""

    def __init__(self) -> None:
        self._pipe: Any = None
        self._load_lock = threading.Lock()
        self._gpu_lock = threading.Lock()
        self._accepts_images = False
        self._load_error: str | None = None
        self._gen_mark = 0.0
        self._step_total = 0
        self._seen_caches: list[Any] = []
        # Модули, которые decode увёз в RAM и не смог вернуть, пока живы тензоры ошибки.
        self._deferred_parked: list[tuple[str, Any, torch.device]] = []

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
            "dtype": compute_dtype,
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
        _instrument_pipeline(pipe)
        self._watch_kv_cache(pipe)
        self._install_quality_decode(pipe)
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

        quantized = settings.memory_mode == "int8"
        for name in ("transformer", "unet", "vae", "controlnet"):
            module = getattr(pipe, name, None)
            if module is None or not isinstance(module, torch.nn.Module):
                continue
            # dtype= после freeze() у quanto либо падает, либо распаковывает int8 обратно в fp16.
            if quantized and name in ("transformer", "unet"):
                module.to(device=device)
                log.info("%s -> %s (int8 weights)", name, device)
            else:
                module.to(device=device, dtype=compute_dtype)
                log.info("%s -> %s/%s", name, device, compute_dtype)

        weight_dtype = _DTYPES.get(settings.text_encoder_dtype, torch.float16)
        pipe.text_encoder = CpuHostedTextEncoder(
            text_encoder,
            compute_device=device,
            compute_dtype=compute_dtype,
            weight_dtype=weight_dtype,
            gpu_layers=settings.text_encoder_gpu_layers,
            gpu_vision=settings.text_encoder_gpu_vision,
        )
        _free_memory()
        log.info(
            "text_encoder: остаток на %s/%s, VRAM занято %.1f GB",
            settings.text_encoder_device,
            weight_dtype,
            torch.cuda.memory_allocated(device) / 2**30 if device.type == "cuda" else 0.0,
        )

    def _quantize(self, pipe: Any) -> None:
        """int8 weight-only для DiT 7B: ~7 GB вместо ~14 GB, вычисления остаются в fp16."""
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
        """Включает тайловый decode на самом VAE.

        ``DiffusionPipeline.enable_vae_tiling`` в текущем diffusers нет, поэтому
        прежняя проверка ``hasattr(pipe, ...)`` молча оставляла ``use_tiling=False``
        и полный кадр 1536×2752 декодировался одним проходом.
        """
        vae = getattr(pipe, "vae", None)
        if vae is None:
            return
        if settings.vae_tiling:
            ratio = getattr(vae, "spatial_compression_ratio", 8)
            tile, stride = _vae_tile_geometry(settings.vae_tile_size, ratio)
            if not _enable_vae_flag(pipe, vae, "enable_tiling", "enable_vae_tiling"):
                log.warning("VAE_TILING=true, но ни VAE, ни пайплайн не умеют enable_tiling")
            elif hasattr(vae, "tile_sample_min_height"):
                vae.tile_sample_min_height = tile
                vae.tile_sample_min_width = tile
                vae.tile_sample_stride_height = stride
                vae.tile_sample_stride_width = stride
                log.info(
                    "VAE tiling: tile %d px, stride %d px, use_tiling=%s",
                    tile,
                    stride,
                    getattr(vae, "use_tiling", True),
                )
        if settings.vae_slicing and not _enable_vae_flag(pipe, vae, "enable_slicing", "enable_vae_slicing"):
            log.warning("VAE_SLICING=true, но ни VAE, ни пайплайн не умеют enable_slicing")

    def _watch_kv_cache(self, pipe: Any) -> None:
        """Запоминает KV денойза: к decode он уже не нужен и может уйти в RAM."""
        module = getattr(pipe, "transformer", None) or getattr(pipe, "unet", None)
        if module is None or getattr(module.forward, "_qwen_kv_watch", False):
            return
        original = module.forward

        def wrapped(*args: Any, **kwargs: Any) -> Any:
            cache = kwargs.get("kv_cache")
            if cache is not None and cache not in self._seen_caches:
                self._seen_caches.append(cache)
            return original(*args, **kwargs)

        wrapped._qwen_kv_watch = True  # type: ignore[attr-defined]
        module.forward = wrapped

    def _install_quality_decode(self, pipe: Any) -> None:
        """Полный кадр VAE, пока DiT лежит в RAM. Тайлы — только если кадр не влез."""
        vae = getattr(pipe, "vae", None)
        if vae is None or settings.memory_mode == "offload" or getattr(vae.decode, "_qwen_quality", False):
            return
        original = vae.decode

        def wrapped(*args: Any, **kwargs: Any) -> Any:
            latent = args[0] if args else kwargs.get("z")
            parked: list[tuple[str, Any, torch.device]] = []
            previous = bool(getattr(vae, "use_tiling", False))
            try:
                self._park_for_decode(pipe, parked)
                if not _prefer_full_decode(latent, vae):
                    log.info("VAE decode: тайлы %d px, DiT в RAM%s", settings.vae_tile_size, _vram_note())
                    return original(*args, **kwargs)
                if hasattr(vae, "use_tiling"):
                    vae.use_tiling = False
                log.info("VAE decode: полный кадр, DiT в RAM%s", _vram_note())
                try:
                    return original(*args, **kwargs)
                except torch.cuda.OutOfMemoryError:
                    if not previous:
                        raise
                    log.warning(
                        "полный кадр не влез в VRAM, повтор тайлами %d px",
                        settings.vae_tile_size,
                    )
                    _free_memory()
                    vae.use_tiling = True
                    return original(*args, **kwargs)
            finally:
                if hasattr(vae, "use_tiling"):
                    vae.use_tiling = previous
                # KV денойза decode не нужен. Пока кадр исключения держит VRAM,
                # возврат DiT на карту только дублирует веса и роняет следующий запрос.
                self._release_kv_caches()
                if sys.exc_info()[0] is None:
                    self._restore_parked(parked)
                if parked:
                    self._deferred_parked.extend(parked)
                    parked.clear()

        wrapped._qwen_quality = True  # type: ignore[attr-defined]
        vae.decode = wrapped

    def _release_kv_caches(self) -> int:
        """Сбрасывает K/V денойза. Копия в RAM не нужна: к decode префикс уже не читается."""
        released = 0
        for cache in self._seen_caches:
            for layer in getattr(cache, "layer_caches", ()):
                for attr in ("k", "v"):
                    tensor = getattr(layer, attr, None)
                    if torch.is_tensor(tensor):
                        released += tensor.numel() * tensor.element_size()
                    setattr(layer, attr, None)
        self._seen_caches.clear()
        return released

    def _park_for_decode(self, pipe: Any, parked: list[tuple[str, Any, torch.device]]) -> None:
        """Убирает из VRAM всё, кроме VAE: KV денойза удаляется, веса DiT уезжают в RAM."""
        gpu = torch.device(settings.device)
        dropped = self._release_kv_caches()
        if dropped:
            log.info("KV денойза сброшен: %.2f GB", dropped / 2**30)
            _free_memory()
        bytes_moved = 0
        vae = getattr(pipe, "vae", None)
        for name in ("transformer", "unet", "controlnet", "text_encoder"):
            module = getattr(pipe, name, None)
            if not isinstance(module, torch.nn.Module) or module is vae:
                continue
            if isinstance(module, CpuHostedTextEncoder):
                bytes_moved += _park_loose_cuda(module, parked, gpu)
                continue
            if not any(param.is_cuda for param in module.parameters()):
                continue
            bytes_moved += sum(
                param.numel() * param.element_size() for param in module.parameters() if param.is_cuda
            )
            parked.append(("module", module, gpu))
            module.to(device=torch.device("cpu"))
        _free_memory()
        log.info("перед VAE decode в RAM уехало %.2f GB", bytes_moved / 2**30)

    def _restore_parked(self, parked: list[tuple[str, Any, torch.device]]) -> None:
        """Возвращает веса на GPU. Неудачный элемент остаётся в списке, а не теряется в RAM."""
        while parked:
            kind, payload, device = parked[-1]
            try:
                _move_back(kind, payload, device)
            except torch.cuda.OutOfMemoryError:
                _free_memory()
                try:
                    _move_back(kind, payload, device)
                except torch.cuda.OutOfMemoryError:
                    log.warning("веса остались в RAM: VRAM ещё держат активации decode")
                    return
            except Exception:
                log.exception("не удалось вернуть веса на GPU")
                return
            parked.pop()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def _reclaim(self) -> None:
        """Отпускает активации прогона и возвращает отложенные веса. Веса модели остаются загруженными."""
        self._release_kv_caches()
        if self._deferred_parked:
            self._restore_parked(self._deferred_parked)
        _free_memory()
        log.info("память после генерации: RSS %.2f GB%s", _rss_gb(), _vram_note())

    def _configure_attention(self, pipe: Any) -> None:
        """Закрепляет бэкенд на всё время процесса.

        ``attention_backend()`` в diffusers — контекстный менеджер: вызов без ``with``
        ничего не меняет. Реестр читает ``DIFFUSERS_ATTN_BACKEND`` один раз при импорте,
        поэтому здесь выставляем активный бэкенд напрямую. На V100 это ``native``
        (PyTorch SDPA): FlashAttention-2 требует SM 8.0.
        """
        del pipe
        backend = _attention_backend_name()
        try:
            from diffusers.models.attention_dispatch import AttentionBackendName, _AttentionBackendRegistry

            name = AttentionBackendName(backend)
            if name not in _AttentionBackendRegistry._backends:
                raise ValueError(f"бэкенд {backend!r} не зарегистрирован")
            _AttentionBackendRegistry.set_active_backend(name)
            log.info("attention backend = %s", name.value)
        except Exception as exc:  # pragma: no cover - зависит от версии diffusers
            log.warning("Не удалось включить attention-бэкенд %s: %s", backend, exc)

    # ------------------------------------------------------------- generate
    @torch.no_grad()
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

        # Пустой negative при true_cfg_scale=1 заставляет пайплайн предупреждать,
        # что guidance не включена. Пробел из старых настроек 1.0 делал то же самое
        # и при scale>1 включал второй проход.
        negative = request.negative_prompt.strip()
        optional = {
            "true_cfg_scale": request.true_cfg_scale,
            "num_images_per_prompt": 1,
        }
        if negative:
            optional["negative_prompt"] = negative
        for name, value in optional.items():
            if _supports(pipe.__call__, name):
                call_kwargs[name] = value

        if _supports(pipe.__call__, "callback_on_step_end"):
            self._step_total = request.steps
            call_kwargs["callback_on_step_end"] = self._log_denoise_step

        log.info(
            "pipeline call: %dx%d steps=%d cfg=%.2f seed=%d refs=%d negative=%r prompt=%r",
            request.width,
            request.height,
            request.steps,
            request.true_cfg_scale,
            request.seed,
            len(request.images),
            negative,
            request.prompt,
        )
        for index, image in enumerate(request.images, start=1):
            log.info("pipeline input image%d: %dx%d %s", index, image.width, image.height, image.mode)

        with self._gpu_lock:
            self._seen_caches.clear()
            self._deferred_parked.clear()
            generator = torch.Generator(device="cpu").manual_seed(request.seed)
            call_kwargs["generator"] = generator
            started = time.monotonic()
            self._gen_mark = started
            self._step_mark = started
            if torch.cuda.is_available():
                torch.cuda.reset_peak_memory_stats()
            # Отдельный кадр: traceback OOM держит тензоры decode, и empty_cache
            # в том же except их не отдаёт. После возврата кадр уже мёртв.
            output = None
            peak = 0.0
            try:
                output = self._invoke_pipeline(pipe, call_kwargs)
                if torch.cuda.is_available():
                    peak = torch.cuda.max_memory_allocated() / 2**30
            finally:
                self._reclaim()
            if output is None:
                raise PipelineError(
                    "Не хватило VRAM. Уменьшите разрешение/число входных фото, "
                    "TEXT_ENCODER_GPU_LAYERS или переключите MEMORY_MODE на offload."
                )
            duration = time.monotonic() - started
            outputs = list(output.images)
            del output
            _free_memory()
            sizes = ", ".join(f"{image.width}x{image.height}" for image in outputs)
            if torch.cuda.is_available():
                log.info(
                    "pipeline done: %d output(s) [%s], %.1f s, peak VRAM %.2f GB, refs=%d",
                    len(outputs),
                    sizes,
                    duration,
                    peak,
                    len(request.images),
                )
            else:
                log.info("pipeline done: %d output(s) [%s], %.1f s", len(outputs), sizes, duration)

        return GenerationResult(images=outputs, seed=request.seed, duration=duration)

    def _invoke_pipeline(self, pipe: Any, call_kwargs: dict[str, Any]) -> Any:
        try:
            return pipe(**call_kwargs)
        except torch.cuda.OutOfMemoryError:
            return None

    def _log_denoise_step(self, pipe: Any, step_index: int, timestep: Any, callback_kwargs: dict[str, Any]) -> dict:
        del pipe
        now = time.monotonic()
        step_s = now - self._step_mark
        self._step_mark = now
        value = timestep.item() if torch.is_tensor(timestep) else float(timestep)
        # Первый callback приходит после encode/latents и первого шага денойза.
        phase = "с вызова пайплайна" if step_index == 0 else "шаг"
        log.info(
            "pipeline «denoise» %d/%d: timestep=%.4g, %s %.2f с, с начала %.1f с%s",
            step_index + 1,
            self._step_total,
            value,
            phase,
            step_s,
            now - self._gen_mark,
            _vram_note(),
        )
        return callback_kwargs


# Нативный кадр 2.1. Полный decode такого размера влезает в 32 GB, когда в VRAM остался только VAE.
_FULL_DECODE_SHORT = 1536
_FULL_DECODE_LONG = 2752


def _prefer_full_decode(latent: Any, vae: Any) -> bool:
    if not torch.is_tensor(latent) or latent.ndim != 5:
        return False
    try:
        ratio = int(getattr(vae, "spatial_compression_ratio", 16))
    except (TypeError, ValueError):
        ratio = 16
    height = int(latent.shape[-2]) * ratio
    width = int(latent.shape[-1]) * ratio
    short, long = sorted((height, width))
    return short <= _FULL_DECODE_SHORT and long <= _FULL_DECODE_LONG


def _move_back(kind: str, payload: Any, device: torch.device) -> None:
    if kind == "module":
        payload.to(device=device)
        return
    for tensor in payload:
        tensor.data = tensor.data.to(device=device)


def _rss_gb() -> float:
    try:
        with open("/proc/self/status", encoding="ascii") as status:
            for line in status:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 2**20
    except OSError:
        return 0.0
    return 0.0


def _park_loose_cuda(module: torch.nn.Module, parked: list[tuple[str, Any, torch.device]], gpu: torch.device) -> int:
    """CpuHostedTextEncoder.to() веса не трогает — переносим CUDA-тензоры по одному."""
    moved: list[torch.Tensor] = []
    total = 0
    for tensor in list(module.parameters()) + list(module.buffers()):
        if torch.is_tensor(tensor) and tensor.is_cuda:
            total += tensor.numel() * tensor.element_size()
            tensor.data = tensor.data.to(device=torch.device("cpu"))
            moved.append(tensor)
    if moved:
        parked.append(("tensors", moved, gpu))
    return total


def _enable_vae_flag(pipe: Any, vae: Any, vae_method: str, pipe_method: str) -> bool:
    """Зовёт метод VAE, а если его нет — старый метод пайплайна."""
    for owner, method in ((vae, vae_method), (pipe, pipe_method)):
        fn = getattr(owner, method, None)
        if callable(fn):
            fn()
            return True
    return False


def _free_memory() -> None:
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.synchronize()
        torch.cuda.empty_cache()
        ipc_collect = getattr(torch.cuda, "ipc_collect", None)
        if ipc_collect is not None:
            ipc_collect()
    # glibc не отдаёт страницы обратно OS после пика загрузки (fp16→fp32 копии).
    try:
        import ctypes

        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass


generator = QwenImageGenerator()
