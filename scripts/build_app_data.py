"""Build the static data bundle for the review app (app/) from frozen project outputs (read-only).

Writes into OUT_DIR (default app/data; refuses a non-empty directory):
  ortho_3857.png + ortho.json      orthophoto warped to Web Mercator (display only) and its lat/lon bounds
  layer_A.geojson / _B / _C        candidate building footprints in EPSG:4326 (RFC 7946) with display flags
  windows.geojson                  evaluation windows labelled by split (W/V = development, T = held-out test)
  manifest.json                    layer provenance (source paths + SHA-256), metrics by split, caveats

Nothing in the source outputs is modified. Display flags are heuristic cues, NOT evaluated error labels.

Example:
    python scripts/build_app_data.py --out-dir app/data
"""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling
from rasterio.warp import calculate_default_transform, reproject, transform_bounds

ROOT = Path(__file__).resolve().parents[1]
IMAGE = ROOT / "data" / "lapaz_predio_bisa.tif"
LAYERS = {
    "A": {"name": "WHU UNet++ baseline", "dir": "outputs/lapaz_predio_bisa_whu_0.3m", "color": "#00a8e8",
          "score_field": "mean_prob", "score_label": "mean WHU building probability"},
    "B": {"name": "Approved RGB-guided post-processing", "dir": "outputs/rgb_guided_reviewed/final/predictions",
          "color": "#e040fb", "score_field": "mean_prob", "score_label": "mean WHU building probability"},
    "C": {"name": "Mask R-CNN @0.3 m (candidate, not promoted)", "dir": "outputs/phase3/maskrcnn_0.3m",
          "color": "#ff8c00", "score_field": "mean_prob", "score_label": "Mask R-CNN instance score"},
}
TEST_KEYS = {"A": "A_whu_baseline", "B": "B_approved_postproc", "C": "C_maskrcnn_0.3m"}
SMALL_M2, LARGE_M2, LOW_SCORE, TOUCH_M = 20.0, 300.0, 0.7, 0.3
DISCLAIMER = ("AI-detected building footprints for review. NOT legal cadastral parcel boundaries; "
              "not verified against authoritative parcel data.")


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def flags_for(gdf: gpd.GeoDataFrame, key: str) -> list[list[str]]:
    sidx = gdf.sindex
    out = []
    for i, g in enumerate(gdf.geometry):
        f = []
        if g.area < SMALL_M2:
            f.append("small_roof")
        if key in ("A", "B") and g.area > LARGE_M2:
            f.append("possible_merged_buildings")
        near = [j for j in sidx.query(g.buffer(TOUCH_M)) if j != i and gdf.geometry.iloc[j].distance(g) <= TOUCH_M]
        if near:
            f.append("possible_over_split" if key == "C" else "touching_neighbour")
        s = gdf.iloc[i].get(LAYERS[key]["score_field"])
        if s is not None and float(s) < LOW_SCORE:
            f.append("low_score")
        out.append(f)
    return out


def metrics_block(scores: dict, run: str) -> dict | None:
    r = scores["runs"].get(run)
    if r is None:
        return None
    keep = ("pixel_precision", "pixel_recall", "pixel_iou", "building_precision", "building_recall",
            "tp", "fp", "fn", "matched", "n_pred", "n_label")
    return {"overall": {k: r["overall"][k] for k in keep},
            "per_window": {w: {k: m[k] for k in keep} for w, m in r["per_window"].items()},
            "settings": {"eval_res_m": scores["eval_res_m"], "match_iou": scores["match_iou_threshold"],
                         "min_piece_m2": scores["min_piece_area_m2"], "n_labels": scores["n_labels"]}}


