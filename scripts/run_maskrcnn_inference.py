"""Mask R-CNN building *instance* inference (geoai building_footprints_usa.pth) on a georeferenced orthophoto.

Phase-3 candidate C (see outputs/phase3/PROTOCOL.md §4). Configuration is fixed by the protocol:
  torchvision maskrcnn_resnet50_fpn, 2 classes, ImageNet mean/std inside the model, input RGB/255;
  512 px tiles, 25 % overlap (128 px); score > 0.5; mask > 0.5; box NMS 0.5 (torchvision default).
Unlike geoai's own object_detection(), instances are KEPT separate:
  - each tile keeps only instances whose mask centroid lies in the tile's core (tile minus half the
    overlap on interior sides) -> no cross-tile duplicates;
  - overlapping instance masks are resolved pixel-wise to the highest-score instance;
  - no-data pixels are cleared; polygons use the WHU baseline rules (min area 10 m², simplify 0.5 px).
No preview images are written unless --write-preview is given (protocol §2.5).

Example:
    python scripts/run_maskrcnn_inference.py data/lapaz_predio_bisa.tif --target-res 0.6 --out-dir outputs/phase3/maskrcnn_0.6m
"""

import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np
import rasterio
import torch
from torchvision.models.detection import maskrcnn_resnet50_fpn

from postprocess_instances import polygonize
from run_building_inference import read_raster, save_previews, write_geotiff

ROOT = Path(__file__).resolve().parents[1]
CKPT = ROOT / "models" / "maskrcnn_geoai" / "building_footprints_usa.pth"
TILE, OVERLAP = 512, 128
SCORE_T, MASK_T = 0.5, 0.5


def load_model(path: Path) -> torch.nn.Module:
    model = maskrcnn_resnet50_fpn(weights=None, weights_backbone=None, num_classes=2,
                                  image_mean=[0.485, 0.456, 0.406], image_std=[0.229, 0.224, 0.225])
    model.load_state_dict(torch.load(path, map_location="cpu", weights_only=True), strict=True)
    return model.eval()


def starts(length: int) -> list[int]:
    if length <= TILE:
        return [0]
    return sorted(set(list(range(0, length - TILE, TILE - OVERLAP)) + [length - TILE]))


def core_bounds(y0, x0, h, w):
    """Core region of a tile: half the overlap removed on sides that have a neighbouring tile."""
    m = OVERLAP // 2
    return (y0 + (m if y0 > 0 else 0), x0 + (m if x0 > 0 else 0),
            y0 + TILE - (m if y0 + TILE < h else 0), x0 + TILE - (m if x0 + TILE < w else 0))


def select_instances(masks, scores, y0, x0, h, w):
    """Keep instances (score > SCORE_T, non-empty mask) whose mask centroid lies in the tile core.
    masks: (N, TILE, TILE) bool; returns list of (score, rows, cols) in global coordinates."""
    cy0, cx0, cy1, cx1 = core_bounds(y0, x0, h, w)
    out = []
    for m, s in zip(masks, scores):
        if s <= SCORE_T:
            continue
        rr, cc = np.nonzero(m)
        rr, cc = rr + y0, cc + x0
        keep = (rr < h) & (cc < w)  # drop padded area
        rr, cc = rr[keep], cc[keep]
        if rr.size == 0:
            continue
        if cy0 <= rr.mean() < cy1 and cx0 <= cc.mean() < cx1:
            out.append((float(s), rr, cc))
    return out


def paint(instances, shape):
    """Pixel-wise highest-score assignment. Returns (instance raster int32, score raster float32)."""
    inst = np.zeros(shape, np.int32)
    score = np.zeros(shape, np.float32)
    for k, (s, rr, cc) in enumerate(sorted(instances, key=lambda t: t[0]), 1):  # ascending: best last
        inst[rr, cc] = k
        score[rr, cc] = s
    return inst, score


