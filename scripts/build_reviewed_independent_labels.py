"""Build approved V1-V4 reference labels from the provisional draft + per-window review decisions.

Inputs (data/validation_independent/, read-only): windows.json, draft_pixel_polygons.json,
provisional_labels.geojson, review_decisions.json.
Output: reviewed_labels.geojson (new file; refuses to overwrite). The provisional file is never modified.

Decision mapping:
  confirm      -> building label, geometry = provisional geometry
  edit         -> building label, geometry = edited pixel polygon from review_decisions.json
  uncertain    -> ignore=true (building separation not established by imagery)
  keep_ignore  -> ignore=true (provisional ignore retained)
  added_ignore_regions -> extra ignore=true features
Precedence rule (if "confirmed_labels_take_precedence" is true): every ignore geometry is clipped to
exclude the union of confirmed/edited building labels in its window.

Example:
    python scripts/build_reviewed_independent_labels.py
"""

import json
from pathlib import Path

import geopandas as gpd
import rasterio
from rasterio.windows import from_bounds
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[1]
VAL = ROOT / "data" / "validation_independent"


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--val-dir", type=Path, default=VAL, help="label set directory (default: V1-V4)")
    VAL_ = ap.parse_args().val_dir
    build(VAL_)


def build(VAL):
    out = VAL / "reviewed_labels.geojson"
    if out.exists():
        raise SystemExit(f"{out} exists - refusing to overwrite approved labels")
    windows = {w["id"]: w for w in json.loads((VAL / "windows.json").read_text())["windows"]}
    draft = json.loads((VAL / "draft_pixel_polygons.json").read_text())["windows"]
    rev = json.loads((VAL / "review_decisions.json").read_text())
    prov = gpd.read_file(VAL / "provisional_labels.geojson")
    assert (prov.status == "provisional_unreviewed").all()
    assert set(rev["windows"]) == set(windows), "every window must have review decisions"
    precedence = bool(rev.get("confirmed_labels_take_precedence", False))

    rows = []
    with rasterio.open(ROOT / "data" / "lapaz_predio_bisa.tif") as src:
        crs = src.crs
        for wid, w in windows.items():
            d = rev["windows"][wid]
            wt = src.window_transform(from_bounds(*w["bounds"], transform=src.transform))
            wbox = box(*w["bounds"])
            to_geo = lambda poly: Polygon([wt * (c, r) for c, r in poly]).intersection(wbox)  # noqa: E731
            pw = prov[prov.window_id == wid].set_index("label_id")
            assert set(d["decisions"]) == set(pw.index), f"{wid}: decisions must cover every provisional label"
            base = {"window_id": wid, "class": "building", "status": "approved", "reviewer": rev["reviewer"],
                    "review_date": rev["review_date"], "source_image": "lapaz_predio_bisa.tif"}
            buildings, ignores = [], []
            for lid, dec in d["decisions"].items():
                p = pw.loc[lid]
                rec = {**base, "label_id": lid, "review_decision": dec, "confidence": p.confidence, "draft_confidence": p.confidence,
                       "note": p.note}
                if dec == "confirm":
                    buildings.append({**rec, "ignore": False, "geometry": p.geometry})
                elif dec == "edit":
                    e = d["edits"][lid]
                    buildings.append({**rec, "ignore": False, "note": f"{p.note} | EDIT: {e['reason']}",
                                      "geometry": to_geo(e["poly"])})
                elif dec in ("uncertain", "keep_ignore"):
                    ignores.append({**rec, "ignore": True, "geometry": p.geometry})
                else:
                    raise SystemExit(f"{wid} {lid}: unknown decision {dec}")
            for k, r in enumerate(d.get("added_ignore_regions", []), 1):
                ignores.append({**base, "label_id": f"{wid.split('_')[0]}-IR{k}", "review_decision": "added_ignore_region",
                                "confidence": "n/a", "draft_confidence": "n/a", "note": r["note"], "ignore": True,
                                "geometry": to_geo(r["poly"])})
            if precedence and buildings:
                keep = unary_union([b["geometry"] for b in buildings])
                for g in ignores:
                    before = g["geometry"].area
                    g["geometry"] = g["geometry"].difference(keep)
                    if before - g["geometry"].area > 1e-6:
                        g["note"] = f"{g['note']} | clipped by confirmed labels (-{before - g['geometry'].area:.2f} m2)"
            rows += buildings + [g for g in ignores if not g["geometry"].is_empty and g["geometry"].area > 0.01]

    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs=crs)
    gdf["geometry"] = gdf.geometry.make_valid()
    gdf = gdf.explode(index_parts=False)
    gdf = gdf[gdf.geom_type == "Polygon"].reset_index(drop=True)
    gdf["area_m2"] = gdf.area.round(2)
    assert gdf.is_valid.all() and (gdf.status == "approved").all()
    gdf.to_file(out, driver="GeoJSON")
    s = gdf.groupby(["window_id", "ignore"]).size().unstack(fill_value=0)
    print(f"{len(gdf)} approved features -> {out}\n{s.to_string()}")
    print(gdf.review_decision.value_counts().to_string())


if __name__ == "__main__":
    main()
