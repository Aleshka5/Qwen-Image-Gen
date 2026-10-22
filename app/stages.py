"""HTTP client for the three inference services.

One transport for every hop, matching ``docs/models_inference_api.md``:

* request: ``multipart/form-data`` with a ``meta`` JSON field, repeated
  ``tensors`` fields (``safetensors`` blobs) and repeated ``images`` (PNG);
* success: an octet-stream blob, or ``text/event-stream`` for the denoiser;
* failure: ``{"error": "..."}`` with 400/503/500.

Nothing here imports torch: the orchestrator forwards blobs it has never
opened, which is what keeps this process at a couple of hundred megabytes.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any, Iterator

import requests

log = logging.getLogger(__name__)

CONNECT_TIMEOUT = 10.0


class StageError(RuntimeError):
    """A stage refused the hop or failed it. Carries the stage name and status."""

    def __init__(self, stage: str, message: str, *, status: int | None = None) -> None:
        detail = f"{stage} (HTTP {status}): {message}" if status else f"{stage}: {message}"
        super().__init__(detail)
        self.stage = stage
        self.status = status


def post(
    url: str,
    *,
    meta: dict[str, Any] | None = None,
    blobs: list[bytes] | None = None,
    images: list[bytes] | None = None,
    timeout: float,
    stream: bool = False,
) -> requests.Response:
    """One multipart POST. 4xx/5xx answers are returned, not raised."""
    files: list[tuple] = [
        ("tensors", (f"blob{index}.safetensors", blob, "application/octet-stream"))
        for index, blob in enumerate(blobs or [])
    ]
    if meta is not None:
        files.append(("meta", (None, json.dumps(meta, separators=(",", ":")))))
    for index, png in enumerate(images or []):
        files.append(("images", (f"image{index}.png", png, "image/png")))
    return requests.post(url, files=files, timeout=(CONNECT_TIMEOUT, timeout), stream=stream)


def check(stage: str, response: requests.Response) -> None:
    """Turn a non-2xx answer into a :class:`StageError` carrying the stage's own message."""
    if response.ok:
        return
    message = response.text[:500]
    try:
        payload = json.loads(response.text)
        if isinstance(payload, dict) and payload.get("error"):
            message = str(payload["error"])
    except (ValueError, TypeError):
        pass
    raise StageError(stage, message, status=response.status_code)


def events(response: requests.Response) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield ``(event, payload)`` frames from the denoiser's SSE stream."""
    event = "message"
    data: list[str] = []
    for raw in response.iter_lines(decode_unicode=True):
        if raw is None:
            continue
        line = raw.rstrip("\r")
        if not line:
            if data:
                yield event, _parse(data)
            event, data = "message", []
            continue
        if line.startswith(":"):
            continue  # heartbeat / comment
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
        else:
            data.append(line)
    if data:
        yield event, _parse(data)


def _parse(chunks: list[str]) -> dict[str, Any]:
    try:
        payload = json.loads("\n".join(chunks))
    except ValueError:
        return {"raw": "\n".join(chunks)}
    return payload if isinstance(payload, dict) else {"value": payload}


def base64_blob(text: str) -> bytes:
    try:
        return base64.b64decode(text.encode("ascii"), validate=True)
    except Exception as exc:
        raise ValueError(f"invalid base64 payload: {exc}") from exc


def health(url: str, timeout: float) -> dict[str, Any]:
    """``GET /healthz`` on a stage."""
    answer = requests.get(f"{url}/healthz", timeout=(2, timeout))
    payload = answer.json() if answer.content else {}
    if not isinstance(payload, dict):
        raise ValueError(f"{url}/healthz did not answer with JSON")
    if answer.status_code >= 400 and not payload.get("error"):
        payload["error"] = f"HTTP {answer.status_code}"
    return payload
