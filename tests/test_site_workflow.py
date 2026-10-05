"""Software gates for Phases 1/2 using declared synthetic GeoTIFFs, not accuracy data."""

import io
import json
import math
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from fastapi.testclient import TestClient
from rasterio.transform import from_origin
from shapely.geometry import box, mapping, shape

from backend.config import Settings
from backend.main import create_app
from backend.services.ingestion_service import ingest_site
from backend.utils.crs import metric_crs, project


@pytest.fixture
def configured(tmp_path):
    return Settings(data_dir=tmp_path / "missing-legacy", workspace_dir=tmp_path / "workspaces",
                    export_root=tmp_path / "exports", sites_dir=tmp_path / "sites", jobs_dir=tmp_path / "jobs",
                    models_dir=tmp_path / "models")


def raster(path, crs="EPSG:32643", count=3, offset=0):
    pixels = np.full((count, 64, 64), 120, np.uint8)
    pixels[:, 20:35, 20:35] = 210
    with rasterio.open(path, "w", driver="GTiff", width=64, height=64, count=count, dtype="uint8", crs=crs,
                       transform=from_origin(500000 + offset, 2500032, 0.5, 0.5)) as dst:
        dst.write(pixels)
    return path


@pytest.fixture
def fixture_sites(configured, tmp_path):
    for site, offset in (("first", 0), ("second", 1000)):
        source = raster(tmp_path / f"{site}.tif", offset=offset)
        ingest_site(configured, source, site, f"Synthetic {site}", license_name="synthetic test fixture")
    return configured


def geom(offset=0, width=10):
    return mapping(project(box(500004 + offset, 2500004, 500004 + offset + width, 2500014), "EPSG:32643", "EPSG:4326"))


def draw(client, site="first", revision=0, geometry=None):
    return client.post("/api/review/features/draw", json={"site_id": site, "geometry": geometry or geom(),
                       "expected_revision": revision, "review_status": "suggested", "actor": "test-reviewer"})


def test_geo_upload_and_tile(configured, tmp_path):
    c = TestClient(create_app(configured))
    source = raster(tmp_path / "input.tif")
    with source.open("rb") as f:
        result = c.post("/api/sites", data={"site_id": "indian-fixture", "name": "Indian coordinate fixture",
                        "licence": "synthetic test fixture"}, files={"imagery": ("input.tif", f, "image/tiff")})
    assert result.status_code == 201, result.text
    site = result.json()
    assert site["crs"] == "EPSG:32643" and site["workflow"]["state"] == "IMAGERY_READY"
    assert site["workspace"]["revision"] == 0 and site["imagery_info"]["tile_url"]
    manifest = json.loads((configured.sites_dir / "indian-fixture/manifest.json").read_text())
    assert manifest["source"]["licence"] == "synthetic test fixture"
    with rasterio.open(configured.sites_dir / "indian-fixture/imagery.cog.tif") as ds:
        assert ds.tags(ns="IMAGE_STRUCTURE").get("LAYOUT") == "COG"
    assert c.get(site["imagery_info"]["display_url"]).content.startswith(b"\x89PNG")
    lon, lat = (site["bounds"][0] + site["bounds"][2]) / 2, (site["bounds"][1] + site["bounds"][3]) / 2
    z = 19
    x = int((lon + 180) / 360 * 2 ** z)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * 2 ** z)
    tile_url = site["imagery_info"]["tile_url"].format(z=z, x=x, y=y)
    first = c.get(tile_url)
    assert first.status_code == 200 and first.content.startswith(b"\x89PNG")
    assert c.get(tile_url).content == first.content
    assert c.get("/api/sites/indian-fixture/imagery/tiles/30/0/0.png").status_code == 422


