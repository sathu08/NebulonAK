"""Resolve NebulonDB credentials from NebulonMD (single configured connection).

NAK configures only its Mind connection; the DB host/username/password come
from Mind's gated ``GET /api/NebulonMind/system/db_connection`` endpoint
(bearer-shared-secret only; the endpoint refuses when Mind auth is disabled).

Security posture (see docs/ARCHITECTURE.md §5):

* credentials live in memory only — never logged, never written to disk;
* in-memory TTL cache (default 15 min); ``refresh()`` forces re-resolution
  (used after a 401 from NebulonDB, i.e. credential rotation);
* failures raise CredentialsError with the server's message and no secrets.
"""

from __future__ import annotations

import threading
import time

from dataclasses import dataclass
from typing import Any

import requests


@dataclass(frozen=True)
class NDBCredentials:
    base_url: str
    username: str
    password: str


class CredentialsError(RuntimeError):
    """Mind credential issuance failed (message carries no secrets)."""


class MindCredentialsResolver:
    """Fetch + cache NDB credentials from NebulonMD (thread-safe)."""

    PATH = "/system/db_connection"

    def __init__(self, mind_base_url: str, auth_token: str,
                 timeout: float = 30.0, max_retries: int = 2,
                 retry_backoff: float = 1.0, ttl: float = 900.0,
                 clock: Any = None) -> None:
        self._base = str(mind_base_url or "").rstrip("/")
        self._token = str(auth_token or "")
        self._timeout = float(timeout) if float(timeout) > 0 else 30.0
        self._retries = max(0, int(max_retries))
        self._backoff = max(0.0, float(retry_backoff))
        self._ttl = max(0.0, float(ttl))
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._cached: NDBCredentials | None = None
        self._cached_at: float = 0.0

    def resolve(self) -> NDBCredentials:
        """Cached credentials, fetching from Mind on first use or TTL expiry."""
        with self._lock:
            if self._cached is not None and (self._clock() - self._cached_at) < self._ttl:
                return self._cached
            creds = self._fetch_locked()
            self._cached = creds
            self._cached_at = self._clock()
            return creds

    def refresh(self) -> NDBCredentials:
        """Force re-resolution (call after a 401 from NebulonDB)."""
        with self._lock:
            creds = self._fetch_locked()
            self._cached = creds
            self._cached_at = self._clock()
            return creds

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    def _fetch_locked(self) -> NDBCredentials:
        url = self._base + self.PATH
        tries = 1 + self._retries
        last: Any = None
        for i in range(tries):
            try:
                resp = requests.get(url, headers=self._headers(),
                                    timeout=self._timeout)
            except (requests.Timeout, requests.ConnectionError) as exc:
                last = exc
            else:
                return self._interpret(resp.status_code, resp.text)
            if i + 1 < tries:
                time.sleep(self._backoff * (2 ** i))
        raise CredentialsError(f"nebulonmind unreachable: {last}")

    @staticmethod
    def _interpret(status: int, text: str) -> NDBCredentials:
        if status == 401:
            raise CredentialsError(
                "nebulonmind rejected the credentials "
                "(check NAK_USER / NAK_AUTH_TOKEN)")
        if status == 503:
            raise CredentialsError(_server_message(text) or
                                   "nebulonmind refused credential issuance")
        if status >= 400:
            raise CredentialsError(
                f"nebulonmind HTTP {status}: {text[:200]}")
        try:
            import json as _json

            body = _json.loads(text)
        except ValueError as exc:
            raise CredentialsError("nebulonmind returned non-JSON") from exc
        if not isinstance(body, dict) or not body.get("success"):
            raise CredentialsError(_server_message(text) or
                                   "nebulonmind refused credential issuance")
        data = body.get("data") or {}
        base_url = str(data.get("base_url", "") or "").rstrip("/")
        username = str(data.get("username", "") or "")
        password = str(data.get("password", "") or "")
        if not base_url or not username or not password:
            raise CredentialsError("nebulonmind returned incomplete credentials")
        return NDBCredentials(base_url=base_url, username=username,
                              password=password)


def _server_message(text: str) -> str:
    try:
        import json as _json

        body = _json.loads(text)
    except ValueError:
        return ""
    if isinstance(body, dict):
        return str(body.get("message", "") or "")[:300]
    return ""
