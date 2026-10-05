"""Frozen functional-use experiments from reviewed local tabular references."""
import argparse
import json
import math
import re
from pathlib import Path

import numpy as np
from shapely.geometry import box
from shapely.strtree import STRtree

from ..services.ingestion_service import sha256
from ..services.job_service import atomic_json, now
from ..utils.crs import metric_crs
from .functional_use import FEATURES, fit, predict
from .metrics import class_scores


def read_manifest(path):
    path = Path(path).resolve()
    data = json.loads(path.read_text())
    if data.get("schema_version") != 1 or data.get("task") != "functional_use":
        raise ValueError("Expected a version-1 functional_use manifest.")
    metric_crs(data.get("analysis_crs", ""))
    if not data.get("licence", "").strip() or data["licence"].strip().lower() == "unknown":
        raise ValueError("Record the permitted reference licence before training.")
    classes = data.get("classes", [])
    if not isinstance(classes, list) or any(not isinstance(c, str) or not c.strip() or c != c.strip() or c.lower() == "unknown" for c in classes) or len(classes) < 2 or len(set(classes)) != len(classes):
        raise ValueError("Declare at least two unique use classes; unknown is reserved for rejection.")
    rows = data.get("samples", [])
    if not rows or len(rows) > 5000:
        raise ValueError("Supply 1–5,000 reviewed samples.")
    ids, blocks, hashes, boxes = set(), {}, {}, []
    splits = {"train", "development", "evaluation"}
    for row in rows:
        key = (row.get("site_id"), row.get("id"))
        split = row.get("split")
        block = (row.get("site_id"), row.get("block_id"))
        if any(not isinstance(row.get(k), str) or not row[k].strip() for k in ("id", "site_id", "block_id")) or key in ids or split not in splits:
            raise ValueError("Samples need unique site/id values, block IDs and declared splits.")
        ids.add(key)
        if block in blocks and blocks[block] != split:
            raise ValueError("Functional-use spatial block leaks across splits.")
        blocks[block] = split
        digest = row.get("geometry_sha256", "")
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or digest in hashes and hashes[digest] != split:
            raise ValueError("Record geometry hashes and keep duplicate geometry out of other splits.")
        hashes[digest] = split
        refs = row.get("evidence_ids")
        if row.get("reviewed") is not True or not isinstance(refs, list) or not refs or any(not isinstance(r, str) or not r.strip() for r in refs):
            raise ValueError("Every use reference needs reviewed source evidence.")
        if row.get("use_label") not in [*classes, "unknown"] or split == "train" and row["use_label"] == "unknown":
            raise ValueError("Use labels must match the declared classes; train labels cannot be unknown.")
        if split == "evaluation" and row.get("labels_locked_before_predictions") is not True:
            raise ValueError("Fresh use evaluation labels must be locked before predictions.")
        values = [row.get(f) for f in FEATURES]
        if any(v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0) for v in values):
            raise ValueError("Use attributes must be finite non-negative numbers or explicit nulls.")
        if split == "train" and any(v is None for v in values):
            raise ValueError("Training needs all declared building/road attributes.")
        bounds = row.get("bounds_analysis", [])
        if len(bounds) != 4 or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in bounds) or bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
            raise ValueError("Record each sample's bounding box in the analysis CRS.")
        boxes.append(box(*bounds))
    if {r["split"] for r in rows} != splits:
        raise ValueError("Supply train, development and fresh evaluation samples.")
    buffer = data.get("spatial_buffer_m", 50)
    if isinstance(buffer, bool) or not isinstance(buffer, (int, float)) or not math.isfinite(buffer) or buffer < 0:
        raise ValueError("Use a finite non-negative spatial buffer.")
    tree = STRtree(boxes)
    for i, geometry in enumerate(boxes):
        for j in tree.query(geometry.buffer(buffer), predicate="intersects"):
            if i != j and rows[i]["split"] != rows[j]["split"]:
                raise ValueError("Functional-use samples overlap or violate the cross-split spatial buffer.")
    return {**data, "manifest_path": str(path), "manifest_sha256": sha256(path)}


