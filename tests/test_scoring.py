"""Self-tests for scripts/score_models.py using synthetic inputs with known answers.

Never scores a model: predictions here are copies/shifts of the labels themselves.
Run: python tests/test_scoring.py
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely.affinity import translate
from shapely.geometry import box

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from score_models import match_objects, pixel_counts  # noqa: E402

PY = sys.executable
ALL_VALID = lambda shape, tf: np.ones(shape, bool)  # noqa: E731


def test_pixel_counts():
    lab, pred = [box(0, 0, 10, 10)], [box(5, 0, 15, 10)]
    c = pixel_counts(lab, pred, [], (0, 0, 20, 20), 0.5, ALL_VALID)
    assert (c["tp"], c["fp"], c["fn"]) == (200, 200, 200), c  # 50 m^2 each at 0.25 m^2/px
    c = pixel_counts(lab, pred, [box(10, 0, 15, 10)], (0, 0, 20, 20), 0.5, ALL_VALID)
    assert c["fp"] == 0 and c["eval_px"] == 1600 - 200, c  # ignore region removes the FP pixels
    nodata = lambda shape, tf: np.arange(shape[1])[None, :].repeat(shape[0], 0) < 20  # noqa: E731
    c = pixel_counts(lab, pred, [], (0, 0, 20, 20), 0.5, nodata)  # only x < 10 m valid
    assert (c["tp"], c["fp"], c["fn"]) == (200, 0, 200), c


def test_matching():
    a = [box(0, 0, 10, 10), box(20, 0, 30, 10)]
    assert match_objects(a, a)["matched"] == 2
    # shift by 4 m -> IoU 60/140 = 0.43 < 0.5 -> no match; 3 m -> 70/130 = 0.54 -> match
    assert match_objects(a, [translate(g, 4, 0) for g in a])["matched"] == 0
    assert match_objects(a, [translate(g, 3, 0) for g in a])["matched"] == 2
    # one big prediction covering two labels (merged buildings) -> at most one match
    merged = match_objects([box(0, 0, 10, 10), box(10, 0, 12, 10)], [box(0, 0, 12, 10)])
    assert merged["matched"] == 1 and merged["n_pred"] == 1 and merged["n_label"] == 2, merged


def test_end_to_end_and_approval_gate():
    labels = gpd.read_file(ROOT / "data" / "validation" / "provisional_labels.geojson")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        base = [PY, str(ROOT / "scripts" / "score_models.py"), "--out-dir", str(td / "eval")]
        # 1) the real provisional labels must be refused
        r = subprocess.run(base + ["--run", f"x={td}"], capture_output=True, text=True)
        assert r.returncode != 0 and "Refusing to score" in (r.stderr + r.stdout), r

        approved = labels.assign(status="approved")
        approved.to_file(td / "labels.geojson", driver="GeoJSON")
        for name, shift in (("same", 0.0), ("shift1m", 1.0)):
            d = td / name
            d.mkdir()
            approved[["geometry"]].assign(geometry=approved.geometry.translate(shift, 0)) \
                .to_file(d / "buildings.geojson", driver="GeoJSON")
            (d / "run_summary.json").write_text(json.dumps({"synthetic": True}))
        r = subprocess.run(base + ["--labels", str(td / "labels.geojson"),
                                   "--run", f"same={td / 'same'}", "--run", f"shift={td / 'shift1m'}"],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        rep = json.loads((td / "eval" / "scores.json").read_text())
        same = rep["runs"]["same"]["overall"]
        assert same["pixel_iou"] == 1.0 and same["building_precision"] == 1.0 and same["building_recall"] == 1.0, same
        shift = rep["runs"]["shift"]["overall"]
        assert 0.5 < shift["pixel_iou"] < 1.0 and shift["fp"] > 0 and shift["fn"] > 0, shift


if __name__ == "__main__":
    for t in (test_pixel_counts, test_matching, test_end_to_end_and_approval_gate):
        t()
        print(f"PASS {t.__name__}")
    print("ALL SCORING SELF-TESTS PASSED")
