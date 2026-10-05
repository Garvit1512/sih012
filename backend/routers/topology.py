from fastapi import APIRouter, Request
from shapely.geometry import Point, shape

from .. import config as C
from ..errors import ApiError
from ..models.topology import (SharedBoundaryRequest, SharedBoundaryResult, TopologyResult, ValidateLayerRequest,
                               ValidateRequest)
from ..services.topology_service import TopoFeat, analyze, shared_boundary
from ..utils.crs import normalise_crs, to_utm
from ..utils.validation import safe_id

router = APIRouter(prefix="/api/topology", tags=["topology"])


def _topo_from_fc(fc: dict, crs: str, skip_rejected: bool = False, analysis_crs: str = C.ANALYSIS_CRS) -> list[TopoFeat]:
    if fc.get("type") != "FeatureCollection" or not isinstance(fc.get("features"), list):
        raise ApiError("INVALID_REQUEST", "features must be a GeoJSON FeatureCollection.", 422)
    if len(fc["features"]) > C.MAX_FEATURES_PER_REQUEST:
        raise ApiError("TOO_MANY_FEATURES", "Too many features in one request.", 422)
    src, out = normalise_crs(crs, analysis_crs), []
    for i, f in enumerate(fc["features"]):
        p = (f or {}).get("properties") or {}
        if skip_rejected and p.get("review_status") == "rejected":
            continue
        try:
            g = shape(f["geometry"])
        except Exception:  # noqa: BLE001
            raise ApiError("INVALID_GEOMETRY", f"feature {i}: unreadable geometry.", 422) from None
        if g.geom_type not in ("Polygon", "MultiPolygon"):
            raise ApiError("INVALID_GEOMETRY", f"feature {i}: {g.geom_type} is not a polygon.", 422)
        out.append(TopoFeat(str(f.get("id") or p.get("workspace_id") or f"feature-{i}"), to_utm(g, src, analysis_crs), p.get("feature_type", "building_footprint")))
    return out


@router.post("/validate", response_model=TopologyResult, summary="Validate topology of submitted footprints")
def validate(body: ValidateRequest, request: Request):
    """Real Shapely checks in EPSG:32719: invalid geometry, overlaps, duplicates, shared boundaries, narrow gaps.
    Returns `valid: true` with no conflicts when nothing is found. Relationships between extracted footprints only."""
    cat = request.app.state.catalog.get(body.site_id or request.app.state.catalog.site_id)
    return analyze(_topo_from_fc(body.features, body.crs, analysis_crs=cat.analysis_crs), analysis_crs=cat.analysis_crs)


@router.post("/validate-layer", response_model=TopologyResult, summary="Validate topology of an AI layer or the workspace")
def validate_layer(body: ValidateLayerRequest, request: Request):
    """`layer_id` is an AI layer id (e.g. `maskrcnn`) or `workspace` (rejected workspace features are ignored).
    `bbox` optionally restricts the check to features intersecting the visible extent."""
    st = request.app.state
    cat = st.catalog.get(body.site_id)
    safe_id(body.layer_id, "layer id")
    from ..utils.validation import parse_bbox
    bbox = parse_bbox(",".join(map(str, body.bbox))) if body.bbox else None
    if body.layer_id == "workspace":
        from shapely.geometry import box
        feats = [TopoFeat(f["id"], to_utm(shape(f["geometry"]), analysis_crs=cat.analysis_crs), f["properties"].get("feature_type", "building_footprint")) for f in st.workspace_for(cat.site_id).all()
                 if f["properties"]["review_status"] != "rejected"
                 and (bbox is None or shape(f["geometry"]).intersects(box(*bbox)))]
    else:
        layer = cat.layers.get(body.layer_id)
        feats = [TopoFeat(f.id, f.utm, f.props.get("feature_type", "building_footprint")) for f in layer.query_bbox(bbox)]
    return analyze(feats, analysis_crs=cat.analysis_crs)


@router.post("/shared-boundary", response_model=SharedBoundaryResult, summary="Neighbour edge for a dragged vertex")
def shared_boundary_ep(body: SharedBoundaryRequest, request: Request):
    """When a vertex of `feature_id` is dragged, find the neighbouring footprint that shares that edge and return
    the neighbour's shared edge (WGS84 LineString) so the UI can flash it for ~200 ms.

    Neighbours come from `features` (inline workspace) if given; else the server workspace for `ws-*` ids;
    else the AI layer named by `layer_id` (default: the layer encoded in the id). `tolerance_m` defaults to 0.30 m."""
    st = request.app.state
    cat = st.catalog.get(body.site_id or st.catalog.site_id)
    tol = body.tolerance_m or C.SHARED_BOUNDARY_TOL_M
    if tol > C.MAX_SHARED_TOL_M:
        raise ApiError("INVALID_REQUEST", f"tolerance_m must be <= {C.MAX_SHARED_TOL_M}.", 422)
    safe_id(body.feature_id, "feature id")
    src = normalise_crs(body.crs, cat.analysis_crs)
    x, y = body.vertex
    if not all(v == v and abs(v) < 1e9 for v in (x, y)):
        raise ApiError("COORDINATES_OUT_OF_RANGE", "vertex coordinates are not finite.", 422)
    v = to_utm(Point(x, y), src, cat.analysis_crs)
    if body.features is not None:
        feats = _topo_from_fc(body.features, "EPSG:4326" if src == "EPSG:4326" else src, skip_rejected=True, analysis_crs=cat.analysis_crs)
        me = next((f for f in feats if f.id == body.feature_id), None)
        if me is None:
            raise ApiError("FEATURE_NOT_FOUND", "feature_id is not in the submitted features.", 404)
        others = feats
    elif body.feature_id.startswith("ws-"):
        all_ws = [TopoFeat(f["id"], to_utm(shape(f["geometry"]), analysis_crs=cat.analysis_crs), f["properties"].get("feature_type", "building_footprint")) for f in st.workspace_for(cat.site_id).all() if f["properties"]["review_status"] != "rejected"]
        me = next((f for f in all_ws if f.id == body.feature_id), None)
        if me is None:
            raise ApiError("FEATURE_NOT_FOUND", f"Workspace feature {body.feature_id!r} not found.", 404)
        others = all_ws
    else:
        cat.require_site(cat.site_id)
        layer = cat.layers.get(body.layer_id or body.feature_id.split(":", 1)[0])
        af = layer.by_id.get(body.feature_id)
        if af is None:
            raise ApiError("FEATURE_NOT_FOUND", f"Feature {body.feature_id!r} not found.", 404)
        me = TopoFeat(af.id, af.utm, af.props.get("feature_type", "building_footprint"))
        others = [TopoFeat(layer.features[i].id, layer.features[i].utm, layer.features[i].props.get("feature_type", "building_footprint")) for i in layer.utm_tree.query(v.buffer(tol * 2))]
    return shared_boundary(me, others, v, tol, cat.analysis_crs)
