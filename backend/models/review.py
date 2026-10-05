from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Crs = Literal["EPSG:4326", "EPSG:32719"]


class GeometryIn(BaseModel):
    geometry: dict = Field(description="GeoJSON Polygon/MultiPolygon")
    crs: str = "EPSG:4326"


class SiteRef(BaseModel):
    site_id: str
    expected_revision: int | None = Field(None, ge=0)
    actor: str = Field("local-reviewer", min_length=1, max_length=80)
    reason: str = Field("", max_length=500)
    duration_ms: int = Field(0, ge=0, le=86_400_000)


class WorkspaceAdd(SiteRef):
    site_id: str
    layer_id: str
    feature_ids: list[str] | None = Field(None, description="AI feature ids to copy; omit to use bbox")
    bbox: list[float] | None = Field(None, description="WGS84 minx,miny,maxx,maxy; copy all AI features intersecting it")


class EditIn(GeometryIn, SiteRef):
    site_id: str


class FunctionalUseIn(SiteRef):
    functional_use: str = Field(min_length=1, max_length=80)
    evidence_refs: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("functional_use")
    @classmethod
    def clean_label(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Provide a local use class or unknown.")
        return "unknown" if value.lower() == "unknown" else value

    @field_validator("evidence_refs")
    @classmethod
    def clean_references(cls, values):
        references = [value.strip() for value in values]
        if any(not value or len(value) > 500 for value in references):
            raise ValueError("Each source reference must contain 1–500 characters.")
        return list(dict.fromkeys(references))

    @model_validator(mode="after")
    def require_evidence(self):
        if self.functional_use != "unknown" and not self.evidence_refs:
            raise ValueError("A known functional-use label requires source references.")
        if not self.actor.strip():
            raise ValueError("Record the reviewer label.")
        return self


class DrawIn(GeometryIn, SiteRef):
    site_id: str
    review_status: Literal["suggested", "accepted"] = "accepted"
    feature_type: Literal["building_footprint", "road_surface", "land_cover"] = "building_footprint"
    class_name: str | None = Field(None, max_length=80)


class MergeIn(SiteRef):
    site_id: str
    feature_ids: list[str] = Field(min_length=2, max_length=50)


class SplitIn(SiteRef):
    site_id: str
    line: dict = Field(description="GeoJSON LineString crossing the polygon completely")
    crs: str = "EPSG:4326"
    gap_m: float = Field(0.02, gt=0, le=0.5, description="Width removed along the cut, same 2 cm default as the UI")


class ReviewResult(BaseModel):
    features: list[dict]
    removed_ids: list[str] = []
    info: dict = {}
    revision: int = 0
