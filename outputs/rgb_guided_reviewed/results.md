# Human-reviewed RGB-guided building footprints

**Status (2026-10-04, updated):** cut review complete and applied. The V1–V4 reference labels have now been
reviewed window by window and approved by the project user, and the **independent evaluation** has been run
(next section). The W1–W4 scores further down remain **diagnostic** only (those windows informed the method
design). Earlier text in sections 0–5 that describes independent validation as pending reflects the state
before the V1–V4 review and is kept for the record.

## INDEPENDENT EVALUATION — windows V1–V4 (2026-10-04) — `final/evaluation_independent_V1-V4/`
Kept separate from the W1–W4 diagnostic results below.

### Reference labels
- Windows V1_dense_informal, V2_large_sheds, V3_residential, V4_small_roofs (40×40 m each, 100 % valid imagery, no overlap with W1–W4), chosen by land use from the raw orthophoto before any cut was reviewed; not used to set thresholds or design splits.
- Provisional outlines (65) drafted by the AI assistant from raw imagery; then reviewed **window by window by the project user** against the full-resolution orthophoto (no model predictions shown). Decisions: `data/validation_independent/review_decisions.json`; approved file: `data/validation_independent/reviewed_labels.geojson` (built by `scripts/build_reviewed_independent_labels.py`). `provisional_labels.geojson` is unchanged (hash verified).
- Outcome: **49 building labels** (47 confirmed, 2 edited: V3-05 trimmed off the neighbouring purple roof, V4-07 notch removed) and **25 ignore areas** (9 labels marked uncertain — mostly possible merged roofs: V1-05, V1-07, V1-11, V1-16, V2-01, V2-04, V2-06, V3-04, V4-03; 7 provisional ignores kept; 9 added ignore regions for ambiguous unlabelled structures).
- Precedence rule (user-approved): ignore areas are clipped so they never remove confirmed building pixels (10 ignores clipped; largest V4-13, −10.42 m²). Confirmed-vs-confirmed overlaps ≤ 4.9 m² along shared walls left as tracing tolerance.

### Results (identical settings for both runs: same scorer, 0.1 m grid, ignore areas, building IoU ≥ 0.5, pieces < 2 m² dropped)
| run | pixel P | pixel R | pixel IoU | building P | building R | pixel TP / FP / FN | building TP / FP / FN | mean matched IoU |
|---|---|---|---|---|---|---|---|---|
| WHU baseline | 0.966 | 0.817 | 0.794 | 0.600 | 0.184 | 227,165 / 8,005 / 50,883 | 9 / 6 / 40 | 0.722 |
| RGB-guided reviewed (final) | 0.966 | 0.817 | 0.794 | 0.684 | 0.265 | 227,156 / 8,005 / 50,892 | 13 / 6 / 36 | 0.730 |
| *Microsoft (secondary, not truth)* | 0.713 | 0.633 | 0.505 | 0.100 | 0.041 | 176,045 / 70,705 / 102,003 | 2 / 18 / 47 | 0.607 |

Per window (baseline → reviewed): pixel IoU identical — V1 0.821, V2 0.962, V3 0.579, V4 0.625 → 0.624.
Buildings matched / predicted / labels: V1 0/3/13 → 0/3/13 · V2 0/1/10 → 0/2/10 · V3 7/7/16 → 7/8/16 · V4 2/4/10 → 6/6/10.

Attribution: all 4 new matches are in V4 from two accepted cuts — C019 (V4-01 IoU 0.33→0.80, V4-02 0.38→0.53, marginal) and C020 (V4-04 0.48→0.76, V4-06 0.43→0.77). C030 (V2) and C041 (V3) each added one unmatched piece (C041 detaches a yard-clutter false positive that already existed in the baseline). No label lost a match; net building FP unchanged (6).

### Interpretation and caveats
- Pixel accuracy is unchanged by construction; building-level matching improved from 9 to 13 of 49 labels on this independent sample. All of that gain comes from one window (V4) and two cuts — a small, fragile effect, not evidence of general accuracy.
- Building recall stays low (0.265): V1 (dense informal) and V2 (large sheds) have **zero** matched buildings for both runs — merged and patchwork roofs remain the dominant failure, and RGB guidance did not help there. V3 recall is limited by missed roofs (pixel recall 0.599).
- Ignoring 9 uncertain merged-roof labels removes some of the hardest cases from scoring, which likely flatters both runs.
- Independence is partial: the same AI assistant drafted the reference outlines and proposed the cut decisions (C019/C020 were accepted after V4 labels were drawn); the user reviewed both. Labels are approximate roof outlines (~0.25–0.5 m), not survey data. 49 labels in 4 windows of one orthophoto.
- V3-03 (5.4 m²) is below the pipeline's 10 m² output minimum and can never be matched.
- Detected building footprints are not cadastral parcel boundaries.

