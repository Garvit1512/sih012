"""RGB-guided separation of touching buildings inside an existing WHU mask (experiment).

Reads a finished WHU run (mask.tif, probability.tif, run_summary.json) and the source
orthophoto, never modifies them, and writes a new directory.

Idea: where WHU's mask merges touching roofs, the roofs often differ in *colour*
(e.g. orange sheet vs grey metal, rusty vs galvanised). Ridges, shadows and roof planes
mostly change *brightness*. So only brightness-normalised chromaticity is used:

  1. RGB area-averaged onto the mask grid; chromaticity r=R/(R+G+B), g=G/(R+G+B);
     5x5 median (1.5 m at 0.3 m) to suppress corrugation texture.
  2. Chroma gradient G (Sobel). Per component: seeds = connected pixels with
     G <= the scene's 30th percentile of building-pixel G; regions grown from the
     seeds in increasing-G order, strictly inside the component (every mask pixel is
     assigned -> building pixels and total area unchanged).
  3. Guard merges (always): a region < --min-part m^2, or touching the component's
     outer edge along < --min-outer-contact of its perimeter (an enclosed patch such as
     a skylight), is merged into its most similar neighbour.
     The regions at this point are the *candidate* parts; their shared boundaries are
     the candidate split lines.
  4. Contrast merges: repeatedly merge the adjacent pair that is least separable until
     every remaining boundary has mean-chroma difference >= --min-contrast AND at least
     --min-edge-support of its length on strong chroma edges (>= scene 75th percentile).
  5. Remaining boundaries are proposed cuts, flagged needs_review=True; confidence
     "high" if contrast >= 2x threshold and edge support >= 0.5, else "medium".

The cut decision never uses labels, Microsoft footprints, rectangles or centroids.
Outputs are detected building footprints, not cadastral parcel boundaries.

Example:
    python scripts/rgb_guided_instances.py outputs/lapaz_predio_bisa_whu_0.3m outputs/rgb_guided_v1
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
from shapely.ops import linemerge, unary_union

from postprocess_instances import FOUR, _boundaries, polygonize
from run_building_inference import read_raster, save_previews, write_geotiff


def chroma_features(rgb: np.ndarray, median_px: int = 5):
    """rgb HxWx3 -> (chroma 2xHxW median-smoothed, gradient magnitude HxW)."""
    x = rgb.astype(np.float32)
    s = x.sum(-1) + 3.0
    chroma = np.stack([ndi.median_filter(x[..., i] / s, median_px) for i in (0, 1)])
    grad = sum(np.hypot(ndi.sobel(c, 0), ndi.sobel(c, 1)) for c in chroma) / 8.0
    return chroma, grad


def _flood(seeds: np.ndarray, comp: np.ndarray, grad: np.ndarray, levels: int = 16) -> np.ndarray:
    """Grow labelled seeds inside comp, admitting pixels in increasing gradient order."""
    lab = np.where(comp, seeds, 0).astype(np.int32)
    qs = np.unique(np.quantile(grad[comp], np.linspace(0, 1, levels + 1)[1:]))
    for t in list(qs) + [np.inf]:
        region = comp & (grad <= t)
        while True:
            grown = ndi.grey_dilation(lab, footprint=FOUR)
            new = region & (lab == 0) & (grown > 0)
            if not new.any():
                break
            lab[new] = grown[new]
    return lab


class RAG:
    """Incremental region adjacency graph for one component.

    Per region: area (px), chroma sums, perimeter edges facing outside the component, total
    perimeter edges. Per adjacent pair: shared 4-neighbour edges and how many of them touch a
    strong-gradient pixel. Merging updates these counters, so no raster recomputation is needed.
    """

    def __init__(self, lab, chroma, strong):
        ids = np.unique(lab[lab > 0])
        self.parent = {int(i): int(i) for i in ids}
        flat = lab.ravel()
        self.area = {int(i): int(c) for i, c in zip(ids, np.bincount(flat)[ids])}
        self.csum = {int(i): np.array([np.bincount(flat, weights=c.ravel())[i] for c in chroma]) for i in ids}
        self.outer = {int(i): 0 for i in ids}
        self.per = {int(i): 0 for i in ids}
        self.pairs = {}
        padl, pads = np.pad(lab, 1), np.pad(strong, 1)
        for (x, y, sx, sy) in ((padl[:, :-1], padl[:, 1:], pads[:, :-1], pads[:, 1:]),
                               (padl[:-1, :], padl[1:, :], pads[:-1, :], pads[1:, :])):
            sel = x != y
            xs, ys, st = x[sel], y[sel], (sx | sy)[sel]
            for a, b, f in zip(xs.tolist(), ys.tolist(), st.tolist()):
                if a and b:
                    k = (a, b) if a < b else (b, a)
                    e = self.pairs.setdefault(k, [0, 0])
                    e[0] += 1
                    e[1] += f
                    self.per[a] += 1
                    self.per[b] += 1
                else:
                    r = a or b
                    self.outer[r] += 1
                    self.per[r] += 1
        self.nbrs = {i: set() for i in self.area}
        for a, b in self.pairs:
            self.nbrs[a].add(b)
            self.nbrs[b].add(a)

    def mean(self, i):
        return self.csum[i] / self.area[i]

    def diff(self, a, b):
        return float(np.linalg.norm(self.mean(a) - self.mean(b)))

    def edge(self, a, b):
        return self.pairs[(a, b) if a < b else (b, a)]

    def merge(self, a, b):
        """Merge region a into region b."""
        n_ab = self.edge(a, b)[0]
        self.area[b] += self.area.pop(a)
        self.csum[b] = self.csum[b] + self.csum.pop(a)
        self.outer[b] += self.outer.pop(a)
        self.per[b] = self.per[b] + self.per.pop(a) - 2 * n_ab
        del self.pairs[(a, b) if a < b else (b, a)]
        for c in self.nbrs.pop(a) - {b}:
            ea = self.pairs.pop((a, c) if a < c else (c, a))
            eb = self.pairs.setdefault((b, c) if b < c else (c, b), [0, 0])
            eb[0] += ea[0]
            eb[1] += ea[1]
            self.nbrs[c].discard(a)
            self.nbrs[c].add(b)
            self.nbrs[b].add(c)
        self.nbrs[b].discard(a)
        self.parent[a] = b

    def find(self, i):
        while self.parent[i] != i:
            i = self.parent[i]
        return i

    def relabel(self, lab):
        lut = np.zeros(int(lab.max()) + 1, np.int32)
        for i in self.parent:
            lut[i] = self.find(i)
        return lut[lab]


def separate_component(comp, chroma, grad, seed_t, strong_t, px, min_part_m2, min_outer, min_contrast,
                       min_support):
    """Return (final labels, candidate labels, cut records) for one component (all local arrays)."""
    seeds, k = ndi.label(comp & (grad <= seed_t), structure=FOUR)
    if k <= 1:
        lab = comp.astype(np.int32)
        return lab, lab.copy(), []
    lab = _flood(seeds, comp, grad)
    g = RAG(lab, chroma, grad >= strong_t)
    min_part_px = min_part_m2 / (px * px)

    def guard_pass():
        while len(g.area) > 1:
            bad = [i for i in g.area if g.nbrs[i] and
                   (g.area[i] < min_part_px or g.outer[i] / max(g.per[i], 1) < min_outer)]
            if not bad:
                return
            i = min(bad, key=lambda j: (g.area[j], j))  # smallest offender first: deterministic
            j = min(g.nbrs[i], key=lambda n: (g.diff(i, n), n))
            g.merge(i, j)

    guard_pass()
    candidates = g.relabel(lab)
    while len(g.area) > 1:
        weak = []
        for (a, b), (n, ns) in g.pairs.items():
            contrast, support = g.diff(a, b), ns / n
            if contrast < min_contrast or support < min_support:
                weak.append((contrast / min_contrast + support / min_support, a, b))
        if not weak:
            break
        _, a, b = min(weak)
        g.merge(a, b) if g.area[a] <= g.area[b] else g.merge(b, a)
        guard_pass()

    cuts = []
    for (a, b), (n, ns) in g.pairs.items():
        contrast, support = g.diff(a, b), ns / n
        conf = "high" if contrast >= 2 * min_contrast and support >= 0.5 else "medium"
        cuts.append({"a": int(a), "b": int(b), "contrast": round(contrast, 4), "edge_support": round(support, 3),
                     "boundary_m": round(n * px, 1), "confidence": conf})
    return g.relabel(lab), candidates, cuts


def boundary_lines(lab, transform, pairs=None):
    """Shared boundaries between labelled regions as LineStrings (unsimplified pixel edges)."""
    polys = {}
    for geom, val in shapes(lab.astype(np.int32), mask=lab > 0, transform=transform, connectivity=4):
        polys.setdefault(int(val), []).append(shape(geom))
    polys = {k: unary_union(v) for k, v in polys.items()}
    out = []
    for (a, b) in (pairs or _boundaries(lab).keys()):
        line = polys[a].boundary.intersection(polys[b].boundary)
        if not line.is_empty:
            out.append(((a, b), linemerge(line) if line.geom_type == "MultiLineString" else line))
    return out


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("baseline_dir", type=Path)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--min-contrast", type=float, default=0.04, help="min mean-chromaticity difference for a cut")
    ap.add_argument("--min-edge-support", type=float, default=0.3, help="min fraction of cut on strong chroma edges")
    ap.add_argument("--min-part", type=float, default=10.0, help="m^2")
    ap.add_argument("--min-outer-contact", type=float, default=0.25, help="min fraction of a part's perimeter on the component edge")
    ap.add_argument("--seed-percentile", type=float, default=30.0)
    ap.add_argument("--strong-percentile", type=float, default=75.0)
    args = ap.parse_args()

    t0 = time.time()
    if args.out_dir.resolve() == args.baseline_dir.resolve():
        raise SystemExit("out_dir must differ from baseline_dir")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    base = json.loads((args.baseline_dir / "run_summary.json").read_text())
    inputs = {p: sha256(args.baseline_dir / p) for p in ("probability.tif", "mask.tif", "run_summary.json")}

    with rasterio.open(args.baseline_dir / "probability.tif") as s:
        prob, transform, crs = s.read(1), s.transform, s.crs
    with rasterio.open(args.baseline_dir / "mask.tif") as s:
        mask = s.read(1).astype(bool)
    assert np.array_equal(prob >= base["threshold"], mask), "baseline mask/probability mismatch"
    rgb, _, rgb_tf, _ = read_raster(Path(base["image"]), base["inference_res_m"])
    assert rgb_tf.almost_equals(transform) and rgb.shape[:2] == mask.shape, "RGB grid differs from mask grid"
    px = abs(transform.a)

    chroma, grad = chroma_features(rgb)
    seed_t = float(np.percentile(grad[mask], args.seed_percentile))  # label-free scene statistics
    strong_t = float(np.percentile(grad[mask], args.strong_percentile))

    comps, n = ndi.label(mask, structure=FOUR)
    final = np.zeros(mask.shape, np.int32)
    cand = np.zeros(mask.shape, np.int32)
    cut_rows, nid, cid = [], 1, 1
    for ci, sl in enumerate(ndi.find_objects(comps), 1):
        sl = tuple(slice(max(s.start - 1, 0), s.stop + 1) for s in sl)
        comp = comps[sl] == ci
        lab, cl, cuts = separate_component(comp, chroma[(slice(None),) + sl], grad[sl], seed_t, strong_t, px,
                                           args.min_part, args.min_outer_contact, args.min_contrast,
                                           args.min_edge_support)
        remap = {}
        for v in np.unique(lab[lab > 0]):
            remap[int(v)] = nid
            final[sl][lab == v] = nid
            nid += 1
        for v in np.unique(cl[cl > 0]):
            cand[sl][cl == v] = cid
            cid += 1
        for c in cuts:
            cut_rows.append({**c, "a": remap[c["a"]], "b": remap[c["b"]], "component": ci})
    assert np.array_equal(final > 0, mask) and np.array_equal(cand > 0, mask), "building pixels changed"

    # polygons (same simplification / min area as the baseline)
    gdf = polygonize(final, prob, transform, crs, base["min_area_m2"], base["simplify_m"])
    split_ids = {r["a"] for r in cut_rows} | {r["b"] for r in cut_rows}
    conf_of = {}
    for r in cut_rows:
        for k in ("a", "b"):
            conf_of[r[k]] = "medium" if "medium" in (conf_of.get(r[k]), r["confidence"]) else "high"
    gdf["rgb_split"] = gdf["instance"].isin(split_ids)
    gdf["needs_review"] = gdf["rgb_split"]
    gdf["split_confidence"] = gdf["instance"].map(conf_of).fillna("unsplit")

    # cut lines (accepted) and candidate lines (considered after guard merges)
    acc = boundary_lines(final, transform, [(min(r["a"], r["b"]), max(r["a"], r["b"])) for r in cut_rows])
    info = {(min(r["a"], r["b"]), max(r["a"], r["b"])): r for r in cut_rows}
    cuts_gdf = gpd.GeoDataFrame([{**info[k], "status": "proposed_cut", "needs_review": True, "geometry": g}
                                 for k, g in acc], geometry="geometry", crs=crs) if acc else \
        gpd.GeoDataFrame({"status": []}, geometry=[], crs=crs)
    cand_lines = boundary_lines(cand, transform)
    cand_gdf = gpd.GeoDataFrame([{"status": "candidate", "geometry": g} for _, g in cand_lines],
                                geometry="geometry", crs=crs) if cand_lines else \
        gpd.GeoDataFrame({"status": []}, geometry=[], crs=crs)

    write_geotiff(args.out_dir / "mask.tif", mask, transform, crs, "uint8")
    write_geotiff(args.out_dir / "instances.tif", final, transform, crs, "int32")
    write_geotiff(args.out_dir / "candidate_regions.tif", cand, transform, crs, "int32")
    for name, g in (("buildings.geojson", gdf), ("buildings_wgs84.geojson", gdf.to_crs(4326)),
                    ("cut_lines.geojson", cuts_gdf), ("candidate_lines.geojson", cand_gdf)):
        (args.out_dir / name).unlink(missing_ok=True)
        g.to_file(args.out_dir / name, driver="GeoJSON")
    save_previews(args.out_dir, rgb, mask, gdf, transform)

    summary = {
        **{k: base[k] for k in ("image", "crs", "source_bounds", "native_res_m", "inference_res_m", "threshold",
                                "min_area_m2", "simplify_m")},
        "model": base.get("model"), "hf_revision": base.get("hf_revision"),
        "postprocess": "rgb_guided_v1: chromaticity region growing + guard merges + contrast/edge-support merges",
        "baseline_dir": str(args.baseline_dir), "baseline_input_sha256": inputs,
        "params": {k: getattr(args, k) for k in ("min_contrast", "min_edge_support", "min_part", "min_outer_contact",
                                                 "seed_percentile", "strong_percentile")},
        "seed_grad_threshold": round(seed_t, 5), "strong_grad_threshold": round(strong_t, 5),
        "connected_components": int(n), "candidate_regions": int(cand.max()), "instances": int(final.max()),
        "proposed_cuts": len(cut_rows), "cuts_high": sum(r["confidence"] == "high" for r in cut_rows),
        "feature_count": len(gdf), "baseline_feature_count": base["feature_count"],
        "building_pixels": int(mask.sum()), "runtime_s": round(time.time() - t0, 1),
    }
    (args.out_dir / "run_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"components {n} -> candidate regions {cand.max()} -> instances {final.max()} "
          f"({len(cut_rows)} proposed cuts, {summary['cuts_high']} high) -> {len(gdf)} polygons "
          f"(baseline {base['feature_count']}); {summary['runtime_s']}s")


if __name__ == "__main__":
    main()

