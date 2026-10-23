"""Call local Qwen, Wan, or Higgsfield. External calls have no step stream."""

from __future__ import annotations

import base64
import io
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PIL import Image

from .catalog import WAN_REGIONS, model_spec
from .crypto import open_secret
from .errors import AppError

_OPEN = urlopen


def run(task: dict, images: list[tuple[str, Image.Image]], on_progress, provider: dict, secret: str):
    spec = model_spec(provider["kind"], task["model"])
    if spec is None:
        raise AppError(f"Model {task['model']} is not in the catalog")
    if task["mode"] not in spec["modes"]:
        raise AppError(f"{spec['label']} does not support {task['mode'].replace('_', ' ')}")
    kind = provider["kind"]
    if kind == "qwen":
        return _qwen(task, images, on_progress)
    if kind == "wan":
        return _wan(task, images, provider, spec, secret)
    if kind == "higgsfield":
        return _higgsfield(task, images, provider, spec, secret)
    raise AppError(f"Unknown provider {kind}")


def _qwen(task, images, on_progress):
    from app.imaging import fit_model_size
    from app.inference import GenerationRequest, InferenceClient

    ordered = [image for _role, image in images]
    width, height = int(task["width"]), int(task["height"])
    if task["mode"] == "generate":
        pipeline = ordered
    else:
        source = ordered[0]
        width, height = fit_model_size(source.width, source.height)
        fitted = [source if source.size == (width, height) else source.resize((width, height), Image.LANCZOS)]
        rest = ordered[1:]
        if task["mode"] == "mask_fill" and rest:
            mask = rest[0].resize((width, height), Image.NEAREST)
            fitted.append(mask)
            rest = rest[1:]
        pipeline = fitted + rest
    result = InferenceClient().generate(
        GenerationRequest(
            prompt=task["prompt"],
            negative_prompt=task.get("negative_prompt") or "",
            width=width,
            height=height,
            steps=int(task["steps"]),
            true_cfg_scale=float(task["true_cfg_scale"]),
            seed=int(task["seed"]),
            images=pipeline,
        ),
        on_progress=on_progress,
    )
    return result.images


def _wan(task, images, provider, spec, secret):
    token = open_secret(secret, provider["secret_enc"])
    region = WAN_REGIONS.get(provider.get("region") or "")
    workspace = (provider.get("workspace_id") or "").strip()
    if not region or not workspace:
        raise AppError("Wan needs a region and a workspace id")
    url = (
        f"https://{workspace}.{region}.maas.aliyuncs.com"
        "/api/v1/services/aigc/multimodal-generation/generation"
    )
    content = [{"text": task["prompt"]}]
    protocol = spec.get("protocol")
    if protocol == "wan-edit":
        for _role, image in images[: spec["max_images"]]:
            content.append({"image": _jpeg_b64(image)})
    body = {
        "model": task["model"],
        "input": {"messages": [{"role": "user", "content": content}]},
        "parameters": {
            "prompt_extend": False,
            "watermark": False,
            "n": 1,
            "negative_prompt": task.get("negative_prompt") or "",
            "size": _wan_size(int(task["width"]), int(task["height"]), edit=protocol == "wan-edit"),
        },
    }
    if protocol == "wan-edit":
        body["parameters"]["enable_interleave"] = False
    headers = {"Content-Type": "application/json"}
    # The vendor header is assembled here, outside the Flask process.
    headers["Authorization"] = f"Bearer {token}"
    if protocol == "wan-async":
        headers["X-DashScope-Async"] = "enable"
    payload = _json(url, body, headers, timeout=180)
    if protocol == "wan-async":
        task_id = (payload.get("output") or {}).get("task_id")
        if not task_id:
            raise AppError("Wan did not return a task id", 502)
        payload = _poll_wan(f"https://{workspace}.{region}.maas.aliyuncs.com/api/v1/tasks/{task_id}", headers)
    return [_download(url) for url in _wan_urls(payload)] or (_fail("Wan returned no image"))


def _poll_wan(url, headers) -> dict:
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        payload = _json(url, None, headers, timeout=60, method="GET")
        status = ((payload.get("output") or {}).get("task_status") or "").upper()
        if status in {"SUCCEEDED", "FAILED", "UNKNOWN"}:
            if status != "SUCCEEDED":
                message = (payload.get("output") or {}).get("message") or status
                raise AppError(f"Wan {message}", 502)
            return payload
        time.sleep(3)
    raise AppError("Wan timed out", 504)


