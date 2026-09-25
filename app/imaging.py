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
_ALIGN = 16  # сторона кратна 16 — требование VAE/патчификатора


@dataclass(frozen=True, slots=True)
class ResolutionPreset:
    key: str
    label: str
    width: int
    height: int


RESOLUTION_PRESETS: tuple[ResolutionPreset, ...] = (
    ResolutionPreset("1328x1328", "1:1 — 1328×1328", 1328, 1328),
    ResolutionPreset("1664x928", "16:9 — 1664×928", 1664, 928),
    ResolutionPreset("928x1664", "9:16 — 928×1664", 928, 1664),
    ResolutionPreset("1472x1140", "4:3 — 1472×1140", 1472, 1140),
    ResolutionPreset("1140x1472", "3:4 — 1140×1472", 1140, 1472),
    ResolutionPreset("1024x1024", "1:1 — 1024×1024 (экономно)", 1024, 1024),
    ResolutionPreset("1280x720", "16:9 — 1280×720 (экономно)", 1280, 720),
    ResolutionPreset("720x1280", "9:16 — 720×1280 (экономно)", 720, 1280),
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


def load_uploads(files: list[FileStorage]) -> list[Image.Image]:
    """Читает загруженные файлы в RGB и ужимает их до INPUT_MAX_SIDE."""
    files = [f for f in files if f and f.filename]
    if len(files) > settings.max_images:
        raise ValidationError(f"Можно передать не больше {settings.max_images} изображений")

    images: list[Image.Image] = []
    for index, storage in enumerate(files, start=1):
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
        image.thumbnail((settings.input_max_side, settings.input_max_side), Image.LANCZOS)
        images.append(image)

    return images


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
