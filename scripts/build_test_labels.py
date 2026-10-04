"""Build provisional reference labels + review sheets for a label set (default: phase-3 test windows T1-T4).

Inputs  (<val-dir>): windows.json, draft_pixel_polygons.json
Outputs (<val-dir>): windows.geojson (if missing), provisional_labels.geojson, review/<window>.png, contact_sheet.png
Refuses to run once reviewed_labels.geojson exists. Renders imagery and draft outlines only - never predictions.

Example:
    python scripts/build_test_labels.py --val-dir data/validation_test
"""

import argparse
import json
from pathlib import Path

import geopandas as gpd
import rasterio
from PIL import Image, ImageDraw
from rasterio.windows import from_bounds
from shapely.geometry import Polygon, box

from build_provisional_labels import draw_outlines

ROOT = Path(__file__).resolve().parents[1]


def build(val: Path, image: Path):
    if (val / "reviewed_labels.geojson").exists():
        raise SystemExit("reviewed_labels.geojson exists - refusing to regenerate provisional labels")
    windows = json.loads((val / "windows.json").read_text())
    draft = json.loads((val / "draft_pixel_polygons.json").read_text())
    (val / "review").mkdir(exist_ok=True)
    rows, tiles = [], []
    with rasterio.open(image) as src:
        assert src.crs.to_string() == windows["crs"]
        for w in windows["windows"]:
            win = from_bounds(*w["bounds"], transform=src.transform)
            wt = src.window_transform(win)
            rgb = src.read([1, 2, 3], window=win, boundless=True, fill_value=0).transpose(1, 2, 0)
            wbox = box(*w["bounds"])
            for i, item in enumerate(draft["windows"][w["id"]], 1):
                geom = Polygon([wt * (c, r) for c, r in item["poly"]]).intersection(wbox)
                assert geom.is_valid and geom.geom_type == "Polygon" and not geom.is_empty, (w["id"], i)
                rows.append({"label_id": f"{w['id'].split('_')[0]}-{i:02d}", "window_id": w["id"], "class": "building",
                             "confidence": item["confidence"], "ignore": bool(item.get("ignore", False)),
                             "note": item["note"], "status": "provisional_unreviewed",
                             "annotator": "AI draft (Claude), raw 0.05 m crop, no predictions shown",
                             "created": draft["created"], "source_image": image.name,
                             "area_m2": round(geom.area, 1), "geometry": geom})
            labels = gpd.GeoDataFrame([r for r in rows if r["window_id"] == w["id"]], crs=src.crs)
            raw = Image.fromarray(rgb)
            pair = Image.new("RGB", (raw.width * 2 + 10, raw.height), "white")
            pair.paste(raw, (0, 0))
            pair.paste(draw_outlines(rgb, labels, wt), (raw.width + 10, 0))
            pair.save(val / "review" / f"{w['id']}.png")
            tiles.append((w["id"], len(labels), int(labels.ignore.sum()), pair))
        crs = src.crs
    gdf = gpd.GeoDataFrame(rows, crs=crs)
    (val / "provisional_labels.geojson").unlink(missing_ok=True)
    gdf.to_file(val / "provisional_labels.geojson", driver="GeoJSON")
    if not (val / "windows.geojson").exists():
        gpd.GeoDataFrame([{"window_id": w["id"], "geometry": box(*w["bounds"])} for w in windows["windows"]],
                         crs=crs).to_file(val / "windows.geojson", driver="GeoJSON")
    scale, header = 0.5, 28
    sheet = Image.new("RGB", (int(tiles[0][3].width * scale), len(tiles) * (400 + header) + 40), "white")
    d = ImageDraw.Draw(sheet)
    for k, (wid, n, ni, pair) in enumerate(tiles):
        y = k * (400 + header)
        d.text((6, y + 8), f"{wid}  ({n} provisional labels, {ni} ignore)   left: original   right: draft outlines", fill="black")
        sheet.paste(pair.resize((int(pair.width * scale), int(pair.height * scale)), Image.LANCZOS), (0, y + header))
    d.text((6, sheet.height - 30), "HELD-OUT TEST windows. PROVISIONAL, UNREVIEWED AI-drafted outlines - not ground truth, "
           "not cadastral boundaries. yellow=high orange=medium magenta=low/ignored", fill="black")
    sheet.save(val / "contact_sheet.png")
    return gdf


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--val-dir", type=Path, default=ROOT / "data" / "validation_test")
    ap.add_argument("--image", type=Path, default=ROOT / "data" / "lapaz_predio_bisa.tif")
    args = ap.parse_args()
    gdf = build(args.val_dir, args.image)
    print(f"{len(gdf)} provisional labels ({int(gdf.ignore.sum())} ignore)")
    print(gdf.groupby("window_id").agg(n=("label_id", "size"), ignored=("ignore", "sum"), area_m2=("area_m2", "sum")).to_string())


if __name__ == "__main__":
    main()
