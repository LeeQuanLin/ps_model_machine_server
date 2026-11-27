import logging
import shutil
import sqlite3
from threading import Lock

from starlette.responses import JSONResponse

from app import store
from app.worker import job_lock

RESET_PATH = "/api/storage/clear"
DATA_FOLDERS = {"inputs": "上传缓存", "outputs": "输出文件", "exports": "导出文件"}


class CleanupBusy(Exception):
    pass


class CleanupGate:
    """Keep requests and streaming downloads out of the workspace during a reset."""

    def __init__(self, app):
        self.app = app
        self.lock = Lock()
        self.active = 0
        self.clearing = False

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return
        reset = scope["path"] == RESET_PATH and scope["method"] == "POST"
        with self.lock:
            blocked = self.clearing or (reset and self.active > 0)
            if not blocked:
                if reset:
                    self.clearing = True
                else:
                    self.active += 1
        if blocked:
            detail = "工作台正在释放空间，请稍后重试" if not reset else "工作台有请求正在处理，请稍后重试"
            await JSONResponse({"detail": detail}, status_code=409)(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        finally:
            with self.lock:
                if reset:
                    self.clearing = False
                else:
                    self.active -= 1


def clear_workspace(data):
    data = data.resolve()
    # The locks span the entire render or export, including publishing its output.
    with job_lock(data / "photoshop.lock") as render_idle, job_lock(data / "exports.lock") as export_idle:
        if not render_idle or not export_idle:
            raise CleanupBusy("正在出图或打包，请等当前任务结束后再释放空间")
        store.clear_records()
        remaining = []
        for directory, label in DATA_FOLDERS.items():
            path = data / directory
            try:
                if path.resolve() != path:
                    raise OSError("数据目录是链接")
                if path.exists():
                    shutil.rmtree(path)
                path.mkdir(exist_ok=True)
            except OSError:
                logging.getLogger(__name__).exception("清理目录失败：%s", path)
                remaining.append(label)

        for path in (*data.glob("download-*.zip"), data / "photoshop.count.tmp"):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logging.getLogger(__name__).exception("清理临时文件失败：%s", path)
                if "临时文件" not in remaining:
                    remaining.append("临时文件")

        try:
            if not store.compact_database():
                remaining.append("数据库压缩")
        except sqlite3.Error:
            logging.getLogger(__name__).exception("数据库压缩失败")
            remaining.append("数据库压缩")
        return remaining
