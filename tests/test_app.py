"""Tests for the review app: data build, server API/export, JS edit operations, full browser workflow.

Run: python tests/test_app.py            (browser E2E is skipped if no Edge/Chrome or Node is found)
Everything runs against temporary directories; frozen artifacts are only read.
"""

import hashlib
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import geopandas as gpd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "app"))
import server as app_server  # noqa: E402

PY = sys.executable
BROWSERS = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe"]
TMP = Path(tempfile.mkdtemp(prefix="sih_app_test_"))
DATA = TMP / "data"
EXPORTS = TMP / "exports"


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def start_server():
    port = socket.socket(); port.bind(("127.0.0.1", 0)); p = port.getsockname()[1]; port.close()
    srv = ThreadingHTTPServer(("127.0.0.1", p), app_server.make_handler(DATA, EXPORTS))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{p}"


def post(url, obj):
    req = urllib.request.Request(url, data=json.dumps(obj).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_build_data_bundle():
    r = subprocess.run([PY, str(ROOT / "scripts" / "build_app_data.py"), "--out-dir", str(DATA)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    m = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    test = json.loads((ROOT / "outputs/phase3/test_scores_T1-T4/scores.json").read_text())
    for key, run in (("A", "A_whu_baseline"), ("B", "B_approved_postproc"), ("C", "C_maskrcnn_0.3m")):
        lay = m["layers"][key]
        src = gpd.read_file(ROOT / lay["source"])
        out = gpd.read_file(DATA / lay["file"])
        assert len(src) == len(out) == lay["count"] and out.crs.to_epsg() == 4326
        assert lay["source_sha256"] == sha(ROOT / lay["source"])
        # metrics copied verbatim from the frozen test scores, labelled as test
        assert lay["metrics"]["test_T1-T4"]["overall"]["pixel_iou"] == test["runs"][run]["overall"]["pixel_iou"]
        # geometry round trip: back-projected areas equal source areas
        assert abs(out.to_crs(32719).area.sum() - src.area.sum()) / src.area.sum() < 1e-6
    assert "NOT legal cadastral" in m["disclaimer"]
    # hybrid D: metrics only, copied verbatim from the frozen held-out scores
    d = m["metrics_only"]["D"]["metrics"]["test_T1-T4"]["overall"]
    assert d == {k: test["runs"]["D_hybrid"]["overall"][k] for k in d}
    assert m["imagery"]["crs"] == "EPSG:32719" and m["imagery"]["native_res_m"] == 0.05
    r2 = subprocess.run([PY, str(ROOT / "scripts" / "build_app_data.py"), "--out-dir", str(DATA)], capture_output=True, text=True)
    assert r2.returncode != 0 and "refusing to overwrite" in (r2.stdout + r2.stderr)


def _ring(x0, y0, d):
    return [[[x0, y0], [x0 + d, y0], [x0 + d, y0 + d], [x0, y0 + d], [x0, y0]]]


def test_export_api_crs_and_validation():
    srv, base = start_server()
    try:
        with urllib.request.urlopen(base + "/api/health") as r:
            assert json.loads(r.read())["data_ready"] is True
        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": _ring(-68.0550, -16.5390, 0.0001)},
             "properties": {"origin": "human_drawn", "review_status": "accepted", "parents": []}},
            {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": _ring(-68.0545, -16.5390, 0.0001)},
             "properties": {"origin": "ai_copy", "review_status": "accepted", "source_layer": "C", "source_id": 3}}]}
        code, j = post(base + "/api/export", {"features": fc, "note": "test"})
        assert code == 200, j
        out = Path(j["dir"])
        assert out.parent == EXPORTS
        utm = gpd.read_file(out / "buildings_reviewed_EPSG32719.geojson")
        wgs = gpd.read_file(out / "buildings_reviewed_WGS84.geojson")
        assert utm.crs.to_epsg() == 32719 and wgs.crs.to_epsg() == 4326 and len(utm) == len(wgs) == 2
        assert utm.is_valid.all() and (utm.disclaimer.str.contains("NOT legal cadastral")).all()
        assert (wgs.to_crs(32719).centroid.distance(utm.centroid) < 1e-6).all()
        assert 100 < utm.area.iloc[0] < 150  # ~0.0001 deg square at -16.5 deg ≈ 11 x 10.7 m
        meta = json.loads((out / "export_metadata.json").read_text())
        assert meta["by_origin"] == {"human_drawn": 1, "ai_copy": 1}
        # second export goes to a NEW directory
        code2, j2 = post(base + "/api/export", {"features": fc})
        assert code2 == 200 and j2["dir"] != j["dir"]
        # invalid inputs are rejected
        bow = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"origin": "human_drawn", "review_status": "accepted"},
               "geometry": {"type": "Polygon", "coordinates": [[[-68.055, -16.539], [-68.054, -16.538], [-68.054, -16.539], [-68.055, -16.538], [-68.055, -16.539]]]}}]}
        assert post(base + "/api/export", {"features": bow})[0] == 422
        utm_coords = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"origin": "human_drawn", "review_status": "accepted"},
                      "geometry": {"type": "Polygon", "coordinates": _ring(600800, 8171200, 10)}}]}
        assert post(base + "/api/export", {"features": utm_coords})[0] == 422
        bad_origin = json.loads(json.dumps(fc)); bad_origin["features"][0]["properties"]["origin"] = "model_truth"
        assert post(base + "/api/export", {"features": bad_origin})[0] == 422
        # path traversal blocked
        for u in ("/data/../../data/lapaz_predio_bisa.tif", "/static/../server.py"):
            req = urllib.request.Request(base + u)
            try:
                urllib.request.urlopen(req)
                raise AssertionError("traversal served " + u)
            except urllib.error.HTTPError as e:
                assert e.code == 404
    finally:
        srv.shutdown()


