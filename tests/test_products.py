import tempfile
import unittest
from contextlib import ExitStack
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote
from uuid import UUID, uuid4
from zipfile import ZipFile

from fastapi import HTTPException, UploadFile
from PIL import Image

from app import exports, main, products, store, worker


class ProductWorkflowTest(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.data = root / "studio"
        for module in (main, store, worker):
            stack.enter_context(patch.object(module, "DATA", self.data))
        templates = root / "templates"
        templates.mkdir()
        self.psd = templates / "样机.psd"
        self.psb = templates / "详情.psb"
        for template in (self.psd, self.psb):
            template.write_bytes(b"test fixture")
        stack.enter_context(patch.object(products, "TEMPLATES", templates))
        stack.enter_context(patch.object(main, "templates", return_value={self.psd.name: self.psd}))
        stack.enter_context(patch.object(main, "detail_template", return_value=self.psb))
        stack.enter_context(patch.object(worker, "template_files",
                                         return_value={self.psd.name: self.psd, self.psb.name: self.psb}))
        self.render_modes = []
        self.detail_inputs = []
        stack.enter_context(patch.object(worker, "render", side_effect=self.render_mockup))
        stack.enter_context(patch.object(worker, "generate_detail_page", side_effect=self.render_detail))
        self.product_id = uuid4()
        self.form = main.ProductForm(name="新品")
        store.initialize()
        main.create_product(self.product_id, self.form)

    def render_mockup(self, template, output, pairs, *, export_mockup, export_subject, **kwargs):
        self.render_modes.append((export_mockup, export_subject))
        self.assertTrue(dict(pairs)["A面"].is_file())
        if export_mockup:
            Image.new("RGB", (80, 80)).save(output)
        if export_subject:
            Image.new("RGBA", (800, 800)).save(worker.transparent_path(output))
        return []

    def render_detail(self, template, output, images, *, slice_height, **kwargs):
        self.detail_inputs = [image.parent.name for image in images]
        output.mkdir()
        for index, height in enumerate((slice_height, 100), 1):
            Image.new("RGB", (80, height)).save(output / f"{index:03d}.png")
        return len(images), 6, 2

    def add_sku(self, name, sku_id=None, *, template="random"):
        sku_id = sku_id or uuid4()
        source = BytesIO()
        Image.new("RGB", (8, 8)).save(source, "PNG")
        source.seek(0)
        result = main.submit_sku(self.product_id, sku_id, name, template, "#ffffff", "#eeeeee",
                                 a_image=UploadFile(source))
        self.assertTrue(any(sku["id"] == sku_id.hex for sku in result["skus"]))
        return sku_id.hex

    def run_job(self, job_id):
        return worker.generate.run(job_id)

    def jobs(self):
        with store.database() as db:
            return products.product_jobs(db, self.product_id.hex)

    def archive(self, response):
        try:
            with ZipFile(response.path) as archive:
                self.assertIsNone(archive.testzip())
                return archive.namelist()
        finally:
            task = response.background
            task.func(*task.args, **task.kwargs)

    def queue_export(self, *ids):
        return UUID(main.submit_export(main.ProductSelection(ids=list(ids)))["id"])

    def export_record(self, export_id):
        return next(row for row in main.list_exports(0, 24)["items"] if row["id"] == export_id.hex)

    def test_draft_runs_skus_and_save_triggers_one_subject_then_ordered_detail(self):
        first = self.add_sku("SKU-B", template="sequential")
        second = self.add_sku("SKU-A")
        self.add_sku("SKU-A", UUID(second))
        with self.assertRaises(HTTPException) as duplicate:
            self.add_sku("sku-a")
        self.assertEqual(duplicate.exception.status_code, 409)
        self.assertEqual(store.next_delivery(), first)
        self.assertIsNone(main.get_product(self.product_id)["thumbnail_job_id"])
        self.run_job(first)
        item = main.list_products(offset=0, limit=24)["items"][0]
        self.assertEqual(item["thumbnail_job_id"], first)
        self.assertEqual(main.output_image(UUID(first), "thumbnail").path.name, "thumb.png")
        self.assertIsNone(item["downloaded_at"])
        first_input = self.data / "inputs" / store.get(first)["batch"]
        second_input = self.data / "inputs" / store.get(second)["batch"]
        self.assertTrue(first_input.is_dir())
        self.assertEqual(main.get_product(self.product_id)["status"], "draft")
        with self.assertRaises(HTTPException) as live_output:
            main.delete_output(UUID(first))
        self.assertEqual(live_output.exception.status_code, 409)

        with patch.object(products.secrets, "choice", side_effect=lambda rows: rows[-1]):
            self.assertEqual(main.save_product(self.product_id, self.form)["status"], "mockups")
        main.save_product(self.product_id, self.form)
        _, _, subject, detail = self.jobs()
        self.assertFalse(first_input.exists())
        self.assertTrue(second_input.is_dir())
        self.assertEqual((subject["status"], detail["status"]), ("blocked", "blocked"))

        self.run_job(second)
        store.initialize()
        self.assertEqual(store.get(subject["id"])["status"], "pending")
        self.assertEqual(store.get(detail["id"])["status"], "blocked")
        self.run_job(subject["id"])
        self.assertFalse(second_input.exists())
        self.assertEqual(store.get(detail["id"])["status"], "pending")
        with patch.object(worker, "DETAIL_PAGE_SLICE_HEIGHT", 1200):
            self.run_job(detail["id"])
        self.assertEqual(self.render_modes, [(True, False), (True, False), (False, True)])
        self.assertEqual(self.detail_inputs, [first, second])
        self.assertTrue(main.get_product(self.product_id)["downloadable"])
        with Image.open(main.output_image(UUID(detail["id"]), "image").path) as first_slice:
            self.assertEqual(first_slice.height, 1200)
        self.assertEqual(main.output_image(UUID(detail["id"]), "image", "002.png").path.name, "002.png")

        contents = [
            "新品/样机图/SKU-B.png", "新品/样机图/SKU-A.png", "新品/透明图/SKU-A-透明图.png",
            "新品/详情页/001.png", "新品/详情页/002.png",
        ]
        single = self.queue_export(self.product_id)
        self.assertEqual(self.export_record(single)["status"], "pending")
        self.assertFalse(exports.archive_path(self.data, single.hex).exists())
        with self.assertRaises(HTTPException) as pending:
            main.download_export(single)
        self.assertEqual(pending.exception.status_code, 409)
        for delete, target in ((main.delete_product, self.product_id), (main.delete_output, UUID(first))):
            with self.assertRaises(HTTPException) as in_use:
                delete(target)
            self.assertEqual(in_use.exception.status_code, 409)
        self.assertTrue(exports.process_next(self.data))
        response = main.download_export(single)
        self.assertIsNone(main.get_product(self.product_id)["downloaded_at"])
        single_stamp = "2026-11-27T08:00:00.000+00:00"
        with patch.object(store, "now", return_value=single_stamp):
            self.assertEqual(self.archive(response), contents)
        self.assertTrue(response.path.is_file())
        single_record = self.export_record(single)
        self.assertEqual(single_record["product_count"], 1)
        self.assertEqual(single_record["downloaded_at"], single_stamp)
        self.assertIsNotNone(single_record["finished_at"])
        self.assertEqual(single_record["size"], response.path.stat().st_size)
        self.assertEqual(main.get_product(self.product_id)["downloaded_at"], single_stamp)
        second_product = uuid4()
        main.create_product(second_product, self.form)
        original_product = self.product_id
        self.product_id = second_product
        self.run_job(self.add_sku("SKU-C"))
        main.save_product(second_product, self.form)
        _, subject, detail = self.jobs()
        self.run_job(subject["id"])
        self.run_job(detail["id"])
        batch = self.queue_export(original_product, second_product, original_product)
        self.assertTrue(exports.process_next(self.data))
        self.assertEqual(self.export_record(batch)["product_count"], 2)
        batch_stamp = "2026-11-27T09:00:00.000+00:00"
        with patch.object(store, "now", return_value=batch_stamp):
            self.assertEqual(self.archive(main.download_export(batch)), contents + [
                "新品 (2)/样机图/SKU-C.png", "新品 (2)/透明图/SKU-C-透明图.png",
                "新品 (2)/详情页/001.png", "新品 (2)/详情页/002.png",
            ])
        self.assertEqual({item["downloaded_at"] for item in main.list_products(0, 24)["items"]}, {batch_stamp})
        main.delete_output(UUID(first))
        product = main.get_product(original_product)
        self.assertEqual(product["status"], "removed")
        self.assertIsNone(product["thumbnail_job_id"])
        with self.assertRaises(HTTPException):
            self.queue_export(original_product)
        self.assertEqual(main.get_product(original_product)["downloaded_at"], batch_stamp)

    def test_failed_sku_cannot_release_detail_job(self):
        first = self.add_sku("失败款")
        second = self.add_sku("成功款")
        with patch.object(products.secrets, "choice", side_effect=lambda rows: rows[-1]):
            main.save_product(self.product_id, self.form)
        _, _, subject, detail = self.jobs()
        with patch.object(worker, "render", side_effect=RuntimeError("模板异常")):
            with self.assertRaisesRegex(RuntimeError, "模板异常"):
                self.run_job(first)
        self.assertEqual(store.get(detail["id"])["status"], "blocked")
        self.run_job(second)
        self.run_job(subject["id"])
        self.assertEqual(store.get(detail["id"])["status"], "skipped")
        summary = main.get_product(self.product_id)
        self.assertEqual(summary["status"], "failed")
        self.assertIn("模板异常", summary["message"])
        self.assertFalse(summary["downloadable"])
        self.assertIsNone(summary["thumbnail_job_id"])

    def test_rename_updates_product_and_download_names_without_rerendering(self):
        self.add_sku("原 SKU")
        main.rename_product(self.product_id, main.ProductForm(name="新草稿名"))
        self.assertEqual(main.get_product(self.product_id)["name"], "新草稿名")
        main.save_product(self.product_id, self.form)
        jobs = self.jobs()
        for job in jobs:
            self.run_job(job["id"])
        main.rename_product(self.product_id, main.ProductForm(name=" 新产品名 "))
        self.assertEqual(main.get_product(self.product_id)["name"], "新产品名")
        self.assertEqual(len(self.jobs()), len(jobs))
        self.assertEqual({row["product_name"] for row in store.page("jobs", 0, 10)["items"]}, {"新产品名"})
        self.assertEqual(store.page("outputs", 0, 10)["items"][0]["download_name"], "新产品名-详情页.zip")
        response = main.output_image(UUID(jobs[-1]["id"]), "download")
        self.assertIn("新产品名-详情页.zip", unquote(response.headers["content-disposition"]))
        self.archive(response)
        export_id = self.queue_export(self.product_id)
        self.assertTrue(exports.process_next(self.data))
        response = main.download_export(export_id)
        self.assertIn("新产品名.zip", unquote(response.headers["content-disposition"]))
        self.assertIn("新产品名/样机图/原 SKU.png", self.archive(response))
        with self.assertRaises(HTTPException) as invalid:
            main.rename_product(self.product_id, main.ProductForm(name="../bad"))
        self.assertEqual(invalid.exception.status_code, 422)

    def test_delete_cancels_pending_jobs_and_removes_only_related_files(self):
        first = self.add_sku("已出图")
        second = self.add_sku("待出图")
        self.run_job(first)
        with patch.object(products.secrets, "choice", side_effect=lambda rows: rows[-1]):
            main.save_product(self.product_id, self.form)
        jobs = self.jobs()
        other = uuid4()
        main.create_product(other, main.ProductForm(name="保留"))
        with store.database() as db:
            store.transition(db, second, "queued", "等待出图")

        self.assertEqual(main.delete_product(self.product_id), {"deleted": True, "cleanup_pending": False})
        self.assertEqual([row["id"] for row in main.list_products(0, 24)["items"]], [other.hex])
        self.assertEqual(store.page("outputs", 0, 10)["total"], 0)
        self.assertEqual(store.page("jobs", 0, 10)["total"], len(jobs))
        self.assertEqual([job["status"] for job in self.jobs()], ["done", "cancelled", "cancelled", "cancelled"])
        for job in jobs:
            self.assertFalse((self.data / "inputs" / job["batch"]).exists())
            self.assertFalse((self.data / "outputs" / job["id"]).exists())
        self.run_job(second)
        self.assertEqual(self.render_modes, [(True, False)])
        self.assertIsNone(store.next_delivery())
        for action in (main.get_product, self.queue_export):
            with self.assertRaises(HTTPException) as missing:
                action(self.product_id)
            self.assertEqual(missing.exception.status_code, 404)
        with self.assertRaises(HTTPException):
            main.create_product(self.product_id, self.form)
        self.assertFalse(main.delete_product(self.product_id)["cleanup_pending"])

    def test_delete_during_render_is_rejected_and_locked_files_can_be_retried(self):
        sku = self.add_sku("进行中")

        def render(*args, **kwargs):
            with self.assertRaises(HTTPException) as active:
                main.delete_product(self.product_id)
            self.assertEqual(active.exception.status_code, 409)
            return self.render_mockup(*args, **kwargs)

        with patch.object(worker, "render", side_effect=render):
            self.run_job(sku)
        with (
            patch.object(main.shutil, "rmtree", side_effect=PermissionError("locked")),
            self.assertLogs(main.__name__, "ERROR"),
        ):
            self.assertTrue(main.delete_product(self.product_id)["cleanup_pending"])
        self.assertTrue((self.data / "outputs" / sku).exists())
        self.assertTrue(main.purge_product_files(self.product_id.hex))
        self.assertFalse((self.data / "outputs" / sku).exists())

    def test_export_recovers_interrupted_archive_and_survives_product_deletion(self):
        self.add_sku("SKU")
        main.save_product(self.product_id, self.form)
        for job in self.jobs():
            self.run_job(job["id"])
        export_id = self.queue_export(self.product_id)
        with store.database() as db:
            db.execute("UPDATE exports SET status='running' WHERE id=?", (export_id.hex,))
        path = exports.archive_path(self.data, export_id.hex)
        path.parent.mkdir()
        path.with_suffix(".part").write_bytes(b"interrupted")
        store.initialize()
        self.assertTrue(exports.process_next(self.data))
        self.assertFalse(path.with_suffix(".part").exists())
        main.delete_product(self.product_id)
        self.assertIn("新品/样机图/SKU.png", self.archive(main.download_export(export_id)))

    def test_failed_export_does_not_block_later_exports_or_product_deletion(self):
        self.add_sku("SKU")
        main.save_product(self.product_id, self.form)
        for job in self.jobs():
            self.run_job(job["id"])
        failed_id = self.queue_export(self.product_id)
        next_id = self.queue_export(self.product_id)
        with (
            patch.object(exports, "write_archive", side_effect=OSError("disk full")),
            self.assertLogs(exports.__name__, "ERROR"),
        ):
            self.assertTrue(exports.process_next(self.data))
        self.assertEqual(self.export_record(failed_id)["status"], "failed")
        with self.assertRaises(HTTPException):
            main.download_export(failed_id)
        self.assertTrue(exports.process_next(self.data))
        self.assertEqual(self.export_record(next_id)["status"], "done")
        self.assertTrue(main.delete_product(self.product_id)["deleted"])


if __name__ == "__main__":
    unittest.main()
