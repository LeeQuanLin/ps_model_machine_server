import unittest
from pathlib import Path

from app.config import queue_name


class QueueNameTest(unittest.TestCase):
    def test_stable_for_same_workspace(self):
        self.assertEqual(
            queue_name("HOST-A", Path("C:/Projects/studio")),
            queue_name("host-a", Path("C:/Projects/studio")),
        )

    def test_differs_by_hostname(self):
        self.assertNotEqual(
            queue_name("HOST-A", Path("C:/Projects/studio")),
            queue_name("HOST-B", Path("C:/Projects/studio")),
        )

    def test_differs_by_data_directory(self):
        self.assertNotEqual(
            queue_name("HOST-A", Path("C:/Projects/studio-a")),
            queue_name("HOST-A", Path("C:/Projects/studio-b")),
        )

    def test_keeps_the_project_prefix(self):
        self.assertTrue(queue_name("HOST-A", Path("C:/Projects/studio")).startswith("photoshop.mockups."))


if __name__ == "__main__":
    unittest.main()
