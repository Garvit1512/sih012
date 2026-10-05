# SIH Building-Footprint Extraction from Drone Imagery

Prototype for the Smart India Hackathon problem **"AI-Based Automated Urban Parcel Mapping and Cadastral
Feature Extraction System using Drone Imagery"**.

**Implemented scope:** extraction of **building footprints** from a georeferenced drone orthophoto, comparison
of several pretrained models and post-processing methods under a fixed evaluation protocol, and a local web
application for human review, correction and GeoJSON export.

> **Not cadastral boundaries.** The system produces AI-generated building footprints for review. It does
> **not** produce legal cadastral parcel boundaries and does not establish property ownership. That would
> require authoritative cadastral and survey reference data, which this project does not use.

---

## 1. Problem statement and motivation

Urban parcel and cadastral mapping is slow and survey-intensive. High-resolution drone orthophotos make it
possible to digitise visible features such as building roofs semi-automatically, then have a human verify
them. This prototype covers the first step: detecting building footprints, measuring how reliable different
models are on real drone imagery, and giving a reviewer tools to accept, correct or reject each footprint
before export.

**New local workflow:** start the FastAPI application without a dataset, import multiple sites, run local extraction jobs and save review revisions. [Setup and commands](docs/LOCAL_WORKFLOW.md) · [Phases 1–3 software progress](docs/SOFTWARE_PROGRESS.md). Model training and pilot accuracy validation still require reviewed local data and checkpoints.

## 2. Features

- **Inference (CPU)** with three pretrained models on a georeferenced GeoTIFF:
  - A: WHU UNet++ / EfficientNet-B4 semantic segmentation (`scripts/run_whu_inference.py`)
  - C: Mask R-CNN instance segmentation, geoai `building_footprints_usa.pth` (`scripts/run_maskrcnn_inference.py`)
  - DLinkNet34 (`scripts/run_building_inference.py`), the first baseline; much weaker on this imagery
- **Post-processing experiments**: constriction-based instance separation, RGB-guided cuts with a
  human-reviewed cut workflow (B), and a WHU + Mask R-CNN hybrid (D)
- **Geospatial output**: polygons in the source CRS (EPSG:32719) plus WGS84 GeoJSON, no-data masking,
  validity checks
- **Evaluation**: pixel precision/recall/IoU and one-to-one building matching (IoU ≥ 0.5) on reviewed reference
  windows, with separate development (W1–W4, V1–V4) and held-out (T1–T4) splits and hash-locked protocol files
- **Review web app** (`app/`): two synchronised maps, model comparison, metrics drawer, failure-mode cues,
  workspace editing (accept, reject, edit vertices, draw, merge, split), and export to EPSG:32719 + WGS84

## 3. Architecture and workflow

```
orthophoto (GeoTIFF, EPSG:32719, 0.05 m)
   │  resample → tile → model inference (CPU)
   ▼
probability / instance rasters ──► polygonise (source affine + CRS, min area 10 m², simplify)
   │                                    │
   │                                    ▼
   │                           buildings.geojson (EPSG:32719) + buildings_wgs84.geojson
   ▼
optional post-processing (B: reviewed RGB cuts · D: hybrid partition)
   │
   ├──► scripts/score_models.py  ── reviewed labels W/V/T ──► scores.json / scores.md
   │
   └──► scripts/build_app_data.py ─► app/data_v2 ─► app/server.py ─► browser review ─► export
                                                                       (outputs/app_exports/<timestamp>/)
```

## 4. Technology stack

- Python 3.12, PyTorch 2.14 (CPU), torchvision, segmentation-models-pytorch 0.5.0, timm
- rasterio, geopandas, shapely, pyproj, numpy, scipy, pandas, Pillow
- Web app: Python standard-library HTTP server; Leaflet 1.9.4, Leaflet-Geoman 2.17.0 and Turf 7.2.0
  (vendored in `app/static/vendor/`, so it works offline)
- Tests: plain Python test scripts; headless Edge/Chrome end-to-end test driven by Node.js over the DevTools Protocol

## 5. Repository structure

