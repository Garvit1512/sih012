from fastapi import APIRouter, Request
import hashlib
import json
from typing import Literal
from pydantic import BaseModel, Field

from ..services.export_service import export_features

router = APIRouter(prefix="/api", tags=["export"])


class ExportIn(BaseModel):
    features: dict | None = Field(None, description="Legacy submitted features; use site_id and revision for canonical exports.")
    site_id: str | None = None
    revision: int | None = Field(None, ge=0)
    statuses: list[Literal["accepted", "suggested", "rejected"]] = ["accepted"]
    note: str = ""
    require_topology_clean: bool = Field(False, description="Block the export when topology conflicts are found")


@router.post("/export", summary="Validate and export reviewed footprints")
def export(body: ExportIn, request: Request) -> dict:
    """Validates geometry/CRS/provenance server-side, then writes a NEW timestamped directory under outputs/app_exports/
    with EPSG:32719 and WGS84 GeoJSON plus `export_metadata.json` (manifest, topology summary, disclaimer).
    Never overwrites. These are AI-generated/human-reviewed building footprints, NOT legal cadastral parcel boundaries."""
    st = request.app.state
    cat = st.catalog.get(body.site_id or st.catalog.site_id)
    if body.site_id is not None:
        if body.features is not None or body.revision is None:
            from ..errors import ApiError
            raise ApiError("INVALID_REQUEST", "Canonical exports require a saved revision and do not accept submitted features.", 422)
        ws = st.workspace_for(cat.site_id)
        snapshot = ws.snapshot(body.revision)
        fc = {"type": "FeatureCollection", "features": [f for f in snapshot["features"] if f["properties"]["review_status"] in body.statuses]}
        history = [e for e in ws.history(1000000)["events"] if e["revision"] <= body.revision]
        provenance = {"site_id": cat.site_id, "workspace_revision": body.revision,
                      "review_history_sha256": hashlib.sha256(json.dumps(history, sort_keys=True).encode()).hexdigest()}
        return export_features(fc, body.note, cat.settings, cat.bounds()[0], body.require_topology_clean, cat.analysis_crs, provenance)
    return export_features(body.features, body.note, cat.settings, cat.bounds()[0], body.require_topology_clean, cat.analysis_crs)
