"""Pair one photo, one painted mask, and ordered references for the condition list."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from PIL import Image
from werkzeug.datastructures import FileStorage

from .config import settings
from .imaging import PhotoFrame, ValidationError, _read_rgb, fit_model_size, load_uploads

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MaskFillFrame:
    """Model frame for a mask fill, plus the images kept for the archive.

    ``images`` is the pipeline order: fitted photo (RGB), fitted mask (RGB,
    white is the area to fill), then reference photos. ``archive`` is the save
    order: the photo and the mask at the uploaded size, then those references.
    ``width`` and ``height`` are the uploaded photo. ``restore`` returns a
    generated frame to that size.
    """

    width: int
    height: int
    model_width: int
    model_height: int
    images: tuple[Image.Image, ...]
    archive: tuple[Image.Image, ...]

    def restore(self, generated: Image.Image) -> Image.Image:
        frame = PhotoFrame(
            width=self.width,
            height=self.height,
            model_width=self.model_width,
            model_height=self.model_height,
            image=self.images[0],
        )
        return frame.restore(generated)


def prepare_mask_fill(
    main_files: list[FileStorage],
    mask_files: list[FileStorage],
    reference_files: list[FileStorage],
) -> MaskFillFrame:
    """Fit the photo and the mask, and append references in the given order.

    The mask must be the photo's pixel size and must contain something to fill.
    References stop at ``MAX_IMAGES - 2`` because the photo and the mask already
    take two condition slots.
    """
    main = _named(main_files)
    if len(main) != 1:
        raise ValidationError("Нужно ровно одно фото")
    masks = _named(mask_files)
    if len(masks) != 1:
        raise ValidationError("Нужна ровно одна маска")

    photo = _read_rgb(main[0], index=1)
    mask = _read_rgb(masks[0], index=1)
    if mask.size != photo.size:
        raise ValidationError(
            f"Маска {mask.width}×{mask.height} не совпадает с фото {photo.width}×{photo.height}"
        )
    if _all_black(mask):
        raise ValidationError("Маска полностью чёрная")

    references = _named(reference_files)
    limit = settings.max_images - 2
    if len(references) > limit:
        raise ValidationError(f"Можно передать не больше {limit} референсов")
    reference_images = tuple(load_uploads(references))

    model_width, model_height = fit_model_size(photo.width, photo.height)
    fitted_photo = _scaled(photo, model_width, model_height, Image.LANCZOS)
    fitted_mask = _scaled(mask, model_width, model_height, Image.NEAREST)
    log.info(
        "маска %s: фото %dx%d, модель %dx%d, референсов %d",
        main[0].filename,
        photo.width,
        photo.height,
        model_width,
        model_height,
        len(reference_images),
    )
    return MaskFillFrame(
        width=photo.width,
        height=photo.height,
        model_width=model_width,
        model_height=model_height,
        images=(fitted_photo, fitted_mask, *reference_images),
        archive=(photo, mask, *reference_images),
    )


def _named(files: list[FileStorage]) -> list[FileStorage]:
    return [item for item in files if item and item.filename]


def _all_black(image: Image.Image) -> bool:
    return all(channel[1] == 0 for channel in image.getextrema())


def _scaled(image: Image.Image, width: int, height: int, resample: int) -> Image.Image:
    if image.size == (width, height):
        return image
    return image.resize((width, height), resample)