Reproduce:
```
.venv\Scripts\python scripts\build_reviewed_independent_labels.py     # refuses if reviewed_labels.geojson exists
.venv\Scripts\python scripts\score_models.py --labels data\validation_independent\reviewed_labels.geojson --windows data\validation_independent\windows.geojson --run whu_baseline=outputs\lapaz_predio_bisa_whu_0.3m --run rgb_guided_reviewed_final=outputs\rgb_guided_reviewed\final\predictions --secondary-reference data\reference\ms_buildings_lapaz.geojson --out-dir outputs\rgb_guided_reviewed\final\evaluation_independent_V1-V4
.venv\Scripts\python tests\test_independent_labels.py
```

## 0. Final review from `cut_review_proposals.csv` (Excel review, 2026-10-04) — `final/`
- CSV validated before use: header `cut_id, proposed_status, reason`; 62 rows, 3 fields each; 62 unique ids matching the inventory (none missing/unknown); statuses only `accepted`/`rejected`/`uncertain` (no case/whitespace variants); no empty reasons.
- The CSV is unchanged since creation (SHA-256 `4f52c82a…`, LF line endings, no Excel re-save detected); its decisions equal the earlier recorded review. The project user confirmed these decisions are final and that uncertain cuts stay unapplied. The CSV was not modified.
- Decisions: **30 accepted, 20 rejected, 12 uncertain** (high confidence 16/5/6, medium 14/15/6).
  Uncertain (not applied, for later manual review): C010 C012 C014 C015 C022 C027 C037 C039 C042 C050 C057 C058.
- Recorded to a new file `final/cut_review_final.geojson` (reviewer, date, decision_source) via `record_cut_review.py --out` (new option; refuses to overwrite an existing review). The earlier `cut_review_reviewed.geojson` and `predictions/` were left untouched.
- Applied to `final/predictions/`: 30 accepted cuts → 151 instances → 102 polygons (baseline 74). Verified:
  - all 30 accepted cuts separate their parts; 0 of 20 rejected and 0 of 12 uncertain cuts are applied;
  - all 56 baseline polygons not touched by an accepted cut are geometrically identical in the output (symmetric difference < 1e-6 m²);
  - instances cover exactly the baseline mask; total area 13,875.93 m² = baseline; CRS EPSG:32719; all polygons valid; `validate_outputs.py` 12/12 checks pass;
  - output files are byte-identical to the earlier `predictions/` (same decisions → same result).
- Comparison sheet: `final/comparison_contact_sheet.png` (original | baseline WHU | reviewed output with accepted cuts in red) for W1–W4 and four accepted-cut areas outside all windows. V1–V4 are deliberately excluded so predictions do not influence the pending label review.
- Diagnostic W1–W4 (`final/evaluation_diagnostic_W1-W4/`, same scorer/settings): identical to section 3 below — pixel P/R/IoU 0.970/0.845/0.824; building P 0.643, R 0.281; pixel TP/FP/FN 231,360/7,039/42,344; building TP/FP/FN 9/5/23 (baseline 7/5/25). Per window matched/pred/labels: W1 3/7/16, W2 2/2/3, W3 2/3/11, W4 2/2/2.
- **Independent V1–V4 validation: pending.** All 65 V1–V4 labels are still `provisional_unreviewed`; nothing was scored on them.
- Pre-existing files: 197 hashed before this step; all unchanged except the intended `scripts/record_cut_review.py` `--out` addition.

Reproduce this step:
```
.venv\Scripts\python scripts\record_cut_review.py outputs\rgb_guided_reviewed --decisions outputs\rgb_guided_reviewed\cut_review_proposals.csv --reviewer "project user" --source "cut_review_proposals.csv reviewed by project user in Excel; decisions confirmed final 2026-10-04 (file unchanged from AI proposals)" --date 2026-10-04 --confirm --out outputs\rgb_guided_reviewed\final\cut_review_final.geojson
.venv\Scripts\python scripts\apply_cut_review.py outputs\rgb_guided_v1 outputs\lapaz_predio_bisa_whu_0.3m outputs\rgb_guided_reviewed\final\cut_review_final.geojson outputs\rgb_guided_reviewed\final\predictions
.venv\Scripts\python tests\validate_outputs.py outputs\rgb_guided_reviewed\final\predictions
.venv\Scripts\python scripts\score_models.py --labels data\validation\reviewed_labels.geojson --run whu_baseline=outputs\lapaz_predio_bisa_whu_0.3m --run rgb_guided_reviewed_final=outputs\rgb_guided_reviewed\final\predictions --secondary-reference data\reference\ms_buildings_lapaz.geojson --out-dir outputs\rgb_guided_reviewed\final\evaluation_diagnostic_W1-W4
```
(`final/` must not already contain `cut_review_final.geojson`; the record step refuses to overwrite it.)

