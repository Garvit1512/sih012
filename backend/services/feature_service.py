"""Feature access: AI predictions (immutable) and workspace features, with bbox filtering and pagination."""

from shapely.geometry import mapping, shape

from .. import config as C
from ..errors import ApiError
from ..utils.crs import to_utm
from .layer_service import LAYER_META, AiFeature, LayerData
from .topology_service import TopoFeat, pair_relation


def ai_feature_json(layer: LayerData, af: AiFeature) -> dict:
    p = af.props
    props = {
        "feature_id": af.id, "layer": layer.id, "source_layer": layer.id, "source_id": af.source_id,
        "area_m2": af.area_m2 if af.area_m2 is None else round(af.area_m2, 3),
        "flags": p.get("flags") or "", "origin": "ai_prediction", "review_status": None,
        "feature_type": p.get("feature_type", "building_footprint"), "class_name": p.get("class_name"),
        "functional_use": p.get("functional_use", "unknown"),
        "provenance": {"origin": "ai_prediction", "source_layer": layer.id, "source_feature_id": af.id, "feature_type": p.get("feature_type", "building_footprint"),
                       "model": LAYER_META.get(layer.key, {}).get("model"), "immutable": True},
    }
    for key in ("functional_use_suggestion", "use_candidate"):
        if key in p:
            props[key] = p[key]
    if p.get("score") is not None:                       # only when the model/bundle actually provides one
        props["score"] = p["score"]
        props["score_label"] = layer.meta.get("score_label")
    return {"type": "Feature", "id": af.id, "geometry": mapping(af.wgs), "properties": props}


def list_features(layer: LayerData, bbox, limit: int, offset: int) -> dict:
    limit = max(1, min(limit, C.MAX_PAGE))
    offset = max(0, offset)
    matched = layer.query_bbox(bbox)
    page = matched[offset: offset + limit]
    return {"type": "FeatureCollection", "features": [ai_feature_json(layer, f) for f in page],
            "numberMatched": len(matched), "numberReturned": len(page), "offset": offset, "limit": limit}


def _confidence(props: dict, label: str | None):
    if props.get("score") is None and props.get("ai_score") is None:
        return None
    return {"value": props.get("score", props.get("ai_score")), "kind": label, "calibrated": False}


def feature_detail(catalog, workspace, feature_id: str) -> dict:
    """Detail for the inspector panel, with topology relationships to neighbouring footprints."""
    tol = C.SHARED_BOUNDARY_TOL_M
    if feature_id.startswith("ws-"):
        f = workspace.get(feature_id)
        g = shape(f["geometry"])
        p = f["properties"]
        label = p.get("ai_score_label")
        out = dict(id=feature_id, kind="workspace", geometry=f["geometry"], area_m2=p["area_m2"], source_layer=p.get("source_layer"),
                   source_feature_id=p.get("source_feature_id"), review_status=p["review_status"], confidence=_confidence(p, label),
                   provenance={"origin": p["origin"], "source_layer": p.get("source_layer"),
                               "source_feature_id": p.get("source_feature_id"), "parents": p.get("parents", []),
                               "repairs": p.get("repairs", [])},
                   properties=p)
        me = TopoFeat(feature_id, to_utm(g, analysis_crs=catalog.analysis_crs), p.get("feature_type", "building_footprint"))
        others = [(o["id"], to_utm(shape(o["geometry"]), analysis_crs=catalog.analysis_crs)) for o in workspace.all()
                  if o["id"] != feature_id and o["properties"]["review_status"] != "rejected"
                  and o["properties"].get("feature_type", "building_footprint") == me.feature_type]
    else:
        if ":" not in feature_id:
            raise ApiError("FEATURE_NOT_FOUND", f"Feature {feature_id!r} not found.", 404)
        layer = catalog.layers.get(feature_id.split(":", 1)[0]) if catalog.ready else None
        if layer is None or feature_id not in layer.by_id:
            raise ApiError("FEATURE_NOT_FOUND", f"Feature {feature_id!r} not found.", 404)
        af = layer.by_id[feature_id]
        j = ai_feature_json(layer, af)
        out = dict(id=feature_id, kind="ai_prediction", geometry=j["geometry"], area_m2=j["properties"]["area_m2"],
                   source_layer=layer.id, source_feature_id=feature_id, review_status=None,
                   confidence=_confidence(af.props, layer.meta.get("score_label")),
                   provenance=j["properties"]["provenance"], properties=j["properties"])
        me = TopoFeat(feature_id, af.utm, af.props.get("feature_type", "building_footprint"))
        others = [(layer.features[i].id, layer.features[i].utm)
                  for i in layer.utm_tree.query(af.utm, predicate="dwithin", distance=tol) if layer.features[i].id != feature_id
                  and layer.features[i].props.get("feature_type", "building_footprint") == me.feature_type]
    rels = []
    if me.utm.is_valid:
        for oid, og in others:
            if not og.is_valid:
                continue
            r = pair_relation(me, TopoFeat(oid, og, me.feature_type), tol)
            if r:
                d = {k: v for k, v in r.items() if k in ("iou", "area_m2", "fraction_of_smaller", "length_m")}
                rels.append({"type": r["type"], "feature_id": oid, "detail": d})
    out["topology"] = {"relationships": sorted(rels, key=lambda r: (r["type"], r["feature_id"])), "valid_geometry": bool(me.utm.is_valid),
                       "tolerance_m": tol}
    return out
