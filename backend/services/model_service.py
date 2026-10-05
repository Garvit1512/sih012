"""Local checkpoint inventory. Installing the application never downloads weights."""
import importlib.util
import json
from pathlib import Path

from ..config import ROOT
from ..errors import ApiError
from ..utils.validation import safe_id
from .ingestion_service import sha256


def models(root: Path = ROOT / "models"):
    rows = [
        {"id": "maskrcnn", "name": "Mask R-CNN building instances", "architecture": "maskrcnn",
         "checkpoint": "maskrcnn_geoai/building_footprints_usa.pth", "feature_type": "building_footprint",
         "source": "giswqs/geoai (NAIP-trained)", "packages": ["torch", "torchvision", "building_footprint_segmentation"]},
        {"id": "whu", "name": "WHU building segmentation", "architecture": "whu",
         "checkpoint": "whu/model.pth", "config": "whu/config.json", "feature_type": "building_footprint",
         "source": "giswqs/whu-building-unetplusplus-efficientnet-b4", "packages": ["torch", "segmentation_models_pytorch", "building_footprint_segmentation"]},
    ]
    registry = root / "local" / "registry.json"
    if registry.is_file():
        for row in json.loads(registry.read_text()).get("models", []):
            safe_id(row["id"], "model id")
            if row["id"] in {r["id"] for r in rows} or row["architecture"] not in ("maskrcnn", "cover_unet", "use_centroid"):
                raise ApiError("INVALID_MODEL_REGISTRY", "Duplicate model id or unsupported architecture.", 422)
            packages = ["numpy"] if row["architecture"] == "use_centroid" else ["torch", "torchvision", "building_footprint_segmentation"] if row["architecture"] == "maskrcnn" else ["torch", "segmentation_models_pytorch", "skimage"]
            rows.append({**row, "packages": packages})
    for row in rows:
        path = (root / row["checkpoint"]).resolve()
        if root.resolve() not in path.parents:
            raise ApiError("INVALID_MODEL_REGISTRY", "Checkpoints must be within the models directory.", 422)
        missing = [p for p in row["packages"] if importlib.util.find_spec(p) is None]
        row["available"] = path.is_file() and not missing
        row["missing"] = missing + ([] if path.is_file() else ["checkpoint"])
        row["status"] = "candidate"
    return rows


def require_model(model_id, root=ROOT / "models"):
    row = next((r for r in models(root) if r["id"] == model_id), None)
    if row is None:
        raise ApiError("MODEL_NOT_FOUND", "Unknown model id.", 404)
    if not row["available"]:
        raise ApiError("MODEL_UNAVAILABLE", "Install the runtime and provide the local checkpoint before inference.", 422,
                       {"missing": row["missing"]})
    row = dict(row, checkpoint_path=str((root / row["checkpoint"]).resolve()))
    row["checkpoint_sha256"] = sha256(Path(row["checkpoint_path"]))
    if row.get("sha256") and row["sha256"] != row["checkpoint_sha256"]:
        raise ApiError("CHECKPOINT_CHANGED", "The checkpoint differs from its registered hash.", 409)
    if row.get("config"):
        row["model_config"] = json.loads((root / row["config"]).read_text())
    return row
