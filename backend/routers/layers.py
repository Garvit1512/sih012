from fastapi import APIRouter, Query, Request

from ..models.feature import FeatureCollectionOut, FeatureDetail
from ..services import feature_service
from ..utils.validation import parse_bbox, safe_id

router = APIRouter(prefix="/api", tags=["layers & features"])


@router.get("/sites/{site_id}/layers", summary="AI layers available for a site")
def list_layers(site_id: str, request: Request, include_metrics: bool = False) -> dict:
    """Counts and provenance come from the data bundle. Metrics (optional) are read from the frozen evaluation
    summary and are labelled by split: held-out T1-T4 windows are already seen; development windows are not test data."""
    cat = request.app.state.catalog.get(site_id)
    return {"layers": cat.layer_summaries(include_metrics), "metrics_only_layers": cat.metrics_only()}


@router.get("/sites/{site_id}/layers/{layer_id}/features", response_model=FeatureCollectionOut,
            summary="AI features (GeoJSON, WGS84) with bbox filter and pagination")
def layer_features(site_id: str, layer_id: str, request: Request,
                   bbox: str | None = Query(None, description="WGS84 'minx,miny,maxx,maxy' (the visible map extent)"),
                   limit: int = Query(500, ge=1, le=5000), offset: int = Query(0, ge=0)):
    """Immutable AI predictions. `score` is present only where the layer provides one and is not a calibrated probability."""
    cat = request.app.state.catalog.get(site_id)
    safe_id(layer_id, "layer id")
    return feature_service.list_features(cat.layers.get(layer_id), parse_bbox(bbox), limit, offset)


@router.get("/features/{feature_id}", response_model=FeatureDetail, summary="Feature detail for the inspector")
def feature_detail(feature_id: str, request: Request, site_id: str | None = None):
    """AI feature ids look like `maskrcnn:12`; workspace feature ids look like `ws-<hex>`."""
    st = request.app.state
    safe_id(feature_id, "feature id")
    cat = st.catalog.get(site_id or st.catalog.site_id)
    return feature_service.feature_detail(cat, st.workspace_for(cat.site_id), feature_id)
