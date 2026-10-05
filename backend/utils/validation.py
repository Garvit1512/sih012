"""Identifier hygiene. IDs are only ever used as dictionary keys, never as file-system paths."""

import re

from ..errors import ApiError

_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")


def safe_id(value: str, what: str = "id") -> str:
    if not isinstance(value, str) or not _ID.match(value) or ".." in value:
        raise ApiError("INVALID_ID", f"Invalid {what}.", 422, {"allowed": "letters, digits, _ . : -  (max 80)"})
    return value


def parse_bbox(bbox: str | None) -> tuple[float, float, float, float] | None:
    if bbox is None or bbox == "":
        return None
    try:
        parts = [float(x) for x in bbox.split(",")]
        assert len(parts) == 4 and all(abs(p) < 1e9 for p in parts)
        minx, miny, maxx, maxy = parts
        assert minx < maxx and miny < maxy
    except Exception:  # noqa: BLE001
        raise ApiError("INVALID_BBOX", "bbox must be 'minx,miny,maxx,maxy' with min < max.", 422) from None
    return minx, miny, maxx, maxy
