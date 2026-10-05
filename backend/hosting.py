"""One-process hosted pilot with a shared-password session and explicit storage root."""
import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse

from .config import Settings
from .demo import build
from .main import create_app

COOKIE = "sih_session"
TTL = 12 * 60 * 60


def hosted_app(root, password, seed_demo=False, secure=True, temporary_storage=False):
    if len(password) < 16:
        raise ValueError("SIH_APP_PASSWORD must contain at least 16 characters.")
    root = Path(root).resolve()
    if seed_demo and (not root.exists() or not any(root.iterdir())):
        build(root)
    settings = Settings(data_dir=root / "legacy", sites_dir=root / "sites", jobs_dir=root / "jobs",
                        workspace_dir=root / "workspaces", export_root=root / "exports", models_dir=root / "models")
    app = create_app(settings)

    def signature(value):
        return hmac.new(password.encode(), value.encode(), hashlib.sha256).hexdigest()

    def valid(value):
        try:
            expires, nonce, digest = value.split(".")
            return (time.time() < int(expires) <= time.time() + TTL
                    and hmac.compare_digest(signature(expires + "." + nonce), digest))
        except (ValueError, AttributeError):
            return False

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        if path == "/api/health" and request.method == "GET":
            return JSONResponse({"ok": True, "backend": "fastapi", "deployment": {
                "temporary_storage": temporary_storage, "synthetic_seed": seed_demo,
                "authentication": "shared workspace password", "worker_count": 1}},
                headers={"Cache-Control": "no-store"})
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"error": {"code": "CROSS_SITE_WRITE", "message": "Open the app to submit changes."}}, 403)
        public = path in ("/", "/api/health", "/api/auth/login") or path.startswith("/static/")
        if not public and not valid(request.cookies.get(COOKIE)):
            return JSONResponse({"error": {"code": "LOGIN_REQUIRED", "message": "Sign in to this review workspace."}}, 401)
        response = await call_next(request)
        if path.startswith("/api/"):
            response.headers["Cache-Control"] = "private, no-store"
        return response

    @app.post("/api/auth/login")
    async def login(request: Request):
        try:
            body = await request.json()
            supplied = body.get("password", "")
        except (ValueError, AttributeError):
            supplied = ""
        if not isinstance(supplied, str) or not hmac.compare_digest(supplied.encode(), password.encode()):
            return JSONResponse({"error": {"message": "Incorrect workspace password."}}, 401)
        value = f"{int(time.time()) + TTL}.{secrets.token_hex(16)}"
        response = JSONResponse({"ok": True})
        response.set_cookie(COOKIE, value + "." + signature(value), max_age=TTL, httponly=True,
                            secure=secure, samesite="lax", path="/")
        return response

    @app.post("/api/auth/logout")
    async def logout():
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE, path="/", secure=secure, httponly=True, samesite="lax")
        return response

    return app


if __name__ == "__main__":
    import uvicorn
    app = hosted_app(os.environ.get("SIH_STORAGE_DIR", "/var/sih"), os.environ.get("SIH_APP_PASSWORD", ""),
                     os.environ.get("SIH_SEED_DEMO") == "1",
                     temporary_storage=os.environ.get("SIH_STORAGE_TEMPORARY") == "1")
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "10000")), workers=1)