@torch.no_grad()
def predict(model, rgb: np.ndarray, batch: int = 2):
    h, w, _ = rgb.shape
    ph, pw = max(h, TILE), max(w, TILE)
    if (ph, pw) != (h, w):
        rgb = np.pad(rgb, ((0, ph - h), (0, pw - w), (0, 0)), mode="reflect")
    coords = [(y, x) for y in starts(ph) for x in starts(pw)]
    instances = []
    for i in range(0, len(coords), batch):
        chunk = coords[i:i + batch]
        imgs = [torch.from_numpy(np.ascontiguousarray(rgb[y:y + TILE, x:x + TILE].transpose(2, 0, 1))).float() / 255.0
                for y, x in chunk]
        for (y, x), o in zip(chunk, model(imgs)):
            masks = (o["masks"][:, 0] > MASK_T).numpy()
            instances += select_instances(masks, o["scores"].numpy(), y, x, h, w)
    print(f"  tiles: {len(coords)} ({TILE}px, overlap {OVERLAP}px); instances kept: {len(instances)}")
    return paint(instances, (h, w))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", type=Path)
    ap.add_argument("--target-res", type=float, required=True, choices=[0.6, 0.3], help="protocol options only")
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--min-area", type=float, default=10.0)
    ap.add_argument("--write-preview", action="store_true")
    args = ap.parse_args()
    if args.out_dir.exists() and any(args.out_dir.iterdir()):
        raise SystemExit(f"{args.out_dir} is not empty - refusing to overwrite")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(0)
    t0 = time.time()

    rgb, valid, transform, meta = read_raster(args.image, args.target_res)
    px = abs(transform.a)
    print(f"[1/3] grid {rgb.shape[1]}x{rgb.shape[0]} @ {px:.3f} m, CRS {meta['crs']}")
    model = load_model(CKPT)
    print("[2/3] inference (CPU)")
    t1 = time.time()
    inst, score = predict(model, rgb)
    infer_s = time.time() - t1
    inst[~valid] = 0
    score[~valid] = 0
    mask = inst > 0
    simplify = px * 0.5
    gdf = polygonize(inst, score, transform, meta["crs"], args.min_area, simplify)
    print(f"[3/3] {int(len(np.unique(inst[mask])))} instances -> {len(gdf)} polygons >= {args.min_area} m²")

    write_geotiff(args.out_dir / "mask.tif", mask, transform, meta["crs"], "uint8")
    write_geotiff(args.out_dir / "instances.tif", inst, transform, meta["crs"], "int32")
    write_geotiff(args.out_dir / "score.tif", score, transform, meta["crs"], "float32")
    gdf.to_file(args.out_dir / "buildings.geojson", driver="GeoJSON")
    gdf.to_crs(4326).to_file(args.out_dir / "buildings_wgs84.geojson", driver="GeoJSON")
    if args.write_preview:
        save_previews(args.out_dir, rgb, mask, gdf, transform)
    summary = {
        "image": str(args.image), "model": "geoai maskrcnn_resnet50_fpn building_footprints_usa",
        "weights": str(CKPT), "weights_sha256": hashlib.sha256(CKPT.read_bytes()).hexdigest(),
        "hf_repo": "giswqs/geoai", "hf_revision": (CKPT.parent / "REVISION").read_text().strip(),
        "torch_version": torch.__version__, "crs": meta["crs"].to_string(), "source_bounds": meta["bounds"],
        "native_res_m": meta["native_res"], "inference_res_m": px, "inference_shape": list(mask.shape),
        "tile": TILE, "overlap": OVERLAP, "score_threshold": SCORE_T, "mask_threshold": MASK_T,
        "threshold": SCORE_T, "min_area_m2": args.min_area, "simplify_m": simplify,
        "feature_count": len(gdf), "instances": int(len(np.unique(inst[mask]))),
        "inference_s": round(infer_s, 1), "runtime_s": round(time.time() - t0, 1),
        "protocol": "outputs/phase3/PROTOCOL.md §4 (candidate C)",
    }
    (args.out_dir / "run_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"  done in {summary['runtime_s']}s -> {args.out_dir}")


if __name__ == "__main__":
    main()
