from pydantic import BaseModel, Field


class ImageryInfo(BaseModel):
    display_url: str = Field(description="Display bundle (PNG warped to EPSG:3857). The source GeoTIFF is never served.")
    display_crs: str
    display_bounds_latlon: list[list[float]] = Field(description="[[south, west], [north, east]] for Leaflet imageOverlay")
    display_size_px: list[int]
    display_resolution_m: float | None
    source_file: str | None
    source_crs: str | None
    source_size_px: list[int] | None
    native_resolution_m: float | None
    source_sha256: str | None
    note: str | None = None


class SiteSummary(BaseModel):
    id: str
    name: str
    location: str | None
    crs: str
    resolution_m: float | None
    bounds: list[float] = Field(description="WGS84 [minx, miny, maxx, maxy]")
    bounds_latlon: list[list[float]]
    bounds_utm: list[float] = Field(description="[minx, miny, maxx, maxy] in the analysis CRS")
    imagery: str = Field(description="URL of the imagery metadata endpoint")


class WorkflowHint(BaseModel):
    state: str
    reason: str
    server_derived_states: list[str]
    client_states: list[str]


class SiteDetail(SiteSummary):
    imagery_info: ImageryInfo
    layers: list[dict]
    feature_counts: dict[str, int]
    workspace: dict
    workflow: WorkflowHint
    disclaimer: str
