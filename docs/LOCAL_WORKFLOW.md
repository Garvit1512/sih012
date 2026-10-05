# Local software workflow

This workflow starts without a dataset. Imagery acquisition, checkpoint restoration and measured accuracy remain separate gates. The historical La Paz bundle and frozen research artifacts retain their original paths and protocols.

## Start the application

Run from the cloned `sih012` repository root using Python 3.12. On macOS/Linux:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-app.txt
.venv/bin/python -m backend.cli doctor
.venv/bin/python -m backend.cli serve
```

On Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-app.txt
.venv\Scripts\python -m backend.cli doctor
.venv\Scripts\python -m backend.cli serve
```

Open `http://127.0.0.1:8765`. The empty screen offers **Add a site**. This backend serves the interface and API together. Run one process/worker: the job queue and JSON workspace locks are designed for a local pilot. The reviewer field is a self-declared label, not an authenticated identity.

The optional model runtime is separate:

```sh
.venv/bin/python -m pip install -r requirements-models.txt
```

Use `.venv\Scripts\python` for the equivalent Windows commands. The existing README's CPU installation guidance applies on Windows/Linux. Installation does not download checkpoints. `/api/models` reports missing packages/checkpoints. Original WHU and Mask R-CNN files remain in their documented `models/` locations; locally trained candidates use new IDs under `models/local/`.

## Import a site

The UI accepts a georeferenced, uint8 RGB GeoTIFF, a site ID/name, licence and source URL, and optional single-band DSM/DTM files. Analysis requires a projected CRS with metre units; geographic source imagery needs an explicit projected analysis CRS. Source files are preserved, hashed and copied into a new directory. Ingestion creates a COG, quicklook and cached map tiles.

The CLI is equivalent:

```sh
.venv/bin/python -m backend.cli import-site /path/to/rgb.tif pilot-01 \
  --name "Pilot 01" --analysis-crs EPSG:32643 --licence "Recorded source licence"
```

Choose the CRS appropriate to the site's location; `EPSG:32643` is an example, not an India-wide default. Horizontal accuracy remains unverified until independent checkpoints are supplied. Unknown licence or unverified elevation metadata remains visible in the manifest. Current local limits are 1 GiB per upload, 250 million source pixels, 16 million inference pixels and 5,000 polygons per extraction/export.

New state defaults to `outputs/sites`, `outputs/jobs`, `outputs/backend_workspace` and `outputs/app_exports`. These paths are ignored by Git. Override them with `SIH_SITES_DIR`, `SIH_JOBS_DIR`, `SIH_WORKSPACE_DIR` and `SIH_EXPORT_ROOT`. `SIH_DATA_DIR` continues to identify the historical display bundle.

`SIH_MODELS_DIR` can select a separate local model root, defaulting to `models/`. When using it, register candidates with `backend.ml.register --model-root /path/to/model-root` so the API and worker use that same inventory. Browser QA creates its temporary models under the isolated fixture directory.

## Extract and review

The extraction panel can run available local model candidates or import prediction GeoJSON with an explicit input CRS. Imports and inference run through the same worker/publication path. A job keeps its specification, progress, log, immutable output directory and result hashes. The panel offers cancellation, retry, output details and artifact downloads; completed results can be loaded into the maps.

Failure/cancellation never registers a partial layer. Retry creates a new job/output ID. On application restart, queued jobs resume; interrupted running jobs are identified for retry. If a completed layer was published immediately before interruption, the ledger reconciles it from the site manifest.

For a registered functional-use candidate, choose its model plus existing building and road-surface input layers, then click **Generate use suggestions**. This job freezes its input feature snapshots, derives metric shape/road/density attributes and publishes a separate candidate layer. The inspector shows its suggested class and provenance, while active functional use remains unknown. Accepting a polygon alone never promotes the suggestion. Record reviewed source references using the use-review form before assigning a class.

AI features remain immutable. **Clicked polygon** or **All in view** creates suggested workspace copies. Drawing, editing, merging and splitting save on the server. Geometry changes require another review before acceptance. Merge requires a connected polygon and matching feature/cover classes. Split retains the existing 2 cm cut rule and records area loss and parent IDs.

Each API mutation includes `expected_revision`; stale writes receive HTTP 409. The interface reloads the current workspace after a failed/stale change. History preserves actor label, reason, timestamp, reported interaction interval, and before/after features. The timer is a UI interaction interval, not a validated measurement of active human labour. **Next suggested** prioritizes display cues; cues are not evaluated error labels.

