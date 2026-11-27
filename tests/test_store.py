import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from app import store

OLD_SCHEMA = """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY, batch TEXT NOT NULL, template TEXT NOT NULL, revision TEXT NOT NULL,
    settings TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
    next_dispatch REAL NOT NULL DEFAULT 0
);
CREATE TABLE template_rotation (
    id INTEGER PRIMARY KEY CHECK (id = 1), last_template TEXT NOT NULL
);
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
    at TEXT NOT NULL, level TEXT NOT NULL, message TEXT NOT NULL
);
"""


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "studio"
        self.data.mkdir()
        self._data = store.DATA
        self._queue = store.QUEUE
        store.DATA = self.data
        store.QUEUE = "photoshop.mockups.test-host"
        self.db_path = self.data / "studio.sqlite3"

    def tearDown(self):
        store.DATA = self._data
        store.QUEUE = self._queue
        self.tmp.cleanup()

    def _create_old_database(self):
        db = sqlite3.connect(self.db_path)
        try:
            db.executescript(OLD_SCHEMA)
            db.commit()
        finally:
            db.close()

    def _insert_job(self, job_id, status, *, next_dispatch=0, settings='{"colors":{},"images":{}}'):
        db = sqlite3.connect(self.db_path)
        try:
            db.execute(
                """INSERT INTO jobs(id,batch,template,revision,settings,status,created_at,updated_at,detail,next_dispatch)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (job_id, "batch", "template.psd", "1:2", settings, status,
                 "2026-01-01T00:00:00.000+00:00", "2026-01-01T00:00:00.000+00:00", "", next_dispatch),
            )
            db.commit()
        finally:
            db.close()

    def test_queue_change_requeues_queued_jobs(self):
        self._create_old_database()
        self._insert_job("queued", "queued")
        self._insert_job("done", "done")

        store.initialize()

        with store.database() as db:
            queued = db.execute("SELECT status,next_dispatch,detail FROM jobs WHERE id='queued'").fetchone()
            done = db.execute("SELECT status FROM jobs WHERE id='done'").fetchone()
            route = db.execute("SELECT queue FROM delivery_route WHERE id=1").fetchone()
            event = db.execute("SELECT level,message FROM events WHERE job_id='queued'").fetchone()
        self.assertEqual(queued["status"], "pending")
        self.assertEqual(queued["next_dispatch"], 0)
        self.assertIn("队列已迁移", queued["detail"])
        self.assertEqual(done["status"], "done")
        self.assertEqual(route["queue"], store.QUEUE)
        self.assertEqual(event["level"], "info")
        self.assertIn("队列已迁移", event["message"])

    def test_unchanged_queue_keeps_new_queued_jobs(self):
        self._create_old_database()
        store.initialize()
        self._insert_job("queued", "queued")

        store.initialize()

        with store.database() as db:
            queued = db.execute("SELECT status FROM jobs WHERE id='queued'").fetchone()
        self.assertEqual(queued["status"], "queued")

    def test_existing_products_have_no_download_time_after_migration(self):
        self._create_old_database()
        with store.database() as db:
            db.executescript("""
                CREATE TABLE products (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL, saved_at TEXT
                );
                INSERT INTO products VALUES ('existing', '已有产品', '2026-11-27', NULL);
            """)
        store.initialize()
        store.initialize()
        with store.database() as db:
            existing = db.execute("SELECT * FROM products WHERE id='existing'").fetchone()
        self.assertEqual(existing["name"], "已有产品")
        self.assertIsNone(existing["downloaded_at"])

    def test_next_delivery_claims_one_job_at_a_time(self):
        self._create_old_database()
        store.initialize()
        self._insert_job("first", "pending")
        self._insert_job("second", "pending")

        claimed = {store.next_delivery(), store.next_delivery()}

        self.assertEqual(claimed, {"first", "second"})
        self.assertIsNone(store.next_delivery())

    def test_next_delivery_respects_backoff(self):
        self._create_old_database()
        store.initialize()
        self._insert_job("later", "pending", next_dispatch=time.time() + 3600)

        self.assertIsNone(store.next_delivery())

    def test_initialize_adds_deleted_at_column_idempotently(self):
        self._create_old_database()

        store.initialize()
        store.initialize()

        with store.database() as db:
            columns = {row["name"] for row in db.execute("PRAGMA table_info(jobs)")}
        self.assertIn("deleted_at", columns)

    def test_outputs_page_exposes_download_name(self):
        self._create_old_database()
        store.initialize()
        custom_id = "0123456789abcdef0123456789abcdef"
        default_id = "fedcba9876543210fedcba9876543210"
        self._insert_job(
            custom_id, "done",
            settings='{"colors":{},"images":{},"download_name":"春日花园-01.png"}',
        )
        self._insert_job(default_id, "done")

        items = {item["id"]: item for item in store.page("outputs", 0, 10)["items"]}

        self.assertEqual(items[custom_id]["download_name"], "春日花园-01.png")
        self.assertEqual(items[default_id]["download_name"], "template-fedcba98.png")
        self.assertNotIn("settings", items[custom_id])

    def test_mark_output_deleted_hides_only_the_outputs_page(self):
        self._create_old_database()
        store.initialize()
        self._insert_job("done", "done")

        with store.database() as db:
            self.assertTrue(store.mark_output_deleted(db, "done"))
        with store.database() as db:
            self.assertFalse(store.mark_output_deleted(db, "done"))

        self.assertEqual(store.page("outputs", 0, 10)["total"], 0)
        self.assertEqual(store.page("jobs", 0, 10)["total"], 1)
        job = store.get("done")
        self.assertIsNotNone(job["deleted_at"])
        self.assertIn("输出图已删除", job["detail"])
        with store.database() as db:
            event = db.execute(
                "SELECT level,message FROM events WHERE job_id='done' AND message='输出图已删除'"
            ).fetchone()
        self.assertEqual(event["level"], "info")


if __name__ == "__main__":
    unittest.main()
