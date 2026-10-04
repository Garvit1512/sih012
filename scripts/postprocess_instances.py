"""Conservative building-instance separation for an existing WHU run (post-processing only).

Reads a finished run directory (probability.tif, mask.tif, run_summary.json) without
modifying it, and writes a new directory with per-building polygons.

Method (all on the unchanged baseline mask = probability >= threshold):
  1. 4-connected components of the mask (same connectivity as the baseline vectorizer).
  2. Per component: Euclidean distance transform (metres). Markers = connected cores
     left after removing everything closer than --marker-radius to the mask edge.
     A component with 0-1 markers is left untouched.
  3. Marker-controlled geodesic region growing (a watershed on distance-to-markers): cores
     expand pixel by pixel strictly inside the component until every mask pixel is
     assigned, so the building/non-building pixels do not change. (scipy's watershed_ift
     was rejected: in testing it flooded across pixels outside the component.)
  4. Conservative merge-back: two adjacent parts stay separate only if their shared
     boundary is short (<= --neck-ratio x the smaller part's equivalent diameter, i.e. a
     real constriction) and both parts are >= --min-part m^2. Otherwise they are merged.
     Touching roofs with no constriction therefore stay as one polygon.
  5. Polygonize each instance with the source CRS/transform, simplify (same tolerance as
     the baseline), drop polygons < --min-area m^2, attach mean probability.

Outputs are detected building footprints, not cadastral parcel boundaries.

Example:
    python scripts/postprocess_instances.py outputs/lapaz_predio_bisa_whu_0.3m outputs/whu_postprocessed_v1
"""

import argparse
import hashlib
import json
import time
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.features import shapes
from scipy import ndimage as ndi
from shapely.geometry import shape
from shapely.ops import unary_union

from run_building_inference import read_raster, save_previews, write_geotiff

FOUR = ndi.generate_binary_structure(2, 1)


def _boundaries(lab: np.ndarray) -> dict:
    """Shared 4-neighbour boundary length (in pixel edges) between every pair of labels > 0."""
    out = {}
    for a, b in ((lab[:, :-1], lab[:, 1:]), (lab[:-1, :], lab[1:, :])):
        sel = (a != b) & (a > 0) & (b > 0)
        pairs = np.sort(np.stack([a[sel], b[sel]], 1), axis=1)
        for (p, q), n in zip(*np.unique(pairs, axis=0, return_counts=True)) if len(pairs) else []:
            out[(int(p), int(q))] = out.get((int(p), int(q)), 0) + int(n)
    return out


def _merge_back(lab: np.ndarray, px: float, min_part_m2: float, neck_ratio: float) -> np.ndarray:
    """Merge adjacent parts until every remaining shared boundary is a genuine constriction."""
    while True:
        ids, counts = np.unique(lab[lab > 0], return_counts=True)
        if len(ids) <= 1:
            return lab
        area = dict(zip(ids.tolist(), (counts * px * px).tolist()))
        eqd = {i: 2 * np.sqrt(a / np.pi) for i, a in area.items()}
        bounds = _boundaries(lab)
        worst = None  # (badness, a, b)
        for (a, b), n in bounds.items():
            length = n * px
            limit = neck_ratio * min(eqd[a], eqd[b])
            too_small = min(area[a], area[b]) < min_part_m2
            if too_small or length > limit:
                badness = (1 if too_small else 0, length / limit)
                if worst is None or badness > worst[0]:
                    worst = (badness, a, b)
        if worst is None:
            return lab
        _, a, b = worst
        lab[lab == b] = a


def _grow(labels: np.ndarray, region: np.ndarray) -> np.ndarray:
    """Geodesic region growing: labelled cores expand by 4-connected steps, strictly inside
    `region`, until every region pixel is claimed (watershed on distance-to-cores). Pixels
    reached by two cores in the same step take the larger label (deterministic tie-break)."""
    labels = np.where(region, labels, 0)
    while True:
        grown = ndi.grey_dilation(labels, footprint=FOUR)
        new = region & (labels == 0) & (grown > 0)
        if not new.any():
            return labels
        labels[new] = grown[new]


def separate_instances(mask: np.ndarray, px: float, marker_radius_m: float = 1.2,
                       min_part_m2: float = 10.0, neck_ratio: float = 0.5) -> np.ndarray:
    """Return an int32 instance raster (0 = background) covering exactly the pixels of `mask`."""
    mask = mask.astype(bool)
    comps, n = ndi.label(mask, structure=FOUR)
    out = np.zeros(mask.shape, np.int32)
    next_id = 1
    for ci, sl in enumerate(ndi.find_objects(comps), 1):
        sl = tuple(slice(max(s.start - 1, 0), s.stop + 1) for s in sl)
        comp = comps[sl] == ci
        dist = ndi.distance_transform_edt(comp) * px
        markers, k = ndi.label(dist > marker_radius_m, structure=FOUR)
        if k <= 1:
            out[sl][comp] = next_id
            next_id += 1
            continue
        ws = _grow(markers.astype(np.int32), comp)
        ws = _merge_back(ws, px, min_part_m2, neck_ratio)
        for part in np.unique(ws[ws > 0]):
            out[sl][ws == part] = next_id
            next_id += 1
    return out


