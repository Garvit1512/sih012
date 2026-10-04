"""Method D (phase 3, PROTOCOL.md amendment 1): partition the frozen WHU mask with Mask R-CNN instances.

Building pixels = WHU baseline mask, unchanged. Seeds = each Mask R-CNN instance ∩ WHU mask, kept if
≥ --min-seed m² (default 10, the shared min-area rule). Inside each 4-connected WHU component, seeds
are grown geodesically (postprocess_instances._grow) until every component pixel is assigned. Pixels
are assigned to the highest-score instance where seeds overlap. Components with 0–1 seeds stay whole.
Polygons use the baseline rules (min area, simplification).

Example:
    python scripts/hybrid_instance_partition.py outputs/lapaz_predio_bisa_whu_0.3m outputs/phase3/maskrcnn_0.3m outputs/phase3/hybrid_whu_maskrcnn
"""

import argparse
import json
from pathlib import Path

import geopandas as gpd  # noqa: F401  (polygonize returns a GeoDataFrame)
import numpy as np
import rasterio
from scipy import ndimage as ndi

from postprocess_instances import FOUR, _grow, polygonize
from run_building_inference import write_geotiff


def partition(mask: np.ndarray, inst: np.ndarray, px: float, min_seed_m2: float = 10.0) -> np.ndarray:
    """Return an int32 instance raster covering exactly `mask`.
    `inst` is the candidate instance raster on the same grid (0 = none; pixel-wise best instance)."""
    mask = mask.astype(bool)
    seeds = np.where(mask, inst, 0).astype(np.int64)
    ids, counts = np.unique(seeds[seeds > 0], return_counts=True)
    small = ids[counts * px * px < min_seed_m2]
    seeds[np.isin(seeds, small)] = 0
    comps, _ = ndi.label(mask, structure=FOUR)
    out = np.zeros(mask.shape, np.int32)
    nid = 1
    for ci, sl in enumerate(ndi.find_objects(comps), 1):
        comp = comps[sl] == ci
        s = np.where(comp, seeds[sl], 0)
        u = np.unique(s[s > 0])
        if len(u) <= 1:
            out[sl][comp] = nid
            nid += 1
            continue
        # relabel seeds 1..k deterministically, then grow inside the component
        lut = np.zeros(int(s.max()) + 1, np.int32)
        lut[u] = np.arange(1, len(u) + 1)
        grown = _grow(lut[s], comp)
        # a seed may be split by its own instance being disconnected inside the component: keep as one label
        for k in range(1, len(u) + 1):
            sel = comp & (grown == k)
            if sel.any():
                out[sl][sel] = nid
                nid += 1
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("whu_dir", type=Path)
    ap.add_argument("instance_dir", type=Path)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--min-seed", type=float, default=10.0)
    args = ap.parse_args()
    if args.out_dir.exists() and any(args.out_dir.iterdir()):
        raise SystemExit(f"{args.out_dir} is not empty - refusing to overwrite")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    base = json.loads((args.whu_dir / "run_summary.json").read_text())
    with rasterio.open(args.whu_dir / "mask.tif") as s:
        mask, tf, crs = s.read(1).astype(bool), s.transform, s.crs
    with rasterio.open(args.whu_dir / "probability.tif") as s:
        prob = s.read(1)
    with rasterio.open(args.instance_dir / "instances.tif") as s:
        assert s.transform == tf and s.crs == crs and s.shape == mask.shape, "instance raster must share the WHU grid"
        inst = s.read(1)
    px = abs(tf.a)
    out = partition(mask, inst, px, args.min_seed)
    assert np.array_equal(out > 0, mask), "building pixels changed"
    gdf = polygonize(out, prob, tf, crs, base["min_area_m2"], base["simplify_m"])
    write_geotiff(args.out_dir / "mask.tif", mask, tf, crs, "uint8")
    write_geotiff(args.out_dir / "instances.tif", out, tf, crs, "int32")
    gdf.to_file(args.out_dir / "buildings.geojson", driver="GeoJSON")
    gdf.to_crs(4326).to_file(args.out_dir / "buildings_wgs84.geojson", driver="GeoJSON")
    summary = {**{k: base[k] for k in ("image", "crs", "source_bounds", "native_res_m", "inference_res_m",
                                       "threshold", "min_area_m2", "simplify_m")},
               "method": "D: WHU mask partitioned by Mask R-CNN instances (PROTOCOL.md amendment 1)",
               "whu_dir": str(args.whu_dir), "instance_dir": str(args.instance_dir), "min_seed_m2": args.min_seed,
               "whu_components": int(ndi.label(mask, structure=FOUR)[1]), "instances": int(len(np.unique(out[out > 0]))),
               "feature_count": len(gdf), "baseline_feature_count": base["feature_count"]}
    (args.out_dir / "run_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"{summary['whu_components']} WHU components -> {summary['instances']} instances -> {len(gdf)} polygons "
          f"(WHU {base['feature_count']})")


if __name__ == "__main__":
    main()