def _wan_urls(payload: dict) -> list[str]:
    output = payload.get("output") or {}
    urls = []
    for choice in output.get("choices") or []:
        for part in ((choice.get("message") or {}).get("content") or []):
            if part.get("image"):
                urls.append(part["image"])
    for item in output.get("results") or []:
        if item.get("url"):
            urls.append(item["url"])
    return urls


def _wan_size(width: int, height: int, *, edit: bool) -> str:
    if edit:
        return "2K" if max(width, height) > 1400 else "1K"
    aspect = min(4.0, max(0.25, width / max(height, 1)))
    area = 1280 * 1280
    side_h = max(512, int(round((area / aspect) ** 0.5)))
    side_w = max(512, int(round(side_h * aspect)))
    return f"{side_w}*{side_h}"


def _higgsfield(task, images, provider, spec, secret):
    token = open_secret(secret, provider["secret_enc"])
    headers = {"Content-Type": "application/json", "Authorization": f"Key {token}"}
    body = {
        "prompt": task["prompt"],
        "seed": int(task["seed"]) % 1_000_000 or 1,
    }
    if spec.get("aspect"):
        body["aspect_ratio"] = _aspect(int(task["width"]), int(task["height"]))
        body["resolution"] = "1080p" if max(int(task["width"]), int(task["height"])) > 1400 else "720p"
    field = spec.get("image_field")
    if field and images:
        urls = [_higgs_upload(image, headers) for _role, image in images[: spec["max_images"]]]
        body[field] = urls[0] if field == "image_url" else urls
    if task["mode"] == "mask_fill":
        body["prompt"] = task["prompt"] + " Change only the white area of the mask. Keep the rest."
    created = _json(f"https://api.higgsfield.ai/{task['model']}", body, headers, timeout=60)
    status_url = created.get("status_url") or f"https://api.higgsfield.ai/requests/{created.get('request_id')}/status"
    payload = _poll_higgs(status_url, headers)
    pictures = payload.get("images") or []
    if not pictures:
        raise AppError("Higgsfield returned no image", 502)
    return [_download(item["url"]) for item in pictures if item.get("url")]


def _higgs_upload(image: Image.Image, headers: dict) -> str:
    opened = _json(
        "https://api.higgsfield.ai/files/generate-upload-url",
        {"content_type": "image/jpeg"},
        headers,
        timeout=30,
    )
    upload_headers = dict(opened.get("upload_headers") or {"Content-Type": "image/jpeg"})
    data = _jpeg(image)
    request = Request(opened["upload_url"], data=data, headers=upload_headers, method="PUT")
    with _OPEN(request, timeout=120) as response:
        response.read()
    return opened["public_url"]


def _poll_higgs(url, headers) -> dict:
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        payload = _json(url, None, headers, timeout=60, method="GET")
        status = payload.get("status")
        if status in {"completed", "failed", "nsfw", "canceled"}:
            if status != "completed":
                raise AppError(f"Higgsfield {status}", 502)
            return payload
        time.sleep(3)
    raise AppError("Higgsfield timed out", 504)


def _aspect(width: int, height: int) -> str:
    ratio = width / max(height, 1)
    choices = {
        "1:1": 1, "4:3": 4 / 3, "3:4": 3 / 4, "3:2": 3 / 2, "2:3": 2 / 3,
        "16:9": 16 / 9, "9:16": 9 / 16,
    }
    return min(choices, key=lambda key: abs(choices[key] - ratio))


def _json(url, body, headers, *, timeout: float, method: str = "POST") -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with _OPEN(request, timeout=timeout) as response:
            raw = response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise AppError(f"Provider HTTP {exc.code}: {detail}", 502) from exc
    except (URLError, OSError) as exc:
        raise AppError(f"Provider unreachable: {exc}", 502) from exc
    try:
        return json.loads(raw or b"{}")
    except ValueError as exc:
        raise AppError("Provider returned a non-JSON body", 502) from exc


def _download(url: str) -> Image.Image:
    if url.startswith("data:") or ";base64," in url[:64]:
        payload = url.split(",", 1)[-1]
        return Image.open(io.BytesIO(base64.b64decode(payload))).convert("RGB")
    request = Request(url, method="GET")
    with _OPEN(request, timeout=120) as response:
        data = response.read()
    image = Image.open(io.BytesIO(data))
    image.load()
    return image.convert("RGB")


def _jpeg(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _jpeg_b64(image: Image.Image) -> str:
    return base64.b64encode(_jpeg(image)).decode("ascii")


def _fail(message: str):
    raise AppError(message, 502)
