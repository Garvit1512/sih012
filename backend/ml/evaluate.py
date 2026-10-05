"""Evaluate a frozen new experiment once, with saved predictions and per-block scores."""
import argparse
import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import shapes
from shapely.geometry import shape
from shapely.ops import unary_union
import torch

from ..config import ROOT
from ..services.ingestion_service import sha256
from ..services.job_service import atomic_json, now
from .cover import cover_model
from .data import Tiles
from .metrics import boundary_scores, class_scores, confusion, instance_scores
from .protocol import read_manifest


def _polygons(mask, transform):
    return [shape(g) for g,v in shapes(mask.astype(np.uint8),mask=mask,transform=transform) if v]


@torch.no_grad()
def evaluate(experiment, output, split="evaluation"):
    experiment,output=Path(experiment),Path(output)
    protocol=json.loads((experiment/"protocol.json").read_text())
    training=json.loads((experiment/"training.json").read_text())
    if training.get("protocol_sha256") != sha256(experiment/"protocol.json"):
        raise ValueError("Training protocol changed after it was frozen.")
    manifest=read_manifest(protocol["manifest"]["manifest_path"])
    if manifest["manifest_sha256"] != protocol["manifest"]["manifest_sha256"]:
        raise ValueError("Dataset manifest changed after the training protocol was frozen.")
    if training["status"] != "complete" or sha256(experiment/"candidate.pth") != training["checkpoint_sha256"]:
        raise ValueError("Candidate is incomplete or differs from the recorded checkpoint.")
    if split not in ("development","evaluation"): raise ValueError("Choose development or evaluation.")
    if output.exists() and any(output.iterdir()): raise ValueError("Refusing to overwrite an evaluation.")
    output.mkdir(parents=True,exist_ok=True)
    settings=protocol["parameters"]["evaluation_settings"]
    atomic_json(output/"evaluation_lock.json",{"started_at":now(),"checkpoint_sha256":training["checkpoint_sha256"],
                "manifest_sha256":manifest["manifest_sha256"],"split":split,"settings":settings,
                "note":"Evaluation samples are viewed by this run; future tuning requires a fresh split."})
    torch.set_num_threads(2)
    architecture=training["model"]["architecture"]
    if architecture=="cover_unet":
        model=cover_model(len(manifest["classes"]),training["model"]["channels"])
        model.load_state_dict(torch.load(experiment/"candidate.pth",map_location="cpu",weights_only=True),strict=True)
    else:
        import sys
        sys.path.insert(0,str(ROOT/"scripts"))
        from run_maskrcnn_inference import load_model
        model=load_model(experiment/"candidate.pth")
    model.eval()
    data=Tiles(manifest,split,training["model"]["channels"]==4)
    classes=len(manifest["classes"])+1 if architecture=="cover_unet" else 2
    matrix=np.zeros((classes,classes),dtype=np.int64); blocks={}; predictions=[]
    for index,row in enumerate(data.rows):
        image,label=data[index]
        with rasterio.open(row["image"]) as src: transform,crs=src.transform,src.crs
        if architecture=="cover_unet":
            reference=label.numpy(); probability=torch.softmax(model(image[None]),dim=1)[0].cpu().numpy()
            prediction=probability.argmax(axis=0)
            prediction[probability.max(axis=0)<settings["confidence_threshold"]]=len(manifest["classes"])
            score=class_scores(confusion(reference,prediction,classes),[*manifest["classes"],"unknown"])
            score["unknown_fraction"]=float((prediction[reference!=255]==len(manifest["classes"])).mean()) if (reference!=255).any() else None
            if "road" in manifest["classes"]:
                from .roads import centerline_scores
                road_id=manifest["classes"].index("road")
                score["road_centerline"]=centerline_scores(reference==road_id,prediction==road_id,transform,crs.to_string(),settings["road_centerline_tolerance_m"])
        else:
            reference=(label["masks"].numpy().sum(axis=0)>0).astype(np.int64)
            raw=model([image])[0]; chosen=raw["scores"].cpu().numpy()>settings["confidence_threshold"]
            masks=raw["masks"][chosen,0].cpu().numpy()>settings["mask_threshold"]
            prediction=(masks.sum(axis=0)>0).astype(np.int64)
            refs=[unary_union(_polygons(mask.astype(bool),transform)) for mask in label["masks"].numpy()]
            preds=[unary_union(_polygons(mask,transform)) for mask in masks]
            score={"instances":instance_scores(refs,preds,settings["instance_match_iou"]),"boundary":boundary_scores(unary_union(refs),unary_union(preds),settings["boundary_tolerance_m"])}
        sample_matrix=confusion(reference,prediction,classes); matrix+=sample_matrix
        key=row["site_id"]+":"+row["block_id"]
        blocks.setdefault(key,{"sample_ids":[],"matrix":np.zeros((classes,classes),dtype=np.int64),"samples":[]})
        blocks[key]["sample_ids"].append(row["id"]); blocks[key]["matrix"]+=sample_matrix
        blocks[key]["samples"].append(score)
        path=output/f"prediction-{index:04d}.tif"
        with rasterio.open(path,"w",driver="GTiff",count=1,width=prediction.shape[1],height=prediction.shape[0],
                           dtype="uint8",crs=crs,transform=transform,compress="deflate") as dst: dst.write(prediction.astype(np.uint8),1)
        predictions.append({"sample_id":row["id"],"file":path.name,"sha256":sha256(path)})
    names=[*manifest["classes"],"unknown"] if architecture=="cover_unet" else ["background","building"]
    report={"split":split,"checkpoint_sha256":training["checkpoint_sha256"],"manifest_sha256":manifest["manifest_sha256"],
            "sample_count":len(data),"settings":settings,"overall":class_scores(matrix,names),"predictions":predictions,
            "per_block":{k:{"sample_ids":v["sample_ids"],"scores":class_scores(v["matrix"],names),"samples":v["samples"]} for k,v in blocks.items()},
            "limitations":["Results apply to these declared samples and blocks.","Image labels are not cadastral or survey verification.",
                           "Road class IoU measures surfaces; access rights and network correctness need independent reference review."]}
    if architecture=="cover_unet":
        report["overall"]["unknown_fraction"]=float(matrix[:,-1].sum()/matrix.sum()) if matrix.sum() else None
    atomic_json(output/"evaluation.json",report)
    return report


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("experiment",type=Path); parser.add_argument("output",type=Path)
    parser.add_argument("--split",choices=["development","evaluation"],default="evaluation")
    args=parser.parse_args(); print(json.dumps(evaluate(args.experiment,args.output,args.split),indent=2))
