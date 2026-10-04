# Instance-separation study — diagnosis and proposed plan (2026-10-04)

Status: **plan only — nothing downloaded, trained or re-run.** The V1–V4 independent set is frozen
(`frozen_hashes.sha256`, guarded by `tests/test_frozen_eval.py`) and was not re-inspected; all new
measurements below use the W1–W4 design windows (32 labels) or label-free scene statistics.

## Diagnosis (measured, W1–W4; `diagnosis/` via `scripts/diagnose_failures.py`)
| failure | labels | evidence | cause |
|---|---|---|---|
| merged roofs | 17 baseline / 15 reviewed | probability inside these roofs is high (mean 0.75–0.98); WHU fires on the roofs but not between them | **model has no instance/boundary output** |
| — of which touching roofs | 13 adjacent pairs with gap 0 m | mean WHU prob on the shared boundary 0.62–0.98 | no physical gap exists; only colour/texture/height cues separate them |
| — of which near-touching | 19 pairs with gap 0.10–1.4 m | most gaps 0.10–0.40 m = **0.3–1.3 px at the 0.3 m inference grid**; gap probability often still 0.5–0.9 | gaps are **at or below the inference resolution** (resolution + model smoothing) |
| missed outright | 5 (W1-08, W1-15, W2-06, W3-09, W3-10) | mean prob 0.00–0.11; ≤ 22 % of pixels ≥ 0.2 | **model limitation** (rusty/red corrugated roofs, clipped edge roofs) — not threshold, not tiling |
| partial | 3 (W1-12, W1-16, W1-17) | mean prob 0.41 / 0.25 / 0.16; W1-12 has 83 % of pixels ≥ 0.2 | W1-12 is threshold-sensitive; the others are model misses |
| too small | 4 labels < 10 m² (W1-04, W1-15, W1-17, W2-06) | 3 of 4 also have low probability | min-area filter is a hard ceiling for these 4, but lowering it alone would recover ≤ 1 |
| false positives | 5 unmatched predictions | 2 merged blobs (405, 1 118 m²), 1 partial piece on a real roof, only 2 spurious slivers (5.3, 3.8 m²) | the precision problem is also mostly **merging** |
| tiling | — | missed labels lie 7–39 m from tile seams; a label 3.2 m from a seam is matched (IoU 0.85) | no evidence of tiling artefacts |

Scene (label-free): 123 mask components, 49 below 10 m² (135.7 m² total, 1 % of mask area; median 1.5 m²) — mostly specks.
Previously reported V1–V4 aggregates are consistent: V1 and V2 have high pixel IoU (0.82, 0.96) but zero matched buildings → merging.

**Conclusion (measured):** post-processing knobs (threshold, min-area, tiling) can address at most ~2 of 25 W1–W4 failures. The dominant failure needs a model that predicts **building instances or boundaries**. Whether any available model does this well on La Paz imagery is **unknown until tested**.

## Candidates (verified 2026-10-04)
| candidate | what it outputs | checkpoint | licence | training data / resolution | CPU | notes |
|---|---|---|---|---|---|---|
| **geoai `building_footprints_usa.pth`** (torchvision Mask R-CNN R50-FPN, 2 classes) | building **instances** | HF `giswqs/geoai`, 176 MB, live | MIT (repo) | US aerial (geoai example uses NAIP; exact data/resolution to confirm from model docs) | yes | lowest integration effort; domain gap (US suburbs) |
| **SAM 2.1 hiera-tiny** (Meta) | class-agnostic object masks from points/boxes or automatic | `sam2.1_hiera_tiny.pt`, 156 MB, live | Apache-2.0 | natural images (SA-1B) | yes (slow-ish) | not a building detector; would split WHU components along learned object boundaries — risk of splitting roof planes like the RGB cuts |
| HiSup (HRNet, CrowdAI/Inria) | building polygons/instances | weight source not found in repo; one source states academic non-commercial | code MIT, **weights unverified** | CrowdAI ~0.3 m satellite; Inria 0.3 m aerial | GPU-oriented | not usable until weight licence/provenance confirmed |
| Frame Field Learning (Girard) | masks + frame field for polygonization | no pretrained-weight link found in README | BSD-3 (code) | CrowdAI, Inria | GPU-oriented | would require training |
| fine-tune WHU UNet++ with a 3-class head (building / boundary / background) | boundaries → instances | own | own | local labels | training needs GPU (Colab) | most promising long-term; needs a few hundred labelled roofs |

## Proposed experiment (fair protocol)
1. **Development set:** keep W1–W4 (32 approved labels; never used to design these candidate models) and add 4 new development windows D1–D4 (40 × 40 m, dense informal ×2, sheds, residential) from the 57,500 m² of the orthophoto outside W/V, ≥ 10 m from any V window. Reference outlines are drawn from raw imagery and reviewed by the user **before** any candidate prediction is generated or viewed.
2. **Candidate runs (CPU, new dir `outputs/instance_study/<candidate>/`):**
   - E1 Mask R-CNN (geoai): run at its documented training resolution (to be confirmed from the model docs, not tuned), confidence/mask thresholds from the library defaults.
   - E2 SAM 2.1-tiny: automatic mask proposals restricted to the frozen WHU mask (WHU decides *where*, SAM decides *how many*); parameters = library defaults; same min-area/simplification as baseline.
   - Each run: same polygon export, `validate_outputs.py`, and new unit tests for any new processing logic (CRS, validity, mask coverage where applicable, determinism).
3. **Scoring:** existing `score_models.py`, identical settings, on W1–W4 + D1–D4, versus the frozen WHU baseline and the reviewed RGB-guided output. Report pixel P/R/IoU, building P/R, TP/FP/FN, per window.
4. **Decision rule (fixed now):** a candidate is promoted only if it improves building recall on the development set **without** reducing pixel IoU by more than 0.02, and the gain is not confined to a single window. At most one configuration per candidate is selected on development data.
5. **Final test:** the single selected configuration is scored once on frozen V1–V4 and reported separately. If no candidate meets the rule, report that and stop; consider fine-tuning (E3) as a separate, approved step.

## What is measured vs hypothesis vs unresolved
- **Measured:** failure categories, probabilities, gap widths, min-area and tiling effects on W1–W4; checkpoint availability/licences listed above.
- **Hypothesis:** an instance-capable model separates touching roofs better than WHU; SAM boundaries follow building walls more than roof ridges; Mask R-CNN transfers from US imagery to La Paz.
- **Unresolved:** exact training data/resolution of the geoai Mask R-CNN; HiSup weight licence; whether any off-the-shelf model can see 0.1–0.4 m gaps (finer inference may be needed, but WHU/Mask R-CNN were trained at coarser resolutions); development set is from the same orthophoto as V1–V4 (not independent imagery).

Building footprints are not cadastral parcel boundaries; nothing here measures cadastral accuracy.
