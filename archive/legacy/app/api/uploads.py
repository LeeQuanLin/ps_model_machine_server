from pathlib import Path, PureWindowsPath

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool


def upload_name(filename: str, extensions: set[str]) -> Path:
    path = PureWindowsPath(filename)
    if (path.name != filename or path.is_reserved() or path.suffix.lower() not in extensions
            or len(filename) > 180 or any(ord(c) < 32 or c in '<>:"|?*' for c in filename)):
        raise HTTPException(422, "文件名或格式无效。")
    return Path(filename)


async def save_upload(request: Request, path: Path, max_bytes: int) -> None:
    size = 0
    with path.open("wb") as stream:
        async for chunk in request.stream():
            size += len(chunk)
            if size > max_bytes:
                raise HTTPException(413, f"上传文件超过 {max_bytes // (1024 ** 2)} MB。")
            if chunk:
                await run_in_threadpool(stream.write, chunk)
    if not size:
        raise HTTPException(422, "上传文件为空。")
