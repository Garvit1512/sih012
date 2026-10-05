"""New-site inference adapters reuse the historical models without changing them."""
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.warp import calculate_default_transform
from shapely.geometry import box, mapping, shape

from ..config import ROOT, MAX_FEATURES_PER_REQUEST
from ..errors import ApiError
from ..utils.crs import metric_crs
from ..utils.geometry import check_polygon
from .ingestion_service import sha256

MAX_INFERENCE_PIXELS = 16_000_000


def bounds_wgs(manifest):
    if manifest.get("raster", {}).get("bounds_wgs84"):
        return manifest["raster"]["bounds_wgs84"]
    (s, w), (n, e) = manifest["ortho"]["bounds_latlon"]
    return [w, s, e, n]


def validate_features(manifest, fc):
    if fc.get("type") != "FeatureCollection" or not isinstance(fc.get("features"), list):
        raise ApiError("INVALID_OUTPUT", "Expected a GeoJSON FeatureCollection.", 422)
    if len(fc["features"]) > MAX_FEATURES_PER_REQUEST:
        raise ApiError("TOO_MANY_FEATURES", "Split this area into smaller sites (limit: 5,000 polygons per extraction).", 422)
    ids = set()
    extent = box(*bounds_wgs(manifest)).buffer(1e-8)
    for feature in fc["features"]:
        props = feature.get("properties") or {}
        source_id = props.get("source_id")
        if not isinstance(source_id, int) or source_id < 1 or source_id in ids:
            raise ApiError("INVALID_OUTPUT", "Unique positive integer source_id values are required.", 422)
        ids.add(source_id)
        geometry = shape(feature["geometry"])
        if geometry.geom_type not in ("Polygon", "MultiPolygon") or geometry.is_empty or not geometry.is_valid:
            raise ApiError("INVALID_OUTPUT", "Output polygons must be non-empty and valid.", 422)
        if not extent.covers(geometry):
            raise ApiError("OUTSIDE_SITE", "Output geometry lies outside the site's imagery extent.", 422)
        if props.get("feature_type") not in ("building_footprint", "road_surface", "land_cover"):
            raise ApiError("INVALID_OUTPUT", "Unknown feature type.", 422)
        if props.get("functional_use", "unknown") != "unknown":
            raise ApiError("INVALID_OUTPUT", "Image extraction cannot assign functional use without a separate evidence-based classifier.", 422)


def import_features(manifest, payload):
    w, s, e, n = bounds_wgs(manifest)
    features = []
    for index, feature in enumerate(payload["features"]["features"], 1):
        checked = check_polygon(feature["geometry"], payload["input_crs"], [w, s, e, n], "imported geometry", manifest["analysis_crs"])
        props = feature.get("properties") or {}
        features.append({"type": "Feature", "geometry": mapping(checked.geometry), "properties": {
            "source_id": index, "feature_type": payload["feature_type"], "class_name": props.get("class_name"),
            "functional_use": "unknown", "flags": props.get("flags", ""),
            "import_source_id": str(feature.get("id", index)), "repairs": checked.repairs,
            **({"score": float(props["score"])} if props.get("score") is not None else {}),
        }})
    fc = {"type": "FeatureCollection", "features": features}
    validate_features(manifest, fc)
    return fc


def read_grid(path, analysis_crs, resolution):
    metric_crs(analysis_crs)
    with rasterio.open(path) as src:
        transform, width, height = calculate_default_transform(src.crs, analysis_crs, src.width, src.height,
                                                               *src.bounds, resolution=resolution)
        if width * height > MAX_INFERENCE_PIXELS:
            raise ApiError("AREA_TOO_LARGE", "Inference is limited to 16 million pixels; import a smaller pilot area or increase resolution.", 422)
        with WarpedVRT(src, crs=analysis_crs, transform=transform, width=width, height=height,
                       resampling=Resampling.average, add_alpha=src.count == 3) as vrt:
            rgb = np.moveaxis(vrt.read([1, 2, 3]), 0, -1)
            valid = vrt.dataset_mask() > 0
    return rgb, valid, transform


