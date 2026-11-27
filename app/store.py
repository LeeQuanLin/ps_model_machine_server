import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

from app.config import DATA, QUEUE, revision


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
        """)
        db.execute("BEGIN IMMEDIATE")
        columns = {row["name"] for row in db.execute("PRAGMA table_info(jobs)")}
        if "deleted_at" not in columns:
            db.execute("ALTER TABLE jobs ADD COLUMN deleted_at TEXT")
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


def download_name(job):
    custom = json.loads(job["settings"]).get("download_name")
    stem = job["template"].rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return custom or f"{stem}-{job['id'][:8]}.png"


def submit(batch, selected, settings, *, sequential=False, output_name=""):
    ids = []
    stamp = now()
    with database() as db:
        if sequential:
            # Advance only when this submission commits, including across API workers.
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT last_template FROM template_rotation WHERE id=1").fetchone()
            names = list(selected)
            index = names.index(row["last_template"]) + 1 if row and row["last_template"] in selected else 0
            name = names[index % len(names)]
            selected = {name: selected[name]}
            db.execute("INSERT OR REPLACE INTO template_rotation(id,last_template) VALUES (1,?)", (name,))
        for index, (name, path) in enumerate(selected.items(), start=1):
            job_id = uuid4().hex
            job_settings = settings
            if output_name:
                suffix = f"-{index:02d}" if len(selected) > 1 else ""
                job_settings = {**settings, "download_name": f"{output_name}{suffix}.png"}
            db.execute("""INSERT INTO jobs(id,batch,template,revision,settings,status,created_at,updated_at)
                          VALUES (?,?,?,?,?,'pending',?,?)""",
                       (job_id, batch, name, revision(path), json.dumps(job_settings), stamp, stamp))
            event(db, job_id, "任务已保存，等待投递")
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
        row = db.execute("SELECT detail FROM jobs WHERE id=? AND status='pending'", (job_id,)).fetchone()
        if row:
            db.execute("UPDATE jobs SET next_dispatch=?, detail=? WHERE id=?",
                       (time.time() + 15, message, job_id))
            if row["detail"] != message:
                event(db, job_id, message, "warning")


def get(job_id):
    with database() as db:
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None


def page(kind, offset, limit):
    with database() as db:
        if kind == "logs":
            table = "events e LEFT JOIN jobs j ON j.id=e.job_id"
            fields = "e.id,e.job_id,j.template,e.at,e.level,e.message"
            order = "e.id DESC"
        else:
            table = "jobs" + (" WHERE status='done' AND deleted_at IS NULL" if kind == "outputs" else "")
            fields = ("id,template,settings,status,created_at,updated_at,detail"
                      if kind == "outputs" else "id,template,status,created_at,updated_at,detail")
            order = "created_at DESC, rowid DESC"
        count = db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        rows = db.execute(f"SELECT {fields} FROM {table} ORDER BY {order} LIMIT ? OFFSET ?",
                          (limit, offset)).fetchall()
        items = [dict(row) for row in rows]
    if kind == "outputs":
        for item in items:
            item["download_name"] = download_name(item)
            item.pop("settings", None)
    return {"items": items, "total": count}
