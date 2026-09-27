"""Подгонка кадра под модель и возврат к исходному HxW живут в PhotoFrame."""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image
from werkzeug.datastructures import FileStorage

from app.config import settings
from app.imaging import ValidationError, fit_model_size, prepare_photo


def _file(width: int, height: int, name: str = "photo.png") -> FileStorage:
    buf = BytesIO()
    Image.new("RGB", (width, height), (1, 2, 3)).save(buf, format="PNG")
    buf.seek(0)
    return FileStorage(stream=buf, filename=name, content_type="image/png")


def test_fit_keeps_aspect_on_the_model_grid():
    width, height = fit_model_size(1000, 750)
    assert width % 32 == 0 and height % 32 == 0
    assert settings.min_side <= width <= settings.max_side
    assert settings.min_side <= height <= settings.max_side
    assert abs(width / height - 1000 / 750) < 0.05


def test_fit_caps_the_long_side_and_lifts_a_tiny_photo():
    wide, tall = fit_model_size(4000, 3000)
    assert max(wide, tall) <= settings.max_side
    assert wide % 32 == 0 and tall % 32 == 0

    small_w, small_h = fit_model_size(40, 30)
    assert min(small_w, small_h) >= settings.min_side
    assert max(small_w, small_h) <= settings.max_side


def test_prepare_photo_restores_the_original_frame():
    frame = prepare_photo([_file(1000, 750)])
    assert (frame.width, frame.height) == (1000, 750)
    assert frame.image.size == (frame.model_width, frame.model_height)
    assert frame.image.size != (1000, 750)

    generated = Image.new("RGB", frame.image.size, (9, 9, 9))
    restored = frame.restore(generated)
    assert restored.size == (1000, 750)
    assert frame.restore(restored).size == (1000, 750)


@pytest.mark.parametrize("files", [[], [_file(32, 32), _file(32, 32, "b.png")]])
def test_prepare_photo_wants_exactly_one_file(files):
    with pytest.raises(ValidationError, match="одно"):
        prepare_photo(files)
