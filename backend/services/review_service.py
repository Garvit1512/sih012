"""Review workspace: editable copies of AI predictions plus human-created features.

AI predictions are immutable (layer_service). Every workspace feature carries provenance
(origin, source_layer, source_feature_id, parents) and a review status. Persistence is one JSON file per site,
written atomically; geometry is stored in WGS84 and every operation re-validates client geometry server-side.
"""

import json
import os
import secrets
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
    def __init__(self, directory: Path, layers: LayerStore | None, site_bounds: list[float] | None, site_id: str | None):
        self.dir, self.layers, self.bounds, self.site_id = directory, layers, site_bounds, site_id
        self._lock = threading.RLock()

    # ---- persistence ----
    def _path(self) -> Path:
        return self.dir / f"{self.site_id}.json"          # site_id is the catalog's own id, validated upstream

    def _load(self) -> dict:
        p = self._path()
        if not p.is_file():
            return {"site_id": self.site_id, "features": {}}
        return json.loads(p.read_text(encoding="utf-8"))

    def _save(self, ws: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path().with_suffix(".tmp")
        tmp.write_text(json.dumps(ws), encoding="utf-8")
        os.replace(tmp, self._path())

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
        return {"count": len(feats), "by_review_status": by}

    # ---- writes ----
    def _new(self, geom_wgs, props: dict) -> dict:
        fid = "ws-" + secrets.token_hex(5)
        now = _now()
        props = {**props, "area_m2": _round(to_utm(geom_wgs).area), "created_utc": now, "updated_utc": now}
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
        chk = check_polygon(geometry, crs, self.bounds, "edited geometry")
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
            p["area_m2"] = _round(to_utm(chk.geometry).area)
            p["updated_utc"] = _now()
            if chk.repairs:
                p["repairs"] = (p.get("repairs") or []) + chk.repairs
            self._save(ws)
        change = (p["area_m2"] - before) / before if before else None
        return {"features": [f], "removed_ids": [], "info": {"area_before_m2": before, "area_after_m2": p["area_m2"],
                                                           "area_change_fraction": None if change is None else round(change, 4),
                                                           "repairs": chk.repairs}}

    def draw(self, geometry: dict, crs: str, status: str) -> ReviewOut:
        chk = check_polygon(geometry, crs, self.bounds, "drawn geometry")
        with self._lock:
            ws = self._load()
            f = self._new(chk.geometry, {"origin": "human_drawn", "review_status": status, "parents": [],
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
            u = polygonal(unary_union([to_utm(shape(p["geometry"])) for p in parts]))
            if not isinstance(u, Polygon) or u.is_empty or not u.is_valid:
                raise ApiError("MERGE_NOT_CONTIGUOUS", "The selected footprints do not form a single connected polygon.", 422)
            u_wgs = to_wgs(u)
            layers = sorted({p["properties"].get("source_layer") for p in parts if p["properties"].get("source_layer")})
            f = self._new(u_wgs, {"origin": "human_merged", "review_status": "accepted", "parents": list(ids),
                                  "source_layer": ",".join(layers) or None})
            for i in ids:
                del ws["features"][i]
            ws["features"][f["id"]] = f
            self._save(ws)
        before = sum(p["properties"]["area_m2"] for p in parts)
        return {"features": [f], "removed_ids": list(ids), "info": {"area_before_m2": _round(before), "area_after_m2": f["properties"]["area_m2"]}}

    def split(self, fid: str, line: dict, crs: str, gap_m: float) -> ReviewOut:
        src = normalise_crs(crs)
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
            poly = to_utm(shape(f["geometry"]))
            cut = to_utm(ln, src).buffer(gap_m / 2)
            diff = polygonal(poly.difference(cut))
            pieces = [g for g in getattr(diff, "geoms", [diff]) if isinstance(g, Polygon) and not g.is_empty and g.is_valid]
            if len(pieces) < 2:
                raise ApiError("SPLIT_INCOMPLETE", "The line does not cut the polygon completely into two or more parts.", 422)
            p0 = f["properties"]
            out = []
            for g in pieces:
                nf = self._new(to_wgs(g), {"origin": "human_split", "review_status": "accepted", "parents": [fid],
                                           "source_layer": p0.get("source_layer"), "source_feature_id": p0.get("source_feature_id"),
                                           "source_id": p0.get("source_id")})
                ws["features"][nf["id"]] = nf
                out.append(nf)
            del ws["features"][fid]
            self._save(ws)
        lost = poly.area - sum(g.area for g in pieces)
        return {"features": out, "removed_ids": [fid], "info": {"area_lost_m2": _round(lost), "area_lost_fraction": round(lost / poly.area, 5),
                                                               "gap_m": gap_m}}