def inference(manifest, site_dir, payload, model, output, progress):
    import torch
    torch.set_num_threads(2); torch.manual_seed(0)
    # These modules are preserved historical code; only their reusable functions are called.
    sys.path.insert(0, str(ROOT / "scripts"))
    path = site_dir / manifest["imagery"]["cog_file"]
    if sha256(path) != manifest["imagery"]["cog_sha256"]:
        raise ApiError("IMAGERY_CHANGED", "COG differs from the imported site's hash.", 409)
    rgb, valid, transform = read_grid(path, manifest["analysis_crs"], payload["target_res_m"])
    progress("loading checkpoint", 0.2)
    checkpoint = Path(model["checkpoint_path"])
    if sha256(checkpoint) != model["checkpoint_sha256"]:
        raise ApiError("CHECKPOINT_CHANGED", "Checkpoint changed after the job was queued.", 409)
    if model["architecture"] == "cover_unet":
        from ..ml.cover import infer_cover
        if model.get("channels", 3) == 4:
            height = manifest.get("elevation", {}).get("ndsm", {})
            if height.get("vertical_units") != "metres" or not height.get("registration_reviewed"):
                raise ApiError("HEIGHT_UNAVAILABLE", "Height inference requires a reviewed, aligned nDSM.", 422)
            height_path = site_dir / height["file"]
            if sha256(height_path) != height["sha256"]:
                raise ApiError("HEIGHT_CHANGED", "nDSM differs from its reviewed source hash.", 409)
            with rasterio.open(height_path) as src, WarpedVRT(src, crs=manifest["analysis_crs"], transform=transform,
                    width=rgb.shape[1], height=rgb.shape[0], resampling=Resampling.bilinear) as vrt:
                z = vrt.read(1)
                valid &= (vrt.dataset_mask() > 0) & np.isfinite(z)
            rgb = np.dstack([rgb.astype(np.float32) / 255, np.clip(np.nan_to_num(z, nan=0), -5, 60) / 60])
        return infer_cover(model, rgb, valid, transform, manifest, payload, output, progress)
    from run_building_inference import vectorize, write_geotiff
    if model["architecture"] == "maskrcnn":
        from run_maskrcnn_inference import load_model, predict
        from postprocess_instances import polygonize
        loaded = load_model(checkpoint)
        progress("building instance inference", 0.35)
        instances, score = predict(loaded, rgb, batch=1)
        instances[~valid] = 0; score[~valid] = 0
        frame = polygonize(instances, score, transform, manifest["analysis_crs"], payload["min_area_m2"], payload["target_res_m"] / 2)
        write_geotiff(output / "instances.tif", instances, transform, manifest["analysis_crs"], "int32")
        method = "instance_segmentation"
    elif model["architecture"] == "whu":
        from run_whu_inference import load_model, predict
        loaded = load_model(checkpoint, model["model_config"])
        progress("building segmentation inference", 0.35)
        score = predict(loaded, rgb, window=512, overlap=128, batch=1)
        score[~valid] = 0
        frame = vectorize((score >= payload["threshold"]) & valid, score, transform, manifest["analysis_crs"],
                          payload["min_area_m2"], payload["target_res_m"] / 2)
        method = "semantic_segmentation"
    else:
        raise ApiError("UNSUPPORTED_MODEL", "Unsupported model architecture.", 422)
    write_geotiff(output / "score.tif", score, transform, manifest["analysis_crs"], "float32")
    features = []
    for i, (_, row) in enumerate(frame.to_crs("EPSG:4326").iterrows(), 1):
        # Pixel polygon simplification can round across the raster bounds; clip only to the declared extent.
        geom = row.geometry.intersection(box(*bounds_wgs(manifest)))
        if geom.is_empty: continue
        features.append({"type": "Feature", "geometry": mapping(geom), "properties": {
            "source_id": i, "feature_type": "building_footprint", "functional_use": "unknown",
            "score": float(row["mean_prob"]), "flags": "unvalidated_model", "class_name": "building"}})
    return {"type": "FeatureCollection", "features": features}, method
