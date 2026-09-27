"""Прокси, удерживающий текстовый энкодер (целиком или частично) в CPU RAM.

Diffusers исходит из того, что все модули пайплайна живут на одном устройстве:
внутри ``__call__`` он берёт ``pipe._execution_device`` и переносит на него входы
энкодера. Поэтому недостаточно просто сделать ``text_encoder.to("cpu")`` — нужен
модуль, который снаружи выглядит как GPU-модуль, а внутри считает на CPU.

Часть энкодера можно оставить в VRAM: vision-башню (через неё идут референсы) и
первые N слоёв декодера. Каждый такой модуль получает pre-hook, который переносит
входы на его устройство и dtype, так что hidden states сами ходят CPU <-> GPU.

Пиксели референсов на CPU не копируем: пайплайн уже кладёт их на GPU, а при
``gpu_vision`` vision-башня считает там же. Иначе десять фото в fp32 забивают RAM
ещё до DiT. ``image_grid_thw`` остаётся рядом с ``input_ids``: RoPE складывает
их в ``get_rope_index``, и разъезд устройств роняет проход. На GPU сетку
переносит уже pre-hook vision-башни.
"""

from __future__ import annotations

import logging
from typing import Any

import torch
from torch import nn

log = logging.getLogger(__name__)

# Только пиксели. Сетки (image_grid_thw) должны остаться на устройстве input_ids.
_GPU_PIXEL_KEYS = frozenset({"pixel_values", "pixel_values_videos"})


def _parameter_bytes(module: nn.Module, device_type: str) -> int:
    return sum(p.numel() * p.element_size() for p in module.parameters() if p.device.type == device_type)


def _relocate(obj: Any, device: torch.device, dtype: torch.dtype | None) -> Any:
    """Рекурсивно переносит тензоры; приводит dtype только у вещественных."""
    if isinstance(obj, torch.Tensor):
        if dtype is not None and obj.is_floating_point():
            return obj.to(device=device, dtype=dtype)
        return obj.to(device=device)
    if isinstance(obj, (list, tuple)):
        moved = [_relocate(item, device, dtype) for item in obj]
        return type(obj)(moved) if not isinstance(obj, tuple) else tuple(moved)
    if isinstance(obj, dict):  # сюда же попадают transformers ModelOutput
        for key in list(obj.keys()):
            obj[key] = _relocate(obj[key], device, dtype)
        return obj
    return obj


def _input_mover(device: torch.device, dtype: torch.dtype):
    def hook(module: nn.Module, args: tuple, kwargs: dict) -> tuple[tuple, dict]:
        mask = kwargs.get("attention_mask")
        if isinstance(mask, torch.Tensor) and mask.is_floating_point():
            # min(fp32) в fp16 превращается в -inf, а строка из одних -inf даёт NaN в softmax.
            kwargs["attention_mask"] = mask.clamp(min=torch.finfo(dtype).min)
        moved_args = _relocate(args, device, dtype)
        moved_kwargs = {k: _relocate(v, device, dtype) for k, v in kwargs.items()}
        return moved_args, moved_kwargs

    return hook


def _cast_parameters(module: nn.Module, device: torch.device, dtype: torch.dtype) -> None:
    """Переносит модуль и приводит только веса: буферы вроде ``inv_freq`` остаются в своём dtype."""
    module.to(device=device)
    for param in module.parameters():
        if param.is_floating_point() and param.dtype != dtype:
            param.data = param.data.to(dtype)


