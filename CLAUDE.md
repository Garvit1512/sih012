# CLAUDE.md — SIH Building Footprint Extraction System

## 1. Project Overview

**Project:** AI-Based Automated Urban Parcel Mapping and Cadastral Feature Extraction System using Drone Imagery

**Current implemented scope:** Building footprint extraction from georeferenced orthophotography, model comparison, geospatial polygon generation, and human review.

**Important terminology rule:** The current system extracts **building footprints**, not legal cadastral parcel boundaries. Never claim that predicted building outlines represent property ownership boundaries or legally authoritative cadastral parcels. Such claims require appropriate official cadastral and survey reference data.

## 2. Primary Objectives

- Extract building footprints from high-resolution orthophotos.
- Compare semantic segmentation, instance segmentation, and post-processing methods.
- Preserve geospatial coordinates and export usable vector geometry.
- Enable human review and correction of AI-generated polygons.
- Communicate model performance, uncertainty, and failure modes honestly.
- Maintain a reproducible, demonstrable SIH prototype.

Prioritize a reliable end-to-end workflow over unnecessary model experimentation.

## 3. Environment and Conventions

- Operating system: Windows.
- Project root: `E:\sih012`.
- Python environment: `.venv`.
- Python version: 3.12.
- Run Python scripts using `.venv\Scripts\python`.
- Use Windows-compatible paths and commands.
- Inspect the existing repository before assuming file names, functions, dependencies, or directory structure.
- Reuse installed dependencies where practical. Do not reinstall or upgrade packages without a clear reason.
- Do not assume a CUDA GPU is available. CPU inference must remain supported.

Example:

```powershell
.venv\Scripts\python scripts\score_models.py --help
.venv\Scripts\python -m pytest
```

Use the project's existing test commands if they differ from these examples.

## 4. Data and Model Background

### Input imagery

The principal orthophoto is:

`data/lapaz_predio_bisa.tif`

Known characteristics:
- La Paz, Bolivia.
- CRS: EPSG:32719.
- Original pixel size: approximately 0.05 m.
- RGB imagery.
- Contains no-data collar areas.

A secondary image exists at:

`data/sf_church_st_sample.tif`

Do not assume that models generalize between locations, image resolutions, sensors, or acquisition conditions.

### Method A — WHU semantic segmentation baseline

- UNet++ with EfficientNet-B4.
- Frozen prediction output: `outputs/lapaz_predio_bisa_whu_0.3m`.
- Produces building masks that are converted to polygons.
- Known issues include missed buildings and merging adjacent roofs.

### Method B — RGB-guided post-processing

- Approved output: `outputs/rgb_guided_reviewed/final/predictions`.
- Attempts to separate building instances using image-guided cuts.
- Improvements on development windows did not transfer robustly to the held-out test.

### Method C — Mask R-CNN instance segmentation

- Script: `scripts/run_maskrcnn_inference.py`.
- Checkpoint repository: `giswqs/geoai`.
- Held-out candidate output: `outputs/phase3/maskrcnn_0.3m`.
- Better building-level matching than WHU on the current test, but misses roof area and can over-split large complex roofs.
- Model was trained on North American NAIP imagery. Domain shift to the La Paz orthophoto is a significant limitation.

### Method D — WHU + Mask R-CNN hybrid

- Script: `scripts/hybrid_instance_partition.py`.
- Uses WHU to determine building pixels and Mask R-CNN instances to partition them.
- Output: `outputs/phase3/hybrid_whu_maskrcnn`.
- Did not demonstrate a reliable improvement on the held-out test.

Always inspect the actual repository and outputs before running any inference or modifying these methods.

## 5. Evaluation Status — Phase 3

The project has separate development and held-out evaluation windows.

- Development windows: W1–W4 and V1–V4.
- Held-out windows: T1–T4.
- T windows have already been labelled, scored, and visually inspected. **They are no longer unseen test data.**
- The evaluation protocol and its amendment are documented in the repository.
- Reference labels were AI-drafted and human-reviewed, not derived from authoritative survey data.
- The test consists of only 30 reference buildings across four windows from the same orthophoto.

### Held-out T1–T4 results

| Method | Pixel IoU | Building precision | Building recall | Matched buildings |
|---|---:|---:|---:|---:|
| A — WHU baseline | 0.572 | 0.429 | 0.200 | 6 |
| B — RGB post-processing | 0.551 | 0.421 | 0.267 | 8 |
| C — Mask R-CNN at 0.3 m | 0.555 | 0.632 | 0.400 | 12 |
| D — Hybrid | 0.588 | 0.368 | 0.233 | 7 |