def polygonize(instances, prob, transform, crs, min_area, simplify):
    rows = []
    geoms = {}
    for geom, val in shapes(instances, mask=instances > 0, transform=transform, connectivity=4):
        geoms.setdefault(int(val), []).append(shape(geom))
    for val, parts in geoms.items():
        poly = unary_union(parts)
        if simplify > 0:
            poly = poly.simplify(simplify, preserve_topology=True)
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty or poly.area < min_area:
            continue
        sel = instances == val
        rows.append({"instance": val, "mean_prob": round(float(prob[sel].mean()), 3),
                     "area_m2": round(poly.area, 1), "geometry": poly})
    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs=crs) if rows else \
        gpd.GeoDataFrame({"instance": [], "mean_prob": [], "area_m2": []}, geometry=[], crs=crs)
    gdf.insert(0, "id", np.arange(1, len(gdf) + 1))
    return gdf


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("baseline_dir", type=Path)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--marker-radius", type=float, default=1.2, help="m; cores closer than this to an edge are removed")
    ap.add_argument("--neck-ratio", type=float, default=0.5, help="max shared boundary / smaller part eq. diameter")
    ap.add_argument("--min-part", type=float, default=10.0, help="m^2; smaller watershed parts are merged back")
    args = ap.parse_args()

    t0 = time.time()
    base = json.loads((args.baseline_dir / "run_summary.json").read_text())
    if args.out_dir.resolve() == args.baseline_dir.resolve():
        raise SystemExit("out_dir must differ from baseline_dir (baseline is read-only)")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    inputs = {p: sha256(args.baseline_dir / p) for p in ("probability.tif", "mask.tif", "buildings.geojson", "run_summary.json")}

    with rasterio.open(args.baseline_dir / "probability.tif") as src:
        prob, transform, crs = src.read(1), src.transform, src.crs
    with rasterio.open(args.baseline_dir / "mask.tif") as src:
        base_mask = src.read(1).astype(bool)
    mask = prob >= base["threshold"]
    assert np.array_equal(mask, base_mask), "re-thresholded probability differs from baseline mask.tif"
    px = abs(transform.a)

    instances = separate_instances(mask, px, args.marker_radius, args.min_part, args.neck_ratio)
    assert np.array_equal(instances > 0, mask), "instance raster must cover exactly the baseline mask"
    n_comp = ndi.label(mask, structure=FOUR)[1]
    gdf = polygonize(instances, prob, transform, crs, base["min_area_m2"], base["simplify_m"])

    write_geotiff(args.out_dir / "mask.tif", mask, transform, crs, "uint8")
    write_geotiff(args.out_dir / "instances.tif", instances, transform, crs, "int32")
    for p in ("buildings.geojson", "buildings_wgs84.geojson"):
        (args.out_dir / p).unlink(missing_ok=True)
    gdf.to_file(args.out_dir / "buildings.geojson", driver="GeoJSON")
    gdf.to_crs(4326).to_file(args.out_dir / "buildings_wgs84.geojson", driver="GeoJSON")
    rgb, _, rgb_tf, _ = read_raster(Path(base["image"]), base["inference_res_m"])
    assert rgb_tf.almost_equals(transform), "imagery grid differs from probability grid"
    save_previews(args.out_dir, rgb, mask, gdf, transform)

    pairs = gpd.sjoin(gdf[["id", "geometry"]], gdf[["id", "geometry"]], predicate="intersects")
    pairs = pairs[pairs.id_left < pairs.id_right]
    overlap_m2 = sum(gdf.geometry.iloc[i].intersection(gdf.geometry.iloc[j]).area
                     for i, j in zip(pairs.index, pairs.index_right))
    summary = {
        **{k: base[k] for k in ("image", "crs", "source_bounds", "native_res_m", "inference_res_m", "threshold",
                                "min_area_m2", "simplify_m")},
        "model": base.get("model"), "hf_revision": base.get("hf_revision"),
        "postprocess": "distance-transform cores + geodesic region growing + constriction merge-back (v1)",
        "baseline_dir": str(args.baseline_dir), "baseline_input_sha256": inputs,
        "marker_radius_m": args.marker_radius, "neck_ratio": args.neck_ratio, "min_part_m2": args.min_part,
        "connected_components": int(n_comp), "instances_raster": int(instances.max()),
        "feature_count": len(gdf), "baseline_feature_count": base["feature_count"],
        "polygon_overlap_m2_total": round(float(overlap_m2), 3),
        "runtime_s": round(time.time() - t0, 1),
    }
    (args.out_dir / "run_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"components {n_comp} -> instances {instances.max()} -> polygons {len(gdf)} "
          f"(baseline {base['feature_count']}); overlap {summary['polygon_overlap_m2_total']} m^2; "
          f"{summary['runtime_s']}s -> {args.out_dir}")


if __name__ == "__main__":
    main()
