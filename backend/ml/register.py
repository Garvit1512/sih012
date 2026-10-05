"""Register a trained local candidate for extraction; registration is not model promotion."""
import argparse
import json
import shutil
from pathlib import Path

from ..config import ROOT
from ..services.ingestion_service import sha256
from ..services.job_service import atomic_json
from ..utils.validation import safe_id


def register(experiment, model_id, name, model_root=ROOT/"models"):
    safe_id(model_id,"model id")
    if model_id in ("whu","maskrcnn"): raise ValueError("Use a new candidate id; preserve historical models.")
    experiment=Path(experiment)
    report=json.loads((experiment/"training.json").read_text())
    use_candidate=report["model"]["architecture"]=="use_centroid"
    checkpoint=experiment/("candidate.json" if use_candidate else "candidate.pth")
    if report["status"]!="complete" or sha256(checkpoint)!=report["checkpoint_sha256"]:
        raise ValueError("Training/checkpoint is incomplete or changed.")
    if report.get("protocol_sha256") and sha256(experiment/"protocol.json")!=report["protocol_sha256"]:
        raise ValueError("Experiment protocol changed after training.")
    destination=model_root/"local"/model_id
    if destination.exists(): raise ValueError("Candidate already exists; use a new id.")
    registry=model_root/"local"/"registry.json"
    rows=json.loads(registry.read_text()).get("models",[]) if registry.is_file() else []
    if model_id in {r["id"] for r in rows}: raise ValueError("Candidate id already registered.")
    filename="model.json" if use_candidate else "model.pth"
    destination.mkdir(parents=True); shutil.copyfile(checkpoint,destination/filename)
    shutil.copyfile(experiment/"protocol.json",destination/"protocol.json")
    shutil.copyfile(experiment/"training.json",destination/"training.json")
    row={**report["model"],"id":model_id,"name":name,"checkpoint":str((destination/filename).relative_to(model_root)),
         "sha256":report["checkpoint_sha256"],"source":("local functional-use experiment: " if use_candidate else "local fine-tuning experiment: ")+str(experiment.resolve()),
         "status":"candidate","accuracy_status":"fresh-reference validation pending"}
    atomic_json(registry,{"models":[*rows,row]}); return row


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("experiment",type=Path); parser.add_argument("model_id"); parser.add_argument("--name",required=True)
    parser.add_argument("--model-root",type=Path,default=ROOT/"models")
    args=parser.parse_args(); print(json.dumps(register(args.experiment,args.model_id,args.name,args.model_root),indent=2))
