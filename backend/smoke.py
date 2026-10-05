"""One-command software smoke check; uses isolated synthetic assets, no downloads.

python -m backend.smoke --report outputs/software-smoke.json
"""
import argparse
import json
import platform
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

from .demo import build, settings_for
from .main import create_app
from .services.job_service import atomic_json


def smoke():
    start = time.monotonic(); checks = []
    with tempfile.TemporaryDirectory(prefix="sih-smoke-") as temporary:
        root = Path(temporary); build(root)
        with TestClient(create_app(settings_for(root))) as client:
            def check(name, response, expected=200):
                if response.status_code != expected: raise RuntimeError(f"{name}: {response.status_code} {response.text[:500]}")
                checks.append(name); return response.json()
            check("health", client.get("/api/health"))
            check("site manifest", client.get("/api/sites/demo"))
            html = client.get("/")
            if html.status_code != 200 or 'id="parcel-controls"' not in html.text: raise RuntimeError("Parcel UI missing.")
            checks.append("application and parcel UI")
            check("building workspace", client.get("/api/review/workspace?site_id=demo"))
            imported = check("boundary evidence import", client.post("/api/parcels/import", json={"site_id":"demo","expected_revision":0,
                "kind":"boundary_segment","features":json.loads((root/"assets/boundaries.geojson").read_text()),"crs":"EPSG:32643",
                "source":"synthetic smoke fixture","captured_at":"2026-10-05","horizontal_datum":"WGS84 fixture","reviewed":True}))
            proposals = check("supported parcel generation", client.post("/api/parcels/propose", json={"site_id":"demo","expected_revision":imported["revision"]}))
            if len(proposals["features"]) != 2: raise RuntimeError("Expected two synthetic supported faces.")
            fid = proposals["features"][0]["id"]
            reviewed = check("parcel review", client.post(f"/api/parcels/features/{fid}/state", json={"site_id":"demo","expected_revision":2,
                "state":"reviewed","record_refs":["synthetic fixture reference"]}))
            denied = client.post(f"/api/parcels/features/{fid}/state", json={"site_id":"demo","expected_revision":3,
                "state":"field_checked","record_refs":["synthetic fixture reference"]})
            if denied.status_code != 422: raise RuntimeError("Missing field evidence was accepted.")
            checks.append("missing field evidence rejected")
            exported = check("canonical GIS export", client.post("/api/parcels/export", json={"site_id":"demo","revision":reviewed["revision"],"require_topology_clean":True}))
            if not client.get(exported["download_url"]).content.startswith(b"PK"): raise RuntimeError("GIS ZIP download failed.")
            checks.append("GIS ZIP download")
        recovered = TestClient(create_app(settings_for(root))).get("/api/parcels/demo").json()
        if recovered["revision"] != 3: raise RuntimeError("Saved parcel revision was not recovered.")
        checks.append("restart recovery")
    return {"status":"passed","checks":checks,"count":len(checks),"runtime_seconds":round(time.monotonic()-start,3),
            "python":platform.python_version(),"platform":platform.platform(),"processor":platform.machine(),
            "scope":"isolated synthetic ASGI software workflow; not browser, model, cadastral or survey accuracy validation"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--report",type=Path)
    args = parser.parse_args(); report = smoke()
    if args.report:
        if args.report.exists(): parser.error("Refusing to overwrite a smoke report.")
        atomic_json(args.report, report)
    print(json.dumps(report,indent=2))
