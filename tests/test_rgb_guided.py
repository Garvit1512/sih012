"""Tests for scripts/rgb_guided_instances.py.

Run: python tests/test_rgb_guided.py
Synthetic tests use 0.3 m pixels; real-output tests read outputs/rgb_guided_v1 and the
WHU baseline without modifying them.
"""

import json
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from rgb_guided_instances import chroma_features, separate_component  # noqa: E402

PX = 0.3
BASE = ROOT / "outputs" / "lapaz_predio_bisa_whu_0.3m"
OUT = ROOT / "outputs" / "rgb_guided_v1"
H, W = 60, 100


def scene(left, right, noise=6.0, seed=0):
    rng = np.random.default_rng(seed)
    x = np.full((H, W, 3), 90, np.float32)
    x[:, :50], x[:, 50:] = left, right
    return (x + rng.normal(0, noise, x.shape)).clip(0, 255)


def roof_mask():
    m = np.zeros((H, W), bool)
    m[10:50, 10:90] = True
    return m


def run(rgb, comp, min_contrast=0.04):
    chroma, grad = chroma_features(rgb)
    lab, cand, cuts = separate_component(comp, chroma, grad, np.percentile(grad[comp], 30),
                                         np.percentile(grad[comp], 75), PX, 10.0, 0.25, min_contrast, 0.3)
    return lab, cand, cuts


def n_parts(lab):
    return len(np.unique(lab[lab > 0]))


def test_touching_buildings_with_colour_contrast_are_split():
    lab, _, cuts = run(scene((220, 120, 60), (150, 150, 150)), roof_mask())  # orange | grey
    assert n_parts(lab) == 2 and len(cuts) == 1 and cuts[0]["confidence"] == "high", cuts
    left, right = np.unique(lab[:, 15]), np.unique(lab[:, 85])
    assert set(left[left > 0]).isdisjoint(set(right[right > 0]))  # cut separates the two halves


def test_ambiguous_edges_not_split():
    comp = roof_mask()
    # ridge / shadow: same material, brightness only
    assert n_parts(run(scene((180, 180, 180), (110, 110, 110)), comp)[0]) == 1
    # enclosed skylight patch inside one roof
    sky = scene((150, 150, 150), (150, 150, 150))
    sky[25:32, 40:52] = (120, 80, 50)
    assert n_parts(run(sky, comp)[0]) == 1
    # weak colour difference below the contrast threshold (grey vs slightly bluish grey)
    lab, cand, _ = run(scene((150, 150, 150), (146, 150, 156)), comp)
    assert n_parts(lab) == 1


def test_no_split_outside_mask_and_area_preserved():
    comp = roof_mask()
    lab, cand, _ = run(scene((220, 120, 60), (150, 150, 150)), comp)
    assert np.array_equal(lab > 0, comp) and np.array_equal(cand > 0, comp)


def test_uniform_component_untouched():
    comp = roof_mask()
    lab, _, cuts = run(np.full((H, W, 3), 150, np.float32), comp)  # no seeds variation -> single part
    assert n_parts(lab) == 1 and cuts == []


def test_deterministic():
    comp, rgb = roof_mask(), scene((140, 85, 60), (160, 160, 165), seed=3)
    a, b = run(rgb, comp), run(rgb.copy(), comp.copy())
    assert np.array_equal(a[0], b[0]) and np.array_equal(a[1], b[1]) and a[2] == b[2]


def test_real_output_crs_validity_and_area():
    with rasterio.open(OUT / "instances.tif") as s:
        inst, crs, tf = s.read(1), s.crs, s.transform
    with rasterio.open(BASE / "mask.tif") as s:
        mask, bcrs, btf = s.read(1).astype(bool), s.crs, s.transform
    assert crs == bcrs and tf == btf
    assert np.array_equal(inst > 0, mask), "building pixels changed"
    gdf = gpd.read_file(OUT / "buildings.geojson")
    base = gpd.read_file(BASE / "buildings.geojson")
    assert gdf.crs == bcrs and gdf.is_valid.all() and not gdf.is_empty.any()
    assert set(gdf.geom_type) <= {"Polygon", "MultiPolygon"}
    # total polygon area within 1 % of the baseline polygons (differences only from simplifying new cuts
    # and from split parts that fall below the 10 m^2 minimum)
    assert abs(gdf.area.sum() - base.area.sum()) / base.area.sum() < 0.01, (gdf.area.sum(), base.area.sum())
    assert (gdf.loc[gdf.rgb_split, "needs_review"]).all()


def test_real_cuts_inside_mask_and_flagged():
    cuts = gpd.read_file(OUT / "cut_lines.geojson")
    with rasterio.open(OUT / "mask.tif") as s:
        mask, tf = s.read(1).astype(bool), s.transform
    assert (cuts.status == "proposed_cut").all() and cuts.needs_review.all()
    assert set(cuts.confidence) <= {"high", "medium"}
    inv = ~tf
    for g in cuts.geometry:  # every cut vertex lies on/inside the building mask (pixel-edge coordinates)
        for line in getattr(g, "geoms", [g]):
            for x, y in line.coords:
                c, r = inv * (x, y)
                r0, c0 = int(np.clip(round(r), 1, mask.shape[0] - 1)), int(np.clip(round(c), 1, mask.shape[1] - 1))
                assert mask[r0 - 1:r0 + 1, c0 - 1:c0 + 1].any(), (x, y)
    summary = json.loads((OUT / "run_summary.json").read_text())
    assert summary["proposed_cuts"] == len(cuts)


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"ALL {len(tests)} RGB-GUIDED TESTS PASSED")
