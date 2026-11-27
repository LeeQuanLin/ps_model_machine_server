import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import 详情页生成 as detail


class DetailPageTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.template = self.root / "详情模板.psb"
        self.template.write_bytes(b"template fixture")
        self.output = self.root / "新品切片"

    def test_order_default_height_and_extra_input(self):
        images = [self.root / name for name in ["图 B.png", "图 A.png", "多余.png"]]

        def photoshop(command, *, env, **kwargs):
            jsx = Path(env["DETAIL_PAGE_JSX"]).read_text(encoding="utf-8-sig")
            job = json.loads(jsx.splitlines()[1].removeprefix("var job = ").removesuffix(";"))
            self.assertEqual(job["images"], list(map(str, images)))
            self.assertEqual(job["height"], 960)
            self.assertNotEqual(Path(job["template"]), self.template)
            self.assertEqual(Path(job["template"]).read_bytes(), self.template.read_bytes())
            for index in (1, 2):
                (Path(job["output"]) / f"{index:03d}.png").write_bytes(b"test slice")
            return subprocess.CompletedProcess(command, 0, "OK:2:2:2", "")

        with mock.patch.object(detail.subprocess, "run", side_effect=photoshop):
            self.assertEqual(detail.generate(self.template, self.output, images), (2, 2, 2))
        self.assertEqual(sorted(p.name for p in self.output.iterdir()), ["001.png", "002.png"])

    def test_incomplete_export_is_not_published(self):
        with mock.patch.object(detail.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, "OK:1:6:24", "")):
            with self.assertRaisesRegex(RuntimeError, "切片不完整"):
                detail.generate(self.template, self.output, ["图.png"], 600)
        self.assertEqual(list(self.root.iterdir()), [self.template])

    def test_existing_output_and_invalid_height(self):
        with self.assertRaisesRegex(ValueError, "正整数"):
            detail.generate(self.template, self.output, ["图.png"], 0)
        self.output.mkdir()
        with self.assertRaises(FileExistsError):
            detail.generate(self.template, self.output, ["图.png"])


@unittest.skipUnless("--photoshop" in sys.argv or os.environ.get("DETAIL_PAGE_PS_TEST") == "1",
                     "使用 --photoshop 运行桌面出图测试")
class DesktopExportTest(unittest.TestCase):
    def test_export_project_template(self):
        template = ROOT / "templates" / "详情页模板.psd"
        # Use a new folder for each run so the exported PNGs can be inspected afterwards.
        parent = ROOT / "data" / "detail-page-test"
        parent.mkdir(parents=True, exist_ok=True)
        folder = Path(tempfile.mkdtemp(prefix="run-", dir=parent))
        images = []
        colors = ["#ff4040", "#3070ff", "#20b060", "#ff9d20", "#9944dd", "#00b8b8"]
        for index, color in enumerate(colors, 1):
            path = folder / f"input-{index}.png"
            with Image.new("RGB", (600, 600), color) as card:
                draw = ImageDraw.Draw(card)
                # The bars identify the replacement even after cropping or template effects.
                for bar in range(index):
                    x = 180 + 40 * bar
                    draw.rectangle((x, 220, x + 18, 380), fill="white")
                card.save(path)
            images.append(path)
        slices = folder / "slices"
        # The seventh path deliberately does not exist. Only consumed inputs may be opened.
        result = subprocess.run(
            [sys.executable, str(ROOT / "详情页生成.py"), str(template), "-o", str(slices),
             "-i", *map(str, images), str(folder / "多余.png")],
            capture_output=True, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("替换 6/6", result.stdout)
        self.assertIn("忽略 1 张", result.stdout)
        with Image.open(template) as original:
            width, height = original.size
        expected = [(width, min(960, height - top)) for top in range(0, height, 960)]
        self.assertEqual(len(list(slices.glob("*.png"))), len(expected))
        for index, size in enumerate(expected, 1):
            with Image.open(slices / f"{index:03d}.png") as image:
                self.assertEqual(image.format, "PNG")
                self.assertEqual(image.size, size)
                image.verify()
        print(f"测试切片：{slices}")


if __name__ == "__main__":
    unittest.main(argv=[arg for arg in sys.argv if arg != "--photoshop"])
