# Building-footprint review app (demo prototype)

A local web app to compare AI building-footprint layers on the La Paz orthophoto, review and correct
them, and export reviewed footprints. **Outputs are building footprints, not legal cadastral parcel
boundaries**; no parcel data was used or is produced.

There was no frontend/backend in this repository before (2026-10-04 audit); this is a new, minimal app.
No new Python dependencies; JS libraries are vendored in `app/static/vendor/` (see `VENDOR.md`) so the
demo works offline.

## Run
```
.venv\Scripts\python scripts\build_app_data.py --out-dir app\data_v2   # once; refuses a non-empty dir
.venv\Scripts\python app\server.py --data-dir app\data_v2              # http://127.0.0.1:8765
```
`app/data` (phase-3 bundle without hybrid metrics or imagery block) is kept unchanged; the Phase-4 UI uses
`app/data_v2`. To rebuild, build into a new directory and pass it with `--data-dir <dir>`.
On Windows the server refuses to start if the port is already in use (previously a second server could bind
the same port silently, so requests could reach a stale server).

## End-to-end workflow (orthophoto → polygons → review → export)
1. Inference (existing, frozen outputs): WHU `scripts/run_whu_inference.py` (A), approved RGB-guided post-processing (B, `outputs/rgb_guided_reviewed/final/predictions`), Mask R-CNN `scripts/run_maskrcnn_inference.py --target-res 0.3` (C).
2. `scripts/build_app_data.py` reads those outputs **read-only** and writes the display bundle: orthophoto warped to Web Mercator (display only), layers in EPSG:4326, metrics, provenance hashes.
3. `app/server.py` serves the app; the browser shows two synchronized maps.
4. Review in the right map; export writes a new `outputs/app_exports/<UTC>_<id>/` folder.

## Features
- **Compare (left map)**: choose layer A, B or C; optional evaluation-window outlines (green = held-out test T1–T4, amber = development W/V).
- **Review (right map)**: choose a candidate layer; copy single AI polygons ("Add clicked AI polygon") or all in view into the workspace. AI layers are never modified.
- **Workspace actions**: Accept / Reject / Reset, Edit vertices, Draw new, Merge (Shift+click to multi-select), Split (draw a line across a polygon; 2 cm cut, area loss shown), Remove. Every feature carries `origin` (`ai_copy`, `human_edited`, `human_merged`, `human_split`, `human_drawn`), `review_status`, source layer/id and parents. Workspace autosaves to browser storage when available.
- **Counts**: total / in-view / cue-flagged polygons per layer; workspace counts by status and origin.
- **Metrics**: copied verbatim from frozen score files. "Held-out test T1–T4" (scored once under `outputs/phase3/PROTOCOL.md`) and "Development W1–W4 / V1–V4" (already viewed) are shown separately with a banner. Protocol conclusion displayed: no method shows a robust improvement over A; C was not promoted.
- **Uncertainty / failure modes**: per-polygon score (WHU mean probability for A/B, instance score for C) and heuristic cues (`small_roof` < 20 m², `possible_merged_buildings` A/B > 300 m², `touching_neighbour` / `possible_over_split` within 0.3 m, `low_score` < 0.7) — display aids, **not evaluated error labels**; known failure modes listed with their measured evidence.
- **Export**: choose statuses (default accepted only). Server validates geometry, origin and status values and lon/lat ranges, writes `buildings_reviewed_EPSG32719.geojson` (analysis CRS), `buildings_reviewed_WGS84.geojson` (RFC 7946) and `export_metadata.json`; every feature carries a "not legal cadastral boundaries" disclaimer. Geometries that become invalid after projection are repaired in EPSG:32719 (recorded in metadata; export refused if area changes > 1 %). "Download WGS84" saves a client-side copy without the server.

## Tests
`python tests/test_app.py` — builds the bundle into a temp dir, checks metrics/geometry/provenance against frozen sources, export API (CRS round trip, new directory per export, rejection of invalid / non-lon-lat / unknown-origin input, path traversal), editing logic in Node, a **headless-browser end-to-end run** of the real UI (Edge/Chrome via DevTools Protocol, `tests/e2e_app.js`: add 125 candidates → accept → reject → merge → split → edit → draw → export, no JS errors), and frozen-artifact hashes.

## Known limitations / blockers
- Prototype for one orthophoto; no authentication, multi-user editing or database (exports are files).
- The orthophoto is shown as one warped PNG (≈ 0.2 m display resolution), not map tiles.
- Interactive use was verified with a scripted headless browser; the Claude-in-Chrome extension was not connected, so no manual click-through by the assistant.
- Cues are heuristics; metrics are from small samples (30 test / 81 development labels) and do not establish general accuracy.
- Building footprints ≠ cadastral parcels: legal boundaries need authoritative parcel/survey data.

## Phase 4 UI redesign (2026-10-04)
- **Shell**: "FOOTPRINT INTELLIGENCE" app bar (location, imagery file, GSD, analysis CRS, mode chip, server/data status, Evaluation and Export actions); the cadastral disclaimer stays visible at every width.
- **Design system** (`app/static/style.css`): dark navy/charcoal surfaces, teal accent for active/primary, amber for review-required and tool modes, red only for destructive/error; system font stack (Inter not installed or vendored → Segoe UI on Windows); inline SVG icons (offline).
- **Sidebar**: collapsible sections — Project (values read from the raster: file, 0.05 m GSD, 312 × 354 m, EPSG:32719), Model comparison (layer cards with active indicator and counts, evaluation-window and cue toggles), Review workflow in four numbered steps, Selected-footprint inspector (provenance, source model/ID, score, area, cues — only values that exist), Known failure modes, Legend.
- **Maps**: same two synchronized Leaflet maps, dark surround, floating titles and per-map legends, zoom/scale bottom-right; distinct styles for AI layers, suggested, accepted, rejected, human-edited and selected; the clicked AI polygon is highlighted before "Add". No change to georeferencing, CRS or geometry logic.
- **Review UX**: buttons enabled only when valid (e.g. Merge needs ≥ 2, Edit/Split exactly 1, Export needs exportable features); explicit tool-mode banner with Finish/Cancel and Esc; toasts for success/warnings/errors; workspace pills (status counts, autosave, unexported changes); browser warns before leaving with unexported edits or an open edit.
- **Evaluation drawer**: tabs for held-out T1–T4 vs development W1–W4 / V1–V4 (development explicitly "not test metrics"); cards for A, B, C (candidate · not promoted) and D (hybrid, metrics only); per-window table; banner states 4 held-out windows of one orthophoto, 30 reference buildings, no city-wide or cadastral accuracy.
- Responsive: maps stack below 1080 px, sidebar/header reflow below 760 px.
