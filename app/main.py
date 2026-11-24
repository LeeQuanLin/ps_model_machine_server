from contextlib import asynccontextmanager
from threading import Lock

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes.photoshop import router
from app.api.routes.renders import router as renders_router
from app.api.routes.templates import router as templates_router
from app.core.config import PROJECT_ROOT, load_settings
from app.services.photoshop import PhotoshopControlError, PhotoshopService
from app.services.renders import RenderService
from app.services.templates import load_templates


ADMIN_ROOT = PROJECT_ROOT / "app" / "admin"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    app.state.settings = settings
    app.state.photoshop = PhotoshopService(settings.photoshop_exe)
    app.state.templates = load_templates(settings.template_dir)
    app.state.template_lock = Lock()
    app.state.renders = RenderService(PROJECT_ROOT / "data" / "renders", settings.template_dir,
                                      app.state.template_lock, app.state.photoshop)
    try:
        yield
    finally:
        app.state.renders.close()


app = FastAPI(title="Photoshop Control", lifespan=lifespan)
app.include_router(router)
app.include_router(templates_router)
app.include_router(renders_router)
app.mount("/admin-assets", StaticFiles(directory=ADMIN_ROOT), name="admin_assets")


@app.get("/", include_in_schema=False)
def admin() -> FileResponse:
    return FileResponse(ADMIN_ROOT / "index.html")


@app.exception_handler(PhotoshopControlError)
async def handle_photoshop_error(_request: Request, error: PhotoshopControlError) -> JSONResponse:
    return JSONResponse(status_code=error.status_code, content={"detail": str(error)})
