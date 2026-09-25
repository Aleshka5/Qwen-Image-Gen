"""Фабрика Flask-приложения."""

from __future__ import annotations

import logging
import threading

from flask import Flask, jsonify

from .config import settings
from .generator import generator
from .routes import bp

log = logging.getLogger(__name__)


def _configure_logging() -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


def _preload_in_background() -> None:
    """Прогреваем модель в фоне, чтобы первый запрос не ждал загрузку весов."""

    def worker() -> None:
        try:
            generator.ensure_loaded()
        except Exception:
            log.exception("Предзагрузка модели не удалась; повтор произойдёт на первом запросе")

    threading.Thread(target=worker, name="model-preload", daemon=True).start()


def create_app() -> Flask:
    _configure_logging()

    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = settings.max_content_length
    app.config["JSON_SORT_KEYS"] = False
    app.register_blueprint(bp)

    @app.errorhandler(Exception)
    def _on_unexpected(exc: Exception):
        log.exception("Необработанная ошибка")
        return jsonify(error=f"Внутренняя ошибка: {type(exc).__name__}"), 500

    if settings.preload_model:
        _preload_in_background()

    return app
