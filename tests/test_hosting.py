from fastapi.testclient import TestClient

from backend.hosting import COOKIE, hosted_app


def test_hosted_session_protects_reads_writes_and_survives_restart(tmp_path):
    password = "synthetic-hosting-test-password"
    with TestClient(hosted_app(tmp_path / "pilot", password, seed_demo=True, secure=False)) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/sites").status_code == 401
        assert client.post("/api/auth/login", json={"password": "wrong"}).status_code == 401
        result = client.post("/api/auth/login", json={"password": password})
        assert result.status_code == 200 and "HttpOnly" in result.headers["set-cookie"]
        cookie = client.cookies.get(COOKIE)
        assert client.get("/api/sites").json()["sites"][0]["id"] == "demo"
        assert client.post("/api/auth/logout", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    with TestClient(hosted_app(tmp_path / "pilot", password, seed_demo=True, secure=False)) as client:
        client.cookies.set(COOKIE, cookie, domain="testserver.local", path="/")
        assert client.get("/api/sites").status_code == 200
        assert client.post("/api/auth/logout").status_code == 200
        assert client.get("/api/sites").status_code == 401
        client.cookies.set(COOKIE, cookie + "changed", domain="testserver.local", path="/")
        assert client.get("/api/sites").status_code == 401
