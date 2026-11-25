from typing import Annotated

from pydantic import BaseModel, Field


LayerId = Annotated[int, Field(gt=0)]


class TemplateSlots(BaseModel):
    a_side: LayerId | None = None
    b_side: LayerId | None = None
    pillowcase: LayerId | None = None
    background: LayerId | None = None


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
    thumbnail_url: str | None = None


class TemplateConfiguration(BaseModel):
    revision: str
    slots: TemplateSlots


class TemplateInspection(TemplateConfiguration):
    layers: list[ReplacementLayerSummary]
    thumbnail_url: str | None = None


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
    slots: TemplateSlots | None = None
