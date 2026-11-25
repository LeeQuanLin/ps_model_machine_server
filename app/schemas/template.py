from pydantic import BaseModel, Field


class LayerPreviewRegion(BaseModel):
    left: float
    top: float
    right: float
    bottom: float


class ReplacementLayerSummary(BaseModel):
    id: int
    name: str
    path: str
    preview_regions: list[LayerPreviewRegion] = Field(default_factory=list)


class TemplateSummary(BaseModel):
    id: str
    name: str
    filename: str
    relative_path: str
    width: int
    height: int
    size_bytes: int
    thumbnail_url: str | None
    thumbnail_pending: bool = False
    replacement_layers: list[ReplacementLayerSummary]
