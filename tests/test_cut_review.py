"""Tests for the reviewed RGB-guided cut workflow.

Run: python tests/test_cut_review.py
Reads outputs/rgb_guided_reviewed (existing outputs are never modified; reruns go to temp dirs).
"""

import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from apply_cut_review import merge_unaccepted  # noqa: E402

RGB = ROOT / "outputs" / "rgb_guided_v1"
BASE = ROOT / "outputs" / "lapaz_predio_bisa_whu_0.3m"
REV = ROOT / "outputs" / "rgb_guided_reviewed"
PRED = REV / "predictions"
PY = sys.executable


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def three_parts():
    inst = np.zeros((10, 30), np.int32)
    inst[2:8, 2:10], inst[2:8, 10:18], inst[2:8, 18:26] = 1, 2, 3
    return inst


def review_df(statuses):
    return gpd.GeoDataFrame({"part_a": [1, 2], "part_b": [2, 3], "status": statuses}, geometry=[None, None])


def test_only_accepted_cuts_applied_synthetic():
    inst = three_parts()
    merged, _ = merge_unaccepted(inst, review_df(["accepted", "rejected"]))
    assert len(np.unique(merged[merged > 0])) == 2
    assert merged[4, 5] != merged[4, 12] and merged[4, 12] == merged[4, 20]
    for s in ("uncertain", "pending", "rejected"):
        merged, _ = merge_unaccepted(inst, review_df([s, s]))
        assert len(np.unique(merged[merged > 0])) == 1, s  # never applied automatically
    assert np.array_equal(merged > 0, inst > 0)


def test_missing_cut_record_refused():
    inst = three_parts()
    partial = gpd.GeoDataFrame({"part_a": [1], "part_b": [2], "status": ["accepted"]}, geometry=[None])
    try:
        merge_unaccepted(inst, partial)
    except SystemExit as e:
        assert "no cut record" in str(e)
    else:
        raise AssertionError("missing cut record was not refused")


def test_real_accepted_vs_unaccepted():
    review = gpd.read_file(REV / "cut_review_reviewed.geojson")
    with rasterio.open(RGB / "instances.tif") as s:
        inst = s.read(1)
    with rasterio.open(PRED / "instances.tif") as s:
        merged = s.read(1)
    for a, b, status in zip(review.part_a, review.part_b, review.status):
        ma, mb = merged[inst == a][0], merged[inst == b][0]
        if status == "accepted":
            assert ma != mb, ("accepted cut not applied", a, b)
        else:
            assert ma == mb, (f"{status} cut was applied", a, b)
    assert set(review.status) == {"accepted", "rejected", "uncertain"}
    assert (review.reviewer != "").all() and (review.review_date != "").all()


def test_rejected_areas_keep_baseline_geometry():
    review = gpd.read_file(REV / "cut_review_reviewed.geojson")
    acc = review[review.status == "accepted"]
    base = gpd.read_file(BASE / "buildings.geojson")
    pred = gpd.read_file(PRED / "buildings.geojson")
    untouched = base[~base.intersects(acc.union_all().buffer(0.5))]
    assert len(untouched) > 0
    for g in untouched.geometry:
        cand = pred[pred.intersects(g.representative_point())]
        assert len(cand) == 1 and cand.geometry.iloc[0].symmetric_difference(g).area < 1e-6


def test_crs_validity_mask_and_area():
    with rasterio.open(PRED / "instances.tif") as s:
        merged, crs, tf = s.read(1), s.crs, s.transform
    with rasterio.open(BASE / "mask.tif") as s:
        mask, bcrs, btf = s.read(1).astype(bool), s.crs, s.transform
    assert crs == bcrs and tf == btf and np.array_equal(merged > 0, mask)
    pred = gpd.read_file(PRED / "buildings.geojson")
    base = gpd.read_file(BASE / "buildings.geojson")
    assert pred.crs == bcrs and pred.is_valid.all() and not pred.is_empty.any()
    assert abs(pred.area.sum() - base.area.sum()) / base.area.sum() < 0.01


def test_record_requires_confirm():
    with tempfile.TemporaryDirectory() as td:
        r = subprocess.run([PY, str(ROOT / "scripts" / "record_cut_review.py"), str(REV), "--decisions",
                            str(REV / "cut_review_proposals.csv"), "--reviewer", "x", "--source", "x", "--date", "x"],
                           capture_output=True, text=True, cwd=td)
        assert r.returncode != 0 and "--confirm is required" in (r.stdout + r.stderr)


def test_apply_deterministic():
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "pred"
        r = subprocess.run([PY, str(ROOT / "scripts" / "apply_cut_review.py"), str(RGB), str(BASE),
                            str(REV / "cut_review_reviewed.geojson"), str(out)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        for f in ("instances.tif", "buildings.geojson", "accepted_cut_lines.geojson"):
            assert sha(out / f) == sha(PRED / f), f


def test_preserved_hashes_unchanged():
    """Every preserved file matches its snapshot hash, except documented external changes
    (hash_exceptions.json), which must still match their recorded current hash."""
    import json
    exc = {}
    for f in (REV / "hash_exceptions.json", ROOT / "outputs" / "independent_validation_final" / "hash_exceptions.json"):
        if f.exists():
            exc.update({e["path"]: e["current_sha256"] for e in json.loads(f.read_text())["exceptions"]})
    changed = []
    for line in (REV / "preserved_hashes.sha256").read_text().splitlines():
        digest, path = line.split(maxsplit=1)
        path = path.lstrip("*")
        now = sha(ROOT / path)
        if now != digest and exc.get(path) != now:
            changed.append(path)
    assert not changed, changed


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"ALL {len(tests)} CUT-REVIEW TESTS PASSED")