## 1. Cut review workflow
| step | script | output |
|---|---|---|
| inventory + verification | `scripts/build_cut_review.py` | `cut_review.geojson` (62 cuts, all `pending`), `cut_inventory.csv`, `cut_panels/C001–C062.png`, `cut_sheets/sheet_01–11.png` |
| decisions | AI proposals from imagery → `cut_review_proposals.csv` (status + reason per cut) | — |
| record (requires `--confirm`) | `scripts/record_cut_review.py` | `cut_review_reviewed.geojson` (status, reason, reviewer, review_date, decision_source) |
| apply (accepted only) | `scripts/apply_cut_review.py` | `predictions/` (buildings, instances, accepted_cut_lines, review_log.csv, overlay) |

- Association is verified, not assumed: each cut's two instance ids must exist, share a boundary in `rgb_guided_v1/instances.tif` and belong to the same baseline component; each cut is linked to its baseline polygon id; image row/col and lon/lat of its midpoint are recorded.
- Each panel shows original | baseline polygon | proposed cut | resulting parts at native 0.05 m.
- Decisions were made from imagery only (no labels, no scores) using the rules: accept only cuts on a visible boundary between distinct roofs; reject cuts through continuous roofs, ridges/roof planes, skylights/rooftop structures, terraces/porches/connectors of the same building, tree occlusion, or clutter; otherwise `uncertain`.
- Decision provenance: proposals were drafted by the AI assistant and **confirmed as-is by the project user** (`decision_source` field). Uncertain cuts are kept for later manual review and are not applied.

### Review counts
| | accepted | rejected | uncertain | total |
|---|---|---|---|---|
| high confidence | 16 | 5 | 6 | 27 |
| medium confidence | 14 | 15 | 6 | 35 |
| **total** | **30** | **20** | **12** | **62** |

Confidence was not a reliable proxy: 5 of 27 high-confidence cuts were rejected.

### False-split examples (rejected)
- C001–C008: eight cuts on one large terracotta house — roof planes/ridges (C001, C004), roof terraces and porch (C002, C003, C005), glass connector/courtyard (C006, C007), tree occlusion (C008). C002 and C006 were *high* confidence.
- C013 (design window W3): isolates a middle segment of a continuous rusty roof row — this was the source of the fragile W3-03 diagnostic match.
- C024: bends through a continuous orange roof (high). C031: loops around a rooftop structure (high). C036: separates a rooftop structure from its own building (high).
- C033 (V1), C047, C060: jagged cuts through clutter / lumber / patterned roofs with no visible boundary.

Accepted cuts that detach a WHU false positive (C041 yard clutter, C043 timber, C044 concrete courtyard, C048 lumber yard) were accepted because the cut follows a real roof edge; the false-positive area exists in the baseline too.