Interpretation:
- Mask R-CNN is promising for separating individual building footprints.
- Its pixel-level coverage is limited.
- The hybrid method did not demonstrate a reliable held-out improvement.
- No method has demonstrated a robust improvement across both development and held-out data.

Do not describe these results as city-wide accuracy, cadastral accuracy, or generalization to other imagery.

## 6. Immutable Artifacts and Evaluation Integrity

Before editing, identify and preserve:

- `frozen_hashes.sha256`
- `PROTOCOL.md`
- `protocol_lock.sha256`
- `protocol_lock_amend1.sha256`
- Reviewed development and test labels.
- Existing model weights and frozen predictions.
- Existing evaluation outputs and comparison images.
- Existing tests and review decisions.

Do not:
- Modify approved labels to make predictions look better.
- Reuse T1–T4 for further tuning and then call them unseen test data.
- Change thresholds, minimum areas, ignore-area rules, or scoring logic without an explicitly documented new experiment.
- Overwrite non-empty output directories.
- Regenerate frozen baselines silently.
- Delete artifacts just because they appear temporary or redundant.
- Rewrite hashes to conceal changes.

If a frozen artifact genuinely needs to change, stop first, explain why, and propose a new versioned artifact and documented experiment. Keep original files intact.

## 7. Development Workflow

For every substantial task:

1. **Audit:** Inspect relevant files, repository status, dependencies, outputs, and tests.
2. **Plan:** List the exact files to create or change and explain why.
3. **Protect:** Confirm that frozen data, labels, predictions, and protocol files will remain untouched.
4. **Implement:** Make small, focused, reversible changes.
5. **Verify:** Run relevant tests, type checks, lint checks, or reproducibility checks available in the repository.
6. **Report:** Summarize changed files, commands executed, test results, limitations, and remaining work.

Do not claim a test passed unless it was actually executed and passed. Distinguish tested behavior from expected behavior.

Prefer minimal changes over large rewrites. Avoid unrelated refactoring, unnecessary dependency changes, and speculative features.

## 8. Application and Demonstration Priorities

When working on the frontend or backend, prioritize:

1. Loading a georeferenced orthophoto correctly.
2. Displaying footprint polygons at their correct geographic positions.
3. Switching between WHU, RGB post-processing, and Mask R-CNN predictions.
4. Comparing methods without conflating development and test metrics.
5. Showing polygon counts, confidence information when available, and model limitations.
6. Supporting human review: accept, reject, edit, merge, and split footprints where implemented.
7. Exporting GeoJSON with correct CRS and geometry.
8. Showing progress, errors, loading states, and empty states clearly.
9. Maintaining a reliable workflow that can be demonstrated without manual code edits during the presentation.

Keep model predictions distinct from human-edited outputs. Never imply that human review or legal validation occurred when it did not.

## 9. Geospatial Requirements

- Respect source CRS and raster transforms.
- Check CRS compatibility before overlaying rasters and vector data.
- Preserve geometry validity during polygon conversion and export.
- Handle no-data regions explicitly.
- Document simplification, area thresholds, clipping, and other geometric operations.
- Do not silently assign a CRS to data whose CRS is unknown.
- Do not treat Microsoft building footprints as authoritative cadastral ground truth; they are only a secondary reference in this project.

## 10. Future Model Experiments

Any future training, fine-tuning, or post-processing experiment must:

- Use a new experiment name and output directory.
- Keep frozen model outputs untouched.
- Record parameters, input resolution, checkpoint provenance, and preprocessing.
- Separate development data from genuinely new evaluation data.
- Establish reference labels before viewing predictions on new test windows.
- Use the existing scorer consistently where appropriate.
- Report precision, recall, IoU, TP, FP, and FN together.
- State sample size and dataset limitations.
- Avoid selecting parameters based on a test set and then reporting that same set as independent evaluation.

Fine-tuning on locally reviewed roofs may be explored as a future hypothesis, but the existing reviewed labels are limited and should not be treated as sufficient evidence of generalization.

## 11. Final Reporting Standard

At the end of each task, report:

- What was inspected.
- What was changed.
- Files created or modified.
- Commands actually executed.
- Tests that passed or failed.
- Relevant quantitative results.
- Known limitations and unverified behavior.
- The next recommended action.

Use direct, technically precise language. Never fabricate metrics, files, model capabilities, screenshots, test results, or completed features.

**Guiding principle:** Build a reliable and reproducible building-footprint extraction and human-review system. Preserve evidence, measure honestly, and distinguish AI-generated footprints from legally authoritative cadastral boundaries.