"""Synthetic use classes verify experiment/jobs behavior, never functional-use accuracy."""
import copy
import hashlib
import json
import subprocess
import sys

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.ml.register import register
from backend.ml.use_experiment import read_manifest, train, evaluate
from backend.services.model_service import require_model
from test_jobs import finish, payload
from test_site_workflow import configured, fixture_sites, geom


def write_use_fixture(tmp_path):
    rows = []
    for i, (split, label) in enumerate([("train", "class-a"), ("train", "class-a"), ("train", "class-b"), ("train", "class-b"),
                                       ("development", "class-a"), ("development", "class-b"), ("evaluation", "class-a"), ("evaluation", "class-b")]):
        rows.append({"id": str(i), "site_id": "synthetic", "block_id": str(i), "split": split,
            "reviewed": True, "labels_locked_before_predictions": True, "evidence_ids": ["synthetic-reference-" + str(i)],
            "use_label": label, "bounds_analysis": [500000+i*100,2500000,500010+i*100,2500010],
            "geometry_sha256": hashlib.sha256(f"synthetic-geometry-{i}".encode()).hexdigest(),
            "area_m2": 100 if label == "class-a" else 500, "compactness": np.pi/4 if label == "class-a" else .2,
            "distance_to_road_m": 2 if label == "class-a" else 30, "neighbours_within_50m": 0 if label == "class-a" else 10})
    rows[-1]["distance_to_road_m"] = None  # Missing inputs must abstain during evaluation.
    manifest = {"schema_version": 1, "task": "functional_use", "analysis_crs": "EPSG:32643", "licence": "synthetic QA fixture",
                "classes": ["class-a", "class-b"], "spatial_buffer_m": 50, "samples": rows}
    path = tmp_path / "use-manifest.json"
    path.write_text(json.dumps(manifest))
    return path


@pytest.fixture
def use_manifest(tmp_path):
    return write_use_fixture(tmp_path)


def test_use_manifest_rejects_leakage_and_unreviewed_labels(use_manifest, tmp_path):
    original = json.loads(use_manifest.read_text())
    for field, value, message in (("labels_locked_before_predictions", False, "locked"), ("reviewed", False, "evidence"),
                                  ("evidence_ids", [], "evidence"), ("block_id", "0", "leaks"),
                                  ("geometry_sha256", original["samples"][0]["geometry_sha256"], "geometry"),
                                  ("bounds_analysis", original["samples"][0]["bounds_analysis"], "buffer")):
        data = copy.deepcopy(original); data["samples"][-1][field] = value
        path = tmp_path / "bad.json"; path.write_text(json.dumps(data))
        with pytest.raises(ValueError, match=message): read_manifest(path)
    assert read_manifest(use_manifest)["classes"] == ["class-a", "class-b"]


def test_use_cli_evaluation_freeze_and_registration(use_manifest, tmp_path):
    experiment = tmp_path / "experiment"
    result = subprocess.run([sys.executable, "-m", "backend.ml.use_experiment", "train", str(use_manifest), str(experiment)],
                            check=True, capture_output=True, text=True, timeout=30)
    assert json.loads(result.stdout)["train_samples"] == 4
    scored = evaluate(experiment, tmp_path / "evaluation")
    assert scored["sample_count"] == 2 and scored["unknown_fraction"] == .5
    predictions = json.loads((tmp_path / "evaluation/predictions.json").read_text())
    assert predictions[0]["functional_use_suggestion"] == "class-a" and predictions[0]["functional_use"] == "unknown"
    assert predictions[1]["functional_use_suggestion"] == "unknown"
    assert len(scored["per_block"]) == 2
    with pytest.raises(ValueError, match="overwrite"): train(use_manifest, experiment)
    with pytest.raises(ValueError, match="overwrite"): evaluate(experiment, tmp_path / "evaluation")
    root = tmp_path / "models"
    registered = register(experiment, "synthetic-use", "Synthetic use candidate", root)
    assert registered["architecture"] == "use_centroid" and require_model("synthetic-use", root)["available"]
    with pytest.raises(ValueError, match="already"): register(experiment, "synthetic-use", "Synthetic", root)
    protocol = json.loads((experiment / "protocol.json").read_text()); protocol["parameters"]["max_distance"] = 99
    (experiment / "protocol.json").write_text(json.dumps(protocol))
    with pytest.raises(ValueError, match="changed"): evaluate(experiment, tmp_path / "tampered")
    with pytest.raises(ValueError, match="changed"): register(experiment, "changed-use", "Changed", root)


