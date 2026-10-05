"""Train a new local candidate; never change historical weights or outputs.

python -m backend.ml.train MANIFEST OUTPUT --architecture cover_unet --epochs 20
python -m backend.ml.train MANIFEST OUTPUT --architecture maskrcnn --checkpoint LOCAL_WEIGHTS
"""
import argparse
import json
import math
import random
import sys
from pathlib import Path

import numpy as np
import torch

from ..config import ROOT
from ..services.ingestion_service import sha256
from ..services.job_service import atomic_json
from .cover import cover_model
from .data import Tiles
from .metrics import class_scores, confusion
from .protocol import read_manifest


@torch.no_grad()
def development_score(model, data, classes):
    model.eval(); matrix=np.zeros((classes,classes),dtype=np.int64)
    for image,labels in data:
        prediction=model(image[None]).argmax(dim=1)[0].cpu().numpy()
        matrix+=confusion(labels.numpy(),prediction,classes)
    return class_scores(matrix,[str(i) for i in range(classes)])


def train(manifest_path, output, architecture="cover_unet", checkpoint=None, epochs=20, learning_rate=1e-4,
          seed=42, use_height=False):
    manifest=read_manifest(manifest_path)
    if not 1<=epochs<=200 or not 0<learning_rate<=.1: raise ValueError("Unsupported epochs/learning rate.")
    if (architecture=="cover_unet") != (manifest["task"]=="cover"):
        raise ValueError("Architecture does not match the manifest task.")
    torch.set_num_threads(2); torch.manual_seed(seed); random.seed(seed); np.random.seed(seed)
    channels=4 if use_height else 3
    if architecture=="cover_unet":
        model=cover_model(len(manifest["classes"]),channels)
        if checkpoint: model.load_state_dict(torch.load(checkpoint,map_location="cpu",weights_only=True),strict=True)
    elif architecture=="maskrcnn":
        if not checkpoint: raise ValueError("Building fine-tuning needs a local starting checkpoint.")
        if use_height: raise ValueError("Mask R-CNN fine-tuning is RGB only.")
        sys.path.insert(0,str(ROOT/"scripts"))
        from run_maskrcnn_inference import load_model
        model=load_model(Path(checkpoint))
        for parameter in model.backbone.parameters(): parameter.requires_grad=False
    else: raise ValueError("Unsupported architecture.")
    training=Tiles(manifest,"train",use_height); development=Tiles(manifest,"development",use_height)
    output=Path(output)
    if output.exists() and any(output.iterdir()): raise ValueError("Refusing to overwrite a non-empty experiment.")
    output.mkdir(parents=True,exist_ok=True)
    parameters={"architecture":architecture,"epochs":epochs,"learning_rate":learning_rate,"seed":seed,
                "device":"cpu","channels":channels,"preprocessing":"RGB/255; height clipped [-5,60]m / 60 if enabled",
                "backbone_frozen":architecture=="maskrcnn","batch_size":1,"torch_version":torch.__version__,
                "initial_checkpoint_sha256":sha256(Path(checkpoint)) if checkpoint else None,
                "selection":"development macro IoU" if architecture=="cover_unet" else "final predeclared epoch; no test-based selection"}
    parameters["evaluation_settings"]={"confidence_threshold":.5,"mask_threshold":.5,"instance_match_iou":.5,
                                       "boundary_tolerance_m":.3,"road_centerline_tolerance_m":1.0,
                                       "pixel_grid":"native reviewed tile grid","geometry_min_area_m2":0}
    atomic_json(output/"protocol.json",{"manifest":manifest,"parameters":parameters})
    optimizer=torch.optim.AdamW([p for p in model.parameters() if p.requires_grad],lr=learning_rate)
    report={"status":"training","parameters":parameters,"epochs":[],"protocol_sha256":sha256(output/"protocol.json"),
            "accuracy_status":"unverified on fresh evaluation"}
    best=-math.inf
    try:
        for epoch in range(epochs):
            model.train(); losses=[]
            order=list(range(len(training))); random.shuffle(order)
            for index in order:
                image,target=training[index]; optimizer.zero_grad()
                if architecture=="cover_unet":
                    if max(image.shape[-2:])<64:
                        image=torch.nn.functional.pad(image,(0,max(0,64-image.shape[-1]),0,max(0,64-image.shape[-2])))
                        target=torch.nn.functional.pad(target,(0,max(0,64-target.shape[-1]),0,max(0,64-target.shape[-2])),value=255)
                    loss=torch.nn.functional.cross_entropy(model(image[None]),target[None],ignore_index=255)
                else: loss=sum(model([image],[target]).values())
                if not torch.isfinite(loss): raise ValueError("Non-finite loss; check valid label coverage.")
                loss.backward(); optimizer.step(); losses.append(float(loss.detach()))
            score=development_score(model,development,len(manifest["classes"])) if architecture=="cover_unet" else None
            row={"epoch":epoch+1,"training_loss":float(np.mean(losses)),"development":score}
            report["epochs"].append(row)
            selection=score["macro_iou"] if score else epoch
            if selection is None: raise ValueError("Development split has no evaluated pixels.")
            if selection>best:
                best=selection; torch.save(model.state_dict(),output/"candidate.pth"); report["selected_epoch"]=epoch+1
            atomic_json(output/"training.json",report)
            print(json.dumps({"epoch":epoch+1,"training_loss":row["training_loss"],"development_macro_iou":score["macro_iou"] if score else None}),flush=True)
        report.update(status="complete",checkpoint_sha256=sha256(output/"candidate.pth"),model={"architecture":architecture,
            "classes":manifest.get("classes"),"channels":channels,"feature_type":"land_cover" if architecture=="cover_unet" else "building_footprint"})
        atomic_json(output/"training.json",report)
        return report
    except Exception as error:
        report.update(status="failed",error=str(error)); atomic_json(output/"training.json",report); raise


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest",type=Path); parser.add_argument("output",type=Path)
    parser.add_argument("--architecture",choices=["cover_unet","maskrcnn"],required=True)
    parser.add_argument("--checkpoint",type=Path); parser.add_argument("--epochs",type=int,default=20)
    parser.add_argument("--learning-rate",type=float,default=1e-4); parser.add_argument("--seed",type=int,default=42)
    parser.add_argument("--use-height",action="store_true")
    args=parser.parse_args()
    train(args.manifest,args.output,args.architecture,args.checkpoint,args.epochs,args.learning_rate,args.seed,args.use_height)
