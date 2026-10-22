"""README, Dockerfile и compose.yaml: оркестратор за Auth Gateway, без своего GPU и без публикации 8000.

С тех пор как пайплайн разложен на три inference-сервиса (ADR 0009), у этого
репозитория другие инварианты: сеть `models_network`, имя контейнера
`qwen-image-split` (с алиасом `qwen-image`), никакого `--device`, единственный
опубликованный порт — `127.0.0.1:18000` для health-проб.
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
COMPOSE = (ROOT / "compose.yaml").read_text(encoding="utf-8")

NETWORK = "models_network"
AUTH_NETWORK = "deploy_auth_network"
CONTAINER = "qwen-image-split"
ALIAS = "qwen-image"


def _code_lines(markdown: str, languages=("bash", "sh", "shell", "")) -> list[str]:
    lines = []
    for lang, body in re.findall(r"^```(\w*)[^\n]*\n(.*?)^```", markdown, re.MULTILINE | re.DOTALL):
        if lang in languages:
            lines.extend(re.sub(r"\\\n\s*", " ", body).splitlines())
    return [line.strip() for line in lines if line.strip()]


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


def test_compose_runs_the_orchestrator_only():
    service = _block(COMPOSE, "qwen-image", indent=2)
    assert f"image: localhost/qwen-image-service:latest" in service
    assert f"container_name: {CONTAINER}" in service
    assert "env_file: .env" in service
    assert "qwen-outputs:/data/outputs" in service
    # Модели не здесь: GPU-устройства и том с весами этому контейнеру не нужны.
    assert "nvidia.com/gpu=all" not in COMPOSE
    assert not re.search(r"^\s+devices:", COMPOSE, re.M)
    assert "qwen-hf-cache" not in COMPOSE
    # Порты: только loopback для health-проб, 8000 наружу не выходит.
    assert '127.0.0.1:18000:8000' in service
    assert "8000:8000" not in COMPOSE.replace("127.0.0.1:18000:8000", "")

    networks = _block(service, "networks", indent=4)
    assert NETWORK in networks
    assert AUTH_NETWORK in networks
    assert f"- {ALIAS}" in networks

    declared = _block(COMPOSE, "networks", indent=0)
    for name in (NETWORK, AUTH_NETWORK):
        assert "external: true" in _block(declared, name, indent=2)


def test_compose_has_no_inference_stages():
    """Три стадии живут в ../Common/models; в этом файле их быть не должно."""
    for stage in ("qwen-embed", "qwen-vae", "qwen-dit"):
        assert stage not in COMPOSE.split("services:", 1)[1].split("networks:", 1)[0]


@pytest.mark.parametrize(
    "text",
    ["https://image.filenkov.store", "Auth Gateway", NETWORK, "http://qwen-image:8000", CONTAINER],
)
def test_readme_mentions_gateway_topology(text):
    assert text in README


def test_readme_documents_start_order():
    """Сначала базовый образ и стадии, потом оркестратор — иначе имена не резолвятся."""
    lines = _code_lines(README)
    joined = "\n".join(lines)
    assert "podman build -t localhost/qwen-image-service:latest" in joined
    assert "podman compose up -d" in joined
    assert "../Common/models" in README and "./scripts/up.sh" in README
    assert README.index("Common/models") < README.index("podman compose up -d")


def test_readme_documents_the_stage_contract():
    assert "docs/models_inference_api.md" in README
    assert "docs/adr/0009" in README


def test_readme_states_webstorage_save_and_network_boundary():
    assert "WEBSTORAGE_URL" in README or "http://app:8000" in README
    assert "X-Auth-User-Id" in README
    assert "The trust boundary is the network itself" in README
    assert re.search(r"WebStorage is not on `models_network`", README)


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


def test_dockerfile_is_the_base_of_the_inference_services():
    """Стадии из Common/models делают FROM этого образа — это должно быть записано."""
    assert "localhost/qwen-image-service:latest" in DOCKERFILE
    assert "Common/models" in DOCKERFILE
