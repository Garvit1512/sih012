"""Reviewer-supplied use assertions; synthetic fixtures establish no use accuracy."""
import hashlib
import json

import geopandas as gpd
from fastapi.testclient import TestClient
from shapely.geometry import LineString, mapping

from backend.main import create_app
from backend.utils.crs import project
from test_site_workflow import configured, fixture_sites, draw, geom


def record_use(client, fid, revision, label="residential", refs=None, **extra):
    return client.post(f"/api/review/features/{fid}/functional-use", json={
        "site_id": "first", "expected_revision": revision, "functional_use": label,
        "evidence_refs": ["synthetic-observation-001"] if refs is None else refs,
        "actor": "fixture-reviewer", "reason": "synthetic software check", **extra,
    })


def test_use_requires_references_and_meaningful_reviewer(fixture_sites):
    client = TestClient(create_app(fixture_sites))
    feature = draw(client).json()["features"][0]
    for label, refs, actor in (("residential", [], "reviewer"), ("   ", [], "reviewer"),
                               ("residential", [" "], "reviewer"), ("residential", ["x" * 501], "reviewer"),
                               ("residential", ["source"], " ")):
        assert record_use(client, feature["id"], 1, label, refs, actor=actor).status_code == 422
    assert client.get("/api/review/workspace?site_id=first").json()["revision"] == 1
    cleared = record_use(client, feature["id"], 1, " UNKNOWN ", [])
    assert cleared.status_code == 200, cleared.text
    props = cleared.json()["features"][0]["properties"]
    assert props["functional_use"] == "unknown" and props["functional_use_review"]["status"] == "unknown"
    assert props["functional_use_review"]["evidence_refs"] == []


def test_use_correction_history_export_restart_and_geometry_invalidation(fixture_sites):
    client = TestClient(create_app(fixture_sites))
    feature = draw(client).json()["features"][0]
    fid = feature["id"]
    client.post(f"/api/review/features/{fid}/accept", json={"site_id": "first", "expected_revision": 1})
    result = record_use(client, fid, 2, " residential ", [" observation-001 ", "record-002", "record-002"])
    assert result.status_code == 200, result.text
    props = result.json()["features"][0]["properties"]
    review = props["functional_use_review"]
    assert props["review_status"] == "suggested" and props["functional_use"] == "residential"
    assert review["evidence_refs"] == ["observation-001", "record-002"]
    assert review["actor"] == "fixture-reviewer" and review["workspace_revision"] == 3
    assert review["geometry_sha256"] == hashlib.sha256(json.dumps(feature["geometry"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert record_use(client, fid, 2, "commercial").status_code == 409
    assert record_use(client, fid, 0, "commercial", site_id="second").status_code == 404
    client.post(f"/api/review/features/{fid}/accept", json={"site_id": "first", "expected_revision": 3})
    restarted = TestClient(create_app(fixture_sites))
    saved = restarted.get("/api/review/workspace?site_id=first").json()
    assert saved["revision"] == 4 and saved["features"][0]["properties"]["functional_use"] == "residential"
    exported = restarted.post("/api/export", json={"site_id": "first", "revision": 4})
    assert exported.status_code == 200, exported.text
    frame = gpd.read_file(fixture_sites.export_root / exported.json()["export_id"] / "features_reviewed.gpkg")
    assert frame.functional_use.iloc[0] == "residential"
    assert json.loads(frame.functional_use_review.iloc[0])["evidence_refs"] == review["evidence_refs"]
    edit = restarted.post(f"/api/review/features/{fid}/edit", json={"site_id": "first", "expected_revision": 4, "geometry": geom(width=12)})
    after = edit.json()["features"][0]["properties"]
    assert after["functional_use"] == "unknown" and after["functional_use_review"]["status"] == "needs_review"
    assert after["functional_use_review"]["geometry_sha256"] == review["geometry_sha256"]
    historical = restarted.get("/api/review/workspace?site_id=first&revision=4").json()
    assert historical["features"][0]["properties"]["functional_use"] == "residential"
    events = restarted.get("/api/review/history?site_id=first").json()["events"]
    assignment = next(e for e in events if e["operation"] == "assign_use")
    assert assignment["before"][fid]["properties"]["functional_use"] == "unknown"
    assert assignment["after"][fid]["properties"]["functional_use"] == "residential"
    restored = record_use(restarted, fid, 5, "commercial", ["rechecked-geometry-005"])
    assert restored.status_code == 200
    cleared = record_use(restarted, fid, 6, "unknown", [])
    assert cleared.json()["features"][0]["properties"]["functional_use"] == "unknown"


def test_use_is_separate_from_cover_and_not_inherited_by_split(fixture_sites):
    client = TestClient(create_app(fixture_sites))
    feature = draw(client).json()["features"][0]
    record_use(client, feature["id"], 1)
    line = mapping(project(LineString([(500009,2500000),(500009,2500020)]), "EPSG:32643", "EPSG:4326"))
    split = client.post(f"/api/review/features/{feature['id']}/split", json={"site_id": "first", "expected_revision": 2, "line": line})
    assert split.status_code == 200, split.text
    assert all(f["properties"]["functional_use"] == "unknown" and "functional_use_review" not in f["properties"] for f in split.json()["features"])
    cover = client.post("/api/review/features/draw", json={"site_id": "first", "expected_revision": 3,
        "geometry": geom(offset=15), "feature_type": "land_cover", "class_name": "tree"}).json()["features"][0]
    rejected = record_use(client, cover["id"], 4)
    assert rejected.status_code == 422 and rejected.json()["error"]["code"] == "USE_FEATURE_TYPE"
