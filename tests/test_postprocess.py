"""Tests for scripts/postprocess_instances.py and scoring reproducibility.

Run: python tests/test_postprocess.py
Synthetic tests use 0.3 m pixels; the real-data tests read the existing baseline and
v1 outputs without modifying them.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from postprocess_instances import polygonize, separate_instances  # noqa: E402
from score_models import pixel_counts  # noqa: E402

PX = 0.3
CRS = rasterio.crs.CRS.from_epsg(32719)
TF = from_origin(600000.0, 8170000.0, PX, PX)
BASE = ROOT / "outputs" / "lapaz_predio_bisa_whu_0.3m"
V1 = ROOT / "outputs" / "whu_postprocessed_v1" / "distance_split"


def rect(m, r0, c0, h, w):
    m[r0:r0 + h, c0:c0 + w] = True


def test_empty_mask():
    inst = separate_instances(np.zeros((50, 50), bool), PX)
    assert inst.max() == 0
    gdf = polygonize(inst, np.zeros((50, 50), np.float32), TF, CRS, 10.0, 0.15)
    assert len(gdf) == 0 and gdf.crs == CRS


def test_touching_buildings_without_constriction_stay_merged():
    m = np.zeros((60, 90), bool)
    rect(m, 10, 10, 30, 30)
    rect(m, 10, 40, 30, 30)  # shares a full 9 m wall: no geometric evidence to split
    assert separate_instances(m, PX).max() == 1


def test_narrow_neck_is_split_and_mask_preserved():
    m = np.zeros((60, 90), bool)
    rect(m, 10, 5, 30, 30)
    rect(m, 10, 55, 30, 30)
    rect(m, 23, 35, 4, 20)  # 1.2 m wide connector
    inst = separate_instances(m, PX)
    sizes = np.bincount(inst.ravel())[1:]
    assert inst.max() == 2 and sizes[0] == sizes[1], sizes
    assert np.array_equal(inst > 0, m)


def test_l_shape_and_small_bump_not_split():
    m = np.zeros((60, 90), bool)
    rect(m, 10, 10, 40, 15)
    rect(m, 35, 10, 15, 45)
    assert separate_instances(m, PX).max() == 1
    m2 = np.zeros((60, 90), bool)
    rect(m2, 10, 10, 30, 30)
    rect(m2, 20, 40, 6, 6)  # 3.2 m^2 bump < min part
    assert separate_instances(m2, PX).max() == 1


def test_small_isolated_roofs():
    m = np.zeros((80, 80), bool)
    rect(m, 5, 5, 12, 12)    # 13 m^2 -> kept
    rect(m, 40, 40, 8, 8)    # 5.8 m^2 -> below 10 m^2, dropped at polygonization
    inst = separate_instances(m, PX)
    assert inst.max() == 2  # separate components are never merged
    gdf = polygonize(inst, m.astype(np.float32), TF, CRS, 10.0, 0.15)
    assert len(gdf) == 1 and abs(gdf.area.iloc[0] - 12.96) < 0.5, gdf.area.tolist()


def test_flooding_never_crosses_outside_pixels():
    # regression for the rejected watershed_ift setup: identical halves must get identical areas
    m = np.zeros((40, 120), bool)
    rect(m, 5, 5, 30, 30)
    rect(m, 5, 85, 30, 30)
    rect(m, 18, 35, 4, 50)  # long thin bridge, lots of outside pixels around it
    sizes = np.bincount(separate_instances(m, PX).ravel())[1:]
    assert len(sizes) == 2 and sizes[0] == sizes[1], sizes


def test_real_outputs_crs_validity_and_coverage():
    with rasterio.open(V1 / "instances.tif") as s:
        inst, crs, tf = s.read(1), s.crs, s.transform
    with rasterio.open(BASE / "mask.tif") as s:
        base_mask, base_crs, base_tf = s.read(1).astype(bool), s.crs, s.transform
    assert crs == base_crs and tf == base_tf
    assert np.array_equal(inst > 0, base_mask), "post-processing changed building pixels"
    gdf = gpd.read_file(V1 / "buildings.geojson")
    assert gdf.crs == base_crs
    assert gdf.is_valid.all() and not gdf.is_empty.any()
    assert set(gdf.geom_type) <= {"Polygon", "MultiPolygon"}
    summary = json.loads((V1 / "run_summary.json").read_text())
    assert summary["polygon_overlap_m2_total"] < 1.0, summary["polygon_overlap_m2_total"]


def test_ignored_areas_excluded():
    lab, pred = [box(0, 0, 10, 10)], [box(0, 0, 10, 10), box(12, 0, 18, 10)]
    always = lambda shape, tf: np.ones(shape, bool)  # noqa: E731
    with_ignore = pixel_counts(lab, pred, [box(11, 0, 19, 10)], (0, 0, 20, 20), 0.5, always)
    without = pixel_counts(lab, pred, [], (0, 0, 20, 20), 0.5, always)
    assert without["fp"] == 240 and with_ignore["fp"] == 0, (without, with_ignore)
    reviewed = gpd.read_file(ROOT / "data" / "validation" / "reviewed_labels.geojson")
    assert int(reviewed.ignore.sum()) == 6 and (reviewed.status == "approved").all()


def test_scoring_is_reproducible():
    with tempfile.TemporaryDirectory() as td:
        outs = []
        for k in range(2):
            out = Path(td) / f"e{k}"
            r = subprocess.run([sys.executable, str(ROOT / "scripts" / "score_models.py"),
                                "--labels", str(ROOT / "data" / "validation" / "reviewed_labels.geojson"),
                                "--run", f"base={BASE}", "--run", f"v1={V1}", "--out-dir", str(out)],
                               capture_output=True, text=True)
            assert r.returncode == 0, r.stderr
            outs.append(json.loads((out / "scores.json").read_text()))
        strip = lambda d: {n: {k: v for k, v in r.items() if k != "run_summary"} for n, r in d["runs"].items()}  # noqa: E731
        assert strip(outs[0]) == strip(outs[1])


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"ALL {len(tests)} POST-PROCESSING TESTS PASSED")
