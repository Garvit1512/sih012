# Geospatial backend (FastAPI)

File-backed backend around the existing review app. It does not replace `app/server.py` (kept unchanged and still
tested); it serves the same single-page UI and data bundle and adds real topology validation, a server-side review
workspace and validated export. Outputs are **building footprints, not legal cadastral parcel boundaries**.

## Run
```
.venv\Scripts\python scripts\build_app_data.py --out-dir app\data_v2      # once (already built)
.venv\Scripts\python -m uvicorn backend.main:app --host 127.0.0.1 --port 8765
```
Open http://127.0.0.1:8765 (UI), `/docs` (Swagger), `/redoc`. Without the backend, `python app/server.py` still works;
the UI then disables the topology toggle and says validation is unavailable.

Environment: `SIH_DATA_DIR` (default `app/data_v2`), `SIH_EXPORT_ROOT` (`outputs/app_exports`), `SIH_WORKSPACE_DIR`
(`outputs/backend_workspace`), `SIH_CORS_ORIGINS` (comma list; default local 8765/8000/5173 on 127.0.0.1 and localhost).
CORS is explicit, never `*`; the bundled UI is same-origin and needs none.

## Layout
`backend/` — `main.py` (app factory), `config.py` (settings + topology tolerances), `errors.py`, `models/` (Pydantic v2),
`services/` (site, imagery via site, layer, feature, topology, review, export), `routers/`, `utils/` (crs, geometry, validation).
Simpler than the originally proposed tree in two places: imagery lives in `site_service` (it is only metadata + one fixed
PNG) and there is one `Catalog` for the single site; no database.

## Endpoints
| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | liveness, data readiness |
| GET | `/api/sites`, `/api/sites/{id}` | metadata read from the bundle manifest: CRS, bounds, resolution, layers, counts, workspace summary, workflow state |
| GET | `/api/sites/{id}/imagery`, `/imagery/display` | display metadata; display PNG (EPSG:3857 warp). The source GeoTIFF is never served |
| GET | `/api/sites/{id}/layers[?include_metrics=true]` | `whu`, `rgb`, `maskrcnn` with counts/provenance; metrics only from the frozen manifest, labelled by split |
| GET | `/api/sites/{id}/layers/{layer}/features?bbox=&limit=&offset=` | immutable AI features, WGS84 GeoJSON, paged |
| GET | `/api/features/{id}` | inspector detail + topology relationships (`maskrcnn:12`, `ws-…`) |
| GET/POST | `/api/review/workspace` | list / copy AI features (`ai_copy`, `suggested`) |
| POST | `/api/review/features/{id}/accept\|reject\|edit\|split`, `/features/merge`, `/features/draw` | review operations (origins `ai_copy`, `human_edited`, `human_merged`, `human_split`, `human_drawn`) |
| POST | `/api/topology/validate`, `/validate-layer`, `/shared-boundary` | topology (below) |
| POST | `/api/export` | validated export to a new timestamped directory |

Errors: `{"error": {"code", "message", "details"}}` (e.g. `INVALID_GEOMETRY`, `INVALID_CRS`, `EMPTY_GEOMETRY`,
`COORDINATES_OUT_OF_RANGE`, `MERGE_NOT_CONTIGUOUS`, `SPLIT_INCOMPLETE`, `EXPORT_VALIDATION_FAILED`, `MALFORMED_JSON`). No stack traces.

State model: `IMAGERY_READY → LAYERS_AVAILABLE → REVIEWING → EXPORT_READY` are derived server-side
(`workflow.state` in the site detail). `IDLE, SITE_SELECTED, FLY_TO_SITE, ACTIVE_LAYER, TOPOLOGY_CONFLICTS, EDITING, EXPORTED`
are single-page UI modes; they are listed in `workflow.client_states` and are not routes.

## Topology (Shapely, EPSG:32719, metres)
Between *extracted building footprints* only. Tolerances live in `backend/config.py` and are echoed in every response (`tolerances`).
- `invalid_geometry` (error): not valid / empty (reason from `explain_validity`, point at the defect).
- `overlap`: intersection ≥ 0.25 m² and ≥ 5 % of the smaller footprint; `error` when ≥ 30 % of the smaller one, else `warning`.
- `duplicate` (error): IoU ≥ 0.90.
- `gap_sliver` (warning): gap < 0.5 m wide, ≥ 1 m long, ≥ 0.1 m², touching ≥ 2 footprints (closing of the union minus the union).
- `shared_boundary` is a *relationship*, not a conflict: boundaries within 0.30 m (same value as the display cue `touching_neighbour`) over ≥ 0.5 m.
  The reported run includes up to ~0.3 m of "hook" around each end because it is a buffer intersection; lengths are therefore
  an upper bound by up to ~2×0.3 m.
`/validate-layer` accepts an AI layer id or `workspace`. A layer with nothing found returns `{"valid": true, "conflicts": []}`.

Measured on the shipped bundle (not a quality claim about the models): WHU 0 conflicts / 0 shared edges; RGB 2 gap slivers / 29 shared edges;
Mask R-CNN 18 gap slivers / 20 shared edges; no overlaps or duplicates in any layer.

### Shared-boundary editing
`POST /api/topology/shared-boundary {feature_id, vertex:[lon,lat], features?: FeatureCollection}` returns the neighbouring footprint whose boundary is
within tolerance of the dragged vertex *and* shares ≥ 0.5 m of boundary with the edited one, plus that neighbour's shared edge (LineString).
The UI sends the current workspace inline (rejected features ignored), fires on Geoman `pm:markerdragstart`, flashes the edge for 200 ms and
continues editing. No shared boundary → `{"shared": false, "neighbor_feature_id": null, "edge": null}`.

## Validation rules
Client geometry is never trusted: polygonal, non-empty, finite, supported CRS (`EPSG:4326`, `EPSG:32719` only), plausible range and within the site
extent (+~2 km), valid. Invalid geometry is repaired only if the area change is ≤ 1 % (same rule as `app/server.py` export) and the repair is recorded;
otherwise it is rejected (e.g. a bow-tie). Export rejects the whole request, writing nothing, if any feature fails.
IDs are only dictionary keys, never paths; static mounts use Starlette `StaticFiles` (traversal blocked).

## Export
New directory `outputs/app_exports/<UTC>_<rand>/` (never overwrites): `buildings_reviewed_EPSG32719.geojson`, `buildings_reviewed_WGS84.geojson`,
`export_metadata.json` (counts by origin/status/source layer, topology summary and conflicts, input repairs, bundle manifest hash, disclaimer).
Overlap conflicts do not block export unless `require_topology_clean: true`.
Statement in every manifest: *These are AI-generated/human-reviewed building footprints and are NOT legal cadastral parcel boundaries.*

## Tests
`.venv\Scripts\python -m pytest tests/test_backend.py` (33 API/unit tests + a real-browser E2E that drags a vertex with CDP mouse events).

## Limitations
- One site (the La Paz bundle). `sf_church_st_sample` has no bundle and is not listed.
- Layer rendering in the UI still loads `/data/layer_*.geojson`; the paged feature API exists and is tested but the maps are not yet wired to it.
- The UI keeps the editable workspace in browser storage; the server-side workspace API is implemented and tested but not used by the UI.
- No automatic `flyTo` on load (initial view is `fitBounds`); the animation contract items for layer cross-fades and accept/export micro-animations are not implemented.
- Scores are the bundle's `score` field (mean WHU probability for A/B, instance score for C), not calibrated confidence.
- Topology results are relationships of unreviewed AI polygons; no evidence about cadastral topology or accuracy.
