"""POST /api/mask-fill: pairing, the stub generator, and the generation save rules."""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from app.config import settings
from tests.test_routes import (
    _STORAGE_ID,
    _USER_V4,
    _install_storage,
    _parts,
)


def _png(width: int, height: int, color=(12, 34, 56)):
    buf = BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    buf.seek(0)
    return buf


def _mask(width: int, height: int):
    image = Image.new("RGB", (width, height), (0, 0, 0))
    image.paste(Image.new("RGB", (width - width // 2, height), (255, 255, 255)), (width // 2, 0))
    buf = BytesIO()
    image.save(buf, format="PNG")
    buf.seek(0)
    return buf


def _form(**extra):
    data = {
        "prompt": "Fill the masked wall with brick",
        "negative_prompt": "blurry",
        "steps": "8",
        "true_cfg_scale": "4.5",
        "seed": "3",
        "image": (_png(1000, 750, (10, 20, 30)), "room.png"),
        "mask": (_mask(1000, 750), "mask.png"),
        "images": [
            (_png(12, 10, (4, 5, 6)), "ref-a.png"),
            (_png(8, 8, (7, 8, 9)), "ref-b.png"),
        ],
    }
    data.update(extra)
    return data


def _image_parts(req) -> list[bytes]:
    return [payload for name, _filename, payload in _parts(req) if name == "images"]


def test_mask_fill_page_ignores_identity_headers(client):
    headers = {
        "X-Auth-User-Id": _USER_V4,
        "X-Auth-Email": "person@example.com",
        "X-Auth-Role": "ADMIN",
        "Authorization": "Bearer secret-token",
        "Cookie": "session=forged",
    }
    plain = client.get("/mask-fill")
    marked = client.get("/mask-fill", headers=headers)
    assert plain.status_code == 200
    assert plain.mimetype == "text/html"
    assert plain.data == marked.data
    page = plain.get_data(as_text=True)
    assert 'action="/api/mask-fill"' in page
    assert 'name="prompt"' in page
    assert 'name="image"' in page
    assert 'name="images"' in page
    assert 'id="mask-view"' in page
    assert "до 8" in page


def test_mask_fill_endpoints(app):
    rules = {rule.rule: rule.endpoint for rule in app.url_map.iter_rules()}
    assert rules["/mask-fill"] == "web.mask_fill"
    assert rules["/api/mask-fill"] == "web.api_mask_fill"


def test_mask_fill_orders_the_pipeline_and_saves_the_archive(
    client, fake_generator, monkeypatch, output_dir
):
    storage = _install_storage(monkeypatch)
    response = client.post(
        "/api/mask-fill",
        data=_form(),
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
    assert (body["width"], body["height"], body["seed"]) == (1000, 750, 3)
    assert body["saved"] is True
    assert body["storage_id"] == _STORAGE_ID
    assert "storage_error" not in body
    assert body["duration"] == 0.5
    assert len(body["images"]) == 1

    req = fake_generator.requests[-1]
    assert req.prompt == "Fill the masked wall with brick"
    assert req.negative_prompt == "blurry"
    assert (req.steps, req.true_cfg_scale, req.seed) == (8, 4.5, 3)
    assert (req.width, req.height) == req.images[0].size == req.images[1].size
    assert req.images[0].size != (1000, 750)
    photo = Image.new("RGB", (1000, 750), (10, 20, 30))
    mask = Image.new("RGB", (1000, 750), (0, 0, 0))
    mask.paste(Image.new("RGB", (500, 750), (255, 255, 255)), (500, 0))
    assert req.images[0].tobytes() == photo.resize(req.images[0].size, Image.LANCZOS).tobytes()
    assert req.images[1].tobytes() == mask.resize(req.images[1].size, Image.NEAREST).tobytes()
    assert req.images[2].size == (12, 10)
    assert req.images[2].getpixel((0, 0)) == (4, 5, 6)
    assert req.images[3].size == (8, 8)
    assert req.images[3].getpixel((0, 0)) == (7, 8, 9)
    assert len(req.images) == 4

    outbound = storage.calls[0]["req"]
    assert outbound.full_url == f"{settings.webstorage_url}/api/generated"
    assert set(outbound.headers) == {"Content-Type", "X-Auth-User-Id", "X-Auth-Email"}
    assert outbound.headers["X-Auth-User-Id"] == _USER_V4
    assert outbound.headers["X-Auth-Email"] == "person@example.com"
    lowered = {name.lower() for name in outbound.headers}
    assert "cookie" not in lowered
    assert "authorization" not in lowered
    assert "x-auth-role" not in lowered

    parts = _parts(outbound)
    fields = {name: payload.decode() for name, filename, payload in parts if filename is None}
    assert fields["prompt"] == "Fill the masked wall with brick"
    assert fields["negative_prompt"] == "blurry"
    assert fields["seed"] == "3"
    assert fields["steps"] == "8"
    assert fields["true_cfg_scale"] == "4.5"
    assert fields["width"] == "1000"
    assert fields["height"] == "750"

    uploaded = [Image.open(BytesIO(payload)) for payload in _image_parts(outbound)]
    assert len(uploaded) == 4
    assert uploaded[0].size == (1000, 750)
    assert uploaded[0].getpixel((0, 0)) == (10, 20, 30)
    assert uploaded[1].size == (1000, 750)
    assert uploaded[1].getpixel((0, 0)) == (0, 0, 0)
    assert uploaded[1].getpixel((999, 0)) == (255, 255, 255)
    assert uploaded[2].size == (12, 10)
    assert uploaded[2].getpixel((0, 0)) == (4, 5, 6)
    assert uploaded[3].size == (8, 8)
    assert uploaded[3].getpixel((0, 0)) == (7, 8, 9)

    saved = Image.open(output_dir / body["images"][0]["name"])
    assert saved.size == (1000, 750)
    served = client.get(body["images"][0]["url"])
    assert served.status_code == 200
    assert served.mimetype == "image/png"
    assert served.data.startswith(b"\x89PNG")


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Auth-User-Id": "not-a-uuid"},
        {"X-Auth-User-Id": ""},
        {"X-Auth-User-Id": "550e8400e29b41d4a716446655440000"},
    ],
)
def test_mask_fill_without_uuid_skips_storage(client, fake_generator, monkeypatch, output_dir, headers):
    storage = _install_storage(monkeypatch)
    response = client.post("/api/mask-fill", data=_form(), headers=headers)
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["saved"] is False
    assert body["storage_error"] == "not saved: no user id"
    assert "storage_id" not in body
    assert storage.calls == []
    assert len(fake_generator.requests) == 1
    assert (output_dir / body["images"][0]["name"]).is_file()
    served = client.get(body["images"][0]["url"])
    assert served.status_code == 200
    assert served.data.startswith(b"\x89PNG")


def test_storage_failure_still_returns_the_png(client, monkeypatch, output_dir):
    import io
    from email.message import Message
    from urllib.error import HTTPError

    error = HTTPError(
        "http://app:8000/api/generated",
        503,
        "error",
        Message(),
        io.BytesIO(b'{"error":"storage down"}'),
    )
    storage = _install_storage(monkeypatch, error=error)
    response = client.post(
        "/api/mask-fill",
        data=_form(),
        headers={"X-Auth-User-Id": _USER_V4},
    )
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["saved"] is False
    assert body["storage_error"] == "storage down"
    assert "storage_id" not in body
    assert len(storage.calls) == 1
    assert (output_dir / body["images"][0]["name"]).is_file()
    served = client.get(body["images"][0]["url"])
    assert served.status_code == 200
    assert served.data.startswith(b"\x89PNG")


@pytest.mark.parametrize(
    "extra",
    [
        {"prompt": ""},
        {"prompt": "Залей стену"},
        {"prompt": "Fill the wall", "steps": "0"},
        {"prompt": "Fill the wall", "true_cfg_scale": "50"},
        {"image": (_png(64, 64), "a.png")},
        {"mask": (_png(64, 64), "mask.png")},
        {"image": [(_png(64, 64), "a.png"), (_png(64, 64), "b.png")]},
        {"mask": [(_mask(64, 64), "a.png"), (_mask(64, 64), "b.png")]},
        {"mask": (_mask(32, 32), "mask.png")},
        {"mask": (_png(1000, 750, (0, 0, 0)), "mask.png")},
    ],
)
def test_mask_fill_validation_does_not_call_storage(client, fake_generator, monkeypatch, extra):
    storage = _install_storage(monkeypatch)
    response = client.post(
        "/api/mask-fill",
        data=_form(**extra),
        headers={"X-Auth-User-Id": _USER_V4},
    )
    assert response.status_code == 400
    assert response.get_json()["error"]
    assert storage.calls == []
    assert fake_generator.requests == []


def test_too_many_references_does_not_call_storage(client, fake_generator, monkeypatch):
    storage = _install_storage(monkeypatch)
    extra = settings.max_images - 2 + 1
    response = client.post(
        "/api/mask-fill",
        data=_form(images=[(_png(8, 8, (index, 0, 0)), f"ref-{index}.png") for index in range(extra)]),
        headers={"X-Auth-User-Id": _USER_V4},
    )
    assert response.status_code == 400
    assert str(settings.max_images - 2) in response.get_json()["error"]
    assert storage.calls == []
    assert fake_generator.requests == []


def test_pipeline_error_does_not_call_storage(client, fake_generator, monkeypatch):
    from app.generator import PipelineError

    storage = _install_storage(monkeypatch)
    fake_generator.fail_with = PipelineError("CUDA out of memory")
    response = client.post(
        "/api/mask-fill",
        data=_form(),
        headers={"X-Auth-User-Id": _USER_V4},
    )
    assert response.status_code == 503
    assert storage.calls == []


def test_oversized_upload_does_not_call_storage(app, client, monkeypatch):
    storage = _install_storage(monkeypatch)
    app.config["MAX_CONTENT_LENGTH"] = 16
    response = client.post(
        "/api/mask-fill",
        data=_form(),
        headers={"X-Auth-User-Id": _USER_V4},
    )
    assert response.status_code == 413
    assert storage.calls == []
