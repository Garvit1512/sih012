"""Supported closed faces, reference conflicts and local field-evidence assertions.

No roof buffering, ownership inference, automatic gap filling or survey snapping.
Parcel and evidence revisions share one atomic journal, separate from building review.
"""
import hashlib
import json

import numpy as np
from shapely import get_coordinates, line_merge
from shapely.geometry import LineString, box, mapping, shape
from shapely.ops import polygonize_full, unary_union

from ..errors import ApiError
from ..utils.crs import normalise_crs
from .review_service import WorkspaceStore, _now

AREA_TOLERANCE = .01
FIELD_TOLERANCE_M = .5


def geometry_hash(feature):
    return hashlib.sha256(json.dumps(feature["geometry"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class ParcelStore(WorkspaceStore):
    def _path(self):
        return self.dir / f"{self.site_id}-parcels.json"

    def _geometry(self, geometry, crs, allowed):
        try:
            g = shape(geometry)
        except Exception:
            raise ApiError("INVALID_GEOMETRY", "Unreadable evidence geometry.", 422) from None
        coordinates = get_coordinates(g, include_z=False)
        if (g.geom_type not in allowed or g.is_empty or not g.is_valid or g.has_z
                or len(coordinates) > 100000 or not np.isfinite(coordinates).all()):
            raise ApiError("INVALID_GEOMETRY", "Use finite, valid 2D geometry of the declared evidence type.", 422)
        src = normalise_crs(crs, self.analysis_crs)
        if src == "EPSG:4326" and (np.abs(coordinates[:, 0]).max() > 180 or np.abs(coordinates[:, 1]).max() > 90):
            raise ApiError("INVALID_GEOMETRY", "Evidence longitude/latitude is out of range.", 422)
        wgs = self._wgs(self._metric(g, src))
        if not wgs.is_valid or not np.isfinite(get_coordinates(wgs)).all() or not box(*self.bounds).buffer(1e-8).covers(wgs):
            raise ApiError("OUTSIDE_SITE", "Evidence must lie within the site's imagery extent.", 422)
        return wgs

    def _entity(self, geometry, kind, properties):
        return self._new(geometry, {"feature_type": kind, "origin": "imported_evidence", "review_status": "suggested",
                                    "functional_use": "unknown", **properties})

    def _parcel(self, ws, fid):
        f = ws["features"].get(fid)
        if f is None or f["properties"]["feature_type"] != "parcel":
            raise ApiError("PARCEL_NOT_FOUND", "Parcel does not exist in this site's saved parcel workspace.", 404)
        return f

    def import_evidence(self, body):
        fc, kind = body["features"], body["kind"]
        if fc.get("type") != "FeatureCollection" or not isinstance(fc.get("features"), list) or not 1 <= len(fc["features"]) <= 2000:
            raise ApiError("INVALID_EVIDENCE", "Import a GeoJSON FeatureCollection with 1–2,000 features.", 422)
        allowed = {"boundary_segment": ("LineString", "MultiLineString"), "survey_observation": ("Point", "LineString", "MultiLineString"),
                   "reference_parcel": ("Polygon", "MultiPolygon"), "excluded_area": ("Polygon", "MultiPolygon"), "survey_extent": ("Polygon",)}[kind]
        ws = self._load()
        target = self._parcel(ws, body["parcel_id"]) if kind == "survey_observation" else None
        if kind == "survey_extent" and (len(fc["features"]) != 1 or any(f["properties"]["feature_type"] == kind for f in ws["features"].values())):
            raise ApiError("EXTENT_EXISTS", "Declare one survey extent per parcel workspace.", 422)
        added = []
        for i, feature in enumerate(fc["features"]):
            geom = self._geometry(feature.get("geometry"), body["crs"], allowed)
            props = {key: body.get(key) for key in ("source", "captured_at", "horizontal_datum", "accuracy_m", "reviewed", "observation_method")}
            props.update(external_id=str(feature.get("id", i)), source_crs=body["crs"], source_geometry=feature["geometry"],
                         reviewer=self._audit_context["actor"], assertion_kind="local reviewer; source authenticity not independently validated")
            if target:
                props.update(parcel_id=target["id"], parcel_geometry_sha256=geometry_hash(target))
            f = self._entity(geom, kind, props)
            ws["features"][f["id"]] = f
            added.append(f)
        if len(ws["features"]) > 5000:
            raise ApiError("TOO_MANY_FEATURES", "Split the pilot area into smaller sites (limit 5,000 parcel/evidence entities).", 422)
        self._save(ws)
        return {"features": added, "removed_ids": [], "info": {"kind": kind, "imported": len(added)}}

    def propose(self, mode):
        ws = self._load()
        inputs = [f for f in ws["features"].values() if f["properties"]["feature_type"] == "boundary_segment" and f["properties"].get("reviewed")]
        references = [f for f in ws["features"].values() if f["properties"]["feature_type"] == "reference_parcel"]
        if not inputs:
            raise ApiError("BOUNDARIES_REQUIRED", "Import reviewed visible or measured boundary lines first.", 422)
        if mode == "reference_reconciliation" and not references:
            raise ApiError("REFERENCES_REQUIRED", "Reference reconciliation needs supplied reference parcels.", 422)
        lines = unary_union([self._metric(shape(f["geometry"])) for f in inputs])
        faces, cuts, dangles, invalid = polygonize_full(lines)
        extents = [self._metric(shape(f["geometry"])) for f in ws["features"].values() if f["properties"]["feature_type"] == "survey_extent"]
        exclusions = unary_union([self._metric(shape(f["geometry"])) for f in ws["features"].values() if f["properties"]["feature_type"] == "excluded_area"])
        existing = [self._metric(shape(f["geometry"])) for f in ws["features"].values() if f["properties"]["feature_type"] == "parcel"]
        added, conflicts = [], []
        for face in faces.geoms:
            if face.area <= AREA_TOLERANCE:
                continue
            if extents and not extents[0].buffer(.001).covers(face):
                conflicts.append({"type": "outside_survey_extent", "geometry": mapping(self._wgs(face))}); continue
            if face.intersection(exclusions).area > AREA_TOLERANCE:
                conflicts.append({"type": "excluded_area_intersection", "geometry": mapping(self._wgs(face))}); continue
            if any(face.symmetric_difference(other).area <= AREA_TOLERANCE for other in existing):
                continue
            if any(face.intersection(other).area > AREA_TOLERANCE for other in existing):
                conflicts.append({"type": "existing_parcel_overlap", "geometry": mapping(self._wgs(face))}); continue
            support = [f["id"] for f in inputs if face.boundary.intersection(self._metric(shape(f["geometry"]))).length > .001]
            matches = []
            for ref in references:
                ref_geom = self._metric(shape(ref["geometry"]))
                area = face.intersection(ref_geom).area
                if area > AREA_TOLERANCE:
                    matches.append({"reference_id": ref["id"], "iou": round(area / face.union(ref_geom).area, 6),
                                    "reference_fraction": round(area / ref_geom.area, 6)})
            f = self._entity(self._wgs(face), "parcel", {"origin": "boundary_proposal", "parcel_state": "proposed", "proposal_mode": mode,
                "boundary_support_ids": support, "boundary_evidence_coverage": min(1.0, face.boundary.intersection(lines).length / face.length),
                "boundary_support_review": "reviewed input lines", "reference_matches": matches,
                "reference_conflict": bool(references) and not any(m["iou"] >= .9 for m in matches),
                "ownership": None, "legal_status": "preliminary; no legal certification"})
            ws["features"][f["id"]] = f; added.append(f); existing.append(face)
        if len(ws["features"]) > 5000:
            raise ApiError("TOO_MANY_FEATURES", "Too many parcel/evidence entities; use a smaller area.", 422)
        self._save(ws)
        return {"features": added, "removed_ids": [], "info": {"closed_faces": len(added), "conflicts": conflicts,
                "unresolved_linework": {key: {"length_m": round(geom.length, 3), "geometry": mapping(self._wgs(geom))}
                                       for key, geom in (("cuts", cuts), ("dangles", dangles), ("invalid_rings", invalid))},
                "note": "Open/occluded boundaries remain unresolved; no inferred ownership or automatic gap filling."}}

    def diagnostics(self, snapshot=None):
        snapshot = snapshot or self.snapshot()
        features = snapshot["features"]
        parcels = [f for f in features if f["properties"]["feature_type"] == "parcel"]
        conflicts = []
        geoms = {f["id"]: self._metric(shape(f["geometry"])) for f in parcels}
        for i, a in enumerate(parcels):
            if not geoms[a["id"]].is_valid:
                conflicts.append({"type": "invalid", "features": [a["id"]]})
            for b in parcels[i + 1:]:
                overlap = geoms[a["id"]].intersection(geoms[b["id"]]).area
                if overlap > AREA_TOLERANCE:
                    conflicts.append({"type": "overlap", "features": [a["id"], b["id"]], "area_m2": round(overlap, 3)})
        extents = [self._metric(shape(f["geometry"])) for f in features if f["properties"]["feature_type"] == "survey_extent"]
        exclusions = unary_union([self._metric(shape(f["geometry"])) for f in features if f["properties"]["feature_type"] == "excluded_area"])
        for fid, geom in geoms.items():
            if extents and not extents[0].buffer(.001).covers(geom):
                conflicts.append({"type": "outside_survey_extent", "features": [fid]})
            if geom.intersection(exclusions).area > AREA_TOLERANCE:
                conflicts.append({"type": "excluded_area_intersection", "features": [fid]})
        boundaries = [self._metric(shape(f["geometry"])) for f in features
                      if f["properties"]["feature_type"] == "boundary_segment" and f["properties"].get("reviewed")]
        _, cuts, dangles, invalid = polygonize_full(unary_union(boundaries))
        gap = extents[0].difference(unary_union(list(geoms.values()) + [exclusions])) if extents else None
        return {"valid": not conflicts, "conflicts": conflicts, "parcel_count": len(parcels),
                "unresolved_extent_area_m2": round(gap.area, 3) if gap is not None else None,
                "gap_note": "Unmapped declared extent; requires boundary evidence, never filled automatically.",
                "unresolved_linework_m": round(cuts.length + dangles.length + invalid.length, 3),
                "field_tolerance_m": FIELD_TOLERANCE_M, "overlap_area_tolerance_m2": AREA_TOLERANCE}

    def set_parcel_state(self, fid, state, record_refs):
        ws = self._load(); f = self._parcel(ws, fid); p = f["properties"]
        if state == "field_checked":
            if p["parcel_state"] != "reviewed":
                raise ApiError("REVIEW_REQUIRED", "Review the current parcel geometry before a field check.", 422)
            if not self.diagnostics({"features": list(ws["features"].values())})["valid"] or p.get("reference_conflict"):
                raise ApiError("PARCEL_CONFLICT", "Resolve parcel/reference conflicts before recording a field check.", 422)
            eligible = [e for e in ws["features"].values() if e["properties"]["feature_type"] == "survey_observation"
                and e["properties"].get("parcel_id") == fid and e["properties"].get("parcel_geometry_sha256") == geometry_hash(f)
                and e["properties"].get("reviewed") and e["properties"].get("observation_method") in ("gnss", "ets")
                and e["properties"].get("accuracy_m") is not None and e["properties"]["accuracy_m"] <= FIELD_TOLERANCE_M]
            observed = unary_union([self._metric(shape(e["geometry"])).buffer(FIELD_TOLERANCE_M) for e in eligible])
            boundary = self._metric(shape(f["geometry"])).boundary
            coverage = boundary.intersection(observed).length / boundary.length
            if coverage < .95:
                raise ApiError("FIELD_EVIDENCE_REQUIRED", "Current reviewed GNSS/ETS evidence must cover at least 95% of this parcel boundary within 0.5 m.", 422,
                               {"boundary_coverage": coverage})
            p["field_check"] = {"status": "current", "geometry_sha256": geometry_hash(f), "observation_ids": [e["id"] for e in eligible],
                "boundary_coverage": coverage, "reviewer": self._audit_context["actor"], "checked_at": _now(),
                "revision": ws["revision"] + 1, "record_refs": record_refs,
                "assertion_kind": "local review of supplied observations; not authenticated survey certification"}
        elif p.get("field_check"):
            p["field_check"]["status"] = "needs_review"
        p.update(parcel_state=state, record_refs=record_refs, updated_utc=_now(), reviewer=self._audit_context["actor"])
        self._save(ws)
        return {"features": [f], "removed_ids": [], "info": {"state": state}}

    def _replace_geometry(self, feature, metric, refs):
        feature["geometry"] = mapping(self._wgs(metric))
        p = feature["properties"]
        p.update(area_m2=round(metric.area, 3), parcel_state="needs_survey", review_status="suggested", updated_utc=_now(),
                 boundary_support_review="needs_review after geometry change", edit_evidence_refs=refs)
        if p.get("field_check"):
            p["field_check"].update(status="needs_review", invalidated_at=_now())

    def edit_parcel(self, fid, geometry, crs, refs):
        ws = self._load(); f = self._parcel(ws, fid)
        replacement = self._metric(self._geometry(geometry, crs, ("Polygon", "MultiPolygon")))
        original = self._metric(shape(f["geometry"]))
        for other in ws["features"].values():
            if other["id"] == fid or other["properties"]["feature_type"] != "parcel": continue
            neighbour = self._metric(shape(other["geometry"]))
            if original.boundary.intersection(neighbour.boundary).length > .01:
                raise ApiError("SHARED_EDGE_REQUIRED", "Use the atomic shared-edge editor for parcels with neighbours.", 422)
            if replacement.intersection(neighbour).area > AREA_TOLERANCE:
                raise ApiError("PARCEL_OVERLAP", "Edited parcel would overlap a neighbouring parcel.", 422)
        self._replace_geometry(f, replacement, refs); self._save(ws)
        return {"features": [f], "removed_ids": [], "info": {"field_check_invalidated": True}}

    def edit_shared_edge(self, ids, geometry, crs, refs):
        if len(set(ids)) != 2:
            raise ApiError("INVALID_REQUEST", "Choose two distinct neighbouring parcels.", 422)
        ws = self._load(); features = [self._parcel(ws, fid) for fid in ids]
        geoms = [self._metric(shape(f["geometry"])) for f in features]
        if any(g.geom_type != "Polygon" or len(g.interiors) for g in geoms):
            raise ApiError("UNSUPPORTED_SHARED_EDGE", "The local editor supports two simple parcels without holes.", 422)
        shared = line_merge(geoms[0].boundary.intersection(geoms[1].boundary))
        if shared.geom_type != "LineString" or shared.length < .01:
            raise ApiError("SHARED_EDGE_REQUIRED", "Parcels must have one connected common edge.", 422)
        replacement = self._metric(self._geometry(geometry, crs, ("LineString",)))
        endpoints = [shared.coords[0], shared.coords[-1]]
        coords = list(replacement.coords)
        if np.linalg.norm(np.array(coords[0]) - endpoints[1]) < .01: endpoints.reverse()
        if max(np.linalg.norm(np.array(coords[0]) - endpoints[0]), np.linalg.norm(np.array(coords[-1]) - endpoints[1])) > .01:
            raise ApiError("EDGE_ENDPOINTS", "Keep the shared edge's outer endpoints fixed (within 1 cm).", 422)
        coords[0], coords[-1] = endpoints
        replacement = LineString(coords)
        if not replacement.is_simple:
            raise ApiError("INVALID_GEOMETRY", "The replacement edge cannot cross itself.", 422)
        rebuilt = []
        for g in geoms:
            faces, cuts, dangles, invalid = polygonize_full(unary_union([g.boundary.difference(shared), replacement]))
            if len(faces.geoms) != 1 or cuts.length > .001 or dangles.length > .001 or invalid.length > .001:
                raise ApiError("INVALID_EDGE", "The edge must leave each neighbour as one valid closed parcel.", 422)
            rebuilt.append(faces.geoms[0])
        if (rebuilt[0].intersection(rebuilt[1]).area > AREA_TOLERANCE
                or unary_union(rebuilt).symmetric_difference(unary_union(geoms)).area > AREA_TOLERANCE):
            raise ApiError("INVALID_EDGE", "A shared-edge edit must preserve the neighbours' combined outer boundary.", 422)
        for f, g in zip(features, rebuilt): self._replace_geometry(f, g, refs)
        self._save(ws)
        return {"features": features, "removed_ids": [], "info": {"atomic_neighbours": ids, "field_checks_invalidated": ids,
                "endpoint_tolerance_m": .01, "evidence_refs": refs}}
