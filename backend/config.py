"""Backend configuration. Everything is overridable through SIH_* environment variables (see docs/backend.md)."""

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_CRS = "EPSG:32719"
WGS84 = "EPSG:4326"
DISCLAIMER = ("AI-assisted building footprints. NOT legal cadastral parcel boundaries; "
              "not verified against authoritative parcel data.")
EXPORT_DISCLAIMER = ("These are AI-generated/human-reviewed building footprints and are NOT legal cadastral "
                     "parcel boundaries.")

# Topology tolerances, all in metres / m2 in the analysis CRS (EPSG:32719). Documented in docs/backend.md.
OVERLAP_MIN_AREA_M2 = 0.25        # intersection smaller than this is ignored (rasterisation noise)
OVERLAP_MIN_FRACTION = 0.05       # ...and it must cover >= 5 % of the smaller polygon
OVERLAP_ERROR_FRACTION = 0.30     # >= 30 % of the smaller polygon -> severity "error", else "warning"
DUPLICATE_IOU = 0.90              # IoU at or above this -> duplicate / near-duplicate
SHARED_BOUNDARY_TOL_M = 0.30      # same value as the display heuristic 'touching_neighbour' (manifest flag_rules)
SHARED_BOUNDARY_MIN_LEN_M = 0.5   # shorter shared runs are treated as corner touches, not shared edges
GAP_MAX_WIDTH_M = 0.5             # gaps narrower than this between two footprints are reported as slivers
GAP_MIN_AREA_M2 = 0.1
GAP_MIN_LENGTH_M = 1.0            # corner fillets from the closing operation are shorter than this and are not reported
MAX_SHARED_TOL_M = 2.0            # upper bound a client may request
REPAIR_MAX_AREA_CHANGE = 0.01     # same 1 % rule as app/server.py export repair
COORD_PAD_DEG = 0.02              # accepted margin around the site bounds (~2 km)
MAX_FEATURES_PER_REQUEST = 5000
MAX_PAGE = 5000


def _csv(name: str, default: str) -> list[str]:
    return [x.strip() for x in os.environ.get(name, default).split(",") if x.strip()]


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("SIH_DATA_DIR", ROOT / "app" / "data_v2")))
    export_root: Path = field(default_factory=lambda: Path(os.environ.get("SIH_EXPORT_ROOT", ROOT / "outputs" / "app_exports")))
    workspace_dir: Path = field(default_factory=lambda: Path(os.environ.get("SIH_WORKSPACE_DIR", ROOT / "outputs" / "backend_workspace")))
    static_dir: Path = ROOT / "app" / "static"
    # The UI is normally served by this same backend (same origin, no CORS needed). These origins cover a
    # separately-served frontend during development.
    cors_origins: list[str] = field(default_factory=lambda: _csv(
        "SIH_CORS_ORIGINS",
        "http://127.0.0.1:8765,http://localhost:8765,http://127.0.0.1:8000,http://localhost:8000,"
        "http://127.0.0.1:5173,http://localhost:5173"))