class CpuHostedTextEncoder(nn.Module):
    """Держит веса энкодера в RAM, но для пайплайна притворяется GPU-модулем."""

    def __init__(
        self,
        module: nn.Module,
        compute_device: torch.device,
        compute_dtype: torch.dtype,
        weight_dtype: torch.dtype = torch.float16,
        gpu_layers: int = 0,
        gpu_vision: bool = False,
    ) -> None:
        super().__init__()
        module = module.eval()
        backbone = self._resolve_backbone(module)
        # Qwen*-VL ForConditionalGeneration: пайплайну нужны только hidden states,
        # поэтому зовём backbone напрямую, а lm_head (~150k x hidden) не держим вовсе.
        self._backbone_only = backbone is not module and isinstance(getattr(module, "lm_head", None), nn.Module)
        if self._backbone_only:
            module.lm_head = nn.Identity()

        gpu_parts = self._gpu_parts(backbone, gpu_layers, gpu_vision)
        for part in gpu_parts:
            _cast_parameters(part, compute_device, compute_dtype)
            part.register_forward_pre_hook(_input_mover(compute_device, compute_dtype), with_kwargs=True)

        gpu_params = {id(p) for part in gpu_parts for p in part.parameters()}
        cpu = torch.device("cpu")
        for param in module.parameters():
            if id(param) not in gpu_params and param.is_floating_point() and param.dtype != weight_dtype:
                param.data = param.data.to(weight_dtype)
        for buffer in module.buffers():
            if buffer.device.type == "cpu" and buffer.is_floating_point():
                buffer.data = buffer.data.to(weight_dtype)

        layers = self._text_layers(backbone)
        if gpu_parts and layers is not None:
            # Слои после GPU-части и финальная norm должны забрать hidden states обратно в RAM.
            text_model = getattr(backbone, "language_model", backbone)
            tail = list(layers)[gpu_layers:] + [getattr(text_model, "norm", None)]
            for layer in tail:
                if isinstance(layer, nn.Module):
                    layer.register_forward_pre_hook(_input_mover(cpu, weight_dtype), with_kwargs=True)

        self.module = module
        self._compute_device = compute_device
        self._compute_dtype = compute_dtype
        self._weight_dtype = weight_dtype
        self._gpu_vision = bool(gpu_vision)
        log.info(
            "text_encoder веса: %.1f GB VRAM / %.1f GB RAM",
            _parameter_bytes(module, compute_device.type) / 2**30,
            _parameter_bytes(module, "cpu") / 2**30,
        )

    @staticmethod
    def _resolve_backbone(module: nn.Module) -> nn.Module:
        """Qwen3VLModel сам, либо ``.model`` у ForConditionalGeneration."""
        if hasattr(module, "visual") or hasattr(module, "language_model"):
            return module
        inner = getattr(module, "model", None)
        return inner if isinstance(inner, nn.Module) else module

    @staticmethod
    def _text_layers(backbone: nn.Module | None) -> nn.ModuleList | None:
        if backbone is None:
            return None
        text_model = getattr(backbone, "language_model", backbone)
        return getattr(text_model, "layers", None)

    @classmethod
    def _gpu_parts(cls, backbone: nn.Module | None, gpu_layers: int, gpu_vision: bool) -> list[nn.Module]:
        parts: list[nn.Module] = []
        visual = getattr(backbone, "visual", None) if backbone is not None else None
        if gpu_vision and visual is not None:
            parts.append(visual)
        layers = cls._text_layers(backbone)
        if gpu_layers > 0:
            if layers is None:
                log.warning("У энкодера не нашлось language_model.layers — слои остаются в RAM")
            else:
                if gpu_layers > len(layers):
                    log.warning("TEXT_ENCODER_GPU_LAYERS=%d, а слоёв %d — на GPU уйдут все", gpu_layers, len(layers))
                parts.extend(list(layers)[:gpu_layers])
            text_model = getattr(backbone, "language_model", backbone) if backbone is not None else None
            embed = getattr(text_model, "embed_tokens", None)
            if isinstance(embed, nn.Module):
                parts.append(embed)
        log.info(
            "text_encoder на GPU: vision=%s, слоёв декодера %d из %d",
            gpu_vision and visual is not None,
            min(gpu_layers, len(layers)) if layers is not None else 0,
            len(layers) if layers is not None else 0,
        )
        return parts

    # --- то, что опрашивает diffusers ------------------------------------
    @property
    def device(self) -> torch.device:  # noqa: D401 - намеренная "ложь"
        return self._compute_device

    @property
    def dtype(self) -> torch.dtype:
        return self._compute_dtype

    @property
    def config(self) -> Any:
        return self.module.config

    def to(self, *args: Any, **kwargs: Any) -> "CpuHostedTextEncoder":
        """Игнорируем попытки пайплайна утащить энкодер на GPU."""
        return self

    def cuda(self, *args: Any, **kwargs: Any) -> "CpuHostedTextEncoder":
        return self

    def __getattr__(self, name: str) -> Any:
        # nn.Module.__getattr__ ищет в _parameters/_buffers/_modules, поэтому
        # `self.module` найдётся штатно; всё остальное проксируем внутрь.
        try:
            return super().__getattr__(name)
        except AttributeError:
            return getattr(self.__dict__["_modules"]["module"], name)

    @torch.inference_mode()
    def forward(self, *args: Any, **kwargs: Any) -> Any:
        cpu = torch.device("cpu")
        args = tuple(_relocate(a, cpu, self._weight_dtype) for a in args)
        moved: dict[str, Any] = {}
        for key, value in kwargs.items():
            if self._gpu_vision and key in _GPU_PIXEL_KEYS:
                moved[key] = _relocate(value, self._compute_device, self._compute_dtype)
            else:
                moved[key] = _relocate(value, cpu, self._weight_dtype)
        kwargs = moved
        # Энкодер прогоняется один раз; KV-кэш на всю последовательность с референсами — лишняя RAM.
        kwargs.setdefault("use_cache", False)

        if not self._backbone_only:
            output = self.module(*args, **kwargs)
            return _relocate(output, self._compute_device, self._compute_dtype)

        # В transformers 5 hidden_states[-1] — это и есть last_hidden_state
        # (capture_outputs(tie_last_hidden_states=True)). Остальные 36 слоёв пайплайн
        # не читает, а на десяти референсах они стоят гигабайты RAM.
        want_hidden = kwargs.pop("output_hidden_states", False)
        kwargs.pop("logits_to_keep", None)
        output = self.module.model(*args, **kwargs)
        output = _relocate(output, self._compute_device, self._compute_dtype)
        if want_hidden:
            output.hidden_states = (output.last_hidden_state,)
        return output
