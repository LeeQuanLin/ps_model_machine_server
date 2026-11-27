import contextlib
import unittest
from unittest import mock

from app import worker


class RequireJobTest(unittest.TestCase):
    def test_missing_job_raises_instead_of_silently_dropping_it(self):
        events = []

        def record_event(db, job_id, message, level="info"):
            events.append((job_id, level, message))

        with mock.patch.object(worker.store, "get", return_value=None), \
                mock.patch.object(worker.store, "database", lambda: contextlib.nullcontext(None)), \
                mock.patch.object(worker.store, "delivery_was_cleared", return_value=False), \
                mock.patch.object(worker.store, "event", side_effect=record_event):
            with self.assertRaises(LookupError):
                worker.require_job("0123456789abcdef0123456789abcdef", "mockup@test-host")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][1], "error")
        self.assertIn("找不到任务", events[0][2])

    def test_existing_job_is_returned(self):
        job = {"id": "job", "status": "queued"}
        with mock.patch.object(worker.store, "get", return_value=job):
            self.assertIs(worker.require_job("job", "mockup@test-host"), job)


if __name__ == "__main__":
    unittest.main()
