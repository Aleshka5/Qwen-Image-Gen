"""Mask-fill form: canvas mask, ordered references, and storage_error."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

from flask import render_template

ROOT = Path(__file__).resolve().parent.parent
MASK_HTML = ROOT / "app" / "templates" / "mask_fill.html"
MASK_JS = ROOT / "app" / "static" / "mask_fill.js"
MASK_CSS = ROOT / "app" / "static" / "mask_fill.css"


class _Ids(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.found: list[tuple[str, bool]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        element_id = attributes.get("id")
        if element_id:
            self.found.append((element_id, "hidden" in attributes))


def _page_html(client, app) -> dict[str, str]:
    pages = {
        "/": client.get("/").get_data(as_text=True),
        "/customize": client.get("/customize").get_data(as_text=True),
    }
    with app.test_request_context("/mask-fill"):
        pages["/mask-fill"] = render_template("mask_fill.html")
    return pages


def _nav(html: str) -> str:
    match = re.search(r'<nav class="pages">.*?</nav>', html, re.S)
    assert match, "nav.pages is missing"
    return match.group(0)


def test_result_card_has_storage_error_after_the_gallery():
    parser = _Ids()
    parser.feed(MASK_HTML.read_text(encoding="utf-8"))
    ids = [element_id for element_id, _hidden in parser.found]
    assert ids.index("result") < ids.index("gallery") < ids.index("storage-error")
    assert dict(parser.found)["storage-error"] is True
    assert "meta" in ids


def test_mask_fill_js_shows_storage_error_only_when_not_saved():
    source = MASK_JS.read_text(encoding="utf-8")
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


def test_failed_mask_fill_does_not_surface_storage_error():
    source = MASK_JS.read_text(encoding="utf-8")
    failed = re.search(
        r"if \(!response\.ok\) \{\s*throw new Error\(payload\.error \|\| `HTTP \$\{response\.status\}`\);\s*\}",
        source,
    )
    assert failed
    catch = re.search(r"\} catch \(error\) \{\s*setStatus\(error\.message, true\);\s*\}", source)
    assert catch
    assert "storageError" not in catch.group(0)
    assert "storage_error" not in catch.group(0)


def test_three_pages_link_to_every_form(client, app):
    pages = _page_html(client, app)
    for path, html in pages.items():
        nav = _nav(html)
        for href in ('href="/"', 'href="/customize"', 'href="/mask-fill"'):
            assert href in nav
        assert nav.count('aria-current="page"') == 1
        current = f'href="{path}"'
        assert re.search(
            rf'{re.escape(current)}[^>]*aria-current="page"',
            nav,
        ), path


def test_form_fields_and_canvas_tools():
    html = MASK_HTML.read_text(encoding="utf-8")
    for name in ("prompt", "negative_prompt", "image", "images", "steps", "true_cfg_scale", "seed"):
        assert f'name="{name}"' in html
    assert 'name="mask"' not in html
    assert 'action="/api/mask-fill"' in html
    for element_id in ("tool-brush", "tool-eraser", "brush-size", "undo", "clear-mask", "mask-view"):
        assert f'id="{element_id}"' in html
    assert "Кисть" in html
    assert "Ластик" in html
    assert "Размер кисти" in html
    assert "Отменить" in html
    assert "Очистить" in html
    assert 'type="range"' in html
    assert "style.css" in html
    assert "mask_fill.css" in html
    assert "mask_fill.js" in html
    assert "canvas" in MASK_CSS.read_text(encoding="utf-8")
    assert 'id="submit"' in html
    assert "disabled" in html.split('id="submit"', 1)[1].split(">", 1)[0]


def test_submitted_mask_is_same_size_black_and_white_png():
    source = MASK_JS.read_text(encoding="utf-8")
    assert "maskCanvas.width = image.naturalWidth" in source
    assert "maskCanvas.height = image.naturalHeight" in source
    assert "src[i + 3] > 0 ? 255 : 0" in source
    assert "out.toBlob" in source
    assert '"image/png"' in source
    assert 'body.append("mask", blob, "mask.png")' in source
    assert "viewCanvas.toBlob" not in source
    assert source.count("toBlob") == 1
    assert 'fetch("/api/mask-fill"' in source
    assert 'body.append("image", photoFile, photoFile.name)' in source
    assert 'body.append("images", item.file, item.file.name)' in source


def test_reference_badges_start_at_image_3_and_can_reorder():
    source = MASK_JS.read_text(encoding="utf-8")
    assert "const number = index + 3;" in source
    assert "`image ${number}`" in source
    assert "MAX_IMAGES - 2" in source
    assert "dragstart" in source
    assert "drop" in source
    assert 'className = "preview-move"' in source
    html = MASK_HTML.read_text(encoding="utf-8")
    assert "image 3, image 4" in html


def test_generate_stays_disabled_until_photo_paint_and_prompt():
    source = MASK_JS.read_text(encoding="utf-8")
    assert "const ready = photo && hasWhite && form.elements.prompt.value.trim();" in source
    assert "submit.disabled = busy || !ready;" in source


def test_rendered_mask_fill_explains_image_order(app):
    with app.test_request_context("/mask-fill"):
        html = render_template("mask_fill.html")
    assert "image 1" in html
    assert "image 2" in html
    assert "image 3, image 4" in html
    assert "до 8" in html
