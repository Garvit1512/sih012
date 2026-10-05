"""A single local worker, persistent job ledger and atomic layer publication.

Workers write to new job directories. Only validated, complete output enters a
site manifest. Cancellation/failure preserves logs and never publishes partial layers.
"""
import json
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from ..config import ROOT
from ..errors import ApiError
from ..utils.validation import safe_id
from .ingestion_service import sha256
from .model_service import require_model

TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


class JobManager:
    def __init__(self, settings, registry):
        self.settings, self.registry = settings, registry
        self.root = settings.jobs_dir
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.thread = None

    def _path(self, job_id):
        safe_id(job_id, "job id")
        return self.root / job_id

    def _read(self, job_id):
        path = self._path(job_id) / "state.json"
        if not path.is_file():
            raise ApiError("JOB_NOT_FOUND", "Job not found.", 404)
        return json.loads(path.read_text())

    def _save(self, state):
        state["updated_at"] = now()
        atomic_json(self._path(state["id"]) / "state.json", state)

    def list(self, site_id):
        with self.lock:
            rows = [json.loads(p.read_text()) for p in self.root.glob("*/state.json")]
            for row in rows:
                path = self._path(row["id"]) / "progress.json"
                if row["status"] == "running" and path.is_file():
                    row["progress"] = json.loads(path.read_text())
        return sorted((r for r in rows if r["site_id"] == site_id), key=lambda r: r["created_at"], reverse=True)

    def get(self, job_id):
        with self.lock:
            state = self._read(job_id)
            directory = self._path(job_id)
            progress = directory / "progress.json"
            if state["status"] == "running" and progress.is_file():
                try: state["progress"] = json.loads(progress.read_text())
                except json.JSONDecodeError: pass
            log = directory / "worker.log"
            if log.is_file():
                with log.open("rb") as stream:
                    stream.seek(max(0, log.stat().st_size - 8000))
                    state["log_tail"] = stream.read().decode("utf-8", errors="replace")
            return state

    def enqueue(self, site_id, payload, retry_of=None):
        cat = self.registry.get(site_id)
        if cat.manifest.get("schema_version", 1) < 2:
            raise ApiError("READ_ONLY_SITE", "Import imagery as a new site before creating new experiments; the historical bundle is read-only.", 409)
        model = require_model(payload["model_id"], self.settings.models_dir) if payload["kind"] in ("inference", "use_classification") else None
        use_inputs = None
        if model and (model["architecture"] == "use_centroid") != (payload["kind"] == "use_classification"):
            raise ApiError("MODEL_TASK_MISMATCH", "Select a use classifier for use classification and an image model for extraction.", 422)
        if payload["kind"] == "use_classification":
            from .feature_service import ai_feature_json
            use_inputs = {}
            for name, feature_type in (("building", "building_footprint"), ("road", "road_surface")):
                layer_id = safe_id(payload[name + "_layer_id"], "layer id")
                layer = cat.layers.get(layer_id)
                features = [ai_feature_json(layer, f) for f in layer.features if f.props.get("feature_type", "building_footprint") == feature_type]
                if not features or len(features) > 5000:
                    raise ApiError("USE_INPUTS_REQUIRED", "Use classification needs 1–5,000 features of each selected type.", 422)
                use_inputs[name] = {"layer_id": layer_id, "features": features}
        if model and model["architecture"] == "maskrcnn" and payload["threshold"] != 0.5:
            raise ApiError("INVALID_REQUEST", "The preserved Mask R-CNN adapter uses score/mask thresholds of 0.5.", 422)
        if model and model.get("channels", 3) == 4 and not cat.manifest.get("elevation", {}).get("ndsm", {}).get("registration_reviewed"):
            raise ApiError("HEIGHT_UNAVAILABLE", "Prepare a reviewed nDSM before queueing a height-model job.", 422)
        with self.lock:
            job_id = "job-" + secrets.token_hex(8)
            directory = self._path(job_id)
            directory.mkdir(parents=True, exist_ok=False)
            state = {"id": job_id, "site_id": site_id, "kind": payload["kind"], "status": "queued",
                     "created_at": now(), "progress": {"stage": "queued", "fraction": 0}, "retry_of": retry_of}
            spec = {"job_id": job_id, "site_id": site_id, "payload": payload,
                    "site_manifest": str(cat.settings.data_dir / "manifest.json"),
                    "output_dir": str(directory / "output"), "progress_file": str(directory / "progress.json"), "model": model,
                    "use_inputs": use_inputs}
            atomic_json(directory / "spec.json", spec)
            self._save(state)
            self.wake.set()
        return state

    def cancel(self, job_id):
        with self.lock:
            state = self._read(job_id)
            if state["status"] in TERMINAL:
                return state
            state["cancel_requested"] = True
            if state["status"] == "queued":
                state["status"] = "cancelled"
            self._save(state); self.wake.set()
            return state

    def retry(self, job_id):
        state = self.get(job_id)
        if state["status"] not in {"failed", "cancelled", "interrupted"}:
            raise ApiError("JOB_NOT_RETRYABLE", "Retry a failed, cancelled or interrupted job.", 409)
        spec = json.loads((self._path(job_id) / "spec.json").read_text())
        return self.enqueue(state["site_id"], spec["payload"], job_id)

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive(): return
            self.stop_event.clear()
            for path in self.root.glob("*/state.json"):
                state = json.loads(path.read_text())
                if state["status"] == "running":
                    cat = self.registry.get(state["site_id"])
                    if state["id"] in cat.manifest["layers"]:
                        state.update(status="succeeded", layer_id=state["id"], progress={"stage": "published", "fraction": 1})
                    else:
                        state.update(status="interrupted", error="Worker process stopped before publishing; retry creates a new job.")
                    self._save(state)
            self.thread = threading.Thread(target=self._loop, name="sih-local-worker", daemon=True)
            self.thread.start()

    def close(self):
        self.stop_event.set(); self.wake.set()
        if self.thread: self.thread.join(timeout=6)

    def _loop(self):
        while not self.stop_event.is_set():
            queued = sorted((json.loads(p.read_text()) for p in self.root.glob("*/state.json")), key=lambda r: r["created_at"])
            state = next((r for r in queued if r["status"] == "queued"), None)
            if state is None:
                self.wake.wait(0.5); self.wake.clear(); continue
            self._run(state["id"])

    def _run(self, job_id):
        directory = self._path(job_id)
        with self.lock:
            state = self._read(job_id)
            if state["status"] != "queued": return
            state.update(status="running", started_at=now())
            self._save(state)
        process = None
        try:
            with (directory / "worker.log").open("ab") as log:
                process = subprocess.Popen([sys.executable, "-u", "-m", "backend.worker", str(directory / "spec.json")],
                                           cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
                while process.poll() is None:
                    if self.stop_event.is_set() or self._read(job_id).get("cancel_requested"):
                        process.terminate()
                        try: process.wait(timeout=3)
                        except subprocess.TimeoutExpired: process.kill(); process.wait()
                        break
                    time.sleep(0.1)
            with self.lock:
                state = self._read(job_id)
                if state.get("cancel_requested"):
                    state["status"] = "cancelled"
                elif self.stop_event.is_set():
                    state.update(status="interrupted", error="Application stopped; retry creates a new job.")
                elif process.returncode != 0:
                    state.update(status="failed", error="Worker failed; see log_tail.")
                else:
                    state["layer_id"] = self._publish(state)
                    state.update(status="succeeded", progress={"stage": "published", "fraction": 1})
                state["finished_at"] = now(); self._save(state)
        except Exception as error:
            if process and process.poll() is None: process.kill(); process.wait()
            with self.lock:
                state = self._read(job_id)
                state.update(status="failed", error=str(error), finished_at=now()); self._save(state)

    def _publish(self, state):
        from .prediction_service import validate_features
        cat = self.registry.get(state["site_id"])
        source = self._path(state["id"]) / "output"
        result = json.loads((source / "result.json").read_text())
        features = json.loads((source / "features.geojson").read_text())
        validate_features(cat.manifest, features)
        target = cat.settings.data_dir / "layers" / state["id"]
        target.parent.mkdir(exist_ok=True)
        if target.exists(): raise ApiError("OUTPUT_EXISTS", "Refusing to overwrite a job output.", 409)
        # The worker directory is on the same local output filesystem as sites.
        import shutil
        shutil.copytree(source, target)
        mp = cat.settings.data_dir / "manifest.json"
        manifest = json.loads(mp.read_text())
        relative = str((target / "features.geojson").relative_to(cat.settings.data_dir))
        manifest["layers"][state["id"]] = {"id": state["id"], "file": relative, "name": result["name"],
            "count": len(features["features"]), "color": "#60a5fa", "type": result["type"], "status": "candidate",
            "score_label": "model score", "source_crs": "EPSG:4326", "source_sha256": sha256(target / "features.geojson"),
            "job_id": state["id"], "provenance": result, "metrics": {}}
        atomic_json(mp, manifest)
        self.registry.refresh(state["site_id"])
        return state["id"]
