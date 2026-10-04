---
library_name: segmentation-models-pytorch
license: apache-2.0
tags:
  - semantic-segmentation
  - building-detection
  - remote-sensing
  - aerial-imagery
  - geospatial
  - unet++
  - efficientnet
  - pytorch
datasets:
  - giswqs/WHU-Building-Dataset
metrics:
  - iou
  - dice
pipeline_tag: image-segmentation
---

# WHU Building Detection — EfficientNet-B4 + UNet++

A semantic segmentation model for **building detection** in high-resolution aerial imagery, trained on the [WHU Building Dataset](https://study.rsgis.whu.edu.cn/pages/download/building_dataset.html).

## Model Description

| Property | Value |
|----------|-------|
| **Architecture** | UNet++ |
| **Encoder** | EfficientNet-B4 (ImageNet pretrained) |
| **Framework** | [segmentation-models-pytorch](https://github.com/qubvel-org/segmentation_models.pytorch) (SMP) |
| **Training Framework** | PyTorch Lightning |
| **Input** | 3-channel RGB, 512x512 tiles |
| **Output** | 2-class mask (Background=0, Building=1) |
| **Parameters** | ~20.8M |
| **Model Size** | ~84 MB |

## Performance

Evaluated on the WHU Building Dataset test split (1,228 tiles):

| Metric | Score |
|--------|-------|
| **IoU** | 0.9054 |
| **Dice** | 0.9503 |
| **Best Val IoU** | 0.9434 |

## Training Details

- **Dataset**: WHU Building Dataset — 5,732 training tiles (512x512 RGB at 0.3m resolution)
- **Validation split**: 20% of training data
- **Optimizer**: AdamW (lr=1e-4, weight_decay=1e-4)
- **Loss**: CrossEntropyLoss
- **Epochs**: 36 (early stopping, patience=10)
- **Batch size**: 16
- **GPU**: NVIDIA RTX 6000 Ada (48GB)
- **Encoder weights**: ImageNet pretrained

## Quick Start

### Installation

```bash
pip install geoai-py timm segmentation-models-pytorch
```

### Inference with GeoAI

```python
import geoai

# Run building detection on a GeoTIFF
geoai.timm_segmentation_from_hub(
    input_path="input_image.tif",
    output_path="building_prediction.tif",
    repo_id="giswqs/whu-building-unetplusplus-efficientnet-b4",
    window_size=512,
    overlap=256,
    batch_size=4,
)

# Vectorize to building footprints
gdf = geoai.orthogonalize(
    input_path="building_prediction.tif",
    output_path="building_footprints.geojson",
    epsilon=2.0,
)
```

### Manual Loading

```python
import json
import torch
import segmentation_models_pytorch as smp

# Load config
with open("config.json") as f:
    config = json.load(f)

# Create model
model = smp.UnetPlusPlus(
    encoder_name="efficientnet-b4",
    encoder_weights=None,
    in_channels=3,
    classes=2,
)

# Load weights
state_dict = torch.load("model.pth", map_location="cpu")
model.load_state_dict(state_dict)
model.eval()
```

## Example Notebook

See the full inference notebook with visualization and analysis:

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/opengeos/geoai/blob/main/docs/examples/building_detection_whu.ipynb)

## Dataset

The [WHU Building Dataset](https://study.rsgis.whu.edu.cn/pages/download/building_dataset.html) consists of aerial imagery at 0.3m resolution with binary building masks:

- **Train**: 5,732 tiles (512x512 RGB)
- **Val**: 1,228 tiles
- **Test**: 1,228 tiles

### Reference

Ji, S., Wei, S., & Lu, M. (2019). Fully Convolutional Networks for Multisource Building Identification. *IEEE Transactions on Geoscience and Remote Sensing*, 57(1), 108-120.

## License

This model is released under the [Apache 2.0 License](https://www.apache.org/licenses/LICENSE-2.0).

## Links

- **GeoAI package**: [https://github.com/opengeos/geoai](https://github.com/opengeos/geoai)
- **Documentation**: [https://geoai.gishub.org](https://geoai.gishub.org)
