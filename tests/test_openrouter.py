"""OpenRouter as a provider: catalog entry, sealed key, and the chat-completions request.

The vendor is reached over HTTPS inside the orchestrator, so the transport is faked here:
no key, no network, no GPU.
"""

from __future__ import annotations

import base64
import io
import json

import pytest
from PIL import Image

from qimg import providers
from qimg.catalog import model_spec
from qimg.crypto import seal
from qimg.errors import AppError
from qimg.workers import drain

USER = "33333333-3333-4333-8333-333333333333"
AUTH = {"X-Auth-User-Id": USER, "X-Auth-Email": "ada@example.com", "X-Auth-Name": "Ada Lovelace"}
KEY = "sk-or-v1-test-AB12"
MODEL = "google/gemini-3.1-flash-image"


def _json(response):
    body = response.get_json()
    assert body is not None, response.data
    return body


def _data_url(size=(16, 16), color=(0, 128, 0)) -> str:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _reply(payload: dict) -> dict:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Here it is.",
                    "images": [{"type": "image_url", "image_url": {"url": _data_url()}}],
                }
            }
        ]
    }


class _Response:
    def __init__(self, payload: dict) -> None:
        self._raw = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._raw


def _capture(monkeypatch, payload: dict) -> dict:
    seen: dict = {}

    def fake_open(request, timeout=None):
        seen["url"] = request.full_url
        seen["headers"] = {name.lower(): value for name, value in request.header_items()}
        seen["body"] = json.loads(request.data.decode("utf-8"))
        seen["timeout"] = timeout
        return _Response(payload)

    monkeypatch.setattr(providers, "_OPEN", fake_open)
    return seen


def _add(client, models=(MODEL,)) -> dict:
    created = client.post("/api/settings/providers", json={
        "kind": "openrouter",
        "label": "OpenRouter",
        "api_key": KEY,
        "models": list(models),
    }, headers=AUTH)
    assert created.status_code == 200, created.data
    return _json(created)


def _task(mode="generate", width=1024, height=576) -> dict:
    return {
        "model": MODEL,
        "mode": mode,
        "prompt": "A lighthouse at dusk",
        "width": width,
        "height": height,
        "seed": 7,
    }


def test_catalog_offers_openrouter_with_an_api_key_only(client):
    entry = next(
        item for item in _json(client.get("/api/catalog"))["providers"]
        if item["kind"] == "openrouter"
    )
    assert entry["fields"] == ["api_key"]
    ids = [model["id"] for model in entry["models"]]
    assert MODEL in ids
    assert "openai/gpt-5-image" in ids
    assert all(model["accepts_images"] for model in entry["models"])


def test_the_key_is_sealed_and_unknown_models_are_dropped(client):
    created = _add(client, models=(MODEL, "someone/not-in-the-catalog"))
    encoded = json.dumps(created)
    assert KEY not in encoded
    assert "secret_enc" not in encoded
    assert created["key_hint"].endswith("AB12")
    assert created["models"] == [MODEL]
    listed = json.dumps(_json(client.get("/api/settings", headers=AUTH)))
    assert KEY not in listed


def test_a_provider_without_a_key_is_refused(client):
    refused = client.post("/api/settings/providers", json={"kind": "openrouter"}, headers=AUTH)
    assert refused.status_code == 400
    assert "OpenRouter API key" in _json(refused)["error"]


def test_a_generation_posts_to_chat_completions(client, app, monkeypatch):
    provider = _add(client)
    project = _json(client.post("/api/projects", data={"kind": "scratch"}, headers=AUTH))["id"]
    seen = _capture(monkeypatch, _reply({}))

    queued = client.post("/api/generations", data={
        "project_id": project,
        "mode": "generate",
        "provider_id": provider["id"],
        "model": MODEL,
        "prompt": "A lighthouse at dusk",
        "size_mode": "preset",
        "quality": "medium",
        "aspect": "16:9",
        "copies": "1",
        "seed": "7",
    }, headers=AUTH)
    assert queued.status_code == 200, queued.data
    task_id = _json(queued)["tasks"][0]["id"]
    assert drain(app.extensions["qimg"]) >= 1

    task = app.extensions["qimg"].store.get("tasks", task_id)
    assert task["status"] == "done", task.get("error")
    assert task["result_url"].startswith("/api/media/")

    assert seen["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert seen["headers"]["authorization"] == f"Bearer {KEY}"
    body = seen["body"]
    assert body["model"] == MODEL
    assert body["modalities"] == ["image", "text"]
    assert body["image_config"]["aspect_ratio"] == "16:9"
    content = body["messages"][0]["content"]
    assert content[0]["type"] == "text"
    assert "A lighthouse at dusk" in content[0]["text"]
    assert len(content) == 1, "a plain generate sends no picture"


def test_mask_fill_sends_the_crop_and_the_mask_as_parts(monkeypatch):
    seen = _capture(monkeypatch, _reply({}))
    images = [
        ("source", Image.new("RGB", (64, 64), (200, 10, 10))),
        ("mask", Image.new("RGB", (64, 64), (255, 255, 255))),
    ]
    task = _task(mode="mask_fill")
    spec = model_spec("openrouter", MODEL)
    assert spec is not None

    pictures = providers._openrouter(
        task, images, {"secret_enc": seal("test-settings-secret", KEY)}, spec, "test-settings-secret",
    )

    assert len(pictures) == 1
    content = seen["body"]["messages"][0]["content"]
    assert "white area of the mask" in content[0]["text"]
    assert [part["type"] for part in content[1:]] == ["image_url", "image_url"]
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_an_answer_without_pictures_carries_the_vendor_text(monkeypatch):
    _capture(monkeypatch, {
        "choices": [{"message": {"role": "assistant", "content": "I can't help with that."}}],
    })
    spec = model_spec("openrouter", MODEL)
    assert spec is not None

    with pytest.raises(AppError) as caught:
        providers._openrouter(
            _task(), [], {"secret_enc": seal("test-settings-secret", KEY)}, spec, "test-settings-secret",
        )

    assert "I can't help with that." in caught.value.message
    assert caught.value.status == 502
