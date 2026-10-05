"""Build/serve a small openly redistributable synthetic software demo.

python -m backend.demo build work/demo
python -m backend.demo serve work/demo --port 8776
"""
import argparse
import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import LineString, box, mapping

from .config import Settings
from .services.ingestion_service import ingest_site
from .services.job_service import atomic_json


def settings_for(root):
    root = Path(root).resolve()
    return Settings(data_dir=root / "legacy", sites_dir=root / "sites", workspace_dir=root / "workspaces",
                    jobs_dir=root / "jobs", export_root=root / "exports", models_dir=root / "models")


def collection(geometries):
    return {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": mapping(g),
            "properties": {"source": "synthetic software fixture; not surveyed parcels"}} for g in geometries]}


def build(root):
    root = Path(root).resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError("Refusing to overwrite a non-empty demo directory.")
    assets = root / "assets"; assets.mkdir(parents=True, exist_ok=True)
    rgb = np.zeros((3, 64, 64), np.uint8); rgb[:] = np.array([85, 120, 78])[:, None, None]
    rgb[:, 12:40, 12:26] = np.array([170, 145, 125])[:, None, None]
    rgb[:, 12:40, 30:44] = np.array([180, 150, 125])[:, None, None]
    rgb[:, :, 50:60] = 105
    image = assets / "synthetic_rgb.tif"
    with rasterio.open(image, "w", driver="GTiff", width=64, height=64, count=3, dtype="uint8", crs="EPSG:32643",
                       transform=from_origin(500000, 2500032, .5, .5)) as dst: dst.write(rgb)
    x, y = 500002, 2500002
    left, right = box(x,y,x+10,y+20), box(x+10,y,x+20,y+20)
    data = {
        "boundaries.geojson": collection([LineString([(x,y),(x+10,y),(x+20,y),(x+20,y+20),(x+10,y+20),(x,y+20),(x,y)]),
                                         LineString([(x+10,y),(x+10,y+20)]), LineString([(x+25,y),(x+25,y+5)])]),
        "references.geojson": collection([left, right]),
        "survey-left.geojson": collection([LineString(left.exterior.coords)]),
        "survey-right.geojson": collection([LineString(right.exterior.coords)]),
        "survey-extent.geojson": collection([box(500001,2500001,500031,2500031)]),
        "excluded-road.geojson": collection([box(500026,2500001,500030,2500031)]),
    }
    for name, content in data.items(): atomic_json(assets / name, content)
    (assets / "LICENSE.txt").write_text("CC0-1.0: generated synthetic imagery and geometry for software demonstration. No real observations, ownership, parcel accuracy or model accuracy are represented.\n")
    ingest_site(settings_for(root), image, "demo", "Synthetic software demo — not survey data", license_name="CC0 synthetic software fixture")
    return {"root": str(root), "site_id": "demo", "assets": str(assets), "synthetic": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["build", "serve"]); parser.add_argument("root", type=Path)
    parser.add_argument("--port", type=int, default=8776)
    args = parser.parse_args()
    if args.action == "build": print(json.dumps(build(args.root), indent=2))
    else:
        import uvicorn
        from .main import create_app
        if not (args.root / "sites/demo/manifest.json").is_file(): parser.error("Build the demo first.")
        uvicorn.run(create_app(settings_for(args.root)), host="127.0.0.1", port=args.port)
