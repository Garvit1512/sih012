from fastapi import APIRouter, Query, Request

from ..models.review import (DrawIn, EditIn, FunctionalUseIn, MergeIn, ReviewResult, SiteRef, SplitIn, WorkspaceAdd)
from ..utils.validation import parse_bbox, safe_id

router = APIRouter(prefix="/api/review", tags=["review workspace"])


def _ws(request: Request, site_id: str):
    request.app.state.catalog.require_site(site_id)
    return request.app.state.workspace_for(site_id)


def _change(request, body, operation, *args):
    return _ws(request, body.site_id).mutate(operation, args, body.expected_revision, body.actor, body.reason, body.duration_ms)


@router.get("/workspace", summary="Current server-side workspace")
def get_workspace(request: Request, site_id: str = Query(...), revision: int | None = Query(None, ge=0)) -> dict:
    ws = _ws(request, site_id)
    snapshot, summary = ws.snapshot(revision), ws.summary()
    current_revision = summary["revision"]
    if revision is not None:
        summary = {"revision": revision, "count": len(snapshot["features"]),
                   "by_review_status": {s: sum(f["properties"]["review_status"] == s for f in snapshot["features"]) for s in ("suggested", "accepted", "rejected")}}
    return {**snapshot, "summary": summary, "current_revision": current_revision}


@router.get("/history", summary="Revision history with before/after geometry and review provenance")
def history(request: Request, site_id: str, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    return _ws(request, site_id).history(limit, offset)


@router.get("/queue", summary="Suggested features ordered by display cues")
def queue(request: Request, site_id: str):
    return _ws(request, site_id).queue()


@router.post("/workspace", response_model=ReviewResult, summary="Copy AI predictions into the editable workspace")
def add_to_workspace(body: WorkspaceAdd, request: Request):
    """Creates `ai_copy` / `suggested` workspace features. The AI predictions themselves are never modified.
    Provide `feature_ids` or a WGS84 `bbox`."""
    ws = _ws(request, body.site_id)
    safe_id(body.layer_id, "layer id")
    bbox = parse_bbox(",".join(map(str, body.bbox))) if body.bbox else None
    return _change(request, body, "add_from_layer", body.layer_id, body.feature_ids, bbox)


@router.post("/features/merge", response_model=ReviewResult, summary="Merge workspace features")
def merge(body: MergeIn, request: Request):
    """Union of two or more workspace footprints (must form one connected polygon) -> `human_merged`, parents recorded."""
    return _change(request, body, "merge", [safe_id(i, "feature id") for i in body.feature_ids])


@router.post("/features/draw", response_model=ReviewResult, summary="Add a human-drawn footprint")
def draw(body: DrawIn, request: Request):
    return _change(request, body, "draw", body.geometry, body.crs, body.review_status, body.feature_type, body.class_name)


@router.post("/features/{feature_id}/accept", response_model=ReviewResult, summary="Accept a workspace feature")
def accept(feature_id: str, body: SiteRef, request: Request):
    return _change(request, body, "set_status", safe_id(feature_id, "feature id"), "accepted")


@router.post("/features/{feature_id}/reject", response_model=ReviewResult, summary="Reject a workspace feature")
def reject(feature_id: str, body: SiteRef, request: Request):
    return _change(request, body, "set_status", safe_id(feature_id, "feature id"), "rejected")


@router.post("/features/{feature_id}/edit", response_model=ReviewResult, summary="Replace a workspace feature's geometry")
def edit(feature_id: str, body: EditIn, request: Request):
    """Server-side validation: polygon, non-empty, supported CRS, plausible coordinates, valid (safe repairs only,
    <= 1 % area change, recorded in `repairs`). `ai_copy` becomes `human_edited`."""
    return _change(request, body, "edit", safe_id(feature_id, "feature id"), body.geometry, body.crs)


@router.post("/features/{feature_id}/split", response_model=ReviewResult, summary="Split a workspace feature with a line")
def split(feature_id: str, body: SplitIn, request: Request):
    """Subtracts a thin (default 2 cm) cut along the line; each part becomes `human_split`. Area lost to the cut is reported."""
    return _change(request, body, "split", safe_id(feature_id, "feature id"), body.line, body.crs, body.gap_m)


@router.post("/features/{feature_id}/functional-use", response_model=ReviewResult,
             summary="Record a reviewer-supplied building-use class with source references")
def functional_use(feature_id: str, body: FunctionalUseIn, request: Request):
    return _change(request, body, "assign_use", safe_id(feature_id, "feature id"), body.functional_use, body.evidence_refs)


@router.post("/features/{feature_id}/reset", response_model=ReviewResult)
def reset(feature_id: str, body: SiteRef, request: Request):
    return _change(request, body, "reset", safe_id(feature_id, "feature id"))


@router.post("/features/{feature_id}/remove", response_model=ReviewResult)
def remove(feature_id: str, body: SiteRef, request: Request):
    return _change(request, body, "remove", safe_id(feature_id, "feature id"))
