"""Download Microsoft Global ML Building Footprints covering an orthophoto (secondary reference).

These are ML-derived footprints from Bing imagery of unknown date (ODbL licence). They
are used only as a secondary comparison, never as ground truth.

Example:
    python scripts/fetch_ms_footprints.py data/lapaz_predio_bisa.tif --region Bolivia
"""

import argparse
import gzip
import io
import json
import math
import urllib.request
from pathlib import Path

import geopandas as gpd
import pandas as pd
import rasterio
from rasterio.warp import transform_bounds
from shapely.geometry import box, shape

ROOT = Path(__file__).resolve().parents[1]
INDEX = "https://minedbuildings.z5.web.core.windows.net/global-buildings/dataset-links.csv"


def quadkey(lat, lon, z=9):
    s = math.sin(math.radians(lat))
    x, y = (lon + 180) / 360, 0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)
    tx, ty = int(x * 2 ** z), int(y * 2 ** z)
    return "".join(str((1 if tx & (1 << (i - 1)) else 0) + (2 if ty & (1 << (i - 1)) else 0)) for i in range(z, 0, -1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", type=Path)
    ap.add_argument("--region", required=True, help="RegionName in dataset-links.csv, e.g. Bolivia")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "reference" / "ms_buildings.geojson")
    args = ap.parse_args()

    with rasterio.open(args.image) as src:
        crs, bounds = src.crs, src.bounds
    w, s, e, n = transform_bounds(crs, "EPSG:4326", *bounds)
    keys = {quadkey(la, lo) for la in (s, n) for lo in (w, e)}
    links = pd.read_csv(INDEX, dtype=str)
    rows = links[(links.Location == args.region) & links.QuadKey.isin(keys)]
    print(f"quadkeys {sorted(keys)} -> {len(rows)} file(s)")
    aoi = box(w, s, e, n)
    feats = []
    for _, r in rows.iterrows():
        print(f"  downloading {r.Url} ({r.Size}, uploaded {r.UploadDate})")
        data = gzip.decompress(urllib.request.urlopen(r.Url, timeout=300).read())
        for line in io.TextIOWrapper(io.BytesIO(data), encoding="utf-8"):
            f = json.loads(line)
            g = shape(f["geometry"])
            if g.intersects(aoi):
                feats.append({**f.get("properties", {}), "ms_file": r.Url.rsplit("/", 1)[-1],
                              "ms_upload_date": r.UploadDate, "geometry": g})
    gdf = gpd.GeoDataFrame(feats, geometry="geometry", crs=4326).to_crs(crs)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.unlink(missing_ok=True)
    gdf.to_file(args.out, driver="GeoJSON")
    print(f"{len(gdf)} Microsoft footprints intersecting the orthophoto -> {args.out}")


if __name__ == "__main__":
    main()
