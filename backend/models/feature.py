from pydantic import BaseModel, Field


class FeatureCollectionOut(BaseModel):
    type: str = "FeatureCollection"
    features: list[dict]
    numberMatched: int
    numberReturned: int
    offset: int
    limit: int
    crs_note: str = "Coordinates are WGS84 lon/lat (EPSG:4326); area_m2 is computed in EPSG:32719."


class Relationship(BaseModel):
    type: str
    feature_id: str
    detail: dict = Field(default_factory=dict)


class FeatureDetail(BaseModel):
    id: str
    kind: str = Field(description="'ai_prediction' (immutable) or 'workspace'")
    geometry: dict
    area_m2: float
    source_layer: str | None
    source_feature_id: str | None
    review_status: str | None
    confidence: dict | None = Field(description="Only present when the model supplies a score; not a calibrated probability.")
    provenance: dict
    properties: dict
    topology: dict
