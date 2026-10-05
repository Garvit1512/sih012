"""Validated export. Reuses the existing, tested writer in app/server.py (new timestamped directory, never overwrites,
EPSG:32719 + WGS84 GeoJSON, 1 % repair rule) and adds server-side validation, topology summary and manifest fields."""

import importlib.util
import json
from pathlib import Path

from shapely.geometry import mapping

from .. import config as C
from ..errors import ApiError
from ..utils.crs import to_utm
from ..utils.geometry import check_polygon
from .review_service import ORIGINS, STATUSES
from .topology_service import TopoFeat, analyze

_legacy = None


def _legacy_server():
    global _legacy
    if _legacy is None:
        spec = importlib.util.spec_from_file_location("sih_legacy_server", C.ROOT / "app" / "server.py")
        _legacy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_legacy)
    return _legacy


def export_features(fc: dict, note: str, settings, site_bounds, require_topology_clean: bool = False) -> dict:
    if not isinstance(fc, dict) or fc.get("type") != "FeatureCollection" or not isinstance(fc.get("features"), list):
        raise ApiError("INVALID_REQUEST", "features must be a GeoJSON FeatureCollection.", 422)
    feats = fc["features"]
    if not feats:
        raise ApiError("NOTHING_TO_EXPORT", "No features to export.", 422)
    if len(feats) > C.MAX_FEATURES_PER_REQUEST:
        raise ApiError("TOO_MANY_FEATURES", "Too many features in one export.", 422)
    rows, topo, repairs, errors = [], [], [], []
    for i, f in enumerate(feats):
        p = dict((f or {}).get("properties") or {})
        try:
            if p.get("origin") not in ORIGINS:
                raise ApiError("INVALID_PROVENANCE", f"feature {i}: origin must be one of {list(ORIGINS)}.", 422)
            if p.get("review_status") not in STATUSES:
                raise ApiError("INVALID_PROVENANCE", f"feature {i}: review_status must be one of {list(STATUSES)}.", 422)
            chk = check_polygon((f or {}).get("geometry"), "EPSG:4326", site_bounds, f"feature {i}")
        except ApiError as e:
            errors.append({"feature_index": i, "code": e.code, "message": e.message})
            continue
        if chk.repairs:
            repairs.append({"feature_index": i, **chk.repairs[0]})
        p.update(disclaimer=C.DISCLAIMER, feature_type="building_footprint")
        rows.append({**{k: v for k, v in p.items() if k != "geometry"}, "geometry": chk.geometry})
        topo.append(TopoFeat(str((f or {}).get("id") or p.get("workspace_id") or f"feature-{i}"), to_utm(chk.geometry)))
    if errors:
        raise ApiError("EXPORT_VALIDATION_FAILED", f"{len(errors)} feature(s) failed validation; nothing was exported.", 422,
                       {"errors": errors[:50]})
    result = analyze(topo)
    if require_topology_clean and not result["valid"]:
        raise ApiError("TOPOLOGY_CONFLICTS", "Export blocked: topology conflicts exist and require_topology_clean was set.", 422,
                       {"summary": result["summary"]})
    legacy = _legacy_server()
    try:
        meta = legacy.write_export(rows, str(note or ""), settings.export_root, settings.data_dir)
    except ValueError as e:
        raise ApiError("EXPORT_VALIDATION_FAILED", str(e), 422) from None
    out_dir = Path(meta.pop("dir"))
    meta["export_id"] = out_dir.name
    try:
        meta["dir"] = str(out_dir.relative_to(C.ROOT))
    except ValueError:
        meta["dir"] = out_dir.name
    meta["export_disclaimer"] = C.EXPORT_DISCLAIMER
    meta["topology"] = {"valid": result["valid"], "summary": result["summary"], "tolerances": result["tolerances"],
                        "conflicts": [{k: c[k] for k in ("id", "type", "severity", "features", "message", "metrics")} for c in result["conflicts"]],
                        "note": "Conflicts do not block export unless require_topology_clean was requested."}
    meta["input_repairs"] = repairs
    meta["by_source_layer"] = _count(r.get("source_layer") for r in rows)
    (out_dir / "export_metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def _count(it) -> dict:
    d: dict = {}
    for x in it:
        d[str(x)] = d.get(str(x), 0) + 1
    return d
