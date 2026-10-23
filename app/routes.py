"""HTTP boundary. The gateway already decided who may call. We only read the id it injected."""

from __future__ import annotations

import json
import re
from pathlib import Path

from flask import Blueprint, current_app, jsonify, render_template, request, send_file
from io import BytesIO

from qimg.errors import AppError
from qimg.service import Service

bp = Blueprint("web", __name__)

_USER_ID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_ACCOUNT_NAME = re.compile(r"[\x00-\x1f\x7f]")
_UI = Path(__file__).resolve().parent / "static" / "ui" / ".vite" / "manifest.json"


def _caller() -> tuple[str | None, str | None]:
    """Identity the gateway stamped on the request. Not a credential check."""
    raw = (request.headers.get("X-Auth-User-Id") or "").strip()
    user = raw if _USER_ID.fullmatch(raw) else None
    email = (request.headers.get("X-Auth-Email") or "").strip() or None
    return user, email


def _account_name() -> str | None:
    raw = request.headers.get("X-Auth-Name")
    if not raw or _ACCOUNT_NAME.search(raw):
        return None
    name = " ".join(raw.split())
    return name[:80] or None


def _user() -> tuple[str, str | None]:
    user, email = _caller()
    if not user:
        raise AppError("A user id is required")
    return user, email


def _svc() -> Service:
    return Service(current_app.extensions["qimg"])


def _ui_assets() -> tuple[list[str], list[str]]:
    if not _UI.exists():
        return [], []
    entry = json.loads(_UI.read_text(encoding="utf-8")).get("index.html") or {}
    scripts = [f"/static/ui/{entry['file']}"] if entry.get("file") else []
    styles = [f"/static/ui/{name}" for name in entry.get("css") or []]
    return scripts, styles


@bp.get("/")
def index():
    scripts, styles = _ui_assets()
    return render_template(
        "index.html",
        account_name=_account_name(),
        scripts=scripts,
        styles=styles,
    )


@bp.get("/healthz")
def healthz():
    from app.config import settings
    from app.inference import InferenceClient

    client = InferenceClient()
    snapshot = client._snapshot()
    return jsonify(
        status="ok",
        model=settings.model_id,
        memory_mode=snapshot.get("memory_mode") or settings.memory_mode,
        loaded=bool(snapshot.get("loaded")),
        load_error=snapshot.get("load_error"),
        inference=settings.inference_url,
    )


@bp.get("/api/config")
def api_config():
    return jsonify(_svc().config())


@bp.get("/api/catalog")
def api_catalog():
    return jsonify(_svc().catalog())


@bp.get("/api/settings")
def api_settings():
    user, _email = _user()
    return jsonify(_svc().get_settings(user))


@bp.post("/api/settings/providers")
def api_create_provider():
    user, _email = _user()
    return jsonify(_svc().create_provider(user, request.get_json(silent=True) or {}))


@bp.patch("/api/settings/providers/<provider_id>")
def api_update_provider(provider_id: str):
    user, _email = _user()
    return jsonify(_svc().update_provider(user, provider_id, request.get_json(silent=True) or {}))


@bp.delete("/api/settings/providers/<provider_id>")
def api_delete_provider(provider_id: str):
    user, _email = _user()
    return jsonify(_svc().delete_provider(user, provider_id))


@bp.post("/api/settings/providers/<provider_id>/default")
def api_default_provider(provider_id: str):
    user, _email = _user()
    return jsonify(_svc().make_default(user, provider_id))


@bp.get("/api/projects")
def api_projects():
    user, _email = _user()
    return jsonify(_svc().list_projects(user))


@bp.post("/api/projects")
def api_create_project():
    user, _email = _user()
    project = _svc().create_project(
        user,
        (request.form.get("kind") or "").strip(),
        request.files.get("file"),
        request.form.get("name"),
    )
    return jsonify(project)


@bp.get("/api/projects/<project_id>")
def api_project(project_id: str):
    user, _email = _user()
    return jsonify(_svc().get_project(user, project_id))


