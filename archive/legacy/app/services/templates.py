import json
import logging
import os
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from struct import error as StructError, unpack
from typing import BinaryIO

from PIL import Image

from app.core.preview_models import DEFAULT_PREVIEW_MODEL
from app.schemas.template import TemplateConfiguration, TemplateSlots
from app.services.template_layers import ReplacementLayer, read_replacement_layers

logger = logging.getLogger(__name__)
VERSIONS = {".psd": 1, ".psb": 2}
THUMBNAIL_SIZE = (320, 320)
THUMBNAIL_RESOURCE_ID = 1036
MAX_THUMBNAIL_BYTES = 4 * 1024 * 1024
SIDECAR_FORMATS = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


@dataclass(frozen=True)
class TemplateRecord:
    id: str
    path: Path
    width: int
    height: int
    size_bytes: int
    thumbnail: bytes | None
    replacement_layers: tuple[ReplacementLayer, ...] = ()
    thumbnail_media_type: str = "image/jpeg"
    thumbnail_pending: bool = False
    revision: str = ""
    slots: TemplateSlots | None = None
    configured_layers: tuple[ReplacementLayer, ...] = ()
    preview_model: str = DEFAULT_PREVIEW_MODEL

    @property
    def available_layers(self) -> tuple[ReplacementLayer, ...]:
        layers = {layer.id: layer for layer in self.replacement_layers}
        layers.update((layer.id, layer) for layer in self.configured_layers)
        return tuple(layers.values())


def template_revision(stat: os.stat_result) -> str:
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def configuration_path(path: Path) -> Path:
    return path.with_name(path.name + ".mockup.json")


def read_configuration(path: Path, revision: str) -> tuple[TemplateConfiguration | None, tuple[ReplacementLayer, ...]]:
    config = configuration_path(path)
    try:
        if config.is_symlink():
            return None, ()
        with config.open(encoding="utf-8") as stream:
            saved = json.load(stream)
        if saved["revision"] == revision:
            settings = TemplateConfiguration.model_validate(saved)
            layers = tuple(ReplacementLayer(item["id"], item["name"], item["path"],
                                            tuple(tuple(bounds) for bounds in item["bounds"]))
                           for item in saved["layers"])
            return settings, layers
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError, KeyError) as error:
        logger.warning("Could not read template configuration for %s: %s", path, error)
    return None, ()


def read_sidecar(template: Path) -> tuple[bytes, str] | None:
    for suffix, media_type in SIDECAR_FORMATS.items():
        candidate = template.with_suffix(suffix)
        try:
            if candidate.is_symlink() or not candidate.is_file():
                continue
            with candidate.open("rb") as stream:
                data = stream.read(32 * 1024 * 1024 + 1)
            if len(data) > 32 * 1024 * 1024:
                raise ValueError("Preview exceeds 32 MB")
            image_format = "PNG" if suffix == ".png" else "JPEG"
            with Image.open(BytesIO(data), formats=[image_format]) as image:
                image.verify()
            return data, media_type
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as error:
            logger.warning("Could not use template preview %s: %s", candidate, error)
    return None


def load_templates(directory: Path) -> dict[str, TemplateRecord]:
    directory = directory.resolve()
    templates: dict[str, TemplateRecord] = {}
    if not directory.exists():
        logger.info("Photoshop template directory does not exist: %s", directory)
        return templates

    for path in sorted(directory.rglob("*"), key=lambda path: (str(path).casefold(), str(path))):
        if path.suffix.lower() not in VERSIONS:
            continue
        try:
            if path.is_file() and path.resolve().is_relative_to(directory):
                template = read_template(path, path.relative_to(directory))
                templates[template.id] = template
        except (OSError, RuntimeError, ValueError, StructError) as error:
            logger.warning("Skipping unreadable Photoshop template %s: %s", path, error)
    logger.info("Loaded %d Photoshop templates from %s", len(templates), directory)
    return templates


