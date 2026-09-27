"""Работа с изображениями: пресеты разрешений, валидация загрузок, сохранение."""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.datastructures import FileStorage

from .config import settings

log = logging.getLogger(__name__)

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "BMP"}
_ALIGN = 32  # VAE 2.1 сжимает в 16 раз, пайплайн требует кратность vae_scale_factor * 2


@dataclass(frozen=True, slots=True)
class ResolutionPreset:
    key: str
    label: str
    width: int
    height: int


# Нативные размеры Qwen-Image-2.1. Все стороны кратны 32.
# 1024 — запас по VRAM на V100, когда 2K с несколькими референсами не влезает.
RESOLUTION_PRESETS: tuple[ResolutionPreset, ...] = (
    ResolutionPreset("2048x2048", "1:1 — 2048×2048", 2048, 2048),
    ResolutionPreset("2400x1792", "4:3 — 2400×1792", 2400, 1792),
    ResolutionPreset("1792x2400", "3:4 — 1792×2400", 1792, 2400),
    ResolutionPreset("2528x1696", "3:2 — 2528×1696", 2528, 1696),
    ResolutionPreset("1696x2528", "2:3 — 1696×2528", 1696, 2528),
    ResolutionPreset("2752x1536", "16:9 — 2752×1536", 2752, 1536),
    ResolutionPreset("1536x2752", "9:16 — 1536×2752", 1536, 2752),
    ResolutionPreset("1024x1024", "1:1 — 1024×1024 (экономно)", 1024, 1024),
)

_PRESETS_BY_KEY = {preset.key: preset for preset in RESOLUTION_PRESETS}
_SIZE_RE = re.compile(r"^\s*(\d{3,5})\s*[x×*]\s*(\d{3,5})\s*$")


class ValidationError(ValueError):
    """Некорректный пользовательский ввод."""


