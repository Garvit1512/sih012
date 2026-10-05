import json
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from ..errors import ApiError
from ..models.site import ImageryInfo, SiteDetail, SiteSummary
from ..services.ingestion_service import MAX_UPLOAD_BYTES, ingest_site, render_tile
from ..services.site_service import CLIENT_STATES, SERVER_STATES

router = APIRouter(prefix="/api/sites", tags=["sites"])


@router.get("")
def list_sites(request: Request):
    rows = [SiteSummary(**c.summary()).model_dump() for c in request.app.state.catalog.all().values()]
    return {"sites": rows, "data_ready": bool(rows)}


@router.post("", status_code=201, summary="Import a georeferenced RGB GeoTIFF as a new site")
async def create_site(request: Request, site_id: str = Form(...), name: str = Form(...),
                      imagery: UploadFile = File(...), analysis_crs: str = Form(""),
                      licence: str = Form("unknown"), source_url: str = Form(""),
                      vertical_datum: str = Form(""), acquired_on: str = Form(""),
                      dsm: UploadFile | None = File(None), dtm: UploadFile | None = File(None)):
    if len(name) > 150 or len(source_url) > 1000 or len(licence) > 150:
        raise ApiError("INVALID_REQUEST", "Name, source URL or licence text exceeds its supported length.", 422)
    if site_id in request.app.state.catalog.all():
        raise ApiError("SITE_EXISTS", "This site id already exists.", 409)
    with tempfile.TemporaryDirectory(prefix="sih-upload-") as tmp:
        paths = {}
        for kind, upload in (("imagery", imagery), ("dsm", dsm), ("dtm", dtm)):
            if upload is None:
                continue
            if Path(upload.filename or "").suffix.lower() not in (".tif", ".tiff"):
                raise ApiError("INVALID_IMAGERY", "Upload GeoTIFF files (.tif/.tiff).", 422)
            path = Path(tmp) / f"{kind}.tif"
            total = 0
            with path.open("wb") as stream:
                while chunk := await upload.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        raise ApiError("IMAGE_TOO_LARGE", "Each upload must be at most 1 GiB.", 413)
                    stream.write(chunk)
            paths[kind] = path
        await run_in_threadpool(ingest_site, request.app.state.settings, paths["imagery"], site_id, name,
                                analysis_crs or None, licence, source_url,
                                {k: paths[k] for k in ("dsm", "dtm") if k in paths},
                                vertical_datum or None, acquired_on or None)
    return get_site(site_id, request)


@router.get("/{site_id}", response_model=SiteDetail)
def get_site(site_id: str, request: Request):
    st = request.app.state
    cat = st.catalog.get(site_id)
    layers = cat.layer_summaries()
    ws = st.workspace_for(site_id).summary()
    if ws["by_review_status"]["accepted"]:
        state, why = "EXPORT_READY", "The workspace contains accepted features."
    elif ws["count"]:
        state, why = "REVIEWING", "Features are being reviewed."
    elif layers:
        state, why = "LAYERS_AVAILABLE", "AI layers are available; the workspace is empty."
    else:
        state, why = "IMAGERY_READY", "Imagery is ready; run an extraction job to create layers."
    return SiteDetail(**cat.summary(), imagery_info=ImageryInfo(**cat.imagery()),
                      layers=[{k: l[k] for k in ("id", "legacy_key", "name", "type", "status", "feature_count")} for l in layers],
                      feature_counts={l["id"]: l["feature_count"] for l in layers}, workspace=ws,
                      workflow={"state": state, "reason": why, "server_derived_states": SERVER_STATES, "client_states": CLIENT_STATES},
                      disclaimer=cat.disclaimer())


@router.get("/{site_id}/manifest")
def manifest(site_id: str, request: Request):
    return request.app.state.catalog.get(site_id).manifest


@router.get("/{site_id}/windows")
def windows(site_id: str, request: Request):
    path = request.app.state.catalog.get(site_id).settings.data_dir / "windows.geojson"
    return json.loads(path.read_text()) if path.is_file() else {"type": "FeatureCollection", "features": []}


@router.get("/{site_id}/imagery", response_model=ImageryInfo)
def get_imagery(site_id: str, request: Request):
    return ImageryInfo(**request.app.state.catalog.get(site_id).imagery())


@router.get("/{site_id}/imagery/display", response_class=FileResponse)
def get_display(site_id: str, request: Request):
    return FileResponse(request.app.state.catalog.get(site_id).display_image_path(), media_type="image/png")


@router.get("/{site_id}/imagery/tiles/{z}/{x}/{y}.png", response_class=FileResponse)
def tile(site_id: str, z: int, x: int, y: int, request: Request):
    path = render_tile(request.app.state.catalog.get(site_id), z, x, y)
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})
