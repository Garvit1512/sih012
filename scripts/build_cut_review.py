"""Build a review inventory + image panels for every proposed RGB-guided cut.

Reads (never modifies) the RGB-guided run (instances.tif, cut_lines.geojson, buildings.geojson),
the WHU baseline run (buildings.geojson) and the orthophoto. Writes into --out-dir:
  cut_review.geojson   one LineString per cut, status="pending" (never auto-approved)
  cut_inventory.csv    same attributes without geometry
  cut_panels/<id>.png  original | baseline polygon | proposed cut | resulting parts (native 0.05 m)
  cut_sheets/sheet_NN.png  6 panels per sheet for quick browsing

Every cut is verified: its two instance ids must exist, share a boundary in instances.tif and lie in
the same baseline connected component; otherwise the script stops instead of guessing.

Example:
    python scripts/build_cut_review.py outputs/rgb_guided_v1 outputs/lapaz_predio_bisa_whu_0.3m outputs/rgb_guided_reviewed
"""

import argparse
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from PIL import Image, ImageDraw
from rasterio.features import shapes
from rasterio.windows import from_bounds
from scipy import ndimage as ndi
from shapely.geometry import box, shape
from shapely.ops import unary_union

from postprocess_instances import FOUR, _boundaries

ROOT = Path(__file__).resolve().parents[1]
PANEL = 420  # px per sub-panel


def line_parts(g):
    if g.geom_type in ("LineString", "LinearRing"):
        return [g]
    return [p for x in getattr(g, "geoms", []) for p in line_parts(x)]


