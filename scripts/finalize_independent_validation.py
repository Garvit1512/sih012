"""Finalize the independent V1-V4 validation: export reference files, verify statuses, draw comparison.

Reads (never modifies): data/validation_independent/{provisional_labels,reviewed_labels}.geojson,
review_decisions.json, windows.json; the orthophoto; two prediction runs.
Writes into OUT_DIR (must not already contain the reference files):
  reference/buildings_approved.geojson   approved building labels (ignore=false)
  reference/ignore_regions.geojson       uncertain labels, kept provisional ignores, added regions
  verification.json                      checks on statuses / ignores / edits (script exits non-zero on failure)
  comparison_V1-V4.png                   original | baseline | approved post-processing | reference labels
Scoring itself is done by the existing scripts/score_models.py.

Example:
    python scripts/finalize_independent_validation.py outputs/lapaz_predio_bisa_whu_0.3m \
        outputs/rgb_guided_reviewed/final/predictions outputs/independent_validation_final
"""

import argparse
import json
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from PIL import Image, ImageDraw
from rasterio.windows import from_bounds
from shapely.geometry import box

ROOT = Path(__file__).resolve().parents[1]
VAL = ROOT / "data" / "validation_independent"
IGNORE_DECISIONS = {"uncertain", "keep_ignore", "added_ignore_region"}
BUILDING_DECISIONS = {"confirm", "edit"}


def verify(prov, rev, dec):
    checks = {}
    decided = {lid: d for w in dec["windows"].values() for lid, d in w["decisions"].items()}
    checks["every_provisional_label_has_explicit_decision"] = set(decided) == set(prov.label_id)
    checks["decisions_valid"] = set(decided.values()) <= {"confirm", "edit", "uncertain", "keep_ignore"}
    checks["all_reviewed_features_approved"] = bool((rev.status == "approved").all())
    checks["buildings_not_ignored"] = not rev[rev.review_decision.isin(BUILDING_DECISIONS)].ignore.any()
    checks["uncertain_and_regions_ignored"] = bool(rev[rev.review_decision.isin(IGNORE_DECISIONS)].ignore.all())
    want = {lid for lid, d in decided.items() if d in BUILDING_DECISIONS}
    checks["every_confirmed_or_edited_label_present"] = want == set(rev[~rev.ignore].label_id)
    n_regions = sum(len(w.get("added_ignore_regions", [])) for w in dec["windows"].values())
    checks["added_ignore_regions_present"] = int((rev.review_decision == "added_ignore_region").sum()) >= n_regions
    overlap = sum(g[g.ignore].intersection(g[~g.ignore].union_all()).area.sum() for _, g in rev.groupby("window_id"))
    checks["ignores_do_not_cover_buildings"] = bool(overlap < 1e-6)
    checks["edits_applied"] = sorted(rev[rev.review_decision == "edit"].label_id) == sorted(
        lid for w in dec["windows"].values() for lid in w.get("edits", {}))
    checks["all_geometries_valid"] = bool(rev.is_valid.all() and not rev.is_empty.any())
    checks["crs"] = rev.crs.to_string()
    counts = {"building_labels": int((~rev.ignore).sum()), "ignore_features": int(rev.ignore.sum()),
              "by_decision": rev.review_decision.value_counts().to_dict()}
    return checks, counts


