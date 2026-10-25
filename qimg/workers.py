"""Intake routing and the generation worker. Both are idempotent on the task id."""

from __future__ import annotations

import io
import logging
import time
import uuid
from datetime import datetime, timezone

from PIL import Image

from app.config import settings

from . import providers
from .errors import AppError
from .pictures import circle_mask, png_bytes
from .tasks import discard_task, owner_gone

log = logging.getLogger(__name__)


def handle_intake(runtime, body: dict) -> None:
    task = runtime.store.get("tasks", body.get("task_id"))
    if task is None or task.get("status") != "queued":
        return
    artifacts = sorted(task.get("artifacts") or [], key=lambda item: item["position"])
    try:
        runtime.queue.publish("gen", {
            "task_id": task["id"],
            "type": task.get("type") or "gen",
            "artifacts": artifacts,
        })
    except Exception:
        # The delivery is nacked without requeue by serve(), so a task left "queued"
        # here would never come back: record the failure where the browser can see it.
        log.exception("task %s could not reach the gen queue", task["id"])
        _update_task(runtime, task["id"], status="failed", error="Queue rejected the task")


def handle_gen(runtime, body: dict) -> None:
    task_id = body.get("task_id")
    task = runtime.store.get("tasks", task_id) if task_id else None
    if task is None or task.get("status") != "queued":
        return
    if not runtime.store.claim("tasks", task_id, "queued", status="running"):
        return
    task = runtime.store.get("tasks", task_id)
    if task is None or _release(runtime, task):
        return
    try:
        loaded = _load(runtime, task)
        provider = _provider(runtime, task)
        dispatch = runtime.dispatch or providers.run

        def on_progress(payload: dict) -> None:
            if task.get("provider_kind") != "qwen":
                return
            _update_task(
                runtime,
                task_id,
                progress={
                    "stage": int(payload.get("stage") or 0),
                    "total": int(payload.get("total") or 10),
                    "name": str(payload.get("name") or ""),
                },
            )

        results = dispatch(
            task,
            loaded,
            on_progress if task.get("provider_kind") == "qwen" else None,
            provider,
            settings.settings_secret,
        )
        current = runtime.store.get("tasks", task_id)
        if current is None or _release(runtime, current):
            return
        _save(runtime, current, loaded, results)
    except Exception as exc:
        message = exc.message if isinstance(exc, AppError) else str(exc)
        log.exception("task %s failed", task_id)
        _update_task(runtime, task_id, status="failed", error=message[:500])


def drain(runtime, queues=("intake", "gen"), limit: int = 100) -> int:
    handled = 0
    handlers = {"intake": handle_intake, "gen": handle_gen}
    while handled < limit:
        progress = False
        for name in queues:
            delivery = runtime.queue.pull(name)
            if delivery is None:
                continue
            progress = True
            handled += 1
            try:
                handlers[name](runtime, delivery.body)
                delivery.ack()
            except Exception:
                delivery.nack()
                raise
        if not progress:
            break
    return handled


def fail_orphans(runtime) -> int:
    """Fail tasks a previous orchestrator left in ``running``.

    A restart drops the AMQP connection; RabbitMQ redelivers the message and the new
    worker sees status ``running`` and skips it, so the task would sit in ``running``
    forever with no error. One orchestrator serves the gen queue (ADR 0013), therefore
    anything still running at startup belongs to a dead process.
    """
    rows = runtime.store.query("tasks", status="running")
    for task in rows:
        _update_task(
            runtime, task["id"], status="failed",
            error="Orchestrator restarted while the task was running",
        )
    if rows:
        log.warning("failed %s task(s) left running by the previous process", len(rows))
    return len(rows)


def serve(queue_name: str, handler, recover=None) -> None:
    from .runtime import build_runtime

    runtime = build_runtime()
    if recover is not None:
        try:
            recover(runtime)
        except Exception:
            log.exception("startup recovery failed")
    log.info("listening on %s", queue_name)
    while True:
        try:
            delivery = runtime.queue.pull(queue_name)
        except Exception:
            log.exception("queue read failed")
            time.sleep(2)
            continue
        if delivery is None:
            time.sleep(0.5)
            continue
        try:
            handler(runtime, delivery.body)
            delivery.ack()
        except Exception:
            log.exception("message failed")
            try:
                delivery.nack()
            except Exception:
                log.exception("nack failed")


def _load(runtime, task) -> list[tuple[str, Image.Image]]:
    ordered = sorted(task.get("artifacts") or [], key=lambda item: item["position"])
    loaded = []
    for artifact in ordered:
        found = runtime.blobs.get(artifact["key"])
        if found is None:
            raise AppError(f"Missing artifact {artifact['role']}")
        image = Image.open(io.BytesIO(found[0]))
        image.load()
        loaded.append((artifact["role"], image))
    return loaded


def _provider(runtime, task) -> dict:
    doc = runtime.store.get("settings", task["user_id"])
    if doc is None:
        raise AppError("Provider settings are missing")
    for item in doc["providers"]:
        if item["id"] == task["provider_id"]:
            return item
    raise AppError("Provider settings are missing")


def _save(runtime, task, loaded, results) -> None:
    if not results:
        raise AppError("The provider returned no image")
    source = next((image for role, image in loaded if role == "source"), None)
    target = source.size if source is not None else (int(task["width"]), int(task["height"]))
    image = results[0]
    if image.size != target:
        image = image.resize(target, Image.LANCZOS)
    shape = runtime.store.get("shapes", task["shape_id"]) if task.get("shape_id") else None
    if shape and shape.get("kind") == "circle":
        image = circle_mask(image)
    if _release(runtime, task):
        return
    key = f"{task['user_id']}/tasks/{task['id']}.png"
    runtime.blobs.put(key, png_bytes(image))
    if _release(runtime, task, key):
        return
    variant_id = str(uuid.uuid4())
    runtime.store.insert("variants", {
        "id": variant_id,
        "shape_id": task.get("shape_id"),
        "project_id": task["project_id"],
        "user_id": task["user_id"],
        "task_id": task["id"],
        "role": "generated",
        "key": key,
        "width": image.width,
        "height": image.height,
        "provider": task.get("provider_kind") or "",
        "model": task.get("model") or "",
        "seed": task.get("seed"),
        "prompt": task.get("prompt") or "",
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    progress = {"stage": 10, "total": 10, "name": "done"} if task.get("provider_kind") == "qwen" else None
    if runtime.store.get("tasks", task["id"]) is None:
        runtime.store.delete("variants", variant_id)
        runtime.blobs.delete(key)
        return
    runtime.store.update("tasks", task["id"], status="done", result_url=f"/api/media/{key}", progress=progress)


def _release(runtime, task, *extra_keys) -> bool:
    """Discard the task when its project or shape is gone.

    Returns True when the caller must stop. A row that is already gone was
    discarded elsewhere: only ``extra_keys`` (the blob just written) are removed.
    """
    current = runtime.store.get("tasks", task["id"])
    if current is None:
        for key in extra_keys:
            if key:
                runtime.blobs.delete(key)
        return True
    if not owner_gone(runtime, current):
        return False
    discard_task(runtime, current, extra_keys=extra_keys)
    return True


def _update_task(runtime, task_id: str, **fields) -> None:
    if runtime.store.get("tasks", task_id) is None:
        return
    runtime.store.update("tasks", task_id, **fields)