def test_ingestion_rejects_bad_raster_crs_and_duplicate(configured, tmp_path):
    source = raster(tmp_path / "input.tif")
    c = TestClient(create_app(configured))
    for bad in ("EPSG:4326", "EPSG:2263", "made-up"):
        with pytest.raises(Exception, match="CRS"):
            metric_crs(bad)
    for body, content in (({"site_id": "../bad", "name": "Bad"}, source.read_bytes()),
                          ({"site_id": "broken", "name": "Broken"}, b"not a tiff")):
        r = c.post("/api/sites", data=body, files={"imagery": ("a.tif", io.BytesIO(content), "image/tiff")})
        assert r.status_code == 422
    assert c.get("/api/sites").json()["sites"] == []
    ingest_site(configured, source, "known", "Known")
    r = c.post("/api/sites", data={"site_id": "known", "name": "Duplicate"}, files={"imagery": ("a.tif", source.read_bytes())})
    assert r.status_code == 409
    assert not list(configured.sites_dir.glob(".ingest-*"))


def test_elevation_coverage_and_alignment(configured, tmp_path):
    source = raster(tmp_path / "source.tif")
    dsm = raster(tmp_path / "dsm.tif", count=1)
    manifest = ingest_site(configured, source, "height", "Height", elevation={"dsm": dsm}, vertical_datum="test datum")
    assert manifest["elevation"]["dsm"]["aligned"]
    assert "dsm_vertical_units_unverified" in manifest["quality"]["flags"]
    far = raster(tmp_path / "far.tif", count=1, offset=1000)
    with pytest.raises(Exception, match="cover"):
        ingest_site(configured, source, "bad-height", "Bad", elevation={"dsm": far})
    assert not (configured.sites_dir / "bad-height").exists()


def test_extent_uses_native_metric_grid_away_from_central_meridian(configured, tmp_path):
    source = raster(tmp_path / "bhopal-coordinate-fixture.tif", offset=246867)
    manifest = ingest_site(configured, source, "grid-extent", "Bhopal coordinate fixture")
    assert manifest["imagery"]["extent_m"] == [32.0, 32.0]


def test_metric_area_revision_recovery_and_site_isolation(fixture_sites):
    c = TestClient(create_app(fixture_sites))
    assert len(c.get("/api/sites").json()["sites"]) == 2
    missing_rev = c.post("/api/review/features/draw", json={"site_id": "first", "geometry": geom()})
    assert missing_rev.status_code == 428
    r = draw(c)
    assert r.status_code == 200, r.text
    f = r.json()["features"][0]
    assert abs(f["properties"]["area_m2"] - 100) < 0.01
    accept = c.post(f"/api/review/features/{f['id']}/accept", json={"site_id": "first", "expected_revision": 1, "duration_ms": 2100})
    assert accept.json()["revision"] == 2
    stale = c.post(f"/api/review/features/{f['id']}/reject", json={"site_id": "first", "expected_revision": 1})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "STALE_REVISION"
    other = c.post(f"/api/review/features/{f['id']}/accept", json={"site_id": "second", "expected_revision": 0})
    assert other.status_code == 404
    restarted = TestClient(create_app(fixture_sites))
    ws = restarted.get("/api/review/workspace?site_id=first").json()
    assert ws["revision"] == 2 and ws["features"][0]["properties"]["review_status"] == "accepted"
    assert ws["summary"]["review_duration_ms"] == 2100
    assert restarted.get("/api/review/workspace?site_id=second").json()["features"] == []
    hist = restarted.get("/api/review/history?site_id=first").json()
    assert hist["total"] == 2 and hist["events"][0]["before"] and hist["events"][0]["after"]
    assert hist["events"][1]["actor"] == "test-reviewer"


