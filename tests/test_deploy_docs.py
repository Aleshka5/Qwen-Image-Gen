"""README, Dockerfile и compose.yaml: запуск за Auth Gateway в двух сетях без публикации порта."""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
COMPOSE = (ROOT / "compose.yaml").read_text(encoding="utf-8")

NETWORK = "qwen_image_gen_network"
AUTH_NETWORK = "deploy_auth_network"


def _code_lines(markdown: str, languages=("bash", "sh", "shell", "")) -> list[str]:
    lines = []
    for lang, body in re.findall(r"^```(\w*)[^\n]*\n(.*?)^```", markdown, re.MULTILINE | re.DOTALL):
        if lang in languages:
            lines.extend(re.sub(r"\\\n\s*", " ", body).splitlines())
    return [line.strip() for line in lines if line.strip()]


def _service_run_commands() -> list[list[str]]:
    commands = []
    for line in _code_lines(README, languages=("bash", "sh", "shell")):
        if not line.startswith("podman run"):
            continue
        tokens = shlex.split(line, comments=True)
        if "qwen-image-service:latest" in tokens and "--device" in tokens:
            commands.append(tokens)
    return commands


def _option(tokens: list[str], name: str) -> list[str]:
    values = []
    for i, token in enumerate(tokens):
        if token == name and i + 1 < len(tokens):
            values.append(tokens[i + 1])
        elif token.startswith(name + "="):
            values.append(token.split("=", 1)[1])
    return values


def _block(text: str, key: str, indent: int) -> str:
    """Тело YAML-ключа на заданном отступе, без вложенных соседей того же уровня."""
    prefix = " " * indent + key + ":"
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line == prefix or line.startswith(prefix + " "):
            start = i + 1
            break
    assert start is not None, f"нет ключа {key!r} с отступом {indent}"
    body: list[str] = []
    for line in lines[start:]:
        if not line.strip() or line.lstrip().startswith("#"):
            body.append(line)
            continue
        current = len(line) - len(line.lstrip(" "))
        if current <= indent:
            break
        body.append(line)
    return "\n".join(body)


@pytest.fixture(scope="module")
def run_commands():
    commands = _service_run_commands()
    assert commands, "в README нет ```bash-блока с `podman run ... --device ... qwen-image-service:latest`"
    return commands


def test_run_uses_container_name_qwen_image(run_commands):
    for tokens in run_commands:
        assert _option(tokens, "--name") == ["qwen-image"]


def test_run_attaches_network(run_commands):
    for tokens in run_commands:
        networks = _option(tokens, "--network")
        assert NETWORK in networks
        auth = [value for value in networks if value != NETWORK]
        assert len(auth) == 1
        assert AUTH_NETWORK in auth[0] or "${AUTH_DOCKER_NETWORK:-deploy_auth_network}" in auth[0]


def test_run_does_not_publish_ports(run_commands):
    for tokens in run_commands:
        publish = [
            t for t in tokens
            if t in {"-p", "-P", "--publish", "--publish-all"}
            or re.match(r"^(-p|--publish|--publish-all)=", t)
            or re.match(r"^-p\d", t)
        ]
        assert publish == []
        assert not any("8000:8000" in t for t in tokens)


def test_run_keeps_gpu_volumes_and_limits(run_commands):
    for tokens in run_commands:
        assert "nvidia.com/gpu=all" in _option(tokens, "--device")
        volumes = _option(tokens, "-v") + _option(tokens, "--volume")
        assert "qwen-hf-cache:/data/huggingface" in volumes
        assert "qwen-outputs:/data/outputs" in volumes
        assert _option(tokens, "--shm-size")
        assert _option(tokens, "--memory")


def test_readme_creates_network():
    assert any(
        re.fullmatch(rf"podman network create {NETWORK}", line)
        for line in _code_lines(README)
    )


