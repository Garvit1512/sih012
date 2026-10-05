"""Validate local orthophotos, preserve sources, and build portable site manifests."""

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling
from rasterio.shutil import copy as raster_copy
from rasterio.transform import from_bounds
from rasterio.vrt import WarpedVRT
from rasterio.warp import calculate_default_transform, transform_bounds
from shapely.geometry import box

from ..config import DISCLAIMER
from ..errors import ApiError
from ..utils.crs import metric_crs

MAX_UPLOAD_BYTES = 1024 * 1024 * 1024
MAX_PIXELS = 250_000_000


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def raster_info(path):
    try:
        with rasterio.open(path) as src:
            if src.driver != "GTiff" or not src.crs or src.transform.is_identity:
                raise ApiError("INVALID_IMAGERY", "A georeferenced GeoTIFF with a declared CRS/transform is required.", 422)
            if src.width * src.height > MAX_PIXELS:
                raise ApiError("IMAGE_TOO_LARGE", f"Imagery exceeds the {MAX_PIXELS:,}-pixel local processing limit.", 422)
            bounds = transform_bounds(src.crs, "EPSG:4326", *src.bounds, densify_pts=21)
            if not all(math.isfinite(x) for x in bounds) or not (-180 <= bounds[0] < bounds[2] <= 180 and -85 < bounds[1] < bounds[3] < 85):
                raise ApiError("INVALID_IMAGERY", "Raster bounds are unsuitable for the supported web map extent.", 422)
            return {"crs": src.crs.to_string(), "bounds": list(src.bounds), "bounds_wgs84": list(bounds),
                    "size_px": [src.width, src.height], "bands": src.count, "dtypes": list(src.dtypes),
                    "nodata": src.nodata if src.nodata is None or math.isfinite(src.nodata) else "NaN",
                    "resolution": list(src.res), "transform": list(src.transform)[:6]}
    except ApiError:
        raise
    except (rasterio.errors.RasterioError, ValueError) as e:
        raise ApiError("INVALID_IMAGERY", f"Cannot read the GeoTIFF: {e}", 422) from None


def quicklook(path, destination):
    with rasterio.open(path) as src:
        tf, w, h = calculate_default_transform(src.crs, "EPSG:3857", src.width, src.height, *src.bounds)
        scale = max(1, w / 1536, h / 1536)
        w, h = max(1, math.ceil(w / scale)), max(1, math.ceil(h / scale))
        tf = tf @ rasterio.Affine.scale(scale)
        with WarpedVRT(src, crs="EPSG:3857", transform=tf, width=w, height=h, resampling=Resampling.average, add_alpha=src.count == 3) as vrt:
            rgb = vrt.read([1, 2, 3])
            rgba = np.dstack([rgb.transpose(1, 2, 0), vrt.dataset_mask()])
            Image.fromarray(rgba).save(destination)
            west, south, east, north = transform_bounds(vrt.crs, "EPSG:4326", *vrt.bounds)
    return {"bounds_latlon": [[south, west], [north, east]], "size_px": [w, h],
            "resolution_m_3857": tf.a, "note": "Resampled display preview; full-resolution data is available through tiles."}


