# RGB-guided building separation v1 — diagnostic experiment

**Outcome (diagnostic, small sample):** building-level matches on the 32 reviewed labels went
from 7 to 10 (building precision 0.583 → 0.667, recall 0.219 → 0.312); pixel metrics unchanged
to three decimals. Of the 3 new matches, 2 come from high-confidence cuts that follow visible
roof boundaries; 1 (W3-03, IoU 0.51) comes from a medium-confidence cut that is only partly
correct and clears the threshold by 0.01. This is **not** evidence of general improvement:
the sample is tiny, the method was designed after inspecting W1/W3, and the 59 scene-wide cuts
outside the windows are unvalidated (several medium cuts are visibly wrong).

## Question
WHU's probability stays ≈1.0 across shared walls (see `outputs/whu_postprocessed_v1/results.md`).
Does the RGB imagery carry evidence to separate touching roofs?

## Inspection (before implementing)
Brightness- and chromaticity-gradient maps of W1/W3 at 0.1 m showed:
- strongest internal edges are not building boundaries: skylights/translucent panels, corrugation, roof-plane edges, clutter;
- some true seams are clear colour changes (orange vs grey in W1, rusty row vs grey shed in W3);
- grey-on-grey shared walls (W1-02/03/05, W3-01/02) are no stronger than corrugation.
Conclusion: edge magnitude alone would cut along skylights and ridges, so the method uses *region colour* (brightness-normalised chromaticity) plus edge support and geometric guards.

## Method (`scripts/rgb_guided_instances.py`)
Inputs (read-only): WHU baseline `mask.tif`, `probability.tif`, `run_summary.json`, source orthophoto.
1. RGB area-averaged to the 0.3 m mask grid; chromaticity r = R/(R+G+B), g = G/(R+G+B); 5×5 median (1.5 m).
2. Chroma gradient G (Sobel). Seeds = connected pixels with G ≤ scene P30 of building-pixel G; regions grown from seeds in increasing-G order strictly inside each 4-connected component.
3. Guard merges: region < 10 m², or < 25 % of its perimeter on the component's outer edge (enclosed patch, e.g. skylight) → merged into the most similar neighbour. Result = candidate regions/lines.
4. Contrast merges: merge the least-separable adjacent pair until every remaining boundary has mean-chroma difference ≥ 0.04 **and** ≥ 30 % of its length on strong chroma edges (≥ scene P75).
5. Remaining boundaries = proposed cuts, all `needs_review=True`; confidence `high` if difference ≥ 0.08 and edge support ≥ 0.5, else `medium`.
6. Polygonize per instance (baseline CRS/transform, simplify 0.15 m, min area 10 m²).

Parameters (fixed before scoring, not tuned on labels): min_contrast 0.04 (≈3× the median 3 m chroma texture of building pixels *outside* the windows, 0.013), min_edge_support 0.3, min_part 10 m², min_outer_contact 0.25, seed P30 (0.00192), strong P75 (0.00866). Percentiles are label-free scene statistics. No labels, Microsoft footprints, rectangles or centroids are used in any cut decision.

Caveat on independence: thresholds were fixed before scoring, but the method design (chromaticity, guards) was informed by looking at W1/W3 imagery — the same windows used for scoring.

## Visual review of cuts (done before scoring; imagery only, no labels shown) — `contact_sheet_cuts.png`
| cut in windows | confidence | follows a visible building boundary? |
|---|---|---|
| W1 orange roof / grey roof | high | yes |
| W3 red curved roof / blue-grey roof | high | yes |
| W3 rusty row segment / grey shed | medium | partly: bottom edge follows the real rust/grey boundary; the two vertical ends cut through the continuous rust row |

Correctly rejected: ridge candidates on both W2 houses; skylight/panel candidates on W3 sheds. Not separable: grey-on-grey merges in W1/W3.
Scene-wide (`scene_cuts_unvalidated.png`, 62 cuts: 27 high, 35 medium): high cuts mostly sit where a strongly coloured roof meets a different neighbour (plausible); several medium cuts are clearly wrong (e.g. through the large orange-tile house roof at the top of the scene, jagged lines inside single roofs). **These are unvalidated.**

