"""The header shows the gateway username. It does not start a session."""

from __future__ import annotations

import pytest

_PAGES = ("/",)


def _page(client, path: str, headers: dict[str, str] | None = None) -> str:
    response = client.get(path, headers=headers or {})
    assert response.status_code == 200
    return response.get_data(as_text=True)


def _slot(html: str) -> str:
    start = html.index('<div class="header-right">')
    end = html.index("</div>", start)
    return html[start:end]


@pytest.mark.parametrize("path", _PAGES)
def test_guest_sees_sign_in(client, path):
    slot = _slot(_page(client, path))
    assert 'id="sign-in"' in slot
    assert "Sign in" in slot
    assert "Sign out" not in slot
    assert "user-name" not in slot


@pytest.mark.parametrize("path", _PAGES)
def test_signed_in_sees_username_and_sign_out(client, path):
    slot = _slot(_page(client, path, {"X-Auth-Name": "Ada Lovelace"}))
    assert "Ada Lovelace" in slot
    assert 'class="user-name"' in slot
    assert 'href="https://filenkov.store"' in slot
    assert "Sign out" in slot
    assert 'id="sign-in"' not in slot
    assert "Sign in" not in slot


@pytest.mark.parametrize("path", _PAGES)
def test_email_role_and_user_header_do_not_fill_the_slot(client, path):
    slot = _slot(
        _page(
            client,
            path,
            {
                "X-Auth-Email": "ada@example.com",
                "X-Auth-Role": "ADMIN",
                "X-Auth-User": "mallory",
                "X-Auth-User-Id": "11111111-1111-4111-8111-111111111111",
            },
        )
    )
    assert "ada@example.com" not in slot
    assert "mallory" not in slot
    assert "ADMIN" not in slot
    assert 'id="sign-in"' in slot


@pytest.mark.parametrize("raw", ["", "   ", "Ada\x00"])
def test_blank_or_broken_name_stays_signed_out(client, raw):
    slot = _slot(_page(client, "/", {"X-Auth-Name": raw}))
    assert 'id="sign-in"' in slot
    assert "Sign out" not in slot


def test_username_is_escaped(client):
    page = _page(client, "/", {"X-Auth-Name": "<b>Ada</b>"})
    assert "<b>Ada</b>" not in page
    assert "&lt;b&gt;Ada&lt;/b&gt;" in page
