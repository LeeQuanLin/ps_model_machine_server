import hashlib
import os
import re
import socket
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
TEMPLATES = ROOT / "templates"
DEFAULT_DETAIL_TEMPLATE = "详情页模板.psd"
DETAIL_TEMPLATE = os.environ.get("DETAIL_PAGE_TEMPLATE", "").strip().replace("\\", "/") or DEFAULT_DETAIL_TEMPLATE
DETAIL_TEMPLATE_NAMES = (
    (DEFAULT_DETAIL_TEMPLATE, "详情页模板.psb")
    if DETAIL_TEMPLATE.casefold() == DEFAULT_DETAIL_TEMPLATE.casefold() else (DETAIL_TEMPLATE,)
)
DATA = Path(os.environ.get("MOCKUP_DATA_DIR", ROOT / "data" / "studio")).resolve()
BROKER = os.environ.get("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
TOKEN = os.environ.get("PHOTOSHOP_API_TOKEN", "")


def queue_name(hostname, data_dir):
    # Jobs carry an ID; the PSD, uploads and SQLite row all live on the submitting workstation.
    workspace = f"{hostname.lower()}\n{os.path.normcase(str(data_dir))}"
    return "photoshop.mockups." + hashlib.sha256(workspace.encode()).hexdigest()[:20]


QUEUE = queue_name(socket.gethostname(), DATA)
_restart_tasks = os.environ.get("PHOTOSHOP_RESTART_AFTER_TASKS", "10").strip()
if not _restart_tasks.isdecimal() or int(_restart_tasks) < 1:
    raise ValueError("PHOTOSHOP_RESTART_AFTER_TASKS 必须是大于 0 的整数")
PHOTOSHOP_RESTART_AFTER_TASKS = int(_restart_tasks)
DEFAULT_B_SIDE_PRESETS = "雾蓝:#A4B6C9,米白:#E9E2D4,豆绿:#A3B59C,藕粉:#D7B0AC,灰紫:#B9AFC5,炭灰:#535B60"


def b_side_presets():
    presets = []
    for item in os.environ.get("B_SIDE_PRESET_COLORS", DEFAULT_B_SIDE_PRESETS).split(","):
        if not item.strip():
            continue
        name, _, value = item.rpartition(":")
        color = value.strip()
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            raise ValueError(f"B_SIDE_PRESET_COLORS 配置无效：{item!r}，请使用 名称:#RRGGBB 或 #RRGGBB")
        presets.append((name.strip() or color, color.lower()))
    return presets


def template_files():
    return {p.relative_to(TEMPLATES).as_posix(): p for p in sorted(TEMPLATES.rglob("*"))
            if p.suffix.lower() in {".psd", ".psb"} and p.is_file()
            and p.resolve().is_relative_to(TEMPLATES.resolve())}


def detail_template():
    available = {name.casefold(): path for name, path in template_files().items()}
    return next((available[name.casefold()] for name in DETAIL_TEMPLATE_NAMES if name.casefold() in available), None)


def templates():
    detail_names = {name.casefold() for name in DETAIL_TEMPLATE_NAMES}
    return {name: path for name, path in template_files().items() if name.casefold() not in detail_names}


def revision(path):
    stat = path.stat()
    return f"{stat.st_size}:{stat.st_mtime_ns}"