def train(manifest_path, output, max_distance=3.0):
    manifest = read_manifest(manifest_path)
    if not math.isfinite(max_distance) or max_distance <= 0:
        raise ValueError("Freeze a finite positive rejection distance.")
    model = fit(manifest["samples"], max_distance)
    if set(model["centers"]) != set(manifest["classes"]):
        raise ValueError("Every declared class needs reviewed training examples.")
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError("Refusing to overwrite a use experiment.")
    output.mkdir(parents=True, exist_ok=True)
    atomic_json(output / "protocol.json", {"dataset": manifest, "parameters": {"max_distance": max_distance, "ambiguity_margin": 0.1},
        "frozen_at": now(), "selection": "predeclared nearest-centroid baseline; no evaluation selection"})
    atomic_json(output / "candidate.json", {**model, "ambiguity_margin": 0.1, "analysis_crs": manifest["analysis_crs"]})
    report = {"status": "complete", "protocol_sha256": sha256(output / "protocol.json"), "model": {"architecture": "use_centroid", "feature_type": "building_footprint",
        "classes": manifest["classes"], "analysis_crs": manifest["analysis_crs"]}, "checkpoint_sha256": sha256(output / "candidate.json"),
        "manifest_sha256": manifest["manifest_sha256"], "train_samples": sum(r["split"] == "train" for r in manifest["samples"]),
        "limitations": ["Local geometric/context attributes do not establish ownership or use without reviewed references.",
                         "Fresh-reference class/unknown evaluation required; registration is not promotion."]}
    atomic_json(output / "training.json", report)
    return report


def evaluate(experiment, output, split="evaluation"):
    experiment, output = Path(experiment), Path(output)
    if split not in ("development", "evaluation"):
        raise ValueError("Choose development or evaluation.")
    protocol = json.loads((experiment / "protocol.json").read_text())
    report = json.loads((experiment / "training.json").read_text())
    manifest = read_manifest(protocol["dataset"]["manifest_path"])
    if manifest["manifest_sha256"] != protocol["dataset"]["manifest_sha256"] or report["status"] != "complete" or sha256(experiment / "candidate.json") != report["checkpoint_sha256"] or sha256(experiment / "protocol.json") != report["protocol_sha256"]:
        raise ValueError("Use manifest/model changed after the experiment was frozen.")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Refusing to overwrite a use evaluation.")
    output.mkdir(parents=True, exist_ok=True)
    atomic_json(output / "evaluation_lock.json", {"started_at": now(), "split": split, "manifest_sha256": manifest["manifest_sha256"],
        "checkpoint_sha256": report["checkpoint_sha256"], "settings": protocol["parameters"],
        "note": "This run views these evaluation labels; future tuning needs a fresh split."})
    model = json.loads((experiment / "candidate.json").read_text())
    names = [*manifest["classes"], "unknown"]
    matrix = np.zeros((len(names), len(names)), dtype=np.int64)
    blocks, predictions = {}, []
    for row in manifest["samples"]:
        if row["split"] != split:
            continue
        result = predict(model, row)
        truth, predicted = names.index(row["use_label"]), names.index(result["functional_use_suggestion"])
        matrix[truth, predicted] += 1
        block = row["site_id"] + ":" + row["block_id"]
        blocks.setdefault(block, np.zeros_like(matrix))[truth, predicted] += 1
        predictions.append({"id": row["id"], "site_id": row["site_id"], "use_label": row["use_label"], **result})
    atomic_json(output / "predictions.json", predictions)
    evaluated = {"split": split, "sample_count": len(predictions), "manifest_sha256": manifest["manifest_sha256"],
        "checkpoint_sha256": report["checkpoint_sha256"], "overall": class_scores(matrix, names),
        "unknown_fraction": float(matrix[:, -1].sum() / matrix.sum()),
        "per_block": {key: class_scores(value, names) for key, value in blocks.items()},
        "predictions_sha256": sha256(output / "predictions.json"), "limitations": report["limitations"]}
    atomic_json(output / "evaluation.json", evaluated)
    return evaluated


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    training = commands.add_parser("train")
    training.add_argument("manifest", type=Path); training.add_argument("output", type=Path)
    training.add_argument("--max-distance", type=float, default=3.0)
    scoring = commands.add_parser("evaluate")
    scoring.add_argument("experiment", type=Path); scoring.add_argument("output", type=Path)
    scoring.add_argument("--split", choices=("development", "evaluation"), default="evaluation")
    args = parser.parse_args()
    result = train(args.manifest, args.output, args.max_distance) if args.command == "train" else evaluate(args.experiment, args.output, args.split)
    print(json.dumps(result, indent=2))
