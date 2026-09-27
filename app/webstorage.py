"""Клиент сохранения генерации в WebStorage. Только stdlib."""

from __future__ import annotations

import json
import logging
import uuid
from typing import NoReturn
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import settings

log = logging.getLogger(__name__)


class StorageError(Exception):
    """WebStorage не принял прогон. Генерация при этом уже состоялась."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def save_generated(
    *,
    user_id: str,
    email: str | None,
    prompt: str,
    negative_prompt: str,
    seed: int,
    steps: int,
    true_cfg_scale: float,
    width: int,
    height: int,
    duration: float,
    images: list[bytes],
    result_png: bytes,
    result_name: str,
) -> str:
    """POST /api/generated. Возвращает id из ответа 201. Иначе StorageError."""
    body, boundary = _encode(
        fields=[
            ("prompt", prompt),
            ("negative_prompt", negative_prompt),
            ("seed", str(seed)),
            ("steps", str(steps)),
            ("true_cfg_scale", _number(true_cfg_scale)),
            ("width", str(width)),
            ("height", str(height)),
            ("duration", f"{duration:.1f}"),
        ],
        files=[
            *[("images", f"image-{index}.png", payload) for index, payload in enumerate(images, start=1)],
            ("result", result_name, result_png),
        ],
    )
    # Ключи задаём напрямую: Request.add_header меняет регистр имени.
    outbound = Request(
        f"{settings.webstorage_url}/api/generated",
        data=body,
        method="POST",
    )
    outbound.headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    outbound.headers["X-Auth-User-Id"] = user_id
    if email:
        outbound.headers["X-Auth-Email"] = email

    try:
        with urlopen(outbound, timeout=settings.webstorage_timeout) as response:
            raw = response.read()
            status = response.status
    except TimeoutError as exc:
        _fail("timeout", exc)
    except HTTPError as exc:
        _fail(_http_reason(exc), exc)
    except URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            _fail("timeout", exc)
        _fail("connection error", exc)

    if status != 201:
        _fail(str(status))

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail("invalid storage response", exc)
    storage_id = payload.get("id") if isinstance(payload, dict) else None
    if not isinstance(storage_id, str) or not storage_id:
        _fail("invalid storage response")
    return storage_id


def _fail(reason: str, exc: BaseException | None = None) -> NoReturn:
    log.warning("Не удалось сохранить генерацию в WebStorage: %s", reason)
    raise StorageError(reason) from exc


def _http_reason(exc: HTTPError) -> str:
    raw = exc.read()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return str(exc.code)
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, str) and error:
            return error
    return str(exc.code)


def _number(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text or "0"


def _encode(
    fields: list[tuple[str, str]],
    files: list[tuple[str, str, bytes]],
) -> tuple[bytes, str]:
    boundary = f"qwen{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields:
        chunks.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n'
            f"\r\n".encode()
        )
        chunks.append(value.encode("utf-8"))
        chunks.append(b"\r\n")
    for name, filename, payload in files:
        chunks.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            f"Content-Type: image/png\r\n"
            f"\r\n".encode()
        )
        chunks.append(payload)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), boundary
