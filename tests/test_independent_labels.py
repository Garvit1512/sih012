"""Tests for the approved independent (V1-V4) reference labels.

Run: python tests/test_independent_labels.py
"""

import json
import subprocess
import sys
from pathlib import Path

import geopandas as gpd

ROOT = Path(__file__).resolve().parents[1]
VAL = ROOT / "data" / "validation_independent"


def test_provisional_untouched_and_reviewed_separate():
    prov = gpd.read_file(VAL / "provisional_labels.geojson")
    rev = gpd.read_file(VAL / "reviewed_labels.geojson")
    assert (prov.status == "provisional_unreviewed").all() and len(prov) == 65
    assert (rev.status == "approved").all() and rev.crs == prov.crs
    assert rev.is_valid.all() and not rev.is_empty.any()


def test_every_provisional_label_has_a_decision():
    prov = gpd.read_file(VAL / "provisional_labels.geojson")
    dec = json.loads((VAL / "review_decisions.json").read_text())
    decided = {lid for w in dec["windows"].values() for lid in w["decisions"]}
    assert decided == set(prov.label_id)
    assert dec.get("all_windows_confirmed", {}).get("by") == "project user"


def test_decision_mapping():
    rev = gpd.read_file(VAL / "reviewed_labels.geojson")
    assert not rev[rev.review_decision.isin(["confirm", "edit"])].ignore.any()
    assert rev[rev.review_decision.isin(["uncertain", "keep_ignore", "added_ignore_region"])].ignore.all()
    edits = rev[rev.review_decision == "edit"]
    assert set(edits.label_id) == {"V3-05", "V4-07"} and edits.note.str.contains("EDIT").all()


def test_ignores_never_cover_confirmed_buildings():
    rev = gpd.read_file(VAL / "reviewed_labels.geojson")
    for wid, g in rev.groupby("window_id"):
        bld = g[~g.ignore].union_all()
        overlap = g[g.ignore].intersection(bld).area.sum()
        assert overlap < 1e-6, (wid, overlap)


def test_builder_refuses_overwrite():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_reviewed_independent_labels.py")],
                       capture_output=True, text=True)
    assert r.returncode != 0 and "refusing to overwrite" in (r.stdout + r.stderr)


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"ALL {len(tests)} INDEPENDENT-LABEL TESTS PASSED")
