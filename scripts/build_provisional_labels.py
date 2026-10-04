"""Build provisional validation labels + review contact sheet from the pixel-space draft.

Inputs  (data/validation/): windows.json, draft_pixel_polygons.json
Outputs (data/validation/): windows.geojson, provisional_labels.geojson (EPSG of the
orthophoto), review/<window>.png, contact_sheet.png

The labels are an UNREVIEWED draft (status=provisional_unreviewed). After a human
corrects provisional_labels.geojson (e.g. in QGIS) they should set status=approved on
every feature; scripts/score_models.py refuses to score unapproved labels.

Re-running this script overwrites provisional_labels.geojson, so do not re-run it
after manual corrections have started.
"""

import argparse
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from PIL import Image, ImageDraw
from rasterio.windows import from_bounds
from shapely.geometry import Polygon, box

ROOT = Path(__file__).resolve().parents[1]
VAL = ROOT / "data" / "validation"
COLORS = {"high": (255, 255, 0), "medium": (255, 140, 0), "low": (255, 0, 255)}


def window_crop(src, bounds):
    win = from_bounds(*bounds, transform=src.transform)
    rgb = np.moveaxis(src.read([1, 2, 3], window=win), 0, -1)
    return rgb, src.window_transform(win)


def draw_outlines(rgb, labels, transform, ids=True):
    im = Image.fromarray(rgb).convert("RGBA")
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    inv = ~transform
    for _, r in labels.iterrows():
        pts = [inv * xy for xy in r.geometry.exterior.coords]
        c = COLORS[r.confidence]
        d.polygon(pts, fill=c + (45,), outline=c + (255,), width=4)
        if ids:
            x, y = inv * r.geometry.representative_point().coords[0]
            d.rectangle([x - 4, y - 6, x + 32, y + 10], fill=(0, 0, 0, 180))
            d.text((x, y - 5), str(r.label_id), fill=(255, 255, 255, 255))
    return Image.alpha_composite(im, layer).convert("RGB")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", type=Path, default=ROOT / "data" / "lapaz_predio_bisa.tif")
    args = ap.parse_args()

    windows = json.loads((VAL / "windows.json").read_text())
    draft = json.loads((VAL / "draft_pixel_polygons.json").read_text())
    (VAL / "review").mkdir(exist_ok=True)

    with rasterio.open(args.image) as src:
        assert src.crs.to_string() == windows["crs"], (src.crs, windows["crs"])
        crs, res = src.crs, abs(src.res[0])
        rows, wrows, tiles = [], [], []
        for w in windows["windows"]:
            rgb, wt = window_crop(src, w["bounds"])
            wbox = box(*w["bounds"])
            wrows.append({"window_id": w["id"], "size_m": windows["size_m"], "geometry": wbox})
            for i, item in enumerate(draft["windows"][w["id"]], 1):
                geom = Polygon([wt * (c, r) for c, r in item["poly"]]).intersection(wbox)
                assert geom.is_valid and geom.geom_type == "Polygon" and not geom.is_empty, (w["id"], i)
                rows.append({
                    "label_id": f"{w['id'].split('_')[0]}-{i:02d}", "window_id": w["id"], "class": "building",
                    "confidence": item["confidence"], "note": item["note"],
                    "status": "provisional_unreviewed",
                    "annotator": "AI draft (Claude), visual tracing on 0.05 m orthophoto crop",
                    "created": draft["created"], "source_image": args.image.name,
                    "area_m2": round(geom.area, 1), "geometry": geom,
                })
            labels = gpd.GeoDataFrame([r for r in rows if r["window_id"] == w["id"]], crs=crs)
            raw = Image.fromarray(rgb)
            out = draw_outlines(rgb, labels, wt)
            pair = Image.new("RGB", (raw.width * 2 + 10, raw.height), "white")
            pair.paste(raw, (0, 0))
            pair.paste(out, (raw.width + 10, 0))
            pair.save(VAL / "review" / f"{w['id']}.png")
            tiles.append((w["id"], len(labels), pair))

    gdf = gpd.GeoDataFrame(rows, crs=crs)
    wins = gpd.GeoDataFrame(wrows, crs=crs)
    assert gdf.within(wins.union_all().buffer(1e-6)).all()
    for p in ("provisional_labels.geojson", "windows.geojson"):
        (VAL / p).unlink(missing_ok=True)
    gdf.to_file(VAL / "provisional_labels.geojson", driver="GeoJSON")
    wins.to_file(VAL / "windows.geojson", driver="GeoJSON")

    # Contact sheet: one row per window (raw | outlined), downscaled to 400 px per panel.
    scale, header = 0.5, 28
    pw = tiles[0][2].width * scale
    sheet = Image.new("RGB", (int(pw), int(len(tiles) * (400 + header)) + 40), "white")
    d = ImageDraw.Draw(sheet)
    for k, (wid, n, pair) in enumerate(tiles):
        y = k * (400 + header)
        d.text((6, y + 8), f"{wid}  ({n} provisional labels, 40 x 40 m, {res:g} m/px)   left: original   right: draft outlines",
               fill="black")
        sheet.paste(pair.resize((int(pair.width * scale), int(pair.height * scale)), Image.LANCZOS), (0, y + header))
    d.text((6, sheet.height - 30), "PROVISIONAL, UNREVIEWED AI-drafted building outlines - not ground truth, not "
           "cadastral boundaries.  yellow=high  orange=medium  magenta=low confidence", fill="black")
    sheet.save(VAL / "contact_sheet.png")

    print(f"{len(gdf)} provisional labels in {len(wins)} windows -> {VAL / 'provisional_labels.geojson'}")
    print(gdf.groupby("window_id").agg(n=("label_id", "size"), area_m2=("area_m2", "sum")).to_string())
    print(gdf.confidence.value_counts().to_string())


if __name__ == "__main__":
    main()
