from fastapi import APIRouter, Depends, Request

from app.api.dependencies import authorize
from app.schemas.photoshop import PhotoshopState


router = APIRouter(prefix="/photoshop", tags=["Photoshop"], dependencies=[Depends(authorize)])


@router.post("/start")
def start_photoshop(request: Request) -> PhotoshopState:
    return request.app.state.photoshop.control("start")


@router.post("/stop")
def stop_photoshop(request: Request) -> PhotoshopState:
    return request.app.state.photoshop.control("stop")
