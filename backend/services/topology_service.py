"""Real topology checks between extracted building footprints (Shapely, metric EPSG:32719).

Detects: invalid geometry, overlaps, duplicates / near-duplicates, shared-boundary relationships and narrow
gaps (slivers). All thresholds are the documented constants in backend/config.py; nothing is simulated.
These are relationships between *extracted footprints*, not legal cadastral parcel topology.
"""

import re
from dataclasses import dataclass

from shapely.geometry import LineString, MultiLineString, Point, mapping
from shapely.ops import linemerge, unary_union
from shapely.strtree import STRtree
from shapely.validation import explain_validity

from .. import config as C
from ..utils.crs import to_wgs
from ..utils.geometry import polygonal


@dataclass
class TopoFeat:
    id: str
    utm: object          # shapely geometry in EPSG:32719
    feature_type: str = "building_footprint"


def tolerances(shared_tol: float | None = None, analysis_crs: str = C.ANALYSIS_CRS) -> dict:
    return {
        "crs": analysis_crs, "units": "metres / square metres",
        "overlap_min_area_m2": C.OVERLAP_MIN_AREA_M2, "overlap_min_fraction_of_smaller": C.OVERLAP_MIN_FRACTION,
        "overlap_error_fraction_of_smaller": C.OVERLAP_ERROR_FRACTION, "duplicate_iou": C.DUPLICATE_IOU,
        "shared_boundary_tolerance_m": shared_tol or C.SHARED_BOUNDARY_TOL_M,
        "shared_boundary_min_length_m": C.SHARED_BOUNDARY_MIN_LEN_M,
        "gap_max_width_m": C.GAP_MAX_WIDTH_M, "gap_min_area_m2": C.GAP_MIN_AREA_M2, "gap_min_length_m": C.GAP_MIN_LENGTH_M,
    }


def _lines(g):
    """Only the linear parts of a geometry, merged."""
    parts = [x for x in getattr(g, "geoms", [g]) if isinstance(x, (LineString, MultiLineString)) and not x.is_empty]
    flat = []
    for p in parts:
        flat.extend(p.geoms if isinstance(p, MultiLineString) else [p])
    if not flat:
        return []
    merged = linemerge(flat)
    return list(merged.geoms) if isinstance(merged, MultiLineString) else [merged]


def shared_runs(a, b, tol: float):
    """Parts of b's boundary that lie within `tol` of a's boundary (the neighbour's side of a shared edge)."""
    return _lines(b.boundary.intersection(a.boundary.buffer(tol)))


def pair_relation(a: TopoFeat, b: TopoFeat, tol: float = C.SHARED_BOUNDARY_TOL_M):
    """Classify the relationship of two valid footprints: duplicate | overlap | shared_boundary | None."""
    if a.feature_type != b.feature_type:
        return None
    inter = a.utm.intersection(b.utm)
    ia = inter.area
    smaller = min(a.utm.area, b.utm.area)
    union_area = a.utm.area + b.utm.area - ia
    iou = ia / union_area if union_area > 0 else 0.0
    if iou >= C.DUPLICATE_IOU:
        return {"type": "duplicate", "iou": round(iou, 4), "intersection": polygonal(inter)}
    if ia >= C.OVERLAP_MIN_AREA_M2 and ia / smaller >= C.OVERLAP_MIN_FRACTION:
        return {"type": "overlap", "intersection": polygonal(inter), "area_m2": round(ia, 3),
                "fraction_of_smaller": round(ia / smaller, 4)}
    if a.utm.distance(b.utm) <= tol:
        runs = shared_runs(a.utm, b.utm, tol)
        length = sum(r.length for r in runs)
        if length >= C.SHARED_BOUNDARY_MIN_LEN_M:
            return {"type": "shared_boundary", "length_m": round(length, 3), "runs": runs}
    return None


def _invalid_point(g):
    m = re.search(r"\[([-\d.eE+]+) ([-\d.eE+]+)\]", explain_validity(g))
    return Point(float(m.group(1)), float(m.group(2))) if m else g.representative_point()


