"""Tests for the FastAPI backend (backend/). Run: .venv\\Scripts\\python -m pytest tests/test_backend.py

Uses the real data bundle (app/data_v2, read-only; built into a temp dir if absent). Workspace and exports go to
temp directories, so frozen artifacts and outputs/app_exports are never touched.
"""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import box, mapping, shape

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.config import EXPORT_DISCLAIMER, Settings  # noqa: E402
from backend.main import create_app  # noqa: E402

SITE = "lapaz_predio_bisa"
# metres -> degrees near La Paz (lat -16.54): good enough for building test shapes
DLAT, DLON = 1 / 110_600, 1 / 106_400
FROZEN = [ROOT / "app" / "data_v2" / "manifest.json"]


@pytest.fixture(scope="session")
def data_dir(tmp_path_factory):
    d = ROOT / "app" / "data_v2"
    if (d / "manifest.json").exists():
        return d
    out = tmp_path_factory.mktemp("bundle") / "data"
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_app_data.py"), "--out-dir", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return out


@pytest.fixture()
def client(data_dir, tmp_path):
    s = Settings(data_dir=data_dir, export_root=tmp_path / "exports", workspace_dir=tmp_path / "ws")
    return TestClient(create_app(s), raise_server_exceptions=False)


def sq(x_m, y_m, w=10.0, h=10.0, origin=(-68.0550, -16.5385)):
    """Axis-aligned rectangle in WGS84, offsets in metres from `origin` (inside the site)."""
    x0, y0 = origin[0] + x_m * DLON, origin[1] + y_m * DLAT
    return mapping(box(x0, y0, x0 + w * DLON, y0 + h * DLAT))


def fc(*geoms_ids, status="accepted", origin="human_drawn"):
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": i, "geometry": g, "properties": {"origin": origin, "review_status": status}} for i, g in geoms_ids]}


# ---------------- sites ----------------
def test_health_and_docs(client):
    assert client.get("/api/health").json()["ok"] is True
    assert client.get("/docs").status_code == 200 and client.get("/redoc").status_code == 200
    assert client.get("/openapi.json").json()["info"]["title"]


def test_site_listing_and_detail(client, data_dir):
    sites = client.get("/api/sites").json()["sites"]
    assert [s["id"] for s in sites] == [SITE]
    m = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    d = client.get(f"/api/sites/{SITE}").json()
    assert d["crs"] == "EPSG:32719" and d["resolution_m"] == m["imagery"]["native_res_m"]
    assert d["feature_counts"] == {"whu": m["layers"]["A"]["count"], "rgb": m["layers"]["B"]["count"], "maskrcnn": m["layers"]["C"]["count"]}
    assert len(d["bounds"]) == 4 and d["bounds"][0] < d["bounds"][2] and d["workflow"]["state"] == "LAYERS_AVAILABLE"
    img = client.get(f"/api/sites/{SITE}/imagery").json()
    assert img["source_crs"] == "EPSG:32719" and img["display_crs"] == "EPSG:3857" and img["source_sha256"]
    png = client.get(img["display_url"])
    assert png.status_code == 200 and png.content[:4] == b"\x89PNG"


