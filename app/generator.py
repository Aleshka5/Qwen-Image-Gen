"""Фасад клиента инференса.

Имена здесь — те, что ждут ``app.routes`` и ``app.__init__``: раньше этот модуль
держал пайплайн и грузил torch, теперь он только реэкспортирует HTTP-клиент из
:mod:`app.inference`. Модуль остался отдельным, чтобы импорт в роутах и
подмена клиента в тестах выглядели как прежде.
"""

from __future__ import annotations

from .inference import (
    GenerationRequest,
    GenerationResult,
    InferenceClient,
    PipelineError,
    generator,
)

__all__ = [
    "GenerationRequest",
    "GenerationResult",
    "InferenceClient",
    "PipelineError",
    "generator",
]
