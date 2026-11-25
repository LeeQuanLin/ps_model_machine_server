"""Build the GLB models used by the browser preview."""

import json
import math
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


Point = tuple[float, float, float]
Surface = Callable[[float, float], Point]
OUTPUT = Path(__file__).resolve().parents[1] / "app" / "admin" / "models"


@dataclass(frozen=True)
class Part:
    key: str
    color: str
    surface: Surface
    segments: int = 32
    underside: bool = False


def textile(width: float, depth: float, center: float, height: float, drape: float, folds: float) -> Surface:
    def sample(u: float, v: float) -> Point:
        x, z = 2 * u - 1, 2 * v - 1
        edge = max(abs(x), abs(z))
        sag = drape * max(0, (edge - .67) / .33) ** 2
        wave = folds * (math.sin(11 * x + 3 * z) + .45 * math.sin(22 * x - 5 * z))
        return width * x * (1 - .055 * abs(z) ** 5) / 2, height - sag + wave, (
            center + depth * z * (1 - .035 * abs(x) ** 5) / 2
        )
    return sample


def pillow(width: float, depth: float, center: float, height: float, thickness: float) -> Surface:
    def sample(u: float, v: float) -> Point:
        x, z = 2 * u - 1, 2 * v - 1
        profile = max(0, (1 - x * x) * (1 - z * z)) ** .55
        return width * x * (1 - .12 * abs(z) ** 5) / 2, height + thickness * profile, (
            center + depth * z * (1 - .10 * abs(x) ** 5) / 2
        )
    return sample


def floor(size: float) -> Surface:
    return lambda u, v: ((u - .5) * size, 0, (v - .5) * size)


def mesh(part: Part):
    n = part.segments
    points, normals, uv, indices = [], [], [], []
    step = 1 / n
    for row in range(n + 1):
        for column in range(n + 1):
            u, v = column * step, row * step
            points.append(part.surface(u, v))
            du = [b - a for a, b in zip(part.surface(max(0, u - step), v), part.surface(min(1, u + step), v))]
            dv = [b - a for a, b in zip(part.surface(u, max(0, v - step)), part.surface(u, min(1, v + step)))]
            normal = (dv[1] * du[2] - dv[2] * du[1], dv[2] * du[0] - dv[0] * du[2],
                      dv[0] * du[1] - dv[1] * du[0])
            length = math.sqrt(sum(component ** 2 for component in normal))
            sign = -1 if part.underside else 1
            normals.append(tuple(sign * component / length for component in normal))
            uv.append((1 - u if part.underside else u, v))
            if column < n and row < n:
                a = row * (n + 1) + column
                b, c = a + 1, a + n + 1
                indices.extend((a, b, c, b, c + 1, c) if part.underside else (a, c, b, b, c, c + 1))
    return points, normals, uv, indices


def linear(component: float) -> float:
    return component / 12.92 if component <= .04045 else ((component + .055) / 1.055) ** 2.4


def write_model(path: Path, parts: list[Part]) -> None:
    data = bytearray()
    gltf = {"asset": {"version": "2.0", "generator": "ps_model_machine_server"}, "scene": 0,
            "scenes": [{"nodes": list(range(len(parts)))}], "nodes": [], "meshes": [], "materials": [],
            "bufferViews": [], "accessors": [], "buffers": []}

    def accessor(values, width: int, indices: bool = False, bounds: bool = False) -> int:
        offset = len(data)
        flat = values if indices else [value for vector in values for value in vector]
        data.extend(struct.pack(f"<{len(flat)}{'H' if indices else 'f'}", *flat))
        index = len(gltf["accessors"])
        gltf["bufferViews"].append({"buffer": 0, "byteOffset": offset, "byteLength": len(data) - offset,
                                    "target": 34963 if indices else 34962})
        entry = {"bufferView": index, "componentType": 5123 if indices else 5126, "count": len(values),
                 "type": "SCALAR" if indices else f"VEC{width}"}
        if bounds:
            entry.update(min=[min(axis) for axis in zip(*values)], max=[max(axis) for axis in zip(*values)])
        gltf["accessors"].append(entry)
        data.extend(b"\0" * (-len(data) % 4))
        return index

    for index, part in enumerate(parts):
        points, normals, uv, triangles = mesh(part)
        gltf["nodes"].append({"name": part.key, "mesh": index, "extras": {"previewPart": part.key}})
        gltf["meshes"].append({"primitives": [{
            "attributes": {"POSITION": accessor(points, 3, bounds=True), "NORMAL": accessor(normals, 3),
                           "TEXCOORD_0": accessor(uv, 2)},
            "indices": accessor(triangles, 1, indices=True), "material": index,
        }]})
        color = [linear(channel / 255) for channel in bytes.fromhex(part.color)]
        gltf["materials"].append({"pbrMetallicRoughness": {"baseColorFactor": [*color, 1],
                                                         "metallicFactor": 0, "roughnessFactor": .9}})
    gltf["buffers"].append({"byteLength": len(data)})
    metadata = json.dumps(gltf, separators=(",", ":")).encode()
    metadata += b" " * (-len(metadata) % 4)
    size = 28 + len(metadata) + len(data)
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, size) + struct.pack("<I4s", len(metadata), b"JSON")
                     + metadata + struct.pack("<I4s", len(data), b"BIN\0") + data)


if __name__ == "__main__":
    OUTPUT.mkdir(exist_ok=True)
    write_model(OUTPUT / "bedding.glb", [
        Part("background", "e5e0f0", floor(5), 1),
        Part("a_side", "c5b8df", textile(2.7, 2.35, .5, .47, .32, .025)),
        Part("b_side", "a7bbd9", textile(2.62, .61, -.52, .54, .095, .018), 24),
        Part("pillowcase", "f4eae4", pillow(1.55, .86, -1.42, .1, .24), 28),
        Part("pillowcase", "f4eae4", pillow(1.55, .86, -1.42, .1, -.095), 28, underside=True),
    ])
    write_model(OUTPUT / "pillowcase.glb", [
        Part("pillowcase", "e4d8f0", pillow(2, 1.25, 0, 0, .31)),
        Part("b_side", "abc0db", pillow(2, 1.25, 0, 0, -.26), underside=True),
    ])
    write_model(OUTPUT / "sheet.glb", [
        Part("background", "e8e3f0", floor(5), 1),
        Part("a_side", "c6b9e4", textile(2.9, 2.9, 0, .27, .18, .03), 38),
    ])
