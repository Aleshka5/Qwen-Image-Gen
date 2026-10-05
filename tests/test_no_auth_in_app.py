"""Граница доверия: решение «пускать или нет» принимает Auth Gateway, приложение auth не содержит.

Исключение — POST /api/generate и POST /api/mask-fill: читают только X-Auth-User-Id
и X-Auth-Email, чтобы сохранить прогон в WebStorage. Остальные заголовки и остальные
маршруты по-прежнему не читают личность.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

SPOOFED_HEADERS = {
    "X-Auth-User": "mallory",
    "X-Auth-User-Id": "not-a-uuid",
    "X-Auth-Email": "mallory@example.com",
    "X-Auth-Role": "ADMIN",
    "X-Auth-Roles": "ADMIN,FAMILY",
    "Authorization": "Bearer x",
}
SPOOFED_COOKIES = {"session": "forged", "access_token": "forged.jwt.token"}

REQUESTS = [
    ("GET", "/healthz", None),
    ("GET", "/", None),
    ("GET", "/customize", None),
    ("GET", "/mask-fill", None),
    ("GET", "/api/config", None),
    ("GET", "/api/logs", None),
    ("POST", "/api/generate", {"prompt": "A lighthouse at dusk", "seed": "42", "resolution": "1024x1024"}),
    ("POST", "/api/generate", {"prompt": ""}),
    ("POST", "/api/customize", {"system_prompt": ""}),
    ("POST", "/api/mask-fill", {"prompt": ""}),
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


@pytest.fixture(autouse=True)
def block_webstorage(monkeypatch):
    """Спойф и маршруты без UUID не должны открывать сокет к Storage."""

    def urlopen(*_args, **_kwargs):
        raise AssertionError("WebStorage must not be called")

    monkeypatch.setattr("app.webstorage.urlopen", urlopen)


@pytest.mark.parametrize(("method", "path", "data"), REQUESTS)
def test_spoofed_auth_headers_and_cookies_are_ignored(app, method, path, data):
    plain = _send(app, method, path, data, spoof=False)
    spoofed = _send(app, method, path, data, spoof=True)
    assert _normalized(plain) == _normalized(spoofed)
    if path in {"/api/generate", "/api/mask-fill"} and plain.status_code == 200:
        for response in (plain, spoofed):
            body = response.get_json()
            assert body["saved"] is False
            assert body["storage_error"] == "not saved: no user id"
            assert "storage_id" not in body
            assert body["images"]
        assert _png_pixels(app, plain) == _png_pixels(app, spoofed)


def _png_pixels(app, response):
    from io import BytesIO

    from PIL import Image

    client = app.test_client()
    pixels = []
    for image in response.get_json()["images"]:
        served = client.get(image["url"])
        assert served.status_code == 200
        assert served.mimetype == "image/png"
        pixels.append(Image.open(BytesIO(served.data)).tobytes())
    return pixels


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


# Эти следы запрещены в любом файле app/, включая путь сохранения генерации.
FORBIDDEN_IN_APP = [
    re.compile(r"jwt", re.IGNORECASE),
    re.compile(r"authorization", re.IGNORECASE),
    re.compile(r"x-auth-role", re.IGNORECASE),
    re.compile(r"request\.cookies"),
    re.compile(r"(?<![\w.])request\.headers"),  # уточняется ниже: только generate
    re.compile(r"\bcookie\b", re.IGNORECASE),
    re.compile(r"\bsession\["),
    re.compile(r"set_cookie"),
    re.compile(r"flask_login|login_required", re.IGNORECASE),
    re.compile(r"from flask import[^\n]*\bsession\b"),
]

# Чтение и пересылка только этих двух имён. Любой другой X-Auth-* роняет сьют.
_ALLOWED_IDENTITY = ("X-Auth-User-Id", "X-Auth-Email")
_X_AUTH = re.compile(r"x-auth[a-z0-9_-]*", re.IGNORECASE)
_FLASK_HEADERS = re.compile(r"(?<![\w.])request\.headers")


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
    """request.headers допускается только внутри сохранения и только для двух заголовков."""
    generate_lines = _save_view_lines()
    hits = []
    for path in _app_sources():
        rel = path.relative_to(ROOT)
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.pattern.startswith(r"(?<![\w.])request\.headers"):
                if not _FLASK_HEADERS.search(line):
                    continue
                allowed = (
                    rel.name == "routes.py"
                    and lineno in generate_lines
                    and _identity_tokens(line)
                    and set(_identity_tokens(line)) <= set(_ALLOWED_IDENTITY)
                )
                if not allowed:
                    hits.append(f"{rel}:{lineno}: {line.strip()}")
                continue
            if pattern.search(line):
                hits.append(f"{rel}:{lineno}: {line.strip()}")
    assert hits == []


def test_identity_headers_only_on_generate_save_path():
    """X-Auth-User-Id и X-Auth-Email — только чтение в сохранении и пересылка в webstorage."""
    generate_lines = _save_view_lines()
    violations = []
    for path in _app_sources():
        rel = path.relative_to(ROOT)
        text = path.read_text(encoding="utf-8")
        if path.suffix != ".py":
            if _X_AUTH.search(text) or _FLASK_HEADERS.search(text):
                violations.append(f"{rel}: identity markup outside Python")
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            tokens = _identity_tokens(line)
            header_read = _FLASK_HEADERS.search(line) is not None
            if not tokens and not header_read:
                continue
            if any(token not in _ALLOWED_IDENTITY for token in tokens):
                violations.append(f"{rel}:{lineno}: forbidden identity token")
                continue
            if rel.name == "routes.py":
                if lineno not in generate_lines or (header_read and not tokens):
                    violations.append(f"{rel}:{lineno}: identity outside the save views")
            elif rel.name != "webstorage.py" or header_read:
                violations.append(f"{rel}:{lineno}: identity outside the save client")
        if path.suffix == ".py":
            violations.extend(_ast_identity_violations(rel, text, generate_lines))
    assert violations == []


def _identity_tokens(line: str) -> list[str]:
    found = []
    for token in _X_AUTH.findall(line):
        canonical = next((name for name in _ALLOWED_IDENTITY if name.lower() == token.lower()), token)
        found.append(canonical if canonical in _ALLOWED_IDENTITY else token)
    return found


_SAVE_VIEWS = frozenset({"api_generate", "api_mask_fill"})


def _save_view_lines() -> set[int]:
    source = (ROOT / "app" / "routes.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    found = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in _SAVE_VIEWS
    }
    assert set(found) == set(_SAVE_VIEWS)
    lines: set[int] = set()
    for node in found.values():
        lines.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return lines


def _ast_identity_violations(rel: Path, source: str, generate_lines: set[int]) -> list[str]:
    """Чтение flask-заголовков — только .get двух имён внутри сохранения. Вызов save — только оттуда."""
    tree = ast.parse(source)
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    violations = []
    for node in ast.walk(tree):
        if _is_flask_headers(node):
            call = parents.get(parents.get(node))
            header_name = _get_header_name(call) if isinstance(call, ast.Call) else None
            if (
                rel.name != "routes.py"
                or node.lineno not in generate_lines
                or header_name not in _ALLOWED_IDENTITY
            ):
                violations.append(f"{rel}:{node.lineno}: request.headers outside the save views")
        if isinstance(node, ast.Call) and _call_name(node) == "save_generated":
            if rel.name != "routes.py" or node.lineno not in generate_lines:
                violations.append(f"{rel}:{node.lineno}: save_generated outside the save views")
    return violations


def _is_flask_headers(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "headers"
        and isinstance(node.value, ast.Name)
        and node.value.id == "request"
    )


def _get_header_name(call: ast.Call | None) -> str | None:
    if call is None or not call.args:
        return None
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr != "get":
        return None
    arg = call.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    return None


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


AUTH_PACKAGES = {
    "pyjwt", "jwt", "python-jose", "jose", "authlib", "flask-login",
    "flask-jwt-extended", "flask-httpauth", "flask-security", "flask-security-too",
}


def _pyproject_array(key: str) -> str:
    """Тело массива `key = [ ... ]` в pyproject.toml (dependencies или группа ml)."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(rf"(?m)^{re.escape(key)}\s*=\s*\[", text)
    assert match, key
    depth = 1
    i = match.end()
    start = i
    while i < len(text) and depth:
        if text[i] == "[":
            depth += 1
        elif text[i] == "]":
            depth -= 1
        i += 1
    assert depth == 0, key
    return text[start : i - 1]


def _dependency_names(body: str) -> set[str]:
    names = set()
    body = re.sub(r"#.*", "", body)
    for match in re.finditer(r"""(['"])(.*?)\1""", body):
        line = match.group(2).strip()
        if line and not line.startswith("-"):
            names.add(re.split(r"[\s<>=!~;\[@]", line, maxsplit=1)[0].lower().replace("_", "-"))
    return names


@pytest.mark.parametrize("key", ["dependencies", "ml"])
def test_pyproject_has_no_auth_libraries(key):
    assert _dependency_names(_pyproject_array(key)) & AUTH_PACKAGES == set()
