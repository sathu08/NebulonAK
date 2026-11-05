"""Mind credentials resolver tests (stubbed HTTP, offline)."""

from __future__ import annotations

import json

import pytest
import requests

from nak_host.adapters.credentials_mind import (
    CredentialsError,
    MindCredentialsResolver,
)


class _Resp:
    def __init__(self, status, text):
        self.status_code = status
        self.text = text


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _ok_payload():
    return json.dumps({"success": True, "message": "ok",
                       "data": {"base_url": "http://db:6969/api/NebulonDB",
                                "username": "ndb_admin",
                                "password": "ndb-secret"}})


def _resolver(monkeypatch, payload, status=200, clock=None):
    seen = []

    def fake_get(url, headers=None, timeout=None):
        seen.append({"url": url, "headers": dict(headers or {})})
        return _Resp(status, payload)

    monkeypatch.setattr(requests, "get", fake_get)
    resolver = MindCredentialsResolver(
        "http://localhost:9696/api/NebulonMind", "mind-token",
        max_retries=0, retry_backoff=0, clock=clock or _Clock())
    return resolver, seen


def test_resolve_maps_shape_and_bearer(monkeypatch):
    resolver, seen = _resolver(monkeypatch, _ok_payload())
    creds = resolver.resolve()
    assert (creds.base_url, creds.username, creds.password) == (
        "http://db:6969/api/NebulonDB", "ndb_admin", "ndb-secret")
    assert seen[0]["url"].endswith("/system/db_connection")
    assert seen[0]["headers"]["Authorization"] == "Bearer mind-token"


def test_unauthorized_maps_to_credentials_error(monkeypatch):
    resolver, _ = _resolver(monkeypatch, "authentication required", status=401)
    with pytest.raises(CredentialsError):
        resolver.resolve()


def test_refusal_message_preserved(monkeypatch):
    payload = json.dumps({"success": False,
                          "message": "db_connection refused: API auth is disabled",
                          "data": None})
    resolver, _ = _resolver(monkeypatch, payload, status=503)
    with pytest.raises(CredentialsError) as exc_info:
        resolver.resolve()
    assert "auth is disabled" in str(exc_info.value)


def test_unreachable_and_incomplete(monkeypatch):
    def down(url, headers=None, timeout=None):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(requests, "get", down)
    resolver = MindCredentialsResolver("http://localhost:9696/api/NebulonMind",
                                       "t", max_retries=0, retry_backoff=0)
    with pytest.raises(CredentialsError):
        resolver.resolve()

    thin, _ = _resolver(monkeypatch, json.dumps(
        {"success": True, "data": {"base_url": "http://x", "username": "u"}}))
    with pytest.raises(CredentialsError):
        thin.resolve()


def test_ttl_cache_and_refresh(monkeypatch):
    clock = _Clock()
    resolver, seen = _resolver(monkeypatch, _ok_payload(), clock=clock)
    assert resolver.resolve() == resolver.resolve()
    assert len(seen) == 1
    clock.t += 901.0
    resolver.resolve()
    assert len(seen) == 2
    resolver.refresh()
    assert len(seen) == 3
