"""README, Dockerfile, Dockerfile.web и compose.yaml: два контейнера за Auth Gateway.

Инференс с весами и GPU — один образ, Flask-оркестратор — второй. Тесты держат
границу между ними: карта и кэш весов только у первого, WebStorage и выходные
картинки только у второго, порт на хост не публикует никто.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
WEBFILE = (ROOT / "Dockerfile.web").read_text(encoding="utf-8")
COMPOSE = (ROOT / "compose.yaml").read_text(encoding="utf-8")

NETWORK = "qwen_image_gen_network"
AUTH_NETWORK = "deploy_auth_network"
INFERENCE_IMAGE = "qwen-inference:latest"
WEB_IMAGE = "qwen-image-service:latest"


def _code_lines(markdown: str, languages=("bash", "sh", "shell", "")) -> list[str]:
    lines = []
    for lang, body in re.findall(r"^```(\w*)[^\n]*\n(.*?)^```", markdown, re.MULTILINE | re.DOTALL):
        if lang in languages:
            lines.extend(re.sub(r"\\\n\s*", " ", body).splitlines())
    return [line.strip() for line in lines if line.strip()]


def _run_commands(image: str) -> list[list[str]]:
    commands = []
    for line in _code_lines(README, languages=("bash", "sh", "shell")):
        if not line.startswith("podman run"):
            continue
        tokens = shlex.split(line, comments=True)
        if any(image in token for token in tokens):
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


def _publish_flags(tokens: list[str]) -> list[str]:
    return [
        t for t in tokens
        if t in {"-p", "-P", "--publish", "--publish-all"}
        or re.match(r"^(-p|--publish|--publish-all)=", t)
        or re.match(r"^-p\d", t)
    ]


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
def inference_commands():
    commands = _run_commands(INFERENCE_IMAGE)
    assert commands, f"в README нет ```bash-блока с `podman run ... {INFERENCE_IMAGE}`"
    return commands


@pytest.fixture(scope="module")
def web_commands():
    commands = _run_commands(WEB_IMAGE)
    assert commands, f"в README нет ```bash-блока с `podman run ... {WEB_IMAGE}`"
    return commands


def test_inference_run_owns_the_gpu_and_the_weight_cache(inference_commands):
    for tokens in inference_commands:
        assert _option(tokens, "--name") == ["qwen-inference"]
        assert "nvidia.com/gpu=all" in _option(tokens, "--device")
        volumes = _option(tokens, "-v") + _option(tokens, "--volume")
        assert "qwen-hf-cache:/data/huggingface" in volumes
        assert _option(tokens, "--shm-size")
        assert _option(tokens, "--memory")
        assert NETWORK in _option(tokens, "--network")


def test_web_run_has_no_gpu_and_keeps_the_outputs(web_commands):
    for tokens in web_commands:
        assert _option(tokens, "--name") == ["qwen-image"]
        assert _option(tokens, "--device") == []
        volumes = _option(tokens, "-v") + _option(tokens, "--volume")
        assert "qwen-outputs:/data/outputs" in volumes
        assert not any("qwen-hf-cache" in volume for volume in volumes)
        networks = _option(tokens, "--network")
        assert NETWORK in networks
        auth = [value for value in networks if value != NETWORK]
        assert len(auth) == 1
        assert AUTH_NETWORK in auth[0] or "${AUTH_DOCKER_NETWORK:-deploy_auth_network}" in auth[0]


def test_run_does_not_publish_ports(inference_commands, web_commands):
    for tokens in inference_commands + web_commands:
        assert _publish_flags(tokens) == []
        assert not any("8000:8000" in t for t in tokens)
        assert not any("8010:8010" in t for t in tokens)


def test_readme_creates_network():
    assert any(
        re.fullmatch(rf"podman network create {NETWORK}", line)
        for line in _code_lines(README)
    )


@pytest.mark.parametrize(
    "text",
    [
        "https://image.filenkov.store",
        "Auth Gateway",
        NETWORK,
        "http://qwen-image:8000",
        "http://qwen-inference:8010",
        "POST /v1/generate",
        "text/event-stream",
    ],
)
def test_readme_mentions_gateway_topology(text):
    assert text in README


def test_compose_has_two_containers_without_publishing():
    inference = _block(COMPOSE, "qwen-inference", indent=2)
    assert f"image: localhost/{INFERENCE_IMAGE}" in inference
    assert "container_name: qwen-inference" in inference
    assert "env_file: .env" in inference
    assert "shm_size: 8gb" in inference
    assert "mem_limit: 64g" in inference
    assert "nvidia.com/gpu=all" in inference
    assert "qwen-hf-cache:/data/huggingface" in inference
    assert f"- {NETWORK}" in _block(inference, "networks", indent=4)

    web = _block(COMPOSE, "qwen-image", indent=2)
    assert f"image: localhost/{WEB_IMAGE}" in web
    assert "container_name: qwen-image" in web
    assert "env_file: .env" in web
    assert "nvidia.com/gpu=all" not in web
    assert "qwen-hf-cache" not in web
    assert "qwen-outputs:/data/outputs" in web
    networks = _block(web, "networks", indent=4)
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
    assert "docs/inference_api.md" in README


def _instruction(text: str, keyword: str) -> str:
    joined = re.sub(r"\\\n\s*", " ", text)
    matches = [line for line in joined.splitlines() if line.startswith(keyword + " ")]
    assert len(matches) == 1, f"ожидается ровно одна инструкция {keyword}"
    return matches[0][len(keyword) + 1:].strip()


def test_inference_dockerfile_serves_8010():
    cmd = json.loads(_instruction(DOCKERFILE, "CMD"))
    assert cmd[0] == "gunicorn"
    assert _option(cmd, "--bind") == ["0.0.0.0:8010"]
    assert _option(cmd, "--workers") == ["1"]
    # Потоки: один держит SSE-генерацию, другой обязан отвечать /healthz.
    assert int(_option(cmd, "--threads")[0]) > 1
    assert _option(cmd, "--timeout") == ["0"]
    assert cmd[-1] == "inference.wsgi:app"


def test_web_dockerfile_serves_8000():
    cmd = json.loads(_instruction(WEBFILE, "CMD"))
    assert cmd[0] == "gunicorn"
    assert _option(cmd, "--bind") == ["0.0.0.0:8000"]
    assert _option(cmd, "--workers") == ["1"]
    assert _option(cmd, "--timeout") == ["0"]
    assert cmd[-1] == "wsgi:app"


@pytest.mark.parametrize(
    "text,port",
    [(DOCKERFILE, "8010"), (WEBFILE, "8000")],
)
def test_dockerfile_healthcheck_probes_healthz(text, port):
    healthcheck = _instruction(text, "HEALTHCHECK")
    assert re.search(rf"CMD .*curl .*:{port}/healthz\b", healthcheck)


def test_images_install_with_uv_sync():
    assert "uv sync --frozen --no-dev --group ml --no-install-project" in DOCKERFILE
    assert "uv sync --frozen --no-dev --no-install-project" in WEBFILE
    assert "--group ml" not in WEBFILE
    for text in (DOCKERFILE, WEBFILE):
        assert "python3-pip" not in text
        assert "pip install" not in text
        assert "uv.lock" in text
        assert "pyproject.toml" in text


def test_web_image_copies_only_the_orchestrator():
    copies = [line for line in WEBFILE.splitlines() if line.startswith("COPY")]
    assert "COPY --chown=service:service app ./app" in copies
    assert not any("inference" in line for line in copies)
    assert "COPY --chown=service:service inference ./inference" in DOCKERFILE
