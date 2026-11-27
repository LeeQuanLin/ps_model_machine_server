import logging
import ntpath
import os
import re
import secrets
import shutil
import sqlite3
import unicodedata
from collections.abc import Iterable
from contextlib import asynccontextmanager
from html import escape
from pathlib import Path, PureWindowsPath
from tempfile import NamedTemporaryFile
from threading import Event, Thread
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from app import exports, products, store
from app.config import DATA, QUEUE, ROOT, TOKEN, b_side_presets, detail_template, templates
from app.storage_cleanup import CleanupBusy, CleanupGate, RESET_PATH, clear_workspace
from app.worker import dispatch, release_inputs

STATIC = ROOT / "app" / "static"
LIMIT = 50 * 1024 * 1024
INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class ProductForm(BaseModel):
    name: str


class ProductSelection(BaseModel):
    ids: list[UUID] = Field(min_length=1)


class StorageClearConfirmation(BaseModel):
    confirm: Literal["clear-all"]


def directory_size(root):
    total = 0
    for directory, _, files in os.walk(root):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(directory, name))
            except OSError:
                continue
    return total


def cleanup_output_trash():
    outputs = DATA / "outputs"
    if not outputs.is_dir():
        return
    for path in outputs.glob(".deleted-*"):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)


def stash_output_folder(job_id):
    folder = DATA / "outputs" / job_id
    if not folder.is_dir():
        return None
    trash = DATA / "outputs" / f".deleted-{job_id}-{uuid4().hex}"
    try:
        folder.rename(trash)
    except OSError as exc:
        raise HTTPException(409, "输出图正在下载或被占用，请稍后重试") from exc
    return trash


def purge_product_files(product_id):
    with store.database() as db:
        pending = db.execute("SELECT cleanup_pending FROM products WHERE id=?", (product_id,)).fetchone()
        if not pending or not pending["cleanup_pending"]:
            return True
        jobs = products.product_jobs(db, product_id)
    try:
        data_root = DATA.resolve()
        for area, keys in (("inputs", {job["batch"] for job in jobs}),
                           ("outputs", {job["id"] for job in jobs})):
            root = data_root / area
            if root.resolve() != root:
                raise OSError("产品存储目录无效")
            for key in keys:
                folder = root / key
                if not re.fullmatch(r"[0-9a-f]{32}", key) or folder.resolve() != folder:
                    raise OSError("产品任务目录无效")
                try:
                    shutil.rmtree(folder)
                except FileNotFoundError:
                    if folder.exists():
                        raise
    except OSError:
        logging.getLogger(__name__).exception("产品 %s 文件清理待重试", product_id)
        return False
    with store.database() as db:
        db.execute("UPDATE products SET cleanup_pending=0 WHERE id=?", (product_id,))
    return True


@asynccontextmanager
async def lifespan(app):
    if not TOKEN.strip():
        raise RuntimeError("请在 .env 中设置 PHOTOSHOP_API_TOKEN")
    app.state.b_side_presets = b_side_presets()
    store.initialize()
    cleanup_output_trash()
    with store.database() as db:
        pending = db.execute("SELECT id FROM products WHERE cleanup_pending=1").fetchall()
    for product in pending:
        purge_product_files(product["id"])
    logging.getLogger("uvicorn.error").info("样机工作台队列 %s，数据目录 %s", QUEUE, DATA)
    stop = Event()
    threads = [
        Thread(target=dispatch, args=(stop,), name="mockup-dispatch", daemon=True),
        Thread(target=exports.dispatch, args=(DATA, stop), name="export-dispatch", daemon=True),
    ]
    for thread in threads:
        thread.start()
    try:
        yield
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=8)


app = FastAPI(title="样机工作台", version="2.0.0", lifespan=lifespan)
app.add_middleware(CleanupGate)


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
    detail = detail_template()
    return {"items": list(templates()), "queue": QUEUE, "detail_template": detail.name if detail else None}


@app.get("/api/storage")
def storage():
    try:
        usage = shutil.disk_usage(DATA)
    except OSError as exc:
        raise HTTPException(503, f"无法读取磁盘空间：{exc}") from exc
    return {
        "drive": DATA.drive or str(DATA.anchor),
        "total": usage.total,
        "free": usage.free,
        "project_used": directory_size(DATA),
    }


@app.post(RESET_PATH)
def clear_storage(confirmation: StorageClearConfirmation):
    before = directory_size(DATA)
    try:
        remaining = clear_workspace(DATA)
    except CleanupBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    except (OSError, sqlite3.Error) as exc:
        logging.getLogger(__name__).exception("无法释放工作台空间")
        raise HTTPException(503, "无法释放空间，请检查磁盘权限后重试") from exc
    usage = storage()
    return {**usage, "released_bytes": max(0, before - usage["project_used"]), "remaining": remaining}


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


