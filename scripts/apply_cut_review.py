"""Apply reviewed RGB-guided cuts to the WHU baseline (only cuts with status == "accepted").

Inputs (read-only): RGB-guided run dir (instances.tif), WHU baseline dir (mask.tif, probability.tif,
run_summary.json), reviewed cut file (cut_review_reviewed.geojson).
Every adjacent instance pair in the RGB-guided instances must have exactly one cut record; pairs whose
cut is rejected, uncertain or pending are merged back, so those areas keep the original unsplit
geometry. Building pixels are never changed.

Example:
    python scripts/apply_cut_review.py outputs/rgb_guided_v1 outputs/lapaz_predio_bisa_whu_0.3m \
        outputs/rgb_guided_reviewed/cut_review_reviewed.geojson outputs/rgb_guided_reviewed/predictions
"""

import argparse
import hashlib
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio

from postprocess_instances import _boundaries, polygonize
from run_building_inference import read_raster, save_previews, write_geotiff

ROOT = Path(__file__).resolve().parents[1]


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def merge_unaccepted(inst: np.ndarray, review: gpd.GeoDataFrame) -> tuple[np.ndarray, dict]:
    """Union-find over instance ids: merge every pair whose cut is not accepted."""
    recorded = {(min(a, b), max(a, b)): s for a, b, s in zip(review.part_a, review.part_b, review.status)}
    adjacent = set(_boundaries(inst).keys())
    missing = adjacent - set(recorded)
    if missing:
        raise SystemExit(f"{len(missing)} adjacent instance pairs have no cut record (e.g. {sorted(missing)[:3]})")
    parent = {int(i): int(i) for i in np.unique(inst[inst > 0])}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for (a, b), status in sorted(recorded.items()):
        if status != "accepted":
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)
    lut = np.zeros(int(inst.max()) + 1, np.int32)
    for i in parent:
        lut[i] = find(i)
    return lut[inst], recorded


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rgb_dir", type=Path)
    ap.add_argument("baseline_dir", type=Path)
    ap.add_argument("review_file", type=Path)
    ap.add_argument("out_dir", type=Path)
    args = ap.parse_args()
    for d in (args.rgb_dir, args.baseline_dir):
        if args.out_dir.resolve() == d.resolve():
            raise SystemExit("out_dir must be a new directory")
    args.out_dir.mkdir(parents=True, exist_ok=True)

    base = json.loads((args.baseline_dir / "run_summary.json").read_text())
    review = gpd.read_file(args.review_file)
    assert set(review.status) <= {"accepted", "rejected", "uncertain", "pending"}, set(review.status)
    with rasterio.open(args.rgb_dir / "instances.tif") as s:
        inst, tf, crs = s.read(1), s.transform, s.crs
    with rasterio.open(args.baseline_dir / "mask.tif") as s:
        mask, btf, bcrs = s.read(1).astype(bool), s.transform, s.crs
    with rasterio.open(args.baseline_dir / "probability.tif") as s:
        prob = s.read(1)
    assert tf == btf and crs == bcrs and np.array_equal(inst > 0, mask), "RGB-guided instances do not match baseline mask"

    merged, recorded = merge_unaccepted(inst, review)
    assert np.array_equal(merged > 0, mask)
    gdf = polygonize(merged, prob, tf, crs, base["min_area_m2"], base["simplify_m"])
    acc = review[review.status == "accepted"]
    split_ids = set()
    for a, b in zip(acc.part_a, acc.part_b):
        split_ids |= {int(merged[inst == a][0]), int(merged[inst == b][0])}
    gdf["split_by_accepted_cut"] = gdf["instance"].isin(split_ids)

    write_geotiff(args.out_dir / "mask.tif", mask, tf, crs, "uint8")
    write_geotiff(args.out_dir / "instances.tif", merged, tf, crs, "int32")
    for name, g in (("buildings.geojson", gdf), ("buildings_wgs84.geojson", gdf.to_crs(4326)),
                    ("accepted_cut_lines.geojson", acc)):
        (args.out_dir / name).unlink(missing_ok=True)
        g.to_file(args.out_dir / name, driver="GeoJSON")
    log = review.drop(columns="geometry").copy()
    log["applied"] = log.status == "accepted"
    log.to_csv(args.out_dir / "review_log.csv", index=False)
    rgb, _, rgb_tf, _ = read_raster(ROOT / base["image"], base["inference_res_m"])
    save_previews(args.out_dir, rgb, mask, gdf, tf)

    summary = {
        **{k: base[k] for k in ("image", "crs", "source_bounds", "native_res_m", "inference_res_m", "threshold",
                                "min_area_m2", "simplify_m")},
        "model": base.get("model"), "hf_revision": base.get("hf_revision"),
        "postprocess": "rgb_guided_v1 cuts filtered by human review (accepted only)",
        "inputs_sha256": {"rgb_instances": sha256(args.rgb_dir / "instances.tif"),
                          "baseline_mask": sha256(args.baseline_dir / "mask.tif"),
                          "review_file": sha256(args.review_file)},
        "cuts": review.status.value_counts().to_dict(), "cuts_applied": int(len(acc)),
        "instances": int(len(np.unique(merged[merged > 0]))), "feature_count": len(gdf),
        "baseline_feature_count": base["feature_count"], "building_pixels": int(mask.sum()),
    }
    (args.out_dir / "run_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"applied {len(acc)} accepted cuts of {len(review)} ({summary['cuts']}) -> "
          f"{summary['instances']} instances -> {len(gdf)} polygons (baseline {base['feature_count']})")


if __name__ == "__main__":
    main()