## Results (unchanged `score_models.py`, reviewed labels, 0.1 m grid, same ignore areas, IoU ≥ 0.5)
| run | pixel P | pixel R | pixel IoU | building P | building R | pixel TP / FP / FN | building TP / FP / FN |
|---|---|---|---|---|---|---|---|
| WHU baseline | 0.970 | 0.845 | 0.824 | 0.583 | 0.219 | 231,364 / 7,038 / 42,340 | 7 / 5 / 25 |
| RGB-guided v1 | 0.970 | 0.845 | 0.824 | 0.667 | 0.312 | 231,348 / 7,039 / 42,356 | 10 / 5 / 22 |

Per window (baseline → RGB-guided): pixel IoU identical (W1 0.741, W2 0.870, W3 0.837, W4 0.904). Buildings matched / predicted / labels: W1 2/6/16 → 3/7/16 · W2 2/2/3 → 2/2/3 · W3 1/2/11 → 3/4/11 · W4 2/2/2 → 2/2/2.

New matches: W1-01 (IoU 0.18 → 0.92, high cut), W3-08 (0.01 → 0.80, high cut), W3-03 (0.06 → 0.51, medium cut — marginal). No previously matched label was lost.

Trade-off: building pixels are identical by construction (instance raster covers exactly the baseline mask; total polygon area 13,875.9 m² in both). The −16 TP / +1 FP pixels come only from simplification along new cut lines. Risk moves to *false cuts*: wrong medium cuts would fragment real buildings; this sample has too few such cases to measure that rate.

## Tests
`tests/test_rgb_guided.py` (7 tests, all pass): colour-contrast split of touching buildings; no split on brightness-only ridge, enclosed skylight, or sub-threshold colour difference; instances cover exactly the mask; uniform component untouched; determinism; real output CRS/validity/area (within 1 %) and `needs_review` flags; every cut vertex on the building mask. Full rerun into a temp directory is byte-identical. Existing suites (`test_postprocess.py`, `test_scoring.py`, `validate_outputs.py` on all six run dirs) pass; 57 preserved files verified unchanged (`preserved_hashes.sha256`).

## Files
`buildings.geojson` / `buildings_wgs84.geojson` (attributes `rgb_split`, `needs_review`, `split_confidence`, `mean_prob`), `cut_lines.geojson` (contrast, edge_support, boundary_m, confidence), `candidate_lines.geojson`, `instances.tif`, `candidate_regions.tif`, `mask.tif`, `overlay.png`, `contact_sheet_cuts.png`, `scene_cuts_unvalidated.png`, `evaluation/scores.{md,json}`, `run_summary.json`, `preserved_hashes.sha256`.

## Reproduce (CPU, ~35 s compute, ~90 s wall incl. I/O)
```
.venv\Scripts\python scripts\rgb_guided_instances.py outputs\lapaz_predio_bisa_whu_0.3m outputs\rgb_guided_v1
.venv\Scripts\python tests\validate_outputs.py outputs\rgb_guided_v1
.venv\Scripts\python scripts\score_models.py --labels data\validation\reviewed_labels.geojson --run whu_baseline=outputs\lapaz_predio_bisa_whu_0.3m --run rgb_guided_v1=outputs\rgb_guided_v1 --secondary-reference data\reference\ms_buildings_lapaz.geojson --out-dir outputs\rgb_guided_v1\evaluation
.venv\Scripts\python tests\test_rgb_guided.py
```
No new dependencies (numpy, scipy, rasterio, geopandas, shapely, Pillow already installed).

## Assessment and recommendation
- RGB chromaticity gives usable evidence **only where adjacent roofs differ in colour**. It cannot separate same-material neighbours, which are most of the remaining merges here.
- High-confidence cuts look trustworthy in this sample; medium cuts are mixed. A conservative deployment would apply only `high` cuts and send `medium` ones to human review.
- Whether this generalises is unknown. The next sound step is an **independent reviewed sample** (new windows not used for design) to measure both gains and false-cut rate, and in parallel to evaluate a model with boundary/instance output (e.g. building/boundary/background segmentation) on that same independent sample.
- Detected building footprints are not cadastral parcel boundaries and must not be presented as verified property boundaries.
