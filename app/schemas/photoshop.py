from typing import Literal

from pydantic import BaseModel


class PhotoshopState(BaseModel):
    status: Literal["running", "stopped"]
    changed: bool
    pid: int | None = None
    version: str | None = None
