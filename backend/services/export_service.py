"""Validated export. Reuses the existing, tested writer in app/server.py (new timestamped directory, never overwrites,
EPSG:32719 + WGS84 GeoJSON, 1 % repair rule) and adds server-side validation, topology summary and manifest fields."""

import importlib.util
import json
import hashlib
import os
import secrets
import shutil
import tempfile
from datetime import datetime, timezone
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


def export_features(fc: dict, note: str, settings, site_bounds, require_topology_clean: bool = False,
                    analysis_crs: str = C.ANALYSIS_CRS, provenance: dict | None = None) -> dict:
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
            chk = check_polygon((f or {}).get("geometry"), "EPSG:4326", site_bounds, f"feature {i}", analysis_crs)
        except ApiError as e:
            errors.append({"feature_index": i, "code": e.code, "message": e.message})
            continue
        if chk.repairs:
            repairs.append({"feature_index": i, **chk.repairs[0]})
        p.update(disclaimer=C.DISCLAIMER, feature_type=p.get("feature_type", "building_footprint"), workspace_id=f.get("id") or p.get("workspace_id"))
        rows.append({**{k: v for k, v in p.items() if k != "geometry"}, "geometry": chk.geometry})
        topo.append(TopoFeat(str((f or {}).get("id") or p.get("workspace_id") or f"feature-{i}"), to_utm(chk.geometry, analysis_crs=analysis_crs)))
    if errors:
        raise ApiError("EXPORT_VALIDATION_FAILED", f"{len(errors)} feature(s) failed validation; nothing was exported.", 422,
                       {"errors": errors[:50]})
    # Overlap checks apply within a feature class; a road/cover layer can legitimately cross a building layer.
    results = []
    for kind in sorted({r["feature_type"] for r in rows}):
        subset = [t for r, t in zip(rows, topo) if r["feature_type"] == kind]
        results.append(analyze(subset, analysis_crs=analysis_crs))
    result = results[0]
    if len(results) > 1:
        result = {**result, "valid": all(r["valid"] for r in results), "conflicts": [c for r in results for c in r["conflicts"]],
                  "summary": {"feature_count": len(rows), "by_class": {k: r["summary"] for k, r in zip(sorted({r["feature_type"] for r in rows}), results)}}}
    if require_topology_clean and not result["valid"]:
        raise ApiError("TOPOLOGY_CONFLICTS", "Export blocked: topology conflicts exist and require_topology_clean was set.", 422,
                       {"summary": result["summary"]})
    try:
        if analysis_crs == C.ANALYSIS_CRS and provenance is None:
            meta = _legacy_server().write_export(rows, str(note or ""), settings.export_root, settings.data_dir)
        else:
            meta = _write_site_export(rows, str(note or ""), settings, analysis_crs)
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
    if provenance:
        meta.update(provenance)
    (out_dir / "export_metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def _write_site_export(rows, note, settings, analysis_crs):
    import geopandas as gpd
    root = settings.export_root
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".export-", dir=root))
    identifier = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + secrets.token_hex(4)
    final = root / identifier
    try:
        flat = [{k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in r.items()} for r in rows]
        wgs = gpd.GeoDataFrame(flat, geometry="geometry", crs=C.WGS84)
        metric = wgs.to_crs(analysis_crs)
        if not metric.is_valid.all():
            raise ValueError("Geometry became invalid in the site analysis CRS.")
        metric["area_m2"] = metric.area.round(3)
        wgs["area_m2"] = metric["area_m2"].values
        analysis_file = "features_reviewed_" + analysis_crs.replace(":", "") + ".geojson"
        wgs_file = "features_reviewed_WGS84.geojson"
        metric.to_file(staging / analysis_file, driver="GeoJSON")
        wgs.to_file(staging / wgs_file, driver="GeoJSON")
        for kind in sorted(metric.feature_type.unique()):
            metric[metric.feature_type == kind].to_file(staging / "features_reviewed.gpkg", layer=kind, driver="GPKG")
        manifest = settings.data_dir / "manifest.json"
        meta = {"dir": str(final), "exported_utc": datetime.now(timezone.utc).isoformat(), "note": note[:500],
                "disclaimer": C.DISCLAIMER, "feature_count": len(rows), "total_area_m2": round(float(metric.area.sum()), 3),
                "by_origin": _count(r.get("origin") for r in rows), "by_review_status": _count(r.get("review_status") for r in rows),
                "by_feature_type": _count(r["feature_type"] for r in rows), "repaired_after_projection": [],
                "data_bundle_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                "files": {"analysis_crs": {"file": analysis_file, "crs": analysis_crs},
                          "wgs84": {"file": wgs_file, "crs": "EPSG:4326 (RFC 7946)"},
                          "geopackage": {"file": "features_reviewed.gpkg", "crs": analysis_crs}}}
        os.rename(staging, final)
        return meta
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _count(it) -> dict:
    d: dict = {}
    for x in it:
        d[str(x)] = d.get(str(x), 0) + 1
    return d
