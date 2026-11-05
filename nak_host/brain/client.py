"""NAK Brain — thin HTTP adapter over NebulonMD.

GOLDEN RULE (enforced by design):
  * Every LLM call in NebulonAK goes through THIS class.
  * This class performs NO LLM inference itself — each method is a
    plain HTTP call to NebulonMD (default http://localhost:9696/api/NebulonMind).
  * There are NO LLM settings here: no provider, no model, no API key,
    no vendor base URL. Configure those in NebulonMD
    (`nebulonmd.cfg [llm]` + `.env`) — never in NebulonAK.

Coverage (Full API, sync `requests` only, Python 3.10+):
  health / live / ready / metrics / llm_status (read-only) |
  user create / resolve / setup / ensure |
  memory remember / get / update / delete / relate / doc-page helper |
  search / context | decide / process |
  agent chat + sessions | chats | evaluation | background | config read

Usage:
    from nak_host.brain import Brain
    brain = Brain()  # reads nebulonak.cfg + NAK_* env
    brain.ensure_user()
    brain.remember("I am building NebulonAK on NebulonMD")
    print(brain.search("what am I building?"))
    print(brain.chat("What do you remember about my project?")["answer"])
"""

from __future__ import annotations

import logging
import time

from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from nak_host.utils.constant import (
    BRAIN_CHAT_TIMEOUT_FLOOR,
    BRAIN_DEFAULT_TOP_K,
    BRAIN_EVAL_TIMEOUT_FLOOR,
    HTTP_RETRYABLE_STATUS,
)

from .config import NAKConfig, load_config

logger = logging.getLogger("nak_host.brain")


