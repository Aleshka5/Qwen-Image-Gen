"""Geometry contract, identical to ``../Common/models/tests/test_geometry.py``.

The orchestrator resizes the condition image and the VAE stage encodes it; the
encoder stage tokenises the same pixels. All three have to agree on the grid, so
both repositories pin the same numbers. If one side changes, this test fails
here and the parity harness in Common/models fails end to end.
"""

from __future__ import annotations

import pytest
from PIL import Image

from app import geometry


@pytest.mark.parametrize(
    ("area", "ratio", "expected"),
    [
        (1024 * 1024, 1.0, (1024, 1024)),
        (1024 * 1024, 16 / 9, (1376, 768)),
        (1024 * 1024, 9 / 16, (768, 1376)),
    ],
)
def test_calculate_dimensions(area, ratio, expected):
    assert geometry.calculate_dimensions(area, ratio) == expected


def test_non_positive_ratio_is_rejected():
    with pytest.raises(geometry.GeometryError):
        geometry.calculate_dimensions(1024 * 1024, 0)


def test_condition_size_uses_the_source_aspect():
    assert geometry.condition_size(480, 640) == (896, 1184)


def test_condition_size_rejects_zero_side():
    with pytest.raises(geometry.GeometryError):
        geometry.condition_size(0, 100)


def test_align_floors_to_32():
    assert geometry.align_sides(1000, 700) == (992, 672)


def test_latent_grid():
    assert geometry.latent_grid(1024, 768) == (64, 48)
    assert geometry.latent_tokens(1024, 1024) == 4096


@pytest.mark.parametrize(
    ("sizes", "width", "height", "expected"),
    [
        ([], 1024, 1024, [[(1, 64, 64)]]),
        ([(896, 1184)], 512, 512, [[(1, 74, 56), (1, 32, 32)]]),
        ([(1024, 1024), (512, 1024)], 512, 512, [[(1, 64, 64), (1, 64, 32), (1, 32, 32)]]),
    ],
)
def test_img_shapes(sizes, width, height, expected):
    assert geometry.img_shapes(sizes, width, height) == expected


def test_resize_is_lanczos():
    source = Image.new("RGB", (480, 640), (10, 20, 30))
    resized = geometry.resize(source, 896, 1184)
    assert resized.size == (896, 1184)
    assert resized.tobytes() == source.resize((896, 1184), resample=Image.LANCZOS).tobytes()
