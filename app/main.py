import re
import secrets
from contextlib import asynccontextmanager
from html import escape
from pathlib import PureWindowsPath
from threading import Event, Thread
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError

from app import store
from app.config import DATA, ROOT, TOKEN, b_side_presets, templates
from app.worker import dispatch

STATIC = ROOT / "app" / "static"
LIMIT = 50 * 1024 * 1024


@asynccontextmanager
async def lifespan(app):
    if not TOKEN.strip():
        raise RuntimeError("请在 .env 中设置 PHOTOSHOP_API_TOKEN")
    app.state.b_side_presets = b_side_presets()
    store.initialize()
    stop = Event()
    thread = Thread(target=dispatch, args=(stop,), name="mockup-dispatch", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=8)


app = FastAPI(title="样机工作台", lifespan=lifespan)


@app.middleware("http")
async def protect(request: Request, call_next):
    if request.url.path.startswith("/api/"):
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if (scheme.lower() != "bearer" or not token
                or not secrets.compare_digest(token.encode(), TOKEN.encode())):
            return JSONResponse({"detail": "请输入有效的访问令牌"}, status_code=401,
                                headers={"WWW-Authenticate": "Bearer"})
    return await call_next(request)


@app.get("/", include_in_schema=False)
def index(request: Request):
    buttons = "".join(
        f'<button type="button" class="swatch" data-color="{color}" '
        f'title="{escape(name)}" aria-label="{escape(name)}"></button>'
        for name, color in request.app.state.b_side_presets
    )
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html.replace("<!-- B_SIDE_SWATCHES -->", buttons), headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/api/templates")
def get_templates():
    return {"items": list(templates())}


def save_image(upload, folder, stem):
    temp = folder / (stem + ".upload")
    size = 0
    with temp.open("xb") as stream:
        while chunk := upload.file.read(1024 * 1024):
            size += len(chunk)
            if size > LIMIT:
                raise HTTPException(413, "单张图片最大 50 MB")
            stream.write(chunk)
    try:
        with Image.open(temp, formats=["JPEG", "PNG"]) as image:
            ext = ".png" if image.format == "PNG" else ".jpg"
            image.verify()
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError, UnidentifiedImageError) as exc:
        raise HTTPException(422, "请上传有效的 PNG 或 JPEG 图片") from exc
    target = folder / (stem + ext)
    temp.rename(target)
    return target.name


@app.post("/api/jobs", status_code=202)
def submit(
    b_color: Annotated[str, Form()],
    background_color: Annotated[str, Form()],
    template: Annotated[str | None, Form(description="留空选择全部；random 随机一款；sequential 顺序一款；也可传模板相对路径")] = None,
    a_image: Annotated[UploadFile | None, File()] = None,
    pillow_image: Annotated[UploadFile | None, File()] = None,
    output_name: Annotated[str | None, Form()] = None,
):
    name = (output_name or "").strip()
    if name:
        if name.lower().endswith(".png"):
            name = name[:-4]
        if (not name or name.endswith((" ", ".")) or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
                or PureWindowsPath(name + ".png").is_reserved()):
            raise HTTPException(422, "请输入有效的输出文件名，不能包含路径或 Windows 文件名禁用字符")
    colors = {"B面": b_color, "背景": background_color}
    if not all(re.fullmatch(r"#[0-9a-fA-F]{6}", color) for color in colors.values()):
        raise HTTPException(422, "颜色应为六位十六进制色值")
    available = templates()
    if not available:
        raise HTTPException(422, "templates 目录中没有 PSD 或 PSB 样机")
    if template == "random":
        template = secrets.choice(tuple(available))
    if template and template != "sequential":
        if template not in available:
            raise HTTPException(404, "样机已移除，请刷新后重新选择")
        available = {template: available[template]}
    count = 1 if template == "sequential" else len(available)
    longest_suffix = f"-{count:02d}.png" if count > 1 else ".png"
    if len((name + longest_suffix).encode("utf-8")) > 255:
        raise HTTPException(422, "输出文件名过长，请缩短后再提交")
    batch = uuid4().hex
    folder = DATA / "inputs" / batch
    folder.mkdir()
    try:
        images = {}
        for layer, upload, stem in (("A面", a_image, "a"), ("枕套", pillow_image, "pillow")):
            if upload is not None:
                images[layer] = save_image(upload, folder, stem)
        ids = store.submit(batch, available, {"images": images, "colors": colors},
                           sequential=template == "sequential", output_name=name)
    except Exception:
        for path in folder.iterdir():
            path.unlink()
        folder.rmdir()
        raise
    return {"ids": ids, "count": len(ids)}


@app.get("/api/jobs")
def jobs(offset: int = Query(0, ge=0), limit: int = Query(36, ge=1, le=120)):
    return store.page("jobs", offset, limit)


@app.get("/api/logs")
def logs(offset: int = Query(0, ge=0), limit: int = Query(36, ge=1, le=120)):
    return store.page("logs", offset, limit)


@app.get("/api/outputs")
def outputs(offset: int = Query(0, ge=0), limit: int = Query(24, ge=1, le=120)):
    return store.page("outputs", offset, limit)


@app.get("/api/outputs/{job_id}/{kind}")
def output_image(job_id: UUID, kind: str):
    job = store.get(job_id.hex)
    if not job or job["status"] != "done" or kind not in {"thumbnail", "image", "download"}:
        raise HTTPException(404, "找不到输出图")
    path = DATA / "outputs" / job_id.hex / ("thumb.png" if kind == "thumbnail" else "render.png")
    if not path.is_file():
        raise HTTPException(404, "输出文件已移除")
    name = store.download_name(job)
    return FileResponse(path, media_type="image/png", filename=name if kind == "download" else None)
