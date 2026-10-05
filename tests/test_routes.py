"""Маршруты не изменились и отвечают без аутентификации."""

from __future__ import annotations

import pytest

from app.imaging import RESOLUTION_PRESETS

EXPECTED_ROUTES = {
    ("/", frozenset({"GET"})),
    ("/customize", frozenset({"GET"})),
    ("/mask-fill", frozenset({"GET"})),
    ("/healthz", frozenset({"GET"})),
    ("/api/config", frozenset({"GET"})),
    ("/api/generate", frozenset({"POST"})),
    ("/api/customize", frozenset({"POST"})),
    ("/api/mask-fill", frozenset({"POST"})),
    ("/api/logs", frozenset({"GET"})),
    ("/outputs/<path:name>", frozenset({"GET"})),
    ("/static/<path:filename>", frozenset({"GET"})),
}


def test_url_map_is_unchanged(app):
    routes = {
        (rule.rule, frozenset(rule.methods - {"HEAD", "OPTIONS"}))
        for rule in app.url_map.iter_rules()
    }
    assert routes == EXPECTED_ROUTES


def test_healthz_is_anonymous(client, fake_generator):
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "ok"
    assert set(body) == {"status", "model", "memory_mode", "loaded", "load_error"}
    assert body["loaded"] is False
    assert body["load_error"] is None


def test_healthz_reports_generator_state(client, fake_generator):
    fake_generator.is_loaded = True
    fake_generator.load_error = "OutOfMemoryError: boom"
    body = client.get("/healthz").get_json()
    assert body["loaded"] is True
    assert body["load_error"] == "OutOfMemoryError: boom"


def test_healthz_does_not_load_model(client, fake_generator):
    client.get("/healthz")
    assert fake_generator.ensure_loaded_calls == 0