def read_template(path: Path, relative_path: Path) -> TemplateRecord:
    with path.open("rb") as stream:
        signature, version, height, width, depth, color_mode = unpack(">4sH8xIIHH", stream.read(26))
        if signature != b"8BPS" or version != VERSIONS[relative_path.suffix.lower()] or not (width and height):
            raise ValueError("Invalid Photoshop document header")
        stat = os.fstat(stream.fileno())
        size = stat.st_size
        if size < (44 if version == 2 else 40):
            raise ValueError("Truncated Photoshop document")
        try:
            layers = read_replacement_layers(stream, version, size, depth, color_mode)
        except (OSError, ValueError, StructError) as error:
            logger.warning("Could not read replacement layers in %s: %s", path, error)
            layers = ()
        sidecar = read_sidecar(path.with_name(relative_path.name))
        if sidecar is None:
            stream.seek(26)
            thumbnail = read_thumbnail(stream, version, size, path)
            media_type = "image/jpeg"
        else:
            thumbnail, media_type = sidecar
    identifier = sha256(relative_path.as_posix().encode("utf-8")).hexdigest()
    revision = template_revision(stat)
    configuration, configured_layers = read_configuration(path, revision)
    originals = {layer.id: layer for layer in layers}
    configured_layers = tuple(originals.get(layer.id, layer) for layer in configured_layers)
    return TemplateRecord(identifier, relative_path, width, height, size, thumbnail, layers,
                          media_type, revision=revision, slots=configuration.slots if configuration else None,
                          configured_layers=configured_layers,
                          preview_model=configuration.preview_model if configuration else DEFAULT_PREVIEW_MODEL)


def read_thumbnail(stream: BinaryIO, version: int, size: int, path: Path) -> bytes | None:
    try:
        thumbnail = embedded_thumbnail(stream, size)
        if thumbnail is not None:
            return thumbnail
        if version == 1:
            stream.seek(0)
            return merged_thumbnail(stream)
    except (OSError, ValueError, SyntaxError, StructError, Image.DecompressionBombError) as error:
        logger.warning("Could not generate a thumbnail for %s: %s", path, error)
        return None
    logger.warning("No supported preview for Photoshop template %s", path)
    return None


def embedded_thumbnail(stream: BinaryIO, file_size: int) -> bytes | None:
    # The color mode and image resources sections have 32-bit lengths in PSD and PSB.
    color_mode_size, = unpack(">I", stream.read(4))
    stream.seek(color_mode_size, os.SEEK_CUR)
    resource_size, = unpack(">I", stream.read(4))
    end = stream.tell() + resource_size
    if end > file_size:
        raise ValueError("Truncated image resources section")

    while end - stream.tell() >= 12:
        _, resource_id, name_size = unpack(">4sHB", stream.read(7))
        stream.seek(name_size + ((name_size + 1) % 2), os.SEEK_CUR)
        size, = unpack(">I", stream.read(4))
        start = stream.tell()
        next_resource = start + size + (size % 2)
        if next_resource > end:
            raise ValueError("Truncated image resource")
        if resource_id == THUMBNAIL_RESOURCE_ID and 28 < size <= MAX_THUMBNAIL_BYTES:
            encoding, _, _, _, _, jpeg_size = unpack(">6I4x", stream.read(28))
            if encoding == 1 and 0 < jpeg_size <= size - 28:
                jpeg = stream.read(jpeg_size)
                with Image.open(BytesIO(jpeg), formats=["JPEG"]) as preview:
                    if preview.width <= THUMBNAIL_SIZE[0] and preview.height <= THUMBNAIL_SIZE[1]:
                        preview.load()
                        return jpeg
        stream.seek(next_resource)
    return None


def merged_thumbnail(stream: BinaryIO) -> bytes | None:
    with Image.open(stream, formats=["PSD"]) as image:
        # The version information resource records whether the merged image was saved.
        for resource_id, _, payload in image.resources:
            if resource_id == 1057 and payload[4:5] == b"\x00":
                return None
        image.thumbnail(THUMBNAIL_SIZE, Image.Resampling.LANCZOS)
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, "white")
        background.paste(rgba, mask=rgba.getchannel("A"))
        output = BytesIO()
        background.save(output, format="JPEG", quality=85)
        return output.getvalue()