## 2. Reviewed predictions (`predictions/`)
30 accepted cuts → 151 instances → 102 polygons (baseline 74). Building pixels identical to the baseline mask; total polygon area 13,876 m² (unchanged). Areas with no accepted cut keep the exact baseline polygon (verified: zero symmetric difference; with all cuts pending the output reproduces the baseline's 74 polygons exactly). `validate_outputs.py` passes (round-trip IoU 0.990).

## 3. Diagnostic evaluation — design windows W1–W4 (NOT independent)
Same scorer, reviewed labels (32 scored), 0.1 m grid, ignore areas, building IoU ≥ 0.5.

| run | pixel P | pixel R | pixel IoU | building P | building R | pixel TP / FP / FN | building TP / FP / FN | mean matched IoU |
|---|---|---|---|---|---|---|---|---|
| WHU baseline | 0.970 | 0.845 | 0.824 | 0.583 | 0.219 | 231,364 / 7,038 / 42,340 | 7 / 5 / 25 | 0.749 |
| RGB-guided v1 (unreviewed) | 0.970 | 0.845 | 0.824 | 0.667 | 0.312 | 231,348 / 7,039 / 42,356 | 10 / 5 / 22 | 0.747 |
| **RGB-guided reviewed** | 0.970 | 0.845 | 0.824 | 0.643 | 0.281 | 231,360 / 7,039 / 42,344 | 9 / 5 / 23 | 0.773 |

Per window, buildings matched / predicted / labels (pixel IoU identical across runs: W1 0.741, W2 0.870, W3 0.837, W4 0.904):
W1 2/6/16 → 3/7/16 (reviewed) · W2 2/2/3 → 2/2/3 · W3 1/2/11 → 2/3/11 · W4 2/2/2 → 2/2/2.

- Reviewed output has one fewer match than unreviewed (W3-03 lost because C013 was rejected on imagery grounds) — consistent with the review not being score-driven.
- Remaining errors in W1–W4 (reviewed): 15 labels still merged (grey-on-grey shared walls in W1 and W3), 5 missed outright (W1-08, W1-15, W2-06, W3-09, W3-10), 3 partially detected (W1-12, W1-16, W1-17). Cuts cannot fix missed or partial detections.

## 4. Independent validation — PENDING
- Windows V1_dense_informal, V2_large_sheds, V3_residential, V4_small_roofs (40×40 m, 100 % valid, no overlap with W1–W4) were chosen by land-use type from the raw orthophoto before any cut panel was reviewed and were not used to set thresholds (`data/validation_independent/windows.json`).
- 65 provisional reference polygons (7 marked `ignore`) were traced from raw imagery before cut review (`provisional_labels.geojson`, `contact_sheet.png`, `review/`). The project user chose **not to approve them yet**, so no V1–V4 score was computed and none should be inferred.
- Independence caveats once approved: labels and cut decisions were drafted by the same AI assistant; the assistant had previously seen low-resolution scene-wide prediction/cut overlays; V2 was known to contain proposed cuts when selected (chosen for land use).
- 8 reviewed cuts fall in V1–V4: C019, C020, C030, C041 accepted; C033 rejected; C022, C027, C037 uncertain.

To run it after approving labels (create `data/validation_independent/reviewed_labels.geojson` with `status=approved`, never editing the provisional file):
```
.venv\Scripts\python scripts\score_models.py --labels data\validation_independent\reviewed_labels.geojson --windows data\validation_independent\windows.geojson --run whu_baseline=outputs\lapaz_predio_bisa_whu_0.3m --run rgb_guided_reviewed=outputs\rgb_guided_reviewed\predictions --secondary-reference data\reference\ms_buildings_lapaz.geojson --out-dir outputs\rgb_guided_reviewed\evaluation_independent_V1-V4
```

## 5. Preservation and quality checks
- `preserved_hashes.sha256`: 90 files (weights, imagery, W1–W4 labels, reference data, all earlier outputs, scripts, tests) hashed at start; all unchanged **except one documented exception**: `scripts/rgb_guided_instances.py` was modified on disk at 12:07:37 by something other than this task's commands (now CRLF line endings; content differs from the snapshot even after normalising line endings; original not recoverable). It was not reverted. Re-running the current file reproduces all five `rgb_guided_v1` outputs byte-identically. Recorded in `hash_exceptions.json`; the test allows only its current hash.
- Tests: `tests/test_cut_review.py` (8): accepted-only application, uncertain/pending/rejected never applied, missing cut records refused, real accepted/unaccepted pairs, rejected areas keep baseline geometry, CRS/validity/mask/area, `--confirm` required, deterministic re-application, preserved hashes. Existing suites pass: `test_scoring.py`, `test_postprocess.py` (9), `test_rgb_guided.py` (7), `validate_outputs.py` on 7 run directories.

## Reproduce
```
.venv\Scripts\python scripts\build_cut_review.py outputs\rgb_guided_v1 outputs\lapaz_predio_bisa_whu_0.3m outputs\rgb_guided_reviewed
.venv\Scripts\python scripts\record_cut_review.py outputs\rgb_guided_reviewed --decisions outputs\rgb_guided_reviewed\cut_review_proposals.csv --reviewer "project user" --source "AI proposal (Claude) from imagery, confirmed as-is by project user" --date 2026-10-04 --confirm
.venv\Scripts\python scripts\apply_cut_review.py outputs\rgb_guided_v1 outputs\lapaz_predio_bisa_whu_0.3m outputs\rgb_guided_reviewed\cut_review_reviewed.geojson outputs\rgb_guided_reviewed\predictions
.venv\Scripts\python tests\validate_outputs.py outputs\rgb_guided_reviewed\predictions
.venv\Scripts\python scripts\score_models.py --labels data\validation\reviewed_labels.geojson --run whu_baseline=outputs\lapaz_predio_bisa_whu_0.3m --run rgb_guided_v1_unreviewed=outputs\rgb_guided_v1 --run rgb_guided_reviewed=outputs\rgb_guided_reviewed\predictions --secondary-reference data\reference\ms_buildings_lapaz.geojson --out-dir outputs\rgb_guided_reviewed\evaluation_diagnostic_W1-W4
.venv\Scripts\python scripts\build_independent_labels.py      # V1-V4 provisional labels (refuses once reviewed labels exist)
.venv\Scripts\python tests\test_cut_review.py
```

## Limitations
- No independent performance figure exists yet; W1–W4 scores are diagnostic only (32 labels, design-contaminated).
- Cut decisions are AI-drafted and user-confirmed without per-cut edits; 12 uncertain cuts await manual review.
- RGB guidance only helps where adjacent roofs differ in colour; same-material merges remain the main error.
- Detected building footprints are not cadastral parcel boundaries and must not be presented as verified property boundaries.
