"""Crops, previews, circle masks, and the recursive render."""

from __future__ import annotations

import io

from PIL import Image, ImageDraw

from .geometry import Box, absolute_box

PREVIEW_SIDE = 2048


def png_bytes(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def load_png(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def preview(image: Image.Image, side: int = PREVIEW_SIDE) -> Image.Image:
    copy = image.copy()
    copy.thumbnail((side, side), Image.LANCZOS)
    return copy


def crop_box(image: Image.Image, box: Box) -> Image.Image:
    width, height = image.size
    left = int(round(box.x * width))
    top = int(round(box.y * height))
    right = int(round((box.x + box.w) * width))
    bottom = int(round((box.y + box.h) * height))
    right = min(width, max(right, left + 1))
    bottom = min(height, max(bottom, top + 1))
    left = max(0, min(left, right - 1))
    top = max(0, min(top, bottom - 1))
    return image.crop((left, top, right, bottom))


def circle_mask(image: Image.Image) -> Image.Image:
    """Hard clip. The model still saw the square; this throws the corners away."""
    rgba = image.convert("RGBA")
    mask = Image.new("L", rgba.size, 0)
    ImageDraw.Draw(mask).ellipse((0, 0, rgba.width - 1, rgba.height - 1), fill=255)
    rgba.putalpha(mask)
    return rgba


def _paste(base: Image.Image, overlay: Image.Image, box: Box) -> None:
    width, height = base.size
    left = int(round(box.x * width))
    top = int(round(box.y * height))
    right = int(round((box.x + box.w) * width))
    bottom = int(round((box.y + box.h) * height))
    tw, th = max(1, right - left), max(1, bottom - top)
    piece = overlay.convert("RGBA").resize((tw, th), Image.LANCZOS)
    base.alpha_composite(piece, (left, top))


def render_tree(
    source: Image.Image,
    shapes: list[dict],
    chosen: dict[str, Image.Image],
) -> Image.Image:
    """Composite selected variants onto the original photo.

    ``chosen`` maps a shape id to the image that should replace its crop.
    Shapes without an entry keep the original pixels. Children are painted
    inside their parent, then the parent is painted onto the project.
    """
    by_id = {shape["id"]: shape for shape in shapes}
    canvas = source.convert("RGBA")
    roots = [shape for shape in shapes if not shape.get("parent_id")]
    for shape in roots:
        part = _render_shape(source, shape, by_id, shapes, chosen)
        _paste(canvas, part, absolute_box(shape, by_id))
    return canvas


def _render_shape(source, shape, by_id, shapes, chosen) -> Image.Image:
    own = absolute_box(shape, by_id)
    children = [item for item in shapes if item.get("parent_id") == shape["id"]]
    if children:
        base = crop_box(source, own).convert("RGBA")
        for child in children:
            child_abs = absolute_box(child, by_id)
            relative = Box(
                (child_abs.x - own.x) / own.w if own.w else 0,
                (child_abs.y - own.y) / own.h if own.h else 0,
                child_abs.w / own.w if own.w else 1,
                child_abs.h / own.h if own.h else 1,
                child["kind"],
            )
            _paste(base, _render_shape(source, child, by_id, shapes, chosen), relative)
        if shape["kind"] == "circle":
            return circle_mask(base)
        return base
    if shape["id"] in chosen:
        image = chosen[shape["id"]]
    else:
        image = crop_box(source, own)
    if shape["kind"] == "circle":
        return circle_mask(image)
    return image.convert("RGBA")
