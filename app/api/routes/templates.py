from pathlib import Path
from struct import error as StructError
from tempfile import NamedTemporaryFile

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from starlette.concurrency import run_in_threadpool

from app.api.dependencies import authorize
from app.api.uploads import save_upload, upload_name
from app.schemas.template import LayerPreviewRegion, ReplacementLayerSummary, TemplateSummary
from app.services.template_layers import ReplacementLayer
from app.services.template_previews import queue_template_previews
from app.services.templates import TemplateRecord, VERSIONS, load_templates, read_template


router = APIRouter(prefix="/templates", tags=["Templates"], dependencies=[Depends(authorize)])


@router.get("")
def list_templates(request: Request) -> list[TemplateSummary]:
    return [summary(request, template) for template in request.app.state.templates.values()]


def summarize_layer(layer: ReplacementLayer, template: TemplateRecord) -> ReplacementLayerSummary:
    regions = []
    for top, left, bottom, right in layer.bounds:
        top, left = max(0, top), max(0, left)
        bottom, right = min(template.height, bottom), min(template.width, right)
        if top < bottom and left < right:
            regions.append(LayerPreviewRegion(left=left / template.width, top=top / template.height,
                                              right=right / template.width, bottom=bottom / template.height))
    return ReplacementLayerSummary(id=layer.id, name=layer.name, path=layer.path, preview_regions=regions)


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
        replacement_layers=[summarize_layer(layer, template) for layer in template.replacement_layers],
    )


@router.post("/refresh")
def refresh_templates(request: Request) -> list[TemplateSummary]:
    state = request.app.state
    with state.template_lock:
        state.templates = load_templates(state.settings.template_dir)
    queue_template_previews(state)
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
    queue_template_previews(state)
    return summary(request, template)


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
