"""Build the independent (V1-V4) provisional reference labels + review sheet.

Inputs  (data/validation_independent/): windows.json, draft_pixel_polygons.json
Outputs (data/validation_independent/): windows.geojson, provisional_labels.geojson, review/<window>.png,
contact_sheet.png

Same conventions as build_provisional_labels.py (whose W1-W4 files are not touched).
Refuses to overwrite provisional_labels.geojson once a reviewed_labels.geojson exists.

Example:
    python scripts/build_independent_labels.py
"""

import argparse
import json
from pathlib import Path

import geopandas as gpd
import rasterio
from PIL import Image, ImageDraw
from shapely.geometry import Polygon, box

from build_provisional_labels import draw_outlines, window_crop

ROOT = Path(__file__).resolve().parents[1]
VAL = ROOT / "data" / "validation_independent"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", type=Path, default=ROOT / "data" / "lapaz_predio_bisa.tif")
    args = ap.parse_args()
    if (VAL / "reviewed_labels.geojson").exists():
        raise SystemExit("reviewed_labels.geojson exists - refusing to regenerate provisional labels")

    windows = json.loads((VAL / "windows.json").read_text())
    draft = json.loads((VAL / "draft_pixel_polygons.json").read_text())
    (VAL / "review").mkdir(exist_ok=True)
    rows, wrows, tiles = [], [], []
    with rasterio.open(args.image) as src:
        assert src.crs.to_string() == windows["crs"]
        crs = src.crs
        for w in windows["windows"]:
            rgb, wt = window_crop(src, w["bounds"])
            wbox = box(*w["bounds"])
            wrows.append({"window_id": w["id"], "size_m": windows["size_m"], "geometry": wbox})
            for i, item in enumerate(draft["windows"][w["id"]], 1):
                geom = Polygon([wt * (c, r) for c, r in item["poly"]]).intersection(wbox)
                assert geom.is_valid and geom.geom_type == "Polygon" and not geom.is_empty, (w["id"], i)
                rows.append({
                    "label_id": f"{w['id'].split('_')[0]}-{i:02d}", "window_id": w["id"], "class": "building",
                    "confidence": item["confidence"], "ignore": bool(item.get("ignore", False)),
                    "note": item["note"], "status": "provisional_unreviewed",
                    "annotator": "AI draft (Claude), visual tracing on 0.05 m orthophoto crop, before cut review",
                    "created": draft["created"], "source_image": args.image.name,
                    "area_m2": round(geom.area, 1), "geometry": geom,
                })
            labels = gpd.GeoDataFrame([r for r in rows if r["window_id"] == w["id"]], crs=crs)
            raw = Image.fromarray(rgb)
            pair = Image.new("RGB", (raw.width * 2 + 10, raw.height), "white")
            pair.paste(raw, (0, 0))
            pair.paste(draw_outlines(rgb, labels, wt), (raw.width + 10, 0))
            pair.save(VAL / "review" / f"{w['id']}.png")
            tiles.append((w["id"], len(labels), int(labels.ignore.sum()), pair))

    gdf = gpd.GeoDataFrame(rows, crs=crs)
    for p in ("provisional_labels.geojson", "windows.geojson"):
        (VAL / p).unlink(missing_ok=True)
    gdf.to_file(VAL / "provisional_labels.geojson", driver="GeoJSON")
    gpd.GeoDataFrame(wrows, crs=crs).to_file(VAL / "windows.geojson", driver="GeoJSON")

    scale, header = 0.5, 28
    sheet = Image.new("RGB", (int(tiles[0][3].width * scale), len(tiles) * (400 + header) + 40), "white")
    d = ImageDraw.Draw(sheet)
    for k, (wid, n, ni, pair) in enumerate(tiles):
        y = k * (400 + header)
        d.text((6, y + 8), f"{wid}  ({n} provisional labels, {ni} marked ignore)   left: original   right: draft outlines",
               fill="black")
        sheet.paste(pair.resize((int(pair.width * scale), int(pair.height * scale)), Image.LANCZOS), (0, y + header))
    d.text((6, sheet.height - 30), "INDEPENDENT windows V1-V4. PROVISIONAL, UNREVIEWED AI-drafted outlines - not ground truth, "
           "not cadastral boundaries. yellow=high orange=medium magenta=low/ignored", fill="black")
    sheet.save(VAL / "contact_sheet.png")
    print(f"{len(gdf)} provisional labels ({int(gdf.ignore.sum())} ignore) in {len(wrows)} windows")
    print(gdf.groupby("window_id").agg(n=("label_id", "size"), ignored=("ignore", "sum"), area_m2=("area_m2", "sum")).to_string())


if __name__ == "__main__":
    main()
