"""Isolated local job entry point. Input is a server-created specification file."""
import json
import sys
import time
from pathlib import Path

from .services.ingestion_service import sha256
from .services.job_service import atomic_json
from .services.prediction_service import import_features, inference, validate_features


def run(path):
    spec = json.loads(path.read_text())
    manifest_path = Path(spec["site_manifest"])
    manifest = json.loads(manifest_path.read_text())
    output = Path(spec["output_dir"])
    output.mkdir(exist_ok=False)
    started = time.monotonic()

    def progress(stage, fraction):
        atomic_json(Path(spec["progress_file"]), {"stage": stage, "fraction": fraction})
        print(stage, flush=True)

    progress("validating inputs", 0.1)
    payload = spec["payload"]
    if payload["kind"] == "import_layer":
        fc, method = import_features(manifest, payload), "imported_predictions"
    elif payload["kind"] == "use_classification":
        from .ml.functional_use import attributes, predict
        model = spec["model"]
        if sha256(Path(model["checkpoint_path"])) != model["checkpoint_sha256"]:
            raise ValueError("Use candidate changed after the job was queued.")
        candidate = json.loads(Path(model["checkpoint_path"]).read_text())
        inputs = spec["use_inputs"]
        atomic_json(output / "use_inputs.json", inputs)
        progress("computing building and road attributes", 0.35)
        rows = attributes(inputs["building"]["features"], inputs["road"]["features"], manifest["analysis_crs"])
        features = []
        for index, (feature, row) in enumerate(zip(inputs["building"]["features"], rows), 1):
            prediction = predict(candidate, row)
            features.append({"type": "Feature", "geometry": feature["geometry"], "properties": {
                "source_id": index, "feature_type": "building_footprint", "functional_use": "unknown",
                "functional_use_suggestion": prediction["functional_use_suggestion"], "flags": "unvalidated_use_candidate",
                "use_candidate": {"model_id": model["id"], "model_sha256": model["checkpoint_sha256"],
                    "building_layer_id": inputs["building"]["layer_id"], "source_feature_id": feature["id"],
                    "road_layer_id": inputs["road"]["layer_id"], "attributes": row,
                    "distance": prediction.get("distance"), "reason": prediction["reason"], "calibrated_probability": None},
            }})
        fc, method = {"type": "FeatureCollection", "features": features}, "functional_use_suggestions"
    else:
        fc, method = inference(manifest, manifest_path.parent, payload, spec["model"], output, progress)
    progress("validating outputs", 0.85)
    validate_features(manifest, fc)
    atomic_json(output / "features.geojson", fc)
    model = spec.get("model")
    result = {"name": payload["name"], "type": method, "job_id": spec["job_id"], "site_id": spec["site_id"],
              "source": model.get("source") if model else payload["source"], "parameters": {k: v for k, v in payload.items() if k != "features"},
              "model": model, "imagery_sha256": manifest["imagery"].get("source_sha256"),
              "feature_count": len(fc["features"]), "runtime_seconds": round(time.monotonic() - started, 3),
              "validation": "geometry and software checks only; accuracy unverified",
              "files": {p.name: sha256(p) for p in output.iterdir() if p.is_file()}}
    atomic_json(output / "result.json", result)
    progress("ready to publish", 0.95)


if __name__ == "__main__":
    run(Path(sys.argv[1]))