def analyze(feats: list[TopoFeat], tol: float = C.SHARED_BOUNDARY_TOL_M, analysis_crs: str = C.ANALYSIS_CRS) -> dict:
    conflicts, rels = [], []
    valid = [f for f in feats if f.utm.is_valid and not f.utm.is_empty]
    for f in feats:
        if f.utm.is_empty or not f.utm.is_valid:
            why = explain_validity(f.utm) if not f.utm.is_empty else "Empty geometry"
            conflicts.append({"type": "invalid_geometry", "severity": "error", "features": [f.id], "geom": None if f.utm.is_empty else _invalid_point(f.utm),
                              "message": f"Invalid footprint geometry: {why}.", "metrics": {"reason": why}})
    if len(valid) > 1:
        tree = STRtree([f.utm for f in valid])
        for i, j in sorted(map(tuple, tree.query(tree.geometries, predicate="dwithin", distance=max(tol, C.GAP_MAX_WIDTH_M)).T)):
            if i >= j:
                continue
            a, b = valid[i], valid[j]
            r = pair_relation(a, b, tol)
            if r is None:
                continue
            ids = sorted([a.id, b.id])
            if r["type"] == "duplicate":
                conflicts.append({"type": "duplicate", "severity": "error", "features": ids, "geom": r["intersection"],
                                  "message": "Two extracted footprints are duplicates or near-duplicates.", "metrics": {"iou": r["iou"]}})
            elif r["type"] == "overlap":
                sev = "error" if r["fraction_of_smaller"] >= C.OVERLAP_ERROR_FRACTION else "warning"
                conflicts.append({"type": "overlap", "severity": sev, "features": ids, "geom": r["intersection"],
                                  "message": "Two extracted footprints overlap.",
                                  "metrics": {"overlap_area_m2": r["area_m2"], "fraction_of_smaller": r["fraction_of_smaller"]}})
            else:
                rels.append({"type": "shared_boundary", "features": ids, "length_m": r["length_m"],
                             "geometry": mapping(to_wgs(unary_union(r["runs"]), analysis_crs))})
        for kind in sorted({f.feature_type for f in valid}):
            group = [f for f in valid if f.feature_type == kind]
            if len(group) > 1:
                conflicts += _gaps(group, STRtree([f.utm for f in group]))
    order = {"invalid_geometry": 0, "duplicate": 1, "overlap": 2, "gap_sliver": 3}
    conflicts.sort(key=lambda c: (order[c["type"]], c["features"]))
    out = []
    for n, c in enumerate(conflicts, 1):
        g = c.pop("geom")
        out.append({"id": f"conflict-{n:03d}", **c, "geometry": mapping(to_wgs(g, analysis_crs)) if g is not None and not g.is_empty else None})
    rels.sort(key=lambda r: r["features"])
    by_type: dict = {}
    for c in out:
        by_type[c["type"]] = by_type.get(c["type"], 0) + 1
    return {"valid": not out, "conflicts": out, "relationships": rels,
            "summary": {"feature_count": len(feats), "conflict_count": len(out), "by_type": by_type,
                        "by_severity": {s: sum(c["severity"] == s for c in out) for s in ("warning", "error")},
                        "shared_boundary_count": len(rels)},
            "tolerances": tolerances(tol, analysis_crs)}


def _gaps(valid: list[TopoFeat], tree: STRtree) -> list[dict]:
    """Narrow gaps between >= 2 footprints: morphological closing of the union minus the union."""
    r = C.GAP_MAX_WIDTH_M / 2
    union = unary_union([f.utm for f in valid])
    gaps = union.buffer(r, 4).buffer(-r, 4).difference(union)
    found = []
    for g in getattr(gaps, "geoms", [gaps]):
        if g.is_empty or g.area < C.GAP_MIN_AREA_M2 or g.geom_type != "Polygon":
            continue
        rect = g.minimum_rotated_rectangle.exterior.coords
        if max(Point(rect[0]).distance(Point(rect[1])), Point(rect[1]).distance(Point(rect[2]))) < C.GAP_MIN_LENGTH_M:
            continue
        near = sorted({valid[i].id for i in tree.query(g, predicate="dwithin", distance=0.05)})
        if len(near) >= 2:
            found.append({"type": "gap_sliver", "severity": "warning", "features": near, "geom": g,
                          "message": f"Narrow gap (< {C.GAP_MAX_WIDTH_M} m wide) between extracted footprints.",
                          "metrics": {"area_m2": round(g.area, 3)}})
    return found


def shared_boundary(target: TopoFeat, others: list[TopoFeat], vertex_utm: Point, tol: float, analysis_crs: str = C.ANALYSIS_CRS) -> dict:
    """Which neighbour shares the edge the user is dragging, and which of its edge to flash.

    The neighbour must have its boundary within `tol` of the dragged vertex and share a boundary run of at
    least SHARED_BOUNDARY_MIN_LEN_M with the target. The returned edge is the neighbour's boundary run
    nearest the vertex (WGS84 LineString).
    """
    cands = []
    for o in others:
        if o.id == target.id or o.feature_type != target.feature_type or not o.utm.is_valid or o.utm.is_empty:
            continue
        d = o.utm.boundary.distance(vertex_utm)
        if d > tol:
            continue
        runs = shared_runs(target.utm, o.utm, tol) if target.utm.is_valid else []
        length = sum(r.length for r in runs)
        if length >= C.SHARED_BOUNDARY_MIN_LEN_M:
            cands.append((d, o, runs, length))
    base = {"tolerance_m": tol, "tolerances": tolerances(tol, analysis_crs)}
    if not cands:
        return {"shared": False, "neighbor_feature_id": None, "edge": None, "distance_m": None, "shared_length_m": None,
                "neighbors": [], **base}
    cands.sort(key=lambda c: (c[0], c[1].id))
    d, o, runs, length = cands[0]
    run = min(runs, key=lambda r: r.distance(vertex_utm))
    # Trim the run to the part of the neighbour's boundary within tol of the vertex's local edge? Keep the whole
    # contiguous shared run: it is the edge the two footprints visibly share.
    return {"shared": True, "neighbor_feature_id": o.id, "edge": mapping(to_wgs(run, analysis_crs)), "distance_m": round(d, 4),
            "shared_length_m": round(length, 3), "neighbors": [c[1].id for c in cands], **base}
