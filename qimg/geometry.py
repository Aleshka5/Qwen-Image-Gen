"""Normalized boxes. The overlap test matches the editor's polygon check."""

from __future__ import annotations

import math
from dataclasses import dataclass

EPS = 0.004
MIN_SIDE = 0.04

LEVEL1_COLORS = (
    "#c47b2b", "#2f7d4a", "#6b4c9a", "#b86b3d", "#3d7a8c",
    "#a33d6b", "#7a8c3d", "#8c6b3d", "#5c6b8c", "#5c8c7a",
    "#8c3d5c", "#4a6b3d", "#b89a3d", "#3d8c6b", "#7a5c3d",
    "#8c4a3d", "#3d6b8c", "#9a6b4c", "#4c7a9a", "#6b8c4a",
)
PHOTO_COLOR = "#143d8f"
TEXT_COLOR = "#8c1a2b"


class GeomError(ValueError):
    pass


@dataclass(frozen=True)
class Box:
    x: float
    y: float
    w: float
    h: float
    kind: str = "rect"

    def as_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}


def clamp_box(box: Box, *, full: bool = False) -> Box:
    if full:
        return Box(0.0, 0.0, 1.0, 1.0, box.kind)
    w = min(1.0, max(MIN_SIDE, box.w))
    h = min(1.0, max(MIN_SIDE, box.h))
    x = min(max(box.x, 0.0), 1.0 - w)
    y = min(max(box.y, 0.0), 1.0 - h)
    return Box(round(x, 4), round(y, 4), round(w, 4), round(h, 4), box.kind)


def _rect(box: Box) -> list[tuple[float, float]]:
    return [
        (box.x, box.y),
        (box.x + box.w, box.y),
        (box.x + box.w, box.y + box.h),
        (box.x, box.y + box.h),
    ]


def _ellipse(box: Box, steps: int = 24) -> list[tuple[float, float]]:
    rx, ry = box.w / 2, box.h / 2
    cx, cy = box.x + rx, box.y + ry
    points = []
    for i in range(steps):
        t = (i / steps) * math.pi * 2
        points.append((cx + math.cos(t) * rx, cy + math.sin(t) * ry))
    return points


def _poly(box: Box) -> list[tuple[float, float]]:
    return _ellipse(box) if box.kind == "circle" else _rect(box)


def _project(poly, axis) -> tuple[float, float]:
    dots = [p[0] * axis[0] + p[1] * axis[1] for p in poly]
    return min(dots), max(dots)


def _separated(poly_a, poly_b) -> bool:
    for i, p in enumerate(poly_a):
        q = poly_a[(i + 1) % len(poly_a)]
        ex, ey = q[0] - p[0], q[1] - p[1]
        length = math.hypot(ex, ey) or 1.0
        axis = (-ey / length, ex / length)
        a0, a1 = _project(poly_a, axis)
        b0, b1 = _project(poly_b, axis)
        if a1 <= b0 + EPS or b1 <= a0 + EPS:
            return True
    return False


def overlaps(a: Box, b: Box) -> bool:
    pa, pb = _poly(a), _poly(b)
    return not _separated(pa, pb) and not _separated(pb, pa)


def hits_any(box: Box, others: list[Box]) -> bool:
    return any(overlaps(box, other) for other in others)


def absolute_box(shape: dict, by_id: dict[str, dict]) -> Box:
    """Map a shape into the project photo's 0..1 space."""
    box = Box(float(shape["x"]), float(shape["y"]), float(shape["w"]), float(shape["h"]), shape["kind"])
    parent_id = shape.get("parent_id")
    if not parent_id:
        return box
    parent = absolute_box(by_id[parent_id], by_id)
    return Box(
        parent.x + box.x * parent.w,
        parent.y + box.y * parent.h,
        box.w * parent.w,
        box.h * parent.h,
        box.kind,
    )
