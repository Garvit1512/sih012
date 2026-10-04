"""WHU UNet++ / EfficientNet-B4 building-footprint inference on a georeferenced orthophoto.

Second model backend alongside run_building_inference.py (DLinkNet34). Raster reading,
resampling, no-data masking, vectorization, GeoTIFF/GeoJSON export and previews are
imported from that script unchanged; only the model, preprocessing and windowing differ.

Checkpoint: huggingface.co/giswqs/whu-building-unetplusplus-efficientnet-b4
(revision in models/whu/REVISION, Apache-2.0). Trained on the WHU aerial building
dataset (Christchurch, NZ, 0.3 m, 512x512 RGB tiles). Preprocessing mirrors
geoai.timm_segment.timm_semantic_segmentation: RGB / 255 (no mean/std), softmax over
2 classes, class 1 = building.

Output polygons are *detected roof/building footprints*, not legal cadastral parcels.

Example:
    python scripts/run_whu_inference.py data/lapaz_predio_bisa.tif --target-res 0.3
"""

import argparse
import json
import time
from pathlib import Path

import numpy as np
import segmentation_models_pytorch as smp
import torch

from run_building_inference import read_raster, save_previews, vectorize, write_geotiff

ROOT = Path(__file__).resolve().parents[1]
WHU_DIR = ROOT / "models" / "whu"


def load_model(weights: Path, config: dict) -> torch.nn.Module:
    assert config["architecture"] == "unetplusplus" and not config["use_timm_model"], config
    model = smp.UnetPlusPlus(encoder_name=config["encoder_name"], encoder_weights=None,
                             in_channels=config["num_channels"], classes=config["num_classes"])
    # Plain state_dict containing only tensors (verified with pickletools) -> weights_only is enough.
    state = torch.load(weights, map_location="cpu", weights_only=True)
    model.load_state_dict(state, strict=True)
    return model.eval()


def window_starts(length: int, window: int, stride: int) -> list[int]:
    if length <= window:
        return [0]
    return sorted(set(list(range(0, length - window, stride)) + [length - window]))


