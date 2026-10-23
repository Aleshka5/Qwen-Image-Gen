"""README, Dockerfile, Dockerfile.web и compose.yaml: веб-морда за Auth Gateway.

Модель (qwen-inference: torch, веса, карта) — отдельный проект и отдельная сеть:
её запуск описан в `../Common/models/services/inference/` (или в каталоге
Switch-Service на master-node). Здесь держится граница: compose этого репозитория
описывает ровно один контейнер, у него нет ни карты, ни кэша весов, а порт на хост
не публикует никто.
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

# models_network — сеть модели; веб-морда входит в неё клиентом, чтобы резолвить
# qwen-inference:8010. Домашняя сеть морды — auth-сеть.
NETWORK = "models_network"
AUTH_NETWORK = "deploy_auth_network"
WEB_IMAGE = "qwen-image-service:latest"
# Запуск модели лежит не здесь, а в отдельном проекте:
MODEL_PROJECT = ROOT.parent / "Common" / "models" / "services" / "inference"


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


def _yaml_keys(block: str, indent: int) -> list[str]:
    keys = []
    for line in block.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if len(line) - len(line.lstrip(" ")) != indent:
            continue
        text = line.strip()
        if text.startswith("- "):
            text = text[2:].strip()
        keys.append(text.rsplit(":", 1)[0].strip())
    return keys


@pytest.fixture(scope="module")
def web_commands():
    commands = _run_commands(WEB_IMAGE)
    assert commands, f"в README нет ```bash-блока с `podman run ... {WEB_IMAGE}`"
    return commands


def test_web_run_has_no_gpu_and_keeps_the_outputs(web_commands):
    for tokens in web_commands:
        assert _option(tokens, "--name") == ["qwen-image"]
        assert _option(tokens, "--device") == []
        volumes = _option(tokens, "-v") + _option(tokens, "--volume")
        assert "qwen-outputs:/data/outputs" in volumes
        assert not any("qwen-hf-cache" in volume for volume in volumes)
        networks = _option(tokens, "--network")
        # Домашняя сеть — auth (там её ждут gateway и WebStorage `app`),
        # models_network — только клиентская, чтобы резолвить qwen-inference.
        assert NETWORK in networks
        auth = [value for value in networks if value != NETWORK]
        assert len(auth) == 1
        assert AUTH_NETWORK in auth[0] or "${AUTH_DOCKER_NETWORK:-deploy_auth_network}" in auth[0]


def test_run_does_not_publish_ports(web_commands):
    for tokens in web_commands:
        assert _publish_flags(tokens) == []
        assert not any("8000:8000" in t for t in tokens)
        assert not any("8010:8010" in t for t in tokens)


def test_readme_leaves_both_networks_to_their_owners():
    # models_network создаёт Switch-Service, auth-сеть — Auth-Service; этот
    # репозиторий не создаёт ни одной и не должен учить обратному.
    assert "make network" in README
    assert "podman network create" not in README


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


def test_compose_is_the_web_face_only():
    services = _block(COMPOSE, "services", indent=0)
    # Ровно один сервис: модель — отдельный проект со своим compose.
    assert _yaml_keys(services, 2) == ["qwen-image"]

    web = _block(services, "qwen-image", indent=2)
    assert f"image: localhost/{WEB_IMAGE}" in web
    assert "container_name: qwen-image" in web
    assert "env_file: .env" in web
    assert "nvidia.com/gpu=all" not in web
    assert "qwen-hf-cache" not in web
    assert "qwen-outputs:/data/outputs" in web
    assert "INFERENCE_URL: http://qwen-inference:8010" in web

    networks = _block(web, "networks", indent=4)
    assert f"- {NETWORK}" in networks
    assert f"- {AUTH_NETWORK}" in networks

    assert "ports:" not in COMPOSE
    assert "8000:8000" not in COMPOSE
    # Между проектами нет depends_on: сеть модели поднимается не здесь.
    assert not re.search(r"^\s*depends_on:", COMPOSE, re.MULTILINE)

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
    assert "may contain only the gateway, this service and the Auth stack" in README
    assert re.search(r"WebStorage is not on `models_network`", README)


def test_readme_has_no_host_port_in_commands():
    offenders = [
        line for line in _code_lines(README)
        if re.search(r"(-p|--publish)[ =]\S*8000", line) or "8000:8000" in line
    ]
    assert offenders == []


def test_readme_has_no_host_localhost_8000():
    # `podman exec ... 127.0.0.1:8000` — проба изнутри контейнера, не с хоста.
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


@pytest.mark.skipif(
    not (MODEL_PROJECT / "compose.yaml").exists(),
    reason="отдельный проект запуска модели (Common/models/services/inference) не найден",
)
def test_model_launch_is_a_separate_project_with_its_own_network():
    model_compose = (MODEL_PROJECT / "compose.yaml").read_text(encoding="utf-8")
    services = _block(model_compose, "services", indent=0)
    assert _yaml_keys(services, 2) == ["qwen-inference"]

    model = _block(services, "qwen-inference", indent=2)
    assert "image: localhost/qwen-inference:latest" in model
    assert "container_name: qwen-inference" in model
    assert "nvidia.com/gpu=all" in model
    assert "qwen-hf-cache:/data/huggingface" in model
    assert "shm_size: 8gb" in model
    # Модель живёт только в models_network и никогда в auth-сети.
    nets = _block(model, "networks", indent=4)
    assert _yaml_keys(nets, 6) == [NETWORK]
    assert AUTH_NETWORK not in model
    # Морда и её артефакты модели не принадлежат.
    assert "qwen-outputs" not in model
    assert "Dockerfile.web" not in model
    # Образ модели собирается из Dockerfile этого репозитория.
    assert "dockerfile: Dockerfile" in model


@pytest.mark.skipif(
    not (MODEL_PROJECT / "run.sh").exists(),
    reason="run.sh отдельного проекта запуска модели не найден",
)
def test_model_run_script_refuses_a_second_owner():
    run = (MODEL_PROJECT / "run.sh").read_text(encoding="utf-8")
    # Два владельца одного имени контейнера дерутся каждый reconcile.
    assert "switch.service" in run
    assert "--force" in run
    assert "state image gpu" in run


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
