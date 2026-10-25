"""Use cases shared by the HTTP process. Mongo is the record; the queue is a nudge."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from PIL import Image

from app.config import settings
from app.imaging import (
    ASPECT_RATIOS,
    ValidationError,
    _read_rgb,
    load_uploads,
    parse_custom_size,
    size_from_quality,
)

from .catalog import model_ids, model_spec, public_catalog
from .crypto import hint, seal
from .errors import AppError
from .geometry import LEVEL1_COLORS as PALETTE
from .geometry import PHOTO_COLOR as PHOTO
from .geometry import TEXT_COLOR as TEXT
from .geometry import Box, absolute_box, clamp_box, hits_any
from .pictures import circle_mask, crop_box, load_png, png_bytes, preview, render_tree

_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_SEED_MAX = 2**31 - 1


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _default_name() -> str:
    return datetime.now(ZoneInfo("Europe/Moscow")).strftime("%Y-%m-%d %H:%M")


def _new_id() -> str:
    return str(uuid.uuid4())


def _fingerprint(meta: dict, blobs: list[bytes]) -> str:
    """Hash the user-controlled request. Random seeds are not in ``meta``."""
    hasher = hashlib.sha256()
    hasher.update(json.dumps(meta, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    for data in blobs:
        hasher.update(hashlib.sha256(data).digest())
    return hasher.hexdigest()


class Service:
    def __init__(self, runtime) -> None:
        self.rt = runtime

    # ---------------------------------------------------------------- config
    def config(self) -> dict:
        return {
            "max_shapes": settings.max_shapes,
            "max_copies": settings.max_copies,
            "max_images": settings.max_images,
            "max_steps": settings.max_steps,
            "default_steps": settings.default_steps,
            "default_cfg": settings.default_true_cfg_scale,
            "model": settings.model_id,
            "aspects": [
                {"key": ratio.key, "width": ratio.width, "height": ratio.height}
                for ratio in ASPECT_RATIOS
            ],
            "level1_colors": list(PALETTE),
            "photo_color": PHOTO,
            "text_color": TEXT,
        }

    def catalog(self) -> dict:
        return public_catalog()

    # -------------------------------------------------------------- settings
    def get_settings(self, user: str) -> dict:
        return {"providers": [self._public_provider(item) for item in self._ensure_settings(user)["providers"]]}

    def create_provider(self, user: str, body: dict) -> dict:
        doc = self._ensure_settings(user)
        kind = body.get("kind")
        if kind not in {"wan", "higgsfield"}:
            raise AppError("Add Wan or Higgsfield. Local Qwen is already available.")
        provider = self._provider_from_body(kind, body, current=None)
        provider["id"] = _new_id()
        if body.get("is_default"):
            for item in doc["providers"]:
                item["is_default"] = False
            provider["is_default"] = True
        doc["providers"].append(provider)
        self.rt.store.update("settings", user, providers=doc["providers"])
        return self._public_provider(provider)

    def update_provider(self, user: str, provider_id: str, body: dict) -> dict:
        doc = self._ensure_settings(user)
        current = self._find_provider(doc, provider_id)
        if current["kind"] == "qwen" and body.get("kind") not in (None, "qwen"):
            raise AppError("Local Qwen stays a local provider")
        merged = self._provider_from_body(current["kind"], body, current=current)
        merged["id"] = current["id"]
        if body.get("is_default"):
            for item in doc["providers"]:
                item["is_default"] = item["id"] == provider_id
        doc["providers"] = [merged if item["id"] == provider_id else item for item in doc["providers"]]
        self.rt.store.update("settings", user, providers=doc["providers"])
        return self._public_provider(merged)

    def delete_provider(self, user: str, provider_id: str) -> dict:
        doc = self._ensure_settings(user)
        current = self._find_provider(doc, provider_id)
        if current["kind"] == "qwen":
            raise AppError("Local Qwen cannot be removed")
        providers = [item for item in doc["providers"] if item["id"] != provider_id]
        if current.get("is_default"):
            for item in providers:
                if item["kind"] == "qwen":
                    item["is_default"] = True
        self.rt.store.update("settings", user, providers=providers)
        return {"deleted": True}

    def make_default(self, user: str, provider_id: str) -> dict:
        doc = self._ensure_settings(user)
        self._find_provider(doc, provider_id)
        for item in doc["providers"]:
            item["is_default"] = item["id"] == provider_id
        self.rt.store.update("settings", user, providers=doc["providers"])
        return self.get_settings(user)

    # --------------------------------------------------------------- projects
    def list_projects(self, user: str) -> dict:
        docs = [
            doc for doc in self.rt.store.query("projects", user_id=user)
            if not doc.get("deleted_at")
        ]
        docs.sort(key=lambda doc: doc["created_at"], reverse=True)
        return {"projects": [self._project_json(doc) for doc in docs]}

    def create_project(self, user: str, kind: str, upload, name: str | None) -> dict:
        if kind not in {"photo", "scratch"}:
            raise AppError("Kind must be photo or scratch")
        width = height = None
        key = None
        project_id = _new_id()
        if kind == "photo":
            if upload is None or not getattr(upload, "filename", None):
                raise AppError("Choose one photo")
            try:
                image = _read_rgb(upload, index=1)
            except ValidationError as exc:
                raise AppError(str(exc)) from exc
            width, height = image.size
            key = f"{user}/projects/{project_id}/source.png"
            self.rt.blobs.put(key, png_bytes(image))
        doc = {
            "id": project_id,
            "user_id": user,
            "name": (name or "").strip() or _default_name(),
            "kind": kind,
            "source_key": key,
            "width": width,
            "height": height,
            "geometry_locked": False,
            "selected_variant_id": None,
            "created_at": _now(),
            "updated_at": _now(),
            "deleted_at": None,
        }
        self.rt.store.insert("projects", doc)
        return self._project_json(doc)

    def get_project(self, user: str, project_id: str) -> dict:
        project = self._project(user, project_id)
        shapes = self._live_shapes(user, project_id)
        return {"project": self._project_json(project), "shapes": [self._shape_json(s) for s in shapes]}

    def rename_project(self, user: str, project_id: str, name: str) -> dict:
        self._project(user, project_id)
        cleaned = " ".join((name or "").split())
        if not cleaned:
            raise AppError("Enter a name")
        doc = self.rt.store.update("projects", project_id, name=cleaned[:80], updated_at=_now())
        return self._project_json(doc)

    def delete_project(self, user: str, project_id: str) -> dict:
        project = self._project(user, project_id)
        self.rt.store.update("projects", project_id, deleted_at=_now())
        for task in self.rt.store.query("tasks", project_id=project_id, user_id=user):
            if task["status"] in {"queued", "pending"}:
                self.rt.store.update("tasks", task["id"], status="invalidated", error="Project deleted")
            for artifact in task.get("artifacts") or []:
                self.rt.blobs.delete(artifact["key"])
        for shape in self.rt.store.query("shapes", project_id=project_id):
            self.rt.store.update("shapes", shape["id"], deleted_at=_now())
        for variant in self.rt.store.query("variants", project_id=project_id):
            if variant.get("key"):
                self.rt.blobs.delete(variant["key"])
        if project.get("source_key"):
            self.rt.blobs.delete(project["source_key"])
        return {"deleted": True}

    def project_preview(self, user: str, project_id: str) -> tuple[bytes, str]:
        project = self._project(user, project_id)
        if not project.get("source_key"):
            raise AppError("This project has no photo", 404)
        image = load_png(self._blob(project["source_key"]))
        return png_bytes(preview(image)), "image/png"

    # ----------------------------------------------------------------- shapes
    def create_shape(self, user: str, project_id: str, body: dict) -> dict:
        project = self._project(user, project_id)
        if project["kind"] != "photo":
            raise AppError("A scratch project has no shapes")
        parent = self._parent(user, project, body.get("parent_id"))
        self._require_unlocked(project, parent)
        kind = body.get("kind") or "rect"
        siblings = self._siblings(user, project_id, parent["id"] if parent else None)
        if len(siblings) >= settings.max_shapes:
            raise AppError(f"You can add up to {settings.max_shapes} shapes")
        if kind == "full_frame":
            if siblings:
                raise AppError("Use entire frame only when no other shapes are here")
            box = Box(0, 0, 1, 1, "full_frame")
        elif kind == "circle":
            if parent is None:
                raise AppError("Circles belong on a part, not on the whole photo")
            box = clamp_box(self._box(body, "circle"))
        elif kind == "rect":
            box = clamp_box(self._box(body, "rect"))
        else:
            raise AppError("Kind must be rect, circle, or full_frame")
        color = PHOTO if parent else self._next_color(siblings)
        doc = {
            "id": _new_id(),
            "project_id": project_id,
            "user_id": user,
            "parent_id": parent["id"] if parent else None,
            "kind": kind,
            "type": None if parent is None else "photo",
            "color": color,
            "x": box.x, "y": box.y, "w": box.w, "h": box.h,
            "saved": False,
            "geometry_locked": False,
            "selected_variant_id": None,
            "created_at": _now(),
            "deleted_at": None,
        }
        self.rt.store.insert("shapes", doc)
        return self._shape_json(doc)

    def update_shape(self, user: str, shape_id: str, body: dict) -> dict:
        shape = self._shape(user, shape_id)
        project = self._project(user, shape["project_id"])
        if shape["kind"] == "full_frame":
            raise AppError("The full frame already covers the photo")
        saved = bool(shape["saved"])
        kind = shape["kind"]
        if saved and ("w" in body or "h" in body):
            if abs(float(body.get("w", shape["w"])) - shape["w"]) > 1e-4 or abs(float(body.get("h", shape["h"])) - shape["h"]) > 1e-4:
                raise AppError("Saved shapes keep their size")
        box = clamp_box(Box(
            float(body.get("x", shape["x"])),
            float(body.get("y", shape["y"])),
            float(shape["w"] if saved else body.get("w", shape["w"])),
            float(shape["h"] if saved else body.get("h", shape["h"])),
            kind,
        ))
        updated = self.rt.store.update("shapes", shape_id, x=box.x, y=box.y, w=box.w, h=box.h)
        if saved:
            self._rewrite_originals(project, updated)
        return self._shape_json(updated)

    def delete_shape(self, user: str, shape_id: str) -> dict:
        shape = self._shape(user, shape_id)
        doomed = [shape, *self._descendants(user, shape)]
        for item in doomed:
            self.rt.store.update("shapes", item["id"], deleted_at=_now())
            for task in self.rt.store.query("tasks", shape_id=item["id"]):
                if task["status"] in {"queued", "pending"}:
                    self.rt.store.update("tasks", task["id"], status="invalidated", error="Object deleted")
            for variant in self.rt.store.query("variants", shape_id=item["id"]):
                if variant.get("key"):
                    self.rt.blobs.delete(variant["key"])
        return {"deleted": True}

    def commit(self, user: str, project_id: str, parent_id) -> dict:
        project = self._project(user, project_id)
        parent = self._parent(user, project, parent_id)
        self._require_unlocked(project, parent)
        siblings = self._siblings(user, project_id, parent["id"] if parent else None)
        if not siblings:
            raise AppError("Add a shape before saving")
        boxes = [self._as_box(shape) for shape in siblings]
        for index, box in enumerate(boxes):
            if hits_any(box, boxes[index + 1:]):
                raise AppError("Shapes overlap. Move them apart before saving.")
        for shape in siblings:
            self.rt.store.update("shapes", shape["id"], saved=True)
            fresh = self._shape(user, shape["id"])
            self._write_original(project, fresh)
        if parent is None:
            self.rt.store.update("projects", project_id, geometry_locked=True)
        else:
            self.rt.store.update("shapes", parent["id"], geometry_locked=True)
        return self.get_project(user, project_id)

    def set_type(self, user: str, shape_id: str, type_name: str) -> dict:
        shape = self._shape(user, shape_id)
        if not shape.get("parent_id"):
            raise AppError("Parts do not have a photo or text type")
        if type_name not in {"photo", "text"}:
            raise AppError("Type must be photo or text")
        color = TEXT if type_name == "text" else PHOTO
        updated = self.rt.store.update("shapes", shape_id, type=type_name, color=color)
        return self._shape_json(updated)

    def shape_source(self, user: str, shape_id: str, *, as_preview: bool) -> tuple[bytes, str]:
        shape = self._shape(user, shape_id)
        project = self._project(user, shape["project_id"])
        image = load_png(self._crop_bytes(project, shape))
        if as_preview:
            image = preview(image)
        return png_bytes(image), "image/png"

    def shape_variants(self, user: str, shape_id: str) -> dict:
        shape = self._shape(user, shape_id)
        return {"variants": self._variant_list(shape["selected_variant_id"], shape_id=shape_id)}

    def project_variants(self, user: str, project_id: str) -> dict:
        project = self._project(user, project_id)
        rows = [
            item for item in self.rt.store.query("variants", project_id=project_id)
            if not item.get("shape_id")
        ]
        return {"variants": self._rows(project.get("selected_variant_id"), rows)}

    def select_shape_variant(self, user: str, shape_id: str, variant_id: str) -> dict:
        shape = self._shape(user, shape_id)
        variant = self._variant(user, variant_id)
        if variant.get("shape_id") != shape_id:
            raise AppError("That variant belongs to another object")
        self.rt.store.update("shapes", shape_id, selected_variant_id=variant_id)
        return self.shape_variants(user, shape_id)

    def select_project_variant(self, user: str, project_id: str, variant_id: str) -> dict:
        self._project(user, project_id)
        variant = self._variant(user, variant_id)
        if variant.get("project_id") != project_id or variant.get("shape_id"):
            raise AppError("That variant belongs to another project")
        self.rt.store.update("projects", project_id, selected_variant_id=variant_id)
        return self.project_variants(user, project_id)

    # ------------------------------------------------------------------- tasks
    def submit(self, user: str, fields: dict, mask_file, references: list) -> dict:
        project = self._project(user, fields.get("project_id") or "")
        mode = (fields.get("mode") or "").strip()
        if mode not in {"generate", "customize", "mask_fill"}:
            raise AppError("Mode must be generate, customize, or mask_fill")
        shape = None
        if fields.get("shape_id"):
            shape = self._shape(user, fields["shape_id"])
            if shape["project_id"] != project["id"]:
                raise AppError("Shape is not in this project")
        if project["kind"] == "scratch" and (mode != "generate" or shape is not None):
            raise AppError("Generate from scratch has no parts")
        if project["kind"] == "photo" and mode == "generate":
            raise AppError("Open a scratch project to generate from scratch")
        if mode != "generate" and shape is None:
            raise AppError("Choose an object")
        if shape is not None and not shape.get("parent_id"):
            raise AppError("Generate inside a part, not on the part itself")
        if shape is not None and shape.get("type") == "text":
            raise AppError("Text editing arrives with the OCR workspace")
        provider = self._enabled_provider(user, fields.get("provider_id") or "")
        spec = model_spec(provider["kind"], (fields.get("model") or "").strip())
        if spec is None or (fields.get("model") not in (provider.get("models") or [fields.get("model")])):
            raise AppError("Choose a model this provider offers")
        if mode not in spec["modes"]:
            raise AppError(f"{spec['label']} does not support this form")
        prompt = self._prompt(fields.get("prompt"), required=True)
        negative = self._prompt(fields.get("negative_prompt"), required=False)
        steps = self._int(fields.get("steps"), settings.default_steps, 1, settings.max_steps, "steps")
        cfg = self._float(fields.get("true_cfg_scale"), settings.default_true_cfg_scale)
        copies = self._int(fields.get("copies"), 1, 1, settings.max_copies, "copies")
        explicit_seed = self._optional_seed(fields.get("seed")) if copies == 1 else None
        parts = self._parts(project, shape, mode, mask_file, references, spec)
        width, height = self._frame(fields, mode, parts)
        meta = {
            "user": user,
            "mode": mode,
            "kind": provider["kind"],
            "model": spec["id"],
            "prompt": prompt,
            "negative": negative,
            "steps": steps,
            "cfg": cfg,
            "width": width,
            "height": height,
            "copies": copies,
            "project_id": project["id"],
            "shape_id": shape["id"] if shape else None,
        }
        if explicit_seed is not None:
            meta["seed"] = explicit_seed
        key = _fingerprint(meta, [data for _role, data in parts])
        existing = self._reusable(user, key)
        if existing:
            return {"reused": True, "tasks": [self._task_json(item) for item in existing]}
        batch = _new_id()
        artifacts = []
        for index, (role, data) in enumerate(parts):
            key_name = f"{user}/batches/{batch}/{index}.png"
            self.rt.blobs.put(key_name, data)
            artifacts.append({"role": role, "key": key_name, "position": index})
        tasks = []
        for index in range(copies):
            seed = explicit_seed if explicit_seed is not None else secrets.randbelow(_SEED_MAX)
            task = {
                "id": _new_id(),
                "user_id": user,
                "project_id": project["id"],
                "shape_id": shape["id"] if shape else None,
                "batch_id": batch,
                "idempotency_key": key,
                "type": "gen",
                "status": "queued",
                "mode": mode,
                "provider_id": provider["id"],
                "provider_kind": provider["kind"],
                "model": spec["id"],
                "prompt": prompt,
                "negative_prompt": negative,
                "steps": steps,
                "true_cfg_scale": cfg,
                "seed": seed,
                "width": width,
                "height": height,
                "copies": copies,
                "copy_index": index,
                "artifacts": artifacts,
                "progress": {"stage": 0, "total": 10, "name": "queued"} if provider["kind"] == "qwen" else None,
                "error": None,
                "result_url": None,
                "created_at": _now(),
            }
            self.rt.store.insert("tasks", task)
            try:
                self.rt.queue.publish("intake", {"task_id": task["id"], "artifacts": artifacts})
            except Exception as exc:
                self.rt.store.update("tasks", task["id"], status="failed", error="Queue rejected the task")
                raise AppError(f"Queue rejected the task: {exc}", 503) from exc
            tasks.append(task)
        return {"reused": False, "tasks": [self._task_json(item) for item in tasks]}

    def get_task(self, user: str, task_id: str) -> dict:
        task = self.rt.store.get("tasks", task_id)
        if task is None or task["user_id"] != user:
            raise AppError("Task not found", 404)
        return self._task_json(task)

    def feed(self, user: str) -> dict:
        tasks = self.rt.store.query("tasks", user_id=user)
        tasks.sort(key=lambda item: item["created_at"], reverse=True)
        return {"tasks": [self._task_json(item) for item in tasks[:50]]}

    # ------------------------------------------------------------------ render
    def render(self, user: str, scope: str, project_id: str | None, shape_id: str | None) -> dict:
        if scope == "shape":
            shape = self._shape(user, shape_id or "")
            project = self._project(user, shape["project_id"])
            image = self._render_shape(project, shape)
        elif scope == "project":
            project = self._project(user, project_id or "")
            if project["kind"] == "scratch":
                return self._render_scratch(project)
            if not project.get("source_key"):
                raise AppError("This project has no photo")
            source = load_png(self._blob(project["source_key"]))
            shapes = self._live_shapes(user, project["id"])
            chosen = self._chosen_images(shapes)
            image = render_tree(source, shapes, chosen)
        else:
            raise AppError("Scope must be project or shape")
        key = f"{user}/renders/{_new_id()}.png"
        self.rt.blobs.put(key, png_bytes(image.convert("RGBA")))
        return {"url": f"/api/media/{key}"}

    def media(self, user: str, key: str) -> tuple[bytes, str]:
        if not key.startswith(f"{user}/") or ".." in key:
            raise AppError("File not found", 404)
        found = self.rt.blobs.get(key)
        if found is None:
            raise AppError("File not found", 404)
        return found

    # ---------------------------------------------------------------- helpers
    def _ensure_settings(self, user: str) -> dict:
        doc = self.rt.store.get("settings", user)
        if doc:
            return doc
        local = {
            "id": "local",
            "kind": "qwen",
            "label": "Local Qwen",
            "enabled": True,
            "is_default": True,
            "models": ["qwen-image-2.1"],
            "key_hint": "",
            "region": "",
            "workspace_id": "",
        }
        doc = {"id": user, "user_id": user, "providers": [local]}
        self.rt.store.insert("settings", doc)
        return doc

    def _provider_from_body(self, kind: str, body: dict, current: dict | None) -> dict:
        models = body.get("models", None if current is None else current.get("models"))
        known = set(model_ids(kind))
        if not isinstance(models, list):
            models = list(known)
        cleaned = [model for model in models if model in known]
        provider = {
            "id": current["id"] if current else "",
            "kind": kind,
            "label": (body.get("label") or (current or {}).get("label") or kind)[:80],
            "enabled": bool(body.get("enabled", True if current is None else current.get("enabled", True))),
            "is_default": bool(body.get("is_default", False if current is None else current.get("is_default"))),
            "models": cleaned,
            "key_hint": (current or {}).get("key_hint") or "",
            "region": body.get("region", (current or {}).get("region") or ""),
            "workspace_id": body.get("workspace_id", (current or {}).get("workspace_id") or ""),
            "secret_enc": (current or {}).get("secret_enc"),
        }
        if kind == "wan":
            api_key = (body.get("api_key") or "").strip()
            if api_key:
                provider["secret_enc"] = self._seal(api_key)
                provider["key_hint"] = hint(api_key)
            elif not provider["secret_enc"]:
                raise AppError("Enter the Wan API key")
            if provider["region"] not in {"singapore", "virginia", "beijing", "frankfurt"}:
                raise AppError("Choose a Wan region")
            if not str(provider["workspace_id"]).strip():
                raise AppError("Enter the Wan workspace id")
        elif kind == "higgsfield":
            key_id = (body.get("key_id") or "").strip()
            key_secret = (body.get("key_secret") or "").strip()
            if key_id and key_secret:
                provider["secret_enc"] = self._seal(f"{key_id}:{key_secret}")
                provider["key_hint"] = hint(key_secret)
            elif not provider["secret_enc"]:
                raise AppError("Enter the Higgsfield key id and secret")
        return provider

    def _seal(self, value: str) -> str:
        if not settings.settings_secret:
            raise AppError("SETTINGS_SECRET is not configured")
        return seal(settings.settings_secret, value)

    def _find_provider(self, doc: dict, provider_id: str) -> dict:
        for item in doc["providers"]:
            if item["id"] == provider_id:
                return item
        raise AppError("Provider not found", 404)

    def _enabled_provider(self, user: str, provider_id: str) -> dict:
        doc = self._ensure_settings(user)
        provider = self._find_provider(doc, provider_id)
        if not provider.get("enabled"):
            raise AppError("That provider is disabled")
        return provider

    def _public_provider(self, provider: dict) -> dict:
        return {
            "id": provider["id"],
            "kind": provider["kind"],
            "label": provider["label"],
            "enabled": provider.get("enabled", True),
            "is_default": provider.get("is_default", False),
            "key_hint": provider.get("key_hint") or "",
            "region": provider.get("region") or "",
            "workspace_id": provider.get("workspace_id") or "",
            "models": list(provider.get("models") or []),
        }

    def _project(self, user: str, project_id: str) -> dict:
        doc = self.rt.store.get("projects", project_id)
        if doc is None or doc.get("user_id") != user or doc.get("deleted_at"):
            raise AppError("Project not found", 404)
        return doc

    def _shape(self, user: str, shape_id: str) -> dict:
        doc = self.rt.store.get("shapes", shape_id)
        if doc is None or doc.get("user_id") != user or doc.get("deleted_at"):
            raise AppError("Shape not found", 404)
        return doc

    def _variant(self, user: str, variant_id: str) -> dict:
        doc = self.rt.store.get("variants", variant_id)
        if doc is None or doc.get("user_id") != user:
            raise AppError("Variant not found", 404)
        return doc

    def _parent(self, user: str, project: dict, parent_id):
        if not parent_id:
            return None
        parent = self._shape(user, parent_id)
        if parent["project_id"] != project["id"] or parent.get("parent_id"):
            raise AppError("Parts can only contain objects one level deep")
        if not parent.get("saved"):
            raise AppError("Save the part before adding objects inside it")
        return parent

    def _require_unlocked(self, project: dict, parent) -> None:
        if parent is None and project.get("geometry_locked"):
            raise AppError("This level is already saved")
        if parent is not None and parent.get("geometry_locked"):
            raise AppError("This level is already saved")

    def _live_shapes(self, user: str, project_id: str) -> list[dict]:
        rows = [
            item for item in self.rt.store.query("shapes", project_id=project_id, user_id=user)
            if not item.get("deleted_at")
        ]
        rows.sort(key=lambda item: item["created_at"])
        return rows

    def _siblings(self, user: str, project_id: str, parent_id) -> list[dict]:
        return [
            item for item in self._live_shapes(user, project_id)
            if item.get("parent_id") == parent_id
        ]

    def _descendants(self, user: str, shape: dict) -> list[dict]:
        children = self._siblings(user, shape["project_id"], shape["id"])
        out = []
        for child in children:
            out.append(child)
            out.extend(self._descendants(user, child))
        return out

    def _next_color(self, siblings: list[dict]) -> str:
        used = {item["color"] for item in siblings}
        for color in PALETTE:
            if color not in used:
                return color
        return PALETTE[len(siblings) % len(PALETTE)]

    def _box(self, body: dict, kind: str) -> Box:
        try:
            return Box(float(body["x"]), float(body["y"]), float(body["w"]), float(body["h"]), kind)
        except (KeyError, TypeError, ValueError) as exc:
            raise AppError("A shape needs x, y, w, and h") from exc

    def _as_box(self, shape: dict) -> Box:
        return Box(shape["x"], shape["y"], shape["w"], shape["h"], shape["kind"])

    def _project_json(self, doc: dict) -> dict:
        return {
            "id": doc["id"],
            "name": doc["name"],
            "kind": doc["kind"],
            "created_at": doc["created_at"],
            "preview_url": f"/api/projects/{doc['id']}/preview" if doc.get("source_key") else None,
            "geometry_locked": bool(doc.get("geometry_locked")),
            "selected_variant_id": doc.get("selected_variant_id"),
        }

    def _shape_json(self, doc: dict) -> dict:
        return {
            "id": doc["id"],
            "project_id": doc["project_id"],
            "parent_id": doc.get("parent_id"),
            "kind": doc["kind"],
            "type": doc.get("type"),
            "color": doc["color"],
            "x": doc["x"], "y": doc["y"], "w": doc["w"], "h": doc["h"],
            "saved": bool(doc.get("saved")),
            "geometry_locked": bool(doc.get("geometry_locked")),
            "selected_variant_id": doc.get("selected_variant_id"),
        }

    def _blob(self, key: str) -> bytes:
        found = self.rt.blobs.get(key)
        if found is None:
            raise AppError("Image data is missing", 404)
        return found[0]

    def _crop_bytes(self, project: dict, shape: dict) -> bytes:
        source = load_png(self._blob(project["source_key"]))
        shapes = {
            item["id"]: item
            for item in self.rt.store.query("shapes", project_id=project["id"])
            if not item.get("deleted_at")
        }
        shapes[shape["id"]] = shape
        return png_bytes(crop_box(source, absolute_box(shape, shapes)))

    def _write_original(self, project: dict, shape: dict) -> None:
        data = self._crop_bytes(project, shape)
        image = load_png(data)
        rows = [
            item for item in self.rt.store.query("variants", shape_id=shape["id"])
            if item.get("role") == "original"
        ]
        if rows:
            self.rt.blobs.put(rows[0]["key"], data)
            self.rt.store.update("variants", rows[0]["id"], width=image.width, height=image.height)
            variant_id = rows[0]["id"]
        else:
            variant_id = _new_id()
            key = f"{shape['user_id']}/shapes/{shape['id']}/original.png"
            self.rt.blobs.put(key, data)
            self.rt.store.insert("variants", {
                "id": variant_id,
                "shape_id": shape["id"],
                "project_id": project["id"],
                "user_id": shape["user_id"],
                "task_id": None,
                "role": "original",
                "key": key,
                "width": image.width,
                "height": image.height,
                "provider": "",
                "model": "",
                "seed": None,
                "prompt": "",
                "created_at": _now(),
            })
        if not shape.get("selected_variant_id"):
            self.rt.store.update("shapes", shape["id"], selected_variant_id=variant_id)

    def _rewrite_originals(self, project: dict, shape: dict) -> None:
        self._write_original(project, shape)
        for child in self._descendants(shape["user_id"], shape):
            if child.get("saved"):
                self._write_original(project, child)

    def _variant_list(self, selected_id, *, shape_id: str) -> list[dict]:
        rows = self.rt.store.query("variants", shape_id=shape_id)
        return self._rows(selected_id, rows)

    def _rows(self, selected_id, rows: list[dict]) -> list[dict]:
        rows.sort(key=lambda item: (item.get("role") != "original", item["created_at"]))
        return [
            {
                "id": item["id"],
                "role": item.get("role") or "generated",
                "url": f"/api/media/{item['key']}",
                "width": item.get("width"),
                "height": item.get("height"),
                "provider": item.get("provider") or "",
                "model": item.get("model") or "",
                "seed": item.get("seed"),
                "prompt": item.get("prompt") or "",
                "created_at": item["created_at"],
                "selected": item["id"] == selected_id,
            }
            for item in rows
        ]

    def _task_json(self, task: dict) -> dict:
        project = self.rt.store.get("projects", task["project_id"])
        return {
            "id": task["id"],
            "status": task["status"],
            "progress": task.get("progress"),
            "mode": task["mode"],
            "provider_kind": task.get("provider_kind"),
            "model": task.get("model"),
            "seed": task.get("seed"),
            "prompt": task.get("prompt") or "",
            "error": task.get("error"),
            "result_url": task.get("result_url"),
            "shape_id": task.get("shape_id"),
            "project_id": task["project_id"],
            "project_name": (project or {}).get("name") or "",
            "created_at": task["created_at"],
        }

    def _prompt(self, raw, *, required: bool) -> str:
        text = (raw or "").strip()
        if not text:
            if required:
                raise AppError("Enter a prompt.")
            return ""
        if len(text) > 2000:
            raise AppError("The prompt is longer than 2000 characters.")
        if _CYRILLIC.search(text):
            raise AppError("Write the prompt in English.")
        return text

    def _int(self, raw, default: int, low: int, high: int, field: str) -> int:
        if raw is None or str(raw).strip() == "":
            return default
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise AppError(f"{field} must be an integer") from exc
        if not low <= value <= high:
            raise AppError(f"{field} must be between {low} and {high}")
        return value

    def _float(self, raw, default: float) -> float:
        if raw is None or str(raw).strip() == "":
            return default
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise AppError("CFG must be a number") from exc
        if not 1.0 <= value <= 10.0:
            raise AppError("CFG must be between 1.0 and 10.0")
        return value

    def _optional_seed(self, raw) -> int | None:
        if raw is None or str(raw).strip() in {"", "-1"}:
            return None
        return self._int(raw, 0, 0, _SEED_MAX, "seed")

    def _parts(self, project, shape, mode, mask_file, references, spec) -> list[tuple[str, bytes]]:
        parts: list[tuple[str, bytes]] = []
        if mode != "generate":
            parts.append(("source", self._crop_bytes(project, shape)))
        if mode == "mask_fill":
            if mask_file is None or not getattr(mask_file, "filename", None):
                raise AppError("Paint the area to fill.")
            try:
                mask = _read_rgb(mask_file, index=1)
            except ValidationError as exc:
                raise AppError(str(exc)) from exc
            source = load_png(parts[0][1])
            if mask.size != source.size:
                raise AppError("The mask must match the crop")
            if mask.convert("L").getextrema()[1] == 0:
                raise AppError("The mask is completely black")
            parts.append(("mask", png_bytes(mask)))
        try:
            refs = load_uploads(references or [])
        except ValidationError as exc:
            raise AppError(str(exc)) from exc
        for image in refs:
            parts.append(("reference", png_bytes(image)))
        if mode != "generate" and not spec["accepts_images"]:
            raise AppError(f"{spec['label']} does not take a photo")
        used = 1 if mode == "customize" else 2 if mode == "mask_fill" else 0
        ref_count = sum(1 for role, _data in parts if role == "reference")
        if ref_count > max(0, spec["max_images"] - used):
            raise AppError(f"{spec['label']} accepts at most {max(0, spec['max_images'] - used)} reference photos here")
        return parts

    def _frame(self, fields, mode, parts) -> tuple[int, int]:
        if mode != "generate":
            image = load_png(parts[0][1])
            return image.size
        mode_name = (fields.get("size_mode") or "preset").strip().lower()
        try:
            if mode_name == "custom":
                return parse_custom_size(fields.get("width"), fields.get("height"))
            return size_from_quality(fields.get("quality"), fields.get("aspect"))
        except ValidationError as exc:
            raise AppError(str(exc)) from exc

    def _reusable(self, user: str, key: str) -> list[dict] | None:
        rows = [
            item for item in self.rt.store.query("tasks", user_id=user, idempotency_key=key)
            if item["status"] in {"queued", "running", "pending", "done"}
        ]
        if not rows:
            return None
        newest = max(rows, key=lambda item: item["created_at"])
        group = [item for item in rows if item["batch_id"] == newest["batch_id"]]
        group.sort(key=lambda item: item["copy_index"])
        return group

    def _chosen_images(self, shapes: list[dict]) -> dict:
        chosen = {}
        for shape in shapes:
            selected = shape.get("selected_variant_id")
            if not selected:
                continue
            variant = self.rt.store.get("variants", selected)
            if variant is None or variant.get("role") == "original":
                continue
            chosen[shape["id"]] = load_png(self._blob(variant["key"]))
        return chosen

    def _render_shape(self, project, shape) -> Image.Image:
        source = load_png(self._blob(project["source_key"]))
        shapes = self._live_shapes(shape["user_id"], project["id"])
        children = [item for item in shapes if item.get("parent_id") == shape["id"]]
        if not children:
            selected = shape.get("selected_variant_id")
            variant = self.rt.store.get("variants", selected) if selected else None
            if variant and variant.get("role") != "original":
                image = load_png(self._blob(variant["key"]))
            else:
                image = crop_box(source, absolute_box(shape, {item["id"]: item for item in shapes}))
            if shape["kind"] == "circle":
                return circle_mask(image)
            return image.convert("RGBA")
        subtree = [shape, *children]
        chosen = self._chosen_images(subtree)
        rendered = render_tree(source, shapes, chosen)
        box = absolute_box(shape, {item["id"]: item for item in shapes})
        return crop_box(rendered, box)

    def _render_scratch(self, project) -> dict:
        selected = project.get("selected_variant_id")
        variant = self.rt.store.get("variants", selected) if selected else None
        if variant is None:
            rows = [
                item for item in self.rt.store.query("variants", project_id=project["id"])
                if not item.get("shape_id") and item.get("role") == "generated"
            ]
            if not rows:
                raise AppError("Nothing to render yet")
            variant = sorted(rows, key=lambda item: item["created_at"])[-1]
        return {"url": f"/api/media/{variant['key']}"}