@pytest.mark.parametrize(
    "text",
    ["https://image.filenkov.store", "Auth Gateway", NETWORK, "http://qwen-image:8000"],
)
def test_readme_mentions_gateway_topology(text):
    assert text in README


def test_compose_attaches_both_networks_without_publishing():
    service = _block(COMPOSE, "qwen-image", indent=2)
    assert "image: qwen-image-service:latest" in service
    assert "container_name: qwen-image" in service
    assert "env_file: .env" in service
    assert "shm_size: 8gb" in service
    assert "mem_limit: 64g" in service
    assert "nvidia.com/gpu=all" in service
    assert "qwen-hf-cache:/data/huggingface" in service
    assert "qwen-outputs:/data/outputs" in service
    networks = _block(service, "networks", indent=4)
    assert f"- {NETWORK}" in networks
    assert f"- {AUTH_NETWORK}" in networks
    assert "ports:" not in COMPOSE
    assert "8000:8000" not in COMPOSE

    declared = _block(COMPOSE, "networks", indent=0)
    for name in (NETWORK, AUTH_NETWORK):
        body = _block(declared, name, indent=2)
        assert "external: true" in body

    assert "compose.yaml" in README
    assert not (ROOT / "deploy" / "qwen-image.container").exists()
    assert not (ROOT / "Containerfile").exists()


def test_readme_states_webstorage_save_and_network_boundary():
    assert "WEBSTORAGE_URL" in README or "http://app:8000" in README
    assert "X-Auth-User-Id" in README
    assert "only the gateway and `qwen-image`" in README
    assert re.search(r"WebStorage is not on `qwen_image_gen_network`", README)


def test_readme_has_no_host_port_in_commands():
    offenders = [
        line for line in _code_lines(README)
        if re.search(r"(-p|--publish)[ =]\S*8000", line) or "8000:8000" in line
    ]
    assert offenders == []


def test_readme_has_no_host_localhost_8000():
    # `podman exec ... localhost:8000` — проба изнутри контейнера, не с хоста.
    offenders = [
        line for line in README.splitlines()
        if "localhost:8000" in line and "podman exec" not in line
    ]
    assert offenders == []


def test_readme_documents_uv_and_podman():
    assert "uv sync" in README
    assert "uv run pytest" in README
    assert "podman build" in README
    assert "podman compose" in README
    for forbidden in ("docker run", "docker build", "docker compose", "docker exec", "docker network"):
        assert forbidden not in README
    assert "Containerfile" not in README
    assert "requirements-dev.txt" not in README
    assert "docs/api-contract.md" in README
    assert "docs/design.md" in README
    assert "docs/adr/" in README
    assert "docs/tests.md" in README


def _instruction(keyword: str) -> str:
    joined = re.sub(r"\\\n\s*", " ", DOCKERFILE)
    matches = [line for line in joined.splitlines() if line.startswith(keyword + " ")]
    assert len(matches) == 1, f"ожидается ровно одна инструкция {keyword}"
    return matches[0][len(keyword) + 1:].strip()


def test_dockerfile_gunicorn_listens_on_container_network():
    cmd = json.loads(_instruction("CMD"))
    assert cmd[0] == "gunicorn"
    assert _option(cmd, "--bind") == ["0.0.0.0:8000"]
    assert _option(cmd, "--workers") == ["1"]
    assert _option(cmd, "--timeout") == ["0"]
    assert cmd[-1] == "wsgi:app"


def test_dockerfile_healthcheck_probes_healthz():
    healthcheck = _instruction("HEALTHCHECK")
    assert re.search(r"CMD .*curl .*:8000/healthz\b", healthcheck)


def test_dockerfile_installs_with_uv_sync():
    assert "uv sync --frozen --no-dev --group ml --no-install-project" in DOCKERFILE
    assert "python3-pip" not in DOCKERFILE
    assert "pip install" not in DOCKERFILE
    assert "uv.lock" in DOCKERFILE
    assert "pyproject.toml" in DOCKERFILE
