# Phase 3 evaluation protocol (fixed 2026-10-04, before any T-window label or prediction exists)

This file is hashed in `protocol_lock.sha256` immediately after writing. Any later change must be
recorded as a dated amendment below, never silently edited.

## 1. Data splits
| split | windows | use |
|---|---|---|
| development | W1–W4 (32 approved labels), V1–V4 (49 approved labels) | sanity checks and the single pre-declared configuration choice in §4; both already inspected |
| **held-out test** | **T1–T4** (`data/validation_test/windows.json`) | scored **once**, after labels are approved, for the configurations fixed in §4 |

T windows (40 × 40 m, EPSG:32719, from the La Paz orthophoto; offsets in m from the image top-left):
T1_mixed_west (55, 238) · T2_dense_south_centre (180, 273) · T3_residential_north (165, 45) · T4_dense_south_west (78, 280).
Selected by land-use type from the raw orthophoto (no predictions shown), each ≥ 10 m from every W/V window
(min distances 13.0, 10.9, 46.8, 11.1 m). Valid-pixel fraction: 100 % except T2 (84.9 %; no-data is excluded by the scorer).
Limitation: same orthophoto/flight as development data — spatially separate, not independent imagery.

## 2. Reference labels for T1–T4
1. AI-drafted outlines traced from raw 0.05 m crops with a 2.5 m grid (`draft_pixel_polygons.json` → `provisional_labels.geojson`). No prediction layer, probability map or cut layer is rendered for T windows before approval.
2. User review window by window against the full-resolution orthophoto: each label confirmed / edited / uncertain (→ ignore); ambiguous unlabelled structures added as ignore regions; when imagery does not establish a separate building, mark uncertain.
3. Precedence: ignore areas are clipped so they never remove confirmed building pixels.
4. Approved labels written to a new `reviewed_labels.geojson`; the provisional file is never modified.
5. Until approval, scene-wide outputs (overlays/previews) are not opened and no T-window crop of any prediction is created.

## 3. Scoring (identical for every method)
`scripts/score_models.py` unchanged: CRS EPSG:32719; 0.1 m evaluation grid; no-data and ignore areas excluded; one-to-one greedy matching, **IoU ≥ 0.5**; pieces < 2 m² dropped after clipping; micro-averaged pixel P/R/IoU and building P/R with TP/FP/FN, per window. Microsoft footprints only as a secondary reference.
Method outputs: polygons with **min area 10 m²** and simplification 0.15 m (the WHU baseline rule), source CRS.

## 4. Methods and configurations (fixed now)
- **A. WHU UNet++ baseline** — frozen `outputs/lapaz_predio_bisa_whu_0.3m` (no re-run).
- **B. Approved RGB-guided post-processing** — frozen `outputs/rgb_guided_reviewed/final/predictions` (no re-run).
- **C. Mask R-CNN instance segmentation** — geoai `building_footprints_usa.pth`, HF `giswqs/geoai` commit `aa2b25d27b2c78f0e92c7b6a4813a571f9bc2288`, LFS SHA-256 `3aea5d0d…bc7bca`; torchvision `maskrcnn_resnet50_fpn`, 2 classes; input RGB/255 with ImageNet mean/std inside the model; 512 px chips, 25 % overlap; confidence 0.5, mask threshold 0.5, NMS IoU 0.5 (library defaults).
  Overlapping instance masks are resolved pixel-wise to the highest-score instance; then the §3 polygon rules (10 m², 0.15 m) replace the library's 100-px minimum so all methods share one minimum-area rule.
  Inference resolution: two pre-declared options only — **C@0.6 m** (resolution of the model's documented example imagery, NAIP) and **C@0.3 m** (WHU grid). Exactly one is selected on the **development** windows with the rule in §5, then frozen before T scoring.

## 5. Decision rules
- Configuration choice for C (development only): the option with higher development building recall, provided its development pixel IoU is ≥ (WHU pixel IoU − 0.02); if neither qualifies, C is reported as not promoted and still scored on T for transparency.
- On T: report A, B and the selected C side by side. A method is described as better than A only if building recall is higher **and** pixel IoU is not lower by more than 0.02 **and** the gain is not confined to a single window. Otherwise report "no demonstrated improvement".
- No threshold, area rule or post-processing parameter is changed after T labels are approved.

## 6. Out of scope / claims
Building footprints only; no cadastral parcel claims. No accuracy claim beyond T1–T4 of this orthophoto.

## Amendments
### Amendment 1 — 2026-10-04 (before any T label approval or T prediction view; dev scores of A, B, C known; D not yet run on any data)
1. Dev result for C (W1–W4 + V1–V4 pooled): C@0.6 pixel IoU 0.566, building recall 0.333; C@0.3 pixel IoU 0.404, building recall 0.407; WHU pixel IoU 0.809. Neither meets the pixel-IoU condition → **C not promoted**. For the transparency score on T, the option with higher dev building recall is used: **C@0.3 m**.
2. New pre-declared method **D. WHU mask partitioned by Mask R-CNN instances** (`scripts/hybrid_instance_partition.py`):
   building pixels = frozen WHU baseline mask (unchanged); seeds = each C@0.3 instance ∩ WHU mask, kept if ≥ 10 m² (the shared min-area rule); per 4-connected WHU component, seeds are grown geodesically inside the component (`postprocess_instances._grow`) until every component pixel is assigned; components with 0–1 seeds stay whole; polygon rules as §3. No other parameters.
   D is evaluated once on the development windows (reported, not tuned) and once on T. Decision rule §5 applies unchanged.
