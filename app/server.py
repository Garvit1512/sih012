"""Local server for the building-footprint review app (Python standard library + project geo stack).

Serves app/static and a data bundle built by scripts/build_app_data.py, and exports reviewed footprints.

  GET  /                 -> app/static/index.html
  GET  /static/<file>    -> app/static/
  GET  /data/<file>      -> data bundle (default app/data)
  GET  /api/health       -> {"ok": true, ...}
  POST /api/export       -> body: {"features": GeoJSON FeatureCollection in EPSG:4326, "note": str}
                            writes a NEW directory outputs/app_exports/<UTC timestamp>_<rand>/ with
                            buildings_reviewed_EPSG32719.geojson, buildings_reviewed_WGS84.geojson,
                            export_metadata.json. Never overwrites.

Exports are building footprints for review, not legal cadastral parcel boundaries.

Run:
    python app/server.py            # then open http://127.0.0.1:8765
"""

import argparse
import hashlib
import json
import mimetypes
import secrets
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import geopandas as gpd
from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.ops import unary_union
from shapely.validation import explain_validity, make_valid

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"
ANALYSIS_CRS = "EPSG:32719"
MAX_BODY = 20 * 1024 * 1024
ALLOWED_ORIGINS = {"ai_copy", "human_edited", "human_merged", "human_split", "human_drawn"}
ALLOWED_STATUS = {"suggested", "accepted", "rejected"}
DISCLAIMER = ("AI-assisted building footprints. NOT legal cadastral parcel boundaries; "
              "not verified against authoritative parcel data.")
mimetypes.add_type("application/geo+json", ".geojson")
mimetypes.add_type("text/javascript", ".js")


def polygonal(g):
    """Keep only the polygonal parts of a geometry (make_valid may return collections)."""
    if isinstance(g, (Polygon, MultiPolygon)):
        return g
    parts = [x for x in getattr(g, "geoms", []) if isinstance(x, (Polygon, MultiPolygon))]
    return unary_union(parts) if parts else Polygon()


def validate_features(fc: dict):
    """Return (rows, errors). Each row: properties + shapely geometry (EPSG:4326)."""
    if not isinstance(fc, dict) or fc.get("type") != "FeatureCollection":
        return [], ["body.features must be a GeoJSON FeatureCollection"]
    rows, errors = [], []
    for i, f in enumerate(fc.get("features", [])):
        try:
            g = shape(f["geometry"])
        except Exception as e:  # noqa: BLE001
            errors.append(f"feature {i}: unreadable geometry ({e})")
            continue
        p = dict(f.get("properties") or {})
        if g.geom_type not in ("Polygon", "MultiPolygon"):
            errors.append(f"feature {i}: {g.geom_type} is not a polygon")
        elif g.is_empty or not g.is_valid:
            errors.append(f"feature {i}: invalid geometry ({explain_validity(g)})")
        elif not (-180 <= g.bounds[0] <= 180 and -90 <= g.bounds[1] <= 90):
            errors.append(f"feature {i}: coordinates are not longitude/latitude (EPSG:4326 expected)")
        elif p.get("origin") not in ALLOWED_ORIGINS:
            errors.append(f"feature {i}: origin must be one of {sorted(ALLOWED_ORIGINS)}")
        elif p.get("review_status") not in ALLOWED_STATUS:
            errors.append(f"feature {i}: review_status must be one of {sorted(ALLOWED_STATUS)}")
        else:
            p["disclaimer"] = DISCLAIMER
            p["feature_type"] = "building_footprint"
            rows.append({**{k: (json.dumps(v) if isinstance(v, (list, dict)) else v) for k, v in p.items()}, "geometry": g})
    if not rows and not errors:
        errors.append("no features to export")
    return rows, errors