def window_index():
    out = []
    for path, kind in ((ROOT / "data" / "validation" / "windows.geojson", "design"),
                       (ROOT / "data" / "validation_independent" / "windows.geojson", "independent")):
        if path.exists():
            for _, r in gpd.read_file(path).iterrows():
                out.append((r.window_id, kind, r.geometry))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rgb_dir", type=Path)
    ap.add_argument("baseline_dir", type=Path)
    ap.add_argument("out_dir", type=Path)
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "cut_panels").mkdir(exist_ok=True)
    (args.out_dir / "cut_sheets").mkdir(exist_ok=True)
    if (args.out_dir / "cut_review_reviewed.geojson").exists():
        raise SystemExit("cut_review_reviewed.geojson exists - refusing to rebuild the pending inventory")

    summary = json.loads((args.rgb_dir / "run_summary.json").read_text())
    cuts = gpd.read_file(args.rgb_dir / "cut_lines.geojson")
    base = gpd.read_file(args.baseline_dir / "buildings.geojson")
    with rasterio.open(args.rgb_dir / "instances.tif") as s:
        inst, tf, crs = s.read(1), s.transform, s.crs
    comps, _ = ndi.label(inst > 0, structure=FOUR)
    adj = _boundaries(inst)
    part_poly = {}
    for geom, val in shapes(inst, mask=inst > 0, transform=tf, connectivity=4):
        part_poly.setdefault(int(val), []).append(shape(geom))
    part_poly = {k: unary_union(v) for k, v in part_poly.items()}

    # stable ids: sorted by (component, smaller part, larger part)
    cuts["pa"] = cuts[["a", "b"]].min(axis=1).astype(int)
    cuts["pb"] = cuts[["a", "b"]].max(axis=1).astype(int)
    cuts = cuts.sort_values(["component", "pa", "pb"]).reset_index(drop=True)
    cuts["cut_id"] = [f"C{i + 1:03d}" for i in range(len(cuts))]
    assert len(cuts) == summary["proposed_cuts"]

    wins = window_index()
    rows = []
    with rasterio.open(ROOT / summary["image"]) as src:
        to_wgs = gpd.GeoSeries([], crs=crs)
        for _, c in cuts.iterrows():
            a, b = c.pa, c.pb
            # --- verification: never guess the association
            assert a in part_poly and b in part_poly, (c.cut_id, a, b)
            assert (a, b) in adj, f"{c.cut_id}: parts {a},{b} do not share a boundary"
            ca, cb = np.unique(comps[inst == a]), np.unique(comps[inst == b])
            assert len(ca) == 1 and ca.tolist() == cb.tolist(), f"{c.cut_id}: parts in different components"
            pair = unary_union([part_poly[a], part_poly[b]])
            ov = base.geometry.intersection(pair).area
            bidx = int(ov.idxmax()) if ov.max() > 0 else None
            lines = line_parts(c.geometry)
            mid = unary_union(lines).interpolate(0.5, normalized=True) if len(lines) == 1 else \
                unary_union(lines).representative_point()
            row, col = src.index(mid.x, mid.y)
            lon, lat = gpd.GeoSeries([mid], crs=crs).to_crs(4326).iloc[0].coords[0]
            inwin = [f"{wid} ({kind})" for wid, kind, g in wins if g.intersects(c.geometry)]

            # --- panel at native resolution around both parts
            bx = pair.buffer(4).bounds
            side = max(bx[2] - bx[0], bx[3] - bx[1])
            cx, cy = (bx[0] + bx[2]) / 2, (bx[1] + bx[3]) / 2
            pb = (cx - side / 2, cy - side / 2, cx + side / 2, cy + side / 2)
            win = from_bounds(*pb, transform=src.transform)
            rgb = np.moveaxis(src.read([1, 2, 3], window=win, boundless=True, fill_value=0), 0, -1)
            wt = src.window_transform(win)
            img = Image.fromarray(rgb).resize((PANEL, PANEL), Image.LANCZOS)
            f = PANEL / rgb.shape[1]
            inv = ~wt

            def pts(geom):
                return [tuple(v * f for v in inv * xy) for xy in geom.exterior.coords]

            def layer(draw_fn):
                im = img.convert("RGBA")
                L = Image.new("RGBA", im.size, (0, 0, 0, 0))
                draw_fn(ImageDraw.Draw(L))
                return Image.alpha_composite(im, L).convert("RGB")

            def draw_base(d):
                if bidx is not None:
                    for p in getattr(base.geometry[bidx], "geoms", [base.geometry[bidx]]):
                        d.polygon(pts(p), outline=(0, 255, 255, 255), width=3)

            def draw_cut(d):
                draw_base(d)
                col_ = (255, 0, 0, 255) if c.confidence == "high" else (255, 150, 0, 255)
                for ln in lines:
                    d.line([tuple(v * f for v in inv * xy) for xy in ln.coords], fill=col_, width=6)

            def draw_parts(d):
                for p, colr in ((part_poly[a], (255, 0, 255)), (part_poly[b], (0, 220, 0))):
                    for q in getattr(p, "geoms", [p]):
                        d.polygon(pts(q), fill=colr + (60,), outline=colr + (255,), width=3)

            panels = [("original", img), ("baseline polygon", layer(draw_base)),
                      (f"proposed cut ({c.confidence})", layer(draw_cut)), ("resulting parts A|B", layer(draw_parts))]
            out = Image.new("RGB", (4 * (PANEL + 6), PANEL + 44), "white")
            d = ImageDraw.Draw(out)
            d.text((4, 3), f"{c.cut_id}  conf={c.confidence}  contrast={c.contrast:.3f}  edge_support={c.edge_support:.2f}  "
                           f"cut={c.boundary_m:.1f} m  partA={part_poly[a].area:.0f} m2  partB={part_poly[b].area:.0f} m2  "
                           f"view {side:.0f} m  {'; '.join(inwin) or 'outside validation windows'}", fill="black")
            for i, (name, p) in enumerate(panels):
                out.paste(p, (i * (PANEL + 6), 40))
                d.text((i * (PANEL + 6) + 4, 24), name, fill="black")
            panel_path = args.out_dir / "cut_panels" / f"{c.cut_id}.png"
            out.save(panel_path)
            rows.append({
                "cut_id": c.cut_id, "component": int(c.component), "part_a": int(a), "part_b": int(b),
                "part_a_m2": round(part_poly[a].area, 1), "part_b_m2": round(part_poly[b].area, 1),
                "baseline_polygon_id": None if bidx is None else int(base.id[bidx]),
                "confidence": c.confidence, "contrast": float(c.contrast), "edge_support": float(c.edge_support),
                "boundary_m": float(c.boundary_m), "mid_x": round(mid.x, 2), "mid_y": round(mid.y, 2),
                "ortho_row": int(row), "ortho_col": int(col), "lon": round(lon, 7), "lat": round(lat, 7),
                "windows": "; ".join(inwin), "panel": str(panel_path.resolve().relative_to(ROOT)).replace("\\", "/"),
                "status": "pending", "reviewer": "", "review_date": "", "reason": "",
                "geometry": c.geometry,
            })

    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs=crs)
    (args.out_dir / "cut_review.geojson").unlink(missing_ok=True)
    gdf.to_file(args.out_dir / "cut_review.geojson", driver="GeoJSON")
    gdf.drop(columns="geometry").to_csv(args.out_dir / "cut_inventory.csv", index=False)
    for k in range(0, len(rows), 6):
        ims = [Image.open(ROOT / r["panel"]) for r in rows[k:k + 6]]
        sheet = Image.new("RGB", (ims[0].width, sum(i.height for i in ims)), "white")
        y = 0
        for im in ims:
            sheet.paste(im, (0, y))
            y += im.height
        sheet.save(args.out_dir / "cut_sheets" / f"sheet_{k // 6 + 1:02d}.png")
    print(f"{len(gdf)} cuts verified and inventoried; {gdf.windows.ne('').sum()} touch a validation window; "
          f"panels in {args.out_dir / 'cut_panels'}")


if __name__ == "__main__":
    main()
