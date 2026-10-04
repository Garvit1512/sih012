"""Tests for scripts/finalize_independent_validation.py and the final independent scores.

Run: python tests/test_finalize_independent.py
"""

import json
import subprocess
import sys
from pathlib import Path

import geopandas as gpd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from finalize_independent_validation import verify  # noqa: E402

VAL = ROOT / "data" / "validation_independent"
OUT = ROOT / "outputs" / "independent_validation_final"


def test_verification_passes_and_recorded():
    v = json.loads((OUT / "verification.json").read_text())
    assert v["passed"] is True
    assert all(val is True for k, val in v["checks"].items() if k != "crs"), v["checks"]
    assert v["counts"]["building_labels"] == 49 and v["counts"]["ignore_features"] == 25


def test_verify_detects_missing_decision():
    prov = gpd.read_file(VAL / "provisional_labels.geojson")
    rev = gpd.read_file(VAL / "reviewed_labels.geojson")
    dec = json.loads((VAL / "review_decisions.json").read_text())
    dec["windows"]["V1_dense_informal"]["decisions"].pop("V1-01")
    checks, _ = verify(prov, rev, dec)
    assert checks["every_provisional_label_has_explicit_decision"] is False


def test_verify_detects_ignored_building():
    prov = gpd.read_file(VAL / "provisional_labels.geojson")
    rev = gpd.read_file(VAL / "reviewed_labels.geojson")
    dec = json.loads((VAL / "review_decisions.json").read_text())
    rev.loc[rev.review_decision == "confirm", "ignore"] = True
    checks, _ = verify(prov, rev, dec)
    assert checks["buildings_not_ignored"] is False


def test_split_files_partition_reviewed_labels():
    rev = gpd.read_file(VAL / "reviewed_labels.geojson")
    b = gpd.read_file(OUT / "reference" / "buildings_approved.geojson")
    i = gpd.read_file(OUT / "reference" / "ignore_regions.geojson")
    assert len(b) + len(i) == len(rev) and not b.ignore.any() and i.ignore.all()
    assert b.crs == rev.crs == i.crs
    assert abs(b.area.sum() - rev[~rev.ignore].area.sum()) < 1e-6
    assert abs(i.area.sum() - rev[rev.ignore].area.sum()) < 1e-6


def test_refuses_overwrite():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "finalize_independent_validation.py"),
                        str(ROOT / "outputs" / "lapaz_predio_bisa_whu_0.3m"),
                        str(ROOT / "outputs" / "rgb_guided_reviewed" / "final" / "predictions"), str(OUT)],
                       capture_output=True, text=True)
    assert r.returncode != 0 and "refusing to overwrite" in (r.stdout + r.stderr)


def test_scores_match_earlier_independent_run_and_settings():
    new = json.loads((OUT / "scores" / "scores.json").read_text())
    old = json.loads((ROOT / "outputs" / "rgb_guided_reviewed" / "final" / "evaluation_independent_V1-V4" / "scores.json").read_text())
    assert (new["eval_res_m"], new["match_iou_threshold"], new["min_piece_area_m2"]) == (0.1, 0.5, 2.0)
    for a, b in (("whu_baseline", "whu_baseline"), ("approved_postprocessing", "rgb_guided_reviewed_final")):
        assert new["runs"][a]["overall"] == old["runs"][b]["overall"]
        assert new["runs"][a]["per_window"] == old["runs"][b]["per_window"]


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"ALL {len(tests)} FINALIZE-INDEPENDENT TESTS PASSED")
