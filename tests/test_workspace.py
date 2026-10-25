"""Project workspace: memory store, no GPU, no network."""

from __future__ import annotations

import io
import json
from dataclasses import replace

from PIL import Image

from app.config import settings
from qimg.runtime import Runtime, build_runtime
from qimg.service import Service
from qimg.store import MemoryBlobs, MemoryQueue, MemoryStore
from qimg.tasks import discard_task
from qimg.workers import drain

USER = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
AUTH = {"X-Auth-User-Id": USER, "X-Auth-Email": "ada@example.com", "X-Auth-Name": "Ada Lovelace"}


def _png(color=(10, 20, 200), size=(100, 80)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _file(data: bytes, name: str):
    return (io.BytesIO(data), name)


def _json(response):
    body = response.get_json()
    assert body is not None, response.data
    return body


def test_config_and_health_need_no_user(client):
    config = client.get("/api/config")
    assert config.status_code == 200
    body = _json(config)
    assert body["max_shapes"] == 20
    assert body["max_copies"] == 8
    assert body["photo_color"] == "#143d8f"
    assert body["text_color"] == "#8c1a2b"
    assert body["level1_colors"]
    health = client.get("/healthz")
    assert health.status_code == 200
    assert _json(health)["status"] == "ok"
    assert _json(health)["loaded"] is False


def test_missing_user_is_400(client):
    response = client.get("/api/projects")
    assert response.status_code == 400
    assert "user id" in _json(response)["error"].lower()
    forged = client.get("/api/projects", headers={"X-Auth-User-Id": "not-a-uuid"})
    assert forged.status_code == 400


def test_home_shell_serves_the_built_ui(client):
    page = client.get("/")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'id="root"' in html
    assert "/static/ui/assets/" in html
    assert 'id="sign-in"' in html
    named = client.get("/", headers={"X-Auth-Name": "Ada Lovelace"})
    assert "Ada Lovelace" in named.get_data(as_text=True)


def test_old_form_routes_are_gone(client):
    for path in ("/customize", "/mask-fill", "/api/generate", "/api/customize", "/api/mask-fill", "/api/logs"):
        assert client.get(path).status_code == 404


def test_project_shapes_commit_and_lock(client):
    created = client.post(
        "/api/projects",
        data={"kind": "photo", "file": _file(_png(), "room.png")},
        headers=AUTH,
    )
    assert created.status_code == 200, created.data
    project = _json(created)
    project_id = project["id"]
    assert project["kind"] == "photo"
    assert project["preview_url"]

    overlap = client.post(
        f"/api/projects/{project_id}/shapes",
        json={"kind": "rect", "x": 0.1, "y": 0.1, "w": 0.4, "h": 0.4},
        headers=AUTH,
    )
    assert overlap.status_code == 200
    first = _json(overlap)
    assert first["color"] != "#143d8f"
    assert first["saved"] is False
    second = client.post(
        f"/api/projects/{project_id}/shapes",
        json={"kind": "rect", "x": 0.2, "y": 0.2, "w": 0.4, "h": 0.4},
        headers=AUTH,
    )
    assert second.status_code == 200, second.data
    other = _json(second)
    moved_over = client.patch(
        f"/api/shapes/{other['id']}",
        json={"x": 0.15, "y": 0.15},
        headers=AUTH,
    )
    assert moved_over.status_code == 200
    refused = client.post(f"/api/projects/{project_id}/commit", json={"parent_id": None}, headers=AUTH)
    assert refused.status_code == 400
    assert "overlap" in _json(refused)["error"].lower()
    cleared = client.patch(
        f"/api/shapes/{other['id']}",
        json={"x": 0.55, "y": 0.55},
        headers=AUTH,
    )
    assert cleared.status_code == 200

    saved = client.post(f"/api/projects/{project_id}/commit", json={"parent_id": None}, headers=AUTH)
    assert saved.status_code == 200
    body = _json(saved)
    assert body["project"]["geometry_locked"] is True
    shape = next(item for item in body["shapes"] if item["id"] == first["id"])
    assert shape["saved"] is True
    assert shape["geometry_locked"] is False
    variants = _json(client.get(f"/api/shapes/{shape['id']}/variants", headers=AUTH))
    assert variants["variants"][0]["role"] == "original"
    assert variants["variants"][0]["selected"] is True

    resized = client.patch(
        f"/api/shapes/{shape['id']}",
        json={"x": shape["x"], "y": shape["y"], "w": 0.2, "h": 0.2},
        headers=AUTH,
    )
    assert resized.status_code == 400
    moved = client.patch(
        f"/api/shapes/{shape['id']}",
        json={"x": 0.5, "y": 0.5},
        headers=AUTH,
    )
    assert moved.status_code == 200
    assert _json(moved)["w"] == shape["w"]

    again = client.post(
        f"/api/projects/{project_id}/shapes",
        json={"kind": "rect", "x": 0.0, "y": 0.0, "w": 0.2, "h": 0.2},
        headers=AUTH,
    )
    assert again.status_code == 400

    child = client.post(
        f"/api/projects/{project_id}/shapes",
        json={"parent_id": shape["id"], "kind": "circle", "x": 0.1, "y": 0.1, "w": 0.4, "h": 0.4},
        headers=AUTH,
    )
    assert child.status_code == 200, child.data
    circle = _json(child)
    assert circle["color"] == "#143d8f"
    assert circle["type"] == "photo"
    text = client.post(f"/api/shapes/{circle['id']}/type", json={"type": "text"}, headers=AUTH)
    assert _json(text)["color"] == "#8c1a2b"
    back = client.post(f"/api/shapes/{circle['id']}/type", json={"type": "photo"}, headers=AUTH)
    assert _json(back)["color"] == "#143d8f"

    stranger = client.get(f"/api/projects/{project_id}", headers={"X-Auth-User-Id": OTHER})
    assert stranger.status_code == 404


def test_generation_order_reuse_progress_circle_and_render(client, app):
    created = client.post(
        "/api/projects",
        data={"kind": "photo", "file": _file(_png(size=(100, 100)), "room.png")},
        headers=AUTH,
    )
    project_id = _json(created)["id"]
    parent = _json(client.post(
        f"/api/projects/{project_id}/shapes",
        json={"kind": "rect", "x": 0.0, "y": 0.0, "w": 0.8, "h": 0.8},
        headers=AUTH,
    ))
    client.post(f"/api/projects/{project_id}/commit", json={"parent_id": None}, headers=AUTH)
    circle = _json(client.post(
        f"/api/projects/{project_id}/shapes",
        json={"parent_id": parent["id"], "kind": "circle", "x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5},
        headers=AUTH,
    ))
    mask_size = _crop_size(app, project_id, circle["id"])
    mask_bytes = _png((255, 255, 255), size=mask_size)
    ref_bytes = _png((1, 2, 3), size=(16, 16))

    def form():
        return {
            "project_id": project_id,
            "shape_id": circle["id"],
            "mode": "mask_fill",
            "provider_id": "local",
            "model": "qwen-image-2.1",
            "prompt": "A red apple on the table",
            "steps": "4",
            "true_cfg_scale": "1",
            "copies": "1",
            "seed": "7",
            "mask": _file(mask_bytes, "mask.png"),
            "images": _file(ref_bytes, "ref.png"),
        }

    first = client.post("/api/generations", data=form(), headers=AUTH)
    assert first.status_code == 200, first.data
    payload = _json(first)
    assert payload["reused"] is False
    task = payload["tasks"][0]
    assert task["status"] == "queued"
    assert task["provider_kind"] == "qwen"
    assert task["progress"]["stage"] == 0
    stored = app.extensions["qimg"].store.get("tasks", task["id"])
    assert [item["role"] for item in stored["artifacts"]] == ["source", "mask", "reference"]
    assert stored["email"] == "ada@example.com"

    second = client.post("/api/generations", data=form(), headers=AUTH)
    assert second.status_code == 200, second.data
    reused = _json(second)
    assert reused["reused"] is True
    assert reused["tasks"][0]["id"] == task["id"]

    runtime = app.extensions["qimg"]
    seen = {}

    def dispatch(job, images, on_progress, provider, secret):
        assert [role for role, _image in images] == ["source", "mask", "reference"]
        assert provider["id"] == "local"
        assert secret
        on_progress({"stage": 4, "total": 10, "name": "denoise"})
        seen["progress"] = runtime.store.get("tasks", job["id"])["progress"]
        return [Image.new("RGB", (9, 9), (220, 10, 10))]

    runtime.dispatch = dispatch
    assert drain(runtime) == 2
    done = _json(client.get(f"/api/tasks/{task['id']}", headers=AUTH))
    assert done["status"] == "done"
    assert done["progress"] == {"stage": 10, "total": 10, "name": "done"}
    assert seen["progress"]["stage"] == 4
    picture = client.get(done["result_url"], headers=AUTH)
    assert picture.status_code == 200
    image = Image.open(io.BytesIO(picture.data))
    assert image.mode == "RGBA"
    assert image.getpixel((0, 0))[3] == 0
    assert image.getpixel((image.width // 2, image.height // 2))[3] == 255

    variants = _json(client.get(f"/api/shapes/{circle['id']}/variants", headers=AUTH))
    generated = next(item for item in variants["variants"] if item["role"] == "generated")
    assert generated["selected"] is False
    client.post(
        f"/api/shapes/{circle['id']}/select",
        json={"variant_id": generated["id"]},
        headers=AUTH,
    )
    rendered = client.post(
        "/api/renders",
        json={"scope": "project", "project_id": project_id},
        headers=AUTH,
    )
    assert rendered.status_code == 200, rendered.data
    composite = Image.open(io.BytesIO(client.get(_json(rendered)["url"], headers=AUTH).data))
    assert composite.getpixel((20, 20))[0] > 150
    assert client.get(done["result_url"], headers={"X-Auth-User-Id": OTHER}).status_code == 404


def test_delete_removes_queued_work_and_a_result_saved_mid_flight(client, app):
    created = client.post(
        "/api/projects",
        data={"kind": "scratch"},
        headers=AUTH,
    )
    project_id = _json(created)["id"]
    queued = client.post("/api/generations", data={
        "project_id": project_id,
        "mode": "generate",
        "provider_id": "local",
        "model": "qwen-image-2.1",
        "prompt": "A quiet harbor at dawn",
        "size_mode": "preset",
        "quality": "medium",
        "aspect": "1:1",
        "copies": "1",
        "seed": "3",
        "images": _file(_png((9, 9, 9), size=(16, 16)), "ref.png"),
    }, headers=AUTH)
    assert queued.status_code == 200, queued.data
    task_id = _json(queued)["tasks"][0]["id"]
    runtime = app.extensions["qimg"]
    stored = runtime.store.get("tasks", task_id)
    artifact_keys = [item["key"] for item in stored["artifacts"]]
    assert artifact_keys
    assert client.delete(f"/api/projects/{project_id}", headers=AUTH).status_code == 200
    assert runtime.store.get("tasks", task_id) is None
    for key in artifact_keys:
        assert runtime.blobs.get(key) is None
    assert drain(runtime) >= 1
    assert runtime.store.get("tasks", task_id) is None
    assert runtime.blobs.get(f"{USER}/tasks/{task_id}.png") is None

    live = client.post("/api/projects", data={"kind": "scratch"}, headers=AUTH)
    live_id = _json(live)["id"]
    started = client.post("/api/generations", data={
        "project_id": live_id,
        "mode": "generate",
        "provider_id": "local",
        "model": "qwen-image-2.1",
        "prompt": "A quiet harbor at dusk",
        "size_mode": "preset",
        "quality": "medium",
        "aspect": "1:1",
        "copies": "1",
        "seed": "9",
    }, headers=AUTH)
    flying = _json(started)["tasks"][0]["id"]

    def dispatch(job, images, on_progress, provider, secret):
        return [Image.new("RGB", (32, 32), (0, 180, 0))]

    real_put = runtime.blobs.put

    def put(key, data, content_type="image/png"):
        real_put(key, data, content_type)
        if key.endswith(f"{flying}.png"):
            runtime.store.update("projects", live_id, deleted_at="2026-10-23T00:00:00Z")

    runtime.dispatch = dispatch
    runtime.blobs.put = put
    drain(runtime)
    assert runtime.store.get("tasks", flying) is None
    assert runtime.blobs.get(f"{USER}/tasks/{flying}.png") is None
    assert runtime.store.query("variants", task_id=flying) == []


def test_finished_mask_fill_task_refills_the_form(client, app):
    created = client.post(
        "/api/projects",
        data={"kind": "photo", "file": _file(_png(size=(100, 100)), "room.png")},
        headers=AUTH,
    )
    project_id = _json(created)["id"]
    parent = _json(client.post(
        f"/api/projects/{project_id}/shapes",
        json={"kind": "rect", "x": 0.0, "y": 0.0, "w": 0.8, "h": 0.8},
        headers=AUTH,
    ))
    client.post(f"/api/projects/{project_id}/commit", json={"parent_id": None}, headers=AUTH)
    circle = _json(client.post(
        f"/api/projects/{project_id}/shapes",
        json={"parent_id": parent["id"], "kind": "circle", "x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5},
        headers=AUTH,
    ))
    mask_size = _crop_size(app, project_id, circle["id"])
    mask_bytes = _png((255, 255, 255), size=mask_size)
    first_ref = _png((1, 2, 3), size=(16, 16))
    second_ref = _png((4, 5, 6), size=(16, 16))
    submitted = client.post("/api/generations", data={
        "project_id": project_id,
        "shape_id": circle["id"],
        "mode": "mask_fill",
        "provider_id": "local",
        "model": "qwen-image-2.1",
        "prompt": "A red apple on the table",
        "negative_prompt": "blurry, watermark",
        "steps": "4",
        "true_cfg_scale": "2.5",
        "copies": "1",
        "seed": "7",
        "mask": _file(mask_bytes, "mask.png"),
        "images": [
            _file(first_ref, "one.png"),
            _file(second_ref, "two.png"),
        ],
    }, headers=AUTH)
    assert submitted.status_code == 200, submitted.data
    task_id = _json(submitted)["tasks"][0]["id"]
    runtime = app.extensions["qimg"]

    def dispatch(job, images, on_progress, provider, secret):
        on_progress({"stage": 4, "total": 10, "name": "denoise"})
        return [Image.new("RGB", (9, 9), (220, 10, 10))]

    runtime.dispatch = dispatch
    assert drain(runtime) == 2
    done = _json(client.get(f"/api/tasks/{task_id}", headers=AUTH))
    assert done["status"] == "done"
    width, height = mask_size
    assert done["provider_id"] == "local"
    assert done["negative_prompt"] == "blurry, watermark"
    assert done["steps"] == 4
    assert done["true_cfg_scale"] == 2.5
    assert done["width"] == width
    assert done["height"] == height
    assert done["copies"] == 1
    assert done["seed"] == 7
    mask = client.get(done["mask_url"], headers=AUTH)
    assert mask.status_code == 200
    assert len(done["references"]) == 2
    colors = []
    for item in done["references"]:
        response = client.get(item["url"], headers=AUTH)
        assert response.status_code == 200
        colors.append(Image.open(io.BytesIO(response.data)).getpixel((0, 0))[:3])
    assert colors == [(1, 2, 3), (4, 5, 6)]
    stored = runtime.store.get("tasks", task_id)
    source_key = next(item["key"] for item in stored["artifacts"] if item["role"] == "source")
    assert f"/api/media/{source_key}" not in [item["url"] for item in done["references"]]
    feed = _json(client.get("/api/feed", headers=AUTH))
    listed = next(item for item in feed["tasks"] if item["id"] == task_id)
    assert listed == done


def test_delete_shape_removes_its_task_from_the_feed(client, app):
    created = client.post(
        "/api/projects",
        data={"kind": "photo", "file": _file(_png(size=(100, 100)), "room.png")},
        headers=AUTH,
    )
    project_id = _json(created)["id"]
    parent = _json(client.post(
        f"/api/projects/{project_id}/shapes",
        json={"kind": "rect", "x": 0.0, "y": 0.0, "w": 0.8, "h": 0.8},
        headers=AUTH,
    ))
    assert client.post(
        f"/api/projects/{project_id}/commit", json={"parent_id": None}, headers=AUTH,
    ).status_code == 200
    child = _json(client.post(
        f"/api/projects/{project_id}/shapes",
        json={"parent_id": parent["id"], "kind": "rect", "x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5},
        headers=AUTH,
    ))
    queued = client.post("/api/generations", data={
        "project_id": project_id,
        "shape_id": child["id"],
        "mode": "customize",
        "provider_id": "local",
        "model": "qwen-image-2.1",
        "prompt": "A red apple on the table",
        "steps": "4",
        "true_cfg_scale": "1",
        "copies": "1",
        "seed": "7",
    }, headers=AUTH)
    assert queued.status_code == 200, queued.data
    task_id = _json(queued)["tasks"][0]["id"]
    assert client.delete(f"/api/shapes/{child['id']}", headers=AUTH).status_code == 200
    assert app.extensions["qimg"].store.get("tasks", task_id) is None
    feed = _json(client.get("/api/feed", headers=AUTH))
    assert task_id not in {item["id"] for item in feed["tasks"]}


def test_shared_artifact_survives_until_the_last_task_is_discarded():
    store = MemoryStore()
    blobs = MemoryBlobs()
    runtime = Runtime(store, blobs, MemoryQueue())
    shared = f"{USER}/batches/batch/0.png"
    only_first = f"{USER}/batches/batch/1.png"
    blobs.put(shared, b"shared")
    blobs.put(only_first, b"only-first")
    shared_artifact = {"role": "source", "key": shared, "position": 0}
    store.insert("tasks", {
        "id": "task-1",
        "user_id": USER,
        "project_id": "project-1",
        "artifacts": [shared_artifact, {"role": "reference", "key": only_first, "position": 1}],
        "result_url": None,
    })
    store.insert("tasks", {
        "id": "task-2",
        "user_id": USER,
        "project_id": "project-1",
        "artifacts": [shared_artifact],
        "result_url": None,
    })
    discard_task(runtime, store.get("tasks", "task-1"))
    assert store.get("tasks", "task-1") is None
    assert blobs.get(shared) is not None
    assert blobs.get(only_first) is None
    discard_task(runtime, store.get("tasks", "task-2"))
    assert store.get("tasks", "task-2") is None
    assert blobs.get(shared) is None


def test_provider_secret_is_write_only(client):
    secret = "wan-live-key-ABCD"
    created = client.post("/api/settings/providers", json={
        "kind": "wan",
        "label": "Wan home",
        "api_key": secret,
        "region": "frankfurt",
        "workspace_id": "ws-1",
        "models": ["wan2.6-t2i"],
    }, headers=AUTH)
    assert created.status_code == 200, created.data
    body = _json(created)
    encoded = json.dumps(body)
    assert secret not in encoded
    assert "secret_enc" not in encoded
    assert body["key_hint"].endswith("ABCD")
    listed = json.dumps(_json(client.get("/api/settings", headers=AUTH)))
    assert secret not in listed
    local = client.delete("/api/settings/providers/local", headers=AUTH)
    assert local.status_code == 400


def test_partial_backend_config_is_refused(monkeypatch):
    monkeypatch.setattr(
        "app.config.settings",
        replace(settings, mongo_url="mongodb://mongo:27017/qimg", rabbit_url="", minio_endpoint=""),
    )
    try:
        build_runtime()
    except RuntimeError as exc:
        assert "MONGO_URL" in str(exc)
    else:
        raise AssertionError("partial backend config was accepted")


def _crop_size(app, project_id: str, shape_id: str) -> tuple[int, int]:
    from qimg.geometry import absolute_box
    from qimg.pictures import crop_box, load_png
    from qimg.service import Service

    service = Service(app.extensions["qimg"])
    project = service._project(USER, project_id)
    shape = service._shape(USER, shape_id)
    shapes = {
        item["id"]: item
        for item in app.extensions["qimg"].store.query("shapes", project_id=project_id)
    }
    image = crop_box(load_png(service._blob(project["source_key"])), absolute_box(shape, shapes))
    return image.size


class _RacingStore(MemoryStore):
    """First insert for a brand-new user loses to a concurrent writer."""

    def __init__(self) -> None:
        super().__init__()
        self._raced = False

    def insert(self, collection: str, doc: dict) -> dict:
        if collection == "settings" and not self._raced:
            self._raced = True
            super().insert(collection, doc)
            raise RuntimeError("E11000 duplicate key")
        return super().insert(collection, doc)


def test_settings_insert_race_reads_the_winner():
    runtime = Runtime(_RacingStore(), MemoryBlobs(), MemoryQueue())
    service = Service(runtime)

    assert [item["id"] for item in service.get_settings("new-user")["providers"]] == ["local"]