def test_use_job_suggestions_require_manual_evidence_review(fixture_sites, use_manifest, tmp_path):
    experiment = tmp_path / "use-experiment"; train(use_manifest, experiment)
    root = tmp_path / "models"; register(experiment, "synthetic-use", "Synthetic candidate", root)
    fixture_sites.models_dir = root
    with TestClient(create_app(fixture_sites)) as client:
        buildings = client.post("/api/sites/first/jobs", json=payload()).json()
        assert finish(client, buildings["id"])["status"] == "succeeded"
        road_payload = {**payload(geom(offset=12, width=3)), "feature_type": "road_surface", "name": "Synthetic road"}
        roads = client.post("/api/sites/first/jobs", json=road_payload).json()
        assert finish(client, roads["id"])["status"] == "succeeded"
        body = {"kind": "use_classification", "model_id": "synthetic-use", "name": "Synthetic use suggestions",
                "building_layer_id": buildings["id"], "road_layer_id": roads["id"]}
        wrong_model_task = client.post("/api/sites/first/jobs", json={**body, "kind": "inference"})
        assert wrong_model_task.status_code == 422 and wrong_model_task.json()["error"]["code"] == "MODEL_TASK_MISMATCH"
        wrong_inputs = client.post("/api/sites/first/jobs", json={**body, "road_layer_id": buildings["id"]})
        assert wrong_inputs.status_code == 422 and wrong_inputs.json()["error"]["code"] == "USE_INPUTS_REQUIRED"
        queued = client.post("/api/sites/first/jobs", json=body)
        assert queued.status_code == 202, queued.text
        completed = finish(client, queued.json()["id"])
        assert completed["status"] == "succeeded", completed
        feature = client.get(f"/api/sites/first/layers/{completed['layer_id']}/features").json()["features"][0]
        props = feature["properties"]
        assert props["functional_use"] == "unknown" and props["functional_use_suggestion"] == "class-a"
        assert props["use_candidate"]["road_layer_id"] == roads["id"] and props["use_candidate"]["calibrated_probability"] is None
        artifacts = client.get(f"/api/jobs/{completed['id']}/artifacts").json()["files"]
        assert any(f["name"] == "use_inputs.json" for f in artifacts)
        copied = client.post("/api/review/workspace", json={"site_id": "first", "expected_revision": 0,
            "layer_id": completed["layer_id"], "feature_ids": [feature["id"]]}).json()["features"][0]
        assert copied["properties"]["functional_use_suggestion"] == "class-a" and copied["properties"]["functional_use"] == "unknown"
        fid = copied["id"]
        client.post(f"/api/review/features/{fid}/accept", json={"site_id": "first", "expected_revision": 1})
        assert client.get("/api/review/workspace?site_id=first").json()["features"][0]["properties"]["functional_use"] == "unknown"
        assert client.post(f"/api/review/features/{fid}/functional-use", json={"site_id": "first", "expected_revision": 2,
            "functional_use": "class-a", "evidence_refs": []}).status_code == 422
        corrected = client.post(f"/api/review/features/{fid}/functional-use", json={"site_id": "first", "expected_revision": 2,
            "functional_use": "class-a", "evidence_refs": ["synthetic-reviewed-observation"]})
        assert corrected.json()["features"][0]["properties"]["functional_use"] == "class-a"
        edited = client.post(f"/api/review/features/{fid}/edit", json={"site_id": "first", "expected_revision": 3, "geometry": geom(width=11)})
        assert edited.json()["features"][0]["properties"]["functional_use_suggestion"] == "unknown"