def draw(rgb, inv, f, geoms, outline, fill, dashed=False, flags=None):
    im = rgb.convert("RGBA")
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for k, g in enumerate(geoms):
        for p in getattr(g, "geoms", [g]):
            if p.geom_type != "Polygon" or p.is_empty:
                continue
            pts = [tuple(v * f for v in inv * xy) for xy in p.exterior.coords]
            ign = flags is not None and flags[k]
            d.polygon(pts, fill=(128, 128, 128, 110) if ign else fill)
            col = (170, 170, 170, 255) if ign else outline
            if dashed or ign:
                for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]):
                    n = max(1, int(np.hypot(x1 - x0, y1 - y0) // 14))
                    for i in range(0, n, 2):
                        d.line([(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n),
                                (x0 + (x1 - x0) * (i + 1) / n, y0 + (y1 - y0) * (i + 1) / n)], fill=col, width=4)
            else:
                d.line(pts, fill=col, width=4)
    return Image.alpha_composite(im, layer).convert("RGB")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("baseline_dir", type=Path)
    ap.add_argument("approved_dir", type=Path)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--panel", type=int, default=420)
    args = ap.parse_args()
    ref_dir = args.out_dir / "reference"
    for p in (ref_dir / "buildings_approved.geojson", ref_dir / "ignore_regions.geojson"):
        if p.exists():
            raise SystemExit(f"{p} exists - refusing to overwrite")
    ref_dir.mkdir(parents=True, exist_ok=True)

    prov = gpd.read_file(VAL / "provisional_labels.geojson")
    rev = gpd.read_file(VAL / "reviewed_labels.geojson")
    dec = json.loads((VAL / "review_decisions.json").read_text())
    checks, counts = verify(prov, rev, dec)
    rev[~rev.ignore].to_file(ref_dir / "buildings_approved.geojson", driver="GeoJSON")
    rev[rev.ignore].to_file(ref_dir / "ignore_regions.geojson", driver="GeoJSON")
    ok = all(v for k, v in checks.items() if k != "crs")
    (args.out_dir / "verification.json").write_text(json.dumps({"passed": ok, "checks": checks, "counts": counts},
                                                               indent=2, default=str))

    base = gpd.read_file(args.baseline_dir / "buildings.geojson")
    appr = gpd.read_file(args.approved_dir / "buildings.geojson")
    windows = json.loads((VAL / "windows.json").read_text())["windows"]
    P, rows = args.panel, []
    with rasterio.open(ROOT / "data" / "lapaz_predio_bisa.tif") as src:
        for w in windows:
            wb = box(*w["bounds"])
            win = from_bounds(*w["bounds"], transform=src.transform)
            inv = ~src.window_transform(win)
            arr = np.moveaxis(src.read([1, 2, 3], window=win), 0, -1)
            f = P / arr.shape[1]
            rgb = Image.fromarray(arr).resize((P, P), Image.LANCZOS)
            bg = [g for g in base.geometry.intersection(wb) if not g.is_empty]
            ag = [g for g in appr.geometry.intersection(wb) if not g.is_empty]
            lw = rev[rev.window_id == w["id"]]
            panels = [("original", rgb),
                      (f"PREDICTION WHU baseline ({len(bg)})", draw(rgb, inv, f, bg, (0, 200, 255, 255), (0, 200, 255, 50))),
                      (f"PREDICTION approved post-proc ({len(ag)})", draw(rgb, inv, f, ag, (255, 0, 255, 255), (255, 0, 255, 50))),
                      (f"REFERENCE {int((~lw.ignore).sum())} bldg + {int(lw.ignore.sum())} ignore",
                       draw(rgb, inv, f, list(lw.geometry), (255, 230, 0, 255), (255, 230, 0, 40), True, list(lw.ignore)))]
            row = Image.new("RGB", (4 * (P + 6), P + 24), "white")
            d = ImageDraw.Draw(row)
            for i, (name, im) in enumerate(panels):
                row.paste(im, (i * (P + 6), 24))
                d.text((i * (P + 6) + 4, 6), f"{w['id']} | {name}", fill="black")
            rows.append(row)
    foot = ["Cyan solid = WHU baseline prediction. Magenta solid = approved RGB-guided post-processing (accepted cuts only).",
            "Yellow dashed = approved reference buildings; grey dashed = ignored (uncertain / ambiguous) areas, excluded from scoring.",
            "Reference outlines are AI-drafted, user-reviewed roof outlines (~0.25-0.5 m), not survey data.",
            "Detected building footprints - NOT cadastral parcel boundaries."]
    sheet = Image.new("RGB", (rows[0].width, sum(r.height for r in rows) + 20 * len(foot) + 10), "white")
    y = 0
    for r in rows:
        sheet.paste(r, (0, y))
        y += r.height
    d = ImageDraw.Draw(sheet)
    for i, t in enumerate(foot):
        d.text((4, y + 4 + 20 * i), t, fill="black")
    sheet.save(args.out_dir / "comparison_V1-V4.png")
    print(json.dumps({"passed": ok, **counts}, default=str))
    for k, v in checks.items():
        print(f"  [{'PASS' if v is True else ('FAIL' if v is False else 'INFO')}] {k}: {v}")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
