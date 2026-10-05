from typing import Literal

from pydantic import BaseModel, Field


class ValidateRequest(BaseModel):
    site_id: str | None = None
    features: dict = Field(description="GeoJSON FeatureCollection (EPSG:4326 unless crs is given); feature `id` is used as the feature id")
    crs: str = "EPSG:4326"


class ValidateLayerRequest(BaseModel):
    site_id: str
    layer_id: str
    bbox: list[float] | None = Field(None, description="Optional WGS84 minx,miny,maxx,maxy to restrict the check")


class Conflict(BaseModel):
    id: str
    type: Literal["overlap", "invalid_geometry", "duplicate", "gap_sliver"]
    severity: Literal["warning", "error"]
    features: list[str]
    geometry: dict | None
    message: str
    metrics: dict = {}


class TopologyResult(BaseModel):
    valid: bool
    conflicts: list[Conflict]
    relationships: list[dict] = Field(description="Shared-boundary relationships (informational, not conflicts)")
    summary: dict
    tolerances: dict
    scope_note: str = ("Topological relationships/conflicts between extracted building footprints. "
                       "Not legal cadastral parcel topology.")


class SharedBoundaryRequest(BaseModel):
    feature_id: str
    vertex: list[float] = Field(min_length=2, max_length=2)
    crs: str = "EPSG:4326"
    site_id: str | None = None
    layer_id: str | None = Field(None, description="Resolve neighbours from this AI layer when the feature is an AI feature")
    features: dict | None = Field(None, description="Optional inline FeatureCollection (current workspace); rejected features are ignored")
    tolerance_m: float | None = Field(None, gt=0, description="Defaults to the documented server tolerance")


class SharedBoundaryResult(BaseModel):
    shared: bool
    neighbor_feature_id: str | None
    edge: dict | None
    distance_m: float | None = None
    shared_length_m: float | None = None
    tolerance_m: float
    neighbors: list[str] = []
