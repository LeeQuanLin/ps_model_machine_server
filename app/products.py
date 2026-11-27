import json
import secrets
from collections import defaultdict
from uuid import uuid4

from app import store
from app.config import TEMPLATES, revision


class ProductConflict(ValueError):
    pass


def find(db, product_id):
    return db.execute("""SELECT * FROM products WHERE id=? AND deleted_at IS NULL""",
                      (product_id,)).fetchone()


def product_jobs(db, product_id):
    return db.execute("SELECT * FROM jobs WHERE product_id=? ORDER BY rowid", (product_id,)).fetchall()


def create(product_id, name):
    with store.database() as db:
        db.execute("INSERT OR IGNORE INTO products(id,name,created_at) VALUES (?,?,?)",
                   (product_id, name, store.now()))
        product = find(db, product_id)
        if product is None:
            raise LookupError("找不到产品")
        return summarize(product, product_jobs(db, product_id))


def rename(product_id, name):
    with store.database() as db:
        if not db.execute("UPDATE products SET name=? WHERE id=? AND deleted_at IS NULL",
                          (name, product_id)).rowcount:
            raise LookupError("找不到产品")
        return {"id": product_id, "name": name}


def delete(product_id):
    with store.database() as db:
        db.execute("BEGIN IMMEDIATE")
        product = db.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
        if product is None:
            raise LookupError("找不到产品")
        if product["deleted_at"]:
            return
        jobs = product_jobs(db, product_id)
        if any(job["status"] == "running" for job in jobs):
            raise ProductConflict("产品正在出图，请等当前任务结束后再删除")
        if store.has_active_export(db, product_id):
            raise ProductConflict("产品正在等待导出或打包，请等导出完成后再删除")
        stamp = store.now()
        db.execute("UPDATE products SET deleted_at=?,cleanup_pending=1 WHERE id=?", (stamp, product_id))
        for job in jobs:
            if job["status"] not in store.TERMINAL:
                store.transition(db, job["id"], "cancelled", "产品已删除，任务已取消")
            elif job["status"] == "done" and not job["deleted_at"]:
                store.mark_output_deleted(db, job["id"])
        store.event(db, "", f"产品「{product['name']}」已删除")


def add_sku(product_id, sku_id, batch, name, template, available, settings):
    with store.database() as db:
        db.execute("BEGIN IMMEDIATE")
        product = find(db, product_id)
        if product is None:
            raise LookupError("找不到产品")
        jobs = product_jobs(db, product_id)
        if any(job["id"] == sku_id for job in jobs):
            return summarize(product, jobs), False
        if product["saved_at"]:
            raise ProductConflict("产品已保存，无法添加 SKU")
        if any(json.loads(job["settings"])["sku_name"].casefold() == name.casefold() for job in jobs):
            raise ProductConflict("同一个产品下的 SKU 名不能重复")
        if template == "random":
            template = secrets.choice(tuple(available))
        elif template == "sequential":
            template = store.rotate_template(db, available)
        store.insert_job(db, sku_id, batch, template, revision(available[template]),
                         {**settings, "sku_name": name, "download_name": f"{name}.png"},
                         product_id=product_id)
        return summarize(product, product_jobs(db, product_id)), True


def save(product_id, name, detail_path):
    with store.database() as db:
        db.execute("BEGIN IMMEDIATE")
        product = find(db, product_id)
        if product is None:
            raise LookupError("找不到产品")
        jobs = product_jobs(db, product_id)
        if product["saved_at"]:
            return summarize(product, jobs), [job["batch"] for job in jobs if job["kind"] == "mockup"]
        if not jobs:
            raise ProductConflict("请先添加至少一个 SKU")
        if detail_path is None:
            raise ProductConflict("详情页模板不存在，请检查 DETAIL_PAGE_TEMPLATE 配置")
        # Prefer SKUs that can still produce a composite if any have already failed.
        source = secrets.choice([job for job in jobs if job["status"] not in {"failed", "skipped"}] or jobs)
        sku_name = json.loads(source["settings"])["sku_name"]
        detail_name = detail_path.relative_to(TEMPLATES).as_posix()
        subject_id, detail_id = uuid4().hex, uuid4().hex
        store.insert_job(db, subject_id, source["batch"], source["template"], source["revision"],
                         {**json.loads(source["settings"]), "source_id": source["id"],
                          "download_name": f"{sku_name}-透明图.png"},
                         product_id=product_id, kind="subject", status="blocked",
                         message=f"已抽中「{sku_name}」生成透明图，等待它的样机完成")
        store.insert_job(db, detail_id, detail_id, detail_name, revision(detail_path),
                         {"source_ids": [job["id"] for job in jobs], "download_name": f"{name}-详情页.zip"},
                         product_id=product_id, kind="detail", status="blocked",
                         message="等待全部 SKU 样机和透明图完成")
        db.execute("UPDATE products SET name=?,saved_at=? WHERE id=?", (name, store.now(), product_id))
        advance(db, product_id)
        return summarize(find(db, product_id), product_jobs(db, product_id)), [job["batch"] for job in jobs]


