from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from ..models.site import ImageryInfo, SiteDetail, SiteSummary
from ..services.site_service import CLIENT_STATES, SERVER_STATES

router = APIRouter(prefix="/api/sites", tags=["sites"])


@router.get("", summary="List available sites")
def list_sites(request: Request) -> dict:
    """Sites are read from the data bundle manifest. Small payload: metadata only, no features."""
    cat = request.app.state.catalog
    return {"sites": [SiteSummary(**cat.summary()).model_dump()] if cat.ready else [], "data_ready": cat.ready}


@router.get("/{site_id}", response_model=SiteDetail, summary="Site metadata, imagery info, layers and counts")
def get_site(site_id: str, request: Request):
    """Everything the frontend needs to `flyTo` the site and offer layers. Features are fetched separately (bbox/paging)."""
    st = request.app.state
    st.catalog.require_site(site_id)
    layers = st.catalog.layer_summaries()
    ws = st.workspace.summary()
    if ws["by_review_status"]["accepted"]:
        state, why = "EXPORT_READY", "The workspace contains accepted footprints."
    elif ws["count"]:
        state, why = "REVIEWING", "The workspace contains footprints but none are accepted yet."
    else:
        state, why = "LAYERS_AVAILABLE", "Imagery and AI layers are available; the workspace is empty."
    return SiteDetail(
        **st.catalog.summary(), imagery_info=ImageryInfo(**st.catalog.imagery()),
        layers=[{k: l[k] for k in ("id", "legacy_key", "name", "type", "status", "feature_count")} for l in layers],
        feature_counts={l["id"]: l["feature_count"] for l in layers}, workspace=ws,
        workflow={"state": state, "reason": why, "server_derived_states": SERVER_STATES, "client_states": CLIENT_STATES},
        disclaimer=st.catalog.disclaimer())


@router.get("/{site_id}/imagery", response_model=ImageryInfo, summary="Orthophoto display metadata")
def get_imagery(site_id: str, request: Request):
    """Bounds, CRS, dimensions, resolution and display URL. The source GeoTIFF is never served or modified."""
    request.app.state.catalog.require_site(site_id)
    return ImageryInfo(**request.app.state.catalog.imagery())


@router.get("/{site_id}/imagery/display", summary="Display orthophoto (PNG, EPSG:3857 warp)", response_class=FileResponse)
def get_display(site_id: str, request: Request):
    cat = request.app.state.catalog
    cat.require_site(site_id)
    return FileResponse(cat.display_image_path(), media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})
