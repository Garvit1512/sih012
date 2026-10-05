# Minimum Phase 4–5 software demo

This implements the requested time-limited scope: supported parcel proposals, local field-evidence states, shared-edge correction, saved GIS exports, a portable synthetic demo and an asset-free smoke command. It does not complete independent parcel/survey evaluation, controlled review-time measurement, a trained visible-boundary model or city-scale deployment.

The demo is already built and running in this workspace at `http://127.0.0.1:8776/?site=demo`. For a fresh checkout, use Python 3.12 and the core dependencies, then run the following commands (build only once into an empty/new directory):

```sh
.venv/bin/python -m backend.smoke
.venv/bin/python -m backend.demo build work/minimum-demo
.venv/bin/python -m backend.demo serve work/minimum-demo --port 8776
```

On Windows, replace `.venv/bin/python` with `.venv\Scripts\python.exe`. Open `http://127.0.0.1:8776/?site=demo`. All demo imagery/evidence is generated CC0 software-test material and is labelled synthetic. The demo has its own sites, jobs, models, workspaces and exports; existing work is preserved.

1. Expand **Parcel proposals & field evidence**. Import `work/minimum-demo/assets/boundaries.geojson` as Boundary lines. Set CRS `EPSG:32643`, source `synthetic fixture`, datum `WGS84 fixture`, a date, and check the evidence-review box.
2. Optionally import `references.geojson` as Reference parcels, then choose reference reconciliation. Generate supported parcels. Two closed faces are created; the separate open line remains unresolved.
3. Select a parcel. Enter a fixture record reference and save **Reviewed**. **Field checked** is blocked until current reviewed GNSS/ETS observations cover its boundary. Import `survey-left.geojson` or `survey-right.geojson` for the geometrically matching selected parcel, method GNSS, accuracy `0.1` m; then field-check it. These are synthetic assertions, not real survey results.
4. A shared-edge correction can be submitted as a GeoJSON LineString in the evidence CRS, for example `{"type":"LineString","coordinates":[[500012,2500002],[500013,2500012],[500012,2500022]]}`. Choose the two neighbours and provide a reference. Both geometries save together; field checks become stale. This local editor supports simple polygons without holes and preserves their combined outer boundary.
5. Export the saved parcel revision. Download the ZIP containing a projected GeoPackage, WGS84 GeoJSON, revision history and quality/provenance metadata. Open `parcel_products.gpkg` in QGIS when available. Separate layers retain reference parcels, observations and unresolved boundary inputs.
6. Reload or restart: parcel revisions and evidence remain. Stale mutations return HTTP 409. Building review and parcel evidence use separate, explicitly labelled revision journals.

For the real application use `python -m backend.cli serve`. UAVPal provides semantic cover/roof labels and DSM; it does not provide ownership parcels, functional-use labels, verified terrain or independent survey controls. Import actual permitted reference/evidence files rather than treating roofs as parcels.

The existing Phase 1–3 workflow remains documented in [LOCAL_WORKFLOW.md](LOCAL_WORKFLOW.md). Core API checks run without model packages. CI covers software fixtures; real model quality, positional accuracy and human effort remain separate evidence requirements.
