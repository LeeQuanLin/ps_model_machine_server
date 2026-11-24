from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.routes.photoshop import router
from app.core.config import load_settings
from app.services.photoshop import PhotoshopControlError, PhotoshopService


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    app.state.settings = settings
    app.state.photoshop = PhotoshopService(settings.photoshop_exe)
    yield


app = FastAPI(title="Photoshop Control", lifespan=lifespan)
app.include_router(router)


@app.exception_handler(PhotoshopControlError)
async def handle_photoshop_error(_request: Request, error: PhotoshopControlError) -> JSONResponse:
    return JSONResponse(status_code=error.status_code, content={"detail": str(error)})
