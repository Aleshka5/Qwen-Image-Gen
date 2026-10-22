"""Типы запроса и результата инференса.

Без torch: их импортирует и HTTP-слой (``inference/app.py``), который обязан
собирать запрос и рассылать ответ, не поднимая CUDA. Сам пайплайн берёт их
отсюда же, поэтому у Flask-оркестратора и контейнера инференса одно описание
того, что пересекает границу.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image


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
