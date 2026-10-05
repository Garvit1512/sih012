"""AI layers: immutable prediction sets read from the frozen display bundle (layer_*.geojson)."""

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path

from shapely.geometry import box, shape
from shapely.strtree import STRtree

from ..errors import ApiError
from ..utils.crs import to_utm

# Presentation metadata the manifest does not carry as fields (the manifest only has free-text names).
LAYER_META = {
    "A": {"id": "whu", "type": "semantic_segmentation", "status": "baseline", "model": "WHU UNet++ / EfficientNet-B4"},
    "B": {"id": "rgb", "type": "postprocessing", "status": "approved", "model": "RGB-guided post-processing of WHU"},
    "C": {"id": "maskrcnn", "type": "instance_segmentation", "status": "candidate", "model": "Mask R-CNN (giswqs/geoai, NAIP-trained)"},
}


@dataclass
class AiFeature:
    id: str
    source_id: int
    props: dict
    wgs: object
    utm: object
    area_m2: float


@dataclass
class LayerData:
    key: str
    id: str
    meta: dict
    features: list[AiFeature]
    by_id: dict = field(default_factory=dict)
    utm_tree: STRtree | None = None
    wgs_tree: STRtree | None = None

    def query_bbox(self, bbox) -> list[AiFeature]:
        """Features intersecting a WGS84 bbox, ordered by source id (stable pagination)."""
        if bbox is None:
            return self.features
        idx = self.wgs_tree.query(box(*bbox), predicate="intersects")
        return sorted((self.features[i] for i in idx), key=lambda f: f.source_id)


class LayerStore:
    def __init__(self, data_dir: Path, manifest: dict):
        self._dir, self._manifest = data_dir, manifest
        self._cache: dict[str, LayerData] = {}
        self._lock = threading.Lock()

    def keys(self) -> list[str]:
        return list(self._manifest["layers"].keys())

    def resolve(self, layer_id: str) -> str:
        for k in self.keys():
            if layer_id in (k, self._manifest["layers"][k].get("id", LAYER_META.get(k, {}).get("id", k.lower()))):
                return k
        raise ApiError("LAYER_NOT_FOUND", f"Unknown layer {layer_id!r}.", 404)

    def get(self, layer_id: str) -> LayerData:
        key = self.resolve(layer_id)
        with self._lock:
            if key not in self._cache:
                self._cache[key] = self._load(key)
            return self._cache[key]

    def _load(self, key: str) -> LayerData:
        m = self._manifest["layers"][key]
        lid = m.get("id", LAYER_META.get(key, {}).get("id", key.lower()))
        path = (self._dir / m["file"]).resolve()          # file name comes from the manifest, never from a client
        if self._dir.resolve() not in path.parents or not path.is_file():
            raise ApiError("LAYER_DATA_MISSING", f"Layer data for {lid} is not available.", 503)
        fc = json.loads(path.read_text(encoding="utf-8"))
        feats = []
        for f in fc["features"]:
            p = dict(f["properties"])
            g = shape(f["geometry"])
            u = to_utm(g, analysis_crs=self._manifest["analysis_crs"])
            feats.append(AiFeature(f"{lid}:{p['source_id']}", int(p["source_id"]), p, g, u, float(u.area)))
        ld = LayerData(key, lid, m, feats)
        ld.by_id = {f.id: f for f in feats}
        ld.utm_tree = STRtree([f.utm for f in feats])
        ld.wgs_tree = STRtree([f.wgs for f in feats])
        return ld
