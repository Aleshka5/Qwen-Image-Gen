"""Живой поток логов одного запроса: ``logging`` -> очередь -> SSE.

Строки остаются теми же, что браузер видел у монолита: их пишет тот же
``logging`` из тех же мест пайплайна. Изменился только адресат — раньше запись
оседала в кольцевом буфере Flask-процесса, теперь она уезжает в поток ответа
``POST /v1/generate`` и оттуда попадает в буфер оркестратора.

Запись выбирается по префиксу имени логгера: ``inference.*`` — это и есть
пайплайн (``inference.pipeline.generator``) и HTTP-слой (``inference.app``),
ровно то, что монолит отдавал по префиксу ``app``. Чужие логи (diffusers,
transformers, urllib) идут только в консоль контейнера.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import queue
from dataclasses import dataclass
from typing import Any, Iterator

#: Префикс имён логгеров, которые уезжают клиенту.
PREFIX = "inference"

#: Маркер конца потока.
STOP = object()

_sink: contextvars.ContextVar["LogStream | None"] = contextvars.ContextVar("qwen_log_sink", default=None)

#: Всего стадий в шкале прогресса (см. docs/change-requests/0001-inference-progress-events.md).
STAGES = 10


@dataclass(frozen=True, slots=True)
class Progress:
    """Стадия пайплайна в той же очереди, что и логи: ``event: progress``."""

    stage: int
    name: str
    total: int = STAGES

    def payload(self) -> dict[str, Any]:
        return {"stage": self.stage, "total": self.total, "name": self.name}


class LogStream:
    """Очередь одного запроса. Писатель — поток генерации, читатель — SSE."""

    def __init__(self) -> None:
        self._queue: queue.Queue[object] = queue.Queue()
        self._closed = False

    def offer(self, level: str, message: str) -> None:
        if not self._closed:
            self._queue.put((level, message))

    def offer_progress(self, stage: int, name: str, total: int = STAGES) -> None:
        if not self._closed:
            self._queue.put(Progress(stage=int(stage), name=str(name), total=int(total)))

    def take(self, timeout: float | None = None) -> object:
        """Следующая запись, :data:`STOP`, либо ``None`` по таймауту (для keepalive)."""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._queue.put(STOP)


@contextlib.contextmanager
def bind(stream: LogStream) -> Iterator[LogStream]:
    """Привязывает поток к текущему контексту — и к потоку, запущенному с его копией."""
    token = _sink.set(stream)
    try:
        yield stream
    finally:
        _sink.reset(token)


def progress(stage: int, name: str, total: int = STAGES) -> dict[str, Any] | None:
    """Кладёт стадию в поток запроса. Без потока (тест, CLI, прогрев) — тишина.

    Возвращает отправленный словарь, чтобы вызывающий мог проверить шкалу
    без разбора очереди.
    """
    payload = {"stage": int(stage), "total": int(total), "name": str(name)}
    stream = _sink.get()
    if stream is not None:
        stream.offer_progress(payload["stage"], payload["name"], payload["total"])
    return payload


class StreamHandler(logging.Handler):
    """Отдаёт записи ``inference.*`` в поток, привязанный к текущему контексту."""

    def __init__(self, prefix: str = PREFIX) -> None:
        super().__init__(level=logging.INFO)
        self._prefix = prefix

    def emit(self, record: logging.LogRecord) -> None:
        stream = _sink.get()
        if stream is None or not record.name.startswith(self._prefix):
            return
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - битый LogRecord
            self.handleError(record)
            return
        stream.offer(record.levelname, message)


_handler: StreamHandler | None = None


def install_handler() -> StreamHandler:
    """Ставит обработчик один раз на процесс: на корневой логгер, уровнем выше всех."""
    global _handler
    if _handler is None:
        _handler = StreamHandler()
        logging.getLogger().addHandler(_handler)
    return _handler
