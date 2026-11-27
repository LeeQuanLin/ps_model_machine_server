import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import HTTPException

from app import main


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


if __name__ == "__main__":
    unittest.main()
