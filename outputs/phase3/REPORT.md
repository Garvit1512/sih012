# Phase 3 — held-out test windows and instance-aware methods (2026-10-04)

Building footprints only; nothing here measures legal cadastral parcel boundaries.

## What was done
1. **Audit + freeze**: 235 project files hashed (`frozen_hashes.sha256`); all existing test suites passed before changes.
2. **New held-out test windows T1–T4** (`data/validation_test/windows.json`), 40 × 40 m, chosen by land use from the raw orthophoto, ≥ 10.9 m from every W1–W4 / V1–V4 window.
3. **Protocol fixed before any T label or prediction** (`PROTOCOL.md`, hash-locked in `protocol_lock.sha256`); one dated amendment before T labels were approved (`protocol_lock_amend1.sha256`).
4. **T reference labels**: 54 AI-drafted outlines from raw crops → reviewed by the project user → 30 buildings + 24 ignore areas (11 uncertain separations, 13 drafted ignores) in `data/validation_test/reviewed_labels.geojson`; provisional file kept; decisions in `review_decisions.json`. No T prediction was rendered or scored before approval.
5. **Candidates** (new outputs only, baselines untouched):
   - C: Mask R-CNN instance segmentation, geoai `building_footprints_usa.pth` (HF `giswqs/geoai` @ `aa2b25d2…`, SHA-256 `3aea5d0d…`, MIT; trained on NAIP RGB 0.6 m — verified from the training notebook and raster header). Instances kept separate (geoai's own inference merges them). `scripts/run_maskrcnn_inference.py`.
   - D: frozen WHU mask partitioned by Mask R-CNN instances (WHU decides *where*, Mask R-CNN decides *how many*). `scripts/hybrid_instance_partition.py`.
   - RGB-edge separation was already tested in earlier phases (method B) and is included as a comparator rather than re-designed.

## Development results (W1–W4 + V1–V4 pooled; already-seen windows; used only for the pre-declared choices)
| method | pixel IoU | building P | building R | building TP/FP/FN |
|---|---|---|---|---|
| A WHU baseline | 0.809 | 0.593 | 0.198 | 16/11/65 |
| B approved RGB post-processing | 0.809 | 0.667 | 0.272 | 22/11/59 |
| C Mask R-CNN @0.6 m | 0.566 | 0.675 | 0.333 | 27/13/54 |
| C Mask R-CNN @0.3 m | 0.404 | 0.825 | 0.407 | 33/7/48 |
| D hybrid (dev, run once after declaration) | W1–W4 0.823 / V1–V4 0.782 | W 0.778 / V 0.696 | W 0.438 / V 0.327 | W 14/4/18 · V 16/7/33 |

Per protocol, C was **not promoted** on development (pixel IoU far below WHU − 0.02); C@0.3 was carried to T for transparency (higher dev building recall).

## Held-out test results — T1–T4 (scored once; `test_scores_T1-T4/`)
Identical settings for all methods: unchanged `score_models.py`, EPSG:32719, 0.1 m grid, IoU ≥ 0.5 one-to-one matching, pieces < 2 m² dropped, same 24 ignore areas, no-data excluded; all methods use the 10 m² output minimum.

| method | pixel P | pixel R | pixel IoU | building P | building R | pixel TP / FP / FN | building TP / FP / FN |
|---|---|---|---|---|---|---|---|
| A WHU baseline | 0.835 | 0.645 | 0.572 | 0.429 | 0.200 | 97,631 / 19,268 / 53,678 | 6 / 8 / 24 |
| B approved RGB post-proc | 0.840 | 0.616 | 0.551 | 0.421 | 0.267 | 93,209 / 17,747 / 58,100 | 8 / 11 / 22 |
| C Mask R-CNN @0.3 m | 0.958 | 0.569 | 0.555 | 0.632 | 0.400 | 86,069 / 3,777 / 65,240 | 12 / 7 / 18 |
| D hybrid WHU + Mask R-CNN | 0.874 | 0.642 | 0.588 | 0.368 | 0.233 | 97,139 / 13,943 / 54,170 | 7 / 12 / 23 |

Per window — pixel IoU; building TP/FP/FN:

| window | A | B | C | D |
|---|---|---|---|---|
| T1 mixed (5 labels) | 0.615; 3/2/2 | 0.615; 3/3/2 | 0.732; 3/2/2 | 0.615; 3/2/2 |
| T2 dense (15) | 0.472; 1/4/14 | 0.421; 2/6/13 | 0.520; 5/1/10 | 0.503; 3/5/12 |
| T3 residential (1) | 0.866; 1/0/0 | 0.866; 1/0/0 | 0.450; 0/2/1 | 0.866; 0/2/1 |
| T4 dense (9) | 0.380; 1/2/8 | 0.372; 2/2/7 | 0.615; 4/2/5 | 0.380; 1/3/8 |

### Decision rule (§5) applied to T
- **C Mask R-CNN**: building recall 0.200 → 0.400, pixel IoU −0.017 (within 0.02), gains in T2 (+4) and T4 (+3) → **meets the T criterion**. But on development C lost ~0.4 pixel IoU and was not promoted. The two splits disagree on the pixel side, so the result is **not robust**; C is a promising candidate, not a demonstrated replacement.
- **D hybrid**: building recall 0.233 (+1 match net: T2 +2, T3 −1); gain confined to one window → **no demonstrated improvement**, despite clearly better development numbers (dev gains did not transfer).
- **B (previous approved post-processing)**: recall 0.267 but pixel IoU −0.021 → **does not meet** the rule.

### Measured explanations
- B and D have exactly the same building area as A in T (1,542 m²). Their lower pixel recall comes from the scorer's existing rule that drops predicted pieces lying > 50 % inside ignore areas: splitting creates such pieces (dropped area A 204 m², B 379 m², D 326 m²). Scorer left unchanged per protocol; this rule penalises splitting methods near ignore areas.
- T pixel IoU is much lower than on development for every method (A 0.572 vs 0.809), mainly T2 and T4 (dense, patchwork roofs, large ignore areas): the test windows are harder.

### Qualitative observations (`comparison_T1-T4.png`; not measured)
- T3: Mask R-CNN splits the single large house (flat skylight roof separated from tiled roof); WHU keeps it whole → over-splitting is the instance model's failure mode on large complex roofs.
- T2/T4: Mask R-CNN outlines individual roofs tightly and detects a dark-panelled grey roof in T4 that WHU misses, but leaves many small/patchwork roofs undetected (low pixel recall).
- D inherits WHU's misses in T4 (it cannot add pixels) and the Mask R-CNN over-split in T3.

## Conclusions (bounded to this evidence)
- No method shows a robust improvement over the WHU baseline across both development and held-out data.
- Instance segmentation (Mask R-CNN) separates touching roofs far better at building level (T: 12 vs 6 matches; dev: 33 vs 16) but misses roof area; combining it with WHU (D) did not hold up on T.
- Remaining failures: missed rusty/patchwork/small roofs (all methods), merged roofs (WHU), over-split large houses (Mask R-CNN).
- Recommended next step (hypothesis): fine-tune an instance or boundary-aware model on locally labelled roofs (the 81 reviewed W/V labels are a start but too few), and build a further held-out set before evaluating it. T1–T4 have now been viewed with predictions and should not be reused as an unseen test.

## Limitations
- 30 test buildings in 4 windows of the same orthophoto/flight; a few buildings change every metric. T3 contains one building.
- Reference outlines are AI-drafted and user-reviewed (~0.25–0.5 m), not survey data; 11 uncertain separations are ignored.
- Protocol amendment 1 (declaring C@0.3 for T and method D) was made after development scores were known but before T labels were approved or any T prediction was viewed.
- `scripts/build_reviewed_independent_labels.py` gained a `--val-dir` option (default behaviour unchanged; V1–V4 rebuild still refused).

## Reproduce
```
.venv\Scripts\python scripts\build_test_labels.py --val-dir data\validation_test
.venv\Scripts\python scripts\build_reviewed_independent_labels.py --val-dir data\validation_test
.venv\Scripts\python scripts\run_maskrcnn_inference.py data\lapaz_predio_bisa.tif --target-res 0.6 --out-dir outputs\phase3\maskrcnn_0.6m
.venv\Scripts\python scripts\run_maskrcnn_inference.py data\lapaz_predio_bisa.tif --target-res 0.3 --out-dir outputs\phase3\maskrcnn_0.3m
.venv\Scripts\python scripts\hybrid_instance_partition.py outputs\lapaz_predio_bisa_whu_0.3m outputs\phase3\maskrcnn_0.3m outputs\phase3\hybrid_whu_maskrcnn
.venv\Scripts\python scripts\score_models.py --labels data\validation\reviewed_labels.geojson --run A_whu_baseline=outputs\lapaz_predio_bisa_whu_0.3m --run B_approved_postproc=outputs\rgb_guided_reviewed\final\predictions --run C_maskrcnn_0.6m=outputs\phase3\maskrcnn_0.6m --run C_maskrcnn_0.3m=outputs\phase3\maskrcnn_0.3m --out-dir outputs\phase3\dev_scores\W1-W4
.venv\Scripts\python scripts\score_models.py --labels data\validation_test\reviewed_labels.geojson --windows data\validation_test\windows.geojson --run A_whu_baseline=outputs\lapaz_predio_bisa_whu_0.3m --run B_approved_postproc=outputs\rgb_guided_reviewed\final\predictions --run C_maskrcnn_0.3m=outputs\phase3\maskrcnn_0.3m --run D_hybrid=outputs\phase3\hybrid_whu_maskrcnn --secondary-reference data\reference\ms_buildings_lapaz.geojson --out-dir outputs\phase3\test_scores_T1-T4
.venv\Scripts\python tests\test_phase3.py
```
(V1–V4 dev scoring: add `--labels data\validation_independent\reviewed_labels.geojson --windows data\validation_independent\windows.geojson`. The inference and hybrid scripts refuse to write into non-empty directories; the label builders refuse once reviewed labels exist.)