```
app/
  server.py                 local server: static app, data bundle, POST /api/export
  static/                   index.html, app.js, edit_ops.js, style.css, vendor/ (Leaflet, Geoman, Turf)
scripts/                    inference, post-processing, label building, scoring, app data bundle
tests/                      test suites + e2e_app.js (browser end-to-end)
data/
  validation/               W1–W4 windows, reviewed labels (development)
  validation_independent/   V1–V4 windows, reviewed labels and decisions (development, previously viewed)
  validation_test/          T1–T4 windows, reviewed labels and decisions (held-out test, scored once)
models/
  whu/                      config.json, README.md, REVISION (weights not included)
  maskrcnn_geoai/           REVISION, model-repo README (weights not included)
outputs/                    frozen model outputs (GeoJSON, rasters, run summaries), scores, reports
  phase3/                   PROTOCOL.md, protocol locks, held-out test scores, REPORT.md
docs/                       building_inference.md, whu_comparison.md, app.md
requirements.txt
```

### Not included in this repository

| Item | Expected local path | Reason |
|---|---|---|
| La Paz orthophoto | `data/lapaz_predio_bisa.tif` | Imagery provenance and licence are not verified for redistribution |
| San Francisco sample orthophoto | `data/sf_church_st_sample.tif` | Same |
| Model checkpoints | `models/best.pt`, `models/whu/model.pth`, `models/maskrcnn_geoai/building_footprints_usa.pth` | Size (84–375 MB); download from the sources below |
| App data bundle | `app/data_v2/` | Generated; contains a rendering of the orthophoto |
| Microsoft building footprints | `data/reference/ms_buildings_lapaz.geojson` | Third-party dataset (ODbL); fetch with the script below |
| Figures showing imagery | overlays, contact sheets, review crops (`*.png` under `outputs/` and `data/validation*/`) | Show the orthophoto |
| Review exports | `outputs/app_exports/` | Local working output |

Frozen `run_summary.json` and score files contain absolute paths from the original machine (`E:\sih012\...`).
They are kept unchanged because they are hash-locked evaluation artifacts.

## 6. Installation (Windows, PowerShell)

```powershell
git clone <repository-url> sih012
cd sih012
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install torch==2.14.1 torchvision==0.29.1 --index-url https://download.pytorch.org/whl/cpu
.venv\Scripts\python -m pip install -r requirements.txt
```
Node.js (v22 was used) and Microsoft Edge or Google Chrome are needed only for the browser end-to-end test.

### Models (download separately)

| Model | Source (pinned) | Save as | SHA-256 | Licence |
|---|---|---|---|---|
| WHU UNet++ EfficientNet-B4 | `https://huggingface.co/giswqs/whu-building-unetplusplus-efficientnet-b4/resolve/09df9efd323bbd3d56b98b4857129eb9b5baa2d3/model.pth` | `models/whu/model.pth` | `922af7c96c0dc44256ab8b4d1a071f2151e0a921c997af80b55bb766bcc30dc6` | Apache-2.0 (model card) |
| Mask R-CNN (geoai) | `https://huggingface.co/giswqs/geoai/resolve/aa2b25d27b2c78f0e92c7b6a4813a571f9bc2288/building_footprints_usa.pth` | `models/maskrcnn_geoai/building_footprints_usa.pth` | `3aea5d0da7803be31ff4e2e1e5ac747f15cab2a13d6bb1a9319b6a6ba8bc7bca` | MIT (repository metadata) |
| DLinkNet34 (Massachusetts) | `https://github.com/fuzailpalnak/building-footprint-segmentation/releases/download/alpha/DlinkNet.zip` → extract `best.pt` | `models/best.pt` | `18b890983015b9bd757796419f4c3d715d4add4b3bf56985fe186e557ba431dd` | package licence: MIT |

```powershell
curl.exe -L -o models\whu\model.pth "https://huggingface.co/giswqs/whu-building-unetplusplus-efficientnet-b4/resolve/09df9efd323bbd3d56b98b4857129eb9b5baa2d3/model.pth"
curl.exe -L -o models\maskrcnn_geoai\building_footprints_usa.pth "https://huggingface.co/giswqs/geoai/resolve/aa2b25d27b2c78f0e92c7b6a4813a571f9bc2288/building_footprints_usa.pth"
Get-FileHash models\whu\model.pth -Algorithm SHA256
```

### Imagery

