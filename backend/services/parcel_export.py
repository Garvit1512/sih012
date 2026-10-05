"""Immutable saved-revision parcel/evidence export, including unresolved inputs."""
import hashlib
import json
import os
import secrets
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
from shapely.geometry import shape

from ..errors import ApiError
from .job_service import atomic_json


def export_parcels(store, settings, revision, states, clean=False):
    snapshot = store.snapshot(revision)
    diagnostics = store.diagnostics(snapshot)
    if clean and not diagnostics["valid"]:
        raise ApiError("PARCEL_CONFLICT", "Resolve parcel topology conflicts before a clean export.", 422)
    features = [f for f in snapshot["features"] if f["properties"]["feature_type"] != "parcel" or f["properties"]["parcel_state"] in states]
    if not features:
        raise ApiError("NOTHING_TO_EXPORT", "No saved parcel or evidence entities to export.", 422)
    root = settings.export_root
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".parcel-export-", dir=root))
    identifier = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + secrets.token_hex(4)
    try:
        atomic_json(staging / "parcels_and_evidence_WGS84.geojson", {"type": "FeatureCollection", "features": features})
        rows = [{"entity_id": f["id"], **{k: json.dumps(v, sort_keys=True) if isinstance(v, (dict, list)) else v for k, v in f["properties"].items()},
                 "geometry": shape(f["geometry"])} for f in features]
        frame = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326").to_crs(store.analysis_crs)
        for kind in sorted(frame.feature_type.unique()):
            frame[frame.feature_type == kind].to_file(staging / "parcel_products.gpkg", layer=kind, driver="GPKG")
        history = [e for e in store.history(1000000)["events"] if e["revision"] <= revision]
        atomic_json(staging / "revision_history.json", history)
        metadata = {"site_id": store.site_id, "parcel_revision": revision, "analysis_crs": store.analysis_crs, "states": states,
                    "entity_count": len(features), "diagnostics": diagnostics,
                    "disclaimer": "Preliminary parcel proposals and local evidence assertions; no ownership determination or legal/survey certification.",
                    "files": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in staging.iterdir()}}
        atomic_json(staging / "export_metadata.json", metadata)
        with zipfile.ZipFile(staging / "parcel_export.zip", "w", zipfile.ZIP_DEFLATED) as archive:
            for p in staging.iterdir():
                if p.name != "parcel_export.zip": archive.write(p, p.name)
        os.rename(staging, root / identifier)
        return {**metadata, "export_id": identifier, "download_url": f"/api/parcels/{store.site_id}/exports/{identifier}"}
    finally:
        if staging.exists(): shutil.rmtree(staging)
