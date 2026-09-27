"""Форма генерации показывает storage_error только когда сохранение не удалось."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "app" / "templates" / "index.html"
APP_JS = ROOT / "app" / "static" / "app.js"


class _Ids(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.found: list[tuple[str, bool]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        element_id = attributes.get("id")
        if element_id:
            self.found.append((element_id, "hidden" in attributes))


def test_result_card_has_storage_error_after_the_gallery():
    parser = _Ids()
    parser.feed(INDEX.read_text(encoding="utf-8"))
    ids = [element_id for element_id, _hidden in parser.found]
    assert ids.index("result") < ids.index("gallery") < ids.index("storage-error")
    assert dict(parser.found)["storage-error"] is True


def test_app_js_shows_storage_error_only_when_not_saved():
    source = APP_JS.read_text(encoding="utf-8")
    for line in source.splitlines():
        if "storageError" in line or "storage_error" in line:
            assert "innerHTML" not in line

    show = re.search(
        r"const notSaved = payload\.saved === false && typeof payload\.storage_error === \"string\"\s*"
        r"\? payload\.storage_error\s*"
        r": \"\";\s*"
        r"storageError\.textContent = notSaved;\s*"
        r"storageError\.hidden = !notSaved;",
        source,
    )
    assert show, "storage_error must be assigned with textContent and hidden unless saved === false"
    assert "storageError.innerHTML" not in source


def test_failed_generate_does_not_surface_storage_error():
    source = APP_JS.read_text(encoding="utf-8")
    failed = re.search(
        r"if \(!response\.ok\) \{\s*throw new Error\(payload\.error \|\| `HTTP \$\{response\.status\}`\);\s*\}",
        source,
    )
    assert failed
    catch = re.search(r"\} catch \(error\) \{\s*setStatus\(error\.message, true\);\s*\}", source)
    assert catch
    assert "storageError" not in catch.group(0)
    assert "storage_error" not in catch.group(0)