def test_edit_ops_in_node():
    node = shutil.which("node")
    if not node:
        print("  SKIP node not found"); return
    js = r"""
const turf=require('./vendor/turf.min.js'), E=require('./edit_ops.js'), assert=require('assert');
const sq=(x,y,d,id)=>({type:'Feature',properties:{layer:'A',source_id:id,score:0.9,flags:''},geometry:{type:'Polygon',coordinates:[[[x,y],[x+d,y],[x+d,y+d],[x,y+d],[x,y]]]}});
const src=sq(-68.055,-16.539,0.0001,1); const srcCopy=JSON.stringify(src);
const a=E.fromCandidate(src), b=E.fromCandidate(sq(-68.0549,-16.539,0.0001,2));
assert.strictEqual(a.properties.origin,'ai_copy'); assert.strictEqual(a.properties.review_status,'suggested');
const m=E.merge([a,b],turf); assert.strictEqual(m.properties.origin,'human_merged'); assert.deepStrictEqual(m.properties.parents,[a.id,b.id]);
assert(Math.abs(turf.area(m)-turf.area(a)-turf.area(b))<0.5);
const line={type:'Feature',properties:{},geometry:{type:'LineString',coordinates:[[-68.05495,-16.5391],[-68.05495,-16.5388]]}};
const s=E.split(a,line,turf); assert.strictEqual(s.parts.length,2); assert(s.area_lost_m2>0 && s.area_lost_m2<1);
assert.throws(()=>E.split(a,{type:'Feature',geometry:{type:'LineString',coordinates:[[-68.06,-16.5],[-68.061,-16.5]]}},turf));
assert.throws(()=>E.merge([a],turf));
const e=E.withEditedGeometry(a,b.geometry); assert.strictEqual(e.properties.origin,'human_edited');
assert.throws(()=>E.setStatus(a,'approved_truth'));
const fc=E.exportCollection([E.setStatus(a,'accepted'),E.setStatus(b,'rejected'),m]);
assert.strictEqual(fc.features.length,2); assert(fc.features.every(f=>/NOT legal cadastral/.test(f.properties.disclaimer)));
assert.strictEqual(JSON.stringify(src),srcCopy);  // AI feature never mutated
console.log('OK');
"""
    r = subprocess.run([node, "-e", js], cwd=ROOT / "app" / "static", capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip() == "OK", r.stderr


def test_browser_end_to_end():
    node = shutil.which("node")
    exe = next((b for b in BROWSERS if Path(b).exists()), None)
    if not node or not exe:
        print("  SKIP browser or node not found"); return
    srv, base = start_server()
    try:
        prof = TMP / "browser_profile"
        r = subprocess.run([node, str(ROOT / "tests" / "e2e_app.js"), base + "/", exe, str(prof)],
                           capture_output=True, text=True, timeout=240)
        assert r.returncode == 0, r.stderr
        rep = json.loads(r.stdout)
        assert rep["js_errors"] == [], rep["js_errors"]
        assert rep["added"] == 125 and rep["accept"] == "accepted" and rep["reject"] == "rejected"
        assert isinstance(rep["merge"], dict) and rep["merge"]["parents"] == 2 and rep["merge"]["gone"], rep["merge"]
        assert rep["split"]["parts"] >= 2 and rep["split"]["parent_removed"], rep["split"]
        assert rep["edit"]["origin"] == "human_edited" and rep["drawn"] == 1
        assert rep["export"].startswith("Exported"), rep["export"]
        assert rep["ai_layer_C_count"] == 125
        assert rep["metric_banner"].startswith("Held-out test") and rep["metric_banner_dev"].startswith("DEVELOPMENT")
        assert "do not establish city-wide or cadastral accuracy" in rep["metric_banner"]
        # UI state: actions disabled until a valid selection exists; tool modes are explicit and exclusive
        assert all(rep["disabled_initial"][k] for k in ("accept", "merge", "split", "export")) and not rep["disabled_initial"]["add_view"]
        assert rep["disabled_one_selected"] == {"accept": False, "merge": True, "split": False, "edit": False}
        assert rep["inspector_one"] is True
        assert rep["split_banner"] and "Split" in rep["split_banner"] and rep["split_blocks_accept"] is True
        assert rep["banner_after_split_hidden"] is True
        # evaluation drawer: four model cards with the frozen held-out values, candidate status explicit
        assert rep["eval_drawer_visible"] and len(rep["metric_cards"]) == 4
        cards = " | ".join(rep["metric_cards"])
        for v in ("0.572", "0.551", "0.555", "0.588", "Matched 6 of 30", "Matched 12 of 30", "Matched 7 of 30", "not promoted"):
            assert v in cards, (v, cards)
        out = sorted(EXPORTS.iterdir())[-1]
        utm = gpd.read_file(out / "buildings_reviewed_EPSG32719.geojson")
        assert utm.crs.to_epsg() == 32719 and utm.is_valid.all()
        assert set(utm.review_status) == {"accepted"}
        assert {"human_merged", "human_split", "human_drawn"} <= set(utm.origin)
        meta = json.loads((out / "export_metadata.json").read_text())
        assert all(r["area_change_fraction"] <= 0.01 for r in meta["repaired_after_projection"])
        wgs = gpd.read_file(out / "buildings_reviewed_WGS84.geojson")
        assert wgs.is_valid.all() and len(wgs) == len(utm)
        print(f"  browser E2E: {json.dumps(rep['counts_by_origin'])}; export {len(utm)} footprints; screenshot {rep['screenshot']}")
    finally:
        srv.shutdown()


def test_frozen_artifacts_unchanged():
    frozen = ("models/", "data/lapaz_predio_bisa.tif", "data/validation/", "data/validation_independent/", "data/validation_test/",
              "outputs/lapaz_predio_bisa_whu_0.3m/", "outputs/rgb_guided_reviewed/final/", "outputs/phase3/")
    changed = []
    for line in (ROOT / "outputs" / "phase3" / "frozen_hashes.sha256").read_text().splitlines():
        d, p = line.split(maxsplit=1)
        p = p.lstrip("*")
        if p.startswith(frozen) and sha(ROOT / p) != d:
            changed.append(p)
    assert not changed, changed
    for line in (ROOT / "outputs" / "phase3" / "protocol_lock_amend1.sha256").read_text().splitlines():
        d, p = line.split(maxsplit=1)
        assert sha(ROOT / p.lstrip("*")) == d, p


if __name__ == "__main__":
    try:
        for t in (test_build_data_bundle, test_export_api_crs_and_validation, test_edit_ops_in_node,
                  test_browser_end_to_end, test_frozen_artifacts_unchanged):
            t()
            print(f"PASS {t.__name__}")
        print("ALL APP TESTS PASSED")
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