Place the orthophoto at `data/lapaz_predio_bisa.tif` (expected SHA-256
`31e5c1220eaffd36a3b9b80382a7d6fe9e1d758e0ce7c9e95c9a0ce804815069`; EPSG:32719, 0.05 m, 6240 × 7080 px, RGB,
produced with Pix4Dmapper). The original download source is not recorded in this repository. Other imagery can
be processed by the inference scripts, but the evaluation results below apply only to this orthophoto.

## 7. Running the application

```powershell
.venv\Scripts\python scripts\build_app_data.py --out-dir app\data_v2     # once; needs the orthophoto
.venv\Scripts\python app\server.py --data-dir app\data_v2                # serves http://127.0.0.1:8765
```
Open **http://127.0.0.1:8765**. The server refuses to start if port 8765 is already in use; stop the other
server or pass `--port`.

## 8. Using the interface

- **Model comparison (left map):** choose A (WHU baseline), B (RGB post-processing) or C (Mask R-CNN
  candidate). Optionally show evaluation windows or highlight failure-mode cues. Both maps stay synchronised.
- **Review workflow (right map):**
  1. Choose a candidate layer.
  2. Copy polygons into the workspace: either the clicked (highlighted) AI polygon or all polygons in view.
     AI layers are never modified.
  3. Select polygons (click; Shift+click to add to the selection), then Accept, Reject or Reset; Edit vertices,
     Draw, Merge (two or more) or Split (exactly one; draw a line across it). Esc cancels a tool.
  4. Export the chosen statuses. This writes `buildings_reviewed_EPSG32719.geojson`,
     `buildings_reviewed_WGS84.geojson` and `export_metadata.json` to a new `outputs/app_exports/<UTC>_<id>/`.
- Every workspace feature records its `origin` (`ai_copy`, `human_edited`, `human_merged`, `human_split`,
  `human_drawn`), `review_status`, source model, source feature ID and parents. Every exported feature carries
  a "not legal cadastral boundaries" disclaimer.
- **Evaluation drawer:** held-out test and development metrics are shown on separate tabs.

## 9. Evaluation methodology and results

- Reference labels: building outlines drafted by an AI assistant from the raw orthophoto, then reviewed window
  by window by the project author. Ambiguous structures are marked as ignore areas. These are not survey data.
- Scoring (`scripts/score_models.py`, identical for every method): 0.1 m evaluation grid; no-data and ignore
  areas excluded; one-to-one greedy building matching at IoU ≥ 0.5; pieces < 2 m² dropped; all methods use a
  10 m² output minimum.
- Protocol: `outputs/phase3/PROTOCOL.md` was fixed and hash-locked before any T1–T4 label or prediction existed.
  One dated amendment was made before T labels were approved. T1–T4 were scored once.

### Held-out windows T1–T4

| Method | Pixel IoU | Building precision | Building recall | Matched buildings |
|---|---:|---:|---:|---:|
| A — WHU baseline | 0.572 | 0.429 | 0.200 | 6 |
| B — RGB post-processing | 0.551 | 0.421 | 0.267 | 8 |
| C — Mask R-CNN @0.3 m | 0.555 | 0.632 | 0.400 | 12 |
| D — Hybrid | 0.588 | 0.368 | 0.233 | 7 |

These results cover only **30 reference buildings in four spatially separate 40 × 40 m windows of one
orthophoto**. They do **not** establish city-wide accuracy or generalisation to other imagery, and they say
nothing about cadastral accuracy.

**Interpretation:**
- Mask R-CNN separates individual buildings better at building level, but it covers less roof area, and on the
  development windows its pixel IoU was far lower (0.404 vs 0.809). Under the pre-declared rule it was
  **not promoted**; it remains a candidate.
- The hybrid did not show a reliable held-out improvement.
- No method demonstrated a robust improvement over the WHU baseline across both development and held-out data.
- T1–T4 have since been viewed with predictions, so they are no longer unseen test data.

Details: `outputs/phase3/REPORT.md`, `docs/whu_comparison.md`, `outputs/independent_validation_final/REPORT.md`.

## 10. Limitations and known failure modes

- **Missed roofs:** small, rusty-red corrugated, patchwork and edge-clipped roofs often get WHU probability
  close to 0.
