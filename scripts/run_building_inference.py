"""Pretrained DLinkNet34 building-footprint inference on a georeferenced orthophoto.

Pipeline: read RGB GeoTIFF (resampled to --target-res metres) -> 384x384 overlapping
tiles -> per-tile min-max + ImageNet normalisation (as in the upstream
examples/Prediction.ipynb) -> DLinkNet34 logits -> sigmoid -> threshold -> polygons
in the raster's own CRS via its affine transform -> GeoJSON + previews.

Output polygons are *detected roof/building footprints*, not legal cadastral parcels.

Example:
    python scripts/run_building_inference.py data/lapaz_predio_bisa.tif --target-res 0.4
"""

import argparse
import _codecs
import json
import time
import warnings
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
import torch
from PIL import Image, ImageDraw
from rasterio.enums import Resampling
from rasterio.features import shapes
from shapely.geometry import shape

from building_footprint_segmentation.seg.binary.models.dlinknet import DLinkNet34
from building_footprint_segmentation.utils.py_network import adjust_model

ROOT = Path(__file__).resolve().parents[1]
TILE = 384  # tile size used by the upstream Prediction example; must be divisible by 32
IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

# best.pt is a TrainStateCallback dict whose "bst_vld_loss" is a pickled numpy.float64.
# Allow exactly the globals needed for that (verified with pickletools) and keep weights_only=True.
SAFE_GLOBALS = [
    (np._core.multiarray.scalar, "numpy.core.multiarray.scalar"),
    np.dtype,
    type(np.dtype("float64")),
    _codecs.encode,
]


def load_model(weights: Path) -> torch.nn.Module:
    with torch.serialization.safe_globals(SAFE_GLOBALS):
        state = torch.load(weights, map_location="cpu", weights_only=True)
    if isinstance(state, dict) and "model" in state:
        state = state["model"]
    with warnings.catch_warnings():
        # The package calls torchvision's deprecated `pretrained=` kwarg; we pass False
        # so no ImageNet weights are downloaded (all weights come from best.pt).
        warnings.simplefilter("ignore", UserWarning)
        model = DLinkNet34(num_classes=1, pre_trained_image_net=False)
    model.load_state_dict(adjust_model(state), strict=True)
    return model.eval()


def normalize(tile: np.ndarray, mode: str) -> np.ndarray:
    """HxWx3 uint8 -> HxWx3 float32, mirroring helpers/normalizer.py (re-implemented
    here because that module imports cv2, which is not needed for inference)."""
    x = tile.astype(np.float32)
    if mode == "min_max_image_net":
        lo = x.min(axis=(0, 1), keepdims=True)
        hi = x.max(axis=(0, 1), keepdims=True)
        x = (x - lo) / np.maximum(hi - lo, 1e-6)
    else:  # divide_255_image_net
        x = x / 255.0
    return (x - IMAGENET_MEAN) / IMAGENET_STD


def read_raster(path: Path, target_res: float | None):
    with rasterio.open(path) as src:
        if src.count < 3:
            raise ValueError(f"Expected an RGB raster, got {src.count} band(s)")
        if src.crs is None or not src.crs.is_projected:
            raise ValueError(f"Raster CRS must be projected (metres); got {src.crs}")
        if target_res:
            scale = target_res / abs(src.res[0])
            out_h = max(1, round(src.height / scale))
            out_w = max(1, round(src.width / scale))
        else:
            out_h, out_w = src.height, src.width
        rgb = src.read([1, 2, 3], out_shape=(3, out_h, out_w), resampling=Resampling.average)
        valid = src.dataset_mask(out_shape=(out_h, out_w), resampling=Resampling.nearest) > 0
        transform = src.transform * src.transform.scale(src.width / out_w, src.height / out_h)
        meta = {"crs": src.crs, "native_res": src.res, "native_shape": (src.height, src.width),
                "bounds": tuple(src.bounds)}
    return np.moveaxis(rgb, 0, -1), valid, transform, meta


def tile_starts(length: int, stride: int) -> list[int]:
    if length <= TILE:
        return [0]
    starts = list(range(0, length - TILE, stride)) + [length - TILE]
    return sorted(set(starts))


@torch.no_grad()
def predict(model, rgb: np.ndarray, norm: str, overlap: int, batch: int) -> np.ndarray:
    """Sliding-window probabilities averaged over overlaps with a centre-weighted window."""
    h, w, _ = rgb.shape
    ph, pw = max(h, TILE), max(w, TILE)
    if (ph, pw) != (h, w):  # small rasters: reflect-pad up to one tile
        rgb = np.pad(rgb, ((0, ph - h), (0, pw - w), (0, 0)), mode="reflect")
    stride = TILE - overlap
    win1d = np.hanning(TILE + 2)[1:-1].astype(np.float32)
    weight = np.outer(win1d, win1d) + 1e-3
    acc = np.zeros((ph, pw), np.float32)
    wsum = np.zeros((ph, pw), np.float32)
    coords = [(y, x) for y in tile_starts(ph, stride) for x in tile_starts(pw, stride)]
    for i in range(0, len(coords), batch):
        chunk = coords[i:i + batch]
        x = np.stack([normalize(rgb[y:y + TILE, x:x + TILE], norm) for y, x in chunk])
        prob = torch.from_numpy(np.moveaxis(x, -1, 1)).float()
        prob = model(prob).sigmoid()[:, 0].numpy()
        for (y, x0), p in zip(chunk, prob):
            acc[y:y + TILE, x0:x0 + TILE] += p * weight
            wsum[y:y + TILE, x0:x0 + TILE] += weight
    print(f"  tiles: {len(coords)} ({TILE}px, overlap {overlap}px)")
    return (acc / wsum)[:h, :w]


