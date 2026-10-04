# Pretrained building-footprint inference (prototype)

## Inputs found in the repo
| File | Location (from raster metadata) | CRS | Native res | Size | Notes |
|---|---|---|---|---|---|
| `data/lapaz_predio_bisa.tif` | La Paz, Bolivia (lon −68.0565…−68.0536, lat −16.5403…−16.5371) | EPSG:32719 (UTM 19S) | 0.05 m | 6240×7080 RGB uint8 | Pix4D orthophoto, JPEG-compressed, internal mask (~36% nodata collar) |
| `data/sf_church_st_sample.tif` | San Francisco, USA (lon −122.4262…−122.4256, lat 37.7635…37.7641) | EPSG:32610 (UTM 10N) | 0.02 m | 3000×3000 RGB uint8 | Heavy orthomosaic smearing artefacts — poor test image |

Checkpoint: `models/DlinkNet.zip` → `models/best.pt` (375 MB). Upstream README: "DlinkNet trained on Massachusetts Buildings Dataset" (~1 m/px aerial imagery).

## What the checkpoint actually is (verified)
- `TrainStateCallback` dict: `model`, `optimizer`, `start_epoch=70`, `end_epoch=100`, `step=12110`, `bst_vld_loss=0.2452` (a training loss, not an accuracy figure).
- `model`: 314 tensors with `module.` (DataParallel) prefix → stripped with the package's `adjust_model`; loads into `DLinkNet34(num_classes=1)` with `strict=True`.
- Loaded with `torch.load(map_location="cpu", weights_only=True)` plus a narrow allowlist (`numpy scalar`, `numpy dtype`, `_codecs.encode`) — the only non-tensor globals in the pickle, confirmed with `pickletools` before loading.
- Preprocessing/postprocessing follow upstream `examples/Prediction.ipynb`: RGB, 384×384 input, `min_max_image_net` normalisation, `sigmoid` on logits.

## Setup performed
```
.venv\Scripts\python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
```
(torch 2.14.1+cpu, torchvision 0.29.1+cpu; they were not installed before.)

## Commands
```
python scripts\run_building_inference.py data\lapaz_predio_bisa.tif
python scripts\run_building_inference.py data\sf_church_st_sample.tif
python tests\validate_outputs.py outputs\lapaz_predio_bisa_0.4m outputs\sf_church_st_sample_0.4m
```
Useful options: `--target-res` (m/px fed to model, default 0.4; 0 = native), `--threshold` (default 0.4, the package's `to_binary` cutoff), `--min-area` (m², default 10), `--simplify` (m).

Outputs in `outputs/<stem>_<res>/`: `buildings.geojson` (source CRS), `buildings_wgs84.geojson` (EPSG:4326), `mask.tif`, `probability.tif`, `mask_preview.png`, `overlay.png` (vectors drawn back through the inverse affine), `run_summary.json`.

## Observed results (CPU, this machine)
| Run | Grid | Tiles | Time | Building px | Polygons |
|---|---|---|---|---|---|
| La Paz @0.4 m | 780×885 | 9 | 3.3 s total | 3.7% of valid area | 6 |
| SF @0.4 m | 150×150 | 1 | 1.3 s | 41.2% | 3 |
| SF native 0.02 m | 3000×3000 | 100 | 26.5 s | 0.4% | 0 |

Resolution sweep on La Paz (fraction of valid area > 0.4): 1.0 m 0.000, 0.6 m 0.002, 0.4 m 0.037, 0.3 m 0.026, 0.2 m 0.010. `divide_255_image_net` vs `min_max_image_net` gave practically identical results.

Validation (`tests/validate_outputs.py`) passes on all runs: CRS equals source, bounds match, all geometries valid, counts consistent, vector→mask round-trip IoU 0.993 / 0.997, WGS84 export reprojects back exactly, all polygons on valid pixels. Visual overlays show polygons sitting on roofs.

## Limitations (important)
- **Recall on La Paz is poor.** Only a few large/bright roofs are detected; most houses, corrugated-metal and tiled roofs are missed. Detections that do appear are on real roofs. Likely cause: domain shift (Massachusetts suburban 1 m imagery vs. Andean 5 cm drone imagery). No ground truth exists here, so **no accuracy number is claimed**.
- Best `--target-res` (≈0.4 m) was chosen empirically on this one image, not validated generally.
- Adjacent roofs merge into single polygons (semantic, not instance segmentation). Polygons are raster-stepped, lightly simplified, not regularised.
- **These are detected building/roof footprints, not cadastral parcels.** Legal boundaries cannot be inferred from imagery; they require survey records and ground verification.
- Next step for usable quality would be fine-tuning on local labelled data (intentionally not done here).
