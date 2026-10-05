"""CRS handling. Only EPSG:4326 (API/display) and EPSG:32719 (analysis) are accepted; nothing is guessed."""

from functools import lru_cache

from pyproj import Transformer
from shapely.ops import transform

from ..config import ANALYSIS_CRS, WGS84
from ..errors import ApiError

SUPPORTED = (WGS84, ANALYSIS_CRS)


def normalise_crs(crs: str | None) -> str:
    c = (crs or WGS84).strip().upper().replace("URN:OGC:DEF:CRS:", "")
    c = {"EPSG:4326": WGS84, "EPSG:32719": ANALYSIS_CRS, "OGC:CRS84": WGS84, "CRS84": WGS84, "WGS84": WGS84}.get(c, c)
    if c not in SUPPORTED:
        raise ApiError("INVALID_CRS", f"Unsupported CRS {crs!r}. Supported: {', '.join(SUPPORTED)}.", 422,
                       {"supported": list(SUPPORTED)})
    return c


@lru_cache(maxsize=4)
def _tf(src: str, dst: str) -> Transformer:
    return Transformer.from_crs(src, dst, always_xy=True)


def project(geom, src: str, dst: str):
    return geom if src == dst else transform(_tf(src, dst).transform, geom)


def to_utm(geom, src: str = WGS84):
    return project(geom, src, ANALYSIS_CRS)


def to_wgs(geom, src: str = ANALYSIS_CRS):
    return project(geom, src, WGS84)
