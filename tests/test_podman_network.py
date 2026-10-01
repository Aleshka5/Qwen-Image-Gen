"""Смоук-тест топологии: контейнер доступен по имени в сети и не публикует порты на хост.

Запуск: RUN_PODMAN_TESTS=1 uv run pytest -m podman. GPU не нужен — /healthz отвечает без модели.
Используются временные сеть и контейнер; реальные qwen_image_gen_network и qwen-image не трогаются.
По умолчанию тест пропускается и не требует демона Podman.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid

import pytest

IMAGE = "qwen-image-service:latest"
ALIAS = "qwen-image"
HEALTH_TIMEOUT_S = 120


def _podman(*args: str, check: bool = True, timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["podman", *args], capture_output=True, text=True, check=check, timeout=timeout
    )


def _image_exists() -> bool:
    if shutil.which("podman") is None:
        return False
    return _podman("image", "inspect", IMAGE, check=False).returncode == 0


pytestmark = [
    pytest.mark.podman,
    pytest.mark.skipif(os.getenv("RUN_PODMAN_TESTS") != "1", reason="set RUN_PODMAN_TESTS=1"),
    pytest.mark.skipif(
        os.getenv("RUN_PODMAN_TESTS") == "1" and not _image_exists(),
        reason=f"podman or image {IMAGE} not available",
    ),
]


@pytest.fixture
def service():
    suffix = uuid.uuid4().hex[:8]
    network = f"qwen_image_test_{suffix}"
    container = f"qwen-image-test-{suffix}"
    try:
        _podman("network", "create", network)
        _podman(
            "run", "-d", "--name", container, "--network", network, "--network-alias", ALIAS,
            "-e", "PRELOAD_MODEL=false", "-e", "HF_HUB_OFFLINE=1", IMAGE,
        )
        yield network, container
    finally:
        _podman("rm", "-f", container, check=False)
        _podman("network", "rm", network, check=False)


def _probe_healthz(network: str) -> dict | None:
    result = _podman(
        "run", "--rm", "--network", network, IMAGE,
        "curl", "-fsS", "--max-time", "5", f"http://{ALIAS}:8000/healthz",
        check=False, timeout=60,
    )
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def test_healthz_reachable_by_name_on_network_without_published_port(service):
    network, container = service

    deadline = time.monotonic() + HEALTH_TIMEOUT_S
    body = None
    while body is None and time.monotonic() < deadline:
        body = _probe_healthz(network)
        if body is None:
            time.sleep(3)
    if body is None:
        logs = _podman("logs", "--tail", "50", container, check=False)
        pytest.fail(f"/healthz не ответил за {HEALTH_TIMEOUT_S} с:\n{logs.stdout}{logs.stderr}")

    assert body["status"] == "ok"
    assert body["loaded"] is False

    assert _podman("port", container).stdout.strip() == ""
    info = json.loads(_podman("inspect", container).stdout)[0]
    assert not info["HostConfig"].get("PortBindings")
    assert all(not bindings for bindings in (info["NetworkSettings"].get("Ports") or {}).values())
