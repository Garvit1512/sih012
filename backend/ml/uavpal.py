"""Prepare immutable RGB-only UAVPal cover tiles on separated spatial bands.

python -m backend.ml.uavpal SOURCE OUTPUT --source-audit AUDIT_JSON
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from rasterio.enums import Resampling

from ..services.ingestion_service import sha256
from ..services.job_service import atomic_json, now
from .protocol import read_manifest

CLASSES = ["background", "water", "road", "car", "building", "tree"]
BANDS = {"train": range(0, 10), "development": range(13, 17), "evaluation": range(20, 23)}
ROWS = (0, 4, 8, 12, 16, 20, 22)


def prepare(source, output, audit_path, size=512):
    source, output, audit_path = Path(source).resolve(), Path(output).resolve(), Path(audit_path).resolve()
    if size not in (256, 512):
        raise ValueError("Choose a declared 256- or 512-pixel preparation grid.")
    if output.exists():
        raise ValueError("Refusing to overwrite a prepared dataset.")
    audit = json.loads(audit_path.read_text())
    if audit.get("dataset") != "UAVPal" or audit.get("checksum_failures") or audit.get("grid_errors"):
        raise ValueError("A successful UAVPal source audit is required.")
    files = {row["path"]: row for row in audit["files"]}
    selected = [(split, f"{y:02d}_{x:02d}") for split, columns in BANDS.items() for y in ROWS for x in columns]
    # Validate every selected source before creating any derivatives.
    sources = {}
    for _, tile in selected:
        for kind in ("Image", "Label"):
            relative = f"{kind}/Tiles/{tile}.tiff"
            path = source / relative
            expected = files.get(relative, {})
            if not expected.get("matches_publisher") or path.stat().st_size != expected.get("bytes"):
                raise ValueError(f"{relative}: missing or unverified publisher checksum.")
            digest = hashlib.sha1(path.read_bytes()).hexdigest()
            if digest != expected.get("sha1"):
                raise ValueError(f"{relative}: source changed after its audit.")
            sources[relative] = {"sha1": digest, "sha256": sha256(path)}
    output.mkdir(parents=True)
    samples, excluded, grids = [], [], set()
    counts = {split: np.zeros(len(CLASSES), dtype=np.int64) for split in BANDS}
    for split, tile in selected:
        image_path = source / f"Image/Tiles/{tile}.tiff"
        label_path = source / f"Label/Tiles/{tile}.tiff"
        with rasterio.open(image_path) as rgb, rasterio.open(label_path) as labels:
            if (rgb.crs is None or rgb.crs.to_epsg() != 32643 or rgb.count != 3 or rgb.dtypes != ("uint8",) * 3
                    or labels.count != 1 or labels.crs != rgb.crs or labels.transform != rgb.transform
                    or labels.shape != rgb.shape or labels.dtypes != ("uint8",)):
                raise ValueError(f"{tile}: source RGB/label grids differ from the audited contract.")
            pixels = rgb.read(out_shape=(3, size, size), resampling=Resampling.average)
            valid = rgb.dataset_mask(out_shape=(size, size), resampling=Resampling.nearest) > 0
            if valid.mean() < .5:
                excluded.append({"id": tile, "split": split, "reason": "less than 50% valid RGB coverage", "valid_fraction": float(valid.mean())})
                continue
            label = labels.read(1, out_shape=(size, size), resampling=Resampling.nearest)
            if np.any(label[valid] >= len(CLASSES)):
                raise ValueError(f"{tile}: unexpected semantic class.")
            # Label 0 is a valid background class; only RGB no-data becomes void.
            label[~valid] = 255
            counts[split] += np.bincount(label[label != 255], minlength=len(CLASSES))
            profile = {"driver": "GTiff", "width": size, "height": size, "crs": rgb.crs,
                       "transform": rgb.transform * Affine.scale(rgb.width / size, rgb.height / size),
                       "dtype": "uint8", "compress": "deflate"}
            grids.add((abs(rgb.transform.a), abs(profile["transform"].a)))
        image_name, label_name = f"rgb-{tile}.tif", f"labels-{tile}.tif"
        with rasterio.open(output / image_name, "w", count=3, **profile) as dst:
            dst.write(pixels)
            with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True):
                dst.write_mask(valid.astype(np.uint8) * 255)
        with rasterio.open(output / label_name, "w", count=1, nodata=255, **profile) as dst:
            dst.write(label, 1)
        samples.append({"id": tile, "site_id": "uavpal-bhopal", "block_id": f"band-{split}", "split": split,
                        "reviewed": True, "labels_locked_before_predictions": True,
                        "label_review_scope": "publisher annotations; class IDs, grid, checksums and void handling checked; no independent field review",
                        "image": image_name, "labels": label_name,
                        "image_sha256": sha256(output / image_name), "labels_sha256": sha256(output / label_name),
                        "sources": {kind: {"path": f"{kind}/Tiles/{tile}.tiff", **sources[f"{kind}/Tiles/{tile}.tiff"]}
                                    for kind in ("Image", "Label")}})
    if len(grids) != 1:
        raise ValueError("Prepared samples must share one declared resolution.")
    native_gsd, prepared_gsd = next(iter(grids))
    manifest = {"schema_version": 1, "task": "cover", "classes": CLASSES, "analysis_crs": "EPSG:32643",
                "licence": "CC BY-NC-SA 4.0; Faculty ITC, University of Twente, 2023",
                "source": "https://doi.org/10.17026/DANS-Z55-6GT4", "source_root": str(source),
                "source_audit_sha256": sha256(audit_path), "created_at": now(), "spatial_buffer_m": 100,
                "preparation": {"size": size, "rgb_resampling": "average", "labels_resampling": "nearest",
                    "void": "255 only where the nearest source RGB validity mask is false",
                    "native_gsd_m": native_gsd, "prepared_gsd_m": prepared_gsd,
                    "rows": list(ROWS), "columns": {k: list(v) for k, v in BANDS.items()},
                    "publisher_split_reused": False, "selection": "predeclared coordinate bands and rows; at least 50% valid RGB coverage; independent of predictions",
                    "height_used": False, "independently_reviewed_labels": False},
                "excluded_samples": excluded,
                "class_pixel_counts": {split: dict(zip(CLASSES, map(int, values))) for split, values in counts.items()},
                "limitations": ["One scene and one region per split; tiles are correlated within regions.",
                    "Downsampling limits thin-road and small-object detail; source files are preserved.",
                    "Semantic roofs are not instance, parcel or functional-use reference labels.",
                    "Published labels support a benchmark, not independent cadastral or survey validation."], "samples": samples}
    path = output / "manifest.json"
    atomic_json(path, manifest)
    checked = read_manifest(path)
    return {"manifest": str(path), "manifest_sha256": checked["manifest_sha256"],
            "sample_counts": {k: sum(s["split"] == k for s in samples) for k in BANDS},
            "class_pixel_counts": manifest["class_pixel_counts"], "prepared_gsd_m": manifest["preparation"]["prepared_gsd_m"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path); parser.add_argument("output", type=Path)
    parser.add_argument("--source-audit", type=Path, required=True)
    parser.add_argument("--size", type=int, choices=[256, 512], default=512)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.output, args.source_audit, args.size), indent=2))
