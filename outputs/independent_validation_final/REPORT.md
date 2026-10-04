# Independent validation V1–V4 — final report (2026-10-04)

Compares the **WHU UNet++ baseline** with the **approved post-processing** (RGB-guided cuts, 30 human-accepted; rejected/uncertain cuts not applied) on four independent 40 × 40 m windows of the La Paz orthophoto. Results apply to this validation set only.

## Reference data (`reference/`, `verification.json`)
- Source of truth: `data/validation_independent/reviewed_labels.geojson` (user-reviewed window by window; decisions in `review_decisions.json`). Not modified; split into two **new** files: `reference/buildings_approved.geojson` (49 buildings) and `reference/ignore_regions.geojson` (25 ignore areas: 9 uncertain labels, 7 kept provisional ignores, 9 added regions).
- Verification (all passed): every one of the 65 provisional labels has an explicit decision; all features `approved`; confirmed/edited labels are buildings; uncertain labels and added regions are ignored; the 2 edits (V3-05, V4-07) are applied; ignore areas never cover confirmed buildings; all geometries valid; CRS EPSG:32719.

## Measured results (`scores/`) — identical settings for both runs
Existing `score_models.py`; CRS EPSG:32719; 0.1 m evaluation grid; building match = one-to-one greedy, IoU ≥ 0.5; pieces < 2 m² dropped; same 25 ignore areas; no-data excluded.

| run | pixel P | pixel R | pixel IoU | building P | building R | pixel TP / FP / FN | building TP / FP / FN |
|---|---|---|---|---|---|---|---|
| WHU baseline | 0.966 | 0.817 | 0.794 | 0.600 | 0.184 | 227,165 / 8,005 / 50,883 | 9 / 6 / 40 |
| approved post-processing | 0.966 | 0.817 | 0.794 | 0.684 | 0.265 | 227,156 / 8,005 / 50,892 | 13 / 6 / 36 |

Per window (baseline → approved):

| window | pixel P | pixel R | pixel IoU | building P | building R | building TP / FP / FN |
|---|---|---|---|---|---|---|
| V1 dense informal | 0.950 | 0.859 | 0.821 | 0.000 → 0.000 | 0.000 → 0.000 | 0/3/13 → 0/3/13 |
| V2 large sheds | 0.981 | 0.981 | 0.962 | 0.000 → 0.000 | 0.000 → 0.000 | 0/1/10 → 0/2/10 |
| V3 residential | 0.945 | 0.599 | 0.579 | 1.000 → 0.875 | 0.438 → 0.438 | 7/0/9 → 7/1/9 |
| V4 small roofs | 0.963 | 0.640 | 0.625 → 0.624 | 0.500 → 1.000 | 0.200 → 0.600 | 2/2/8 → 6/0/4 |

These numbers are byte-identical to the earlier independent run (`outputs/rgb_guided_reviewed/final/evaluation_independent_V1-V4/`).

### What improved (measured)
- Building matches 9 → 13 of 49 (building recall 0.184 → 0.265, precision 0.600 → 0.684); mean matched IoU 0.722 → 0.730.
- All 4 new matches are in V4, from two accepted cuts: C019 (V4-01 IoU 0.33 → 0.80; V4-02 0.38 → **0.53**, marginal) and C020 (V4-04 0.48 → 0.76; V4-06 0.43 → 0.77).

### What did not improve (measured)
- Pixel precision/recall/IoU: unchanged (post-processing never changes building pixels; −9 TP px from simplification along cuts).
- V1 and V2: **zero** matched buildings in both runs, despite high pixel IoU (0.821, 0.962).
- V3: no new matches; one extra unmatched piece (C041 separated a yard-clutter false positive that already existed in the baseline). V2: one extra unmatched piece (C030). Net building FP unchanged (6).
- Building recall remains low overall (0.265): 36 of 49 reference buildings are not matched.

## Remaining failure cases
Measured on W1–W4 (`outputs/instance_study/diagnosis/`), consistent with V1–V4 aggregates:
- **Merged roofs** (dominant): touching roofs or gaps of 0.1–0.4 m (≤ 1.3 px at 0.3 m inference) are predicted as one region.
- **Missed roofs**: rusty/red corrugated and edge-clipped roofs with WHU probability ≈ 0.
- **Small roofs**: labels < 10 m² cannot be matched (output min-area); V3-03 is one example.

Qualitative observations from `comparison_V1-V4.png` (not measured): in V1 a few large predicted regions each span many small roofs; in V2 the shed complex is predicted as one or two regions; in V3 several roofs (e.g. the long grey roof at the left edge, some of the clipped roofs along the top edge) have no prediction; in V4 the rotated rusty roof (V4-07) and the small white roof (V4-11) appear undetected.

## Limitations
- 49 reference buildings in 4 windows of one orthophoto; differences of a few buildings change the metrics substantially. No claim beyond this set.
- Reference outlines are AI-drafted and user-reviewed roof outlines (~0.25–0.5 m), not survey data; 9 uncertain (mostly merged-roof) cases are ignored, which likely flatters both runs.
- The same AI assistant drafted the reference outlines and proposed the cut decisions; the user reviewed both.
- This report's visual comparison shows predictions on V1–V4; V1–V4 should therefore not be treated as an unseen test set for future method design (use new held-out windows).
- No thresholds or parameters were tuned on these labels.
- `scripts/score_models.py` was modified on disk at 14:31:52 by something other than this task's commands; it reproduces all stored scores exactly and is documented in `hash_exceptions.json` (not reverted).
- Detected building footprints are **not** legal cadastral parcel boundaries; no cadastral accuracy is claimed.

## Reproduce
```
.venv\Scripts\python scripts\finalize_independent_validation.py outputs\lapaz_predio_bisa_whu_0.3m outputs\rgb_guided_reviewed\final\predictions outputs\independent_validation_final
.venv\Scripts\python scripts\score_models.py --labels data\validation_independent\reviewed_labels.geojson --windows data\validation_independent\windows.geojson --run whu_baseline=outputs\lapaz_predio_bisa_whu_0.3m --run approved_postprocessing=outputs\rgb_guided_reviewed\final\predictions --secondary-reference data\reference\ms_buildings_lapaz.geojson --out-dir outputs\independent_validation_final\scores
.venv\Scripts\python tests\test_finalize_independent.py
```
(The finalize step refuses to overwrite existing `reference/` files.)
