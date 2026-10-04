"""Score building-footprint model runs against human-approved validation labels.

Example (after the labels have been reviewed and every feature has status=approved):
    python scripts/score_models.py \
        --run dlinknet=outputs/lapaz_predio_bisa_0.4m \
        --run whu=outputs/lapaz_predio_bisa_whu_0.3m \
        --secondary-reference data/reference/ms_buildings_lapaz.geojson

What is scored: each run's final product (buildings.geojson, i.e. after thresholding,
min-area filtering and simplification), clipped to each validation window.

Pixel metrics: labels and predictions are rasterized on one common grid per window
(--eval-res, default 0.1 m) so models run at different resolutions are compared on
identical pixels. No-data pixels of the source orthophoto and label features with
ignore=true are excluded. TP/FP/FN are summed over all windows (micro-average), and
also reported per window.

Building-level metrics: one-to-one greedy matching of predicted polygons to label
polygons by descending polygon IoU; a pair counts as a match only if IoU >= 0.5.
precision = matches / predicted polygons, recall = matches / label polygons. Pieces
smaller than --min-piece-area m^2 after clipping to a window are dropped on both sides
(edge slivers). Predictions mostly (>50%) inside ignore regions are dropped.

The Microsoft footprints, if given, are scored the same way but reported in a
separate "secondary reference" section: they are ML-derived, of unknown imagery date,
and are not ground truth.

Outputs are detected building footprints; nothing here evaluates legal parcel boundaries.
"""

import argparse
import json
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.warp import reproject
from rasterio.transform import from_origin

ROOT = Path(__file__).resolve().parents[1]
MATCH_IOU = 0.5


def safe_div(a, b):
    return float(a / b) if b else None


def pixel_counts(label_geoms, pred_geoms, ignore_geoms, bounds, res, valid_fn):
    left, bottom, right, top = bounds
    w, h = round((right - left) / res), round((top - bottom) / res)
    tf = from_origin(left, top, res, res)

    def burn(geoms):
        geoms = [g for g in geoms if not g.is_empty]
        if not geoms:
            return np.zeros((h, w), bool)
        return rasterize(geoms, out_shape=(h, w), transform=tf, dtype="uint8").astype(bool)

    lab, pred, ign = burn(label_geoms), burn(pred_geoms), burn(ignore_geoms)
    keep = valid_fn((h, w), tf) & ~ign
    return {"tp": int((lab & pred & keep).sum()), "fp": int((~lab & pred & keep).sum()),
            "fn": int((lab & ~pred & keep).sum()), "eval_px": int(keep.sum())}


def match_objects(label_geoms, pred_geoms, thr=MATCH_IOU):
    pairs = []
    for i, p in enumerate(pred_geoms):
        for j, g in enumerate(label_geoms):
            if p.intersects(g):
                inter = p.intersection(g).area
                iou = inter / (p.area + g.area - inter)
                if iou >= thr:
                    pairs.append((iou, i, j))
    used_p, used_l, matches = set(), set(), []
    for iou, i, j in sorted(pairs, reverse=True):
        if i not in used_p and j not in used_l:
            used_p.add(i); used_l.add(j); matches.append(iou)
    return {"matched": len(matches), "n_pred": len(pred_geoms), "n_label": len(label_geoms),
            "mean_matched_iou": float(np.mean(matches)) if matches else None}


def metrics(px, obj):
    tp, fp, fn = px["tp"], px["fp"], px["fn"]
    return {"pixel_precision": safe_div(tp, tp + fp), "pixel_recall": safe_div(tp, tp + fn),
            "pixel_iou": safe_div(tp, tp + fp + fn),
            "building_precision": safe_div(obj["matched"], obj["n_pred"]),
            "building_recall": safe_div(obj["matched"], obj["n_label"]),
            **px, **obj}


def clip(gdf, window_geom, min_area):
    parts = gdf.geometry.intersection(window_geom).explode(index_parts=False)
    parts = parts[parts.geom_type == "Polygon"]
    return [g for g in parts if g.area >= min_area]


def score(name, preds, labels, windows, ignore, res, min_area, valid_fn):
    total_px, total_obj, per_window = {"tp": 0, "fp": 0, "fn": 0, "eval_px": 0}, \
        {"matched": 0, "n_pred": 0, "n_label": 0}, {}
    ious = []
    for _, w in windows.iterrows():
        wg = w.geometry
        lab = clip(labels[labels.window_id == w.window_id], wg, min_area)
        ign = clip(ignore[ignore.window_id == w.window_id], wg, 0) if len(ignore) else []
        prd = clip(preds, wg, min_area)
        if ign:
            ign_union = gpd.GeoSeries(ign).union_all()
            prd = [p for p in prd if p.intersection(ign_union).area <= 0.5 * p.area]
        px = pixel_counts(lab, prd, ign, wg.bounds, res, valid_fn)
        obj = match_objects(lab, prd)
        per_window[w.window_id] = metrics(px, obj)
        for k in total_px:
            total_px[k] += px[k]
        for k in total_obj:
            total_obj[k] += obj[k]
        if obj["mean_matched_iou"] is not None:
            ious += [obj["mean_matched_iou"]] * obj["matched"]
    total_obj["mean_matched_iou"] = float(np.mean(ious)) if ious else None
    return {"name": name, "overall": metrics(total_px, total_obj), "per_window": per_window}


