from dataclasses import dataclass
from struct import unpack
from typing import BinaryIO, Iterator


SMART_OBJECT_TAGS = {b"SoLd", b"SoLE", b"PlLd", b"plLd"}
PSB_LONG_TAGS = {
    b"LMsk", b"Lr16", b"Lr32", b"Layr", b"Mt16", b"Mt32", b"Mtrn", b"Alph",
    b"FMsk", b"lnk2", b"FEid", b"FXid", b"FELS", b"PxSD", b"lnk3", b"lnkE", b"pths",
    b"extd", b"extn", b"cinf", b"artd",
}
Bounds = tuple[int, int, int, int]


@dataclass(frozen=True)
class ReplacementLayer:
    id: int
    name: str
    path: str
    bounds: tuple[Bounds, ...] = ()


@dataclass(frozen=True)
class LayerRecord:
    id: int
    name: str
    divider: int
    visible: bool
    opacity: int
    smart: bool
    source: str | None
    bounds: Bounds | None = None


def layer_bounds(stream: BinaryIO, end: int, bounds: Bounds) -> Bounds | None:
    mask_end = section_end(stream, end)
    top, left, bottom, right = bounds
    if mask_end - stream.tell() >= 18:
        mask_top, mask_left, mask_bottom, mask_right, default, flags = unpack(">4i2B", stream.read(18))
        if default == 0 and not flags & 6:
            if flags & 1:
                mask_top, mask_bottom = mask_top + top, mask_bottom + top
                mask_left, mask_right = mask_left + left, mask_right + left
            top, left = max(top, mask_top), max(left, mask_left)
            bottom, right = min(bottom, mask_bottom), min(right, mask_right)
    stream.seek(mask_end)
    return (top, left, bottom, right) if top < bottom and left < right else None


def read_int(stream: BinaryIO, size: int = 4) -> int:
    data = stream.read(size)
    if len(data) != size:
        raise ValueError("Truncated Photoshop layer metadata")
    return int.from_bytes(data, "big")


def section_end(stream: BinaryIO, limit: int, size: int = 4) -> int:
    if stream.tell() + size > limit:
        raise ValueError("Truncated Photoshop metadata section")
    length = read_int(stream, size)
    end = stream.tell() + length
    if end > limit:
        raise ValueError("Photoshop metadata section exceeds its bounds")
    return end


def tagged_blocks(stream: BinaryIO, end: int, version: int, padding: int) -> Iterator[tuple[bytes, int]]:
    while end - stream.tell() >= 12:
        signature, key = unpack(">4s4s", stream.read(8))
        if signature not in (b"8BIM", b"8B64"):
            raise ValueError("Invalid Photoshop layer tag")
        length = read_int(stream, 8 if version == 2 and key in PSB_LONG_TAGS else 4)
        payload_end = stream.tell() + length
        next_block = payload_end + (-length % padding)
        if next_block > end:
            raise ValueError("Truncated Photoshop layer tag")
        yield key, length
        stream.seek(next_block)
        if padding == 1 and length % 2 and next_block < end:
            # Some writers leave a layer tag's alignment byte outside its declared size.
            if stream.read(1) != b"\0":
                stream.seek(next_block)