def align(value: int) -> int:
    return max(_ALIGN, (value // _ALIGN) * _ALIGN)


def parse_resolution(raw: str | None) -> tuple[int, int]:
    """Принимает ключ пресета или произвольное ``ШxВ``."""
    raw = (raw or "").strip()
    if not raw:
        preset = RESOLUTION_PRESETS[0]
        return preset.width, preset.height

    if raw in _PRESETS_BY_KEY:
        preset = _PRESETS_BY_KEY[raw]
        return preset.width, preset.height

    match = _SIZE_RE.match(raw)
    if not match:
        raise ValidationError(f"Не понимаю разрешение {raw!r}; ожидается «ширинаxвысота»")

    width, height = int(match.group(1)), int(match.group(2))
    for side, name in ((width, "ширина"), (height, "высота")):
        if not settings.min_side <= side <= settings.max_side:
            raise ValidationError(
                f"{name.capitalize()} {side} вне диапазона "
                f"{settings.min_side}–{settings.max_side} px"
            )
    # стороны выравниваем уже после проверки, чтобы в ошибке было исходное число
    return align(width), align(height)


def _read_rgb(storage: FileStorage, *, index: int) -> Image.Image:
    """Декодирует загрузку в RGB с учётом EXIF. Размер здесь не меняется."""
    try:
        image = Image.open(storage.stream)
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValidationError(f"Файл #{index} ({storage.filename}) не читается как изображение") from exc

    if image.format and image.format.upper() not in ALLOWED_FORMATS:
        raise ValidationError(
            f"Файл #{index}: формат {image.format} не поддерживается "
            f"({', '.join(sorted(ALLOWED_FORMATS))})"
        )

    image = ImageOps.exif_transpose(image).convert("RGB")
    if image.width < 1 or image.height < 1:
        raise ValidationError(f"Файл #{index}: у фото нулевая сторона")
    return image


def load_uploads(files: list[FileStorage]) -> list[Image.Image]:
    """Читает загруженные файлы в RGB и ужимает длинную сторону до INPUT_MAX_SIDE.

    Это только потолок на размер кадра в RAM. Пайплайн затем сам приводит каждый
    референс к площади 1024² — от неё, а не от INPUT_MAX_SIDE, зависит длина KV-кэша.
    """
    files = [f for f in files if f and f.filename]
    if len(files) > settings.max_images:
        raise ValidationError(f"Можно передать не больше {settings.max_images} изображений")

    images: list[Image.Image] = []
    for index, storage in enumerate(files, start=1):
        image = _read_rgb(storage, index=index)
        image.thumbnail((settings.input_max_side, settings.input_max_side), Image.LANCZOS)
        log.info(
            "вход image%d: %s %dx%d %s",
            index, storage.filename, image.width, image.height, image.mode,
        )
        images.append(image)

    return images


@dataclass(frozen=True, slots=True)
class PhotoFrame:
    """Исходный кадр и его копия для пайплайна.

    Подгонка под кратность 32 и лимиты сторон, и обратный возврат к исходному
    HxW, живут только здесь. Снаружи видны исходные ширина и высота.
    """

    width: int
    height: int
    model_width: int
    model_height: int
    image: Image.Image

    def restore(self, generated: Image.Image) -> Image.Image:
        """Возвращает результат к исходному HxW. Повторный вызов размера не меняет."""
        target = (self.width, self.height)
        if generated.size == target:
            return generated
        return generated.resize(target, Image.LANCZOS)


def fit_model_size(width: int, height: int) -> tuple[int, int]:
    """Стороны для пайплайна: пропорции фото и кратность 32.

    Длинная сторона не больше MAX_SIDE. Короткая поднимается до MIN_SIDE, если
    это не выталкивает длинную за MAX_SIDE.
    """
    if width < 1 or height < 1:
        raise ValidationError("У фото нулевая сторона")

    long_side = max(width, height)
    short_side = min(width, height)
    scale = 1.0
    if long_side > settings.max_side:
        scale = settings.max_side / long_side
    if short_side * scale < settings.min_side:
        scale = settings.min_side / short_side
    if long_side * scale > settings.max_side:
        scale = settings.max_side / long_side

    return _snap(width * scale), _snap(height * scale)


def _snap(value: float) -> int:
    high = align(settings.max_side)
    snapped = int(round(value / _ALIGN)) * _ALIGN
    if snapped < _ALIGN:
        snapped = _ALIGN
    if snapped > high:
        snapped = high
    return snapped


def prepare_photo(files: list[FileStorage]) -> PhotoFrame:
    """Одно референсное фото: исходный HxW и кадр, который можно отдать пайплайну."""
    files = [f for f in files if f and f.filename]
    if len(files) != 1:
        raise ValidationError("Нужно ровно одно референсное фото")

    source = _read_rgb(files[0], index=1)
    model_width, model_height = fit_model_size(source.width, source.height)
    model = source
    if source.size != (model_width, model_height):
        model = source.resize((model_width, model_height), Image.LANCZOS)
    log.info(
        "кастомизация %s: фото %dx%d, модель %dx%d",
        files[0].filename,
        source.width,
        source.height,
        model_width,
        model_height,
    )
    return PhotoFrame(
        width=source.width,
        height=source.height,
        model_width=model_width,
        model_height=model_height,
        image=model,
    )


def save_outputs(images: list[Image.Image]) -> list[str]:
    """Сохраняет результат в OUTPUT_DIR и возвращает имена файлов."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    names: list[str] = []
    for image in images:
        name = f"{stamp}-{uuid.uuid4().hex[:8]}.png"
        image.save(settings.output_dir / name, format="PNG")
        names.append(name)
    _prune_outputs()
    return names


def _prune_outputs() -> None:
    """Держим в OUTPUT_DIR не больше KEEP_OUTPUTS последних файлов."""
    if settings.keep_outputs <= 0:
        return
    files = sorted(
        (p for p in settings.output_dir.glob("*.png") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for stale in files[settings.keep_outputs:]:
        try:
            stale.unlink()
        except OSError as exc:  # pragma: no cover - гонка с другим процессом
            log.warning("Не удалось удалить %s: %s", stale, exc)


def output_path(name: str) -> Path:
    """Безопасно разрешает имя файла внутри OUTPUT_DIR."""
    candidate = (settings.output_dir / name).resolve()
    if candidate.parent != settings.output_dir.resolve():
        raise ValidationError("Некорректное имя файла")
    return candidate
