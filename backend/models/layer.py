from pydantic import BaseModel


class LayerSummary(BaseModel):
    id: str
    legacy_key: str
    name: str
    type: str
    status: str
    feature_count: int
    color: str | None
    score_field: str | None
    score_label: str | None
    source_sha256: str | None
    source_crs: str | None
    flag_counts: dict | None
    metrics: dict | None = None
    metrics_note: str | None = None