@torch.no_grad()
def predict(model, rgb: np.ndarray, window: int, overlap: int, batch: int) -> np.ndarray:
    """Building-class softmax probability, blended over overlapping windows."""
    h, w, _ = rgb.shape
    ph, pw = max(h, window), max(w, window)
    if (ph, pw) != (h, w):
        rgb = np.pad(rgb, ((0, ph - h), (0, pw - w), (0, 0)), mode="reflect")
    stride = window - overlap
    win1d = np.hanning(window + 2)[1:-1].astype(np.float32)
    weight = np.outer(win1d, win1d) + 1e-3
    acc = np.zeros((ph, pw), np.float32)
    wsum = np.zeros((ph, pw), np.float32)
    coords = [(y, x) for y in window_starts(ph, window, stride) for x in window_starts(pw, window, stride)]
    for i in range(0, len(coords), batch):
        chunk = coords[i:i + batch]
        x = np.stack([rgb[y:y + window, x0:x0 + window] for y, x0 in chunk]).astype(np.float32) / 255.0
        logits = model(torch.from_numpy(np.ascontiguousarray(np.moveaxis(x, -1, 1))))
        prob = torch.softmax(logits, dim=1)[:, 1].numpy()
        for (y, x0), p in zip(chunk, prob):
            acc[y:y + window, x0:x0 + window] += p * weight
            wsum[y:y + window, x0:x0 + window] += weight
    print(f"  windows: {len(coords)} ({window}px, overlap {overlap}px)")
    return (acc / wsum)[:h, :w]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", type=Path)
    ap.add_argument("--model-dir", type=Path, default=WHU_DIR)
    ap.add_argument("--out-dir", type=Path, default=None, help="default: outputs/<image-stem>_whu_<res>m")
    ap.add_argument("--target-res", type=float, default=0.3, help="m/pixel fed to the model (WHU is 0.3 m); 0 = native")
    ap.add_argument("--threshold", type=float, default=0.5, help="building-class probability (0.5 == argmax)")
    ap.add_argument("--window", type=int, default=512)
    ap.add_argument("--overlap", type=int, default=128)
    ap.add_argument("--batch", type=int, default=2)
    ap.add_argument("--min-area", type=float, default=10.0, help="drop polygons smaller than this (m^2)")
    ap.add_argument("--simplify", type=float, default=None, help="simplify tolerance in metres (default: 0.5 x pixel)")
    args = ap.parse_args()

    t0 = time.time()
    res_tag = f"{args.target_res:g}m" if args.target_res else "native"
    out_dir = args.out_dir or ROOT / "outputs" / f"{args.image.stem}_whu_{res_tag}"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[1/5] reading {args.image}")
    rgb, valid, transform, meta = read_raster(args.image, args.target_res or None)
    px = abs(transform.a)
    print(f"  CRS {meta['crs']}  native res {meta['native_res'][0]:.4f} m  "
          f"inference grid {rgb.shape[1]}x{rgb.shape[0]} @ {px:.4f} m  valid px {valid.mean():.1%}")

    config = json.loads((args.model_dir / "config.json").read_text())
    revision = (args.model_dir / "REVISION").read_text().strip()
    print(f"[2/5] loading {args.model_dir / 'model.pth'} (rev {revision[:8]}, {config['architecture']}/{config['encoder_name']})")
    model = load_model(args.model_dir / "model.pth", config)

    print("[3/5] inference on CPU (norm=divide_by_255, softmax class 1)")
    t1 = time.time()
    prob = predict(model, rgb, args.window, args.overlap, args.batch)
    infer_s = time.time() - t1
    prob[~valid] = 0.0  # outside the orthophoto footprint (nodata collar)
    print(f"  {infer_s:.1f}s  prob min/mean/max {prob.min():.3f}/{prob.mean():.3f}/{prob.max():.3f}")

    mask = prob >= args.threshold
    print(f"[4/5] vectorizing (threshold {args.threshold}, building px {mask[valid].mean():.1%} of valid area)")
    simplify = px * 0.5 if args.simplify is None else args.simplify
    gdf = vectorize(mask, prob, transform, meta["crs"], args.min_area, simplify)
    print(f"  {len(gdf)} polygons >= {args.min_area} m^2")

    print(f"[5/5] writing outputs to {out_dir}")
    write_geotiff(out_dir / "probability.tif", prob, transform, meta["crs"], "float32")
    write_geotiff(out_dir / "mask.tif", mask, transform, meta["crs"], "uint8")
    for p in ("buildings.geojson", "buildings_wgs84.geojson"):
        (out_dir / p).unlink(missing_ok=True)
    gdf.to_file(out_dir / "buildings.geojson", driver="GeoJSON")
    gdf.to_crs(4326).to_file(out_dir / "buildings_wgs84.geojson", driver="GeoJSON")
    save_previews(out_dir, rgb, mask, gdf, transform)
    summary = {
        "image": str(args.image), "model": "whu-unetplusplus-efficientnet-b4",
        "weights": str(args.model_dir / "model.pth"),
        "hf_repo": "giswqs/whu-building-unetplusplus-efficientnet-b4", "hf_revision": revision,
        "smp_version": smp.__version__, "torch_version": torch.__version__,
        "crs": meta["crs"].to_string(), "source_bounds": meta["bounds"], "native_res_m": meta["native_res"],
        "inference_res_m": px, "inference_shape": list(mask.shape), "norm": "divide_by_255",
        "window": args.window, "overlap": args.overlap, "threshold": args.threshold,
        "min_area_m2": args.min_area, "simplify_m": simplify, "feature_count": len(gdf),
        "building_fraction_of_valid": float(mask[valid].mean()),
        "inference_s": round(infer_s, 1), "runtime_s": round(time.time() - t0, 1),
    }
    (out_dir / "run_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"  done in {summary['runtime_s']}s")


if __name__ == "__main__":
    main()
