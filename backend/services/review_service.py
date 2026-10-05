"""Review workspace: editable copies of AI predictions plus human-created features.

AI predictions are immutable (layer_service). Every workspace feature carries provenance
(origin, source_layer, source_feature_id, parents) and a review status. Persistence is one JSON file per site,
written atomically; geometry is stored in WGS84 and every operation re-validates client geometry server-side.
"""

import json
import hashlib
import copy
import os
import secrets
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from shapely.geometry import LineString, Polygon, mapping, shape
from shapely.ops import unary_union

from .. import config as C
from ..errors import ApiError
from ..utils.crs import normalise_crs, to_utm, to_wgs
from ..utils.geometry import check_polygon, polygonal
from .layer_service import LayerStore

ORIGINS = ("ai_copy", "human_edited", "human_merged", "human_split", "human_drawn")
STATUSES = ("suggested", "accepted", "rejected")
ReviewOut = dict


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _round(v: float) -> float:
    return round(float(v), 3)


class WorkspaceStore:
    def __init__(self, directory: Path, layers: LayerStore | None, site_bounds: list[float] | None, site_id: str | None,
                 analysis_crs: str = C.ANALYSIS_CRS, require_revision: bool = False):
        self.dir, self.layers, self.bounds, self.site_id = directory, layers, site_bounds, site_id
        self.analysis_crs, self.require_revision = analysis_crs, require_revision
        self._audit_context = {}
        self._lock = threading.RLock()

    # ---- persistence ----
    def _path(self) -> Path:
        return self.dir / f"{self.site_id}.json"          # site_id is the catalog's own id, validated upstream

    def _load(self) -> dict:
        p = self._path()
        if not p.is_file():
            return {"site_id": self.site_id, "features": {}, "base_features": {}, "revision": 0, "history": []}
        ws = json.loads(p.read_text(encoding="utf-8"))
        ws.setdefault("base_features", copy.deepcopy(ws["features"]))
        ws.setdefault("revision", 0)
        ws.setdefault("history", [])
        return ws

    def _save(self, ws: dict) -> None:
        before = self._load()
        ids = set(before["features"]) | set(ws["features"])
        changed = [i for i in sorted(ids) if before["features"].get(i) != ws["features"].get(i)]
        if not changed:
            return
        ws["revision"] = before["revision"] + 1
        event = {"revision": ws["revision"], "timestamp": _now(), **self._audit_context,
                 "before": {i: before["features"][i] for i in changed if i in before["features"]},
                 "after": {i: copy.deepcopy(ws["features"][i]) for i in changed if i in ws["features"]}}
        ws["history"] = [*before["history"], event]
        self.dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(ws, f, allow_nan=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._path())
        finally:
            Path(tmp).unlink(missing_ok=True)

    def mutate(self, operation, args=(), expected_revision=None, actor="local-reviewer", reason="", duration_ms=0):
        """One local writer atomically validates revision and persists geometry plus audit."""
        with self._lock:
            revision = self._load()["revision"]
            if self.require_revision and expected_revision is None:
                raise ApiError("REVISION_REQUIRED", "Send the workspace revision with every change.", 428)
            if expected_revision is not None and expected_revision != revision:
                raise ApiError("STALE_REVISION", "The workspace changed; reload it before retrying.", 409, {"current_revision": revision})
            audit_operation = {"accepted": "accept", "rejected": "reject", "suggested": "reset"}.get(args[1], operation) if operation == "set_status" else operation
            self._audit_context = {"operation": audit_operation, "actor": actor, "reason": reason,
                                   "review_duration_ms": duration_ms, "actor_kind": "local_session_label"}
            try:
                result = getattr(self, operation)(*args)
                result["revision"] = self._load()["revision"]
                return result
            finally:
                self._audit_context = {}

    def snapshot(self, revision=None):
        with self._lock:
            ws = self._load()
            wanted = ws["revision"] if revision is None else revision
            if not isinstance(wanted, int) or not 0 <= wanted <= ws["revision"]:
                raise ApiError("REVISION_NOT_FOUND", "Workspace revision does not exist.", 404)
            features = copy.deepcopy(ws["base_features"])
            for event in ws["history"]:
                if event["revision"] > wanted:
                    break
                for fid in event["before"]:
                    features.pop(fid, None)
                features.update(copy.deepcopy(event["after"]))
            return {"site_id": self.site_id, "revision": wanted, "type": "FeatureCollection", "features": list(features.values())}

    def history(self, limit=50, offset=0):
        with self._lock:
            ws = self._load()
            rows = list(reversed(ws["history"]))
            return {"revision": ws["revision"], "total": len(rows), "events": rows[offset:offset + limit]}

    def _metric(self, geometry, crs="EPSG:4326"):
        return to_utm(geometry, crs, self.analysis_crs)

    def _wgs(self, geometry):
        return to_wgs(geometry, self.analysis_crs)

    # ---- reads ----
    def all(self) -> list[dict]:
        with self._lock:
            return list(self._load()["features"].values())

    def get(self, fid: str) -> dict:
        with self._lock:
            f = self._load()["features"].get(fid)
        if f is None:
            raise ApiError("FEATURE_NOT_FOUND", f"Workspace feature {fid!r} not found.", 404)
        return f

    def summary(self) -> dict:
        feats = self.all()
        by = {s: sum(f["properties"]["review_status"] == s for f in feats) for s in STATUSES}
        with self._lock:
            ws = self._load()
        return {"count": len(feats), "by_review_status": by, "revision": ws["revision"], "history_count": len(ws["history"]),
                "review_duration_ms": sum(e.get("review_duration_ms", 0) for e in ws["history"])}

    # ---- writes ----
    def _new(self, geom_wgs, props: dict) -> dict:
        fid = "ws-" + secrets.token_hex(5)
        now = _now()
        props = {**props, "area_m2": _round(self._metric(geom_wgs).area), "created_utc": now, "updated_utc": now}
        return {"type": "Feature", "id": fid, "geometry": mapping(geom_wgs), "properties": props}

    def add_from_layer(self, layer_id: str, feature_ids: list[str] | None, bbox) -> ReviewOut:
        if self.layers is None:
            raise ApiError("DATA_NOT_READY", "No data bundle.", 503)
        layer = self.layers.get(layer_id)
        if feature_ids:
            missing = [i for i in feature_ids if i not in layer.by_id]
            if missing:
                raise ApiError("FEATURE_NOT_FOUND", "Unknown AI feature id(s).", 404, {"ids": missing[:20]})
            chosen = [layer.by_id[i] for i in feature_ids]
        elif bbox is not None:
            chosen = layer.query_bbox(bbox)
        else:
            raise ApiError("INVALID_REQUEST", "Provide feature_ids or bbox (refusing to copy a whole layer implicitly).", 422)
        if len(chosen) > C.MAX_FEATURES_PER_REQUEST:
            raise ApiError("TOO_MANY_FEATURES", "Too many features requested.", 422)
        with self._lock:
            ws = self._load()
            have = {(f["properties"].get("source_layer"), f["properties"].get("source_feature_id")) for f in ws["features"].values()}
            added, skipped = [], []
            for af in chosen:
                if (layer.id, af.id) in have:
                    skipped.append(af.id)
                    continue
                f = self._new(af.wgs, {
                    "origin": "ai_copy", "review_status": "suggested", "source_layer": layer.id, "source_feature_id": af.id,
                    "source_id": af.source_id, "ai_score": af.props.get("score"), "ai_score_label": layer.meta.get("score_label"),
                    "ai_flags": af.props.get("flags") or "", "parents": [],
                    "feature_type": af.props.get("feature_type", "building_footprint"),
                    "class_name": af.props.get("class_name"), "functional_use": af.props.get("functional_use", "unknown"),
                    **{key: af.props[key] for key in ("functional_use_suggestion", "use_candidate") if key in af.props},
                })
                ws["features"][f["id"]] = f
                added.append(f)
            self._save(ws)
        return {"features": added, "removed_ids": [], "info": {"added": len(added), "skipped_already_in_workspace": skipped}}

    def set_status(self, fid: str, status: str) -> ReviewOut:
        assert status in STATUSES
        with self._lock:
            ws = self._load()
            f = ws["features"].get(fid)
            if f is None:
                raise ApiError("FEATURE_NOT_FOUND", f"Workspace feature {fid!r} not found.", 404)
            f["properties"]["review_status"] = status
            f["properties"]["updated_utc"] = _now()
            self._save(ws)
        return {"features": [f], "removed_ids": [], "info": {}}

    def edit(self, fid: str, geometry: dict, crs: str) -> ReviewOut:
        chk = check_polygon(geometry, crs, self.bounds, "edited geometry", self.analysis_crs)
        with self._lock:
            ws = self._load()
            f = ws["features"].get(fid)
            if f is None:
                raise ApiError("FEATURE_NOT_FOUND", f"Workspace feature {fid!r} not found.", 404)
            before = f["properties"]["area_m2"]
            f["geometry"] = mapping(chk.geometry)
            p = f["properties"]
            if p["origin"] == "ai_copy":
                p["origin"] = "human_edited"
            p["area_m2"] = _round(self._metric(chk.geometry).area)
            p["review_status"] = "suggested"
            if p.get("functional_use_review"):
                p["functional_use"] = "unknown"
                p["functional_use_review"]["status"] = "needs_review"
                p["functional_use_review"]["invalidated_utc"] = _now()
            if p.get("use_candidate"):
                p["functional_use_suggestion"] = "unknown"
                p["use_candidate"]["reason"] = "geometry changed; recompute candidate attributes"
            p["updated_utc"] = _now()
            if chk.repairs:
                p["repairs"] = (p.get("repairs") or []) + chk.repairs
            self._save(ws)
        change = (p["area_m2"] - before) / before if before else None
        return {"features": [f], "removed_ids": [], "info": {"area_before_m2": before, "area_after_m2": p["area_m2"],
                                                           "area_change_fraction": None if change is None else round(change, 4),
                                                           "repairs": chk.repairs}}

    def draw(self, geometry: dict, crs: str, status: str, feature_type="building_footprint", class_name=None) -> ReviewOut:
        if feature_type == "land_cover" and not class_name:
            raise ApiError("CLASS_REQUIRED", "A drawn cover polygon needs a cover class label.", 422)
        chk = check_polygon(geometry, crs, self.bounds, "drawn geometry", self.analysis_crs)
        with self._lock:
            ws = self._load()
            f = self._new(chk.geometry, {"origin": "human_drawn", "review_status": status, "parents": [],
                                        "feature_type": feature_type, "class_name": class_name, "functional_use": "unknown",
                                        **({"repairs": chk.repairs} if chk.repairs else {})})
            ws["features"][f["id"]] = f
            self._save(ws)
        return {"features": [f], "removed_ids": [], "info": {"repairs": chk.repairs}}

    def merge(self, ids: list[str]) -> ReviewOut:
        if len(set(ids)) != len(ids):
            raise ApiError("INVALID_REQUEST", "feature_ids must be distinct.", 422)
        with self._lock:
            ws = self._load()
            parts = []
            for i in ids:
                if i not in ws["features"]:
                    raise ApiError("FEATURE_NOT_FOUND", f"Workspace feature {i!r} not found.", 404)
                parts.append(ws["features"][i])
            classes = {(p["properties"].get("feature_type", "building_footprint"), p["properties"].get("class_name")) for p in parts}
            if len(classes) != 1:
                raise ApiError("CLASS_MISMATCH", "Merge features of the same type and cover class.", 422)
            u = polygonal(unary_union([self._metric(shape(p["geometry"])) for p in parts]))
            if not isinstance(u, Polygon) or u.is_empty or not u.is_valid:
                raise ApiError("MERGE_NOT_CONTIGUOUS", "The selected footprints do not form a single connected polygon.", 422)
            u_wgs = self._wgs(u)
            layers = sorted({p["properties"].get("source_layer") for p in parts if p["properties"].get("source_layer")})
            f = self._new(u_wgs, {"origin": "human_merged", "review_status": "suggested", "parents": list(ids),
                                  "source_layer": ",".join(layers) or None, "feature_type": next(iter(classes))[0],
                                  "class_name": next(iter(classes))[1], "functional_use": "unknown"})
            for i in ids:
                del ws["features"][i]
            ws["features"][f["id"]] = f
            self._save(ws)
        before = sum(p["properties"]["area_m2"] for p in parts)
        return {"features": [f], "removed_ids": list(ids), "info": {"area_before_m2": _round(before), "area_after_m2": f["properties"]["area_m2"]}}

    def split(self, fid: str, line: dict, crs: str, gap_m: float) -> ReviewOut:
        src = normalise_crs(crs, self.analysis_crs)
        try:
            ln = shape(line)
        except Exception:  # noqa: BLE001
            raise ApiError("INVALID_GEOMETRY", "Unreadable split line.", 422) from None
        if not isinstance(ln, LineString) or ln.is_empty or len(ln.coords) < 2:
            raise ApiError("INVALID_GEOMETRY", "Split needs a GeoJSON LineString with at least two points.", 422)
        with self._lock:
            ws = self._load()
            f = ws["features"].get(fid)
            if f is None:
                raise ApiError("FEATURE_NOT_FOUND", f"Workspace feature {fid!r} not found.", 404)
            poly = self._metric(shape(f["geometry"]))
            cut = self._metric(ln, src).buffer(gap_m / 2)
            diff = polygonal(poly.difference(cut))
            pieces = [g for g in getattr(diff, "geoms", [diff]) if isinstance(g, Polygon) and not g.is_empty and g.is_valid]
            if len(pieces) < 2:
                raise ApiError("SPLIT_INCOMPLETE", "The line does not cut the polygon completely into two or more parts.", 422)
            p0 = f["properties"]
            out = []
            for g in pieces:
                nf = self._new(self._wgs(g), {"origin": "human_split", "review_status": "suggested", "parents": [fid],
                                           "source_layer": p0.get("source_layer"), "source_feature_id": p0.get("source_feature_id"),
                                           "source_id": p0.get("source_id")})
                nf["properties"].update({k: p0[k] for k in ("feature_type", "class_name") if k in p0})
                nf["properties"]["functional_use"] = "unknown"
                ws["features"][nf["id"]] = nf
                out.append(nf)
            del ws["features"][fid]
            self._save(ws)
        lost = poly.area - sum(g.area for g in pieces)
        return {"features": out, "removed_ids": [fid], "info": {"area_lost_m2": _round(lost), "area_lost_fraction": round(lost / poly.area, 5),
                                                               "gap_m": gap_m}}

    def assign_use(self, fid, label, evidence_refs):
        with self._lock:
            ws = self._load()
            f = ws["features"].get(fid)
            if f is None:
                raise ApiError("FEATURE_NOT_FOUND", "Workspace feature not found.", 404)
            p = f["properties"]
            if p.get("feature_type", "building_footprint") != "building_footprint":
                raise ApiError("USE_FEATURE_TYPE", "Record building use on a building footprint; cover classes describe surfaces.", 422)
            if label != "unknown" and not evidence_refs:
                raise ApiError("USE_EVIDENCE_REQUIRED", "A known functional-use label requires source references.", 422)
            p["functional_use"] = label
            p["functional_use_review"] = {
                "label": label, "status": "reviewed" if label != "unknown" else "unknown",
                "evidence_refs": list(evidence_refs), "actor": self._audit_context.get("actor", "local-reviewer"),
                "actor_kind": "local_session_label", "recorded_utc": _now(),
                "workspace_revision": ws["revision"] + 1,
                "geometry_sha256": hashlib.sha256(json.dumps(f["geometry"], sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                "verification": "reviewer assertion; source references are not independently validated",
            }
            p["review_status"] = "suggested"
            p["updated_utc"] = _now()
            self._save(ws)
        return {"features": [f], "removed_ids": [], "info": {"functional_use": label}}

    def reset(self, fid):
        return self.set_status(fid, "suggested")

    def remove(self, fid):
        with self._lock:
            ws = self._load()
            if fid not in ws["features"]:
                raise ApiError("FEATURE_NOT_FOUND", "Workspace feature not found.", 404)
            del ws["features"][fid]
            self._save(ws)
        return {"features": [], "removed_ids": [fid], "info": {}}

    def queue(self):
        feats = [f for f in self.all() if f["properties"]["review_status"] == "suggested"]
        feats.sort(key=lambda f: (-len(list(filter(None, (f["properties"].get("ai_flags") or "").split(",")))), f["id"]))
        return {"features": feats, "note": "Priority uses display cues; these are not evaluated error labels."}