The export revision control defaults to the latest saved revision and can select an earlier one. GeoJSON and GeoPackage exports come from that server snapshot, include revision/history/manifest provenance, and use the site's metric CRS plus WGS84. Historical geometry remains exportable after later edits or removal. `require_topology_clean=true` can block an API export on topology conflicts. Overlap checks compare like feature types; extracted features are not legal parcel boundaries.

Select a single workspace building to record **Functional use** in the inspector. Supply the locally agreed class and reviewed record/observation references, one per line. A known label requires references; `unknown` is valid without them. The save records the reviewer label, source references, workspace revision and geometry hash, and returns the feature to suggested status for acceptance. A geometry edit clears the active use to unknown and marks the old assertion as needing review. Merge/split outputs also start with unknown use; parent history retains the earlier assertions. Saved revisions and exports preserve the use record. This is a reviewer assertion: references are not automatically fetched or independently authenticated, and accepting geometry does not restore a stale use label.

## Prepare a future training experiment

The software expects reviewed local tiles and labels; no training data has been acquired in this work. A version-1 manifest declares `task` (`buildings` or `cover`), `analysis_crs`, `licence`, optional `spatial_buffer_m`, and samples. Cover also declares unique `classes`, starting with `background`. Example:

```json
{
  "schema_version": 1,
  "task": "cover",
  "analysis_crs": "EPSG:32643",
  "licence": "Record the source's permitted licence",
  "classes": ["background", "building", "road", "tree", "water", "bare_soil"],
  "spatial_buffer_m": 50,
  "samples": [
    {
      "id": "tile-001", "site_id": "pilot-01", "block_id": "block-a",
      "split": "train", "reviewed": true,
      "image": "tiles/tile-001.tif", "labels": "labels/tile-001.tif",
      "image_sha256": "actual file hash", "labels_sha256": "actual file hash"
    }
  ]
}
```

This is a schema example, not a runnable dataset. Supply train, development and fresh evaluation samples. Evaluation samples require `labels_locked_before_predictions: true`. Hashes, tile grids, label classes, spatial blocks, duplicate imagery and cross-split overlap/buffer distances are validated before training. Historical W/V/T samples are already viewed; do not describe them as fresh evaluation data.

Tiles use uint8 RGB in the declared metric CRS and are 32–512 pixels per side; cover dimensions are multiples of 32. Labels are single-band integer GeoTIFFs on exactly the same grid. Cover uses class IDs and 255 for void. Building labels use instance IDs with 0 background; building training tiles must be fully reviewed, contain no void/no-data, and reserve 255. Make label validity and licence decisions before supplying the manifest.

## Train, evaluate and register candidates

The building tool fine-tunes the existing Mask R-CNN from a local checkpoint with its backbone frozen. The cover tool provides a supervised ResNet18 U-Net research baseline, initialized without downloads. Neither is presented as an evaluated Indian pilot model.

```sh
.venv/bin/python -m backend.ml.train /path/to/manifest.json outputs/experiments/cover-v1 \
  --architecture cover_unet --epochs 20
.venv/bin/python -m backend.ml.train /path/to/buildings.json outputs/experiments/buildings-v1 \
  --architecture maskrcnn --checkpoint /path/to/starting-weights.pth --epochs 20
.venv/bin/python -m backend.ml.evaluate outputs/experiments/cover-v1 outputs/experiments/cover-v1/evaluation
.venv/bin/python -m backend.ml.register outputs/experiments/cover-v1 local-cover-v1 --name "Local cover candidate v1"
```

Experiments refuse non-empty destinations, freeze the dataset/parameters, record versions and checkpoint hashes, and save training history. Cover selection uses development argmax macro IoU; building selection uses the predeclared final epoch. Fresh evaluation uses frozen score/mask thresholds and tolerances, preserves prediction rasters, and reports confusion matrices, per-class metrics, unknown coverage and per-block results. Building evaluation adds matched instances and boundary distances. Road scoring derives centerlines from reviewed surface masks; connectivity counts are diagnostics, not evidence of legal access or independent route accuracy. Changing inference thresholds/area filters needs a separately frozen comparison.

Registration creates a **candidate** model; it does not promote it. Choose promotion criteria and collect fresh-reference evidence before declaring improvement over the historical candidates. Boundary-aware WHU fine-tuning remains conditional on measured Mask R-CNN residuals, as specified in the roadmap.