- **Merged buildings:** adjacent roofs sharing walls are merged by WHU. Gaps between roofs are often
  0.1–0.4 m, at or below the 0.3 m inference resolution.
- **Over-splitting:** Mask R-CNN can split one large, complex roof into several pieces.
- **Domain shift:** Mask R-CNN was trained on North American NAIP imagery (0.6 m); WHU on Christchurch aerial
  imagery (0.3 m).
- **Small evaluation:** 30 held-out and 81 development reference buildings from one orthophoto; labels are
  AI-drafted and human-reviewed, not authoritative.
- **Prototype app:** single user, local files, orthophoto displayed as one image rather than tiles.

## 11. Data, model provenance and licences

- Model sources and licences: see the Models table. Leaflet (BSD-2-Clause), Leaflet-Geoman free (MIT) and
  Turf (MIT) are vendored with hashes in `app/static/vendor/VENDOR_SHA256.txt`.
- Microsoft Global ML Building Footprints (ODbL) are used only as a secondary reference, never as ground truth:
  `.venv\Scripts\python scripts\fetch_ms_footprints.py data\lapaz_predio_bisa.tif --region Bolivia --out data\reference\ms_buildings_lapaz.geojson`
- Orthophoto source and licence: **not recorded / not verified**; imagery is therefore not distributed here.
- This repository does not currently include a licence file for the project's own code.

## 12. Reproducibility and tests

The full test suites expect the local orthophoto and checkpoints at the paths above, because they verify
frozen hashes and re-read source data.
```powershell
.venv\Scripts\python tests\test_scoring.py
.venv\Scripts\python tests\test_postprocess.py
.venv\Scripts\python tests\test_rgb_guided.py
.venv\Scripts\python tests\test_cut_review.py
.venv\Scripts\python tests\test_independent_labels.py
.venv\Scripts\python tests\test_finalize_independent.py
.venv\Scripts\python tests\test_frozen_eval.py
.venv\Scripts\python tests\test_phase3.py
.venv\Scripts\python tests\test_app.py        # includes the headless-browser end-to-end test
.venv\Scripts\python tests\validate_outputs.py outputs\lapaz_predio_bisa_whu_0.3m outputs\phase3\maskrcnn_0.3m
```
Frozen artifacts are listed in `outputs/phase3/frozen_hashes.sha256`. Protocol files are locked by
`outputs/phase3/protocol_lock*.sha256`.

## 13. Future work

The [five-phase development roadmap](docs/ROADMAP.md) connects the existing prototype to the full SIH26012
scope, with implementation tasks, dependencies and completion gates for each remaining phase.

- Fine-tune an instance- or boundary-aware model on locally reviewed roofs, and evaluate it on new held-out
  windows (ideally from different imagery).
- Collect more reference buildings and authoritative survey data before any cadastral use.
- Tile-based orthophoto serving and multi-user review persistence.

## 14. Responsible use

Outputs are AI-assisted building footprints intended for human review. They must not be used as legal
cadastral parcel boundaries, as evidence of property ownership, or for decisions about individuals' property
without verification against authoritative records and qualified surveyors. Reported accuracy applies only to
the small evaluation sample described above.

## Backend (FastAPI)
See `docs/backend.md`: `.venv\Scripts\python -m uvicorn backend.main:app --port 8765` serves the UI plus topology validation, review workspace and validated export APIs (`/docs`).

## Minimum parcel demo (Phases 4–5)

The local application now supports evidence-backed preliminary parcel proposals, field-evidence states, shared-edge correction and saved-revision GIS ZIP exports. Run `python -m backend.smoke`, then `python -m backend.demo build work/minimum-demo` and `python -m backend.demo serve work/minimum-demo --port 8776`. The demo uses labelled synthetic fixtures. See [the short demo guide](docs/MINIMUM_DEMO.md) for the workflow and explicitly deferred validation.

## Hosted demo

The [deployment guide](docs/DEPLOYMENT.md) configures the Vercel frontend and a password-protected Render backend. The free Render demo uses synthetic imagery and temporary storage; export work before a restart. Persistent review and model inference need separately provisioned storage/runtime resources. Cloud deployment does not establish model or cadastral accuracy.