@bp.patch("/api/projects/<project_id>")
def api_rename_project(project_id: str):
    user, _email = _user()
    body = request.get_json(silent=True) or {}
    return jsonify(_svc().rename_project(user, project_id, body.get("name") or ""))


@bp.delete("/api/projects/<project_id>")
def api_delete_project(project_id: str):
    user, _email = _user()
    return jsonify(_svc().delete_project(user, project_id))


@bp.get("/api/projects/<project_id>/preview")
def api_project_preview(project_id: str):
    user, _email = _user()
    data, mime = _svc().project_preview(user, project_id)
    return send_file(BytesIO(data), mimetype=mime)


@bp.post("/api/projects/<project_id>/shapes")
def api_create_shape(project_id: str):
    user, _email = _user()
    return jsonify(_svc().create_shape(user, project_id, request.get_json(silent=True) or {}))


@bp.post("/api/projects/<project_id>/commit")
def api_commit(project_id: str):
    user, _email = _user()
    body = request.get_json(silent=True) or {}
    return jsonify(_svc().commit(user, project_id, body.get("parent_id")))


@bp.get("/api/projects/<project_id>/variants")
def api_project_variants(project_id: str):
    user, _email = _user()
    return jsonify(_svc().project_variants(user, project_id))


@bp.post("/api/projects/<project_id>/select")
def api_select_project(project_id: str):
    user, _email = _user()
    body = request.get_json(silent=True) or {}
    return jsonify(_svc().select_project_variant(user, project_id, body.get("variant_id") or ""))


@bp.patch("/api/shapes/<shape_id>")
def api_update_shape(shape_id: str):
    user, _email = _user()
    return jsonify(_svc().update_shape(user, shape_id, request.get_json(silent=True) or {}))


@bp.delete("/api/shapes/<shape_id>")
def api_delete_shape(shape_id: str):
    user, _email = _user()
    return jsonify(_svc().delete_shape(user, shape_id))


@bp.post("/api/shapes/<shape_id>/type")
def api_shape_type(shape_id: str):
    user, _email = _user()
    body = request.get_json(silent=True) or {}
    return jsonify(_svc().set_type(user, shape_id, body.get("type") or ""))


@bp.get("/api/shapes/<shape_id>/source")
def api_shape_source(shape_id: str):
    user, _email = _user()
    data, mime = _svc().shape_source(user, shape_id, as_preview=request.args.get("preview") == "1")
    return send_file(BytesIO(data), mimetype=mime)


@bp.get("/api/shapes/<shape_id>/variants")
def api_shape_variants(shape_id: str):
    user, _email = _user()
    return jsonify(_svc().shape_variants(user, shape_id))


@bp.post("/api/shapes/<shape_id>/select")
def api_select_shape(shape_id: str):
    user, _email = _user()
    body = request.get_json(silent=True) or {}
    return jsonify(_svc().select_shape_variant(user, shape_id, body.get("variant_id") or ""))


@bp.post("/api/generations")
def api_generations():
    user, email = _user()
    payload = _svc().submit(
        user,
        request.form,
        request.files.get("mask"),
        request.files.getlist("images"),
    )
    if email:
        for task in payload["tasks"]:
            current_app.extensions["qimg"].store.update("tasks", task["id"], email=email)
    return jsonify(payload)


@bp.get("/api/tasks/<task_id>")
def api_task(task_id: str):
    user, _email = _user()
    return jsonify(_svc().get_task(user, task_id))


@bp.get("/api/feed")
def api_feed():
    user, _email = _user()
    return jsonify(_svc().feed(user))


@bp.post("/api/renders")
def api_render():
    user, _email = _user()
    body = request.get_json(silent=True) or {}
    return jsonify(_svc().render(user, body.get("scope") or "", body.get("project_id"), body.get("shape_id")))


@bp.get("/api/media/<path:key>")
def api_media(key: str):
    user, _email = _user()
    data, mime = _svc().media(user, key)
    return send_file(BytesIO(data), mimetype=mime)
