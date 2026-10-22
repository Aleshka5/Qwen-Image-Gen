"""Geometry of one generation: condition resize, latent grid, ``img_shapes``.

This is the orchestrator's copy of ``shared/qwensplit/geometry.py`` from
``../Common/models`` (the repository that owns the three inference services).
The two are the same arithmetic and both are covered by a contract test with
the same expected values — ``tests/test_geometry_contract.py`` here and
``tests/test_geometry.py`` there. If they ever drift, ``img_shapes`` stops
matching the encoder's token grid and ``scripts/parity.sh`` in Common/models
fails, because the reference pipeline keeps producing the old geometry.

The numbers here are the pipeline's own, not approximations:

* ``calculate_dimensions`` is ``diffusers.pipelines.qwenimage.pipeline_qwenimage21``;
  condition images are resized to an area of ``output_resolution**2`` and the
  sides rounded to a multiple of 32.
* ``resize`` reproduces ``VaeImageProcessor.resize`` for PIL input
  (``resample=lanczos``, ``reducing_gap=2.0``).  One resize feeds both the text
  encoder and the VAE, so it has to happen once, in the orchestrator.
* the VAE compresses 16x spatially and the transformer consumes latents
  unpatched, so one token is a 16x16 pixel tile and a 2x2 group of tokens is one
  ``img_mask`` slot.
"""

from __future__ import annotations

import math

from PIL import Image

VAE_SCALE_FACTOR = 16
# The pipeline requires both sides to be a multiple of vae_scale_factor * 2.
ALIGN = VAE_SCALE_FACTOR * 2

DEFAULT_OUTPUT_RESOLUTION = 1024


class GeometryError(ValueError):
    """The request cannot be expressed on the pipeline's latent grid."""


def calculate_dimensions(target_area: float, ratio: float) -> tuple[int, int]:
    """Width and height for a target area and an aspect ratio, rounded to 32."""
    if ratio <= 0 or not math.isfinite(ratio):
        raise GeometryError(f"aspect ratio must be positive, got {ratio!r}")
    width = math.sqrt(target_area * ratio)
    height = width / ratio
    return round(width / ALIGN) * ALIGN, round(height / ALIGN) * ALIGN


def condition_size(width: int, height: int, output_resolution: int = DEFAULT_OUTPUT_RESOLUTION) -> tuple[int, int]:
    """The size a condition image is resized to before the encoder and the VAE see it."""
    if width < 1 or height < 1:
        raise GeometryError(f"image has a zero side: {width}x{height}")
    return calculate_dimensions(output_resolution * output_resolution, width / height)


def resize(image: Image.Image, width: int, height: int) -> Image.Image:
    """``VaeImageProcessor.resize`` for PIL.

    The processor is built as ``VaeImageProcessor(vae_scale_factor=16,
    vae_latent_channels=64)``, so the resample filter is lanczos and
    ``reducing_gap`` is left at its default ``None``.
    """
    return image.resize((width, height), resample=Image.LANCZOS)


def align_sides(width: int, height: int) -> tuple[int, int]:
    """The pipeline floors both sides to a multiple of 32 before the loop."""
    return width // ALIGN * ALIGN, height // ALIGN * ALIGN


def latent_grid(width: int, height: int) -> tuple[int, int]:
    """Latent columns and rows for a pixel size. Returns ``(cols, rows)``."""
    return width // VAE_SCALE_FACTOR, height // VAE_SCALE_FACTOR


def latent_tokens(width: int, height: int) -> int:
    """Packed target-token count: one token per 16x16 pixel tile."""
    cols, rows = latent_grid(width, height)
    return cols * rows


def img_shapes(condition_sizes: list[tuple[int, int]], width: int, height: int) -> list[list[tuple[int, int, int]]]:
    """``img_shapes`` for batch 1: one ``(frame, rows, cols)`` per image, target last.

    ``condition_sizes`` are ``(width, height)`` pairs, the order the pipeline
    builds them in (``for vae_width, vae_height in input_image_sizes``).
    """
    shapes: list[tuple[int, int, int]] = []
    for cond_width, cond_height in condition_sizes:
        cols, rows = latent_grid(cond_width, cond_height)
        shapes.append((1, rows, cols))
    cols, rows = latent_grid(width, height)
    shapes.append((1, rows, cols))
    return [shapes]
