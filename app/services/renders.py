import logging
import shutil
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock

from PIL import Image

from app.schemas.render import RenderSummary
from app.services.photoshop import PhotoshopControlError, PhotoshopService
from app.services.templates import TemplateRecord
from scripts.mockup import prepare_job


logger = logging.getLogger(__name__)
ARTWORK_FORMATS = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG"}


def validate_artwork(path: Path, extension: str) -> None:
    with Image.open(path, formats=[ARTWORK_FORMATS[extension]]) as image:
        image.verify()


class RenderService:
    def __init__(self, root: Path, template_dir: Path, template_lock: Lock, photoshop: PhotoshopService):
        self.root = root
        self.template_dir = template_dir
        self.template_lock = template_lock
        self.photoshop = photoshop
        root.mkdir(parents=True, exist_ok=True)
        with self._database() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS renders (
                    id TEXT PRIMARY KEY, filename TEXT NOT NULL, template_name TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL,
                    width INTEGER, height INTEGER, size_bytes INTEGER, error TEXT
                )
            """)
            connection.execute("""
                UPDATE renders SET status = 'failed', error = '服务重启，生成任务已中断。'
                WHERE status IN ('queued', 'running')
            """)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="photoshop-render")

    @contextmanager
    def _database(self):
        with closing(sqlite3.connect(self.root / "renders.sqlite3")) as connection, connection:
            connection.row_factory = sqlite3.Row
            yield connection

    def close(self) -> None:
        self.executor.shutdown(wait=True, cancel_futures=True)

    def list(self) -> list[RenderSummary]:
        with self._database() as connection:
            rows = connection.execute("SELECT * FROM renders ORDER BY created_at DESC, id DESC").fetchall()
        return [RenderSummary.model_validate(dict(row)) for row in rows]

    def get(self, render_id: str) -> RenderSummary | None:
        with self._database() as connection:
            row = connection.execute("SELECT * FROM renders WHERE id = ?", (render_id,)).fetchone()
        return RenderSummary.model_validate(dict(row)) if row else None

    def enqueue(self, render_id: str, artwork: Path, filename: str, template: TemplateRecord,
                layer: str | int | None) -> RenderSummary:
        record = RenderSummary(id=render_id, filename=filename, template_name=template.path.as_posix(),
                               status="queued", created_at=datetime.now(timezone.utc))
        with self._database() as connection:
            connection.execute("INSERT INTO renders(id, filename, template_name, status, created_at) VALUES(?, ?, ?, ?, ?)",
                               (record.id, record.filename, record.template_name, record.status, record.created_at.isoformat()))
        self.executor.submit(self._render, render_id, artwork, template, layer)
        return record

    def _render(self, render_id: str, artwork: Path, template: TemplateRecord, layer: str | int | None) -> None:
        folder = self.root / render_id
        with self._database() as connection:
            connection.execute("UPDATE renders SET status = 'running' WHERE id = ?", (render_id,))
        try:
            with TemporaryDirectory(dir=folder, ignore_cleanup_errors=True) as work_dir:
                work = Path(work_dir)
                staged = work / "result.png"
                with self.template_lock:
                    source = (self.template_dir / template.path).resolve()
                    if not source.is_relative_to(self.template_dir) or not source.is_file():
                        raise ValueError("样机文件已移除，请重新选择样机。")
                    script = prepare_job(work, source, artwork, staged, None, layer)
                self.photoshop.render(script)
                report = work / "result.txt"
                result = report.read_text(encoding="utf-8-sig").strip() if report.is_file() else ""
                if not result.startswith("OK ") or not staged.is_file():
                    if "Candidates:" in result:
                        raise ValueError("请选择智能对象图层。可选图层：" + result.split("Candidates:", 1)[1].strip())
                    raise ValueError(result or "Photoshop 未生成 PNG 文件。")

                with Image.open(staged, formats=["PNG"]) as image:
                    width, height = image.size
                    image.thumbnail((560, 560), Image.Resampling.LANCZOS)
                    image.save(work / "thumbnail.png")
                size_bytes = staged.stat().st_size
                (work / "thumbnail.png").replace(folder / "thumbnail.png")
                staged.replace(folder / "result.png")
                with self._database() as connection:
                    connection.execute("""
                        UPDATE renders SET status = 'completed', width = ?, height = ?, size_bytes = ? WHERE id = ?
                    """, (width, height, size_bytes, render_id))
        except (ValueError, PhotoshopControlError) as error:
            self._fail(render_id, str(error))
        except Exception:
            logger.exception("Render %s failed", render_id)
            self._fail(render_id, "生成失败，请检查服务器日志。")
        finally:
            artwork.unlink(missing_ok=True)

    def _fail(self, render_id: str, message: str) -> None:
        with self._database() as connection:
            connection.execute("UPDATE renders SET status = 'failed', error = ? WHERE id = ?", (message[:2000], render_id))

    def delete(self, render_id: str) -> bool:
        folder = self.root / render_id
        if folder.resolve().parent != self.root.resolve():
            raise OSError("Unexpected render directory")
        removed = folder.with_name(f".removed-{render_id}")
        with self._database() as connection:
            cursor = connection.execute("DELETE FROM renders WHERE id = ? AND status IN ('completed', 'failed')", (render_id,))
            if not cursor.rowcount:
                return False
            if folder.exists():
                folder.rename(removed)
        if removed.is_dir():
            try:
                shutil.rmtree(removed)
            except OSError:
                logger.exception("Could not remove render directory %s", removed)
        return True
