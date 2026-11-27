import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from uuid import UUID, uuid4

from fastapi import HTTPException

from app import main, store


class OutputDeleteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "studio"
        self.data.mkdir()
        self._store_data = store.DATA
        self._main_data = main.DATA
        store.DATA = self.data
        main.DATA = self.data
        store.initialize()
        self.db_path = self.data / "studio.sqlite3"

    def tearDown(self):
        store.DATA = self._store_data
        main.DATA = self._main_data
        self.tmp.cleanup()

    def _insert_job(self, job_id, status="done"):
        db = sqlite3.connect(self.db_path)
        try:
            db.execute(
                """INSERT INTO jobs(id,batch,template,revision,settings,status,created_at,updated_at,detail,next_dispatch)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (job_id, "batch", "template.psd", "1:2", '{"colors":{},"images":{}}', status,
                 "2026-01-01T00:00:00.000+00:00", "2026-01-01T00:00:00.000+00:00", "已生成：B面、背景", 0),
            )
            db.commit()
        finally:
            db.close()

    def _make_output(self, job_id):
        folder = self.data / "outputs" / job_id
        folder.mkdir()
        (folder / "render.png").write_bytes(b"render")
        (folder / "thumb.png").write_bytes(b"thumb")
        return folder

    def test_delete_output_removes_files_and_hides_only_outputs(self):
        job_id = uuid4().hex
        self._insert_job(job_id)
        folder = self._make_output(job_id)

        result = main.delete_output(UUID(job_id))

        self.assertEqual(result, {"deleted": True})
        self.assertFalse(folder.exists())
        self.assertEqual(list((self.data / "outputs").glob(".deleted-*")), [])
        job = store.get(job_id)
        self.assertIsNotNone(job["deleted_at"])
        self.assertIn("输出图已删除", job["detail"])
        self.assertEqual(store.page("outputs", 0, 10)["total"], 0)
        self.assertEqual(store.page("jobs", 0, 10)["total"], 1)
        with self.assertRaises(HTTPException) as raised:
            main.output_image(UUID(job_id), "image")
        self.assertEqual(raised.exception.status_code, 404)

    def test_delete_missing_or_already_deleted_output_returns_404(self):
        job_id = uuid4().hex
        self._insert_job(job_id)
        self._make_output(job_id)
        main.delete_output(UUID(job_id))

        with self.assertRaises(HTTPException) as raised:
            main.delete_output(UUID(job_id))
        self.assertEqual(raised.exception.status_code, 404)

    def test_running_job_cannot_be_deleted(self):
        job_id = uuid4().hex
        self._insert_job(job_id, status="running")
        folder = self._make_output(job_id)

        with self.assertRaises(HTTPException) as raised:
            main.delete_output(UUID(job_id))

        self.assertEqual(raised.exception.status_code, 404)
        self.assertTrue(folder.exists())
        self.assertIsNone(store.get(job_id)["deleted_at"])

    def test_locked_output_returns_409_and_keeps_database(self):
        job_id = uuid4().hex
        self._insert_job(job_id)
        folder = self._make_output(job_id)

        with mock.patch.object(Path, "rename", side_effect=PermissionError("locked")):
            with self.assertRaises(HTTPException) as raised:
                main.delete_output(UUID(job_id))

        self.assertEqual(raised.exception.status_code, 409)
        self.assertTrue(folder.exists())
        self.assertIsNone(store.get(job_id)["deleted_at"])

    def test_missing_output_folder_still_marks_job_deleted(self):
        job_id = uuid4().hex
        self._insert_job(job_id)

        result = main.delete_output(UUID(job_id))

        self.assertEqual(result, {"deleted": True})
        self.assertIsNotNone(store.get(job_id)["deleted_at"])
        self.assertEqual(store.page("outputs", 0, 10)["total"], 0)

    def test_cleanup_output_trash_removes_leftovers(self):
        trash = self.data / "outputs" / ".deleted-old"
        trash.mkdir()
        (trash / "render.png").write_bytes(b"render")

        main.cleanup_output_trash()

        self.assertFalse(trash.exists())


if __name__ == "__main__":
    unittest.main()