def write_export(rows, note: str, export_root: Path, data_dir: Path) -> dict:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = export_root / f"{ts}_{secrets.token_hex(3)}"
    out.mkdir(parents=True, exist_ok=False)
    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    utm = gdf.to_crs(ANALYSIS_CRS)
    # Geometries valid in lon/lat can become invalid after projection (e.g. near-coincident vertices from a
    # union). Repair them in the analysis CRS, keep only polygonal parts, and refuse large changes.
    repaired = []
    for i, g in enumerate(utm.geometry):
        if g.is_valid:
            continue
        fixed = polygonal(make_valid(g))
        change = abs(fixed.area - g.buffer(0).area) / max(g.buffer(0).area, 1e-9)
        if fixed.is_empty or not fixed.is_valid or change > 0.01:
            out.rmdir()
            raise ValueError(f"feature {i}: geometry invalid in {ANALYSIS_CRS} and not safely repairable")
        utm.iloc[i, utm.columns.get_loc("geometry")] = fixed
        repaired.append({"feature": i, "area_change_fraction": round(change, 6)})
    gdf = utm.to_crs("EPSG:4326")
    utm["area_m2"] = utm.area.round(2)
    gdf["area_m2"] = utm["area_m2"].values
    assert utm.is_valid.all() and gdf.is_valid.all()
    utm.to_file(out / "buildings_reviewed_EPSG32719.geojson", driver="GeoJSON")
    gdf.to_file(out / "buildings_reviewed_WGS84.geojson", driver="GeoJSON")
    manifest = data_dir / "manifest.json"
    meta = {
        "exported_utc": ts, "note": note[:500], "disclaimer": DISCLAIMER, "feature_count": int(len(gdf)),
        "by_origin": gdf["origin"].value_counts().to_dict(), "by_review_status": gdf["review_status"].value_counts().to_dict(),
        "total_area_m2": round(float(utm.area.sum()), 2),
        "repaired_after_projection": repaired,
        "files": {"analysis_crs": {"file": "buildings_reviewed_EPSG32719.geojson", "crs": ANALYSIS_CRS},
                  "wgs84": {"file": "buildings_reviewed_WGS84.geojson", "crs": "EPSG:4326 (RFC 7946)"}},
        "data_bundle_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest() if manifest.exists() else None,
    }
    (out / "export_metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return {"dir": str(out), **meta}


def make_handler(data_dir: Path, export_root: Path):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quieter console
            sys.stderr.write("[app] " + fmt % args + "\n")

        def _send(self, code, body: bytes, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj, indent=2).encode("utf-8"))

        def _file(self, base: Path, rel: str):
            target = (base / rel).resolve()
            if base.resolve() not in target.parents or not target.is_file():
                return self._json(404, {"error": "not found"})
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self._send(200, target.read_bytes(), ctype)

        def do_GET(self):  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                return self._file(STATIC, "index.html")
            if path.startswith("/static/"):
                return self._file(STATIC, path[len("/static/"):])
            if path.startswith("/data/"):
                return self._file(data_dir, path[len("/data/"):])
            if path == "/api/health":
                return self._json(200, {"ok": True, "data_dir": str(data_dir), "export_root": str(export_root),
                                        "data_ready": (data_dir / "manifest.json").exists()})
            return self._json(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            if self.path != "/api/export":
                return self._json(404, {"error": "not found"})
            n = int(self.headers.get("Content-Length", 0))
            if n <= 0 or n > MAX_BODY:
                return self._json(400, {"error": "empty or too large body"})
            try:
                body = json.loads(self.rfile.read(n))
            except json.JSONDecodeError:
                return self._json(400, {"error": "body is not JSON"})
            rows, errors = validate_features(body.get("features"))
            if errors:
                return self._json(422, {"error": "validation failed", "details": errors[:50]})
            try:
                return self._json(200, write_export(rows, str(body.get("note", "")), export_root, data_dir))
            except Exception as e:  # noqa: BLE001
                return self._json(500, {"error": f"export failed: {e}"})

    return Handler


class AppServer(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second server bind an occupied port silently, so requests may
    # reach a stale server. Refuse to share the port there; keep the default elsewhere.
    allow_reuse_address = sys.platform != "win32"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--data-dir", type=Path, default=ROOT / "app" / "data")
    ap.add_argument("--export-root", type=Path, default=ROOT / "outputs" / "app_exports")
    args = ap.parse_args()
    if not (args.data_dir / "manifest.json").exists():
        raise SystemExit(f"{args.data_dir} has no manifest.json - run scripts/build_app_data.py first")
    try:
        srv = AppServer((args.host, args.port), make_handler(args.data_dir, args.export_root))
    except OSError as e:
        raise SystemExit(f"Port {args.port} is already in use ({e}). Stop the other server or use --port.")
    print(f"Building-footprint review app: http://{args.host}:{args.port}  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
