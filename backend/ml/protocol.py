"""Validate reviewed, hashed spatial splits before an experiment can run."""
import json
from pathlib import Path

import numpy as np
import rasterio
from shapely.geometry import box

from ..services.ingestion_service import sha256
from ..utils.crs import metric_crs

SPLITS = {"train", "development", "evaluation"}


def read_manifest(path):
    path = Path(path).resolve()
    manifest = json.loads(path.read_text())
    if manifest.get("schema_version") != 1 or manifest.get("task") not in ("buildings", "cover"):
        raise ValueError("Use a version-1 buildings or cover manifest.")
    analysis = metric_crs(manifest["analysis_crs"])
    classes = manifest.get("classes", [])
    if manifest["task"] == "cover" and (not 2 <= len(classes) <= 32 or classes[0] != "background" or len(set(classes)) != len(classes)):
        raise ValueError("Cover needs 2–32 unique classes, starting with background.")
    if not manifest.get("licence") or manifest["licence"] == "unknown":
        raise ValueError("Record the permitted data licence before training.")
    samples, identities, groups, hashes = [], set(), {}, {}
    for row in manifest.get("samples", []):
        row = dict(row)
        if row["id"] in identities: raise ValueError("Duplicate sample id.")
        identities.add(row["id"])
        if row["split"] not in SPLITS or row.get("reviewed") is not True:
            raise ValueError("Every sample needs a declared split and reviewed labels.")
        if row["split"] == "evaluation" and row.get("labels_locked_before_predictions") is not True:
            raise ValueError("Evaluation labels must be locked before viewing predictions.")
        group = (row["site_id"], row["block_id"])
        if group in groups and groups[group] != row["split"]:
            raise ValueError("A spatial block cannot occur in multiple splits.")
        groups[group] = row["split"]
        for key in ("image", "labels"):
            local = (path.parent / row[key]).resolve()
            actual = sha256(local)
            if row.get(key + "_sha256") != actual:
                raise ValueError(f"{row['id']}: {key} hash missing or changed.")
            row[key] = str(local)
        if row["image_sha256"] in hashes and hashes[row["image_sha256"]] != row["split"]:
            raise ValueError("Identical imagery occurs in multiple splits.")
        hashes[row["image_sha256"]] = row["split"]
        with rasterio.open(row["image"]) as image, rasterio.open(row["labels"]) as labels:
            if image.crs is None or image.crs.to_string() != analysis or image.count != 3 or image.dtypes[0] != "uint8":
                raise ValueError("Training tiles need 3-band uint8 RGB in the metric analysis CRS.")
            if max(image.width, image.height) > 512 or min(image.width, image.height) < 32:
                raise ValueError("Training tiles must be 32–512 pixels per side.")
            if manifest["task"] == "cover" and (image.width % 32 or image.height % 32):
                raise ValueError("Cover tile dimensions must be multiples of the encoder stride (32 pixels).")
            if labels.crs != image.crs or labels.transform != image.transform or labels.shape != image.shape or labels.count != 1:
                raise ValueError("Labels must be on exactly the same georeferenced grid as imagery.")
            label = labels.read(1)
            if label.dtype.kind not in "ui": raise ValueError("Labels must use integer class/instance IDs.")
            valid = image.dataset_mask() > 0
            if manifest["task"] == "cover":
                values = np.unique(label[valid & (label != 255)])
                if len(values) and values.max() >= len(classes): raise ValueError("Label class is outside the declared schema.")
            elif not valid.all() or (label == 255).any():
                raise ValueError("Building training tiles must be fully reviewed and contain no void/no-data regions.")
            row["bounds"] = list(image.bounds)
        if row.get("ndsm"):
            if manifest.get("elevation_verified") is not True or manifest.get("vertical_units") != "metres" or not manifest.get("vertical_datum"):
                raise ValueError("nDSM requires verified alignment, vertical datum and metre units.")
            height = (path.parent / row["ndsm"]).resolve()
            if sha256(height) != row.get("ndsm_sha256"): raise ValueError("nDSM hash missing or changed.")
            with rasterio.open(height) as z, rasterio.open(row["image"]) as image:
                if z.crs != image.crs or z.transform != image.transform or z.shape != image.shape or z.count != 1:
                    raise ValueError("nDSM must be aligned with RGB.")
            row["ndsm"] = str(height)
        samples.append(row)
    if not samples or {s["split"] for s in samples} != SPLITS:
        raise ValueError("Provide reviewed train, development and fresh evaluation samples.")
    if any(s.get("ndsm") for s in samples) and not all(s.get("ndsm") for s in samples):
        raise ValueError("Supply aligned nDSM for every tile or run the RGB-only ablation.")
    buffer = float(manifest.get("spatial_buffer_m", 0))
    if not np.isfinite(buffer) or buffer < 0: raise ValueError("Spatial buffer must be non-negative metres.")
    for i, a in enumerate(samples):
        for b in samples[i + 1:]:
            if a["split"] == b["split"]: continue
            left, right = box(*a["bounds"]), box(*b["bounds"])
            if left.intersection(right).area > 1e-6 or (buffer and left.distance(right) < buffer):
                raise ValueError("Spatial leakage: imagery overlaps across splits or violates the declared buffer.")
    return {**manifest, "samples": samples, "manifest_sha256": sha256(path), "manifest_path": str(path)}