def vectorize(mask, prob, transform, crs, min_area, simplify):
    records = []
    for geom, val in shapes(mask.astype(np.uint8), mask=mask, transform=transform, connectivity=4):
        poly = shape(geom)
        if simplify > 0:
            poly = poly.simplify(simplify, preserve_topology=True)
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty or poly.area < min_area:
            continue
        records.append({"geometry": poly})
    gdf = gpd.GeoDataFrame(records, geometry="geometry", crs=crs) if records else \
        gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=crs)
    if len(gdf):
        # mean probability inside each polygon, sampled on the inference grid
        from rasterio.features import rasterize
        labels = rasterize(((g, i + 1) for i, g in enumerate(gdf.geometry)),
                           out_shape=prob.shape, transform=transform, dtype="int32")
        sums = np.bincount(labels.ravel(), weights=prob.ravel(), minlength=len(gdf) + 1)
        counts = np.bincount(labels.ravel(), minlength=len(gdf) + 1)
        gdf["mean_prob"] = np.round(sums[1:] / np.maximum(counts[1:], 1), 3)
        gdf["area_m2"] = gdf.geometry.area.round(1)
        gdf.insert(0, "id", np.arange(1, len(gdf) + 1))
    return gdf


def write_geotiff(path, array, transform, crs, dtype):
    with rasterio.open(path, "w", driver="GTiff", height=array.shape[0], width=array.shape[1],
                      count=1, dtype=dtype, crs=crs, transform=transform, compress="deflate") as dst:
        dst.write(array.astype(dtype), 1)


def save_previews(out_dir, rgb, mask, gdf, transform, max_side=1600):
    h, w = mask.shape
    f = min(max_side / max(h, w), max(1.0, 800 / max(h, w)))  # upscale tiny grids for legibility
    size = (max(1, round(w * f)), max(1, round(h * f)))
    Image.fromarray((mask * 255).astype(np.uint8)).resize(size, Image.NEAREST).save(out_dir / "mask_preview.png")
    # Overlay draws the *saved vector geometries* mapped back through the inverse affine
    # transform, so it independently checks georeferencing rather than re-showing the mask.
    base = Image.fromarray(rgb).resize(size, Image.BILINEAR).convert("RGBA")
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    inv = ~transform
    for geom in gdf.geometry:
        for poly in getattr(geom, "geoms", [geom]):
            pts = [tuple(c * f for c in inv * xy) for xy in poly.exterior.coords]
            draw.polygon(pts, fill=(255, 0, 255, 70), outline=(255, 255, 0, 255), width=2)
    Image.alpha_composite(base, layer).convert("RGB").save(out_dir / "overlay.png")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", type=Path)
    ap.add_argument("--weights", type=Path, default=ROOT / "models" / "best.pt")
    ap.add_argument("--out-dir", type=Path, default=None, help="default: outputs/<image-stem>_<res>m")
    ap.add_argument("--target-res", type=float, default=0.4,
                    help="metres/pixel fed to the model (weights trained on ~1 m Massachusetts imagery); 0 = native")
    ap.add_argument("--norm", choices=["min_max_image_net", "divide_255_image_net"], default="min_max_image_net")
    ap.add_argument("--threshold", type=float, default=0.4)
    ap.add_argument("--overlap", type=int, default=64)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--min-area", type=float, default=10.0, help="drop polygons smaller than this (m^2)")
    ap.add_argument("--simplify", type=float, default=None, help="simplify tolerance in metres (default: 0.5 x pixel)")
    args = ap.parse_args()

    torch.set_num_threads(max(1, torch.get_num_threads()))
    t0 = time.time()
    res_tag = f"{args.target_res:g}m" if args.target_res else "native"
    out_dir = args.out_dir or ROOT / "outputs" / f"{args.image.stem}_{res_tag}"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[1/5] reading {args.image}")
    rgb, valid, transform, meta = read_raster(args.image, args.target_res or None)
    px = abs(transform.a)
    print(f"  CRS {meta['crs']}  native res {meta['native_res'][0]:.4f} m  "
          f"inference grid {rgb.shape[1]}x{rgb.shape[0]} @ {px:.4f} m  valid px {valid.mean():.1%}")

    print(f"[2/5] loading {args.weights}")
    model = load_model(args.weights)

    print(f"[3/5] inference on CPU (norm={args.norm})")
    t1 = time.time()
    prob = predict(model, rgb, args.norm, args.overlap, args.batch)
    prob[~valid] = 0.0  # outside the orthophoto footprint (nodata collar)
    print(f"  {time.time() - t1:.1f}s  prob min/mean/max {prob.min():.3f}/{prob.mean():.3f}/{prob.max():.3f}")

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
    gdf.to_file(out_dir / "buildings.geojson", driver="GeoJSON")  # source CRS (crs member written)
    gdf.to_crs(4326).to_file(out_dir / "buildings_wgs84.geojson", driver="GeoJSON")  # RFC 7946
    save_previews(out_dir, rgb, mask, gdf, transform)
    summary = {
        "image": str(args.image), "weights": str(args.weights), "crs": meta["crs"].to_string(),
        "source_bounds": meta["bounds"], "native_res_m": meta["native_res"], "inference_res_m": px,
        "inference_shape": list(mask.shape), "norm": args.norm, "threshold": args.threshold,
        "min_area_m2": args.min_area, "simplify_m": simplify, "feature_count": len(gdf),
        "building_fraction_of_valid": float(mask[valid].mean()),
        "runtime_s": round(time.time() - t0, 1),
    }
    (out_dir / "run_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"  done in {summary['runtime_s']}s")


if __name__ == "__main__":
    main()
