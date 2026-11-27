import json
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

from app.config import DATA, QUEUE, revision

TERMINAL = {"done", "skipped", "failed", "cancelled"}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@contextmanager
def database():
    db = sqlite3.connect(DATA / "studio.sqlite3", timeout=20)
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize(*, migrate_queue=True):
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "inputs").mkdir(exist_ok=True)
    (DATA / "outputs").mkdir(exist_ok=True)
    with database() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, batch TEXT NOT NULL, template TEXT NOT NULL, revision TEXT NOT NULL,
                settings TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
                next_dispatch REAL NOT NULL DEFAULT 0, deleted_at TEXT
            );
            CREATE INDEX IF NOT EXISTS job_dispatch ON jobs(status, next_dispatch);
            CREATE INDEX IF NOT EXISTS job_created ON jobs(created_at);
            CREATE TABLE IF NOT EXISTS delivery_route (
                id INTEGER PRIMARY KEY CHECK (id = 1), queue TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS template_rotation (
                id INTEGER PRIMARY KEY CHECK (id = 1), last_template TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                at TEXT NOT NULL, level TEXT NOT NULL, message TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS products (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL, saved_at TEXT,
                downloaded_at TEXT, deleted_at TEXT, cleanup_pending INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS exports (
                id TEXT PRIMARY KEY, filename TEXT NOT NULL, product_count INTEGER NOT NULL,
                manifest TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
                finished_at TEXT, downloaded_at TEXT, size INTEGER,
                error TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS export_status ON exports(status, created_at);
            CREATE TABLE IF NOT EXISTS export_products (
                export_id TEXT NOT NULL, product_id TEXT NOT NULL,
                PRIMARY KEY (export_id, product_id)
            );
            CREATE INDEX IF NOT EXISTS export_product ON export_products(product_id);
            CREATE TABLE IF NOT EXISTS storage_reset (
                id INTEGER PRIMARY KEY CHECK (id = 1), cleared_at TEXT NOT NULL
            );
        """)
        db.execute("BEGIN IMMEDIATE")
        columns = {row["name"] for row in db.execute("PRAGMA table_info(jobs)")}
        if "deleted_at" not in columns:
            db.execute("ALTER TABLE jobs ADD COLUMN deleted_at TEXT")
        if "product_id" not in columns:
            db.execute("ALTER TABLE jobs ADD COLUMN product_id TEXT")
        if "kind" not in columns:
            db.execute("ALTER TABLE jobs ADD COLUMN kind TEXT NOT NULL DEFAULT 'mockup'")
        product_columns = {row["name"] for row in db.execute("PRAGMA table_info(products)")}
        if "downloaded_at" not in product_columns:
            db.execute("ALTER TABLE products ADD COLUMN downloaded_at TEXT")
        if "deleted_at" not in product_columns:
            db.execute("ALTER TABLE products ADD COLUMN deleted_at TEXT")
        if "cleanup_pending" not in product_columns:
            db.execute("ALTER TABLE products ADD COLUMN cleanup_pending INTEGER NOT NULL DEFAULT 0")
        db.execute("CREATE INDEX IF NOT EXISTS job_product ON jobs(product_id)")
        db.execute("CREATE INDEX IF NOT EXISTS job_batch ON jobs(batch)")
        if migrate_queue:
            previous = db.execute("SELECT queue FROM delivery_route WHERE id=1").fetchone()
            if previous is None or previous["queue"] != QUEUE:
                message = "队列已迁移，等待重新投递到本机 worker"
                db.execute("""INSERT INTO events(job_id,at,level,message)
                              SELECT id,?,'info',? FROM jobs WHERE status='queued'""", (now(), message))
                db.execute("""UPDATE jobs SET status='pending', next_dispatch=0, updated_at=?, detail=?
                              WHERE status='queued'""", (now(), message))
                db.execute("INSERT OR REPLACE INTO delivery_route VALUES (1,?)", (QUEUE,))


def event(db, job_id, message, level="info"):
    db.execute("INSERT INTO events(job_id, at, level, message) VALUES (?,?,?,?)",
               (job_id, now(), level, message))


def clear_records():
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        for table in ("export_products", "exports", "events", "jobs", "products", "template_rotation"):
            db.execute(f"DELETE FROM {table}")
        db.execute("DELETE FROM sqlite_sequence WHERE name='events'")
        db.execute("INSERT OR REPLACE INTO storage_reset VALUES (1,?)", (now(),))


def compact_database():
    with database() as db:
        db.execute("VACUUM")
        busy, *_ = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        return not busy


def delivery_was_cleared(db, created_at):
    row = db.execute("SELECT cleared_at FROM storage_reset WHERE id=1").fetchone()
    return bool(row and (created_at is None or created_at <= row["cleared_at"]))


def download_name(job):
    if "product_name" in job.keys() and job["kind"] == "detail" and job["product_name"]:
        return f"{job['product_name']}-详情页.zip"
    custom = json.loads(job["settings"]).get("download_name")
    stem = job["template"].rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return custom or f"{stem}-{job['id'][:8]}.png"


def rotate_template(db, selected):
    # Call inside a write transaction so the rotation only advances on commit.
    row = db.execute("SELECT last_template FROM template_rotation WHERE id=1").fetchone()
    names = list(selected)
    index = names.index(row["last_template"]) + 1 if row and row["last_template"] in selected else 0
    name = names[index % len(names)]
    db.execute("INSERT OR REPLACE INTO template_rotation(id,last_template) VALUES (1,?)", (name,))
    return name


def insert_job(db, job_id, batch, template, template_revision, settings, *,
               product_id=None, kind="mockup", status="pending", message="任务已保存，等待投递"):
    stamp = now()
    db.execute("""INSERT INTO jobs(id,batch,template,revision,settings,status,created_at,updated_at,
                                   detail,product_id,kind)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
               (job_id, batch, template, template_revision, json.dumps(settings), status, stamp, stamp,
                message, product_id, kind))
    event(db, job_id, message)


def submit(batch, selected, settings, *, sequential=False, output_name=""):
    ids = []
    with database() as db:
        if sequential:
            # Advance only when this submission commits, including across API workers.
            db.execute("BEGIN IMMEDIATE")
            name = rotate_template(db, selected)
            selected = {name: selected[name]}
        for index, (name, path) in enumerate(selected.items(), start=1):
            job_id = uuid4().hex
            job_settings = settings
            if output_name:
                suffix = f"-{index:02d}" if len(selected) > 1 else ""
                job_settings = {**settings, "download_name": f"{output_name}{suffix}.png"}
            insert_job(db, job_id, batch, name, revision(path), job_settings)
            ids.append(job_id)
    return ids


def next_delivery():
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("""SELECT id FROM jobs WHERE status='pending' AND next_dispatch <= ?
                            ORDER BY created_at, rowid LIMIT 1""", (time.time(),)).fetchone()
        if row:
            db.execute("UPDATE jobs SET next_dispatch=? WHERE id=?", (time.time() + 60, row["id"]))
            return row["id"]


def transition(db, job_id, status, message, level="info", *, expected=None):
    query = "UPDATE jobs SET status=?, detail=?, updated_at=? WHERE id=?"
    values = [status, message, now(), job_id]
    if expected:
        query += " AND status=?"
        values.append(expected)
    if db.execute(query, values).rowcount:
        event(db, job_id, message, level)


def mark_output_deleted(db, job_id):
    row = db.execute(
        "SELECT detail FROM jobs WHERE id=? AND status='done' AND deleted_at IS NULL", (job_id,)
    ).fetchone()
    if not row:
        return False
    detail = row["detail"]
    if "输出图已删除" not in detail:
        detail = f"{detail}；输出图已删除" if detail else "输出图已删除"
    stamp = now()
    updated = db.execute(
        """UPDATE jobs SET deleted_at=?, updated_at=?, detail=?
           WHERE id=? AND status='done' AND deleted_at IS NULL""",
        (stamp, stamp, detail, job_id),
    )
    if not updated.rowcount:
        return False
    event(db, job_id, "输出图已删除")
    return True


def dispatch_error(job_id, exc):
    message = f"投递到 Redis 失败（{type(exc).__name__}），15 秒后重试"
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT detail FROM jobs WHERE id=? AND status='pending'", (job_id,)).fetchone()
        if row:
            db.execute("UPDATE jobs SET next_dispatch=?, detail=? WHERE id=?",
                       (time.time() + 15, message, job_id))
            if row["detail"] != message:
                event(db, job_id, message, "warning")


def get(job_id):
    with database() as db:
        row = db.execute("""SELECT j.*,p.name AS product_name FROM jobs j
                            LEFT JOIN products p ON p.id=j.product_id WHERE j.id=?""", (job_id,)).fetchone()
        return dict(row) if row else None


def detail_slices(folder):
    return sorted((path for path in (folder / "slices").glob("*.png")
                   if re.fullmatch(r"[0-9]{3,}\.png", path.name)), key=lambda path: int(path.stem))


def can_delete_output(db, product_id):
    if not product_id:
        return True
    return bool(db.execute("""SELECT 1 FROM jobs WHERE product_id=? AND kind='detail'
                              AND status IN ('done','failed','skipped')""", (product_id,)).fetchone())


def has_active_export(db, product_id):
    return bool(db.execute("""SELECT 1 FROM export_products ep JOIN exports e ON e.id=ep.export_id
                              WHERE ep.product_id=? AND e.status IN ('pending','running') LIMIT 1""",
                           (product_id,)).fetchone())


def page(kind, offset, limit):
    with database() as db:
        if kind == "logs":
            table = "events e LEFT JOIN jobs j ON j.id=e.job_id LEFT JOIN products p ON p.id=j.product_id"
            fields = "e.id,e.job_id,j.template,j.kind,p.name AS product_name,e.at,e.level,e.message"
            order = "e.id DESC"
        else:
            table = "jobs j LEFT JOIN products p ON p.id=j.product_id"
            if kind == "outputs":
                table += " WHERE j.status='done' AND j.deleted_at IS NULL"
            fields = ("j.id,j.template,j.kind,j.settings,j.status,j.created_at,j.updated_at,j.detail,"
                      "j.product_id,p.name AS product_name")
            order = "j.created_at DESC, j.rowid DESC"
        count = db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        rows = db.execute(f"SELECT {fields} FROM {table} ORDER BY {order} LIMIT ? OFFSET ?",
                          (limit, offset)).fetchall()
        items = [dict(row) for row in rows]
        if kind == "outputs":
            for item in items:
                item["can_delete"] = can_delete_output(db, item["product_id"])
    if kind == "logs":
        return {"items": items, "total": count}
    for item in items:
        settings = json.loads(item["settings"])
        item["sku_name"] = settings.get("sku_name")
        if kind == "outputs":
            item["download_name"] = download_name(item)
            folder = DATA / "outputs" / item["id"]
            item["has_transparent"] = item["kind"] == "mockup" and (folder / "subject.png").is_file()
            if item["kind"] == "detail":
                item["slices"] = [path.name for path in detail_slices(folder)]
        item.pop("settings")
    return {"items": items, "total": count}