def advance(db, product_id):
    """Promote dependent jobs in the same transaction as the predecessor's state change."""
    jobs = product_jobs(db, product_id)
    subject = next((job for job in jobs if job["kind"] == "subject"), None)
    if subject is None:
        return
    detail = next(job for job in jobs if job["kind"] == "detail")
    sku_statuses = {job["id"]: job["status"] for job in jobs if job["kind"] == "mockup"}
    subject_status = subject["status"]
    if subject_status == "blocked":
        source_status = sku_statuses[json.loads(subject["settings"])["source_id"]]
        if source_status == "done":
            store.transition(db, subject["id"], "pending", "选中的 SKU 已出图，等待生成透明图")
            subject_status = "pending"
        elif source_status in store.TERMINAL:
            store.transition(db, subject["id"], "skipped", "选中的 SKU 未出图，无法生成透明图", "warning")
            subject_status = "skipped"
    if detail["status"] != "blocked":
        return
    prerequisites = set(sku_statuses.values()) | {subject_status}
    if prerequisites == {"done"}:
        store.transition(db, detail["id"], "pending", "全部 SKU 样机和透明图已完成，等待制作详情页")
    elif prerequisites <= store.TERMINAL:
        store.transition(db, detail["id"], "skipped", "有 SKU 或透明图未成功生成，已跳过详情页", "warning")


def summarize(product, jobs):
    skus = [job for job in jobs if job["kind"] == "mockup"]
    first_sku = skus[0] if skus else None
    thumbnail_job_id = None
    if first_sku and first_sku["status"] == "done" and first_sku["deleted_at"] is None:
        thumbnail_job_id = first_sku["id"]
    subject = next((job for job in jobs if job["kind"] == "subject"), None)
    detail = next((job for job in jobs if job["kind"] == "detail"), None)
    completed = sum(job["status"] == "done" for job in skus)
    failed = next((job for job in jobs if job["status"] in {"failed", "skipped"}), None)
    download = False
    if not product["saved_at"]:
        status = "draft"
    elif detail["status"] == "done":
        download = all(job["deleted_at"] is None for job in jobs)
        status = "done" if download else "removed"
    elif detail["status"] in store.TERMINAL:
        status = "failed"
    elif any(job["status"] not in store.TERMINAL for job in skus):
        status = "mockups"
    elif subject["status"] not in store.TERMINAL:
        status = "subject"
    else:
        status = "detail"
    return {"id": product["id"], "name": product["name"], "created_at": product["created_at"],
            "saved_at": product["saved_at"], "downloaded_at": product["downloaded_at"],
            "status": status, "sku_count": len(skus), "sku_done": completed,
            "downloadable": download, "thumbnail_job_id": thumbnail_job_id,
            "message": failed["detail"] if failed else "",
            "skus": [{"id": job["id"], "name": json.loads(job["settings"])["sku_name"],
                      "template": job["template"], "status": job["status"], "message": job["detail"]}
                     for job in skus]}


def get(product_id):
    with store.database() as db:
        db.execute("BEGIN")
        product = find(db, product_id)
        return summarize(product, product_jobs(db, product_id)) if product else None


def page(offset, limit):
    with store.database() as db:
        db.execute("BEGIN")
        total = db.execute("SELECT count(*) FROM products WHERE deleted_at IS NULL").fetchone()[0]
        rows = db.execute("""SELECT * FROM products WHERE deleted_at IS NULL
                             ORDER BY created_at DESC,rowid DESC LIMIT ? OFFSET ?""",
                          (limit, offset)).fetchall()
        jobs = defaultdict(list)
        if rows:
            placeholders = ",".join("?" for _ in rows)
            for row in db.execute(f"SELECT * FROM jobs WHERE product_id IN ({placeholders}) ORDER BY rowid",
                                  tuple(row["id"] for row in rows)):
                jobs[row["product_id"]].append(row)
        items = [summarize(row, jobs[row["id"]]) for row in rows]
    for item in items:
        item.pop("skus")
    return {"items": items, "total": total}