def read_records(stream: BinaryIO, end: int, version: int) -> list[LayerRecord]:
    count = abs(unpack(">h", stream.read(2))[0])
    records = []
    for _ in range(count):
        bounds = unpack(">4i", stream.read(16))
        stream.seek(read_int(stream, 2) * (10 if version == 2 else 6), 1)
        stream.seek(8, 1)
        opacity, _, flags, _ = unpack(">4B", stream.read(4))
        extra_end = section_end(stream, end)
        visible_bounds = layer_bounds(stream, extra_end, bounds)
        stream.seek(section_end(stream, extra_end))
        name_length = read_int(stream, 1)
        name = stream.read(name_length).decode("latin-1")
        stream.seek(-(name_length + 1) % 4, 1)
        layer_id = divider = 0
        smart = False
        source = None
        for key, length in tagged_blocks(stream, extra_end, version, 1):
            if key == b"luni" and length >= 4:
                name = stream.read(min(read_int(stream) * 2, (length - 4) & ~1, 8192)).decode(
                    "utf-16-be", errors="replace"
                ).rstrip("\0")
            elif key == b"lyid" and length >= 4:
                layer_id = read_int(stream)
            elif key in (b"lsct", b"lsdk") and length >= 4:
                divider = read_int(stream)
            elif key in SMART_OBJECT_TAGS:
                smart = True
                data = stream.read(min(length, 4096))
                if key in (b"SoLd", b"SoLE"):
                    # Idnt is the embedded source shared by copies of a Smart Object.
                    _, marker, value = data.partition(b"\0\0\0\0IdntTEXT")
                    if marker and len(value) >= 4:
                        size = int.from_bytes(value[:4], "big") * 2
                        if 0 < size <= len(value) - 4:
                            source = value[4:4 + size].decode("utf-16-be", errors="replace").rstrip("\0") or source
                elif len(data) >= 9 and data[8] <= len(data) - 9:
                    source = source or data[9:9 + data[8]].decode("latin-1") or None
        if stream.tell() > extra_end:
            raise ValueError("Photoshop layer record exceeds its bounds")
        stream.seek(extra_end)
        records.append(LayerRecord(layer_id, name, divider, not (flags & 2), opacity, smart, source, visible_bounds))
    return records


def read_replacement_layers(stream: BinaryIO, version: int, file_size: int) -> tuple[ReplacementLayer, ...]:
    stream.seek(26)
    for _ in range(2):
        stream.seek(section_end(stream, file_size))
    length_size = 8 if version == 2 else 4
    end = section_end(stream, file_size, length_size)
    if stream.tell() == end:
        return ()
    layer_end = section_end(stream, end, length_size)
    records = read_records(stream, layer_end, version) if stream.tell() < layer_end else []
    if not records:
        stream.seek(layer_end)
        if end - layer_end >= 4:
            stream.seek(section_end(stream, end))
            for key, length in tagged_blocks(stream, end, version, 4):
                if key in (b"Layr", b"Lr16", b"Lr32") and length >= 2:
                    records = read_records(stream, stream.tell() + length, version)
                    break
    return select_replacement_layers(records)


def select_replacement_layers(records: list[LayerRecord]) -> tuple[ReplacementLayer, ...]:
    groups: list[LayerRecord] = []
    placeholders = []
    legacy_sources = []
    visible_sources: dict[str, list[LayerRecord]] = {}
    has_paste_group = False
    for layer in reversed(records):
        if layer.divider in (1, 2):
            groups.append(layer)
            has_paste_group |= layer.name.strip() == "贴图区"
        elif layer.divider == 3:
            if groups:
                groups.pop()
        elif layer.smart and layer.id > 0:
            path = " / ".join([group.name for group in groups] + [layer.name])
            shown = layer.visible and layer.opacity > 0 and all(group.visible and group.opacity for group in groups)
            if any(group.name.strip() == "贴图区" for group in groups):
                placeholders.append((layer, path, shown))
            if not groups and layer.opacity == 0 and layer.source:
                legacy_sources.append((layer, path, False))
            if shown and layer.source:
                visible_sources.setdefault(layer.source, []).append(layer)

    options = []
    for layer, path, shown in placeholders if has_paste_group else legacy_sources:
        copies = visible_sources.get(layer.source or "", [])
        if shown or copies:
            target_id = layer.id if shown else copies[0].id
            regions = tuple(dict.fromkeys(item.bounds for item in copies or [layer] if item.bounds is not None))
            options.append(ReplacementLayer(target_id, layer.name, path, regions))
    return tuple({option.id: option for option in options}.values())
