from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from PIL import Image
from starlette.concurrency import run_in_threadpool

from app.api.dependencies import authorize
from app.api.uploads import save_upload, upload_name
from app.schemas.render import RenderSummary
from app.services.renders import ARTWORK_FORMATS, validate_artwork


router = APIRouter(prefix="/renders", tags=["Renders"], dependencies=[Depends(authorize)])


@router.get("")
def list_renders(request: Request) -> list[RenderSummary]:
    return request.app.state.renders.list()


@router.post("", status_code=202)
async def create_render(request: Request, template_id: str, filename: str = Query(max_length=180),
                        layer: str | None = Query(default=None, max_length=180),
                        layer_id: int | None = Query(default=None, gt=0)) -> RenderSummary:
    state = request.app.state
    template = state.templates.get(template_id)
    if template is None:
        raise HTTPException(404, "样机不存在，请刷新样机列表。")
    selector = (layer or "").strip()
    if layer_id is not None:
        if selector:
            raise HTTPException(422, "请选择一种图层指定方式。")
        if not any(option.id == layer_id for option in template.replacement_layers):
            raise HTTPException(422, "该图层不在此样机的可替换图层中，请刷新样机目录。")
        layer_selector = layer_id
    else:
        layer_selector = int(selector) if selector.isascii() and selector.isdecimal() else selector or None
    name = upload_name(filename, set(ARTWORK_FORMATS))
    state.photoshop.ensure_desktop_session()
    extension = name.suffix.lower()
    render_id = uuid4().hex
    folder = state.renders.root / render_id
    folder.mkdir()
    artwork = folder / f"artwork{extension}"
    try:
        await save_upload(request, artwork, 50 * 1024 ** 2)
        try:
            await run_in_threadpool(validate_artwork, artwork, extension)
        except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as error:
            raise HTTPException(422, "无法读取上传的 PNG 或 JPEG 图片。") from error
        return state.renders.enqueue(render_id, artwork, f"{name.stem}_{template.path.stem}.png", template, layer_selector)
    except BaseException:
        artwork.unlink(missing_ok=True)
        folder.rmdir()
        raise


@router.get("/{render_id}/image")
def get_render_image(render_id: str, request: Request, download: bool = False,
                     thumbnail: bool = False) -> FileResponse:
    renders = request.app.state.renders
    result = renders.get(render_id)
    if result is None or result.status != "completed":
        raise HTTPException(404, "生成图不存在。")
    path = renders.root / result.id / ("thumbnail.png" if thumbnail else "result.png")
    if not path.is_file():
        raise HTTPException(404, "生成图文件不存在。")
    return FileResponse(path, media_type="image/png", filename=result.filename if download else None)


@router.delete("/{render_id}", status_code=204)
def delete_render(render_id: str, request: Request) -> Response:
    renders = request.app.state.renders
    result = renders.get(render_id)
    if result is None:
        raise HTTPException(404, "生成记录不存在。")
    try:
        deleted = renders.delete(result.id)
    except OSError as error:
        raise HTTPException(409, "无法删除生成图，请稍后重试。") from error
    if not deleted:
        raise HTTPException(409, "任务还在运行，完成后可以删除。")
    return Response(status_code=204)
