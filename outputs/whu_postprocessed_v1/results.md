# WHU post-processing v1 — conservative building-instance separation

**Outcome: no measured change.** On the 32 reviewed labels, v1 scores exactly the same as the
WHU baseline (pixel TP/FP/FN identical, same 7 building matches). The method behaves as designed
— it only splits at genuine constrictions — and the merged roofs in the validation windows have
no constriction to split at. No improvement is claimed.

## Why building-level recall is low (diagnosis, before any change)
Of 32 scored labels, 7 match (IoU ≥ 0.5). The 25 misses:

| cause | labels | detail |
|---|---|---|
| merged into one larger prediction (label ≥ 50 % covered, prediction spans ≥ 2 labels) | 17 | W1: one 405 m² polygon spans 7 roofs, another spans 3; W3: one 1,118 m² polygon spans 8 |
| missed (no overlap) | 5 | W1-08 rusty roof, W1-15, W2-06, W3-09, W3-10 (small / edge-clipped / rusty) |
| partial (< 50 % covered) | 3 | W1-12, W1-16, W1-17 |

- Boundary accuracy is not the problem: isolated buildings (W2, W4) match at IoU 0.79–0.92.
- Scorer verified: same CRS (EPSG:32719) for labels, windows and predictions; 6 `ignore=true` labels excluded from truth and their areas from pixel counts; one-to-one matching means a merged polygon can match at most one label — correct behaviour, not a bug.
- Evidence check: in the WHU probability raster the merged W1 roofs (shared walls) and W3 sheds stay at ≈1.0 across the seams — the model gives no signal there; visible separation is only colour/texture in the RGB.

## Method (`scripts/postprocess_instances.py`)
Input: the baseline run directory, read-only (`probability.tif`, `mask.tif`, `run_summary.json`).
1. Re-threshold `probability.tif` at the baseline threshold (0.5) and assert it equals the baseline `mask.tif`.
2. 4-connected components (same as the baseline vectorizer).
3. Per component: Euclidean distance transform; markers = cores farther than **1.2 m** from the mask edge. 0–1 cores → unchanged.
4. Geodesic region growing from the cores, strictly inside the component (a watershed on distance-to-cores); every mask pixel is assigned, so building pixels are unchanged (asserted).
5. Merge-back: adjacent parts stay separate only if the shared boundary ≤ **0.5 ×** the smaller part's equivalent diameter **and** both parts ≥ **10 m²**; otherwise merged. Touching roofs without a neck therefore stay one polygon.
6. Polygonize per instance with the source transform/CRS, simplify 0.15 m (= baseline), drop < 10 m² (= baseline), attach mean probability.

Parameters were fixed before evaluation and not tuned on the validation labels.
Optional confidence filtering was not applied (it cannot separate buildings; `mean_prob` is kept as an attribute).

Implementation note: an earlier draft used `scipy.ndimage.watershed_ift`; a synthetic test showed it flooding across pixels outside the component (two identical halves got 1204 vs 676 px). It was replaced by explicit region growing, and `test_flooding_never_crosses_outside_pixels` guards against regressions. The first (buggy) run's outputs were deleted before the final run.

## Results (same labels, same 0.1 m grid, same ignore areas, IoU ≥ 0.5, unchanged `score_models.py`)
| run | pixel P | pixel R | pixel IoU | building P | building R | pixel TP / FP / FN | building TP / FP / FN |
|---|---|---|---|---|---|---|---|
| WHU baseline | 0.970 | 0.845 | 0.824 | 0.583 | 0.219 | 231,364 / 7,038 / 42,340 | 7 / 5 / 25 |
| WHU post-processed v1 | 0.970 | 0.845 | 0.824 | 0.583 | 0.219 | 231,364 / 7,038 / 42,340 | 7 / 5 / 25 |
| *Microsoft (secondary, not truth)* | 0.659 | 0.608 | 0.463 | 0.000 | 0.000 | — | 0 / 24 / 32 |

Per window (identical for both runs): W1 pixel IoU 0.741, buildings 2 matched / 6 predicted / 16 labels · W2 0.870, 2/2/3 · W3 0.837, 1/2/11 · W4 0.904, 2/2/2.

Whole scene: 123 components → 133 instances → 84 polygons (baseline 74). The 10 extra splits are at narrow necks; none changes a scored match inside the windows. The two blobs that intersect the windows were split as follows: the W3 blob (2,084 m²) into 1,715 + 321 + 48 m², the W1/central blob (2,843 m²) into 2,808 + 35 m². The remaining large merged roofs share 5–9 m boundaries and fail the constriction test, so the parts overlapping labelled roofs are still far larger than any single label (IoU < 0.5). Splits elsewhere in the scene are not validated.

Trade-off: v1 cannot lose pixel accuracy (building pixels are identical by construction; polygons differ only by simplification along new cuts, 0.23 m² total overlap). It also cannot gain building recall where roofs physically touch — which is where the misses are.

## Files
- `baseline_hashes.sha256` — SHA-256 of 22 baseline inputs (weights, imagery, labels, baseline outputs, scores, scripts, tests) recorded before any change; all verified unchanged afterwards.
- `distance_split/` — v1 output (`buildings.geojson`, `buildings_wgs84.geojson`, `instances.tif`, `mask.tif`, `overlay.png`, `mask_preview.png`, `run_summary.json` with parameters and baseline input hashes).
- `evaluation/scores.md`, `scores.json` — baseline vs v1 scores (baseline scores in `outputs/evaluation/` untouched).
- `contact_sheet.png` — per window: original | baseline prediction (cyan) | v1 prediction (magenta) | reviewed reference labels (yellow dashed; ignored = grey dashed).

## Reproduce
```
.venv\Scripts\python -m pip install scipy            # scipy 1.18.1 (only new dependency)
.venv\Scripts\python scripts\postprocess_instances.py outputs\lapaz_predio_bisa_whu_0.3m outputs\whu_postprocessed_v1\distance_split
.venv\Scripts\python tests\validate_outputs.py outputs\whu_postprocessed_v1\distance_split
.venv\Scripts\python scripts\score_models.py --labels data\validation\reviewed_labels.geojson --run whu_baseline=outputs\lapaz_predio_bisa_whu_0.3m --run whu_postprocessed_v1=outputs\whu_postprocessed_v1\distance_split --secondary-reference data\reference\ms_buildings_lapaz.geojson --out-dir outputs\whu_postprocessed_v1\evaluation
.venv\Scripts\python tests\test_postprocess.py
.venv\Scripts\python tests\test_scoring.py
```
Runtime on this CPU: ~2 s for post-processing.

## Limitations and next options
- Tiny sample (4 windows, 32 labels, one orthophoto); results do not establish general accuracy.
- Reference labels are AI-drafted, user-approved, ~0.25–0.5 m outline accuracy — not survey ground truth.
- Separating touching roofs needs evidence the mask does not contain. Candidates, each needing its own pre-registered evaluation: RGB edge/colour-boundary cues inside merged blobs (risk: splits along roof ridges and panels); a model with a boundary or instance output (e.g. 3-class building/boundary/background), which means fine-tuning — out of scope here.
- Detected building footprints are not cadastral parcel boundaries and must not be presented as verified property boundaries without authoritative parcel data.
