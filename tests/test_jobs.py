"""Actual subprocess worker tests with synthetic vectors and georeferencing."""
import json
import time

from fastapi.testclient import TestClient

from backend.main import create_app
from backend.services.job_service import atomic_json
from test_site_workflow import configured, fixture_sites, geom


def payload(geometry=None):
    return {"kind": "import_layer", "name": "Synthetic buildings", "source": "synthetic test fixture",
            "features": {"type": "FeatureCollection", "features": [{"type": "Feature", "id": "fixture-1",
                       "geometry": geometry or geom(), "properties": {}}]}}


def finish(client, job_id):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        job = client.get("/api/jobs/" + job_id).json()
        if job["status"] in ("succeeded", "failed", "cancelled", "interrupted"):
            return job
        time.sleep(0.1)
    raise AssertionError("worker did not finish")


def test_worker_publishes_then_review_and_export(fixture_sites):
    with TestClient(create_app(fixture_sites)) as client:
        job = client.post("/api/sites/first/jobs", json=payload())
        assert job.status_code == 202, job.text
        result = finish(client, job.json()["id"])
        assert result["status"] == "succeeded", result
        layer = result["layer_id"]
        listed = client.get("/api/sites/first/layers").json()["layers"]
        artifacts = client.get("/api/jobs/" + layer + "/artifacts").json()["files"]
        assert artifacts and client.get(artifacts[0]["url"]).status_code == 200
        assert client.get("/api/jobs/" + layer + "/artifacts/spec.json").status_code == 404
        assert len(listed) == 1 and listed[0]["id"] == layer and listed[0]["feature_count"] == 1
        assert client.get("/api/sites/second/layers").json()["layers"] == []
        features = client.get(f"/api/sites/first/layers/{layer}/features?limit=1").json()
        feature = features["features"][0]
        assert abs(feature["properties"]["area_m2"] - 100) < .01
        assert "score" not in feature["properties"]  # imports without a score must not invent confidence
        added = client.post("/api/review/workspace", json={"site_id": "first", "layer_id": layer,
                            "feature_ids": [feature["id"]], "expected_revision": 0}).json()
        fid = added["features"][0]["id"]
        assert added["features"][0]["properties"]["functional_use"] == "unknown"
        client.post(f"/api/review/features/{fid}/accept", json={"site_id": "first", "expected_revision": 1})
        export = client.post("/api/export", json={"site_id": "first", "revision": 2})
        assert export.status_code == 200, export.text
        assert export.json()["feature_count"] == 1
    with TestClient(create_app(fixture_sites)) as again:
        assert again.get("/api/jobs/" + layer).json()["status"] == "succeeded"
        assert len(again.get("/api/review/workspace?site_id=first").json()["features"]) == 1


def test_failure_is_unpublished_and_retry_keeps_old_output(fixture_sites):
    with TestClient(create_app(fixture_sites)) as client:
        bad = payload(geom(offset=1000))
        job = client.post("/api/sites/first/jobs", json=bad).json()
        failed = finish(client, job["id"])
        assert failed["status"] == "failed" and "outside" in failed["log_tail"].lower()
        assert client.get("/api/sites/first/layers").json()["layers"] == []
        retried = client.post("/api/jobs/" + job["id"] + "/retry").json()
        assert retried["id"] != job["id"] and retried["retry_of"] == job["id"]
        assert finish(client, retried["id"])["status"] == "failed"
        assert (fixture_sites.jobs_dir / job["id"] / "worker.log").exists()


def test_cancel_queued_and_restart_recovery(fixture_sites):
    app = create_app(fixture_sites)
    client = TestClient(app)  # no lifespan: queue intentionally remains stopped
    cancelled = client.post("/api/sites/first/jobs", json=payload()).json()
    assert client.post("/api/jobs/" + cancelled["id"] + "/cancel").json()["status"] == "cancelled"
    interrupted = client.post("/api/sites/first/jobs", json=payload()).json()
    path = fixture_sites.jobs_dir / interrupted["id"] / "state.json"
    state = json.loads(path.read_text()); state["status"] = "running"; atomic_json(path, state)
    with TestClient(create_app(fixture_sites)) as again:
        assert again.get("/api/jobs/" + interrupted["id"]).json()["status"] == "interrupted"
        assert again.get("/api/jobs/" + cancelled["id"]).json()["status"] == "cancelled"
        assert again.get("/api/sites/first/layers").json()["layers"] == []
        retry = again.post("/api/jobs/" + interrupted["id"] + "/retry").json()
        assert finish(again, retry["id"])["status"] == "succeeded"


def test_missing_model_and_bad_request_do_not_queue(fixture_sites):
    client = TestClient(create_app(fixture_sites))
    models = client.get("/api/models").json()["models"]
    assert all(not m["available"] and "checkpoint" in m["missing"] for m in models)
    result = client.post("/api/sites/first/jobs", json={"kind": "inference", "model_id": "maskrcnn"})
    assert result.status_code == 422 and result.json()["error"]["code"] == "MODEL_UNAVAILABLE"
    assert client.post("/api/sites/first/jobs", json={"kind": "import_layer"}).status_code == 422
    assert client.get("/api/sites/first/jobs").json()["jobs"] == []


def test_cancel_running_does_not_publish_partial_layer(fixture_sites):
    with TestClient(create_app(fixture_sites)) as client:
        job = client.post("/api/sites/first/jobs", json=payload()).json()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = client.get("/api/jobs/" + job["id"]).json()
            if state["status"] == "running": break
            time.sleep(.01)
        assert state["status"] == "running"
        client.post("/api/jobs/" + job["id"] + "/cancel")
        assert finish(client, job["id"])["status"] == "cancelled"
        assert client.get("/api/sites/first/layers").json()["layers"] == []
