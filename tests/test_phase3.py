"""Tests for phase-3 logic: Mask R-CNN instance handling, hybrid partition, protocol lock, freeze.

Run: python tests/test_phase3.py
"""

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import rasterio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from hybrid_instance_partition import partition  # noqa: E402
from run_maskrcnn_inference import OVERLAP, TILE, core_bounds, paint, select_instances, starts  # noqa: E402

P3 = ROOT / "outputs" / "phase3"


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def test_tiles_cover_image():
    for n in (300, 512, 513, 1040, 1180):
        s = starts(n)
        cover = np.zeros(max(n, TILE), bool)
        for x in s:
            cover[x:x + TILE] = True
        assert cover[:n].all() and s[0] == 0


def test_core_regions_partition_image():
    h, w = 1180, 1040
    owner = np.zeros((h, w), np.int16)
    for y in starts(h):
        for x in starts(w):
            cy0, cx0, cy1, cx1 = core_bounds(y, x, h, w)
            owner[cy0:min(cy1, h), cx0:min(cx1, w)] += 1
    assert owner.min() >= 1  # every pixel is in at least one core -> no instance is lost by the centroid rule


def test_select_instances_score_and_core():
    h = w = 1000
    m = np.zeros((2, TILE, TILE), bool)
    m[0, 200:260, 200:260] = True        # centroid in core of tile (0,0)
    m[1, 480:510, 480:510] = True        # centroid in the overlap margin -> owned by neighbour tile
    got = select_instances(m, np.array([0.9, 0.9]), 0, 0, h, w)
    assert len(got) == 1 and got[0][1].mean() < 300
    assert select_instances(m[:1], np.array([0.4]), 0, 0, h, w) == []  # below score threshold


def test_paint_highest_score_wins():
    a = (0.6, np.array([5, 5, 6]), np.array([5, 6, 5]))
    b = (0.9, np.array([5]), np.array([5]))
    inst, score = paint([a, b], (10, 10))
    assert score[5, 5] == np.float32(0.9) and score[5, 6] == np.float32(0.6)
    assert inst[5, 5] != inst[5, 6]


def test_partition_splits_touching_buildings_with_two_seeds():
    px = 0.3
    mask = np.zeros((60, 100), bool)
    mask[10:50, 10:90] = True                   # one WHU blob, 12 x 24 m
    inst = np.zeros_like(mask, np.int32)
    inst[12:48, 12:45] = 1                      # instance A (~107 m²)
    inst[12:48, 55:88] = 2                      # instance B
    out = partition(mask, inst, px)
    assert np.array_equal(out > 0, mask)
    assert len(np.unique(out[mask])) == 2
    assert out[30, 20] != out[30, 80]


def test_partition_keeps_blob_without_seeds_or_with_small_seeds():
    px = 0.3
    mask = np.zeros((60, 100), bool)
    mask[10:50, 10:90] = True
    assert len(np.unique(partition(mask, np.zeros_like(mask, np.int32), px)[mask])) == 1
    inst = np.zeros_like(mask, np.int32)
    inst[12:48, 12:45] = 1
    inst[20:25, 60:65] = 2                      # 2.25 m² < 10 m² -> ignored
    assert len(np.unique(partition(mask, inst, px)[mask])) == 1


def test_partition_never_leaves_mask():
    px = 0.3
    mask = np.zeros((40, 40), bool)
    mask[5:20, 5:20] = True
    inst = np.zeros_like(mask, np.int32)
    inst[0:40, 0:40] = 1                        # instance much larger than the WHU mask
    out = partition(mask, inst, px)
    assert np.array_equal(out > 0, mask)


def test_real_outputs_crs_and_coverage():
    with rasterio.open(ROOT / "outputs" / "lapaz_predio_bisa_whu_0.3m" / "mask.tif") as s:
        whu, crs, tf = s.read(1).astype(bool), s.crs, s.transform
    with rasterio.open(P3 / "hybrid_whu_maskrcnn" / "instances.tif") as s:
        assert s.crs == crs and s.transform == tf
        assert np.array_equal(s.read(1) > 0, whu)
    for d in ("maskrcnn_0.6m", "maskrcnn_0.3m", "hybrid_whu_maskrcnn"):
        summ = json.loads((P3 / d / "run_summary.json").read_text())
        assert summ["crs"] == "EPSG:32719" and summ["min_area_m2"] == 10.0


def test_protocol_locked_after_amendment():
    for line in (P3 / "protocol_lock_amend1.sha256").read_text().splitlines():
        digest, path = line.split(maxsplit=1)
        assert sha(ROOT / path.lstrip("*")) == digest, path


def test_frozen_inputs_unchanged():
    frozen = ("models/whu/", "models/best.pt", "data/lapaz_predio_bisa.tif", "data/validation/",
              "data/validation_independent/", "outputs/lapaz_predio_bisa_whu_0.3m/", "outputs/rgb_guided_reviewed/final/",
              "outputs/independent_validation_final/")
    changed = []
    for line in (P3 / "frozen_hashes.sha256").read_text().splitlines():
        digest, path = line.split(maxsplit=1)
        path = path.lstrip("*")
        if path.startswith(frozen) and sha(ROOT / path) != digest:
            changed.append(path)
    assert not changed, changed


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"ALL {len(tests)} PHASE-3 TESTS PASSED")
