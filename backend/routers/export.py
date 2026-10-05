from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ..services.export_service import export_features

router = APIRouter(prefix="/api", tags=["export"])


class ExportIn(BaseModel):
    features: dict = Field(description="GeoJSON FeatureCollection (EPSG:4326). Each feature needs properties.origin and review_status.")
    note: str = ""
    require_topology_clean: bool = Field(False, description="Block the export when topology conflicts are found")


@router.post("/export", summary="Validate and export reviewed footprints")
def export(body: ExportIn, request: Request) -> dict:
    """Validates geometry/CRS/provenance server-side, then writes a NEW timestamped directory under outputs/app_exports/
    with EPSG:32719 and WGS84 GeoJSON plus `export_metadata.json` (manifest, topology summary, disclaimer).
    Never overwrites. These are AI-generated/human-reviewed building footprints, NOT legal cadastral parcel boundaries."""
    st = request.app.state
    bounds = st.catalog.bounds()[0] if st.catalog.ready else None
    return export_features(body.features, body.note, st.settings, bounds, body.require_topology_clean)
