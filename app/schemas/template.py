from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.preview_models import DEFAULT_PREVIEW_MODEL, PREVIEW_MODELS


LayerId = Annotated[int, Field(gt=0)]


class TemplateSlots(BaseModel):
    model_config = ConfigDict(extra="allow")
    __pydantic_extra__: dict[str, LayerId | None] = Field(init=False)

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
    preview_model: str = DEFAULT_PREVIEW_MODEL

    @field_validator("preview_model")
    @classmethod
    def validate_preview_model(cls, value: str) -> str:
        if value not in PREVIEW_MODELS:
            raise ValueError("未知的样机预览类型。")
        return value


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
    preview_model: str = DEFAULT_PREVIEW_MODEL
