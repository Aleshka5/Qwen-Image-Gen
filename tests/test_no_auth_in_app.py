"""Граница доверия: решение «пускать или нет» принимает Auth Gateway, приложение auth не содержит."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

SPOOFED_HEADERS = {
    "X-Auth-User": "mallory",
    "X-Auth-Role": "ADMIN",
    "X-Auth-Roles": "ADMIN,FAMILY",
    "Authorization": "Bearer x",
}
SPOOFED_COOKIES = {"session": "forged", "access_token": "forged.jwt.token"}

REQUESTS = [
    ("GET", "/healthz", None),
    ("GET", "/", None),
    ("GET", "/customize", None),
    ("GET", "/api/config", None),
    ("GET", "/api/logs", None),
    ("POST", "/api/generate", {"prompt": "A lighthouse at dusk", "seed": "42", "resolution": "1024x1024"}),
    ("POST", "/api/generate", {"prompt": ""}),
    ("POST", "/api/customize", {"system_prompt": ""}),
]


def _normalized(response):
    body = response.get_json(silent=True)
    if body is None:
        return response.status_code, response.data
    if "images" in body:
        body = {**body, "images": [i["url"].rsplit("/", 1)[0] for i in body["images"]]}
    return response.status_code, body


def _send(app, method, path, data, *, spoof):
    client = app.test_client()
    headers = {}
    if spoof:
        headers = SPOOFED_HEADERS
        for name, value in SPOOFED_COOKIES.items():
            client.set_cookie(name, value)
    return client.open(path, method=method, data=data, headers=headers)


@pytest.mark.parametrize(("method", "path", "data"), REQUESTS)
def test_spoofed_auth_headers_and_cookies_are_ignored(app, method, path, data):
    plain = _send(app, method, path, data, spoof=False)
    spoofed = _send(app, method, path, data, spoof=True)
    assert _normalized(plain) == _normalized(spoofed)


@pytest.mark.parametrize(("method", "path", "data"), REQUESTS)
def test_no_cookies_are_set(app, method, path, data):
    for spoof in (False, True):
        response = _send(app, method, path, data, spoof=spoof)
        assert "Set-Cookie" not in response.headers


AUTH_PATHS = ["/login", "/logout", "/auth", "/auth/login", "/signin"]


def test_no_auth_rules_in_url_map(app):
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert not {r for r in rules if re.search(r"login|logout|auth|signin|session|token", r)}


@pytest.mark.parametrize("path", AUTH_PATHS)
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_auth_paths_are_not_served(client, method, path):
    status = client.open(path, method=method).status_code
    assert status >= 404 and status not in (401, 403)


@pytest.mark.parametrize("path", AUTH_PATHS)
def test_auth_paths_are_404(client, path):
    assert client.get(path).status_code == 404


FORBIDDEN_IN_APP = [
    re.compile(r"x-auth", re.IGNORECASE),
    re.compile(r"jwt", re.IGNORECASE),
    re.compile(r"request\.cookies"),
    re.compile(r"request\.headers"),
    re.compile(r"\bsession\["),
    re.compile(r"set_cookie"),
    re.compile(r"flask_login|login_required", re.IGNORECASE),
    re.compile(r"from flask import[^\n]*\bsession\b"),
]


def _app_sources():
    return [
        p for p in (ROOT / "app").rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix in {".py", ".html", ".js"}
    ]


def test_app_sources_exist():
    names = {p.name for p in _app_sources()}
    assert {"__init__.py", "routes.py", "index.html", "app.js"} <= names


@pytest.mark.parametrize("pattern", FORBIDDEN_IN_APP, ids=lambda p: p.pattern)
def test_app_contains_no_auth_code(pattern):
    hits = [
        f"{path.relative_to(ROOT)}:{lineno}: {line.strip()}"
        for path in _app_sources()
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert hits == []


AUTH_PACKAGES = {
    "pyjwt", "jwt", "python-jose", "jose", "authlib", "flask-login",
    "flask-jwt-extended", "flask-httpauth", "flask-security", "flask-security-too",
}


@pytest.mark.parametrize("filename", ["requirements.txt", "requirements-ml.txt"])
def test_requirements_have_no_auth_libraries(filename):
    names = set()
    for line in (ROOT / filename).read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line and not line.startswith("-"):
            names.add(re.split(r"[\s<>=!~;\[@]", line, maxsplit=1)[0].lower().replace("_", "-"))
    assert names & AUTH_PACKAGES == set()
