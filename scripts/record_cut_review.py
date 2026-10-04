"""Record human review decisions for RGB-guided cuts into a NEW reviewed file.

Reads the pending inventory (cut_review.geojson, never modified) and a decisions CSV
(cut_id, proposed_status, reason). Writes cut_review_reviewed.geojson with status set from the CSV,
plus reviewer, review_date and decision source. Requires --confirm: decisions are never applied
silently, and nothing is approved from confidence scores.

Example:
    python scripts/record_cut_review.py outputs/rgb_guided_reviewed --decisions outputs/rgb_guided_reviewed/cut_review_proposals.csv \
        --reviewer "project user" --source "AI proposal (Claude) confirmed by project user" --date 2026-10-04 --confirm
"""

import argparse
from pathlib import Path

import geopandas as gpd
import pandas as pd

VALID = {"accepted", "rejected", "uncertain"}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("review_dir", type=Path)
    ap.add_argument("--decisions", type=Path, required=True)
    ap.add_argument("--reviewer", required=True)
    ap.add_argument("--source", required=True, help="how the decisions were made, recorded per cut")
    ap.add_argument("--date", required=True)
    ap.add_argument("--confirm", action="store_true", help="required: the reviewer confirmed these decisions")
    ap.add_argument("--out", type=Path, default=None,
                    help="output file (default <review_dir>/cut_review_reviewed.geojson); an existing --out is never overwritten")
    args = ap.parse_args()
    if not args.confirm:
        raise SystemExit("--confirm is required; decisions must be explicitly confirmed by the reviewer")

    inv = gpd.read_file(args.review_dir / "cut_review.geojson")
    dec = pd.read_csv(args.decisions)
    assert dec.cut_id.is_unique and set(dec.cut_id) == set(inv.cut_id), "decisions must cover every cut exactly once"
    bad = set(dec.proposed_status) - VALID
    assert not bad, f"invalid statuses {bad}"
    assert (inv.status == "pending").all(), "inventory must be the untouched pending file"
    out = inv.drop(columns=["status", "reviewer", "review_date", "reason"]).merge(
        dec.rename(columns={"proposed_status": "status"}), on="cut_id")
    out["reviewer"], out["review_date"], out["decision_source"] = args.reviewer, args.date, args.source
    out = gpd.GeoDataFrame(out, geometry="geometry", crs=inv.crs)
    if args.out is not None:
        path = args.out
        if path.exists():
            raise SystemExit(f"{path} exists - refusing to overwrite a recorded review")
    else:
        path = args.review_dir / "cut_review_reviewed.geojson"
        path.unlink(missing_ok=True)
    out.to_file(path, driver="GeoJSON")
    print(f"{len(out)} cuts recorded -> {path}: {out.status.value_counts().to_dict()}")


if __name__ == "__main__":
    main()
