"""Inputs for preliminary parcel mapping and geometry-bound local evidence."""
from datetime import date
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .review import EditIn, SiteRef

ParcelState = Literal["proposed", "reviewed", "field_checked", "needs_survey", "disputed"]
EntityKind = Literal["boundary_segment", "reference_parcel", "survey_observation", "excluded_area", "survey_extent"]


class EvidenceImport(SiteRef):
    kind: EntityKind
    features: dict
    crs: str = "EPSG:4326"
    source: str = Field(min_length=1, max_length=500)
    captured_at: date
    horizontal_datum: str = Field(min_length=1, max_length=120)
    accuracy_m: float | None = Field(None, gt=0, le=100, allow_inf_nan=False)
    reviewed: bool = False
    observation_method: Literal["gnss", "ets", "record", "photo", "visible_boundary"] = "record"
    parcel_id: str | None = None

    @model_validator(mode="after")
    def observation_target(self):
        if not self.source.strip() or not self.horizontal_datum.strip() or not self.actor.strip():
            raise ValueError("Record the source, datum and local reviewer label.")
        if self.kind == "survey_observation" and not self.parcel_id:
            raise ValueError("Bind observations to a saved parcel geometry.")
        return self


class ProposeIn(SiteRef):
    mode: Literal["visible_boundaries", "reference_reconciliation"] = "visible_boundaries"


class ParcelStateIn(SiteRef):
    state: ParcelState
    record_refs: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("record_refs")
    @classmethod
    def references(cls, values):
        refs = [v.strip() for v in values]
        if any(not v or len(v) > 500 for v in refs):
            raise ValueError("References must contain 1–500 characters.")
        return list(dict.fromkeys(refs))

    @model_validator(mode="after")
    def reason_required(self):
        if not self.actor.strip():
            raise ValueError("Record the local reviewer label.")
        if self.state in ("reviewed", "field_checked") and not self.record_refs:
            raise ValueError("Reviewed parcels need record or observation references.")
        if self.state in ("needs_survey", "disputed") and not self.reason.strip():
            raise ValueError("Describe the unresolved boundary or dispute.")
        return self


class SharedEdgeIn(SiteRef):
    feature_ids: list[str] = Field(min_length=2, max_length=2)
    line: dict
    crs: str = "EPSG:4326"
    evidence_refs: list[str] = Field(min_length=1, max_length=20)

    @field_validator("evidence_refs")
    @classmethod
    def references(cls, values):
        return ParcelStateIn.references(values)


class ParcelEditIn(EditIn):
    evidence_refs: list[str] = Field(min_length=1, max_length=20)

    @field_validator("evidence_refs")
    @classmethod
    def references(cls, values):
        return ParcelStateIn.references(values)


class ParcelExportIn(SiteRef):
    revision: int = Field(ge=0)
    states: list[ParcelState] = Field(default_factory=lambda: ["proposed", "reviewed", "field_checked", "needs_survey", "disputed"])
    require_topology_clean: bool = False
