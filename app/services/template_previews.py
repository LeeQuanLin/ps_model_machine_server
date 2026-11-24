import json
import logging
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image
from starlette.datastructures import State

from app.services.photoshop import PhotoshopControlError
from app.services.templates import read_sidecar


logger = logging.getLogger(__name__)
SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "export_template_preview.jsx"
SHORT_EDGE = 800


def queue_template_previews(state: State) -> None:
    with state.template_lock:
        for template in state.templates.values():
            if template.thumbnail_pending and template.id not in state.preview_jobs:
                state.preview_jobs.add(template.id)
                state.renders.executor.submit(generate_template_preview, state, template.id)


def generate_template_preview(state: State, template_id: str) -> None:
    template = None
    sidecar = None
    retry = False
    try:
        with state.template_lock:
            template = state.templates.get(template_id)
            if template is None or not template.thumbnail_pending:
                return
            root = state.settings.template_dir
            path = root / template.path
            source = path.resolve()
            if not source.is_relative_to(root):
                raise ValueError("Template is outside its configured directory")
            snapshot = source.stat()
            sidecar = read_sidecar(path)
        if sidecar is not None:
            return

        with TemporaryDirectory(prefix=".preview-", dir=path.parent, ignore_cleanup_errors=True) as temporary:
            folder = Path(temporary)
            output = folder / "preview.png"
            report = folder / "result.txt"
            script = folder / "preview.jsx"
            job = {"template": str(source), "output": str(output), "report": str(report), "short_edge": SHORT_EDGE}
            script.write_text("#target photoshop\nvar job = " + json.dumps(job) + ";\n"
                              + SCRIPT.read_text(encoding="utf-8"), encoding="utf-8-sig")
            state.photoshop.render(script)
            if not output.is_file() or report.exists():
                message = report.read_text(encoding="utf-8-sig") if report.exists() else "No PNG was exported"
                raise ValueError(message)
            with Image.open(output, formats=["PNG"]) as image:
                if min(image.size) != SHORT_EDGE:
                    raise ValueError("Photoshop preview has an unexpected size")
                image.verify()

            with state.template_lock:
                current_stat = source.stat()
                if (state.templates.get(template_id) is None or path.resolve() != source
                        or (current_stat.st_mtime_ns, current_stat.st_size) != (snapshot.st_mtime_ns, snapshot.st_size)):
                    return
                sidecar = read_sidecar(path)
                if sidecar is None:
                    output.replace(path.with_suffix(".png"))
                    sidecar = read_sidecar(path)
    except (OSError, ValueError, SyntaxError, PhotoshopControlError, Image.DecompressionBombError) as error:
        logger.warning("Could not export template preview for %s: %s", getattr(template, "path", template_id), error)
    finally:
        with state.template_lock:
            state.preview_jobs.discard(template_id)
            current = state.templates.get(template_id)
            if current is not None and current.thumbnail_pending:
                if sidecar is not None:
                    image, media_type = sidecar
                    updated = replace(current, thumbnail=image, thumbnail_media_type=media_type, thumbnail_pending=False)
                    state.templates = {**state.templates, template_id: updated}
                elif current is template:
                    state.templates = {**state.templates, template_id: replace(current, thumbnail_pending=False)}
                else:
                    retry = True
        if retry:
            queue_template_previews(state)