def test_invalid_site_and_error_format(client):
    r = client.get("/api/sites/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "SITE_NOT_FOUND"
    assert set(r.json()["error"]) == {"code", "message", "details"}
    assert "Traceback" not in r.text


# ---------------- layers / features ----------------
def test_layer_listing_uses_manifest_counts(client, data_dir):
    m = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    layers = {l["id"]: l for l in client.get(f"/api/sites/{SITE}/layers?include_metrics=true").json()["layers"]}
    assert set(layers) == {"whu", "rgb", "maskrcnn"}
    for key, lid in (("A", "whu"), ("B", "rgb"), ("C", "maskrcnn")):
        assert layers[lid]["feature_count"] == m["layers"][key]["count"]
        assert layers[lid]["metrics"] == m["layers"][key]["metrics"]
    assert "no longer unseen" in layers["whu"]["metrics_note"]


def test_features_bbox_and_paging(client):
    url = f"/api/sites/{SITE}/layers/maskrcnn/features"
    full = client.get(url + "?limit=5000").json()
    assert full["numberMatched"] == full["numberReturned"] == 125
    f0 = full["features"][0]
    assert f0["properties"]["origin"] == "ai_prediction" and f0["properties"]["provenance"]["immutable"] is True
    assert f0["properties"]["area_m2"] > 0
    page = client.get(url + "?limit=10&offset=120").json()
    assert page["numberReturned"] == 5 and page["numberMatched"] == 125
    g = shape(f0["geometry"])
    small = client.get(url + "?bbox=" + ",".join(map(str, g.buffer(1e-6).bounds))).json()
    assert 0 < small["numberMatched"] < 125 and f0["id"] in {f["id"] for f in small["features"]}
    far = client.get(url + "?bbox=10,10,10.1,10.1").json()
    assert far["numberMatched"] == 0 and far["features"] == []
    assert client.get(url + "?bbox=1,2,3").status_code == 422
    assert client.get(url + "?bbox=5,5,1,1").json()["error"]["code"] == "INVALID_BBOX"


def test_no_fabricated_score_and_unknown_layer(client):
    assert client.get(f"/api/sites/{SITE}/layers/nope/features").json()["error"]["code"] == "LAYER_NOT_FOUND"
    for lid in ("whu", "rgb", "maskrcnn"):
        for f in client.get(f"/api/sites/{SITE}/layers/{lid}/features?limit=5000").json()["features"]:
            assert ("score" in f["properties"]) == (f["properties"].get("score_label") is not None)


def test_feature_detail(client):
    d = client.get("/api/features/rgb:14").json()
    assert d["kind"] == "ai_prediction" and d["source_layer"] == "rgb" and d["provenance"]["immutable"]
    assert any(r["type"] == "shared_boundary" and r["feature_id"] == "rgb:17" for r in d["topology"]["relationships"])
    assert client.get("/api/features/rgb:999999").status_code == 404
    assert client.get("/api/features/ws-deadbeef").status_code == 404


# ---------------- geometry validation ----------------
def edit(client, fid, geometry, **kw):
    return client.post(f"/api/review/features/{fid}/edit", json={"site_id": SITE, "geometry": geometry, **kw})


def make_ws(client, geom=None):
    r = client.post("/api/review/features/draw", json={"site_id": SITE, "geometry": geom or sq(0, 0)})
    assert r.status_code == 200, r.text
    return r.json()["features"][0]


def test_geometry_validation(client):
    f = make_ws(client)
    bow = {"type": "Polygon", "coordinates": [[[-68.055, -16.5385], [-68.0549, -16.5384], [-68.0549, -16.5385], [-68.055, -16.5384], [-68.055, -16.5385]]]}
    r = edit(client, f["id"], bow)
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_GEOMETRY"
    assert edit(client, f["id"], {"type": "Polygon", "coordinates": []}).json()["error"]["code"] in ("EMPTY_GEOMETRY", "INVALID_GEOMETRY")
    assert edit(client, f["id"], {"type": "Point", "coordinates": [-68.055, -16.5385]}).json()["error"]["code"] == "INVALID_GEOMETRY"
    assert edit(client, f["id"], sq(0, 0), crs="EPSG:3857").json()["error"]["code"] == "INVALID_CRS"
    far = {"type": "Polygon", "coordinates": [[[10, 10], [10.001, 10], [10.001, 10.001], [10, 10]]]}
    assert edit(client, f["id"], far).json()["error"]["code"] == "COORDINATES_OUT_OF_RANGE"
    lonlat_as_utm = edit(client, f["id"], sq(0, 0), crs="EPSG:32719")
    assert lonlat_as_utm.json()["error"]["code"] == "COORDINATES_OUT_OF_RANGE"
    # none of the rejected edits changed the stored geometry
    assert client.get(f"/api/features/{f['id']}").json()["geometry"] == f["geometry"]


def test_valid_polygon_accepted(client):
    f = make_ws(client)
    r = edit(client, f["id"], sq(0, 0, 12, 10))
    assert r.status_code == 200 and r.json()["info"]["area_change_fraction"] > 0.15


# ---------------- review ----------------
def test_workspace_copy_preserves_ai_and_provenance(client):
    before = client.get(f"/api/sites/{SITE}/layers/maskrcnn/features?limit=5000").json()
    r = client.post("/api/review/workspace", json={"site_id": SITE, "layer_id": "maskrcnn", "feature_ids": ["maskrcnn:1", "maskrcnn:2"]})
    assert r.status_code == 200, r.text
    feats = r.json()["features"]
    assert [f["properties"]["origin"] for f in feats] == ["ai_copy"] * 2
    assert all(f["properties"]["review_status"] == "suggested" and f["properties"]["source_layer"] == "maskrcnn" for f in feats)
    assert feats[0]["properties"]["source_feature_id"] == "maskrcnn:1" and "ai_score" in feats[0]["properties"]
    again = client.post("/api/review/workspace", json={"site_id": SITE, "layer_id": "maskrcnn", "feature_ids": ["maskrcnn:1"]}).json()
    assert again["features"] == [] and again["info"]["skipped_already_in_workspace"] == ["maskrcnn:1"]
    # accept / reject
    a = client.post(f"/api/review/features/{feats[0]['id']}/accept", json={"site_id": SITE}).json()["features"][0]
    assert a["properties"]["review_status"] == "accepted" and a["properties"]["origin"] == "ai_copy"
    rj = client.post(f"/api/review/features/{feats[1]['id']}/reject", json={"site_id": SITE}).json()["features"][0]
    assert rj["properties"]["review_status"] == "rejected"
    # edit -> human_edited, provenance kept; AI prediction untouched
    g = shape(feats[0]["geometry"])
    e = edit(client, feats[0]["id"], mapping(g.buffer(0.2 * DLAT))).json()["features"][0]
    assert e["properties"]["origin"] == "human_edited" and e["properties"]["source_feature_id"] == "maskrcnn:1"
    after = client.get(f"/api/sites/{SITE}/layers/maskrcnn/features?limit=5000").json()
    assert before == after
    assert client.get("/api/review/workspace", params={"site_id": SITE}).json()["summary"]["count"] == 2
    assert client.get(f"/api/sites/{SITE}").json()["workflow"]["state"] == "EXPORT_READY"


def test_workspace_requires_selection(client):
    r = client.post("/api/review/workspace", json={"site_id": SITE, "layer_id": "whu"})
    assert r.status_code == 422
    assert client.post("/api/review/workspace", json={"site_id": SITE, "layer_id": "whu", "feature_ids": ["whu:99999"]}).status_code == 404


def test_merge_split_draw(client):
    a, b = make_ws(client, sq(0, 0)), make_ws(client, sq(10, 0))     # share the x=10 edge exactly
    m = client.post("/api/review/features/merge", json={"site_id": SITE, "feature_ids": [a["id"], b["id"]]})
    assert m.status_code == 200, m.text
    mf = m.json()["features"][0]
    assert mf["properties"]["origin"] == "human_merged" and sorted(mf["properties"]["parents"]) == sorted([a["id"], b["id"]])
    assert sorted(m.json()["removed_ids"]) == sorted([a["id"], b["id"]]) and abs(mf["properties"]["area_m2"] - 200) < 3
    far = make_ws(client, sq(100, 100))
    bad = client.post("/api/review/features/merge", json={"site_id": SITE, "feature_ids": [mf["id"], far["id"]]})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "MERGE_NOT_CONTIGUOUS"
    # split the merged 20x10 box with a vertical line at x=10
    lx = -68.0550 + 10 * DLON
    line = {"type": "LineString", "coordinates": [[lx, -16.5385 - 5 * DLAT], [lx, -16.5385 + 15 * DLAT]]}
    s = client.post(f"/api/review/features/{mf['id']}/split", json={"site_id": SITE, "line": line})
    assert s.status_code == 200, s.text
    parts = s.json()["features"]
    assert len(parts) == 2 and all(p["properties"]["origin"] == "human_split" and p["properties"]["parents"] == [mf["id"]] for p in parts)
    assert 0 < s.json()["info"]["area_lost_m2"] < 1
    short = {"type": "LineString", "coordinates": [[lx, -16.5385 + 2 * DLAT], [lx, -16.5385 + 4 * DLAT]]}
    assert client.post(f"/api/review/features/{parts[0]['id']}/split", json={"site_id": SITE, "line": short}).json()["error"]["code"] == "SPLIT_INCOMPLETE"
    d = client.post("/api/review/features/draw", json={"site_id": SITE, "geometry": sq(50, 50)}).json()["features"][0]
    assert d["properties"]["origin"] == "human_drawn"


def test_review_unknown_feature_and_bad_ids(client):
    assert client.post("/api/review/features/ws-nope/accept", json={"site_id": SITE}).status_code == 404
    assert client.post("/api/review/features/ws-nope/accept", json={"site_id": "../x"}).status_code == 422


# ---------------- topology ----------------
def validate(client, *items):
    r = client.post("/api/topology/validate", json={"features": fc(*items)})
    assert r.status_code == 200, r.text
    return r.json()


def test_topology_overlap(client):
    j = validate(client, ("a", sq(0, 0)), ("b", sq(8, 0)))          # 2 m x 10 m = 20 m2 overlap
    assert j["valid"] is False
    c = [c for c in j["conflicts"] if c["type"] == "overlap"]
    assert len(c) == 1 and c[0]["features"] == ["a", "b"] and abs(c[0]["metrics"]["overlap_area_m2"] - 20) < 1
    assert c[0]["severity"] == "warning" and shape(c[0]["geometry"]).area > 0
    assert validate(client, ("a", sq(0, 0)), ("b", sq(3, 0)))["conflicts"][0]["severity"] == "error"


def test_topology_no_conflict(client):
    j = validate(client, ("a", sq(0, 0)), ("b", sq(40, 40)))
    assert j == {**j, "valid": True, "conflicts": []} and j["relationships"] == []
    assert validate(client, ("a", sq(0, 0)))["valid"] is True


def test_topology_shared_boundary_is_relationship_not_conflict(client):
    j = validate(client, ("a", sq(0, 0)), ("b", sq(10, 0)))
    assert j["valid"] is True and j["conflicts"] == []
    assert len(j["relationships"]) == 1 and 10 <= j["relationships"][0]["length_m"] < 11.5   # +/- tolerance hooks at the corners


def test_topology_duplicate_and_gap(client):
    assert validate(client, ("a", sq(0, 0)), ("b", sq(0.1, 0)))["conflicts"][0]["type"] == "duplicate"
    j = validate(client, ("a", sq(0, 0)), ("b", sq(10.28, 0)))        # 0.28 m wide, 10 m long gap
    assert [c["type"] for c in j["conflicts"]] == ["gap_sliver"] and j["conflicts"][0]["features"] == ["a", "b"]
    assert j["relationships"] and j["relationships"][0]["type"] == "shared_boundary"   # within 0.3 m tolerance


def test_topology_invalid_geometry(client):
    bow = {"type": "Polygon", "coordinates": [[[-68.055, -16.5385], [-68.0549, -16.5384], [-68.0549, -16.5385], [-68.055, -16.5384], [-68.055, -16.5385]]]}
    j = validate(client, ("bad", bow), ("ok", sq(40, 40)))
    c = j["conflicts"][0]
    assert c["type"] == "invalid_geometry" and c["severity"] == "error" and c["features"] == ["bad"] and "Self-intersection" in c["message"]


def test_topology_request_errors(client):
    assert client.post("/api/topology/validate", json={"features": {"type": "Feature"}}).status_code == 422
    assert client.post("/api/topology/validate", json={"features": fc(("a", sq(0, 0))), "crs": "EPSG:3857"}).json()["error"]["code"] == "INVALID_CRS"
    assert client.post("/api/topology/validate", content=b"{not json", headers={"Content-Type": "application/json"}).json()["error"]["code"] == "MALFORMED_JSON"
    assert client.post("/api/topology/validate", json={"features": {"type": "FeatureCollection", "features": [{"geometry": {"type": "Point", "coordinates": [0, 0]}}]}}).status_code == 422


def test_validate_layer_real_data(client):
    for lid, expect_shared in (("whu", 0), ("rgb", 29), ("maskrcnn", 20)):
        j = client.post("/api/topology/validate-layer", json={"site_id": SITE, "layer_id": lid}).json()
        assert j["summary"]["shared_boundary_count"] == expect_shared and j["summary"]["feature_count"] > 0
        assert j["valid"] == (not j["conflicts"])
        assert "cadastral" in j["scope_note"]
    assert client.post("/api/topology/validate-layer", json={"site_id": SITE, "layer_id": "x/y"}).status_code == 422


def test_shared_boundary_endpoint(client):
    feats = fc(("a", sq(0, 0)), ("b", sq(10, 0)), ("far", sq(40, 40)))
    x, y = -68.0550 + 10 * DLON, -16.5385 + 5 * DLAT           # on the shared edge
    r = client.post("/api/topology/shared-boundary", json={"feature_id": "a", "vertex": [x, y], "features": feats}).json()
    assert r["shared"] is True and r["neighbor_feature_id"] == "b" and r["edge"]["type"] == "LineString"
    e = shape(r["edge"])
    assert abs(e.bounds[0] - x) < 0.4 * DLON and 9 < e.length / DLAT < 11.5   # the neighbour's shared edge (~10 m + tolerance hooks)
    assert r["tolerance_m"] == 0.3
    # vertex on an outer, unshared edge
    n = client.post("/api/topology/shared-boundary", json={"feature_id": "a", "vertex": [-68.0550, y], "features": feats}).json()
    assert n["shared"] is False and n["neighbor_feature_id"] is None and n["edge"] is None
    # rejected neighbours are ignored
    rej = fc(("a", sq(0, 0)), ("b", sq(10, 0)), status="rejected")
    rej["features"][0]["properties"]["review_status"] = "accepted"
    assert client.post("/api/topology/shared-boundary", json={"feature_id": "a", "vertex": [x, y], "features": rej}).json()["shared"] is False
    assert client.post("/api/topology/shared-boundary", json={"feature_id": "zzz", "vertex": [x, y], "features": feats}).status_code == 404


def test_shared_boundary_on_ai_layer_and_workspace(client):
    j = client.post("/api/topology/validate-layer", json={"site_id": SITE, "layer_id": "rgb"}).json()
    rel = j["relationships"][0]
    edge = shape(rel["geometry"])
    first = (edge.geoms[0] if hasattr(edge, "geoms") else edge).coords[0]
    r = client.post("/api/topology/shared-boundary", json={"feature_id": rel["features"][0], "vertex": list(first)}).json()
    assert r["shared"] is True and r["neighbor_feature_id"] == rel["features"][1]
    # server workspace
    a, b = make_ws(client, sq(0, 0)), make_ws(client, sq(10, 0))
    w = client.post("/api/topology/shared-boundary", json={"feature_id": a["id"], "vertex": [-68.0550 + 10 * DLON, -16.5385 + 5 * DLAT]}).json()
    assert w["neighbor_feature_id"] == b["id"]
    assert client.post("/api/topology/shared-boundary", json={"feature_id": "a", "vertex": [0, 0], "tolerance_m": 50, "features": fc(("a", sq(0, 0)))}).status_code == 422


# ---------------- export ----------------
def test_export(client, tmp_path):
    body = {"features": fc(("a", sq(0, 0)), ("b", sq(10, 0))), "note": "test"}
    r = client.post("/api/export", json=body)
    assert r.status_code == 200, r.text
    j = r.json()
    out = tmp_path / "exports" / j["export_id"]
    assert (out / "buildings_reviewed_EPSG32719.geojson").is_file() and (out / "buildings_reviewed_WGS84.geojson").is_file()
    utm = json.loads((out / "buildings_reviewed_EPSG32719.geojson").read_text())
    wgs = json.loads((out / "buildings_reviewed_WGS84.geojson").read_text())
    assert "32719" in json.dumps(utm["crs"]) and utm["features"][0]["geometry"]["coordinates"][0][0][0] > 100000
    assert -180 <= wgs["features"][0]["geometry"]["coordinates"][0][0][0] <= 180
    assert len(utm["features"]) == len(wgs["features"]) == 2
    p = wgs["features"][0]["properties"]
    assert p["origin"] == "human_drawn" and p["review_status"] == "accepted" and "NOT legal cadastral" in p["disclaimer"]
    meta = json.loads((out / "export_metadata.json").read_text())
    assert meta["export_disclaimer"] == EXPORT_DISCLAIMER and meta["topology"]["valid"] is True and meta["feature_count"] == 2
    assert "dir" in j and ":" not in j["dir"] and j["files"]["analysis_crs"]["crs"] == "EPSG:32719"
    assert shape(utm["features"][0]["geometry"]).is_valid
    # second export never overwrites
    r2 = client.post("/api/export", json=body).json()
    assert r2["export_id"] != j["export_id"] and len(list((tmp_path / "exports").iterdir())) == 2
    assert (out / "export_metadata.json").is_file()


def test_export_rejections_write_nothing(client, tmp_path):
    bow = {"type": "Polygon", "coordinates": [[[-68.055, -16.5385], [-68.0549, -16.5384], [-68.0549, -16.5385], [-68.055, -16.5384], [-68.055, -16.5385]]]}
    r = client.post("/api/export", json={"features": fc(("a", sq(0, 0)), ("bad", bow))})
    assert r.status_code == 422 and r.json()["error"]["code"] == "EXPORT_VALIDATION_FAILED"
    assert r.json()["error"]["details"]["errors"][0]["feature_index"] == 1
    no_prov = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": sq(0, 0), "properties": {}}]}
    assert client.post("/api/export", json={"features": no_prov}).json()["error"]["details"]["errors"][0]["code"] == "INVALID_PROVENANCE"
    assert client.post("/api/export", json={"features": {"type": "FeatureCollection", "features": []}}).json()["error"]["code"] == "NOTHING_TO_EXPORT"
    blocked = client.post("/api/export", json={"features": fc(("a", sq(0, 0)), ("b", sq(3, 0))), "require_topology_clean": True})
    assert blocked.json()["error"]["code"] == "TOPOLOGY_CONFLICTS"
    assert not (tmp_path / "exports").exists() or not list((tmp_path / "exports").iterdir())
    # overlap conflicts alone do not block (reported in the manifest)
    ok = client.post("/api/export", json={"features": fc(("a", sq(0, 0)), ("b", sq(3, 0)))}).json()
    assert ok["topology"]["valid"] is False and ok["topology"]["conflicts"][0]["type"] == "overlap"


# ---------------- security / robustness ----------------
@pytest.mark.parametrize("path", ["/data/../manifest.json", "/data/%2e%2e/%2e%2e/data/lapaz_predio_bisa.tif", "/static/../server.py",
                                  "/static/%2e%2e/server.py", "/data/..%5c..%5cCLAUDE.md", "/api/sites/..%2f..%2fCLAUDE.md"])
def test_path_traversal_blocked(client, path):
    r = client.get(path)
    assert r.status_code in (400, 404, 422)
    assert b"CLAUDE" not in r.content[:200] and b"SIH Building" not in r.content


def test_id_sanitising_and_malformed_bodies(client):
    assert client.get("/api/features/..%2f..%2fetc").status_code in (404, 422)
    assert client.get("/api/features/a b").status_code in (404, 422)
    r = client.post("/api/review/features/draw", content=b"{broken", headers={"Content-Type": "application/json"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "MALFORMED_JSON"
    assert client.post("/api/review/features/draw", json={"site_id": SITE}).json()["error"]["code"] == "INVALID_REQUEST"
    assert client.post("/api/review/features/draw", json={"site_id": SITE, "geometry": "x"}).status_code == 422
    assert client.post("/api/export", json={"features": 5}).status_code == 422
    assert client.get("/api/health").json()["ok"]


def test_cors_is_explicit(client):
    ok = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    bad = client.get("/api/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in bad.headers


def test_frozen_manifest_not_modified_by_backend(client, data_dir):
    h = hashlib.sha256((data_dir / "manifest.json").read_bytes()).hexdigest()
    client.get(f"/api/sites/{SITE}/layers/maskrcnn/features")
    client.post("/api/topology/validate-layer", json={"site_id": SITE, "layer_id": "rgb"})
    assert hashlib.sha256((data_dir / "manifest.json").read_bytes()).hexdigest() == h


# ---------------- browser E2E against the FastAPI-served single page ----------------
def test_browser_e2e_backend(data_dir, tmp_path):
    import os
    import shutil
    import socket
    import time
    import urllib.request

    browsers = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Google\Chrome\Application\chrome.exe"]
    exe, node = next((b for b in browsers if Path(b).exists()), None), shutil.which("node")
    if not exe or not node:
        pytest.skip("no Edge/Chrome or Node available")
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    env = {**os.environ, "SIH_DATA_DIR": str(data_dir), "SIH_EXPORT_ROOT": str(tmp_path / "exports"), "SIH_WORKSPACE_DIR": str(tmp_path / "ws")}
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.main:app", "--port", str(port)], cwd=ROOT, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1)
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        prof = tmp_path / "profile"
        prof.mkdir()
        r = subprocess.run([node, str(ROOT / "tests" / "e2e_backend.js"), f"http://127.0.0.1:{port}/", exe, str(prof)],
                           capture_output=True, text=True, timeout=240)
        assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
        assert "Exported 1 footprints" in r.stdout
    finally:
        srv.terminate()
