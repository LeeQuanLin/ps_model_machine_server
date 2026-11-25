import logging
from dataclasses import replace
from pathlib import Path
from struct import error as StructError
from tempfile import NamedTemporaryFile

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from starlette.concurrency import run_in_threadpool

from app.api.dependencies import authorize
from app.api.uploads import save_upload, upload_name
from app.schemas.template import (LayerPreviewRegion, ReplacementLayerSummary, TemplateConfiguration,
                                  TemplateInspection, TemplateSlots, TemplateSummary)
from app.services.template_configuration import check_template_source, inspect_template, save_configuration, write_sidecar
from app.services.template_layers import ReplacementLayer
from app.services.templates import TemplateRecord, VERSIONS, configuration_path, load_templates, read_sidecar, read_template


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/templates", tags=["Templates"], dependencies=[Depends(authorize)])


@router.get("")
def list_templates(request: Request) -> list[TemplateSummary]:
    return [summary(request, template) for template in request.app.state.templates.values()]


def summarize_layer(layer: ReplacementLayer, template: TemplateRecord, request: Request) -> ReplacementLayerSummary:
    regions = []
    for top, left, bottom, right in layer.bounds:
        top, left = max(0, top), max(0, left)
        bottom, right = min(template.height, bottom), min(template.width, right)
        if top < bottom and left < right:
            regions.append(LayerPreviewRegion(left=left / template.width, top=top / template.height,
                                              right=right / template.width, bottom=bottom / template.height))
    thumbnail_url = (request.url_for("get_layer_thumbnail", template_id=template.id, layer_id=layer.id).path
                     if layer.thumbnail is not None else None)
    return ReplacementLayerSummary(id=layer.id, name=layer.name, path=layer.path, preview_regions=regions,
                                   thumbnail_url=thumbnail_url)


def summary(request: Request, template: TemplateRecord) -> TemplateSummary:
    return TemplateSummary(
        id=template.id,
        name=template.path.stem,
        filename=template.path.name,
        relative_path=template.path.as_posix(),
        width=template.width,
        height=template.height,
        size_bytes=template.size_bytes,
        thumbnail_url=(request.url_for("get_template_thumbnail", template_id=template.id).path
                       if template.thumbnail is not None else None),
        thumbnail_pending=template.thumbnail_pending,
        replacement_layers=[summarize_layer(layer, template, request) for layer in template.available_layers],
        slots=template.slots,
    )


@router.post("/refresh")
def refresh_templates(request: Request) -> list[TemplateSummary]:
    state = request.app.state
    with state.template_lock:
        state.templates = load_templates(state.settings.template_dir)
        state.template_inspections.clear()
    return list_templates(request)


@router.post("", status_code=201)
async def add_template(request: Request, filename: str = Query(max_length=180)) -> TemplateSummary:
    name = upload_name(filename, set(VERSIONS))
    directory = request.app.state.settings.template_dir
    directory.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=directory, suffix=".upload", delete=False) as temporary:
        staged = Path(temporary.name)
    try:
        await save_upload(request, staged, 8 * 1024 ** 3)
        return await run_in_threadpool(publish_template, request, staged, name)
    finally:
        staged.unlink(missing_ok=True)


def publish_template(request: Request, staged: Path, name: Path) -> TemplateSummary:
    state = request.app.state
    try:
        template = read_template(staged, name)
    except (ValueError, StructError) as error:
        raise HTTPException(422, "无法读取 PSD 或 PSB 样机。") from error
    with state.template_lock:
        target = state.settings.template_dir / name
        if target.exists() or target.is_symlink():
            raise HTTPException(409, "目录中已有同名样机。")
        staged.rename(target)
        state.templates = {**state.templates, template.id: template}
    return summary(request, template)


def configuration_source(request: Request, template_id: str) -> tuple[TemplateRecord, Path]:
    state = request.app.state
    template = state.templates.get(template_id)
    if template is None:
        raise HTTPException(404, "样机不存在。")
    try:
        return template, check_template_source(state.settings.template_dir, template)
    except (OSError, ValueError) as error:
        raise HTTPException(409, "样机文件已变化，请刷新目录后重新配置。") from error


