"""FastAPI backend for the building-footprint review workspace.

Run (from E:\\sih012):
    .venv\\Scripts\\python -m uvicorn backend.main:app --host 127.0.0.1 --port 8765

Serves the existing single-page UI (app/static) and data bundle, plus the /api routers. Docs: /docs, /redoc.
Outputs are building footprints, not legal cadastral parcel boundaries.
"""

import mimetypes
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import errors
from .config import Settings
from .routers import export, jobs, layers, parcels, review, sites, topology
from .services.job_service import JobManager
from .services.review_service import WorkspaceStore
from .services.parcel_service import ParcelStore
from .services.site_service import SiteRegistry

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
    @asynccontextmanager
    async def lifespan(app):
        app.state.jobs.start()
        try: yield
        finally: app.state.jobs.close()

    app = FastAPI(title="SIH Building Footprint Backend", version="0.2.0", description=DESCRIPTION, lifespan=lifespan)
    errors.install(app)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST"],
                       allow_headers=["Content-Type"])
    catalog = SiteRegistry(settings)
    app.state.settings, app.state.catalog = settings, catalog
    app.state.jobs = JobManager(settings, catalog)
    workspaces = {}
    workspace_lock = threading.Lock()

    def workspace_for(site_id):
        cat = catalog.get(site_id)
        with workspace_lock:
            if site_id not in workspaces:
                workspaces[site_id] = WorkspaceStore(settings.workspace_dir, cat.layers, cat.bounds()[0], site_id,
                                                     cat.analysis_crs, cat.manifest.get("schema_version", 1) >= 2)
            workspaces[site_id].layers = cat.layers
            return workspaces[site_id]

    app.state.workspace_for = workspace_for
    parcel_workspaces = {}

    def parcels_for(site_id):
        cat = catalog.get(site_id)
        with workspace_lock:
            if site_id not in parcel_workspaces:
                parcel_workspaces[site_id] = ParcelStore(settings.workspace_dir, None, cat.bounds()[0], site_id,
                                                       cat.analysis_crs, True)
            return parcel_workspaces[site_id]

    app.state.parcels_for = parcels_for

    @app.get("/api/health", tags=["health"], summary="Health check")
    def health(request: Request) -> dict:
        c = request.app.state.catalog
        return {"ok": True, "backend": "fastapi", "data_ready": c.ready, "site_id": c.site_id,
                "site_count": len(c.all()), "storage_mode": "single-process local writer"}

    for r in (sites.router, layers.router, topology.router, review.router, export.router, jobs.router, parcels.router):
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
