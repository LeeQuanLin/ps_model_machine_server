import json
import logging
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from celery import Celery
from PIL import Image

from app import store
from app.config import BROKER, DATA, PHOTOSHOP_RESTART_AFTER_TASKS, QUEUE, revision, templates
from 贴样机 import render

celery = Celery("mockup", broker=BROKER)
celery.conf.update(task_default_queue=QUEUE, task_ignore_result=True, worker_prefetch_multiplier=1,
                   task_acks_late=True, task_reject_on_worker_lost=True,
                   broker_transport_options={"visibility_timeout": 3600, "socket_connect_timeout": 3,
                                             "socket_timeout": 5},
                   task_serializer="json", accept_content=["json"])


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
            store.event(db, job_id, f"Photoshop 重启待重试：{exc}"[:2400], "warning")
        return False
    save_count(0)
    with store.database() as db:
        store.event(db, job_id, f"已处理 {count} 次，Photoshop 已重启：{result.stdout.strip()}")
    return True


def run_job(job, folder):
    source = templates().get(job["template"])
    if source is None or revision(source) != job["revision"]:
        raise ValueError("样机在提交后被移动或修改，请重新提交")
    settings = json.loads(job["settings"])
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
        skipped = render(source, output, pairs, colors=settings["colors"], skip_missing=True,
                         on_processed=lambda: save_count(processed_count() + 1))
        if len(skipped) == len(pairs):
            return "skipped", "模板没有可处理的对应图层，已跳过：" + "、".join(skipped)
        with Image.open(output) as im:
            im.thumbnail((640, 640), Image.Resampling.LANCZOS)
            im.save(work / "thumb.png", "PNG")
        output.replace(folder / "render.png")
        (work / "thumb.png").replace(folder / "thumb.png")
        processed = "、".join(name for name, _ in pairs if name not in skipped)
        detail = "已生成：" + processed
        if skipped:
            detail += "。未找到对应图层，已跳过：" + "、".join(skipped)
        return "done", detail


def release_inputs(batch):
    with store.database() as db:
        active = db.execute("""SELECT 1 FROM jobs WHERE batch=? AND status IN ('pending','queued','running')
                               LIMIT 1""", (batch,)).fetchone()
    if not active:
        folder = DATA / "inputs" / batch
        if folder.is_dir():
            for path in folder.glob("*"):
                path.unlink(missing_ok=True)
            try:
                folder.rmdir()
            except FileNotFoundError:
                pass


@celery.task(name="mockup.render", bind=True, max_retries=None)
def generate(self, job_id):
    if UUID(job_id).hex != job_id:
        raise ValueError("无效的任务 ID")
    job = store.get(job_id)
    if not job:
        return
    folder = DATA / "outputs" / job_id
    folder.mkdir(exist_ok=True)
    # Jobs share one Photoshop instance, including the interval between quit and relaunch.
    with job_lock(DATA / "photoshop.lock") as acquired:
        if not acquired:
            raise self.retry(countdown=30)
        job = store.get(job_id)
        if job["status"] in {"done", "skipped", "failed"}:
            release_inputs(job["batch"])
            return
        if not restart_if_due(job_id):
            with store.database() as db:
                store.transition(db, job_id, "queued", "等待 Photoshop 重启，60 秒后重试", "warning")
            raise self.retry(countdown=60)
        with store.database() as db:
            store.transition(db, job_id, "running", "Photoshop 正在处理样机")
        try:
            status, message = run_job(job, folder)
        except Exception as exc:
            with store.database() as db:
                store.transition(db, job_id, "failed", str(exc)[:2400], "error")
            raise
        else:
            with store.database() as db:
                store.transition(db, job_id, status, message, "warning" if status == "skipped" else "info")
        finally:
            try:
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
            except Exception:
                store.dispatch_error(job_id)
                stop.wait(3)
                continue
            with store.database() as db:
                store.transition(db, job_id, "queued", "已进入 Celery 队列", expected="pending")
        except Exception:
            logger.exception("队列投递出现异常")
            stop.wait(3)
