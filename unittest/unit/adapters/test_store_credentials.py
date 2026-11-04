"""Resolver wiring: fill-on-ensure, explicit-wins, 401 refresh (offline)."""

from __future__ import annotations

import pytest
import requests

from nak.adapters.credentials_mind import CredentialsError, NDBCredentials
from nak.adapters.store_nebulondb import (
    NDBAuthError,
    NebulonDBTaskStore,
    _NDBClient,
)
from test_store_nebulondb import FakeNDBServer, _task


class _Resolver:
    def __init__(self, user="resolved-user", password="resolved-pw",
                 base_url="http://resolved:6969/api/NebulonDB"):
        self.creds = NDBCredentials(base_url=base_url, username=user,
                                    password=password)
        self.resolves = 0
        self.refreshes = 0

    def resolve(self):
        self.resolves += 1
        return self.creds

    def refresh(self):
        self.refreshes += 1
        return self.creds


def test_fill_missing_from_resolver():
    server = FakeNDBServer()
    resolver = _Resolver()
    # Empty base_url means "not configured" -> resolved from Mind.
    store = NebulonDBTaskStore(client=server, credentials_resolver=resolver,
                               base_url="")
    store.create_task(_task())
    assert resolver.resolves == 1
    assert server.auth == ("resolved-user", "resolved-pw")
    assert server.base == "http://resolved:6969/api/NebulonDB"
    # Second op reuses the filled values (lazy ensure ran once).
    store.get_task("t1")
    assert resolver.resolves == 1


def test_explicit_fields_never_overwritten():
    server = FakeNDBServer()
    resolver = _Resolver()
    store = NebulonDBTaskStore(client=server, credentials_resolver=resolver,
                               user="explicit-user", password="explicit-pw")
    store.create_task(_task())
    assert server.auth == ("explicit-user", "explicit-pw")


def test_resolver_failure_surfaces_clearly():
    server = FakeNDBServer()

    class Down:
        def resolve(self):
            raise CredentialsError("nebulonmind unreachable: down")

        def refresh(self):
            raise CredentialsError("nebulonmind unreachable: down")

    store = NebulonDBTaskStore(client=server, credentials_resolver=Down())
    with pytest.raises(CredentialsError):
        store.create_task(_task())


def test_client_reauth_on_401_then_succeeds(monkeypatch):
    seen = []
    refreshed = []

    class Resp401:
        status_code = 401
        text = "nope"

    class Resp200:
        status_code = 200
        text = ""

        def json(self):
            return {"success": True, "data": {}}

    plan = [Resp401(), Resp200()]

    def fake_post(url, json=None, auth=None, timeout=None):
        seen.append((auth.username, auth.password))
        return plan[len(seen) - 1]

    def reauth(client):
        refreshed.append(1)
        client.set_auth("u", "new")

    monkeypatch.setattr(requests, "post", fake_post)
    client = _NDBClient("http://x", "u", "old", max_retries=0, retry_backoff=0,
                        on_unauthorized=lambda: reauth(client))
    assert client.post("/x", {}) == {"success": True, "data": {}}
    assert refreshed == [1]
    assert seen == [("u", "old"), ("u", "new")]


def test_unauthorized_without_resolver_is_final(monkeypatch):
    def fake_post(url, json=None, auth=None, timeout=None):
        class Resp401:
            status_code = 401
            text = "nope"

        return Resp401()

    monkeypatch.setattr(requests, "post", fake_post)
    client = _NDBClient("http://x", "u", "p", max_retries=0, retry_backoff=0)
    with pytest.raises(NDBAuthError):
        client.post("/x", {})


def test_store_reauth_refreshes_resolved_only():
    server = FakeNDBServer()
    resolver = _Resolver()
    store = NebulonDBTaskStore(client=server, credentials_resolver=resolver,
                               user="explicit-user")
    store.create_task(_task())
    assert server.auth == ("explicit-user", "resolved-pw")
    store._reauth()
    assert resolver.refreshes == 1
    assert server.auth == ("explicit-user", "resolved-pw")
