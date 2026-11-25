from io import BytesIO
from struct import error as StructError, unpack
from typing import BinaryIO
from zlib import decompressobj, error as ZipError

from PIL import Image


THUMBNAIL_EDGE = 96
MAX_PLANE_BYTES = 32 * 1024 * 1024


def decode_plane(data: bytes, width: int, height: int, version: int, depth: int) -> Image.Image:
    row_width = width * (depth // 8)
    size = row_width * height
    compression, = unpack(">H", data[:2])
    if compression == 1:
        # The row-length table precedes the contiguous PackBits rows.
        offset = 2 + height * (4 if version == 2 else 2)
        plane = Image.frombytes("L", (row_width, height), data[offset:], "packbits", "L")
        pixels = plane.tobytes() if depth == 16 else None
    elif compression == 2:
        inflater = decompressobj()
        pixels = inflater.decompress(data[2:], size + 1)
        if len(pixels) != size or not inflater.eof:
            raise ValueError("Truncated Photoshop layer channel")
    elif compression == 0:
        pixels = data[2:]
        if len(pixels) != size:
            raise ValueError("Truncated Photoshop layer channel")
    else:
        raise ValueError("Unsupported Photoshop layer compression")
    if pixels is not None:
        plane = Image.frombytes("L", (width, height), pixels[::2] if depth == 16 else pixels)
    plane.thumbnail((THUMBNAIL_EDGE, THUMBNAIL_EDGE), Image.Resampling.LANCZOS)
    return plane


def read_layer_thumbnail(stream: BinaryIO, channels: tuple[tuple[int, int], ...], width: int, height: int,
                         version: int, depth: int, color_mode: int) -> bytes | None:
    color_channels = (0, 1, 2) if color_mode == 3 else (0,)
    if depth not in (8, 16) or color_mode not in (1, 3) or not (width > 0 and height > 0):
        return None
    if width * height * (depth // 8) > MAX_PLANE_BYTES:
        return None

    planes = {}
    try:
        for channel, size in channels:
            if channel in color_channels or channel == -1:
                if not 2 <= size <= MAX_PLANE_BYTES:
                    return None
                planes[channel] = decode_plane(stream.read(size), width, height, version, depth)
            else:
                stream.seek(size, 1)
        if not all(channel in planes for channel in color_channels):
            return None
        image = Image.merge("RGB", tuple(planes[channel] for channel in color_channels)) if color_mode == 3 else planes[0]
        if -1 in planes:
            image.putalpha(planes[-1])
        output = BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()
    except (OSError, ValueError, StructError, ZipError):
        return None
