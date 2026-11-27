import json
import logging
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from celery import Celery
from celery.exceptions import Retry
from celery.signals import worker_init, worker_ready, worker_shutdown
from PIL import Image

from app import products, store
from app.config import BROKER, DATA, PHOTOSHOP_RESTART_AFTER_TASKS, QUEUE, revision, template_files
from 详情页生成 import generate as generate_detail_page
from 贴样机 import render, transparent_path

celery = Celery("mockup", broker=BROKER)
celery.conf.update(task_default_queue=QUEUE, task_ignore_result=True, worker_prefetch_multiplier=1,
                   task_acks_late=True, task_reject_on_worker_lost=True,
                   broker_transport_options={"visibility_timeout": 3600, "socket_connect_timeout": 3,
                                             "socket_timeout": 5},
                   task_serializer="json", accept_content=["json"])


@worker_init.connect
def worker_starting(sender, **kwargs):
    store.initialize(migrate_queue=False)
    with store.database() as db:
        store.event(db, "", f"Worker {sender.hostname} 正在启动，队列 {QUEUE}，数据目录 {DATA}")


@worker_ready.connect
def worker_online(sender, **kwargs):
    queues = [queue.name for queue in sender.task_consumer.queues]
    with store.database() as db:
        store.event(db, "", f"Worker {sender.hostname} 已就绪，监听队列 {'、'.join(queues)}",
                    "info" if QUEUE in queues else "warning")


@worker_shutdown.connect
def worker_offline(sender, **kwargs):
    with store.database() as db:
        store.event(db, "", f"Worker {sender.hostname} 正在退出")


@contextmanager
def job_lock(path):
    # OS releases the lock if the worker exits, so Redis can redeliver safely.
    with path.open("a+b") as stream:
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
        else:
            yield True


def processed_count():
    try:
        return int((DATA / "photoshop.count").read_text(encoding="ascii"))
    except FileNotFoundError:
        return 0


def save_count(count):
    # The desktop render lock covers this update and the restart.
    staged = DATA / "photoshop.count.tmp"
    staged.write_text(str(count), encoding="ascii")
    staged.replace(DATA / "photoshop.count")


def restart_if_due(job_id):
    count = processed_count()
    if count < PHOTOSHOP_RESTART_AFTER_TASKS:
        return True
    script = Path(__file__).with_name("restart_photoshop.ps1")
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-File", str(script)],
            capture_output=True, encoding="utf-8", errors="replace", timeout=90,
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "PowerShell 未完成重启")
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        with store.database() as db:
            store.event(db, job_id, f"Photoshop 重启待重试：{exc}", "warning")
        return False
    save_count(0)
    with store.database() as db:
        store.event(db, job_id, f"已处理 {count} 次，Photoshop 已重启：{result.stdout.strip()}")
    return True


def make_thumbnail(source, target):
    with Image.open(source) as im:
        im.thumbnail((640, 640), Image.Resampling.LANCZOS)
        im.save(target, "PNG")


def run_detail(job, folder, source, settings):
    if (folder / "slices").is_dir() and (folder / "thumb.png").is_file():
        return "done", f"已导出 {len(list((folder / 'slices').glob('*.png')))} 张详情页切片"
    images = [DATA / "outputs" / job_id / "render.png" for job_id in settings["source_ids"]]
    if any(not image.is_file() for image in images):
        raise FileNotFoundError("部分 SKU 样机合成图已丢失")
    with store.database() as db:
        store.event(db, job["id"], f"正在调用 Photoshop，按添加顺序填入 {len(images)} 张 SKU 样机图")
    with TemporaryDirectory(dir=folder) as tmp:
        work = Path(tmp)
        used, slots, count = generate_detail_page(source, work / "slices", images,
                                                  on_processed=lambda: save_count(processed_count() + 1))
        make_thumbnail(work / "slices" / "001.png", work / "thumb.png")
        (work / "thumb.png").replace(folder / "thumb.png")
        (work / "slices").rename(folder / "slices")
    return "done", f"已替换 {used}/{slots} 个智能对象，导出 {count} 张详情页切片"


