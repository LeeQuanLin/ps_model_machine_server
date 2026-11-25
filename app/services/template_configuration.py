import json
from dataclasses import replace
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from urllib.parse import unquote

from PIL import Image

from app.services.photoshop import PhotoshopService
from app.services.template_layers import ReplacementLayer
from app.services.templates import TemplateRecord, configuration_path, read_sidecar, template_revision


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "export_template_preview.jsx"
SHORT_EDGE = 800


def check_template_source(root: Path, template: TemplateRecord) -> Path:
    source = (root / template.path).resolve()
    if not source.is_relative_to(root) or not source.is_file() or template_revision(source.stat()) != template.revision:
        raise ValueError("样机文件已变化，请刷新目录后重新配置。")
    return source


def inspect_template(source: Path, template: TemplateRecord, photoshop: PhotoshopService
                     ) -> tuple[TemplateRecord, tuple[ReplacementLayer, ...]]:
    with TemporaryDirectory(prefix=".mockup-", dir=source.parent, ignore_cleanup_errors=True) as temporary:
        folder = Path(temporary)
        output = folder / "preview.png"
        report = folder / "result.txt"
        script = folder / "inspect.jsx"
        sidecar = read_sidecar(source)
        job = {"template": str(source), "output": str(output) if sidecar is None else None,
               "report": str(report), "short_edge": SHORT_EDGE}
        script.write_text("#target photoshop\nvar job = " + json.dumps(job) + ";\n"
                          + SCRIPT.read_text(encoding="utf-8"), encoding="utf-8-sig")
        photoshop.render(script)
        if not report.is_file():
            raise ValueError("Photoshop 未返回图层信息。")
        lines = report.read_text(encoding="utf-8-sig").splitlines()
        if not lines or lines[0] != "OK":
            raise ValueError("Photoshop 读取样机失败。" + (lines[0].removeprefix("ERROR ") if lines else ""))
        parsed = {layer.id: layer for layer in template.replacement_layers}
        layers = []
        for line in lines[1:]:
            layer_id, name, path, top, left, bottom, right = line.split("\t")
            bounds = tuple(map(int, (top, left, bottom, right)))
            layers.append(parsed.get(int(layer_id)) or ReplacementLayer(int(layer_id), unquote(name), unquote(path),
                                                                       (bounds,)))
        if output.is_file():
            with Image.open(output, formats=["PNG"]) as image:
                if min(image.size) != SHORT_EDGE:
                    raise ValueError("Photoshop 返回的缩略图尺寸有误。")
                image.verify()
            sidecar = output.read_bytes(), "image/png"
        if sidecar:
            image_data, media_type = sidecar
            template = replace(template, thumbnail=image_data, thumbnail_media_type=media_type)
    return template, tuple(layers)


def save_configuration(source: Path, template: TemplateRecord) -> None:
    config = {"revision": template.revision, "slots": template.slots.model_dump(),
              "layers": [{"id": layer.id, "name": layer.name, "path": layer.path, "bounds": layer.bounds}
                         for layer in template.configured_layers]}
    write_sidecar(configuration_path(source), json.dumps(config, ensure_ascii=False, indent=2).encode("utf-8"))


def write_sidecar(path: Path, data: bytes) -> None:
    with NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        staged = Path(temporary.name)
    try:
        staged.write_bytes(data)
        staged.replace(path)
    finally:
        staged.unlink(missing_ok=True)