class BrainError(RuntimeError):
    """Raised when NebulonMD rejects the call or is unreachable."""

    def __init__(
        self,
        message: str,
        status: Optional[int] = None,
        body: Optional[dict] = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.body = body or {}


class Brain:
    """Thin sync client for NebulonMD. All intelligence lives server-side."""

    #: Default recall depth when a call omits `top_k` (see constant).
    default_top_k: int = BRAIN_DEFAULT_TOP_K
    #: Default context budget for `context()`.
    default_max_characters: int = 4000

    def __init__(
        self,
        base_url: Optional[str] = None,
        user: Optional[str] = None,
        cfg_path: Optional[str | Path] = None,
        timeout: Optional[float] = None,
        auth_token: Optional[str] = None,
        config: Optional[NAKConfig] = None,
        max_retries: Optional[int] = None,
        retry_backoff: Optional[float] = None,
    ) -> None:
        self._config = config or load_config(cfg_path)
        self.base_url = (base_url or self._config.base_url).rstrip("/")
        self.user = (user or self._config.user).strip() or "nmd_user_01"
        self.timeout = float(timeout) if timeout is not None else self._config.timeout
        self.auth_token = auth_token if auth_token is not None else self._config.auth_token
        self.max_retries = int(max_retries) if max_retries is not None else self._config.max_retries
        self.retry_backoff = float(retry_backoff) if retry_backoff is not None else self._config.retry_backoff

        self._session = requests.Session()
        self._session.headers.update(
            {"Content-Type": "application/json", "Accept": "application/json"}
        )
        if self.auth_token:
            self._session.headers["Authorization"] = f"Bearer {self.auth_token}"

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.base_url}{path}"

    #: HTTP statuses worth one more attempt (overload / restart / limiter).
    RETRYABLE_STATUS = HTTP_RETRYABLE_STATUS

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
        retries: Optional[int] = None,
    ) -> Dict[str, Any]:
        """One logical call with bounded retries on transient failures.

        Retried: connection errors, timeouts, HTTP 429/502/503/504 —
        up to `retries` (default max_retries) extra attempts with
        exponential backoff. Never retried: other 4xx and success:false
        envelopes (fail fast).
        """
        url = self._url(path)
        t = timeout if timeout is not None else self.timeout
        budget = self.max_retries if retries is None else retries
        attempts = max(0, budget) + 1
        last_error: Optional[BrainError] = None
        for attempt in range(1, attempts + 1):
            try:
                resp = self._session.request(method, url, params=params, json=payload, timeout=t)
            except requests.RequestException as exc:
                last_error = BrainError(f"NebulonMD unreachable at {self.base_url}: {exc}")
            else:
                try:
                    body = resp.json() if resp.content else {}
                except ValueError:
                    body = {"message": resp.text[:500], "success": resp.ok}
                if not isinstance(body, dict):
                    body = {"data": body, "success": resp.ok}
                if resp.status_code in self.RETRYABLE_STATUS:
                    msg = body.get("message") or body.get("detail") or f"HTTP {resp.status_code} for {method} {path}"
                    last_error = BrainError(str(msg), status=resp.status_code, body=body)
                elif resp.status_code >= 400:
                    msg = body.get("message") or body.get("detail") or f"HTTP {resp.status_code} for {method} {path}"
                    raise BrainError(str(msg), status=resp.status_code, body=body)
                elif body.get("success") is False:
                    msg = body.get("message") or f"NebulonMD rejected {method} {path}"
                    raise BrainError(str(msg), status=resp.status_code, body=body)
                else:
                    return body
            if attempt < attempts:
                wait = max(0.0, self.retry_backoff) * (2 ** (attempt - 1))
                logger.warning("[brain] %s %s transient (%s); retry %d/%d in %.1fs",
                               method, path, last_error, attempt, attempts - 1, wait)
                if wait:
                    time.sleep(wait)
        assert last_error is not None
        raise last_error

    def _user_params(self, user: Optional[str] = None) -> Dict[str, str]:
        return {"user_id": (user or self.user).strip()}

    # ------------------------------------------------------------------ #
    # Health / status (no LLM inference; llm_status is read-only observe)
    # ------------------------------------------------------------------ #

    def health(self, user: Optional[str] = None) -> Dict[str, Any]:
        """GET /health — service + backend status."""
        body = self._request("GET", "/health", params=self._user_params(user))
        return body.get("data", body)

    def health_live(self) -> Dict[str, Any]:
        """GET /health/live — liveness probe (no auth needed)."""
        body = self._request("GET", "/health/live")
        return body.get("data", body)

    def health_ready(self) -> Dict[str, Any]:
        """GET /health/ready — readiness (503 body returned, not raised).

        Single shot (retries=0): a readiness probe must answer fast,
        not wait out an outage. Use require_mind() for the gated version.
        """
        try:
            body = self._request("GET", "/health/ready", retries=0)
            return body.get("data", body)
        except BrainError as exc:
            if exc.status == 503:
                return exc.body.get("data", exc.body) if isinstance(exc.body, dict) else {"raw": exc.body}
            raise

    def require_mind(self) -> Dict[str, Any]:
        """Hard gate: fail fast unless Mind AND its DB are up. Raises BrainError.

        * Mind process down -> connection error -> "NebulonMD is down".
        * Mind up but NebulonDB down -> /health/ready is 503 -> "not ready".
        Call before any Mind-backed work (worker chat/decide/process do).
        Returns the ready payload when everything is up.
        """
        try:
            self.health_live()
        except BrainError as exc:
            raise BrainError(
                f"NebulonMD is down at {self.base_url} — AgentKit will not work. "
                "Start order: NebulonDB (:6969) first, then NebulonMD (:9696). "
                f"({exc})", status=exc.status, body=exc.body) from exc
        try:
            body = self._request("GET", "/health/ready")
        except BrainError as exc:
            if exc.status == 503:
                raise BrainError(
                    "NebulonMD is not ready — NebulonDB is down or unreachable, "
                    "so AgentKit will not work. Start NebulonDB first, then "
                    "restart NebulonMD.", status=503, body=exc.body) from exc
            raise BrainError(
                f"NebulonMD readiness check failed at {self.base_url}: {exc}",
                status=exc.status, body=exc.body) from exc
        return body.get("data", body)

    def llm_status(self) -> Dict[str, Any]:
        """GET /llm/status — observe Mind's provider/model (never set it here)."""
        body = self._request("GET", "/llm/status", params=self._user_params())
        return body.get("data", body)

    def metrics(self) -> str:
        """GET /metrics (server root, Prometheus text) — stays public."""
        from urllib.parse import urlsplit

        try:
            parts = urlsplit(self.base_url)
            root = f"{parts.scheme}://{parts.netloc}".rstrip("/")
            if not parts.scheme or not parts.netloc:
                raise ValueError(f"bad base_url {self.base_url!r}")
        except (ValueError, AttributeError) as exc:
            raise BrainError(f"metrics unreachable: {exc}") from exc
        try:
            resp = self._session.get(f"{root}/metrics", timeout=self.timeout)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:
            raise BrainError(f"metrics unreachable: {exc}") from exc

    # ------------------------------------------------------------------ #
    # Users — Mind owns identity; brain only forwards.
    # ------------------------------------------------------------------ #

    def create_user(self, username: Optional[str] = None) -> Dict[str, Any]:
        """POST /user/create_user — idempotent registration."""
        u = (username or self.user).strip()
        if not u or u.startswith("/"):
            raise ValueError(f"invalid username {u!r}")
        body = self._request("POST", "/user/create_user", payload={"username": u})
        return body.get("data", body)

    def resolve_user(self, username: Optional[str] = None) -> Dict[str, Any]:
        """GET /user/resolve?username= — lookup, never auto-registers."""
        u = (username or self.user).strip()
        body = self._request("GET", "/user/resolve", params={"username": u})
        return body.get("data", body)

    def setup_user(self, username: Optional[str] = None) -> Dict[str, Any]:
        """POST /user/setup — activate an existing username."""
        u = (username or self.user).strip()
        body = self._request("POST", "/user/setup", payload={"username": u})
        return body.get("data", body)

    def ensure_user(self, username: Optional[str] = None) -> Dict[str, Any]:
        """Resolve-or-create (create_user is idempotent, so safe to retry)."""
        u = (username or self.user).strip()
        try:
            return self.resolve_user(u)
        except BrainError as exc:
            if exc.status == 404:
                return self.create_user(u)
            raise
        except Exception:
            return self.create_user(u)

    # ------------------------------------------------------------------ #
    # Memory CRUD — storage lives in Mind/DB, never locally.
    # ------------------------------------------------------------------ #

    def remember(
        self,
        text: str,
        *,
        category: Optional[str] = None,
        memory_type: Optional[str] = None,
        lang: str = "en",
        importance: Optional[float] = None,
        retention_policy: Optional[str] = None,
        gate: bool = True,
        user: Optional[str] = None,
    ) -> Dict[str, Any]:
        """POST /memory — store one memory (lifecycle gate on by default)."""
        if not text or not text.strip():
            raise ValueError("text must be non-empty")
        effective_user = (user or self.user).strip()
        payload: Dict[str, Any] = {
            "user_id": effective_user,
            "content": {"text": text.strip()},
        }
        classification: Dict[str, Any] = {}
        if lang:
            classification["lang"] = lang
        if memory_type:
            classification["memory_type"] = memory_type
        if category:
            classification["category"] = category
        if classification:
            payload["classification"] = classification
        if lang:
            payload["lang"] = lang
        if memory_type:
            payload["memory_type"] = memory_type
        if importance is not None:
            payload["importance"] = {"score": float(importance)}
        if retention_policy:
            payload["lifecycle"] = {"retention_policy": retention_policy}
        params = self._user_params(user)
        params["gate"] = str(gate).lower()
        body = self._request("POST", "/memory", params=params, payload=payload)
        return body.get("data", body)

    def remember_doc_page(
        self,
        text: str,
        filename: str,
        page: int,
        total_pages: int,
        *,
        summary: str = "",
        user: Optional[str] = None,
    ) -> Dict[str, Any]:
        """One PDF page -> one `doc` memory (re-uploads dedupe via gate)."""
        if not text or not text.strip():
            raise ValueError("text must be non-empty")
        effective_user = (user or self.user).strip()
        payload: Dict[str, Any] = {
            "user_id": effective_user,
            "content": {
                "text": text.strip(),
                "summary": summary or f"{filename} p.{page}/{total_pages}",
                "structured_data": {
                    "filename": filename,
                    "page": page,
                    "total_pages": total_pages,
                },
            },
            "classification": {
                "memory_type": "doc",
                "category": "knowledge",
                "source": filename,
                "lang": "en",
            },
            "lifecycle": {"retention_policy": "permanent"},
        }
        params = self._user_params(user)
        params["gate"] = "true"
        body = self._request("POST", "/memory", params=params, payload=payload)
        return body.get("data", body)

    def get_memory(self, memory_id: str, user: Optional[str] = None) -> Dict[str, Any]:
        body = self._request("GET", f"/memory/{memory_id}", params=self._user_params(user))
        return body.get("data", body)

    def update_memory(
        self, memory_id: str, fields: Dict[str, Any], user: Optional[str] = None
    ) -> Dict[str, Any]:
        body = self._request(
            "PUT", f"/memory/{memory_id}", params=self._user_params(user), payload=fields
        )
        return body.get("data", body)

    def delete_memory(self, memory_id: str, user: Optional[str] = None) -> Dict[str, Any]:
        body = self._request("DELETE", f"/memory/{memory_id}", params=self._user_params(user))
        return body.get("data", body)

    def relate(
        self,
        memory_id: str,
        entity: str,
        relation: str = "HAS_ENTITY",
        user: Optional[str] = None,
    ) -> Dict[str, Any]:
        """POST /memory/{id}/relate — memory -> entity graph edge."""
        params = self._user_params(user)
        params.update({"entity": entity, "relation": relation})
        body = self._request("POST", f"/memory/{memory_id}/relate", params=params)
        return body.get("data", body)

    # ------------------------------------------------------------------ #
    # Recall — ranking happens inside Mind.
    # ------------------------------------------------------------------ #

    def search(
        self,
        query: str,
        top_k: Optional[int] = None,
        expand: bool = False,
        user: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """GET /search — lifecycle-ranked recall."""
        if not query or not query.strip():
            raise ValueError("query must be non-empty")
        params = self._user_params(user)
        params["query"] = query.strip()
        params["top_k"] = str(top_k or self.default_top_k)
        params["expand"] = str(expand).lower()
        body = self._request("GET", "/search", params=params)
        data = body.get("data", body)
        if isinstance(data, dict):
            return data.get("results", data.get("memories", []))
        return data if isinstance(data, list) else []

    def context(
        self,
        query: str,
        top_k: Optional[int] = None,
        max_items: int = 10,
        max_characters: Optional[int] = None,
        user: Optional[str] = None,
    ) -> Dict[str, Any]:
        """POST /memory/context — bounded LLM context built BY Mind."""
        params = self._user_params(user)
        params["query"] = query.strip()
        params["top_k"] = str(top_k or self.default_top_k)
        params["max_items"] = str(max_items)
        params["max_characters"] = str(max_characters or self.default_max_characters)
        body = self._request("POST", "/memory/context", params=params)
        return body.get("data", body)

    # ------------------------------------------------------------------ #
    # Intelligence — LLM extraction runs inside Mind, not here.
    # ------------------------------------------------------------------ #

    def decide(
        self,
        conversation: str | List[Dict[str, str]],
        include_rejected: bool = False,
        user: Optional[str] = None,
    ) -> Dict[str, Any]:
        """POST /intelligence/decide — decisions WITHOUT storage."""
        if isinstance(conversation, str):
            payload: Dict[str, Any] = {"text": conversation, "include_rejected": include_rejected}
        else:
            payload = {"conversation": {"turns": conversation}, "include_rejected": include_rejected}
        body = self._request("POST", "/intelligence/decide", params=self._user_params(user), payload=payload)
        return body.get("data", body)

    def process(
        self,
        conversation: str | List[Dict[str, str]],
        persist: bool = True,
        user: Optional[str] = None,
    ) -> Dict[str, Any]:
        """POST /intelligence/process — decide + gate + store (all in Mind)."""
        if isinstance(conversation, str):
            payload = {"text": conversation, "persist": persist}
        else:
            payload = {"conversation": {"turns": conversation}, "persist": persist}
        body = self._request("POST", "/intelligence/process", params=self._user_params(user), payload=payload)
        return body.get("data", body)

    # ------------------------------------------------------------------ #
    # Agent chat + sessions — THE only chat path. No local LLM fallback.
    # ------------------------------------------------------------------ #

    def chat(
        self,
        text: str,
        *,
        messages: Optional[List[Dict[str, str]]] = None,
        session_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        user: Optional[str] = None,
        raw: bool = False,
    ) -> Dict[str, Any]:
        """POST /agent/chat — Mind's tool-calling loop over this user's memory.

        Mind accepts only `user/assistant/tool` roles in `messages`
        (`system` is rejected server-side) — keep history clean upstream.
        ``raw=True`` asks Mind for a direct LLM completion (no memory tools or
        conversation state); external coding agents use this to run their own
        prompt protocol.
        """
        if not text or not text.strip():
            raise ValueError("text must be non-empty")
        payload: Dict[str, Any] = {"text": text.strip()}
        if messages:
            payload["messages"] = messages
        if session_id:
            payload["session_id"] = session_id
        if conversation_id:
            payload["conversation_id"] = conversation_id
        if raw:
            payload["raw"] = True
        timeout = max(self.timeout, BRAIN_CHAT_TIMEOUT_FLOOR)  # LLM turns take 15-45s+ under load
        body = self._request(
            "POST", "/agent/chat", params=self._user_params(user), payload=payload, timeout=timeout
        )
        return body.get("data", body)

    def create_session(
        self, metadata: Optional[Dict[str, Any]] = None, user: Optional[str] = None
    ) -> Dict[str, Any]:
        body = self._request(
            "POST", "/agent/session", params=self._user_params(user), payload={"metadata": metadata or {}}
        )
        return body.get("data", body)

    def get_session(self, session_id: str, user: Optional[str] = None) -> Dict[str, Any]:
        body = self._request("GET", f"/agent/session/{session_id}", params=self._user_params(user))
        return body.get("data", body)

    def list_sessions(self, user: Optional[str] = None) -> List[Dict[str, Any]]:
        body = self._request("GET", "/agent/sessions", params=self._user_params(user))
        data = body.get("data", body)
        if isinstance(data, dict):
            return data.get("sessions", data.get("results", []))
        return data if isinstance(data, list) else []

    def close_session(self, session_id: str, user: Optional[str] = None) -> Dict[str, Any]:
        body = self._request("DELETE", f"/agent/session/{session_id}", params=self._user_params(user))
        return body.get("data", body)

    # ------------------------------------------------------------------ #
    # Chats / eval / background / config-read — pass-throughs.
    # ------------------------------------------------------------------ #

    def list_chats(self, user: Optional[str] = None) -> List[Dict[str, Any]]:
        body = self._request("GET", "/chats", params=self._user_params(user))
        data = body.get("data", body)
        if isinstance(data, dict):
            return data.get("chats", data.get("results", []))
        return data if isinstance(data, list) else []

    def get_chat(self, chat_id: str, user: Optional[str] = None) -> Dict[str, Any]:
        body = self._request("GET", f"/chats/{chat_id}", params=self._user_params(user))
        return body.get("data", body)

    def save_chat(self, chat: Dict[str, Any], user: Optional[str] = None) -> Dict[str, Any]:
        payload = {"user_id": (user or self.user).strip(), "chat": chat}
        body = self._request("POST", "/chats", payload=payload)
        return body.get("data", body)

    def delete_chat(self, chat_id: str, user: Optional[str] = None) -> Dict[str, Any]:
        body = self._request("DELETE", f"/chats/{chat_id}", params=self._user_params(user))
        return body.get("data", body)

    def get_dataset(self) -> Dict[str, Any]:
        body = self._request("GET", "/evaluation/dataset", params=self._user_params())
        return body.get("data", body)

    def run_eval(
        self, dataset: str = "memory_test_v1", seed: bool = True, max_items: int = 10
    ) -> Dict[str, Any]:
        body = self._request(
            "POST",
            "/evaluation/run",
            params=self._user_params(),
            payload={"dataset": dataset, "seed": seed, "max_items": max_items},
            timeout=max(self.timeout, BRAIN_EVAL_TIMEOUT_FLOOR),
        )
        return body.get("data", body)

    def background_memory_run(self, mode: str = "consolidate", user: Optional[str] = None) -> Dict[str, Any]:
        body = self._request(
            "POST", "/background/memory/run", params=self._user_params(user), payload={"mode": mode}
        )
        return body.get("data", body)

    def background_task_run(self, days: int = 7, user: Optional[str] = None) -> Dict[str, Any]:
        body = self._request(
            "POST", "/background/task/run", params=self._user_params(user), payload={"days": days}
        )
        return body.get("data", body)

    def background_status(self, user: Optional[str] = None) -> Dict[str, Any]:
        body = self._request("GET", "/background/status", params=self._user_params(user))
        return body.get("data", body)

    def get_config(self) -> Dict[str, Any]:
        """GET /config/cfg — READ Mind's config (writes stay in Mind's dashboard)."""
        body = self._request("GET", "/config/cfg", params=self._user_params())
        return body.get("data", body)

    # ------------------------------------------------------------------ #
    # Probe
    # ------------------------------------------------------------------ #

    def test_connection(self, probe_write: bool = True, verbose: bool = True) -> Dict[str, Any]:
        """health/live + health + llm_status + ensure_user [+ write/search probe]."""
        results: Dict[str, Any] = {"base_url": self.base_url, "user": self.user, "steps": {}}
        ok = True

        def _step(name: str, fn):
            nonlocal ok
            start = time.time()
            try:
                data = fn()
                elapsed = round((time.time() - start) * 1000)
                results["steps"][name] = {"ok": True, "elapsed_ms": elapsed, "data": data}
                if verbose:
                    logger.info("[brain] %s ok (%d ms)", name, elapsed)
                return data
            except BrainError as exc:
                elapsed = round((time.time() - start) * 1000)
                results["steps"][name] = {
                    "ok": False, "elapsed_ms": elapsed, "error": str(exc),
                    "status": exc.status, "body": exc.body,
                }
                ok = False
                if verbose:
                    logger.warning("[brain] %s FAIL: %s", name, exc)
                return None
            except Exception as exc:  # noqa: BLE001
                elapsed = round((time.time() - start) * 1000)
                results["steps"][name] = {"ok": False, "elapsed_ms": elapsed, "error": str(exc)}
                ok = False
                return None

        _step("health_live", self.health_live)
        _step("health", self.health)
        _step("llm_status", self.llm_status)
        _step("ensure_user", self.ensure_user)
        if probe_write and ok:
            probe_text = f"nak_host connectivity probe {int(time.time())}"

            def _probe():
                stored = self.remember(probe_text, category="probe", gate=True)
                time.sleep(0.5)
                hits = self.search(probe_text, top_k=3)
                return {"stored": stored, "hits": hits, "hits_count": len(hits)}

            _step("write_search_probe", _probe)
            try:
                ws = results["steps"].get("write_search_probe", {})
                stored = (ws.get("data") or {}).get("stored", {})
                mem = stored.get("memory") if isinstance(stored, dict) else None
                mem_id = None
                if isinstance(mem, dict):
                    mem_id = mem.get("memory_id") or mem.get("id")
                elif isinstance(stored, dict):
                    mem_id = stored.get("memory_id") or stored.get("id")
                if mem_id:
                    _step("probe_cleanup", lambda: self.delete_memory(mem_id))
            except Exception:
                pass

        results["ok"] = ok and all(s.get("ok") for s in results["steps"].values())
        results["message"] = "connected" if results["ok"] else "not fully connected; see steps"
        return results

    def __repr__(self) -> str:
        return f"Brain(base_url={self.base_url!r}, user={self.user!r})"
