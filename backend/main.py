"""FastAPI backend for the building-footprint review workspace.

Run (from E:\\sih012):
    .venv\\Scripts\\python -m uvicorn backend.main:app --host 127.0.0.1 --port 8765

Serves the existing single-page UI (app/static) and data bundle, plus the /api routers. Docs: /docs, /redoc.
Outputs are building footprints, not legal cadastral parcel boundaries.
"""

import mimetypes

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import errors
from .config import Settings
from .routers import export, layers, review, sites, topology
from .services.review_service import WorkspaceStore
from .services.site_service import Catalog

mimetypes.add_type("application/geo+json", ".geojson")
mimetypes.add_type("text/javascript", ".js")

DESCRIPTION = """
Geospatial backend for **site → orthophoto → AI layer → feature review → topology validation → topology-aware editing → export**.

* AI layers are immutable; edits live in a separate workspace with provenance.
* Topology checks are real Shapely computations between **extracted building footprints** (not legal cadastral parcel topology).
* Errors use `{"error": {"code", "message", "details"}}`.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="SIH Building Footprint Backend", version="0.1.0", description=DESCRIPTION)
    errors.install(app)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST"],
                       allow_headers=["Content-Type"])
    catalog = Catalog(settings)
    app.state.settings, app.state.catalog = settings, catalog
    app.state.workspace = WorkspaceStore(settings.workspace_dir, catalog.layers, catalog.bounds()[0] if catalog.ready else None, catalog.site_id)

    @app.get("/api/health", tags=["health"], summary="Health check")
    def health(request: Request) -> dict:
        c = request.app.state.catalog
        return {"ok": True, "backend": "fastapi", "data_ready": c.ready, "site_id": c.site_id}

    for r in (sites.router, layers.router, topology.router, review.router, export.router):
        app.include_router(r)

    # Existing single-page UI + display data bundle (read-only, StaticFiles blocks path traversal).
    app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")
    if settings.data_dir.is_dir():
        app.mount("/data", StaticFiles(directory=settings.data_dir), name="data")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(settings.static_dir / "index.html", headers={"Cache-Control": "no-store"})

    return app


app = create_app()
