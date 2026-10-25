"""Контракт POST /v1/generate со стороны Flask-оркестратора: SSE на живом сервере.

Здесь нет ни мока urlopen, ни подмены клиента: настоящий сокет, настоящий
``text/event-stream``. Проверяется то, что ломается тише всего — порядок
событий, строки пайплайна в буфере страницы и картинки из ``done``.
"""

from __future__ import annotations

import base64
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from PIL import Image

from app.inference import GenerationRequest, InferenceClient, PipelineError
from app.logbuf import live_logs


def _png(size: tuple[int, int] = (8, 8), color: tuple[int, int, int] = (10, 20, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def _event(name: str, payload: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(payload)}\n\n"


class _Handler(BaseHTTPRequestHandler):
    state: SimpleNamespace

    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # тишина в выводе pytest
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - имя задаёт http.server
        if self.path != "/healthz":
            self._send(404, b"{}", "application/json")
            return
        state = self.state
        self._send(
            state.health_status,
            json.dumps(state.health).encode(),
            "application/json",
        )

    def do_POST(self) -> None:  # noqa: N802 - имя задаёт http.server
        length = int(self.headers.get("Content-Length") or 0)
        self.state.posted = (self.headers.get("Content-Type", ""), self.rfile.read(length))
        state = self.state
        if state.stream_status != 200:
            self._send(state.stream_status, json.dumps({"error": state.error}).encode(), "application/json")
            return
        body = "".join(state.events).encode()
        self._send(200, body, "text/event-stream")


@pytest.fixture
def server():
    state = SimpleNamespace(
        health={"loaded": True, "accepts_images": True, "memory_mode": "offload", "load_error": None},
        health_status=200,
        stream_status=200,
        error="boom",
        events=[],
        posted=None,
    )
    handler = type("Handler", (_Handler,), {"state": state})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield SimpleNamespace(state=state, url=f"http://127.0.0.1:{httpd.server_address[1]}")
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _request(tmp_path, images=0) -> GenerationRequest:
    photos = []
    for index in range(images):
        path = tmp_path / f"photo-{index}.png"
        path.write_bytes(_png((12, 9)))
        photos.append(Image.open(path).convert("RGB"))
    return GenerationRequest(
        prompt="a cat",
        negative_prompt="blurry",
        width=64,
        height=64,
        steps=2,
        true_cfg_scale=1.0,
        seed=7,
        images=photos,
    )


def test_generate_relays_pipeline_logs_and_decodes_images(server, tmp_path):
    server.state.events = [
        _event("start", {"model": "Qwen/Qwen-Image-2.1", "memory_mode": "offload"}),
        ": keepalive\n\n",
        _event("log", {"level": "INFO", "message": "pipeline «denoise» 1/2: timestep=1000"}),
        _event("log", {"level": "WARNING", "message": "pipeline: память на пределе"}),
        _event(
            "done",
            {
                "seed": 42,
                "duration": 1.75,
                "count": 1,
                "images": [base64.b64encode(_png()).decode("ascii")],
            },
        ),
    ]
    client = InferenceClient(base_url=server.url, timeout=10)

    result = client.generate(_request(tmp_path))

    assert result.seed == 42
    assert result.duration == pytest.approx(1.75)
    assert [image.size for image in result.images] == [(8, 8)]

    lines = live_logs().since(0)
    messages = [line["message"] for line in lines]
    assert "pipeline «denoise» 1/2: timestep=1000" in messages
    assert "pipeline: память на пределе" in messages
    levels = {line["message"]: line["level"] for line in lines}
    assert levels["pipeline: память на пределе"] == "WARNING"
    # Id растут: страница просит /api/logs?after=<последний>, без дыр в нумерации.
    ids = [line["id"] for line in lines]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)


def test_progress_events_reach_the_task_and_disable_log_scraping(server, tmp_path):
    server.state.events = [
        _event("start", {"model": "Qwen/Qwen-Image-2.1", "memory_mode": "offload"}),
        _event("progress", {"stage": 1, "total": 10, "name": "start"}),
        _event("log", {"level": "INFO", "message": "pipeline «denoise» 1/2: timestep=1000"}),
        _event("progress", {"stage": 2, "total": 10, "name": "prepare"}),
        # После первого настоящего progress строки лога стадий больше не дают.
        _event("log", {"level": "INFO", "message": "pipeline «denoise» 2/2: timestep=500"}),
        _event("progress", {"stage": 10, "total": 10, "name": "done"}),
        _event("done", {"seed": 7, "duration": 0.5, "count": 0, "images": []}),
    ]
    client = InferenceClient(base_url=server.url, timeout=10)
    seen: list[dict] = []

    client.generate(_request(tmp_path), on_progress=seen.append)

    assert [(item["stage"], item["name"]) for item in seen] == [(1, "start"), (2, "prepare"), (10, "done")]
    assert all(item["total"] == 10 for item in seen)


def test_an_old_server_without_progress_events_still_moves_the_stage(server, tmp_path):
    """Пока контейнер инференса не обновлён, стадии берутся из строк денойза."""
    server.state.events = [
        _event("start", {"model": "Qwen/Qwen-Image-2.1", "memory_mode": "offload"}),
        _event("log", {"level": "INFO", "message": "pipeline «denoise» 1/40: timestep=1000"}),
        _event("log", {"level": "INFO", "message": "pipeline «denoise» 40/40: timestep=10"}),
        _event("done", {"seed": 7, "duration": 0.5, "count": 0, "images": []}),
    ]
    client = InferenceClient(base_url=server.url, timeout=10)
    seen: list[dict] = []

    client.generate(_request(tmp_path), on_progress=seen.append)

    assert [(item["stage"], item["name"]) for item in seen] == [
        (1, "start"),
        (4, "denoise"),
        (8, "denoise"),
        (10, "done"),
    ]


def test_generate_sends_multipart_with_the_uploaded_images(server, tmp_path):
    server.state.events = [_event("done", {"seed": 7, "duration": 0.5, "count": 0, "images": []})]
    client = InferenceClient(base_url=server.url, timeout=10)

    client.generate(_request(tmp_path, images=2))

    content_type, body = server.state.posted
    assert content_type.startswith("multipart/form-data; boundary=")
    assert b'name="prompt"' in body and b"a cat" in body
    assert b'name="images"; filename="image-1.png"' in body
    assert b'name="images"; filename="image-2.png"' in body
    assert b'name="steps"' in body
    assert body.count(b"\r\n\r\n") >= 3


def test_error_event_becomes_pipeline_error(server, tmp_path):
    server.state.events = [
        _event("log", {"level": "INFO", "message": "pipeline: старт"}),
        _event("error", {"kind": "pipeline", "error": "CUDA out of memory"}),
    ]
    client = InferenceClient(base_url=server.url, timeout=10)

    with pytest.raises(PipelineError, match="CUDA out of memory"):
        client.generate(_request(tmp_path))


def test_http_error_body_becomes_pipeline_error(server, tmp_path):
    server.state.stream_status = 503
    server.state.error = "веса не загрузились"
    client = InferenceClient(base_url=server.url, timeout=10)

    with pytest.raises(PipelineError, match="веса не загрузились"):
        client.generate(_request(tmp_path))


def test_stream_without_done_is_an_error(server, tmp_path):
    server.state.events = [_event("log", {"level": "INFO", "message": "pipeline: старт"})]
    client = InferenceClient(base_url=server.url, timeout=10)

    with pytest.raises(PipelineError, match="не прислав результат"):
        client.generate(_request(tmp_path))


def test_health_snapshot_is_cached_and_reports_state(server):
    client = InferenceClient(base_url=server.url)
    assert client.is_loaded is True
    assert client.accepts_images is True
    assert client.memory_mode == "offload"
    assert client.load_error is None

    # Второй вызов в пределах TTL берёт снимок, а не дёргает сеть: состояние
    # на сервере поменялось, значение — нет.
    server.state.health = {"loaded": False, "load_error": "загрузка"}
    assert client.is_loaded is True


def test_health_of_a_dead_container_is_not_loaded():
    client = InferenceClient(base_url="http://127.0.0.1:1", timeout=None)
    assert client.is_loaded is False
    assert "инференс недоступен" in (client.load_error or "")
    assert client.memory_mode is None
