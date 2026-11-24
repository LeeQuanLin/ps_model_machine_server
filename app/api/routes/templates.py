from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.api.dependencies import authorize
from app.schemas.template import TemplateSummary


router = APIRouter(prefix="/templates", tags=["Templates"], dependencies=[Depends(authorize)])


@router.get("")
def list_templates(request: Request) -> list[TemplateSummary]:
    return [
        TemplateSummary(
            id=template.id,
            name=template.path.stem,
            filename=template.path.name,
            relative_path=template.path.as_posix(),
            width=template.width,
            height=template.height,
            size_bytes=template.size_bytes,
            thumbnail_url=(
                request.url_for("get_template_thumbnail", template_id=template.id).path
                if template.thumbnail is not None else None
            ),
        )
        for template in request.app.state.templates.values()
    ]


@router.get(
    "/{template_id}/thumbnail",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}}}, 404: {"description": "Thumbnail not found"}},
)
def get_template_thumbnail(template_id: str, request: Request) -> Response:
    template = request.app.state.templates.get(template_id)
    if template is None or template.thumbnail is None:
        raise HTTPException(404, "Thumbnail not found.")
    return Response(content=template.thumbnail, media_type="image/jpeg")