def render_ortho(out_dir: Path, res: float):
    with rasterio.open(IMAGE) as src:
        tf, w, h = calculate_default_transform(src.crs, "EPSG:3857", src.width, src.height, *src.bounds, resolution=res)
        rgb = np.zeros((3, h, w), np.uint8)
        alpha = np.zeros((h, w), np.uint8)
        for b in (1, 2, 3):
            reproject(rasterio.band(src, b), rgb[b - 1], dst_transform=tf, dst_crs="EPSG:3857", resampling=Resampling.average)
        reproject(src.dataset_mask(), alpha, src_transform=src.transform, src_crs=src.crs, dst_transform=tf,
                  dst_crs="EPSG:3857", resampling=Resampling.nearest)
        b3857 = (tf.c, tf.f + tf.e * h, tf.c + tf.a * w, tf.f)
        west, south, east, north = transform_bounds("EPSG:3857", "EPSG:4326", *b3857)
        Image.fromarray(np.dstack([rgb.transpose(1, 2, 0), alpha])).save(out_dir / "ortho_3857.png", optimize=True)
        meta = {"bounds_latlon": [[south, west], [north, east]], "size_px": [w, h], "resolution_m_3857": res,
                "source": str(IMAGE.relative_to(ROOT)), "source_crs": src.crs.to_string(),
                "source_sha256": sha256(IMAGE), "note": "display copy warped to EPSG:3857; analysis uses the source CRS"}
    (out_dir / "ortho.json").write_text(json.dumps(meta, indent=2))
    return meta


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", type=Path, default=ROOT / "app" / "data")
    ap.add_argument("--ortho-res", type=float, default=0.2, help="display resolution in EPSG:3857 metres")
    args = ap.parse_args()
    if args.out_dir.exists() and any(args.out_dir.iterdir()):
        raise SystemExit(f"{args.out_dir} is not empty - refusing to overwrite")
    args.out_dir.mkdir(parents=True, exist_ok=True)

    ortho = render_ortho(args.out_dir, args.ortho_res)
    test = json.loads((ROOT / "outputs/phase3/test_scores_T1-T4/scores.json").read_text())
    dev = {s: json.loads((ROOT / f"outputs/phase3/dev_scores/{s}/scores.json").read_text()) for s in ("W1-W4", "V1-V4")}
    layers = {}
    for key, cfg in LAYERS.items():
        src = ROOT / cfg["dir"] / "buildings.geojson"
        gdf = gpd.read_file(src)
        assert gdf.crs.to_epsg() == 32719, (key, gdf.crs)
        gdf["layer"] = key
        gdf["source_id"] = gdf["id"].astype(int)
        gdf["area_m2"] = gdf.area.round(1)
        gdf["score"] = gdf[cfg["score_field"]].astype(float).round(3)
        gdf["flags"] = [",".join(f) for f in flags_for(gdf, key)]
        gdf["origin"] = "ai_prediction"
        out = gdf[["layer", "source_id", "area_m2", "score", "flags", "origin", "geometry"]].to_crs(4326)
        out.to_file(args.out_dir / f"layer_{key}.geojson", driver="GeoJSON")
        layers[key] = {**cfg, "file": f"layer_{key}.geojson", "count": int(len(gdf)),
                       "source": str(src.relative_to(ROOT)).replace("\\", "/"), "source_sha256": sha256(src),
                       "source_crs": "EPSG:32719",
                       "metrics": {"test_T1-T4": metrics_block(test, TEST_KEYS[key]),
                                   "development_W1-W4": metrics_block(dev["W1-W4"], TEST_KEYS[key]),
                                   "development_V1-V4": metrics_block(dev["V1-V4"], TEST_KEYS[key])},
                       "flag_counts": {f: int(gdf["flags"].str.contains(f).sum()) for f in
                                       ("small_roof", "possible_merged_buildings", "touching_neighbour",
                                        "possible_over_split", "low_score")}}
    # metrics-only entries (no display layer): hybrid D, scored on T1-T4 and once on development
    devD = {s: json.loads((ROOT / f"outputs/phase3/dev_scores_D/{s}/scores.json").read_text()) for s in ("W1-W4", "V1-V4")}
    metrics_only = {"D": {"name": "Hybrid: WHU mask partitioned by Mask R-CNN", "color": "#a3e635",
                          "source": "outputs/phase3/hybrid_whu_maskrcnn",
                          "metrics": {"test_T1-T4": metrics_block(test, "D_hybrid"),
                                      "development_W1-W4": metrics_block(devD["W1-W4"], "D_hybrid"),
                                      "development_V1-V4": metrics_block(devD["V1-V4"], "D_hybrid")},
                          "note": "metrics only; not shown as a map layer"}}
    with rasterio.open(IMAGE) as src:
        imagery = {"file": IMAGE.name, "crs": src.crs.to_string(), "native_res_m": round(abs(src.res[0]), 4),
                   "size_px": [src.width, src.height], "bands": src.count,
                   "extent_m": [round(src.bounds.right - src.bounds.left, 1), round(src.bounds.top - src.bounds.bottom, 1)],
                   "software": src.tags().get("TIFFTAG_SOFTWARE"), "location": "La Paz, Bolivia (from raster georeference)"}
    wins = []
    for path, split in (("data/validation/windows.geojson", "development (W1-W4)"),
                        ("data/validation_independent/windows.geojson", "development (V1-V4, previously viewed)"),
                        ("data/validation_test/windows.geojson", "held-out test (T1-T4)")):
        w = gpd.read_file(ROOT / path)
        w["split"] = split
        wins.append(w[["window_id", "split", "geometry"]])
    gpd.GeoDataFrame(np.concatenate([w.values for w in wins]), columns=["window_id", "split", "geometry"],
                     geometry="geometry", crs="EPSG:32719").to_crs(4326).to_file(args.out_dir / "windows.geojson",
                                                                                driver="GeoJSON")
    manifest = {
        "built_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "disclaimer": DISCLAIMER,
        "ortho": ortho, "imagery": imagery, "layers": layers, "metrics_only": metrics_only, "analysis_crs": "EPSG:32719",
        "flag_rules": {"small_roof": f"area < {SMALL_M2} m²", "possible_merged_buildings": f"A/B polygon area > {LARGE_M2} m²",
                       "touching_neighbour": f"another polygon of the same layer within {TOUCH_M} m (A/B)",
                       "possible_over_split": f"another Mask R-CNN polygon within {TOUCH_M} m (C)",
                       "low_score": f"score < {LOW_SCORE}",
                       "note": "Display heuristics chosen from qualitative failure analysis; NOT evaluated error labels."},
        "known_failure_modes": [
            {"mode": "Missed small / rusty / patchwork roofs", "evidence": "W1–W4 diagnosis: 5 of 32 labels with WHU probability ≈ 0; low pixel recall of C on all splits", "applies_to": "A, B, C"},
            {"mode": "Adjacent roofs merged into one polygon", "evidence": "W1–W4: 17 of 32 labels merged in A; V1/V2 0 matched buildings for A and B", "applies_to": "A (B partly)"},
            {"mode": "Complex single roofs over-split", "evidence": "T3 (qualitative): C split one large house into 2 pieces", "applies_to": "C"},
            {"mode": "Shared walls below inference resolution", "evidence": "W1–W4: most gaps between roofs 0.1–0.4 m (≤ 1.3 px at 0.3 m)", "applies_to": "A, B, C"}],
        "evaluation_notes": [
            "test_T1-T4 = held-out windows scored once under outputs/phase3/PROTOCOL.md (30 reviewed buildings).",
            "development_* = windows already viewed during method design; NOT test metrics.",
            "Protocol decision: no method shows a robust improvement over A. C meets the test rule on T1–T4 but failed it on development (pixel IoU 0.404 vs 0.809) and was not promoted.",
            "Small samples (30 / 32 / 49 labels) from one orthophoto; metrics do not establish general accuracy."],
    }
    (args.out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"app data -> {args.out_dir}: " + ", ".join(f"{k}={v['count']}" for k, v in layers.items()) +
          f"; ortho {ortho['size_px']}")


if __name__ == "__main__":
    main()
