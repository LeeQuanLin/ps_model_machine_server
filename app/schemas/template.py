from pydantic import BaseModel


class TemplateSummary(BaseModel):
    id: str
    name: str
    filename: str
    relative_path: str
    width: int
    height: int
    size_bytes: int
    thumbnail_url: str | None
