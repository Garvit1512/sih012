from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse

from ..config import MAX_FEATURES_PER_REQUEST
from ..errors import ApiError
from ..services.model_service import models

router = APIRouter(prefix="/api", tags=["jobs & local models"])


class JobIn(BaseModel):
    kind: Literal["import_layer", "inference", "use_classification"]
    model_id: str | None = None
    building_layer_id: str | None = None
    road_layer_id: str | None = None
    name: str = Field("New extraction", min_length=1, max_length=150)
    features: dict | None = None
    source: str = Field("local import", max_length=1000)
    input_crs: str = "EPSG:4326"
    feature_type: Literal["building_footprint", "road_surface", "land_cover"] = "building_footprint"
    target_res_m: float = Field(0.3, ge=0.05, le=2)
    min_area_m2: float = Field(10, ge=0, le=1000)
    threshold: float = Field(0.5, ge=0.05, le=0.95)


@router.get("/models")
def list_models(request: Request):
    return {"models": models(request.app.state.settings.models_dir), "note": "Local candidates; availability does not imply evaluated accuracy."}


@router.post("/sites/{site_id}/jobs", status_code=202)
def create_job(site_id: str, body: JobIn, request: Request):
    if body.kind in ("inference", "use_classification") and not body.model_id:
        raise ApiError("INVALID_REQUEST", "Model jobs need a model id.", 422)
    if body.kind == "use_classification" and not all((body.building_layer_id, body.road_layer_id)):
        raise ApiError("INVALID_REQUEST", "Use classification needs building and road layer IDs.", 422)
    if body.kind == "import_layer":
        if not body.features or body.features.get("type") != "FeatureCollection":
            raise ApiError("INVALID_REQUEST", "Import a GeoJSON FeatureCollection with an explicit CRS.", 422)
        if len(body.features.get("features", [])) > MAX_FEATURES_PER_REQUEST:
            raise ApiError("TOO_MANY_FEATURES", "Import at most 5,000 features per job.", 422)
    return request.app.state.jobs.enqueue(site_id, body.model_dump())


@router.get("/sites/{site_id}/jobs")
def list_jobs(site_id: str, request: Request):
    request.app.state.catalog.get(site_id)
    return {"jobs": request.app.state.jobs.list(site_id)}


@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request):
    return request.app.state.jobs.get(job_id)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request):
    return request.app.state.jobs.cancel(job_id)


@router.post("/jobs/{job_id}/retry", status_code=202)
def retry_job(job_id: str, request: Request):
    return request.app.state.jobs.retry(job_id)


@router.get("/jobs/{job_id}/artifacts")
def artifacts(job_id: str, request: Request):
    import json
    manager = request.app.state.jobs
    state = manager.get(job_id)
    if state["status"] != "succeeded":
        return {"files": []}
    root = manager._path(job_id) / "output"
    result = json.loads((root / "result.json").read_text())
    return {"files": [{"name": name, "sha256": digest, "url": f"/api/jobs/{job_id}/artifacts/{name}"}
                       for name, digest in result["files"].items()]}


@router.get("/jobs/{job_id}/artifacts/{filename}")
def artifact(job_id: str, filename: str, request: Request):
    from ..utils.validation import safe_id
    safe_id(filename, "artifact name")
    allowed = {f["name"] for f in artifacts(job_id, request)["files"]}
    if filename not in allowed:
        raise ApiError("ARTIFACT_NOT_FOUND", "Artifact is not available from a completed job.", 404)
    return FileResponse(request.app.state.jobs._path(job_id) / "output" / filename, filename=filename)