def test_index_serves_html(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.mimetype == "text/html"
    assert b"<form" in response.data
    assert "image 1, image 2" in response.get_data(as_text=True)
    assert 'href="/customize"' in response.get_data(as_text=True)


def test_customize_page_has_one_photo_and_no_resolution(client):
    response = client.get("/customize")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert 'name="system_prompt"' in page
    assert 'name="negative_prompt"' in page
    assert 'name="image"' in page
    assert "multiple" not in page
    assert "resolution" not in page
    assert 'name="images"' not in page


def test_api_config(client):
    response = client.get("/api/config")
    assert response.status_code == 200
    body = response.get_json()
    assert body["accepts_images"] is True
    assert [p["key"] for p in body["presets"]] == [p.key for p in RESOLUTION_PRESETS]


def test_generate_and_download(client, fake_generator, output_dir):
    fake_generator.num_images = 2
    response = client.post(
        "/api/generate",
        data={"prompt": "A red fox in snow", "resolution": "1024x1024", "steps": "12", "seed": "7"},
    )
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert (body["seed"], body["width"], body["height"]) == (7, 1024, 1024)
    assert len(body["images"]) == 2

    req = fake_generator.requests[-1]
    assert (req.prompt, req.width, req.height, req.steps, req.seed) == (
        "A red fox in snow", 1024, 1024, 12, 7,
    )

    for image in body["images"]:
        assert image["url"] == f"/outputs/{image['name']}"
        assert (output_dir / image["name"]).is_file()
        served = client.get(image["url"])
        assert served.status_code == 200
        assert served.mimetype == "image/png"
        assert served.data.startswith(b"\x89PNG")


def test_index_offers_quality_aspect_and_custom_size(client):
    page = client.get("/").get_data(as_text=True)
    assert 'name="size_mode" value="preset"' in page
    assert 'name="size_mode" value="custom"' in page
    assert 'name="quality"' in page
    assert "Среднее (~1K)" in page
    for aspect in ("1:1", "4:3", "3:4", "3:2", "2:3", "16:9", "9:16"):
        assert f'value="{aspect}"' in page
    assert 'data-high="2752×1536"' in page
    assert 'data-medium="1376×768"' in page
    assert 'name="height"' in page and 'name="width"' in page
    assert 'min="32"' in page and 'max="3000"' in page


@pytest.mark.parametrize(
    ("form", "size"),
    [
        (
            {"size_mode": "preset", "quality": "high", "aspect": "16:9"},
            (2752, 1536),
        ),
        (
            {"size_mode": "preset", "quality": "medium", "aspect": "4:3"},
            (1200, 896),
        ),
        (
            {"size_mode": "preset", "quality": "medium", "aspect": "1:1"},
            (1024, 1024),
        ),
        (
            {"size_mode": "custom", "width": "1000", "height": "750"},
            (992, 736),
        ),
        (
            {"size_mode": "custom", "width": "3000", "height": "32"},
            (2976, 32),
        ),
    ],
)
def test_generate_size_modes(client, fake_generator, form, size):
    response = client.post(
        "/api/generate",
        data={"prompt": "A red fox in snow", "seed": "1", **form},
    )
    assert response.status_code == 200, response.get_json()
    req = fake_generator.requests[-1]
    assert (req.width, req.height) == size


@pytest.mark.parametrize(
    "form",
    [
        {"size_mode": "custom", "width": "16", "height": "1024"},
        {"size_mode": "custom", "width": "3001", "height": "1024"},
        {"size_mode": "custom", "width": "1024", "height": ""},
        {"size_mode": "preset", "quality": "high", "aspect": "5:4"},
        {"size_mode": "preset", "quality": "ultra", "aspect": "1:1"},
    ],
)
def test_generate_size_rejected(client, fake_generator, form):
    response = client.post("/api/generate", data={"prompt": "A red fox in snow", **form})
    assert response.status_code == 400
    assert response.get_json()["error"]
    assert fake_generator.requests == []


@pytest.mark.parametrize(
    "form",
    [
        {"prompt": ""},
        {"prompt": "   "},
        {"prompt": "Кот в шляпе"},
        {"prompt": "A cat", "steps": "abc"},
        {"prompt": "A cat", "steps": "0"},
        {"prompt": "A cat", "steps": "9999"},
        {"prompt": "A cat", "resolution": "huge"},
        {"prompt": "A cat", "true_cfg_scale": "50"},
    ],
)
def test_generate_validation(client, fake_generator, form):
    response = client.post("/api/generate", data=form)
    assert response.status_code == 400
    assert response.get_json()["error"]
    assert fake_generator.requests == []


def _png(width: int, height: int, color=(12, 34, 56)):
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    buf.seek(0)
    return buf


def test_customize_returns_the_uploaded_size(client, fake_generator, output_dir):
    response = client.post(
        "/api/customize",
        data={
            "system_prompt": "Remove the lamp behind the person",
            "negative_prompt": "blurry",
            "steps": "8",
            "seed": "3",
            "image": (_png(1000, 750), "room.png"),
        },
    )
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert (body["width"], body["height"], body["seed"]) == (1000, 750, 3)
    assert len(body["images"]) == 1

    req = fake_generator.requests[-1]
    assert req.prompt == "Remove the lamp behind the person"
    assert req.negative_prompt == "blurry"
    assert req.steps == 8
    assert len(req.images) == 1
    assert req.width % 32 == 0 and req.height % 32 == 0
    assert (req.width, req.height) == req.images[0].size
    assert (req.width, req.height) != (1000, 750)

    from PIL import Image

    saved = Image.open(output_dir / body["images"][0]["name"])
    assert saved.size == (1000, 750)
    served = client.get(body["images"][0]["url"])
    assert served.status_code == 200
    assert served.mimetype == "image/png"


@pytest.mark.parametrize(
    "data",
    [
        {"system_prompt": ""},
        {"system_prompt": "Убери лампу"},
        {"system_prompt": "Remove the lamp", "steps": "0"},
    ],
)
def test_customize_validation(client, fake_generator, data):
    response = client.post("/api/customize", data=data)
    assert response.status_code == 400
    assert response.get_json()["error"]
    assert fake_generator.requests == []


def test_customize_rejects_a_missing_or_extra_photo(client, fake_generator):
    missing = client.post("/api/customize", data={"system_prompt": "Remove the lamp"})
    assert missing.status_code == 400
    extra = client.post(
        "/api/customize",
        data={
            "system_prompt": "Remove the lamp",
            "image": [(_png(64, 64), "a.png"), (_png(64, 64), "b.png")],
        },
    )
    assert extra.status_code == 400
    assert fake_generator.requests == []


def test_pipeline_error_is_503(client, fake_generator):
    from app.generator import PipelineError

    fake_generator.fail_with = PipelineError("CUDA out of memory")
    response = client.post("/api/generate", data={"prompt": "A cat"})
    assert response.status_code == 503
    assert "CUDA out of memory" in response.get_json()["error"]


def test_logs_returns_lines_written_during_request(client):
    import logging

    logger = logging.getLogger("app.generator")
    logger.setLevel(logging.INFO)
    logger.info("pipeline «denoise» 1/4: test")
    response = client.get("/api/logs?after=0")
    assert response.status_code == 200
    lines = response.get_json()["lines"]
    assert any("denoise" in line["message"] for line in lines)
    last = lines[-1]["id"]
    assert client.get(f"/api/logs?after={last}").get_json()["lines"] == []
    assert client.get("/api/logs?after=nope").status_code == 200


def test_missing_output_is_404(client):
    assert client.get("/outputs/nope.png").status_code == 404


def test_unknown_path_is_404(client):
    assert client.get("/no-such-page").status_code == 404


@pytest.mark.parametrize(("method", "path"), [("GET", "/api/generate"), ("POST", "/healthz")])
def test_wrong_method_is_405(client, method, path):
    assert client.open(path, method=method).status_code == 405


def test_oversized_upload_is_413_json(app, client):
    app.config["MAX_CONTENT_LENGTH"] = 16
    response = client.post("/api/generate", data={"prompt": "A cat" * 10})
    assert response.status_code == 413
    assert response.get_json()["error"]


def test_unexpected_error_is_500_json(client, fake_generator):
    fake_generator.fail_with = ValueError("boom")
    response = client.post("/api/generate", data={"prompt": "A cat"})
    assert response.status_code == 500
    assert response.get_json() == {"error": "Внутренняя ошибка: ValueError"}


@pytest.mark.parametrize(
    "url",
    [
        "/outputs/../secret.png",
        "/outputs/%2e%2e/secret.png",
        "/outputs/..%2fsecret.png",
        "/outputs/%2e%2e%2fsecret.png",
        "/outputs/../../conftest.py",
        "/outputs/sub/../../secret.png",
    ],
)
def test_output_path_traversal_is_rejected(client, secret_outside_outputs, url):
    response = client.get(url)
    assert response.status_code in (400, 404)
    assert not response.data.startswith(b"\x89PNG")


_USER_V4 = "3f1c2b7a-6d4e-4a11-9c2b-8e7f6a5b4c3d"
_USER_V1 = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"
_STORAGE_ID = "20260927T115012Z-3b47ec64"


class _StorageResponse:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _StorageClient:
    """Подмена urllib.request.urlopen внутри app.webstorage. Сеть не открывается."""

    def __init__(self, *, error: BaseException | None = None, status: int = 201, body: bytes | None = None):
        self.error = error
        self.status = status
        self.body = body if body is not None else (
            b'{"id":"' + _STORAGE_ID.encode() + b'","created_at":"2026-09-27T11:50:12Z"}'
        )
        self.calls: list[dict] = []

    def __call__(self, req, timeout=None):
        self.calls.append({"req": req, "timeout": timeout})
        if self.error is not None:
            raise self.error
        return _StorageResponse(self.status, self.body)


def _install_storage(monkeypatch, **kwargs) -> _StorageClient:
    client = _StorageClient(**kwargs)
    monkeypatch.setattr("app.webstorage.urlopen", client)
    return client


def _parts(req) -> list[tuple[str, str | None, bytes]]:
    boundary = req.headers["Content-Type"].split("boundary=", 1)[1].strip().encode()
    parts = []
    for chunk in req.data.split(b"--" + boundary):
        if chunk.strip() in (b"", b"--"):
            continue
        if chunk.startswith(b"\r\n"):
            chunk = chunk[2:]
        if chunk.endswith(b"\r\n"):
            chunk = chunk[:-2]
        header_blob, _, body = chunk.partition(b"\r\n\r\n")
        headers = header_blob.decode()
        parts.append((_disposition(headers, "name"), _disposition(headers, "filename"), body))
    return parts


def _disposition(headers: str, key: str) -> str | None:
    import re

    match = re.search(rf'{key}="([^"]*)"', headers)
    return match.group(1) if match else None


@pytest.mark.parametrize(
    ("headers", "extra_form"),
    [
        ({}, {}),
        ({"X-Auth-User-Id": "not-a-uuid"}, {}),
        ({"X-Auth-User-Id": "550e8400e29b41d4a716446655440000"}, {}),
        ({"X-Auth-User-Id": ""}, {}),
        ({}, {"user_id": _USER_V4, "X-Auth-User-Id": _USER_V4}),
    ],
)
def test_generate_without_uuid_is_not_saved(client, monkeypatch, output_dir, headers, extra_form):
    storage = _install_storage(monkeypatch)
    response = client.post(
        "/api/generate",
        data={
            "prompt": "A red fox in snow",
            "resolution": "1024x1024",
            "seed": "7",
            **extra_form,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["saved"] is False
    assert body["storage_error"] == "not saved: no user id"
    assert "storage_id" not in body
    assert storage.calls == []
    assert (output_dir / body["images"][0]["name"]).is_file()
    served = client.get(body["images"][0]["url"])
    assert served.status_code == 200
    assert served.data.startswith(b"\x89PNG")


@pytest.mark.parametrize("user_id", [_USER_V4, _USER_V1, f"  {_USER_V4.upper()}  ", "00000000-0000-0000-0000-000000000000"])
def test_uuid_user_id_is_saved(client, monkeypatch, user_id):
    storage = _install_storage(monkeypatch)
    response = client.post(
        "/api/generate",
        data={"prompt": "A red fox in snow", "seed": "7", "resolution": "1024x1024"},
        headers={"X-Auth-User-Id": user_id, "X-Auth-Email": "   "},
    )
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["saved"] is True
    assert body["storage_id"] == _STORAGE_ID
    assert "storage_error" not in body
    outbound = storage.calls[0]["req"]
    assert outbound.headers["X-Auth-User-Id"] == user_id.strip()
    assert "X-Auth-Email" not in outbound.headers


def test_generate_posts_the_bundle(client, fake_generator, monkeypatch, output_dir):
    from dataclasses import replace

    from app.config import settings
    import app.routes as routes_mod

    monkeypatch.setattr(
        routes_mod,
        "settings",
        replace(routes_mod.settings, default_negative_prompt="ugly, deformed"),
    )

    original = fake_generator.generate

    def generate(request):
        result = original(request)
        result.duration = 1.26
        return result

    monkeypatch.setattr(fake_generator, "generate", generate)
    storage = _install_storage(monkeypatch)
    response = client.post(
        "/api/generate",
        data={
            "prompt": "  A red fox in snow  ",
            "resolution": "1024x1024",
            "steps": "12",
            "true_cfg_scale": "4.5",
            "seed": "-1",
            "images": [
                (_png(8, 8, (255, 0, 0)), "red.png"),
                (_png(8, 8, (0, 0, 255)), "blue.png"),
            ],
        },
        headers={
            "X-Auth-User-Id": f"  {_USER_V4} ",
            "X-Auth-Email": " person@example.com ",
            "X-Auth-Role": "ADMIN",
            "Authorization": "Bearer secret-token",
            "Cookie": "session=forged",
        },
    )
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["saved"] is True
    assert body["storage_id"] == _STORAGE_ID
    assert "storage_error" not in body
    assert body["seed"] != -1
    assert body["duration"] == 1.26
    assert storage.calls[0]["timeout"] == settings.webstorage_timeout == 60
    outbound = storage.calls[0]["req"]
    assert outbound.full_url == f"{settings.webstorage_url}/api/generated"
    assert outbound.get_method() == "POST"
    assert set(outbound.headers) == {"Content-Type", "X-Auth-User-Id", "X-Auth-Email"}
    assert outbound.headers["X-Auth-User-Id"] == _USER_V4
    assert outbound.headers["X-Auth-Email"] == "person@example.com"
    lowered = {name.lower() for name in outbound.headers}
    assert "cookie" not in lowered
    assert "authorization" not in lowered
    assert "x-auth-role" not in lowered

    parts = _parts(outbound)
    assert [name for name, _filename, _body in parts] == [
        "prompt",
        "negative_prompt",
        "seed",
        "steps",
        "true_cfg_scale",
        "width",
        "height",
        "duration",
        "images",
        "images",
        "result",
    ]
    fields = {name: payload.decode() for name, filename, payload in parts if filename is None}
    assert fields["prompt"] == "A red fox in snow"
    assert fields["negative_prompt"] == ""
    assert fake_generator.requests[-1].negative_prompt == "ugly, deformed"
    assert fields["seed"] == str(body["seed"])
    assert fields["steps"] == "12"
    assert fields["true_cfg_scale"] == "4.5"
    assert fields["width"] == "1024"
    assert fields["height"] == "1024"
    assert fields["duration"] == "1.3"

    from io import BytesIO

    from PIL import Image

    uploaded = [payload for name, _filename, payload in parts if name == "images"]
    references = fake_generator.requests[-1].images
    assert len(uploaded) == len(references) == 2
    for payload, image in zip(uploaded, references, strict=True):
        assert Image.open(BytesIO(payload)).tobytes() == image.tobytes()
    result_name, result_payload = next(
        (filename, payload) for name, filename, payload in parts if name == "result"
    )
    assert result_name == body["images"][0]["name"]
    assert result_payload == (output_dir / result_name).read_bytes()
    assert result_payload.startswith(b"\x89PNG")


@pytest.mark.parametrize(
    ("status", "payload", "expected"),
    [
        (403, {"error": "forbidden"}, "forbidden"),
        (413, {"error": "quota exceeded"}, "quota exceeded"),
        (503, {"error": "storage down"}, "storage down"),
        (503, b"busy", "503"),
    ],
)
def test_storage_http_error_still_returns_the_image(client, monkeypatch, output_dir, status, payload, expected):
    import io
    import json
    from email.message import Message
    from urllib.error import HTTPError

    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    error = HTTPError(
        "http://app:8000/api/generated",
        status,
        "error",
        Message(),
        io.BytesIO(raw),
    )
    storage = _install_storage(monkeypatch, error=error)
    response = client.post(
        "/api/generate",
        data={"prompt": "A red fox in snow", "seed": "7", "resolution": "1024x1024"},
        headers={"X-Auth-User-Id": _USER_V4},
    )
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["saved"] is False
    assert body["storage_error"] == expected
    assert "storage_id" not in body
    assert len(storage.calls) == 1
    assert (output_dir / body["images"][0]["name"]).is_file()
    served = client.get(body["images"][0]["url"])
    assert served.status_code == 200
    assert served.data.startswith(b"\x89PNG")


def _storage_errors():
    from urllib.error import URLError

    return [
        (URLError(OSError("refused")), "connection error"),
        (TimeoutError(), "timeout"),
        (URLError(TimeoutError()), "timeout"),
    ]


@pytest.mark.parametrize(("error", "expected"), _storage_errors())
def test_storage_transport_error_still_returns_the_image(
    client, monkeypatch, caplog, output_dir, error, expected
):
    import logging

    caplog.set_level(logging.WARNING, logger="app.webstorage")
    storage = _install_storage(monkeypatch, error=error)
    response = client.post(
        "/api/generate",
        data={"prompt": "A red fox in snow", "seed": "7", "resolution": "1024x1024"},
        headers={
            "X-Auth-User-Id": _USER_V4,
            "X-Auth-Email": "person@example.com",
            "Authorization": "Bearer secret-token",
        },
    )
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["saved"] is False
    assert body["storage_error"] == expected
    assert "storage_id" not in body
    assert len(storage.calls) == 1
    assert (output_dir / body["images"][0]["name"]).is_file()
    assert "person@example.com" not in caplog.text
    assert "secret-token" not in caplog.text
    assert _USER_V4 not in caplog.text
    assert expected in caplog.text


def test_generate_validation_does_not_call_storage(client, fake_generator, monkeypatch):
    storage = _install_storage(monkeypatch)
    response = client.post("/api/generate", data={"prompt": ""})
    assert response.status_code == 400
    assert storage.calls == []
    assert fake_generator.requests == []


def test_pipeline_error_does_not_call_storage(client, fake_generator, monkeypatch):
    from app.generator import PipelineError

    storage = _install_storage(monkeypatch)
    fake_generator.fail_with = PipelineError("CUDA out of memory")
    response = client.post("/api/generate", data={"prompt": "A cat"}, headers={"X-Auth-User-Id": _USER_V4})
    assert response.status_code == 503
    assert storage.calls == []


def test_oversized_upload_does_not_call_storage(app, client, monkeypatch):
    storage = _install_storage(monkeypatch)
    app.config["MAX_CONTENT_LENGTH"] = 16
    response = client.post(
        "/api/generate",
        data={"prompt": "A cat" * 10},
        headers={"X-Auth-User-Id": _USER_V4},
    )
    assert response.status_code == 413
    assert storage.calls == []