## Optional height and functional-use experiments

DSM/DTM ingestion alone enables no height claims. `backend.ml.elevation` requires exact alignment with RGB, metre units, a stated vertical datum, registration review, a reviewer and source evidence before creating a new nDSM. The metadata records the assertion; independent vertical accuracy is still unestablished.

```sh
.venv/bin/python -m backend.ml.elevation outputs/sites/pilot-01 \
  --vertical-datum "Recorded source datum" --registration-reviewed \
  --reviewer "Reviewer label" --evidence "Source metadata and registration review reference"
```

For a cover height ablation, all training samples need aligned, hashed `ndsm` files and manifest fields `elevation_verified: true`, `vertical_units: "metres"`, and `vertical_datum`. Train with `--use-height`. RGB/height preprocessing is fixed and recorded; a registered height candidate also requires a reviewed site nDSM at inference. Compare RGB-only and height candidates on the same frozen reference blocks.

`backend.ml.functional_use` supplies metric building/road attributes and a nearest-centroid research baseline with rejection to **unknown**. Image cover never assigns residential/commercial use or ownership. Manual use corrections use the inspector and saved revision workflow; the later parcel/field-evidence workflow remains Phase 4 work.

Use `backend.ml.use_experiment` for a separately frozen use experiment. Its version-1 JSON manifest declares `task: "functional_use"`, a metric `analysis_crs`, permitted `licence`, two or more locally agreed `classes`, `spatial_buffer_m` and `samples`. Every sample needs `id`, `site_id`, `block_id`, `split`, `reviewed: true`, reviewed `evidence_ids`, a `use_label`, `geometry_sha256` and `bounds_analysis` in that CRS. Compute the four attributes from declared building/road inputs: `area_m2`, `compactness`, `distance_to_road_m`, and `neighbours_within_50m`. Source geometry hashes, locations and reference assertions must faithfully describe the reviewed features; they are not independently authenticated by this tool.

Provide train, development and fresh evaluation blocks. Every known training class needs at least two reviewed examples with complete finite attributes. Evaluation labels require `labels_locked_before_predictions: true`; missing evaluation attributes cause rejection to unknown. Validation rejects duplicate geometry across splits, shared spatial blocks and cross-split overlap/buffer violations. The rejection distance and ambiguity rule are fixed before evaluation; changing them requires a new experiment.

```sh
.venv/bin/python -m backend.ml.use_experiment train /path/to/use-manifest.json outputs/experiments/use-v1 --max-distance 3
.venv/bin/python -m backend.ml.use_experiment evaluate outputs/experiments/use-v1 outputs/experiments/use-v1/evaluation
.venv/bin/python -m backend.ml.register outputs/experiments/use-v1 local-use-v1 --name "Local use candidate v1"
```

The evaluation preserves predictions, class confusion/precision/recall/F1, per-block results, unknown fraction and input/model/protocol hashes. It refuses changed frozen inputs and non-empty outputs. The nearest-centroid model is a bounded baseline; suitability and improvement need real labelled references, class coverage and fresh evaluation.

## Software checks and preservation

```sh
.venv/bin/python -m pytest tests/test_site_workflow.py tests/test_jobs.py tests/test_use_review.py tests/test_use_experiment.py tests/test_ml_workflow.py -q
.venv/bin/python -m compileall -q backend
.venv/bin/python -m backend.cli check-frozen
```

The tests generate declared synthetic fixtures. The browser test has a separate empty fixture server:

```sh
.venv/bin/python tests/serve_site_fixture.py --root /path/to/empty-fixture-directory --port 8771
node tests/e2e_site_workflow.js http://127.0.0.1:8771 /path/to/chrome /path/to/browser-profile /path/to/empty-fixture-directory/synthetic.tif
```

To include the use-classification job in browser QA, start a fresh fixture server with `--use-candidate` and add `use` as the browser command's final argument. The server trains an explicitly synthetic temporary candidate in the fixture root; it does not touch the application model inventory.

CI runs the lightweight ingestion/review/worker tests without external imagery or checkpoints. The model workflow tests require `requirements-models.txt`. CI was added; hosted CI execution is not claimed here.

Older frozen inventories have discrepancies already present in the cloned commit, and some omitted binary assets are absent. `check-frozen` reports these honestly instead of changing hashes. The amended protocol lock matches. See [software progress](SOFTWARE_PROGRESS.md) for the validation record and remaining real-data gates.
