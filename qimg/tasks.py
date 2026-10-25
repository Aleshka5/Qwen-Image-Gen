"""Remove a generation and the files that belong only to it."""

from __future__ import annotations

_MEDIA = "/api/media/"


def owner_gone(runtime, task: dict) -> bool:
    """The project or the part this task belongs to is missing or deleted."""
    project = runtime.store.get("projects", task.get("project_id"))
    if project is None or project.get("deleted_at"):
        return True
    shape_id = task.get("shape_id")
    if not shape_id:
        return False
    shape = runtime.store.get("shapes", shape_id)
    return shape is None or bool(shape.get("deleted_at"))


def discard_task(runtime, task: dict, *, extra_keys=()) -> None:
    """Delete the task row, its variant rows, the result file, and ``extra_keys``.

    Artifact blobs are shared by every copy in a batch. A key is removed only
    when no remaining task for this user still lists it. A task document that
    is already gone stays gone.
    """
    task_id = task.get("id")
    if not task_id:
        return
    for variant in runtime.store.query("variants", task_id=task_id):
        if variant.get("key"):
            runtime.blobs.delete(variant["key"])
        runtime.store.delete("variants", variant["id"])
    result_url = task.get("result_url") or ""
    if isinstance(result_url, str) and result_url.startswith(_MEDIA):
        runtime.blobs.delete(result_url[len(_MEDIA):])
    for key in extra_keys:
        if key:
            runtime.blobs.delete(key)
    runtime.store.delete("tasks", task_id)
    artifact_keys: list[str] = []
    seen: set[str] = set()
    for artifact in task.get("artifacts") or []:
        key = artifact.get("key")
        if key and key not in seen:
            seen.add(key)
            artifact_keys.append(key)
    if not artifact_keys:
        return
    remaining = runtime.store.query("tasks", user_id=task.get("user_id"))
    in_use = {
        item.get("key")
        for other in remaining
        for item in (other.get("artifacts") or [])
        if item.get("key")
    }
    for key in artifact_keys:
        if key not in in_use:
            runtime.blobs.delete(key)
