"""Flask factory. Pages, JSON, and the three shared-backend clients. No weights."""

from __future__ import annotations

import logging
import threading

from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from qimg.errors import AppError
from qimg.runtime import build_runtime

from .config import settings
from .routes import bp

log = logging.getLogger(__name__)


def _configure_logging() -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


def _probe_inference() -> None:
    from .inference import InferenceClient

    def worker() -> None:
        try:
            InferenceClient().ensure_loaded()
        except Exception:
            log.exception("Inference did not answer at startup")

    threading.Thread(target=worker, name="inference-probe", daemon=True).start()


def create_app(runtime=None) -> Flask:
    _configure_logging()
    app = Flask(__name__)
    app.extensions["qimg"] = runtime or build_runtime()
    app.config["MAX_CONTENT_LENGTH"] = settings.max_content_length
    app.config["JSON_SORT_KEYS"] = False
    app.register_blueprint(bp)

    @app.errorhandler(AppError)
    def _on_app(exc: AppError):
        return jsonify(error=exc.message), exc.status

    @app.errorhandler(RequestEntityTooLarge)
    def _on_large(_exc):
        return jsonify(error=f"Upload is larger than {settings.max_upload_mb} MB"), 413

    @app.errorhandler(Exception)
    def _on_unexpected(exc: Exception):
        if isinstance(exc, HTTPException):
            return exc
        log.exception("Unhandled error")
        return jsonify(error=f"Internal error: {type(exc).__name__}"), 500

    if settings.preload_model:
        _probe_inference()
    return app
