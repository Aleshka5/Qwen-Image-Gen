"""Маршруты не изменились и отвечают без аутентификации."""

from __future__ import annotations

import pytest

from app.imaging import RESOLUTION_PRESETS

EXPECTED_ROUTES = {
    ("/", frozenset({"GET"})),
    ("/customize", frozenset({"GET"})),
    ("/healthz", frozenset({"GET"})),
    ("/api/config", frozenset({"GET"})),
    ("/api/generate", frozenset({"POST"})),
    ("/api/customize", frozenset({"POST"})),
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


def _png(width: int, height: int):
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (width, height), (12, 34, 56)).save(buf, format="PNG")
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