@router.post("/{template_id}/inspect")
def inspect_configuration(template_id: str, request: Request) -> TemplateInspection:
    state = request.app.state
    with state.template_lock:
        template, source = configuration_source(request, template_id)
    try:
        inspected, layers = inspect_template(source, template, state.photoshop)
    except (OSError, ValueError) as error:
        raise HTTPException(422, str(error)) from error
    with state.template_lock:
        current, _ = configuration_source(request, template_id)
        if current.revision != inspected.revision:
            raise HTTPException(409, "样机文件已变化，请重新读取图层。")
        if inspected.thumbnail and inspected.thumbnail_media_type == "image/png" and read_sidecar(source) is None:
            try:
                write_sidecar(source.with_suffix(".png"), inspected.thumbnail)
            except OSError as error:
                logger.warning("Could not save template preview for %s: %s", current.path, error)
        updated = replace(current, thumbnail=inspected.thumbnail, thumbnail_media_type=inspected.thumbnail_media_type)
        state.templates = {**state.templates, template_id: updated}
        candidates = [(layer, summarize_layer(layer, updated, request)) for layer in layers]
        candidates = [(layer, item) for layer, item in candidates if item.preview_regions]
        state.template_inspections[template_id] = (inspected.revision, {layer.id: layer for layer, _ in candidates})
    return TemplateInspection(revision=updated.revision, slots=updated.slots or TemplateSlots(),
                              layers=[item for _, item in candidates],
                              thumbnail_url=request.url_for("get_template_thumbnail", template_id=template_id).path
                              if updated.thumbnail is not None else None)


@router.put("/{template_id}/configuration")
def configure_template(template_id: str, config: TemplateConfiguration, request: Request) -> TemplateSummary:
    state = request.app.state
    with state.template_lock:
        template, source = configuration_source(request, template_id)
        inspection = state.template_inspections.get(template_id)
        if not inspection or inspection[0] != config.revision or template.revision != config.revision:
            raise HTTPException(409, "请重新读取样机图层后保存配置。")
        selected = [value for value in config.slots.model_dump().values() if value is not None]
        if len(set(selected)) != len(selected) or any(layer not in inspection[1] for layer in selected):
            raise HTTPException(422, "每个部位需选择不同的可替换智能对象图层。")
        updated = replace(template, slots=config.slots,
                          configured_layers=tuple(inspection[1][layer] for layer in selected))
        try:
            save_configuration(source, updated)
        except OSError as error:
            logger.warning("Could not save template configuration for %s: %s", template.path, error)
            raise HTTPException(409, "无法保存配置，请检查样机目录的写入权限。") from error
        state.templates = {**state.templates, template_id: updated}
    return summary(request, updated)


@router.delete("/{template_id}", status_code=204)
def delete_template(template_id: str, request: Request) -> Response:
    state = request.app.state
    with state.template_lock:
        template = state.templates.get(template_id)
        if template is None:
            raise HTTPException(404, "样机不存在。")
        root = state.settings.template_dir
        target = root / template.path
        if not target.resolve().is_relative_to(root):
            raise HTTPException(409, "样机路径已变化，请刷新列表。")
        try:
            target.unlink(missing_ok=True)
        except OSError as error:
            raise HTTPException(409, "无法删除样机，请检查文件是否正在使用。") from error
        state.templates = {key: value for key, value in state.templates.items() if key != template_id}
        state.template_inspections.pop(template_id, None)
        try:
            configuration_path(target).unlink(missing_ok=True)
        except OSError as error:
            logger.warning("Could not remove template configuration for %s: %s", template.path, error)
    return Response(status_code=204)


@router.get(
    "/{template_id}/thumbnail",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}, "image/png": {}}}, 404: {"description": "Thumbnail not found"}},
)
def get_template_thumbnail(template_id: str, request: Request) -> Response:
    template = request.app.state.templates.get(template_id)
    if template is None or template.thumbnail is None:
        raise HTTPException(404, "Thumbnail not found.")
    return Response(content=template.thumbnail, media_type=template.thumbnail_media_type)


@router.get("/{template_id}/layers/{layer_id}/thumbnail", response_class=Response,
            responses={200: {"content": {"image/png": {}}}, 404: {"description": "Layer thumbnail not found"}})
def get_layer_thumbnail(template_id: str, layer_id: int, request: Request) -> Response:
    template = request.app.state.templates.get(template_id)
    if template is not None:
        layer = next((item for item in template.available_layers if item.id == layer_id), None)
        if layer is not None and layer.thumbnail is not None:
            return Response(content=layer.thumbnail, media_type="image/png")
    raise HTTPException(404, "图层缩略图不存在。")
