"""README и Containerfile: запуск за Auth Gateway в сети qwen_image_gen_network без публикации порта."""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
CONTAINERFILE = (ROOT / "Containerfile").read_text(encoding="utf-8")

NETWORK = "qwen_image_gen_network"


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
        if "qwen-image-service:latest" in tokens and "--device" in " ".join(tokens):
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
        assert _option(tokens, "--network") == [NETWORK]


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


def _instruction(keyword: str) -> str:
    joined = re.sub(r"\\\n\s*", " ", CONTAINERFILE)
    matches = [line for line in joined.splitlines() if line.startswith(keyword + " ")]
    assert len(matches) == 1, f"ожидается ровно одна инструкция {keyword}"
    return matches[0][len(keyword) + 1:].strip()


def test_containerfile_gunicorn_listens_on_container_network():
    cmd = json.loads(_instruction("CMD"))
    assert cmd[0] == "gunicorn"
    assert _option(cmd, "--bind") == ["0.0.0.0:8000"]
    assert _option(cmd, "--workers") == ["1"]
    assert _option(cmd, "--timeout") == ["0"]
    assert cmd[-1] == "wsgi:app"


def test_containerfile_healthcheck_probes_healthz():
    healthcheck = _instruction("HEALTHCHECK")
    assert re.search(r"CMD .*curl .*:8000/healthz\b", healthcheck)
