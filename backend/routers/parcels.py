from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse

from ..errors import ApiError
from ..models.parcel import EvidenceImport, ParcelEditIn, ParcelExportIn, ParcelStateIn, ProposeIn, SharedEdgeIn
from ..services.parcel_export import export_parcels
from ..utils.validation import safe_id

router = APIRouter(prefix="/api/parcels", tags=["preliminary parcels and field evidence"])


def _store(request, site_id):
    request.app.state.catalog.require_site(site_id)
    return request.app.state.parcels_for(site_id)


def _change(request, body, operation, *args):
    return _store(request, body.site_id).mutate(operation, args, body.expected_revision, body.actor, body.reason, body.duration_ms)


@router.get("/{site_id}")
def snapshot(site_id: str, request: Request, revision: int | None = Query(None, ge=0)):
    store = _store(request, site_id)
    data = store.snapshot(revision)
    return {**data, "diagnostics": store.diagnostics(data), "current_revision": store.snapshot()["revision"]}


@router.get("/{site_id}/history")
def history(site_id: str, request: Request):
    return _store(request, site_id).history()


@router.post("/import")
def import_evidence(body: EvidenceImport, request: Request):
    return _change(request, body, "import_evidence", body.model_dump(mode="json"))


@router.post("/propose")
def propose(body: ProposeIn, request: Request):
    return _change(request, body, "propose", body.mode)


@router.post("/shared-edge")
def shared_edge(body: SharedEdgeIn, request: Request):
    return _change(request, body, "edit_shared_edge", [safe_id(i) for i in body.feature_ids], body.line, body.crs, body.evidence_refs)


@router.post("/features/{feature_id}/state")
def state(feature_id: str, body: ParcelStateIn, request: Request):
    return _change(request, body, "set_parcel_state", safe_id(feature_id), body.state, body.record_refs)


@router.post("/features/{feature_id}/edit")
def edit(feature_id: str, body: ParcelEditIn, request: Request):
    return _change(request, body, "edit_parcel", safe_id(feature_id), body.geometry, body.crs, body.evidence_refs)


@router.post("/export")
def export(body: ParcelExportIn, request: Request):
    store = _store(request, body.site_id)
    cat = request.app.state.catalog.get(body.site_id)
    return export_parcels(store, cat.settings, body.revision, body.states, body.require_topology_clean)


@router.get("/{site_id}/exports/{export_id}")
def download(site_id: str, export_id: str, request: Request):
    _store(request, site_id)
    root = request.app.state.catalog.get(site_id).settings.export_root
    directory = root / safe_id(export_id)
    metadata = directory / "export_metadata.json"
    import json
    if not metadata.is_file() or json.loads(metadata.read_text()).get("site_id") != site_id or not (directory / "parcel_export.zip").is_file():
        raise ApiError("EXPORT_NOT_FOUND", "Saved parcel export does not exist for this site.", 404)
    return FileResponse(directory / "parcel_export.zip", filename=f"{site_id}-parcels-{export_id}.zip", media_type="application/zip")
