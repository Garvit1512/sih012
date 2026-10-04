# WHU UNet++ vs DLinkNet34 on La Paz — visual comparison and evaluation

Status (2026-10-04): WHU inference run and validated; provisional labels reviewed by the
project user (guided window review) and scored — see "Results on the reviewed sample" below.

## WHU checkpoint (verified)
| Item | Value |
|---|---|
| Source | huggingface.co/giswqs/whu-building-unetplusplus-efficientnet-b4, revision `09df9efd323bbd3d56b98b4857129eb9b5baa2d3` (pinned in `models/whu/REVISION`) |
| File | `models/whu/model.pth`, 84,027,346 bytes, SHA-256 `922af7c9…cc30dc6` = Hugging Face LFS ETag |
| Licence | Apache-2.0 (model card); training data: WHU Building Dataset (Christchurch NZ aerial, 0.3 m) |
| Format | plain `state_dict` (838 tensors); pickle contains only tensor-rebuild globals → `torch.load(weights_only=True, map_location="cpu")`, strict load OK |
| Architecture | `smp.UnetPlusPlus("efficientnet-b4", classes=2)`, 20.8 M params |
| Preprocessing | RGB / 255, no mean/std (from `geoai.timm_segment.timm_semantic_segmentation`); softmax, class 1 = building |
| Card-reported metric | IoU 0.9054 on WHU test split — the authors' number on *their* data, not ours |

Environment: torch 2.14.1+cpu, torchvision 0.29.1+cpu, segmentation-models-pytorch 0.5.0, timm 1.0.30, rasterio 1.5.2, geopandas 1.2.0, shapely 2.1.2, numpy 2.5.3. Newly installed: `segmentation-models-pytorch`, `timm` (plus their deps, incl. huggingface-hub).

## Runs compared
| | DLinkNet34 (`outputs/lapaz_predio_bisa_0.4m`, unchanged) | WHU (`outputs/lapaz_predio_bisa_whu_0.3m`) |
|---|---|---|
| Inference grid | 0.4 m, 780×885 | 0.3 m, 1040×1180 |
| Tiles | 384 px, overlap 64, 9 tiles | 512 px, overlap 128, 9 windows |
| Normalisation | per-tile min-max + ImageNet | /255 |
| Threshold | sigmoid ≥ 0.4 | softmax(building) ≥ 0.5 |
| Post-processing | no-data → 0, min area 10 m², simplify 0.5 px | same (shared code) |
| CPU time (this machine) | 3.3 s total | 7.0 s inference, 8.3 s total |
| Building px / valid area | 3.7 % | 19.9 % |
| Polygons | 6 | 74 (median 75 m², max 2843 m²) |
| `validate_outputs.py` | all checks pass | all checks pass (round-trip IoU 0.990) |

## Visual comparison (`outputs/comparison/windows_comparison.png`, full scene `outputs/lapaz_predio_bisa_whu_0.3m/overlay.png`)
Observations only:
- DLinkNet detects essentially one roof in the four windows (the orange roof in W1).
- WHU outlines most roofs in all four windows, including the slate and clay-tile houses (W2), both large sheds (W3) and the roof under tree cover (W4).
- WHU failure modes seen: adjacent roofs merged into one polygon (W1, W3); rusty/red corrugated roofs often missed or partial (W1 bottom-right, W3 rust row); flat concrete slab roof missed (W1); some terracotta roofs missed elsewhere in the scene; a few blobs in gardens that look like false positives.
- Microsoft footprints appear visibly offset by several metres from this orthophoto and are coarser; consistent with different source imagery/date. Secondary reference only.

## Validation labels
- `data/validation/windows.json` / `windows.geojson`: 4 windows, 40×40 m, EPSG:32719, all 100 % valid pixels:
  W1 dense informal, W2 formal residential, W3 large sheds, W4 vegetation.
- `data/validation/provisional_labels.geojson`: the original draft, 38 polygons (22 high, 11 medium, 5 low confidence), `status=provisional_unreviewed`. Not modified after creation.
- Provenance: traced by the AI assistant (Claude) on native 0.05 m crops with a 2.5 m grid (`draft_pixel_polygons.json`, built by `scripts/build_provisional_labels.py`). No prediction layer was displayed while tracing, but the assistant had previously seen the full-scene model overlays, so independence is not perfect. Outlines are rectangle-like approximations (~0.25–0.5 m), roof outlines not wall footprints, and are clipped at window edges.
- Review aids: `data/validation/contact_sheet.png`, `data/validation/review/<window>.png`.

