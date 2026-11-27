import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import HTTPException

from app import main, products, storage_cleanup, store, worker


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._data = main.DATA
        main.DATA = Path(self.tmp.name)

    def tearDown(self):
        main.DATA = self._data
        self.tmp.cleanup()

    def test_reports_disk_and_project_usage(self):
        nested = Path(self.tmp.name) / "nested"
        nested.mkdir()
        (nested / "sample.bin").write_bytes(b"x" * 12345)

        data = main.storage()

        self.assertTrue(data["drive"])
        self.assertGreater(data["total"], 0)
        self.assertGreaterEqual(data["free"], 0)
        self.assertLessEqual(data["free"], data["total"])
        self.assertEqual(data["project_used"], 12345)
        self.assertNotIn("used", data)

    def test_unreadable_disk_returns_503(self):
        with mock.patch.object(main.shutil, "disk_usage", side_effect=OSError("unavailable")):
            with self.assertRaises(HTTPException) as raised:
                main.storage()

        self.assertEqual(raised.exception.status_code, 503)

    def test_reset_clears_workbench_files_and_records_and_can_accept_new_jobs(self):
        data = main.DATA
        with mock.patch.object(store, "DATA", data), mock.patch.object(worker, "DATA", data):
            store.initialize()
            products.create("old-product", "旧产品")
            with store.database() as db:
                store.insert_job(db, "1" * 32, "batch", "template.psd", "rev", {})
                db.execute("INSERT INTO exports(id,filename,product_count,manifest,status,created_at) "
                           "VALUES ('export','sample.zip',1,'[]','pending',?)", (store.now(),))
                db.execute("INSERT INTO export_products VALUES ('export','old-product')")
                db.execute("INSERT INTO template_rotation VALUES (1,'template.psd')")
            old = store.get("1" * 32)
            for name in storage_cleanup.DATA_FOLDERS:
                folder = data / name
                folder.mkdir(exist_ok=True)
                (folder / "old.bin").write_bytes(b"x" * 100_000)
            temporary_zip = data / "download-test.zip"
            temporary_zip.write_bytes(b"old zip")
            (data / "logs").mkdir()
            logs = data / "logs" / "api.log"
            logs.write_text("process log")

            result = main.clear_storage(main.StorageClearConfirmation(confirm="clear-all"))

            self.assertEqual(result["remaining"], [])
            self.assertGreaterEqual(result["released_bytes"], 300_000)
            for name in storage_cleanup.DATA_FOLDERS:
                self.assertEqual(list((data / name).iterdir()), [])
            self.assertFalse(temporary_zip.exists())
            self.assertTrue(logs.is_file())
            self.assertTrue((data / "studio.sqlite3").is_file())
            with store.database() as db:
                for table in ("jobs", "products", "exports", "export_products", "template_rotation", "events"):
                    self.assertEqual(db.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)
            worker.generate.run(old["id"], old["created_at"])
            worker.generate.run(old["id"])
            self.assertEqual(store.page("logs", 0, 10)["total"], 0)
            self.assertEqual(products.create("new-product", "新产品")["name"], "新产品")

    def test_reset_preserves_data_while_rendering_or_exporting(self):
        data = main.DATA
        with mock.patch.object(store, "DATA", data):
            store.initialize()
            products.create("existing", "保留")
            for lock in ("photoshop.lock", "exports.lock"):
                with worker.job_lock(data / lock) as locked:
                    self.assertTrue(locked)
                    with self.assertRaises(HTTPException) as raised:
                        main.clear_storage(main.StorageClearConfirmation(confirm="clear-all"))
                self.assertEqual(raised.exception.status_code, 409)
                self.assertEqual(products.get("existing")["name"], "保留")

    def test_reset_reports_busy_files_and_can_be_retried(self):
        data = main.DATA
        with mock.patch.object(store, "DATA", data):
            store.initialize()
            products.create("existing", "待删")
            with (
                mock.patch.object(storage_cleanup.shutil, "rmtree", side_effect=PermissionError("busy")),
                self.assertLogs(storage_cleanup.__name__, "ERROR"),
            ):
                result = main.clear_storage(main.StorageClearConfirmation(confirm="clear-all"))
            self.assertCountEqual(result["remaining"], ["上传缓存", "输出文件"])
            self.assertIsNone(products.get("existing"))
            result = main.clear_storage(main.StorageClearConfirmation(confirm="clear-all"))
            self.assertEqual(result["remaining"], [])


if __name__ == "__main__":
    unittest.main()
