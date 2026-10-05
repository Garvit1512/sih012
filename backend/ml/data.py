import numpy as np
import rasterio
import torch
from torch.utils.data import Dataset


class Tiles(Dataset):
    def __init__(self, manifest, split, use_height=False):
        self.rows = [s for s in manifest["samples"] if s["split"] == split]
        self.task, self.use_height = manifest["task"], use_height
        if use_height and (self.task != "cover" or not all(s.get("ndsm") for s in self.rows)):
            raise ValueError("Height ablation requires verified nDSM and a cover experiment.")

    def __len__(self): return len(self.rows)

    def __getitem__(self, index):
        row = self.rows[index]
        with rasterio.open(row["image"]) as src:
            image = src.read().astype(np.float32) / 255
            valid = src.dataset_mask() > 0
        with rasterio.open(row["labels"]) as src: label = src.read(1).astype(np.int64)
        if self.use_height:
            with rasterio.open(row["ndsm"]) as src:
                z = src.read(1).astype(np.float32)
                valid &= (src.dataset_mask() > 0) & np.isfinite(z)
            # Fixed, recorded preprocessing; do not fit normalization on evaluation data.
            image = np.concatenate([image, np.clip(np.nan_to_num(z, nan=0), -5, 60)[None] / 60])
        if self.task == "cover":
            label[~valid] = 255
            return torch.from_numpy(image), torch.from_numpy(label)
        ids = [i for i in np.unique(label) if i > 0]
        masks, boxes = [], []
        for i in ids:
            mask = label == i; y, x = np.nonzero(mask)
            if x.max() == x.min() or y.max() == y.min(): continue
            masks.append(mask); boxes.append([x.min(), y.min(), x.max() + 1, y.max() + 1])
        boxes = torch.tensor(np.array(boxes, dtype=np.float32).reshape(-1, 4))
        target = {"boxes": boxes, "labels": torch.ones(len(boxes), dtype=torch.int64),
                  "masks": torch.tensor(np.array(masks, dtype=np.uint8).reshape(-1, *label.shape)),
                  "image_id": torch.tensor(index), "area": (boxes[:,2]-boxes[:,0])*(boxes[:,3]-boxes[:,1]),
                  "iscrowd": torch.zeros(len(boxes), dtype=torch.int64)}
        return torch.from_numpy(image), target
