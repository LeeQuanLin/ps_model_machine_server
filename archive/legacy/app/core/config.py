import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9._~+/-]+=*")


@dataclass(frozen=True)
class Settings:
    token: bytes = field(repr=False)
    photoshop_exe: Path
    template_dir: Path


def load_environment() -> dict[str, str | None]:
    return {**dotenv_values(PROJECT_ROOT / ".env", interpolate=False), **os.environ}


def resolve_template_dir(config: Mapping[str, str | None]) -> Path:
    directory = config.get("PHOTOSHOP_TEMPLATE_DIR") or "templates"
    return (PROJECT_ROOT / directory).resolve()


def load_settings() -> Settings:
    config = load_environment()
    token = config.get("PHOTOSHOP_API_TOKEN") or ""
    if not TOKEN_PATTERN.fullmatch(token):
        raise RuntimeError("Set PHOTOSHOP_API_TOKEN to a valid Bearer token in .env.")

    exe = Path(config.get("PHOTOSHOP_EXE") or "")
    if not exe.is_absolute() or exe.name.lower() != "photoshop.exe" or not exe.is_file():
        raise RuntimeError("Set PHOTOSHOP_EXE to the full path of an existing Photoshop.exe.")

    return Settings(
        token=token.encode("ascii"),
        photoshop_exe=exe.resolve(),
        template_dir=resolve_template_dir(config),
    )
