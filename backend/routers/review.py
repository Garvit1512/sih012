from fastapi import APIRouter, Query, Request

from ..models.review import (DrawIn, EditIn, MergeIn, ReviewResult, SiteRef, SplitIn, WorkspaceAdd)
from ..utils.validation import parse_bbox, safe_id

router = APIRouter(prefix="/api/review", tags=["review workspace"])


def _ws(request: Request, site_id: str):
    request.app.state.catalog.require_site(site_id)
    return request.app.state.workspace


@router.get("/workspace", summary="Current server-side workspace")
def get_workspace(request: Request, site_id: str = Query(...)) -> dict:
    ws = _ws(request, site_id)
    return {"type": "FeatureCollection", "features": ws.all(), "summary": ws.summary()}


@router.post("/workspace", response_model=ReviewResult, summary="Copy AI predictions into the editable workspace")
def add_to_workspace(body: WorkspaceAdd, request: Request):
    """Creates `ai_copy` / `suggested` workspace features. The AI predictions themselves are never modified.
    Provide `feature_ids` or a WGS84 `bbox`."""
    ws = _ws(request, body.site_id)
    safe_id(body.layer_id, "layer id")
    bbox = parse_bbox(",".join(map(str, body.bbox))) if body.bbox else None
    return ws.add_from_layer(body.layer_id, body.feature_ids, bbox)


@router.post("/features/merge", response_model=ReviewResult, summary="Merge workspace features")
def merge(body: MergeIn, request: Request):
    """Union of two or more workspace footprints (must form one connected polygon) -> `human_merged`, parents recorded."""
    return _ws(request, body.site_id).merge([safe_id(i, "feature id") for i in body.feature_ids])


@router.post("/features/draw", response_model=ReviewResult, summary="Add a human-drawn footprint")
def draw(body: DrawIn, request: Request):
    return _ws(request, body.site_id).draw(body.geometry, body.crs, body.review_status)


@router.post("/features/{feature_id}/accept", response_model=ReviewResult, summary="Accept a workspace feature")
def accept(feature_id: str, body: SiteRef, request: Request):
    return _ws(request, body.site_id).set_status(safe_id(feature_id, "feature id"), "accepted")


@router.post("/features/{feature_id}/reject", response_model=ReviewResult, summary="Reject a workspace feature")
def reject(feature_id: str, body: SiteRef, request: Request):
    return _ws(request, body.site_id).set_status(safe_id(feature_id, "feature id"), "rejected")


@router.post("/features/{feature_id}/edit", response_model=ReviewResult, summary="Replace a workspace feature's geometry")
def edit(feature_id: str, body: EditIn, request: Request):
    """Server-side validation: polygon, non-empty, supported CRS, plausible coordinates, valid (safe repairs only,
    <= 1 % area change, recorded in `repairs`). `ai_copy` becomes `human_edited`."""
    return _ws(request, body.site_id).edit(safe_id(feature_id, "feature id"), body.geometry, body.crs)


@router.post("/features/{feature_id}/split", response_model=ReviewResult, summary="Split a workspace feature with a line")
def split(feature_id: str, body: SplitIn, request: Request):
    """Subtracts a thin (default 2 cm) cut along the line; each part becomes `human_split`. Area lost to the cut is reported."""
    return _ws(request, body.site_id).split(safe_id(feature_id, "feature id"), body.line, body.crs, body.gap_m)
