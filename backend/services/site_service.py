"""Site catalog. Site metadata is read from the data-bundle manifest (built by scripts/build_app_data.py)."""

import json
import threading
from dataclasses import replace
from pathlib import Path

from shapely.geometry import box

from ..config import DISCLAIMER, Settings
from ..errors import ApiError
from ..utils.crs import metric_crs, to_utm
from ..utils.validation import safe_id
from .layer_service import LAYER_META, LayerStore

SERVER_STATES = ["IMAGERY_READY", "LAYERS_AVAILABLE", "REVIEWING", "EXPORT_READY"]
CLIENT_STATES = ["IDLE", "SITE_SELECTED", "FLY_TO_SITE", "ACTIVE_LAYER", "TOPOLOGY_CONFLICTS", "EDITING", "EXPORTED"]


class Catalog:
    """One site per data bundle. The site id is the imagery file stem (e.g. 'lapaz_predio_bisa')."""

    def __init__(self, settings: Settings):
        self.settings = settings
        mp = settings.data_dir / "manifest.json"
        self.manifest = json.loads(mp.read_text(encoding="utf-8")) if mp.is_file() else None
        self.manifest_mtime = mp.stat().st_mtime_ns if mp.is_file() else None
        self.ready = self.manifest is not None
        self.analysis_crs = metric_crs(self.manifest["analysis_crs"]) if self.ready else "EPSG:32719"
        self.layers: LayerStore | None = LayerStore(settings.data_dir, self.manifest) if self.ready else None
        self.site_id = (self.manifest.get("site_id") or Path(self.manifest["imagery"]["file"]).stem) if self.ready else None

    def _require(self):
        if not self.ready:
            raise ApiError("DATA_NOT_READY", "No data bundle found. Run scripts/build_app_data.py.", 503)

    def require_site(self, site_id: str) -> str:
        safe_id(site_id, "site id")
        self._require()
        if site_id != self.site_id:
            raise ApiError("SITE_NOT_FOUND", f"Unknown site {site_id!r}.", 404)
        return site_id

    def bounds(self):
        if self.manifest.get("raster", {}).get("bounds_wgs84"):
            w, s, e, n = self.manifest["raster"]["bounds_wgs84"]
            return [w, s, e, n], [[s, w], [n, e]]
        (s, w), (n, e) = self.manifest["ortho"]["bounds_latlon"]
        return [w, s, e, n], [[s, w], [n, e]]

    def summary(self) -> dict:
        self._require()
        m, img = self.manifest, self.manifest["imagery"]
        b, ll = self.bounds()
        return {
            "id": self.site_id,
            "name": m.get("name", self.site_id),
            "location": (img.get("location") or "").split(" (")[0] or None,
            "crs": m["analysis_crs"],
            "resolution_m": img.get("native_res_m"),
            "bounds": b, "bounds_latlon": ll, "bounds_utm": list(to_utm(box(*b), analysis_crs=self.analysis_crs).bounds),
            "imagery": f"/api/sites/{self.site_id}/imagery",
        }

    def imagery(self) -> dict:
        self._require()
        o, img = self.manifest["ortho"], self.manifest["imagery"]
        return {
            "display_url": f"/api/sites/{self.site_id}/imagery/display",
            "display_crs": "EPSG:3857",
            "display_bounds_latlon": o["bounds_latlon"],
            "display_size_px": o["size_px"],
            "display_resolution_m": o.get("resolution_m_3857"),
            "source_file": img.get("file"), "source_crs": o.get("source_crs"), "source_size_px": img.get("size_px"),
            "native_resolution_m": img.get("native_res_m"), "source_sha256": o.get("source_sha256"),
            "note": o.get("note"),
            "tile_url": f"/api/sites/{self.site_id}/imagery/tiles/{{z}}/{{x}}/{{y}}.png" if m_is_tiled(self.manifest) else None,
            "quality": self.manifest.get("quality", {}),
        }

    def display_image_path(self) -> Path:
        d = self.settings.data_dir.resolve()
        p = (d / "ortho_3857.png").resolve()
        if d not in p.parents or not p.is_file():
            raise ApiError("IMAGERY_MISSING", "Display imagery is not available.", 503)
        return p

    def layer_summaries(self, with_metrics: bool = False) -> list[dict]:
        self._require()
        out = []
        for k, m in self.manifest["layers"].items():
            meta = {**LAYER_META.get(k, {"id": k.lower(), "type": "unknown", "status": "unknown"}), **m}
            d = {
                "id": meta["id"], "legacy_key": k, "name": m["name"], "type": meta["type"], "status": meta["status"],
                "feature_count": m["count"], "color": m.get("color"), "score_field": m.get("score_field"),
                "score_label": m.get("score_label"), "source_sha256": m.get("source_sha256"),
                "source_crs": m.get("source_crs"), "flag_counts": m.get("flag_counts"),
            }
            if with_metrics:
                d["metrics"] = m.get("metrics")
                d["metrics_note"] = ("Read from the frozen evaluation summary in the manifest. test_T1-T4 windows were already "
                                     "viewed and are no longer unseen; development_* are not test metrics. Not city-wide or "
                                     "cadastral accuracy.")
            out.append(d)
        return out

    def metrics_only(self) -> list[dict]:
        self._require()
        return [{"legacy_key": k, "name": v["name"], "source": v.get("source"), "has_features": False}
                for k, v in (self.manifest.get("metrics_only") or {}).items()]

    def disclaimer(self) -> str:
        return (self.manifest or {}).get("disclaimer", DISCLAIMER)


def m_is_tiled(manifest):
    return bool(manifest.get("imagery", {}).get("cog_file"))


class SiteRegistry:
    """Discover new manifests while keeping the original single-site bundle read-only."""

    def __init__(self, settings):
        self.settings = settings
        self.legacy = Catalog(settings)
        self._sites = {}
        self._lock = threading.RLock()

    @property
    def ready(self):
        return bool(self.all())

    @property
    def site_id(self):
        return self.legacy.site_id or next(iter(self.all()), None)

    def all(self):
        with self._lock:
            return self._discover()

    def _discover(self):
        sites = {self.legacy.site_id: self.legacy} if self.legacy.ready else {}
        if self.settings.sites_dir.is_dir():
            for path in sorted(self.settings.sites_dir.glob("*/manifest.json")):
                if path.parent.name.startswith("."):
                    continue
                if path not in self._sites or self._sites[path].manifest_mtime != path.stat().st_mtime_ns:
                    self._sites[path] = Catalog(replace(self.settings, data_dir=path.parent))
                cat = self._sites[path]
                if cat.site_id in sites:
                    raise ApiError("DUPLICATE_SITE", f"Duplicate site id {cat.site_id!r}.", 409)
                sites[cat.site_id] = cat
        return sites

    def get(self, site_id):
        safe_id(site_id, "site id")
        cat = self.all().get(site_id)
        if cat is None:
            raise ApiError("SITE_NOT_FOUND", f"Unknown site {site_id!r}.", 404)
        return cat

    def refresh(self, site_id):
        with self._lock:
            for path, cat in list(self._sites.items()):
                if cat.site_id == site_id:
                    del self._sites[path]

    def require_site(self, site_id):
        return self.get(site_id)
