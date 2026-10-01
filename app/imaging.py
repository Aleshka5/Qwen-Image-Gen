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


@dataclass(frozen=True, slots=True)
class AspectRatio:
    """Соотношение сторон. High — нативный кадр ~2K, Medium — те же пропорции около 1024²."""

    key: str
    width: int
    height: int

    def pixels(self, quality: str) -> tuple[int, int]:
        if quality == "high":
            return self.width, self.height
        if quality == "medium":
            return self.width // 2, self.height // 2
        raise ValidationError("Качество: high или medium")


# Нативные размеры Qwen-Image-2.1 (~2K). Medium — ровно половина, около 1024².
# Все стороны кратны 64, поэтому medium остаётся кратным 32.
ASPECT_RATIOS: tuple[AspectRatio, ...] = (
    AspectRatio("1:1", 2048, 2048),
    AspectRatio("4:3", 2400, 1792),
    AspectRatio("3:4", 1792, 2400),
    AspectRatio("3:2", 2528, 1696),
    AspectRatio("2:3", 1696, 2528),
    AspectRatio("16:9", 2752, 1536),
    AspectRatio("9:16", 1536, 2752),
)

# Свои ширина и высота на форме генерации. MIN_SIDE/MAX_SIDE — только подгонка фото кастомизации.
GEN_MIN_SIDE = 32
GEN_MAX_SIDE = 3000

_ASPECTS_BY_KEY = {ratio.key: ratio for ratio in ASPECT_RATIOS}


def _preset(quality: str, ratio: AspectRatio) -> ResolutionPreset:
    width, height = ratio.pixels(quality)
    return ResolutionPreset(f"{width}x{height}", f"{ratio.key} — {width}×{height}", width, height)


RESOLUTION_PRESETS: tuple[ResolutionPreset, ...] = tuple(
    _preset(quality, ratio) for quality in ("high", "medium") for ratio in ASPECT_RATIOS
)

_PRESETS_BY_KEY = {preset.key: preset for preset in RESOLUTION_PRESETS}
_SIZE_RE = re.compile(r"^\s*(\d{2,4})\s*[x×*]\s*(\d{2,4})\s*$")


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

    return _checked_sides(int(match.group(1)), int(match.group(2)))


def size_from_quality(quality: str | None, aspect: str | None) -> tuple[int, int]:
    """Кадр для пары «качество + соотношение» с формы генерации."""
    quality = (quality or "high").strip().lower()
    aspect_key = (aspect or "1:1").strip()
    ratio = _ASPECTS_BY_KEY.get(aspect_key)
    if ratio is None:
        known = ", ".join(item.key for item in ASPECT_RATIOS)
        raise ValidationError(f"Неизвестное соотношение {aspect_key!r}; ожидается {known}")
    return ratio.pixels(quality)


def parse_custom_size(width_raw: str | None, height_raw: str | None) -> tuple[int, int]:
    """Свои ширина и высота: 32…3000, затем кратность 32."""
    return _checked_sides(_parse_side(width_raw, "Ширина"), _parse_side(height_raw, "Высота"))


def _parse_side(raw: str | None, name: str) -> int:
    text = (raw or "").strip()
    if not text:
        raise ValidationError(f"{name}: укажите число пикселей")
    try:
        value = int(text)
    except ValueError as exc:
        raise ValidationError(f"{name}: ожидается целое число") from exc
    if not GEN_MIN_SIDE <= value <= GEN_MAX_SIDE:
        raise ValidationError(f"{name} {value} вне диапазона {GEN_MIN_SIDE}–{GEN_MAX_SIDE} px")
    return value


def _checked_sides(width: int, height: int) -> tuple[int, int]:
    for side, name in ((width, "Ширина"), (height, "Высота")):
        if not GEN_MIN_SIDE <= side <= GEN_MAX_SIDE:
            raise ValidationError(f"{name} {side} вне диапазона {GEN_MIN_SIDE}–{GEN_MAX_SIDE} px")
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
