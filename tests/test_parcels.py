"""Declared fixtures test parcel logic; these are not surveyed ownership parcels."""
import json

import geopandas as gpd
from fastapi.testclient import TestClient
from shapely.geometry import LineString, box, mapping, shape

from backend.main import create_app
from test_site_workflow import configured, fixture_sites


def feature(geom):
    return {"type": "Feature", "geometry": mapping(geom), "properties": {}}


def import_body(kind, geometries, revision=0, **extra):
    return {"site_id": "first", "expected_revision": revision, "actor": "fixture-reviewer", "kind": kind,
            "features": {"type": "FeatureCollection", "features": [feature(g) for g in geometries]},
            "crs": "EPSG:32643", "source": "synthetic evidence fixture", "captured_at": "2026-10-05",
            "horizontal_datum": "WGS84 fixture", "reviewed": True, **extra}


def lines():
    x, y = 500002, 2500002
    return [LineString([(x,y),(x+10,y),(x+20,y),(x+20,y+20),(x+10,y+20),(x,y+20),(x,y)]),
            LineString([(x+10,y),(x+10,y+20)]), LineString([(x+25,y),(x+25,y+5)])]


def setup(client):
    r = client.post("/api/parcels/import", json=import_body("boundary_segment", lines()))
    assert r.status_code == 200, r.text
    r = client.post("/api/parcels/propose", json={"site_id": "first", "expected_revision": 1})
    assert r.status_code == 200, r.text
    assert len(r.json()["features"]) == 2
    assert sum(p["length_m"] for p in r.json()["info"]["unresolved_linework"].values()) == 5
    return r.json()["features"]


def state(client, fid, status, revision):
    return client.post(f"/api/parcels/features/{fid}/state", json={"site_id": "first", "expected_revision": revision,
                       "state": status, "actor": "fixture-reviewer", "record_refs": ["synthetic survey record"], "reason": "fixture test"})


def test_supported_faces_open_boundaries_revision_isolation_and_recovery(fixture_sites):
    c = TestClient(create_app(fixture_sites))
    assert c.post("/api/parcels/propose", json={"site_id": "first", "expected_revision": 0}).status_code == 422
    no_rev = import_body("boundary_segment", lines()); no_rev.pop("expected_revision")
    assert c.post("/api/parcels/import", json=no_rev).status_code == 428
    parcels = setup(c)
    assert all(p["properties"]["boundary_evidence_coverage"] > .999 for p in parcels)
    assert all(p["properties"]["ownership"] is None for p in parcels)
    again = c.post("/api/parcels/propose", json={"site_id": "first", "expected_revision": 2})
    assert again.status_code == 200 and again.json()["features"] == [] and again.json()["revision"] == 2
    assert state(c, parcels[0]["id"], "reviewed", 1).status_code == 409
    assert c.get("/api/parcels/second").json()["features"] == []
    restored = TestClient(create_app(fixture_sites))
    assert restored.get("/api/parcels/first").json()["revision"] == 2
    assert restored.get("/api/parcels/first/history").json()["total"] == 2
    assert c.get("/api/review/workspace?site_id=first").json()["features"] == []


def test_field_gate_atomic_shared_edge_and_stale_observation_invalidation(fixture_sites):
    c = TestClient(create_app(fixture_sites)); parcels = setup(c); revision = 2
    for p in parcels:
        assert state(c, p["id"], "reviewed", revision).status_code == 200; revision += 1
        denied = state(c, p["id"], "field_checked", revision)
        assert denied.status_code == 422 and denied.json()["error"]["code"] == "FIELD_EVIDENCE_REQUIRED"
        body = import_body("survey_observation", [LineString(shape(p["geometry"]).exterior.coords)], revision,
                           parcel_id=p["id"], observation_method="gnss", accuracy_m=.1)
        body["crs"] = "EPSG:4326"
        r = c.post("/api/parcels/import", json=body); assert r.status_code == 200, r.text; revision += 1
        r = state(c, p["id"], "field_checked", revision); assert r.status_code == 200, r.text; revision += 1
    before = c.get("/api/parcels/first").json()
    edit = {"site_id": "first", "expected_revision": revision, "feature_ids": [p["id"] for p in parcels],
            "line": mapping(LineString([(500012,2500002),(500013,2500012),(500012,2500022)])),
            "crs": "EPSG:32643", "evidence_refs": ["synthetic corrected edge"], "actor": "fixture-reviewer"}
    r = c.post("/api/parcels/shared-edge", json=edit)
    assert r.status_code == 200, r.text
    assert r.json()["revision"] == revision + 1
    changed = r.json()["features"]
    assert all(f["properties"]["parcel_state"] == "needs_survey" and f["properties"]["field_check"]["status"] == "needs_review" for f in changed)
    assert abs(sum(f["properties"]["area_m2"] for f in changed) - 400) < .01
    hist = c.get("/api/parcels/first/history").json()["events"][0]
    assert len(hist["before"]) == len(hist["after"]) == 2
    assert state(c, parcels[0]["id"], "reviewed", revision + 1).status_code == 200
    assert state(c, parcels[0]["id"], "field_checked", revision + 2).status_code == 422
    historical = c.get(f"/api/parcels/first?revision={before['revision']}").json()
    assert all(f["properties"]["parcel_state"] == "field_checked" for f in historical["features"] if f["properties"]["feature_type"] == "parcel")
    duplicate = {**edit, "expected_revision": revision + 2, "feature_ids": [parcels[0]["id"]] * 2}
    assert c.post("/api/parcels/shared-edge", json=duplicate).status_code == 422


def test_exclusions_extent_reference_conflicts_and_canonical_gis_export(fixture_sites):
    c = TestClient(create_app(fixture_sites))
    assert c.post("/api/parcels/import", json=import_body("boundary_segment", lines()[:2])).status_code == 200
    assert c.post("/api/parcels/import", json=import_body("survey_extent", [box(500001,2500001,500031,2500031)], 1)).status_code == 200
    assert c.post("/api/parcels/import", json=import_body("excluded_area", [box(500003,2500003,500006,2500006)], 2)).status_code == 200
    assert c.post("/api/parcels/import", json=import_body("reference_parcel", [box(500012,2500002,500021,2500022)], 3)).status_code == 200
    r = c.post("/api/parcels/propose", json={"site_id":"first","expected_revision":4,"mode":"reference_reconciliation"})
    assert r.status_code == 200, r.text
    assert len(r.json()["features"]) == 1 and r.json()["info"]["conflicts"][0]["type"] == "excluded_area_intersection"
    data = c.get("/api/parcels/first").json()
    assert data["diagnostics"]["unresolved_extent_area_m2"] > 600
    out = c.post("/api/parcels/export", json={"site_id":"first","revision":5,"require_topology_clean":True})
    assert out.status_code == 200, out.text
    export = out.json(); path = fixture_sites.export_root / export["export_id"]
    gpkg = gpd.read_file(path / "parcel_products.gpkg", layer="parcel")
    assert gpkg.crs.to_epsg() == 32643 and len(gpkg) == 1
    assert json.loads((path / "export_metadata.json").read_text())["parcel_revision"] == 5
    assert c.get(export["download_url"]).content.startswith(b"PK")
    assert c.get(export["download_url"].replace("/first/", "/second/")).status_code == 404
    outside = import_body("survey_observation", [LineString([(510000,2500002),(510001,2500002)])], 5,
                          parcel_id=r.json()["features"][0]["id"], observation_method="gnss", accuracy_m=.1)
    assert c.post("/api/parcels/import", json=outside).status_code == 422
