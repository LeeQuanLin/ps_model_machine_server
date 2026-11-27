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

    def _insert_job(self, job_id, status, *, next_dispatch=0):
        db = sqlite3.connect(self.db_path)
        try:
            db.execute(
                """INSERT INTO jobs(id,batch,template,revision,settings,status,created_at,updated_at,detail,next_dispatch)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (job_id, "batch", "template.psd", "1:2", '{"colors":{},"images":{}}', status,
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


if __name__ == "__main__":
    unittest.main()