### Label review (2026-10-04)
- Backup of the untouched draft: `data/validation/provisional_labels.backup_20261004_010138.geojson` (SHA-256 `226c30d3…ddd2f4`, identical to `provisional_labels.geojson`).
- Reviewed file: `data/validation/reviewed_labels.geojson` — all 38 features `status=approved`, `review_date=2026-10-04`, `reviewed_by=project user`, `review_decision` per window, `geometry_edited=false` (the user accepted the drafted shapes; no vertices were changed).
- `ignore=true` (excluded from scoring): W1-14 concrete slab, W1-18 corner sliver, W2-02 sunroom, W2-04 barrel-vault walkway, W2-05 small tiled structure, W2-07 dark strip → 32 scored labels.
- W3-01/W3-02 and W3-04/W3-05 kept as separate buildings.
- Do not re-run `build_provisional_labels.py` without intent: it overwrites `provisional_labels.geojson` (the reviewed file is separate).

## Scoring method
`scripts/score_models.py` refuses to run unless all labels are `approved`. It scores each run's `buildings.geojson` per window on a common 0.1 m grid: pixel precision/recall/IoU (micro-averaged over windows, no-data and `ignore` regions excluded) and building-level precision/recall with one-to-one greedy matching at IoU ≥ 0.5 (pieces < 2 m² after clipping dropped; predictions >50 % inside ignore regions dropped). Microsoft footprints are reported in a separate "not ground truth" section. Mechanics verified by `tests/test_scoring.py` (synthetic known-answer cases, approval gate).

## Results on the reviewed sample (`outputs/evaluation/scores.md`, `scores.json`)
| run | pixel precision | pixel recall | pixel IoU | building precision | building recall | matched / pred / labels |
|---|---|---|---|---|---|---|
| DLinkNet34 @0.4 m | 0.966 | 0.028 | 0.028 | 1.000 | 0.031 | 1 / 1 / 32 |
| WHU UNet++ @0.3 m | 0.970 | 0.845 | 0.824 | 0.583 | 0.219 | 7 / 12 / 32 |
| *Microsoft (secondary, not truth)* | 0.659 | 0.608 | 0.463 | 0.000 | 0.000 | 0 / 24 / 32 |

WHU per window (pixel IoU; building recall): W1 0.741; 2/16 — W2 0.870; 2/3 — W3 0.837; 1/11 — W4 0.904; 2/2.

Interpretation and caveats:
- Sample is tiny: 4 windows (6,400 m²), 32 scored labels, one orthophoto. These numbers describe this sample only and do **not** establish general accuracy; a few polygons change them substantially.
- Labels are AI-drafted outlines (~0.25–0.5 m approximation) approved without geometry edits; the drafter had previously seen model overlays. Treat as a reviewed reference, not survey-grade ground truth.
- Pixel metrics are area-weighted and dominated by large roofs (W3 sheds ≈ 48 % of scored label area).
- WHU's low building-level recall is mostly merging: in W3 it produced 2 polygons for 11 labelled roofs, in W1 6 for 16. Separating adjacent roofs is the main gap, not detecting roof area.
- Microsoft scores 0 building matches mainly because its footprints are offset by several metres from this orthophoto.

## Commands
```
.venv\Scripts\python -m pip install segmentation-models-pytorch timm
# checkpoint (pinned revision)
curl -L -o models/whu/model.pth https://huggingface.co/giswqs/whu-building-unetplusplus-efficientnet-b4/resolve/09df9efd323bbd3d56b98b4857129eb9b5baa2d3/model.pth
curl -L -o models/whu/config.json https://huggingface.co/giswqs/whu-building-unetplusplus-efficientnet-b4/resolve/09df9efd323bbd3d56b98b4857129eb9b5baa2d3/config.json

.venv\Scripts\python scripts\run_whu_inference.py data\lapaz_predio_bisa.tif            # -> outputs\lapaz_predio_bisa_whu_0.3m
.venv\Scripts\python tests\validate_outputs.py outputs\lapaz_predio_bisa_whu_0.3m
.venv\Scripts\python scripts\build_provisional_labels.py                                # draft labels + contact sheet
.venv\Scripts\python scripts\fetch_ms_footprints.py data\lapaz_predio_bisa.tif --region Bolivia --out data\reference\ms_buildings_lapaz.geojson
.venv\Scripts\python tests\test_scoring.py
.venv\Scripts\python scripts\score_models.py --labels data\validation\reviewed_labels.geojson --run dlinknet=outputs\lapaz_predio_bisa_0.4m --run whu=outputs\lapaz_predio_bisa_whu_0.3m --secondary-reference data\reference\ms_buildings_lapaz.geojson
```

