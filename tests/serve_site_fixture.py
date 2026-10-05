"""Isolated, empty application for the browser gate. Test imagery is explicitly synthetic."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.config import Settings
from backend.main import create_app
from test_site_workflow import raster


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8771)
    parser.add_argument("--use-candidate", action="store_true", help="Create an explicitly synthetic local use candidate for browser QA")
    args = parser.parse_args()
    if args.root.exists() and any(args.root.iterdir()):
        raise SystemExit("Use an empty fixture directory; refusing to overwrite.")
    args.root.mkdir(parents=True, exist_ok=True)
    raster(args.root / "synthetic.tif")
    settings = Settings(data_dir=args.root / "missing", workspace_dir=args.root / "workspace", export_root=args.root / "exports",
                        sites_dir=args.root / "sites", jobs_dir=args.root / "jobs", models_dir=args.root / "models")
    if args.use_candidate:
        from test_use_experiment import write_use_fixture
        from backend.ml.use_experiment import train
        from backend.ml.register import register
        manifest = write_use_fixture(args.root)
        experiment = args.root / "synthetic-use-experiment"
        train(manifest, experiment)
        register(experiment, "synthetic-use", "Synthetic use QA candidate", settings.models_dir)
    import uvicorn
    uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port)
