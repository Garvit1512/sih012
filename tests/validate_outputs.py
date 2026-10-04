"""Validate outputs of scripts/run_building_inference.py against the source raster.

Usage:
    python tests/validate_outputs.py outputs/lapaz_predio_bisa_0.4m [more output dirs...]

Checks: CRS, bounds containment, geometry validity, feature count, mask/vector
round-trip agreement, WGS84 export consistency and that polygons land on valid
(non-nodata) source pixels. Exits non-zero on any failure.
"""

import json
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.features import rasterize
from shapely.geometry import box


def validate(out_dir: Path) -> list[str]:
    failures = []

    def check(ok, msg):
        print(f"  [{'PASS' if ok else 'FAIL'}] {msg}")
        if not ok:
            failures.append(f"{out_dir.name}: {msg}")

    summary = json.loads((out_dir / "run_summary.json").read_text())
    src_path = Path(summary["image"])
    gdf = gpd.read_file(out_dir / "buildings.geojson")
    wgs = gpd.read_file(out_dir / "buildings_wgs84.geojson")

    with rasterio.open(src_path) as src, rasterio.open(out_dir / "mask.tif") as m:
        src_crs, src_bounds, src_res = src.crs, src.bounds, src.res
        mask, mtf = m.read(1).astype(bool), m.transform
        check(m.crs == src_crs, f"mask.tif CRS {m.crs} == source CRS {src_crs}")
        tol = abs(mtf.a)  # one inference pixel
        diff = np.abs(np.array(m.bounds) - np.array(src_bounds)).max()
        check(diff <= tol, f"mask.tif bounds match source within 1 inference px (max diff {diff:.4f} m)")

        check(gdf.crs == src_crs, f"buildings.geojson CRS {gdf.crs.to_string() if gdf.crs else None} == source")
        check(wgs.crs.to_epsg() == 4326, "buildings_wgs84.geojson is EPSG:4326")
        check(len(gdf) == summary["feature_count"] == len(wgs),
              f"feature count consistent: {len(gdf)} (summary {summary['feature_count']}, wgs84 {len(wgs)})")
        if len(gdf) == 0:
            return failures

        check(gdf.geometry.is_valid.all() and not gdf.geometry.is_empty.any(), "all geometries valid and non-empty")
        check(set(gdf.geom_type) <= {"Polygon", "MultiPolygon"}, f"geometry types {sorted(set(gdf.geom_type))}")
        check(gdf.within(box(*src_bounds).buffer(1e-6)).all(), "all polygons inside source raster bounds")
        check((gdf.area >= summary["min_area_m2"] - 1e-6).all(),
              f"all polygons >= min area {summary['min_area_m2']} m^2 (min {gdf.area.min():.1f})")

        # Vector -> raster round trip on the inference grid: polygons should reproduce the mask
        # (differences only from simplification and dropped < min_area blobs).
        burned = rasterize(gdf.geometry, out_shape=mask.shape, transform=mtf, dtype="uint8").astype(bool)
        inter, union = (burned & mask).sum(), (burned | mask).sum()
        iou = inter / union if union else 1.0
        check(iou >= 0.85, f"vector/mask round-trip IoU {iou:.3f} (>= 0.85)")

        # WGS84 export must reproject back onto the source-CRS polygons.
        back = wgs.to_crs(src_crs)
        shift = back.centroid.distance(gdf.centroid).max()
        check(shift < 0.01, f"WGS84 file reprojects back to source CRS (max centroid shift {shift:.2e} m)")

        # Each polygon's representative point should fall on valid (non-nodata) source pixels.
        rows, cols = zip(*(src.index(p.x, p.y) for p in gdf.representative_point()))
        valid = src.dataset_mask()
        on_valid = valid[np.array(rows), np.array(cols)] > 0
        check(on_valid.all(), f"polygon interior points on valid source pixels: {on_valid.sum()}/{len(gdf)}")

    lon, lat = wgs.union_all().centroid.coords[0]
    print(f"  info: {len(gdf)} polygons, total {gdf.area.sum():.0f} m^2, "
          f"median {gdf.area.median():.0f} m^2, centroid lon/lat {lon:.5f}, {lat:.5f}")
    return failures


def main():
    dirs = [Path(p) for p in sys.argv[1:]] or sorted(Path("outputs").glob("*/"))
    failures = []
    for d in dirs:
        print(f"== {d}")
        failures += validate(d)
    print(f"\n{'ALL CHECKS PASSED' if not failures else f'{len(failures)} FAILURE(S)'}")
    for f in failures:
        print("  -", f)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
