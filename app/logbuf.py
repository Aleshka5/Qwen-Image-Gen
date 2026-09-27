"""Кольцевой буфер логов для веб-страницы во время генерации.

Gunicorn держит один воркер и несколько потоков: генерация занимает поток,
а ``GET /api/logs`` отвечает из другого. Перезапуск контейнера для этого не нужен.
"""

from __future__ import annotations

import logging
import threading
from collections import deque

_CAPACITY = 400

_buffer: "LiveLogBuffer | None" = None


class LiveLogBuffer(logging.Handler):
    def __init__(self, capacity: int = _CAPACITY) -> None:
        super().__init__(level=logging.INFO)
        self._guard = threading.Lock()
        self._lines: deque[dict[str, object]] = deque(maxlen=capacity)
        self._next_id = 1

    def emit(self, record: logging.LogRecord) -> None:
        if not record.name.startswith("app"):
            return
        try:
            message = record.getMessage()
        except Exception:
            self.handleError(record)
            return
        with self._guard:
            line = {
                "id": self._next_id,
                "level": record.levelname,
                "message": message,
            }
            self._next_id += 1
            self._lines.append(line)

    def since(self, after: int) -> list[dict[str, object]]:
        with self._guard:
            return [dict(line) for line in self._lines if int(line["id"]) > after]


def live_logs() -> LiveLogBuffer:
    global _buffer
    if _buffer is None:
        _buffer = LiveLogBuffer()
        logging.getLogger().addHandler(_buffer)
    return _buffer