def test_edit_reset_remove_and_canonical_history_export(fixture_sites):
    c = TestClient(create_app(fixture_sites))
    f = draw(c).json()["features"][0]
    c.post(f"/api/review/features/{f['id']}/accept", json={"site_id": "first", "expected_revision": 1})
    edit = c.post(f"/api/review/features/{f['id']}/edit", json={"site_id": "first", "expected_revision": 2, "geometry": geom(width=12)})
    assert edit.json()["features"][0]["properties"]["review_status"] == "suggested"
    out = c.post("/api/export", json={"site_id": "first", "revision": 2})
    assert out.status_code == 200, out.text
    meta = out.json()
    assert meta["workspace_revision"] == 2 and abs(meta["total_area_m2"] - 100) < 0.01
    saved = fixture_sites.export_root / meta["export_id"]
    gpkg = gpd.read_file(saved / "features_reviewed.gpkg")
    assert gpkg.crs.to_epsg() == 32643 and len(gpkg) == 1
    assert gpkg.workspace_id.iloc[0] == f["id"]
    assert c.post("/api/export", json={"site_id": "first", "revision": 3}).status_code == 422  # edited feature needs review
    assert c.post("/api/export", json={"site_id": "first", "revision": 2, "features": {}}).status_code == 422
    assert c.post(f"/api/review/features/{f['id']}/reset", json={"site_id": "first", "expected_revision": 3}).status_code == 200
    current = c.get("/api/review/workspace?site_id=first").json()["revision"]
    removed = c.post(f"/api/review/features/{f['id']}/remove", json={"site_id": "first", "expected_revision": current})
    assert removed.status_code == 200 and removed.json()["removed_ids"] == [f["id"]]
    assert c.get("/api/review/workspace?site_id=first").json()["features"] == []
    # Historical exports still use the saved geometry after removal.
    assert c.post("/api/export", json={"site_id": "first", "revision": 2}).status_code == 200


def test_two_concurrent_writes_only_one_revision_wins(fixture_sites):
    from concurrent.futures import ThreadPoolExecutor
    client = TestClient(create_app(fixture_sites))
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: draw(client), range(2)))
    assert sorted(r.status_code for r in responses) == [200, 409]
    snapshot = client.get("/api/review/workspace?site_id=first").json()
    assert snapshot["revision"] == 1 and len(snapshot["features"]) == 1


def test_metric_merge_split_and_mixed_class_topology(fixture_sites):
    client = TestClient(create_app(fixture_sites))
    first = draw(client).json()["features"][0]
    second_geom = mapping(project(box(500014,2500004,500024,2500014),"EPSG:32643","EPSG:4326"))
    second = draw(client,revision=1,geometry=second_geom).json()["features"][0]
    merged = client.post("/api/review/features/merge",json={"site_id":"first","expected_revision":2,"feature_ids":[first["id"],second["id"]]})
    assert merged.status_code == 200, merged.text
    polygon = merged.json()["features"][0]
    assert abs(polygon["properties"]["area_m2"]-200)<.01 and polygon["properties"]["review_status"]=="suggested"
    from shapely.geometry import LineString
    line = mapping(project(LineString([(500014,2500000),(500014,2500020)]),"EPSG:32643","EPSG:4326"))
    split = client.post(f"/api/review/features/{polygon['id']}/split",json={"site_id":"first","expected_revision":3,"line":line})
    assert split.status_code == 200, split.text
    assert len(split.json()["features"])==2 and abs(split.json()["info"]["area_lost_m2"]-.2)<.01
    assert all(f["properties"]["parents"]==[polygon["id"]] for f in split.json()["features"])
    inline = {"type":"FeatureCollection","features":[{"type":"Feature","id":kind,"geometry":geom(),"properties":{"feature_type":kind}} for kind in ("building_footprint","road_surface")]}
    assert client.post("/api/topology/validate",json={"site_id":"first","features":inline}).json()["valid"]
    assert client.post("/api/review/features/draw",json={"site_id":"second","expected_revision":0,"geometry":geom(offset=1000),"feature_type":"land_cover"}).status_code==422


def test_topology_uses_site_crs(fixture_sites):
    c = TestClient(create_app(fixture_sites))
    fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "id": "a", "geometry": geom(), "properties": {}},
                                                       {"type": "Feature", "id": "b", "geometry": geom(offset=9), "properties": {}}]}
    r = c.post("/api/topology/validate", json={"site_id": "first", "features": fc})
    assert r.status_code == 200, r.text
    assert r.json()["tolerances"]["crs"] == "EPSG:32643"
    overlap = next(x for x in r.json()["conflicts"] if x["type"] == "overlap")
    assert abs(overlap["metrics"]["overlap_area_m2"] - 10) < 0.01
    coords = shape(overlap["geometry"]).bounds
    assert 70 < coords[0] < 80  # geometry was transformed back with the Indian site CRS
