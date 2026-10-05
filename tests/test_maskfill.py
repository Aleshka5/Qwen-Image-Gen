"""Mask pairing. ValidationError is what the app turns into HTTP 400."""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image
from werkzeug.datastructures import FileStorage

from app.config import settings
from app.imaging import ValidationError
from app.maskfill import prepare_mask_fill


def _png(image: Image.Image, name: str) -> FileStorage:
    buf = BytesIO()
    image.save(buf, format="PNG")
    buf.seek(0)
    return FileStorage(stream=buf, filename=name, content_type="image/png")


def _solid(width: int, height: int, color: tuple[int, int, int], name: str) -> FileStorage:
    return _png(Image.new("RGB", (width, height), color), name)


def _pattern(width: int, height: int) -> Image.Image:
    image = Image.new("RGB", (width, height))
    for y in range(height):
        for x in range(width):
            image.putpixel((x, y), ((x * 3) % 256, (y * 5) % 256, 40))
    return image


def _split_mask(width: int, height: int) -> Image.Image:
    image = Image.new("RGB", (width, height), (0, 0, 0))
    for y in range(height):
        for x in range(width // 2, width):
            image.putpixel((x, y), (255, 255, 255))
    return image


def test_prepare_pairs_fitted_photo_nearest_mask_and_references_in_order():
    photo = _pattern(64, 48)
    mask = _split_mask(64, 48)
    frame = prepare_mask_fill(
        [_png(photo, "photo.png")],
        [_png(mask, "mask.png")],
        [
            _solid(12, 10, (4, 5, 6), "ref-a.png"),
            _solid(8, 8, (7, 8, 9), "ref-b.png"),
        ],
    )

    assert (frame.width, frame.height) == (64, 48)
    assert (frame.model_width, frame.model_height) == frame.images[0].size
    assert frame.images[0].size != (64, 48)
    assert frame.images[0].mode == "RGB"
    assert frame.images[0].tobytes() == photo.resize(frame.images[0].size, Image.LANCZOS).tobytes()

    fitted_mask = frame.images[1]
    assert fitted_mask.size == frame.images[0].size
    assert fitted_mask.mode == "RGB"
    assert fitted_mask.tobytes() == mask.resize(fitted_mask.size, Image.NEAREST).tobytes()
    assert fitted_mask.tobytes() != mask.resize(fitted_mask.size, Image.LANCZOS).tobytes()
    assert fitted_mask.getpixel((fitted_mask.width - 1, fitted_mask.height // 2)) == (255, 255, 255)
    assert fitted_mask.getpixel((0, 0)) == (0, 0, 0)

    assert frame.images[2].getpixel((0, 0)) == (4, 5, 6)
    assert frame.images[2].size == (12, 10)
    assert frame.images[3].getpixel((0, 0)) == (7, 8, 9)
    assert len(frame.images) == 4

    assert frame.archive[0].size == (64, 48)
    assert frame.archive[1].size == (64, 48)
    assert frame.archive[2] is frame.images[2]
    assert frame.archive[3] is frame.images[3]


def test_mask_pixel_size_must_match_the_photo():
    # Same aspect, so a long-side thumbnail would hide the mismatch.
    photo = _solid(2000, 1000, (1, 2, 3), "photo.png")
    mask = _png(_split_mask(1800, 900), "mask.png")
    with pytest.raises(ValidationError, match="не совпадает"):
        prepare_mask_fill([photo], [mask], [])


def test_all_black_mask_is_rejected():
    photo = _solid(32, 32, (9, 9, 9), "photo.png")
    mask = _solid(32, 32, (0, 0, 0), "mask.png")
    with pytest.raises(ValidationError, match="чёрн"):
        prepare_mask_fill([photo], [mask], [])


def test_reference_count_stops_at_two_slots_below_the_cap():
    photo = _solid(32, 32, (1, 1, 1), "photo.png")
    mask = _solid(32, 32, (255, 255, 255), "mask.png")
    limit = settings.max_images - 2
    accepted = [
        _solid(8, 8, (index, 0, 0), f"ref-{index}.png") for index in range(limit)
    ]
    frame = prepare_mask_fill([photo], [mask], accepted)
    assert len(frame.images) == limit + 2

    extra = accepted + [_solid(8, 8, (1, 2, 3), "ref-extra.png")]
    with pytest.raises(ValidationError, match=str(limit)):
        prepare_mask_fill(
            [_solid(32, 32, (1, 1, 1), "photo.png")],
            [_solid(32, 32, (255, 255, 255), "mask.png")],
            extra,
        )


def test_restore_returns_the_photo_size():
    frame = prepare_mask_fill(
        [_solid(1000, 750, (1, 2, 3), "photo.png")],
        [_png(_split_mask(1000, 750), "mask.png")],
        [],
    )
    assert (frame.width, frame.height) == (1000, 750)
    assert frame.images[0].size == (frame.model_width, frame.model_height)
    assert frame.images[0].size != (1000, 750)

    generated = Image.new("RGB", frame.images[0].size, (9, 9, 9))
    restored = frame.restore(generated)
    assert restored.size == (1000, 750)
    assert frame.restore(restored).size == (1000, 750)
