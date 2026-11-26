from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class RenderSummary(BaseModel):
    id: str
    filename: str
    template_name: str
    status: Literal["queued", "running", "completed", "failed"]
    created_at: datetime
    width: int | None = None
    height: int | None = None
    size_bytes: int | None = None
    error: str | None = None
