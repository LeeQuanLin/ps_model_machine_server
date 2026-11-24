from pydantic import BaseModel


class ReplacementLayerSummary(BaseModel):
    id: int
    name: str
    path: str


class TemplateSummary(BaseModel):
    id: str
    name: str
    filename: str
    relative_path: str
    width: int
    height: int
    size_bytes: int
    thumbnail_url: str | None
    replacement_layers: list[ReplacementLayerSummary]