def layer_settings(folder, a_image, pillow_image, b_color, background_color):
    colors = {"B面": b_color, "背景": background_color}
    if not all(re.fullmatch(r"#[0-9a-fA-F]{6}", color) for color in colors.values()):
        raise HTTPException(422, "颜色应为六位十六进制色值")
    images = {}
    for layer, upload, stem in (("A面", a_image, "a"), ("枕套", pillow_image, "pillow")):
        if upload is not None:
            images[layer] = save_image(upload, folder, stem)
    return {"images": images, "colors": colors}


def product_name(value, label):
    name = unicodedata.normalize("NFC", value.strip())
    filename = f"{name}.png"
    reserved = ntpath.isreserved(filename) if hasattr(ntpath, "isreserved") else PureWindowsPath(filename).is_reserved()
    if (not name or name.endswith(".") or INVALID_FILENAME.search(name)
            or reserved
            or len(name.encode("utf-8", errors="replace")) > 200
            or any(0xD800 <= ord(char) <= 0xDFFF for char in name)):
        raise HTTPException(422, f"请输入有效的{label}，文件名部分最多 200 字节")
    return name


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
    longest_suffix = f"-{count:02d}-透明图.png" if count > 1 else "-透明图.png"
    if len((name + longest_suffix).encode("utf-8")) > 255:
        raise HTTPException(422, "输出文件名过长，请缩短后再提交")
    batch = uuid4().hex
    folder = DATA / "inputs" / batch
    folder.mkdir()
    try:
        settings = layer_settings(folder, a_image, pillow_image, b_color, background_color)
        ids = store.submit(batch, available, settings,
                           sequential=template == "sequential", output_name=name)
    except Exception:
        for path in folder.iterdir():
            path.unlink()
        folder.rmdir()
        raise
    return {"ids": ids, "count": len(ids)}


@app.get("/api/products")
def list_products(offset: int = Query(0, ge=0), limit: int = Query(24, ge=1, le=120)):
    return products.page(offset, limit)


@app.get("/api/products/{product_id}")
def get_product(product_id: UUID):
    product = products.get(product_id.hex)
    if product is None:
        raise HTTPException(404, "找不到产品")
    return product


@app.put("/api/products/{product_id}/draft")
def create_product(product_id: UUID, form: ProductForm):
    try:
        return products.create(product_id.hex, product_name(form.name, "产品名"))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.patch("/api/products/{product_id}")
def rename_product(product_id: UUID, form: ProductForm):
    try:
        return products.rename(product_id.hex, product_name(form.name, "产品名"))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.delete("/api/products/{product_id}")
def delete_product(product_id: UUID):
    try:
        products.delete(product_id.hex)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except products.ProductConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"deleted": True, "cleanup_pending": not purge_product_files(product_id.hex)}


@app.post("/api/products/{product_id}/skus/{sku_id}", status_code=202)
def submit_sku(
    product_id: UUID,
    sku_id: UUID,
    sku_name: Annotated[str, Form()],
    template: Annotated[str, Form()],
    b_color: Annotated[str, Form()],
    background_color: Annotated[str, Form()],
    a_image: Annotated[UploadFile | None, File()] = None,
    pillow_image: Annotated[UploadFile | None, File()] = None,
):
    name = product_name(sku_name, "SKU 名")
    product = get_product(product_id)
    if any(sku["id"] == sku_id.hex for sku in product["skus"]):
        return product
    if product["saved_at"]:
        raise HTTPException(409, "产品已保存，无法添加 SKU")
    available = templates()
    if not available:
        raise HTTPException(422, "templates 目录中没有可用的样机模板")
    if template not in {"random", "sequential"} and template not in available:
        raise HTTPException(422, "请选择一个样机模板")
    batch = uuid4().hex
    folder = DATA / "inputs" / batch
    folder.mkdir()
    inserted = False
    try:
        settings = layer_settings(folder, a_image, pillow_image, b_color, background_color)
        result, inserted = products.add_sku(product_id.hex, sku_id.hex, batch, name, template,
                                            available, settings)
        return result
    except products.ProductConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(409, "样机模板已移除，请重新选择") from exc
    finally:
        if not inserted:
            shutil.rmtree(folder)


@app.put("/api/products/{product_id}/save", status_code=202)
def save_product(product_id: UUID, form: ProductForm):
    name = product_name(form.name, "产品名")
    try:
        result, batches = products.save(product_id.hex, name, detail_template())
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (products.ProductConflict, FileNotFoundError) as exc:
        raise HTTPException(409, str(exc)) from exc
    for batch in batches:
        release_inputs(batch)
    return result


