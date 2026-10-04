"""Failure diagnosis for a building run on the W1-W4 design windows (+ label-free scene statistics).

Deliberately restricted to data/validation (W1-W4). The independent V1-V4 set is frozen and is
not read by this script.

For every scored reference label: area, WHU probability inside it, best-matching prediction and a
failure class (matched / merged / partial / missed / oversized). For every pair of reference labels
closer than 1.5 m: physical gap width and WHU probability in the gap / on the shared boundary.
Scene-wide (label-free): connected components of the mask, how many fall below the min-area filter.

Example:
    python scripts/diagnose_failures.py outputs/lapaz_predio_bisa_whu_0.3m outputs/rgb_guided_reviewed/final/predictions outputs/instance_study/diagnosis
"""

import argparse
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import rasterize
from scipy import ndimage as ndi

from score_models import clip

ROOT = Path(__file__).resolve().parents[1]
FOUR = ndi.generate_binary_structure(2, 1)


def classify(lab_geoms, preds):
    rows = []
    for lid, g in lab_geoms:
        ov = [(p.intersection(g).area, p) for p in preds if p.intersects(g)]
        if not ov:
            rows.append((lid, "missed", 0.0, 0.0, 0))
            continue
        a, p = max(ov, key=lambda t: t[0])
        iou, cov = a / (p.area + g.area - a), a / g.area
        n_in = sum(1 for _, g2 in lab_geoms if p.intersection(g2).area > 0.5 * g2.area)
        cls = "matched" if iou >= 0.5 else ("merged" if cov >= 0.5 and n_in >= 2 else
                                             ("partial" if cov < 0.5 else "oversized"))
        rows.append((lid, cls, round(iou, 3), round(cov, 3), n_in))
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("baseline_dir", type=Path)
    ap.add_argument("compare_dir", type=Path, help="e.g. reviewed RGB-guided predictions")
    ap.add_argument("out_dir", type=Path)
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    labels = gpd.read_file(ROOT / "data" / "validation" / "reviewed_labels.geojson")
    windows = gpd.read_file(ROOT / "data" / "validation" / "windows.geojson")
    truth = labels[~labels.ignore]
    summ = json.loads((args.baseline_dir / "run_summary.json").read_text())
    with rasterio.open(args.baseline_dir / "probability.tif") as s:
        prob, tf = s.read(1), s.transform
    px = abs(tf.a)
    mask = prob >= summ["threshold"]
    base = gpd.read_file(args.baseline_dir / "buildings.geojson")
    comp_pred = gpd.read_file(args.compare_dir / "buildings.geojson")

    # tiling: distance of a label centroid to the nearest tile seam on the inference grid
    win, ov = summ.get("window", 512), summ.get("overlap", 128)
    stride, inv = win - ov, ~tf

    def seam_dist(g):
        c, r = inv * g.centroid.coords[0]
        return round(min(c % stride, stride - c % stride, r % stride, stride - r % stride) * px, 1)

    # ---- per-label probability + failure class
    lab_rows = []
    for _, w in windows.iterrows():
        lab = truth[truth.window_id == w.window_id]
        geoms = list(zip(lab.label_id, lab.geometry.intersection(w.geometry)))
        cls_b = {r[0]: r[1:] for r in classify(geoms, clip(base, w.geometry, 2.0))}
        cls_c = {r[0]: r[1:] for r in classify(geoms, clip(comp_pred, w.geometry, 2.0))}
        for lid, g in geoms:
            m = rasterize([g], out_shape=prob.shape, transform=tf, dtype="uint8").astype(bool)
            p = prob[m] if m.any() else np.array([np.nan])
            lab_rows.append({"label_id": lid, "window": w.window_id[:2], "area_m2": round(g.area, 1),
                             "below_min_area": g.area < summ["min_area_m2"],
                             "prob_mean": round(float(np.nanmean(p)), 3), "frac_ge_thr": round(float(np.nanmean(p >= summ["threshold"])), 3),
                             "frac_ge_0.2": round(float(np.nanmean(p >= 0.2)), 3),
                             "baseline_class": cls_b[lid][0], "baseline_iou": cls_b[lid][1],
                             "compare_class": cls_c[lid][0], "compare_iou": cls_c[lid][1],
                             "tile_seam_dist_m": seam_dist(g)})
    L = pd.DataFrame(lab_rows)

    # ---- adjacent label pairs: physical gap and probability in the gap
    pair_rows = []
    for wid, g in truth.groupby("window_id"):
        recs = list(zip(g.label_id, g.geometry))
        for i, (a, ga) in enumerate(recs):
            for b, gb in recs[i + 1:]:
                d = ga.distance(gb)
                if d > 1.5:
                    continue
                if d > 0.05:  # real gap: probability over the gap strip
                    strip = ga.buffer(d + 0.05).intersection(gb.buffer(d + 0.05)).difference(ga).difference(gb)
                    kind = "gap"
                else:  # touching / overlapping: probability on a 0.6 m band around the shared boundary
                    strip = ga.buffer(0.3).intersection(gb.buffer(0.3))
                    kind = "touching"
                m = rasterize([strip], out_shape=prob.shape, transform=tf, dtype="uint8", all_touched=True).astype(bool) \
                    if not strip.is_empty else np.zeros(prob.shape, bool)
                pv = prob[m]
                pair_rows.append({"window": wid[:2], "a": a, "b": b, "kind": kind, "gap_m": round(d, 2),
                                  "gap_in_px": round(d / px, 2), "strip_px": int(m.sum()),
                                  "prob_min": round(float(pv.min()), 3) if pv.size else None,
                                  "prob_mean": round(float(pv.mean()), 3) if pv.size else None})
    P = pd.DataFrame(pair_rows)

    # ---- label-free scene statistics: components vs min-area filter
    comps, n = ndi.label(mask, structure=FOUR)
    areas = np.bincount(comps.ravel())[1:] * px * px
    small = areas < summ["min_area_m2"]
    comp_mean_prob = ndi.mean(prob, comps, np.arange(1, n + 1))
    scene = {"components": int(n), "components_below_min_area": int(small.sum()),
             "area_below_min_area_m2": round(float(areas[small].sum()), 1),
             "area_total_m2": round(float(areas.sum()), 1),
             "below_min_area_size_quartiles_m2": [round(float(q), 2) for q in np.quantile(areas[small], [0.25, 0.5, 0.75])] if small.any() else [],
             "below_min_area_mean_prob_median": round(float(np.median(comp_mean_prob[small])), 3) if small.any() else None,
             "inference_res_m": px, "threshold": summ["threshold"], "min_area_m2": summ["min_area_m2"],
             "tile_px": summ.get("window"), "tile_overlap_px": summ.get("overlap")}

    L.to_csv(args.out_dir / "labels_W1-W4.csv", index=False)
    P.to_csv(args.out_dir / "adjacent_pairs_W1-W4.csv", index=False)
    (args.out_dir / "scene_stats.json").write_text(json.dumps(scene, indent=2))
    print(L.to_string(index=False))
    print("\nadjacent label pairs (<=1.5 m):")
    print(P.to_string(index=False))
    print("\nscene:", json.dumps(scene))


if __name__ == "__main__":
    main()
