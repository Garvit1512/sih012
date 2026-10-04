"""Guard: the independent V1-V4 evaluation set and the frozen baselines must not change.

Hashes come from outputs/instance_study/frozen_hashes.sha256 (taken 2026-10-04 before the
instance-segmentation study). Run: python tests/test_frozen_eval.py
"""

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "outputs" / "instance_study" / "frozen_hashes.sha256"
FROZEN_PREFIXES = (
    "data/validation_independent/",          # V1-V4 labels, decisions, windows (test set)
    "data/validation/",                      # W1-W4 reviewed labels
    "data/lapaz_predio_bisa.tif",            # source imagery
    "models/",                               # checkpoints
    "outputs/lapaz_predio_bisa_whu_0.3m/",   # WHU baseline
    "outputs/rgb_guided_reviewed/final/",    # final reviewed output + independent scores
)


def manifest():
    for line in MANIFEST.read_text().splitlines():
        digest, path = line.split(maxsplit=1)
        yield digest, path.lstrip("*")


def test_frozen_files_unchanged():
    checked, changed = 0, []
    for digest, path in manifest():
        if path.startswith(FROZEN_PREFIXES):
            checked += 1
            if hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != digest:
                changed.append(path)
    assert checked > 50, checked
    assert not changed, changed


def test_independent_labels_in_manifest():
    paths = {p for _, p in manifest()}
    for f in ("reviewed_labels.geojson", "review_decisions.json", "windows.geojson", "provisional_labels.geojson"):
        assert f"data/validation_independent/{f}" in paths, f


if __name__ == "__main__":
    for t in (test_frozen_files_unchanged, test_independent_labels_in_manifest):
        t()
        print(f"PASS {t.__name__}")
    print("ALL FROZEN-EVALUATION TESTS PASSED")