def zip_response(files: Iterable[tuple[Path, str]], filename: str):
    with NamedTemporaryFile(dir=DATA, prefix="download-", suffix=".zip", delete=False) as tmp:
        path = Path(tmp.name)
    try:
        exports.write_archive(path, files)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return FileResponse(path, media_type="application/zip", filename=filename,
                        background=BackgroundTask(path.unlink, missing_ok=True))


@app.post("/api/exports", status_code=202)
def submit_export(selection: ProductSelection):
    ids = tuple(dict.fromkeys(product_id.hex for product_id in selection.ids))
    try:
        return exports.submit(DATA, ids)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except products.ProductConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/exports")
def list_exports(offset: int = Query(0, ge=0), limit: int = Query(24, ge=1, le=120)):
    return exports.page(offset, limit)


@app.get("/api/exports/{export_id}/download")
def download_export(export_id: UUID):
    record = exports.get(export_id.hex)
    if not record:
        raise HTTPException(404, "找不到导出记录")
    if record["status"] != "done":
        raise HTTPException(409, "该导出记录没有可下载文件")
    path = exports.archive_path(DATA, export_id.hex)
    if not path.is_file():
        raise HTTPException(404, "导出文件已丢失，请重新导出")
    return FileResponse(path, media_type="application/zip", filename=record["filename"],
                        headers={"Cache-Control": "no-store"},
                        background=BackgroundTask(exports.record_download, export_id.hex))


@app.get("/api/jobs")
def jobs(offset: int = Query(0, ge=0), limit: int = Query(36, ge=1, le=120)):
    return store.page("jobs", offset, limit)


@app.get("/api/logs")
def logs(offset: int = Query(0, ge=0), limit: int = Query(36, ge=1, le=120)):
    return store.page("logs", offset, limit)


@app.get("/api/outputs")
def outputs(offset: int = Query(0, ge=0), limit: int = Query(24, ge=1, le=120)):
    return store.page("outputs", offset, limit)


@app.delete("/api/outputs/{job_id}")
def delete_output(job_id: UUID):
    trash = None
    try:
        with store.database() as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("""SELECT product_id FROM jobs
                                WHERE id=? AND status='done' AND deleted_at IS NULL""", (job_id.hex,)).fetchone()
            if not job:
                raise HTTPException(404, "找不到输出图")
            if not store.can_delete_output(db, job["product_id"]):
                raise HTTPException(409, "产品任务仍需要该输出图，请在流程结束后删除")
            if job["product_id"] and store.has_active_export(db, job["product_id"]):
                raise HTTPException(409, "该输出图正在等待导出或打包，请等导出完成后再删除")
            trash = stash_output_folder(job_id.hex)
            store.mark_output_deleted(db, job_id.hex)
    except Exception:
        if trash is not None:
            try:
                trash.rename(DATA / "outputs" / job_id.hex)
            except OSError:
                pass
        raise
    if trash is not None:
        shutil.rmtree(trash, ignore_errors=True)
    return {"deleted": True}


@app.get("/api/outputs/{job_id}/{kind}")
def output_image(job_id: UUID, kind: str, slice_name: str | None = None):
    job = store.get(job_id.hex)
    if not job or job["status"] != "done" or job.get("deleted_at"):
        raise HTTPException(404, "找不到输出图")
    folder = DATA / "outputs" / job_id.hex
    name = store.download_name(job) if kind == "download" else None
    if kind == "thumbnail":
        path = folder / "thumb.png"
    elif job["kind"] == "detail":
        if kind == "download":
            slices = store.detail_slices(folder)
            if not slices:
                raise HTTPException(404, "详情页切片已移除")
            try:
                return zip_response(((path, path.name) for path in slices), name)
            except FileNotFoundError as exc:
                raise HTTPException(404, "详情页切片已移除") from exc
        if kind != "image" or (slice_name is not None and not re.fullmatch(r"[0-9]{3,}\.png", slice_name)):
            raise HTTPException(404, "找不到详情页切片")
        path = folder / "slices" / (slice_name or "001.png")
    elif kind == "transparent" and job["kind"] == "mockup":
        path = folder / "subject.png"
        name = PureWindowsPath(store.download_name(job)).stem + "-透明图.png"
    elif kind in {"image", "download"}:
        path = folder / ("subject.png" if job["kind"] == "subject" else "render.png")
    else:
        raise HTTPException(404, "找不到输出图")
    if not path.is_file():
        raise HTTPException(404, "输出文件已移除")
    return FileResponse(path, media_type="image/png", filename=name)