def ingest_site(settings, source, site_id, name, analysis_crs=None, license_name="unknown", source_url="",
                elevation=None, vertical_datum=None, acquired_on=None):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", site_id):
        raise ApiError("INVALID_ID", "Site id must use letters, digits, underscore or hyphen (1–60 characters).", 422)
    settings.sites_dir.mkdir(parents=True, exist_ok=True)
    final = settings.sites_dir / site_id
    if final.exists():
        raise ApiError("SITE_EXISTS", "This site already exists; use a new site/version id.", 409)
    info = raster_info(source)
    if info["bands"] < 3 or any(d != "uint8" for d in info["dtypes"][:3]):
        raise ApiError("INVALID_IMAGERY", "The local RGB workflow needs three uint8 bands. Convert other band formats explicitly.", 422)
    crs = metric_crs(analysis_crs or info["crs"])
    staging = Path(tempfile.mkdtemp(prefix=".ingest-", dir=settings.sites_dir))
    try:
        shutil.copyfile(source, staging / "source.tif")
        raster_copy(staging / "source.tif", staging / "imagery.cog.tif", driver="COG", BLOCKSIZE=512, COMPRESS="DEFLATE")
        ortho = quicklook(staging / "imagery.cog.tif", staging / "ortho_3857.png")
        ortho.update(source_crs=info["crs"], source_sha256=sha256(staging / "source.tif"))
        quality = {"flags": [], "absolute_accuracy": "unverified: no independent checkpoints supplied"}
        if not license_name.strip() or license_name.lower() == "unknown":
            quality["flags"].append("source_licence_unverified")
        elev = {}
        footprint = box(*info["bounds_wgs84"])
        for kind, path in (elevation or {}).items():
            meta = raster_info(path)
            if kind not in ("dsm", "dtm") or meta["bands"] != 1:
                raise ApiError("INVALID_ELEVATION", "Elevation inputs must be single-band DSM/DTM GeoTIFFs.", 422)
            if not box(*meta["bounds_wgs84"]).covers(footprint):
                raise ApiError("INVALID_ELEVATION", f"{kind.upper()} does not cover the imagery extent.", 422)
            aligned = meta["crs"] == info["crs"] and meta["transform"] == info["transform"] and meta["size_px"] == info["size_px"]
            shutil.copyfile(path, staging / f"{kind}.tif")
            elev[kind] = {**meta, "file": f"{kind}.tif", "sha256": sha256(path), "aligned": aligned,
                          "vertical_datum": vertical_datum, "vertical_units": "unverified"}
            if not aligned:
                quality["flags"].append(f"{kind}_requires_explicit_alignment")
            quality["flags"].append(f"{kind}_vertical_units_unverified")
        # Transform source bounds directly; a WGS84 bounding-box round trip enlarges off-meridian extents.
        bounds = transform_bounds(info["crs"], crs, *info["bounds"], densify_pts=21)
        extent = [round(bounds[2] - bounds[0], 2), round(bounds[3] - bounds[1], 2)]
        native_res = info["resolution"][0] if info["crs"] == crs else None
        manifest = {"schema_version": 2, "site_id": site_id, "name": name, "analysis_crs": crs,
                    "created_utc": datetime.now(timezone.utc).isoformat(), "source": {"url": source_url, "licence": license_name, "acquired_on": acquired_on},
                    "imagery": {"file": Path(source).name, "source_file": "source.tif", "cog_file": "imagery.cog.tif", "source_sha256": sha256(source),
                                "cog_sha256": sha256(staging / "imagery.cog.tif"),
                                "native_res_m": native_res, "size_px": info["size_px"], "extent_m": extent},
                    "raster": info, "ortho": ortho, "quality": quality, "elevation": elev, "layers": {},
                    "disclaimer": DISCLAIMER, "known_failure_modes": [], "flag_rules": {}, "evaluation_notes": [], "metrics_only": {}}
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2, allow_nan=False))
        (staging / "ortho.json").write_text(json.dumps(ortho, indent=2))
        os.rename(staging, final)
        return manifest
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def render_tile(catalog, z, x, y):
    if not (0 <= z <= 24 and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
        raise ApiError("INVALID_TILE", "Tile coordinates are outside the zoom grid.", 422)
    root = catalog.settings.data_dir
    if not catalog.manifest.get("imagery", {}).get("cog_file"):
        raise ApiError("TILES_UNAVAILABLE", "This legacy bundle has only a display preview.", 404)
    dest = root / "tile_cache" / str(z) / str(x) / f"{y}.png"
    if dest.exists():
        return dest
    edge = 20037508.342789244
    step = 2 * edge / 2 ** z
    bounds = (-edge + x * step, edge - (y + 1) * step, -edge + (x + 1) * step, edge - y * step)
    with rasterio.open(root / "imagery.cog.tif") as src:
        with WarpedVRT(src, crs="EPSG:3857", transform=from_bounds(*bounds, 256, 256), width=256, height=256,
                       resampling=Resampling.bilinear, add_alpha=src.count == 3) as vrt:
            rgba = np.dstack([vrt.read([1, 2, 3]).transpose(1, 2, 0), vrt.dataset_mask()])
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, suffix=".png")
    os.close(fd)
    try:
        Image.fromarray(rgba).save(tmp)
        os.replace(tmp, dest)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return dest
