"""Image models the orchestrator can call. Video models are not offered."""

from __future__ import annotations

PHOTO = "#143d8f"
TEXT = "#8c1a2b"

CATALOG = (
    {
        "kind": "qwen",
        "label": "Local Qwen",
        "fields": [],
        "models": (
            {
                "id": "qwen-image-2.1",
                "label": "Qwen-Image 2.1",
                "modes": ("generate", "customize", "mask_fill"),
                "accepts_images": True,
                "max_images": 10,
            },
        ),
    },
    {
        "kind": "wan",
        "label": "Wan",
        "fields": ("api_key", "region", "workspace_id"),
        "models": (
            {
                "id": "wan2.6-t2i",
                "label": "Wan 2.6 text to image",
                "modes": ("generate",),
                "accepts_images": False,
                "max_images": 0,
                "protocol": "wan-t2i",
            },
            {
                "id": "wan2.5-t2i-preview",
                "label": "Wan 2.5 text to image",
                "modes": ("generate",),
                "accepts_images": False,
                "max_images": 0,
                "protocol": "wan-async",
            },
            {
                "id": "wan2.2-t2i-plus",
                "label": "Wan 2.2 Plus",
                "modes": ("generate",),
                "accepts_images": False,
                "max_images": 0,
                "protocol": "wan-async",
            },
            {
                "id": "wan2.2-t2i-flash",
                "label": "Wan 2.2 Flash",
                "modes": ("generate",),
                "accepts_images": False,
                "max_images": 0,
                "protocol": "wan-async",
            },
            {
                "id": "wan2.6-image",
                "label": "Wan 2.6 edit",
                "modes": ("customize", "mask_fill"),
                "accepts_images": True,
                "max_images": 4,
                "protocol": "wan-edit",
            },
        ),
    },
    {
        "kind": "higgsfield",
        "label": "Higgsfield",
        "fields": ("key_id", "key_secret"),
        "models": (
            {
                "id": "higgsfield-ai/soul/v2/standard",
                "label": "Soul 2",
                "modes": ("generate",),
                "accepts_images": False,
                "max_images": 0,
                "aspect": True,
            },
            {
                "id": "higgsfield-ai/soul/standard",
                "label": "Soul",
                "modes": ("generate",),
                "accepts_images": False,
                "max_images": 0,
                "aspect": True,
            },
            {
                "id": "higgsfield-ai/soul/cinema",
                "label": "Soul Cinema",
                "modes": ("generate",),
                "accepts_images": False,
                "max_images": 0,
                "aspect": True,
            },
            {
                "id": "xai/grok-imagine-image-2.0",
                "label": "Grok Image 2.0",
                "modes": ("generate", "customize", "mask_fill"),
                "accepts_images": True,
                "max_images": 10,
                "image_field": "image_urls",
            },
            {
                "id": "alibaba/qwen-image-3/text-to-image",
                "label": "Qwen Image 3",
                "modes": ("generate",),
                "accepts_images": False,
                "max_images": 0,
            },
            {
                "id": "alibaba/qwen-image-3/edit",
                "label": "Qwen Image 3 edit",
                "modes": ("customize", "mask_fill"),
                "accepts_images": True,
                "max_images": 3,
                "image_field": "image_urls",
            },
            {
                "id": "ideogram/v4.0",
                "label": "Ideogram 4.0",
                "modes": ("generate", "customize"),
                "accepts_images": True,
                "max_images": 1,
                "image_field": "image_url",
            },
            {
                "id": "recraft/v4.1/text-to-image",
                "label": "Recraft V4.1",
                "modes": ("generate",),
                "accepts_images": False,
                "max_images": 0,
            },
            {
                "id": "recraft/v4.1/pro/text-to-image",
                "label": "Recraft V4.1 Pro",
                "modes": ("generate",),
                "accepts_images": False,
                "max_images": 0,
            },
            {
                "id": "z-image/turbo",
                "label": "Z-Image Turbo",
                "modes": ("generate",),
                "accepts_images": False,
                "max_images": 0,
            },
            {
                "id": "marketing-studio/image/flare",
                "label": "Marketing Studio Flare",
                "modes": ("generate",),
                "accepts_images": False,
                "max_images": 0,
            },
        ),
    },
)

_BY_KIND = {entry["kind"]: entry for entry in CATALOG}


def public_catalog() -> dict:
    providers = []
    for entry in CATALOG:
        providers.append({
            "kind": entry["kind"],
            "label": entry["label"],
            "fields": list(entry["fields"]),
            "models": [
                {
                    "id": model["id"],
                    "label": model["label"],
                    "modes": list(model["modes"]),
                    "accepts_images": model["accepts_images"],
                }
                for model in entry["models"]
            ],
        })
    return {"providers": providers}


def model_ids(kind: str) -> list[str]:
    entry = _BY_KIND.get(kind)
    if entry is None:
        return []
    return [model["id"] for model in entry["models"]]


def model_spec(kind: str, model_id: str) -> dict | None:
    entry = _BY_KIND.get(kind)
    if entry is None:
        return None
    for model in entry["models"]:
        if model["id"] == model_id:
            return model
    return None


WAN_REGIONS = {
    "singapore": "ap-southeast-1",
    "virginia": "us-east-1",
    "beijing": "cn-beijing",
    "frankfurt": "eu-central-1",
}
