from typing import Literal

from pydantic import BaseModel, Field

Crs = Literal["EPSG:4326", "EPSG:32719"]


class GeometryIn(BaseModel):
    geometry: dict = Field(description="GeoJSON Polygon/MultiPolygon")
    crs: str = "EPSG:4326"


class WorkspaceAdd(BaseModel):
    site_id: str
    layer_id: str
    feature_ids: list[str] | None = Field(None, description="AI feature ids to copy; omit to use bbox")
    bbox: list[float] | None = Field(None, description="WGS84 minx,miny,maxx,maxy; copy all AI features intersecting it")


class SiteRef(BaseModel):
    site_id: str


class EditIn(GeometryIn):
    site_id: str


class DrawIn(GeometryIn):
    site_id: str
    review_status: Literal["suggested", "accepted"] = "accepted"


class MergeIn(BaseModel):
    site_id: str
    feature_ids: list[str] = Field(min_length=2, max_length=50)


class SplitIn(BaseModel):
    site_id: str
    line: dict = Field(description="GeoJSON LineString crossing the polygon completely")
    crs: str = "EPSG:4326"
    gap_m: float = Field(0.02, gt=0, le=0.5, description="Width removed along the cut, same 2 cm default as the UI")


class ReviewResult(BaseModel):
    features: list[dict]
    removed_ids: list[str] = []
    info: dict = {}
