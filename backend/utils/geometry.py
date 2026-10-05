"""Server-side geometry validation. Client geometry is never trusted."""

from dataclasses import dataclass, field

from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.ops import unary_union
from shapely.validation import explain_validity, make_valid

from ..config import ANALYSIS_CRS, COORD_PAD_DEG, REPAIR_MAX_AREA_CHANGE, WGS84
from ..errors import ApiError
from .crs import normalise_crs, to_utm, to_wgs


def polygonal(g):
    """Keep only the polygonal parts of a geometry (make_valid may return collections)."""
    if isinstance(g, (Polygon, MultiPolygon)):
        return g
    parts = [x for x in getattr(g, "geoms", []) if isinstance(x, (Polygon, MultiPolygon))]
    return unary_union(parts) if parts else Polygon()


@dataclass
class Checked:
    geometry: object                     # valid polygonal shapely geometry in WGS84
    repairs: list = field(default_factory=list)


def check_polygon(geojson_geom: dict, crs: str | None = None, site_bounds: tuple | None = None, label: str = "geometry", analysis_crs: str = ANALYSIS_CRS) -> Checked:
    """Validate a client polygon; return it in WGS84.

    Rejects: unreadable/non-polygon/empty geometry, unsupported CRS, coordinates outside the plausible range
    (or far outside the site), and invalid geometry that cannot be repaired within REPAIR_MAX_AREA_CHANGE.
    A safe repair is applied and recorded in `repairs`; a material repair is rejected, never applied silently.
    """
    src = normalise_crs(crs, analysis_crs)
    try:
        g = shape(geojson_geom)
    except Exception:  # noqa: BLE001
        raise ApiError("INVALID_GEOMETRY", f"{label}: unreadable GeoJSON geometry.", 422) from None
    if g.geom_type not in ("Polygon", "MultiPolygon"):
        raise ApiError("INVALID_GEOMETRY", f"{label}: {g.geom_type} is not a polygon.", 422)
    if g.is_empty:
        raise ApiError("EMPTY_GEOMETRY", f"{label}: geometry is empty.", 422)
    minx, miny, maxx, maxy = g.bounds
    if not all(map(_finite, (minx, miny, maxx, maxy))):
        raise ApiError("COORDINATES_OUT_OF_RANGE", f"{label}: non-finite coordinates.", 422)
    if src == WGS84 and not (-180 <= minx and maxx <= 180 and -90 <= miny and maxy <= 90):
        raise ApiError("COORDINATES_OUT_OF_RANGE", f"{label}: coordinates are not valid longitude/latitude (EPSG:4326).", 422)
    if src == "EPSG:32719" and not (0 <= minx and maxx <= 1_000_000 and 0 <= miny and maxy <= 10_000_000):
        raise ApiError("COORDINATES_OUT_OF_RANGE", f"{label}: coordinates are outside the valid UTM range.", 422)
    repairs = []
    if not g.is_valid:
        why = explain_validity(g)
        base = g.buffer(0)
        fixed = polygonal(make_valid(g))
        change = abs(fixed.area - base.area) / max(base.area, 1e-18)
        if fixed.is_empty or not fixed.is_valid or change > REPAIR_MAX_AREA_CHANGE:
            raise ApiError("INVALID_GEOMETRY", f"{label}: invalid polygon ({why}); a repair would change it materially.", 422,
                           {"reason": why})
        repairs.append({"reason": why, "area_change_fraction": round(change, 6)})
        g = fixed
    g = to_wgs(g, src)
    if site_bounds is not None:
        sx0, sy0, sx1, sy1 = site_bounds
        gx0, gy0, gx1, gy1 = g.bounds
        if gx1 < sx0 - COORD_PAD_DEG or gx0 > sx1 + COORD_PAD_DEG or gy1 < sy0 - COORD_PAD_DEG or gy0 > sy1 + COORD_PAD_DEG:
            raise ApiError("COORDINATES_OUT_OF_RANGE", f"{label}: geometry lies outside the site extent.", 422)
    if not g.is_valid or g.is_empty:
        raise ApiError("INVALID_GEOMETRY", f"{label}: geometry became invalid after reprojection.", 422)
    return Checked(g, repairs)


def _finite(v: float) -> bool:
    return v == v and abs(v) != float("inf")


def area_m2(g_wgs) -> float:
    return float(to_utm(g_wgs).area)


def feature_dict(fid: str, geom_wgs, props: dict) -> dict:
    return {"type": "Feature", "id": fid, "geometry": mapping(geom_wgs), "properties": props}