def fmt(v):
    return "n/a" if v is None else (f"{v:.3f}" if isinstance(v, float) else str(v))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--labels", type=Path, default=ROOT / "data" / "validation" / "provisional_labels.geojson")
    ap.add_argument("--windows", type=Path, default=ROOT / "data" / "validation" / "windows.geojson")
    ap.add_argument("--image", type=Path, default=ROOT / "data" / "lapaz_predio_bisa.tif")
    ap.add_argument("--run", action="append", default=[], metavar="NAME=OUTPUT_DIR", required=True)
    ap.add_argument("--secondary-reference", type=Path, default=None,
                    help="e.g. Microsoft footprints; reported separately, never as ground truth")
    ap.add_argument("--eval-res", type=float, default=0.1)
    ap.add_argument("--min-piece-area", type=float, default=2.0)
    ap.add_argument("--out-dir", type=Path, default=ROOT / "outputs" / "evaluation")
    args = ap.parse_args()

    labels = gpd.read_file(args.labels)
    windows = gpd.read_file(args.windows)
    status = labels["status"].value_counts().to_dict()
    if set(status) != {"approved"}:
        sys.exit(f"Refusing to score: labels must all have status=approved after human review; found {status}. "
                 f"Review {args.labels} first.")

    with rasterio.open(args.image) as src:
        crs = src.crs
        assert labels.crs == crs and windows.crs == crs, (labels.crs, windows.crs, crs)

        def valid_fn(shape, tf):
            out = np.zeros(shape, np.uint8)
            reproject(src.dataset_mask(), out, src_transform=src.transform, src_crs=crs,
                      dst_transform=tf, dst_crs=crs, resampling=Resampling.nearest)
            return out > 0

        is_ignore = labels["ignore"].fillna(False).astype(bool) if "ignore" in labels else \
            np.zeros(len(labels), bool)
        ignore, truth = labels[is_ignore], labels[~is_ignore]

        results, meta = [], {}
        for spec in args.run:
            name, d = spec.split("=", 1)
            d = Path(d)
            preds = gpd.read_file(d / "buildings.geojson")
            assert preds.crs == crs, (name, preds.crs)
            meta[name] = json.loads((d / "run_summary.json").read_text())
            results.append(score(name, preds, truth, windows, ignore, args.eval_res, args.min_piece_area, valid_fn))
        secondary = None
        if args.secondary_reference:
            ref = gpd.read_file(args.secondary_reference).to_crs(crs)
            secondary = score("microsoft_ml_footprints", ref, truth, windows, ignore,
                              args.eval_res, args.min_piece_area, valid_fn)

    report = {
        "labels": str(args.labels), "n_labels": int(len(truth)), "n_ignore_regions": int(len(ignore)),
        "label_confidence_counts": truth["confidence"].value_counts().to_dict(),
        "eval_res_m": args.eval_res, "match_iou_threshold": MATCH_IOU, "min_piece_area_m2": args.min_piece_area,
        "runs": {r["name"]: {**r, "run_summary": meta[r["name"]]} for r in results},
        "secondary_reference_not_ground_truth": secondary,
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "scores.json").write_text(json.dumps(report, indent=2, default=str))

    keys = ["pixel_precision", "pixel_recall", "pixel_iou", "building_precision", "building_recall",
            "matched", "n_pred", "n_label", "mean_matched_iou"]
    lines = [f"# Building detection scores ({len(truth)} approved labels, {len(windows)} windows)", "",
             f"Eval grid {args.eval_res} m; building match IoU >= {MATCH_IOU}; pieces < {args.min_piece_area} m^2 dropped.",
             "", "| run | " + " | ".join(keys) + " |", "|---" * (len(keys) + 1) + "|"]
    for r in results:
        lines.append(f"| {r['name']} | " + " | ".join(fmt(r["overall"][k]) for k in keys) + " |")
    if secondary:
        lines += ["", "## Secondary reference (Microsoft ML footprints - NOT ground truth)", "",
                  "| source | " + " | ".join(keys) + " |", "|---" * (len(keys) + 1) + "|",
                  "| microsoft | " + " | ".join(fmt(secondary["overall"][k]) for k in keys) + " |"]
    lines += ["", "## Per window", ""]
    for r in results + ([secondary] if secondary else []):
        for wid, m in r["per_window"].items():
            lines.append(f"- {r['name']} / {wid}: " + ", ".join(f"{k}={fmt(m[k])}" for k in keys))
    (args.out_dir / "scores.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()

