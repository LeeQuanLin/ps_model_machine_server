import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
TEMPLATES = ROOT / "templates"
DATA = Path(os.environ.get("MOCKUP_DATA_DIR", ROOT / "data" / "studio")).resolve()
BROKER = os.environ.get("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
TOKEN = os.environ.get("PHOTOSHOP_API_TOKEN", "")
QUEUE = "photoshop.mockups"


def templates():
    return {p.relative_to(TEMPLATES).as_posix(): p for p in sorted(TEMPLATES.rglob("*"))
            if p.suffix.lower() in {".psd", ".psb"} and p.is_file()
            and p.resolve().is_relative_to(TEMPLATES.resolve())}


def revision(path):
    stat = path.stat()
    return f"{stat.st_size}:{stat.st_mtime_ns}"