def run_job(job, folder):
    source = template_files().get(job["template"])
    if source is None or revision(source) != job["revision"]:
        raise ValueError("模板在提交后被移动或修改，请核对模板")
    settings = json.loads(job["settings"])
    if job["kind"] == "detail":
        return run_detail(job, folder, source, settings)
    subject_only = job["kind"] == "subject"
    inputs = DATA / "inputs" / job["batch"]
    with TemporaryDirectory(dir=folder) as tmp:
        work = Path(tmp)
        pairs = []
        for name in ("A面", "B面", "枕套", "背景"):
            if name in settings["colors"]:
                color = settings["colors"][name]
                image = work / (color[1:] + ".png")
                Image.new("RGB", (32, 32), color).save(image)
            elif name in settings["images"]:
                image = inputs / settings["images"][name]
            else:
                continue
            pairs.append((name, image))
        output = work / "render.png"

        def log_photoshop_start():
            with store.database() as db:
                store.event(db, job["id"], "正在调用 Photoshop，处理图层：" + "、".join(name for name, _ in pairs))

        skipped = render(source, output, pairs, colors=settings["colors"], skip_missing=True,
                         export_mockup=not subject_only, export_subject=subject_only or not job["product_id"],
                         on_photoshop_start=log_photoshop_start,
                         on_processed=lambda: save_count(processed_count() + 1))
        if len(skipped) == len(pairs):
            return "skipped", "模板没有可处理的对应图层，已跳过：" + "、".join(skipped)
        subject = transparent_path(output)
        make_thumbnail(subject if subject_only else output, work / "thumb.png")
        (work / "thumb.png").replace(folder / "thumb.png")
        if subject_only:
            subject.replace(folder / "subject.png")
            return "done", f"已生成「{settings['sku_name']}」的 800 × 800 透明图"
        output.replace(folder / "render.png")
        has_subject = subject.is_file()
        if has_subject:
            subject.replace(folder / "subject.png")
        else:
            (folder / "subject.png").unlink(missing_ok=True)
        processed = "、".join(name for name, _ in pairs if name not in skipped)
        detail = "已生成：" + processed
        if not has_subject and not job["product_id"]:
            detail += "。模板缺少「主体」分组，仅生成合成图"
        if skipped:
            detail += "。未找到对应图层，已跳过：" + "、".join(skipped)
        return "done", detail


def release_inputs(batch):
    with store.database() as db:
        active = db.execute("""SELECT 1 FROM jobs j LEFT JOIN products p ON p.id=j.product_id
                               WHERE j.batch=? AND (j.status IN ('blocked','pending','queued','running')
                                   OR (p.id IS NOT NULL AND p.saved_at IS NULL)) LIMIT 1""",
                            (batch,)).fetchone()
    if not active:
        folder = DATA / "inputs" / batch
        if folder.is_dir():
            for path in folder.glob("*"):
                path.unlink(missing_ok=True)
            try:
                folder.rmdir()
            except FileNotFoundError:
                pass


def require_job(job_id, hostname):
    job = store.get(job_id)
    if not job:
        message = f"Worker {hostname} 在本机数据库找不到任务，请核对投递队列"
        with store.database() as db:
            store.event(db, job_id, message, "error")
        raise LookupError(message)
    return job


def finish_job(job, status, message, level):
    with store.database() as db:
        store.transition(db, job["id"], status, message, level)
        if job["product_id"]:
            products.advance(db, job["product_id"])


@celery.task(name="mockup.render", bind=True, max_retries=None)
def generate(self, job_id):
    if UUID(job_id).hex != job_id:
        raise ValueError("无效的任务 ID")
    job = require_job(job_id, self.request.hostname)
    with store.database() as db:
        store.event(db, job_id, f"Worker {self.request.hostname} 已接单")
    # Jobs share one Photoshop instance, including the interval between quit and relaunch.
    with job_lock(DATA / "photoshop.lock") as acquired:
        if not acquired:
            with store.database() as db:
                store.event(db, job_id, "Photoshop 正由其他任务使用，30 秒后重试", "warning")
            raise self.retry(countdown=30)
        job = store.get(job_id)
        if job["status"] in store.TERMINAL or job["status"] == "blocked":
            release_inputs(job["batch"])
            return
        render_started = False
        try:
            if not restart_if_due(job_id):
                with store.database() as db:
                    store.transition(db, job_id, "queued", "等待 Photoshop 重启，60 秒后重试", "warning")
                raise self.retry(countdown=60)
            with store.database() as db:
                store.transition(db, job_id, "running", "正在准备模板工作副本与素材")
            render_started = True
            folder = DATA / "outputs" / job_id
            folder.mkdir(exist_ok=True)
            status, message = run_job(job, folder)
        except Retry:
            raise
        except Exception as exc:
            finish_job(job, "failed", f"{type(exc).__name__}：{exc}", "error")
            raise
        else:
            finish_job(job, status, message, "warning" if status == "skipped" else "info")
        finally:
            try:
                if render_started:
                    restart_if_due(job_id)
            finally:
                release_inputs(job["batch"])


def dispatch(stop):
    logger = logging.getLogger(__name__)
    while not stop.is_set():
        try:
            job_id = store.next_delivery()
            if not job_id:
                stop.wait(1)
                continue
            try:
                generate.apply_async(args=[job_id], task_id=job_id, retry=False)
            except Exception as exc:
                store.dispatch_error(job_id, exc)
                stop.wait(3)
                continue
            with store.database() as db:
                store.transition(db, job_id, "queued", f"已投递到 {QUEUE}，等待本机 worker 接单", expected="pending")
        except Exception:
            logger.exception("队列投递出现异常")
            stop.wait(3)
