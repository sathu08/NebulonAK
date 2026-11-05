"""Console server tests (TestClient, no services needed)."""
from fastapi.testclient import TestClient

from nak_host.api.main import app
from nak_host.utils.settings import load_settings

client = TestClient(app, raise_server_exceptions=False)


def test_laya_route_mounted_and_validates():
    # Laya stays on the decision prefix (workers.json points there).
    # Missing 'questions' -> 400 without running inference (no weights needed).
    resp = client.post("/api/NebulonAgentKit/laya/", json={})
    assert resp.status_code == 400
    resp = client.post("/api/NebulonAgentKit/laya", json={})
    assert resp.status_code == 400


def test_status_never_raises_offline():
    resp = client.get("/api/NebulonAK/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "ok" in body and "checks" in body
    assert {c["label"] for c in body["checks"]} >= {
        "server", "nebulonmind_live", "nebulonmind_ready",
        "nebulondb", "laya", "taskstore"}


def test_settings_redacts_secrets():
    token = load_settings().auth_token
    assert token, "test needs a real token in nebulonak.cfg"
    resp = client.get("/api/NebulonAK/settings")
    assert resp.status_code == 200
    body = resp.text
    assert "***" in body
    assert token not in body
    assert token[:8] not in body


def test_manifests_lists_registries():
    resp = client.get("/api/NebulonAK/settings/manifests")
    assert resp.status_code == 200
    body = resp.json()
    assert "arivu" in body["workers"]
    assert "core" in body["teams"]
    assert "build" in body["crafts"]
    assert "search" in body["tools"]


def test_pipeline_endpoints_offline():
    tasks = client.get("/api/NebulonAK/pipeline/tasks")
    assert tasks.status_code == 200
    assert tasks.json()["backend"] in ("memory", "nebulondb")
    missing = client.get("/api/NebulonAK/pipeline/tasks/no-such-task")
    assert missing.status_code in (404, 503)
    vetting = client.get("/api/NebulonAK/pipeline/vetting")
    assert vetting.status_code == 200
    assert "runs" in vetting.json()
    sessions = client.get("/api/NebulonAK/pipeline/sessions")
    assert sessions.status_code == 200
    assert "sessions" in sessions.json()


def test_console_pages_serve_html():
    for page in ("", "pipeline", "settings"):
        resp = client.get(f"/api/NebulonAK/dashboard/{page}")
        assert resp.status_code == 200, page
        assert "<html" in resp.text.lower()
