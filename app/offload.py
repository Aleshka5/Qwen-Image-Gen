"""Прокси, удерживающий текстовый энкодер в CPU RAM.

Diffusers исходит из того, что все модули пайплайна живут на одном устройстве:
внутри ``__call__`` он берёт ``pipe._execution_device`` и переносит на него входы
энкодера. Поэтому недостаточно просто сделать ``text_encoder.to("cpu")`` — нужен
модуль, который снаружи выглядит как GPU-модуль, а внутри считает на CPU.
"""

from __future__ import annotations

from typing import Any

import torch
from torch import nn


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


class CpuHostedTextEncoder(nn.Module):
    """Держит веса энкодера в RAM, но для пайплайна притворяется GPU-модулем."""

    def __init__(
        self,
        module: nn.Module,
        compute_device: torch.device,
        compute_dtype: torch.dtype,
        weight_dtype: torch.dtype = torch.float32,
    ) -> None:
        super().__init__()
        self.module = module.to(device="cpu", dtype=weight_dtype).eval()
        self._compute_device = compute_device
        self._compute_dtype = compute_dtype
        self._weight_dtype = weight_dtype

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
        kwargs = {k: _relocate(v, cpu, self._weight_dtype) for k, v in kwargs.items()}
        output = self.module(*args, **kwargs)
        return _relocate(output, self._compute_device, self._compute_dtype)
