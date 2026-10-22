"""SSE-контракт контейнера инференса: POST /v1/generate, GET /healthz.

Пайплайн подменён: тест проверяет HTTP-слой (порядок событий, keepalive,
ошибки, кодирование картинок), а сам пайплайн остаётся непроверенным здесь —
он требует GPU и весов.
"""

from __future__ import annotations

import base64
import io
import json
import logging
from typing import Any

import pytest
from PIL import Image

from inference.app import create_app
from inference.pipeline.types import GenerationResult, PipelineError


def _png(size: tuple[int, int] = (6, 6)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (5, 6, 7)).save(buffer, format="PNG")
    return buffer.getvalue()


def _events(text: str) -> list[tuple[str, dict[str, Any]]]:
    parsed: list[tuple[str, dict[str, Any]]] = []
    for block in text.split("\n\n"):
        if not block.strip() or block.lstrip().startswith(":"):
            continue
        name = ""
        data: list[str] = []
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[len("event: "):]
            elif line.startswith("data: "):
                data.append(line[len("data: "):])
        if name:
            parsed.append((name, json.loads("\n".join(data)) if data else {}))
    return parsed


class FakePipeline:
    def __init__(self, *, fail: Exception | None = None, images: int = 1) -> None:
        self.is_loaded = True
        self.load_error: str | None = None
        self.accepts_images = True
        self.requests: list[Any] = []
        self._fail = fail
        self._images = images

    def ensure_loaded(self) -> object:
        return self

    def generate(self, request):
        self.requests.append(request)
        log = logging.getLogger("inference.pipeline.generator")
        log.info("pipeline «denoise» 1/1: timestep=1000, шаг 0.40 с")
        if self._fail is not None:
            raise self._fail
        log.warning("pipeline: последний шаг")
        return GenerationResult(
            images=[Image.new("RGB", (6, 6), (5, 6, 7)) for _ in range(self._images)],
            seed=request.seed,
            duration=0.42,
        )


@pytest.fixture
def pipeline():
    return FakePipeline()


@pytest.fixture
def client(pipeline):
    app = create_app(generator_factory=lambda: pipeline)
    app.config["TESTING"] = True
    return app.test_client()


def _form(**overrides: str) -> dict[str, str]:
    form = {"prompt": "a cat", "width": "64", "height": "64", "steps": "2", "true_cfg_scale": "1.0", "seed": "11"}
    form.update(overrides)
    return form


def test_healthz_reports_pipeline_state(client):
    body = client.get("/healthz").get_json()
    assert body["status"] == "ok"
    assert body["loaded"] is True
    assert body["accepts_images"] is True
    assert body["load_error"] is None
    assert body["memory_mode"]


def test_generate_streams_start_logs_then_done(client, pipeline):
    response = client.post("/v1/generate", data=_form())
    assert response.status_code == 200
    assert response.mimetype == "text/event-stream"

    events = _events(response.get_data(as_text=True))
    names = [name for name, _ in events]
    assert names[0] == "start"
    assert names[-1] == "done"
    assert "log" in names

    logs = [payload["message"] for name, payload in events if name == "log"]
    assert "инференс: 64x64, steps=2, cfg=1.0, seed=11, входных фото=0" in logs
    assert "pipeline «denoise» 1/1: timestep=1000, шаг 0.40 с" in logs
    levels = {payload["message"]: payload["level"] for name, payload in events if name == "log"}
    assert levels["pipeline: последний шаг"] == "WARNING"

    done = events[-1][1]
    assert done["seed"] == 11
    assert done["duration"] == pytest.approx(0.42)
    assert done["count"] == 1
    image = Image.open(io.BytesIO(base64.b64decode(done["images"][0])))
    assert image.size == (6, 6)

    assert len(pipeline.requests) == 1
    request = pipeline.requests[0]
    assert (request.width, request.height, request.steps, request.true_cfg_scale) == (64, 64, 2, 1.0)


def test_generate_passes_uploaded_images_to_the_pipeline(client, pipeline, tmp_path):
    path = tmp_path / "photo.png"
    path.write_bytes(_png((9, 12)))
    with path.open("rb") as handle:
        response = client.post(
            "/v1/generate",
            data={**_form(), "images": (handle, "photo.png")},
            content_type="multipart/form-data",
        )
    assert response.status_code == 200
    # Поток ленивый: тело надо вычитать, иначе генератор не запустится вовсе.
    assert "event: done" in response.get_data(as_text=True)
    assert [image.size for image in pipeline.requests[0].images] == [(9, 12)]


def test_pipeline_failure_is_an_error_event_not_a_broken_stream():
    app = create_app(generator_factory=lambda: FakePipeline(fail=PipelineError("CUDA out of memory")))
    response = app.test_client().post("/v1/generate", data=_form())
    assert response.status_code == 200

    events = _events(response.get_data(as_text=True))
    assert events[-1][0] == "error"
    assert events[-1][1] == {"kind": "pipeline", "error": "CUDA out of memory"}
    assert ("done",) != (events[-1][0],)


def test_unexpected_failure_is_an_internal_error_event():
    app = create_app(generator_factory=lambda: FakePipeline(fail=RuntimeError("диск отвалился")))
    events = _events(app.test_client().post("/v1/generate", data=_form()).get_data(as_text=True))
    assert events[-1][0] == "error"
    assert events[-1][1]["kind"] == "internal"
    assert "RuntimeError" in events[-1][1]["error"]


@pytest.mark.parametrize("form", [{"prompt": ""}, {"width": "0"}, {"steps": "0"}, {"seed": "abc"}])
def test_bad_form_is_400_without_starting_a_job(client, pipeline, form):
    response = client.post("/v1/generate", data=_form(**form))
    assert response.status_code == 400
    assert response.get_json()["error"]
    assert pipeline.requests == []