## Post-processing experiment v1: building-instance separation (2026-10-04)
Full write-up: `outputs/whu_postprocessed_v1/results.md`. Baseline files hashed beforehand (`outputs/whu_postprocessed_v1/baseline_hashes.sha256`, 22 files) and verified unchanged afterwards.

- Diagnosis of the 25 missed labels: 17 merged into larger predictions (W1: 7 roofs in one polygon + 3 in another; W3: 8 in one), 5 missed outright, 3 partially detected. The scorer was checked (CRS, ignore handling, one-to-one matching) and is not the cause.
- The WHU probability stays ≈1.0 across the shared walls of the merged roofs, so the mask carries no evidence to split them.
- Method: distance-transform cores (1.2 m) + geodesic region growing + merge-back unless the shared boundary is a real constriction (≤ 0.5 × smaller part's equivalent diameter, both parts ≥ 10 m²). Parameters fixed before evaluation. Building pixels are unchanged by construction. New dependency: scipy 1.18.1.
- Result: **identical scores to the baseline** (pixel P/R/IoU 0.970/0.845/0.824; building P/R 0.583/0.219; pixel TP/FP/FN and building TP/FP/FN identical in all four windows). The whole scene went from 74 to 84 polygons via splits at narrow necks, none of which created a match in the validation windows. **No improvement is claimed.**
- An initial `scipy.ndimage.watershed_ift` implementation was found (by a synthetic test) to flood across pixels outside the component; it was replaced, its outputs discarded, and a regression test added (`tests/test_postprocess.py`, 9 tests).
- Implication: separating touching roofs needs extra evidence (RGB edges/colour boundaries, or a model with boundary/instance output), each requiring its own pre-declared evaluation.

## RGB-guided separation experiment v1 (2026-10-04)
Full write-up: `outputs/rgb_guided_v1/results.md`. 57 preserved files hashed beforehand and verified unchanged.

- Inspection: inside merged roofs the strongest RGB edges are skylights, corrugation and roof planes; only some true seams show as colour changes, and grey-on-grey shared walls are not distinguishable from texture.
- Method (`scripts/rgb_guided_instances.py`): chromaticity (brightness-normalised colour) region growing inside the unchanged WHU mask, guard merges (parts < 10 m² or enclosed by one roof), cuts kept only with colour difference ≥ 0.04 and ≥ 30 % strong-edge support; every cut flagged `needs_review` with high/medium confidence. Parameters fixed before scoring; method design was informed by viewing W1/W3.
- Visual review before scoring: 3 cuts in the windows — 2 high cuts follow visible roof boundaries; 1 medium cut is partly wrong. Ridges and skylights were correctly not cut.
- Diagnostic scores (same scorer/labels): pixel P/R/IoU unchanged (0.970/0.845/0.824); building matches 7 → 10, building P 0.583 → 0.667, R 0.219 → 0.312. Two new matches come from high cuts; one (W3-03, IoU 0.51) from the partly wrong medium cut. 59 scene-wide cuts are unvalidated, and several medium ones are visibly wrong.
- Interpretation: colour evidence helps only where adjacent roofs differ in colour. The gain is on a tiny, design-contaminated sample and does not establish improvement. Next: an independent reviewed sample to measure gains and false cuts, and evaluating a boundary/instance-segmentation model on it.

## Human-reviewed RGB-guided output (2026-10-04)
Full write-up: `outputs/rgb_guided_reviewed/results.md`.

- All 62 proposed cuts were inventoried (association verified against `instances.tif`), rendered as native-resolution panels and reviewed from imagery: **30 accepted, 20 rejected, 12 uncertain** (AI proposals confirmed as-is by the project user). 5 of 27 high-confidence cuts were rejected (roof terraces, rooftop structures, continuous roofs).
- Reviewed predictions apply only accepted cuts: 102 polygons (baseline 74), building pixels and total area unchanged, unsplit areas identical to the baseline.
- Diagnostic W1–W4 (not independent): pixel P/R/IoU unchanged (0.970/0.845/0.824); building matches baseline 7, unreviewed 10, reviewed 9 (P 0.643, R 0.281, mean matched IoU 0.773). The lost match (W3-03) came from a cut rejected on imagery grounds.
- Independent validation is **pending**: four new windows V1–V4 and 65 provisional labels were prepared before cut review, but the labels were not approved, so no independent score exists.
- One preserved file (`scripts/rgb_guided_instances.py`) changed on disk outside this task's commands; it reproduces the v1 outputs byte-identically and is documented in `outputs/rgb_guided_reviewed/hash_exceptions.json`.
- Final review (2026-10-04): the user's reviewed `cut_review_proposals.csv` was validated (62 unique ids, valid statuses, no missing entries; file unchanged from the proposals) and confirmed final: 30 accepted / 20 rejected / 12 uncertain (uncertain kept unapplied). Recorded to `outputs/rgb_guided_reviewed/final/cut_review_final.geojson` and applied to `final/predictions/` — byte-identical to the earlier reviewed predictions; accepted-only application, baseline geometry for rejected/uncertain areas, mask/area/CRS/validity all verified. Diagnostic W1–W4 scores unchanged (building TP 9 vs baseline 7). Independent V1–V4 validation remains pending (labels not yet approved).

## Independent evaluation on V1–V4 (2026-10-04)
Separate from all W1–W4 diagnostic results above. Details: `outputs/rgb_guided_reviewed/results.md` (INDEPENDENT EVALUATION section).

- Reference labels for four new windows (dense informal, large sheds, residential, small roofs), drafted from raw imagery and reviewed window by window by the project user: 49 building labels (2 edited) + 25 ignore areas (9 uncertain merged-roof cases, 7 kept, 9 added); ignores clipped so they never cover confirmed buildings; provisional file preserved.
- Identical settings for both runs:

| run | pixel P / R / IoU | building P | building R | building TP / FP / FN |
|---|---|---|---|---|
| WHU baseline | 0.966 / 0.817 / 0.794 | 0.600 | 0.184 | 9 / 6 / 40 |
| RGB-guided reviewed (final) | 0.966 / 0.817 / 0.794 | 0.684 | 0.265 | 13 / 6 / 36 |

- All 4 extra matches come from two accepted cuts in V4 (one at IoU 0.53); V1 and V2 have zero matched buildings for both runs. Pixel metrics unchanged.
- Caveats: small sample (49 labels, one orthophoto); 9 hard merged-roof cases ignored; the same AI assistant drafted labels and cut proposals (user reviewed both). Not a measure of general accuracy.

### Final independent validation package (2026-10-04)
`outputs/independent_validation_final/` (`REPORT.md`, `comparison_V1-V4.png`, `scores/`, `reference/`, `verification.json`): reference split into approved buildings (49) and ignore areas (25), all status checks passed, scores re-run with identical settings and byte-identical to the earlier independent run (building TP 9 → 13 of 49; pixel metrics unchanged; V1/V2 zero matches). Since predictions on V1–V4 have now been viewed, future method design should use new held-out windows for its final test.

## Phase 3 — held-out T1–T4 and instance-aware methods (2026-10-04)
Full report: `outputs/phase3/REPORT.md`; protocol fixed before any T label/prediction: `outputs/phase3/PROTOCOL.md`.
- New held-out windows T1–T4 (≥ 10.9 m from all W/V windows); 30 user-approved buildings + 24 ignore areas.
- Held-out results (identical scorer settings): WHU A pixel IoU 0.572, building R 0.200 (6/30); approved RGB post-proc B 0.551 / 0.267; Mask R-CNN C@0.3 m 0.555 / 0.400 (12/30, building P 0.632); hybrid D 0.588 / 0.233.
- Mask R-CNN meets the pre-declared rule on T but failed it badly on development (pixel IoU 0.404 vs 0.809) → promising, not robust. The hybrid D looked best on development but its T gain was confined to one window → no demonstrated improvement. B fails by 0.001 on pixel IoU.
- Splitting methods lose scored pixel recall through the scorer's ">50 % inside ignore → drop piece" rule (documented, scorer unchanged).
- T1–T4 have now been viewed with predictions; a future final test needs new held-out data.

## Limitations
- One orthophoto, four 40 m windows, 32 scored labels: wide uncertainty.
- 0.3 m was chosen from the training resolution, not tuned; threshold 0.5 = argmax, not tuned.
- Microsoft footprints (ODbL, 187 features in extent, file dated 2026-02-23) are ML-derived with unknown imagery date and visible misregistration.
- All outputs are detected building/roof footprints. They are not cadastral parcels, and imagery alone cannot establish legal property boundaries.
