"""Small declared UAVPal-shaped fixtures exercise real preparation safeguards."""
import hashlib
import json

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from backend.ml import uavpal
from backend.ml.protocol import read_manifest
from backend.services.ingestion_service import sha256


def test_preparation_preserves_background_void_grid_and_source_hashes(tmp_path, monkeypatch):
    monkeypatch.setattr(uavpal, "BANDS", {"train": range(1), "development": range(13, 14), "evaluation": range(20, 21)})
    monkeypatch.setattr(uavpal, "ROWS", (0,))
    source = tmp_path / "source"
    files = []
    for x in (0, 13, 20):
        for kind in ("Image", "Label"):
            path = source / kind / "Tiles" / f"00_{x:02d}.tiff"
            path.parent.mkdir(parents=True, exist_ok=True)
            profile = dict(driver="GTiff", width=64, height=64, dtype="uint8", nodata=0,
                           crs="EPSG:32643", transform=from_origin(746000 + x * 64, 2570000, 1, 1))
            array = np.full((3, 64, 64), 20 + x, np.uint8) if kind == "Image" else np.zeros((1, 64, 64), np.uint8)
            array[:, 0, 0] = 0
            if kind == "Label": array[0, 20:30, 20:30] = 4
            with rasterio.open(path, "w", count=len(array), **profile) as dst: dst.write(array)
            files.append({"path": str(path.relative_to(source)), "bytes": path.stat().st_size,
                          "sha1": hashlib.sha1(path.read_bytes()).hexdigest(), "matches_publisher": True})
    audit = tmp_path / "audit.json"
    audit.write_text(json.dumps({"dataset": "UAVPal", "checksum_failures": [], "grid_errors": [], "files": files}))
    original = {row["path"]: sha256(source / row["path"]) for row in files}
    output = tmp_path / "prepared"
    summary = uavpal.prepare(source, output, audit, 256)
    assert summary["sample_counts"] == dict(train=1, development=1, evaluation=1)
    assert summary["prepared_gsd_m"] == .25
    manifest = read_manifest(output / "manifest.json")
    assert manifest["spatial_buffer_m"] == 100 and manifest["preparation"]["independently_reviewed_labels"] is False
    for row in manifest["samples"]:
        with rasterio.open(row["image"]) as image, rasterio.open(row["labels"]) as labels:
            assert image.shape == labels.shape == (256, 256)
            assert image.transform == labels.transform and labels.nodata == 255
            mask = labels.read(1)
            assert mask[0, 0] == 255 and mask[40, 40] == 0 and mask[90, 90] == 4
            assert image.dataset_mask()[0, 0] == 0 and image.dataset_mask()[40, 40] == 255
    assert {row["path"]: sha256(source / row["path"]) for row in files} == original
    with pytest.raises(ValueError, match="overwrite"): uavpal.prepare(source, output, audit, 256)
    changed = source / files[0]["path"]
    with rasterio.open(changed, "r+") as dst:
        data = dst.read(); data[:, 10, 10] = 100; dst.write(data)
    with pytest.raises(ValueError, match="source changed"): uavpal.prepare(source, tmp_path / "changed", audit, 256)
    assert not (tmp_path / "changed").exists()
