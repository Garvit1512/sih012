"""WGS84 display/API coordinates and each site's explicitly declared metric analysis CRS."""

from functools import lru_cache

from pyproj import CRS, Transformer
from shapely.ops import transform

from ..config import ANALYSIS_CRS, WGS84
from ..errors import ApiError

SUPPORTED = (WGS84, ANALYSIS_CRS)


def metric_crs(value: str) -> str:
    """Only projected, metre-based analysis systems can use the metric thresholds."""
    try:
        c = CRS.from_user_input(value)
    except Exception:
        raise ApiError("INVALID_CRS", f"Unreadable analysis CRS {value!r}.", 422) from None
    if not c.is_projected or len(c.axis_info) < 2 or any(abs(a.unit_conversion_factor - 1) > 1e-9 for a in c.axis_info[:2]):
        raise ApiError("INVALID_CRS", "Analysis CRS must be projected with metre-based horizontal units.", 422)
    return c.to_string()


def normalise_crs(crs: str | None, analysis_crs: str = ANALYSIS_CRS) -> str:
    raw = (crs or WGS84).strip()
    c = {"OGC:CRS84": WGS84, "CRS84": WGS84, "WGS84": WGS84}.get(raw.upper(), raw)
    supported = (WGS84, analysis_crs)
    try:
        source = CRS.from_user_input(c)
        for value in supported:
            if source.equals(CRS.from_user_input(value), ignore_axis_order=True):
                return value
    except Exception:
        pass
    raise ApiError("INVALID_CRS", f"Unsupported CRS {crs!r}. Supported: {', '.join(supported)}.", 422,
                   {"supported": list(supported)})


@lru_cache(maxsize=4)
def _tf(src: str, dst: str) -> Transformer:
    return Transformer.from_crs(src, dst, always_xy=True)


def project(geom, src: str, dst: str):
    return geom if src == dst else transform(_tf(src, dst).transform, geom)


def to_utm(geom, src: str = WGS84, analysis_crs: str = ANALYSIS_CRS):
    return project(geom, src, analysis_crs)


def to_wgs(geom, src: str = ANALYSIS_CRS):
    return project(geom, src, WGS84)
