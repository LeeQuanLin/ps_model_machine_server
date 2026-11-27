import json
import logging
from uuid import uuid4
from zipfile import ZipFile

from app import products, store
from app.worker import job_lock


def submit(data, ids):
    export_id = uuid4().hex
    outputs = data / "outputs"
    manifest = []
    used_names = set()
    with store.database() as db:
        db.execute("BEGIN IMMEDIATE")
        for product_id in ids:
            product = products.find(db, product_id)
            if product is None:
                raise LookupError("找不到所选产品")
            jobs = products.product_jobs(db, product_id)
            name = product["name"]
            if not products.summarize(product, jobs)["downloadable"]:
                raise products.ProductConflict(f"「{name}」的输出未完成或已删除")
            prefix = name
            suffix = 2
            while prefix.casefold() in used_names:
                prefix = f"{name} ({suffix})"
                suffix += 1
            used_names.add(prefix.casefold())
            for job in jobs:
                folder = outputs / job["id"]
                if job["kind"] == "detail":
                    slices = store.detail_slices(folder)
                    if not slices:
                        raise products.ProductConflict(f"「{name}」的详情页切片已丢失")
                    manifest.extend((path.relative_to(outputs).as_posix(), f"{prefix}/详情页/{path.name}")
                                    for path in slices)
                else:
                    section, filename = ("透明图", "subject.png") if job["kind"] == "subject" else ("样机图", "render.png")
                    if not (folder / filename).is_file():
                        raise products.ProductConflict(f"「{name}」的{section}已丢失")
                    manifest.append((f"{job['id']}/{filename}", f"{prefix}/{section}/{store.download_name(job)}"))
        filename = f"{name}.zip" if len(ids) == 1 else "产品输出图.zip"
        db.execute("""INSERT INTO exports(id,filename,product_count,manifest,status,created_at)
                      VALUES (?,?,?,?,'pending',?)""",
                   (export_id, filename, len(ids), json.dumps(manifest), store.now()))
        db.executemany("INSERT INTO export_products VALUES (?,?)", ((export_id, item) for item in ids))
    return {"id": export_id, "status": "pending"}


def page(offset, limit):
    with store.database() as db:
        db.execute("BEGIN")
        count = db.execute("SELECT count(*) FROM exports").fetchone()[0]
        rows = db.execute("""SELECT id,filename,product_count,status,created_at,finished_at,downloaded_at,size,error
                             FROM exports ORDER BY created_at DESC,rowid DESC LIMIT ? OFFSET ?""",
                          (limit, offset)).fetchall()
    return {"items": [dict(row) for row in rows], "total": count}


def get(export_id):
    with store.database() as db:
        return db.execute("SELECT filename,status FROM exports WHERE id=?", (export_id,)).fetchone()


def record_download(export_id):
    with store.database() as db:
        db.execute("BEGIN IMMEDIATE")
        stamp = store.now()
        db.execute("UPDATE exports SET downloaded_at=? WHERE id=?", (stamp, export_id))
        db.execute("""UPDATE products SET downloaded_at=? WHERE deleted_at IS NULL AND id IN
                      (SELECT product_id FROM export_products WHERE export_id=?)""", (stamp, export_id))


def archive_path(data, export_id):
    return data / "exports" / f"{export_id}.zip"


def write_archive(path, files):
    # The images are already compressed and can be copied directly into the ZIP.
    with ZipFile(path, "w") as archive:
        for source, name in files:
            archive.write(source, name)


def process_next(data):
    with job_lock(data / "exports.lock") as acquired:
        if not acquired:
            return False
        with store.database() as db:
            record = db.execute("""SELECT id,manifest FROM exports WHERE status IN ('pending','running')
                                   ORDER BY created_at, rowid LIMIT 1""").fetchone()
            if not record:
                return False
            db.execute("UPDATE exports SET status='running' WHERE id=?", (record["id"],))
        target = archive_path(data, record["id"])
        partial = target.with_suffix(".part")
        try:
            target.parent.mkdir(exist_ok=True)
            if not target.is_file():
                files = ((data / "outputs" / path, name) for path, name in json.loads(record["manifest"]))
                write_archive(partial, files)
                partial.replace(target)
            size = target.stat().st_size
        except (OSError, ValueError, RuntimeError):
            logging.getLogger(__name__).exception("导出 %s 打包失败", record["id"])
            try:
                partial.unlink(missing_ok=True)
            except OSError:
                logging.getLogger(__name__).exception("导出 %s 临时文件清理失败", record["id"])
            with store.database() as db:
                db.execute("""UPDATE exports SET status='failed', finished_at=?, error=?
                              WHERE id=?""", (store.now(), "打包失败，请检查输出文件与磁盘空间后重新导出", record["id"]))
        else:
            with store.database() as db:
                db.execute("UPDATE exports SET status='done', finished_at=?, size=? WHERE id=?",
                           (store.now(), size, record["id"]))
        return True


def dispatch(data, stop):
    while not stop.is_set():
        try:
            if not process_next(data):
                stop.wait(1)
        except Exception:
            logging.getLogger(__name__).exception("导出队列处理异常")
            stop.wait(3)
