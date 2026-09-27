"""Общие фикстуры. app.generator (torch/diffusers) подменяется до импорта пакета app."""

from __future__ import annotations

import os
import sys
import tempfile
import types
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from flask.testing import FlaskClient
from PIL import Image

_TMP_ROOT = Path(tempfile.mkdtemp(prefix="qwen-image-tests-"))
OUTPUT_DIR = _TMP_ROOT / "outputs"
os.environ["OUTPUT_DIR"] = str(OUTPUT_DIR)
os.environ["PRELOAD_MODEL"] = "false"


@dataclass(slots=True)
class GenerationRequest:
    prompt: str
    negative_prompt: str
    width: int
    height: int
    steps: int
    true_cfg_scale: float
    seed: int
    images: list[Image.Image]


@dataclass(slots=True)
class GenerationResult:
    images: list[Image.Image]
    seed: int
    duration: float


class PipelineError(RuntimeError):
    pass


@dataclass
class FakeGenerator:
    is_loaded: bool = False
    load_error: str | None = None
    accepts_images: bool = True
    num_images: int = 1
    fail_with: Exception | None = None
    requests: list[GenerationRequest] = field(default_factory=list)
    ensure_loaded_calls: int = 0

    def ensure_loaded(self) -> object:
        self.ensure_loaded_calls += 1
        self.is_loaded = True
        return object()

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        if self.fail_with is not None:
            raise self.fail_with
        images = [Image.new("RGB", (8, 8), (i * 40 % 256, 0, 0)) for i in range(self.num_images)]
        return GenerationResult(images=images, seed=request.seed, duration=0.5)

    def reset(self) -> None:
        self.__init__()


_fake = FakeGenerator()
_stub = types.ModuleType("app.generator")
_stub.GenerationRequest = GenerationRequest
_stub.GenerationResult = GenerationResult
_stub.PipelineError = PipelineError
_stub.QwenImageGenerator = FakeGenerator
_stub.generator = _fake
sys.modules["app.generator"] = _stub

import app as app_pkg  # noqa: E402


class RecordingClient(FlaskClient):
    """Запоминает все ответы, чтобы в teardown проверить: ни одного 401/403/редиректа на логин."""

    responses: list = []

    def open(self, *args, **kwargs):
        response = super().open(*args, **kwargs)
        RecordingClient.responses.append(response)
        return response


@pytest.fixture
def fake_generator() -> FakeGenerator:
    _fake.reset()
    yield _fake
    _fake.reset()


@pytest.fixture
def app(fake_generator):
    flask_app = app_pkg.create_app()
    flask_app.config["TESTING"] = True
    flask_app.test_client_class = RecordingClient
    RecordingClient.responses = []
    yield flask_app
    for response in RecordingClient.responses:
        assert response.status_code not in (401, 403), response.request.path
        location = response.headers.get("Location", "")
        assert not (300 <= response.status_code < 400 and "login" in location.lower()), location


@pytest.fixture
def client(app) -> FlaskClient:
    return app.test_client()


@pytest.fixture
def output_dir() -> Path:
    return OUTPUT_DIR


@pytest.fixture
def secret_outside_outputs() -> Path:
    """Файл рядом с OUTPUT_DIR: path traversal не должен его отдать."""
    path = _TMP_ROOT / "secret.png"
    Image.new("RGB", (4, 4)).save(path, format="PNG")
    return path
