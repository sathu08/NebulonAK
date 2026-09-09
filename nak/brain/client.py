"""
NAK Brain Client
================
Thin, typed wrapper over NebulonMind's REST API (port 9696).

Design goals:
- Zero coupling to nmd_host internals; only HTTP.
- Friendly errors with no secret leakage.
- `user` scoping on every call (query param `user_id`).
- Future-proof for LLM agent loop: chat() already does recall+remember+decide in-process.

Usage:
    from nak.brain import Brain

    brain = Brain()                          # loads nebulonak.cfg
    brain = Brain(user="alice")              # override user
    brain = Brain(base_url="http://...")     # override URL

    brain.health()           # -> dict {service, backend, minds, ...}
    brain.ensure_user()      # create_user idempotent
    brain.remember("I love Python")
    brain.search("Python")
    brain.chat("What do you remember about me?")
    brain.test_connection()  # health + LLM status + write/read probe

All methods raise BrainError on failure.
"""

from __future__ import annotations

import time
import logging
import requests

from pathlib import Path
from typing import Any, Dict, List, Optional


from ..utils.config import NAKConfig, load_config

logger = logging.getLogger("nak.brain.client")


class BrainError(RuntimeError):
    """Raised when NebulonMind rejects or is unreachable."""

    def __init__(self, message: str, status: Optional[int] = None, body: Optional[dict] = None):
        super().__init__(message)
        self.status = status
        self.body = body or {}


class Brain:
    """NAK Brain -- the memory layer that talks to NebulonMind."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        user: Optional[str] = None,
        cfg_path: Optional[str | Path] = None,
        timeout: Optional[float] = None,
        auth_token: Optional[str] = None,
        config: Optional[NAKConfig] = None,
    ) -> None:
        """
        Args:
            base_url: override NebulonMind base URL (e.g. http://localhost:9696/api/NebulonMind)
            user: override username (otherwise from nebulonak.cfg)
            cfg_path: explicit path to nebulonak.cfg
            timeout: override request timeout (seconds)
            auth_token: override auth token
            config: fully resolved NAKConfig (when given, other overrides still apply)
        """
        self._config = config or load_config(cfg_path)
        # apply overrides on top
        self.base_url = (base_url or self._config.base_url).rstrip("/")
        self.user = (user or self._config.user).strip() or "nmd_user_01"
        self.timeout = float(timeout) if timeout is not None else self._config.timeout
        self.auth_token = auth_token if auth_token is not None else self._config.auth_token
        self._cfg_path = self._config.cfg_path
        self._auto_create = self._config.auto_create_user
        self._persist_user = self._config.persist_user

        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        if self.auth_token:
            self._session.headers["Authorization"] = f"Bearer {self.auth_token}"

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.base_url}{path}"

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        # always inject user_id unless caller explicitly passes username= or user_id= elsewhere
        # NebulonMind expects ?user_id=<username> on every route except /user/create_user variations
        url = self._url(path)
        # choose timeout per method
        t = timeout if timeout is not None else self.timeout
        try:
            resp = self._session.request(
                method, url, params=params, json=payload, timeout=t
            )
        except requests.RequestException as exc:
            raise BrainError(f"NebulonMind unreachable at {self.base_url}: {exc}") from exc

        # try JSON, tolerate non-JSON errors
        try:
            body = resp.json() if resp.content else {}
        except ValueError:
            body = {"message": resp.text[:500], "success": resp.ok}

        if not isinstance(body, dict):
            body = {"data": body, "success": resp.ok}

        if resp.status_code >= 400:
            msg = body.get("message") or body.get("detail") or f"HTTP {resp.status_code} for {method} {path}"
            # NebulonMind wraps 403 for unregistered user -> surface clearly
            raise BrainError(str(msg), status=resp.status_code, body=body)

        if body.get("success") is False:
            msg = body.get("message") or f"NebulonMind rejected {method} {path}"
            raise BrainError(str(msg), status=resp.status_code, body=body)

        return body

    def _user_params(self, user: Optional[str] = None) -> Dict[str, str]:
        u = (user or self.user).strip()
        return {"user_id": u}

    # ------------------------------------------------------------------ #
    # Health / status
    # ------------------------------------------------------------------ #

    def health(self, user: Optional[str] = None) -> Dict[str, Any]:
        """GET /health -- service + backend status (compat)."""
        body = self._request("GET", "/health", params=self._user_params(user))
        return body.get("data", body)

    def health_live(self) -> Dict[str, Any]:
        """GET /health/live -- liveness probe."""
        body = self._request("GET", "/health/live")
        return body.get("data", body)

    def health_ready(self) -> Dict[str, Any]:
        """GET /health/ready -- readiness probe (503 if backend down)."""
        try:
            body = self._request("GET", "/health/ready")
            return body.get("data", body)
        except BrainError as exc:
            # 503 is expected when backend down; return body instead of raising for probing
            if exc.status == 503:
                return exc.body.get("data", exc.body) if isinstance(exc.body, dict) else {"raw": exc.body}
            raise

    def llm_status(self) -> Dict[str, Any]:
        """GET /llm/status -- provider health."""
        body = self._request("GET", "/llm/status", params=self._user_params())
        return body.get("data", body)

    def metrics(self) -> str:
        """GET /metrics -- Prometheus exposition (text)."""
        url = self.base_url.replace("/api/NebulonMind", "/metrics")
        try:
            resp = self._session.get(url, timeout=self.timeout)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:
            raise BrainError(f"metrics unreachable: {exc}") from exc

    # ------------------------------------------------------------------ #
    # User management
    # ------------------------------------------------------------------ #

    def create_user(self, username: Optional[str] = None) -> Dict[str, Any]:
        """POST /user/create_user -- idempotent registration."""
        u = (username or self.user).strip()
        if not u or u.startswith("/"):
            raise ValueError(f"invalid username {u!r}")
        body = self._request("POST", "/user/create_user", payload={"username": u})
        data = body.get("data", body)
        # persist if configured
        if self._persist_user and username is None:
            try:
                self._config.set_user(u)
            except Exception:
                pass
        return data

    def resolve_user(self, username: Optional[str] = None) -> Dict[str, Any]:
        """GET /user/resolve -- lookup without creating."""
        u = (username or self.user).strip()
        body = self._request("GET", "/user/resolve", params={"username": u})
        return body.get("data", body)

    def setup_user(self, username: Optional[str] = None) -> Dict[str, Any]:
        """POST /user/setup -- activate existing user."""
        u = (username or self.user).strip()
        body = self._request("POST", "/user/setup", payload={"username": u})
        return body.get("data", body)

    def ensure_user(self, username: Optional[str] = None) -> Dict[str, Any]:
        """Ensure the username is registered (create if missing). Returns user Data."""
        u = (username or self.user).strip()
        # try resolve, then create
        try:
            return self.resolve_user(u)
        except BrainError as exc:
            if exc.status == 404:
                return self.create_user(u)
            raise
        except Exception:
            # fallback: create_user is idempotent
            return self.create_user(u)

    # ------------------------------------------------------------------ #
    # Memory CRUD
    # ------------------------------------------------------------------ #

    def remember(
        self,
        text: str,
        *,
        category: Optional[str] = None,
        memory_type: Optional[str] = None,
        lang: str = "en",
        importance: Optional[float] = None,
        gate: bool = True,
        user: Optional[str] = None,
        retention_policy: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        POST /memory -- store a memory directly.
        With gate=true (default) the lifecycle gate runs (dedupe, expiry, supersede).

        Args:
            text: memory content (required)
            category: optional category hint
            memory_type: doc|semantic|episodic|working|short_term|long_term|knowledge
            lang: language code
            importance: 0.0-1.0 importance
            gate: run lifecycle ingest gate
            retention_policy: permanent|temporary|session
        """
        if not text or not text.strip():
            raise ValueError("text must be non-empty")
        # Build payload matching NebulonMind's MemoryCreate schema:
        #   { user_id, content: {text}, classification: {...}, importance: {...}, lifecycle: {...} }
        effective_user = (user or self.user).strip()
        # resolve opaque user_id for body -- try to use resolved id if we have it, but
        # server will pin it anyway from query param, so just send username
        payload: Dict[str, Any] = {
            "user_id": effective_user,
            "content": {"text": text.strip()},
        }
        # classification merge (lang / memory_type / category)
        classification: Dict[str, Any] = {}
        if lang:
            classification["lang"] = lang
        if memory_type:
            classification["memory_type"] = memory_type
        if category:
            classification["category"] = category
        if classification:
            payload["classification"] = classification
        # also send top-level lang/memory_type shorthands for compat (server merges them too)
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

    def get_memory(self, memory_id: str, user: Optional[str] = None) -> Dict[str, Any]:
        body = self._request("GET", f"/memory/{memory_id}", params=self._user_params(user))
        return body.get("data", body)

    def update_memory(self, memory_id: str, fields: Dict[str, Any], user: Optional[str] = None) -> Dict[str, Any]:
        body = self._request("PUT", f"/memory/{memory_id}", params=self._user_params(user), payload=fields)
        return body.get("data", body)

    def delete_memory(self, memory_id: str, user: Optional[str] = None) -> Dict[str, Any]:
        body = self._request("DELETE", f"/memory/{memory_id}", params=self._user_params(user))
        return body.get("data", body)

    def relate(self, memory_id: str, entity: str, relation: str = "HAS_ENTITY", user: Optional[str] = None) -> Dict[str, Any]:
        params = self._user_params(user)
        params.update({"entity": entity, "relation": relation})
        body = self._request("POST", f"/memory/{memory_id}/relate", params=params)
        return body.get("data", body)

    # ------------------------------------------------------------------ #
    # Retrieval / Search / Context
    # ------------------------------------------------------------------ #

    def search(
        self,
        query: str,
        top_k: Optional[int] = None,
        expand: bool = False,
        user: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """GET /search -- lifecycle-ranked recall."""
        if not query or not query.strip():
            raise ValueError("query must be non-empty")
        params = self._user_params(user)
        params.update({
            "q": query.strip(),
            "query": query.strip(),  # compat; NebulonMind uses `q` or `query`
            "top_k": str(top_k or self._config.default_top_k),
            "expand": str(expand).lower(),
        })
        # NebulonMind actually expects `q` param (check server.py: query = Query(...))
        # server.py uses `query` param name `query` -> we send both for compat, and correct `query`
        # The route is `GET /search?query=...&top_k=...` in server.py
        # So send `query` correctly:
        params = self._user_params(user)
        params["query"] = query.strip()
        params["top_k"] = str(top_k or self._config.default_top_k)
        params["expand"] = str(expand).lower()
        body = self._request("GET", "/search", params=params)
        data = body.get("data", body)
        if isinstance(data, dict):
            return data.get("results", data.get("memories", []))
        if isinstance(data, list):
            return data
        return []

    def context(
        self,
        query: str,
        top_k: Optional[int] = None,
        max_items: int = 10,
        max_characters: Optional[int] = None,
        user: Optional[str] = None,
    ) -> Dict[str, Any]:
        """POST /memory/context -- bounded LLM context."""
        params = self._user_params(user)
        params["query"] = query.strip()
        params["top_k"] = str(top_k or self._config.default_top_k)
        params["max_items"] = str(max_items)
        params["max_characters"] = str(max_characters or self._config.default_max_characters)
        body = self._request("POST", "/memory/context", params=params)
        return body.get("data", body)

    # ------------------------------------------------------------------ #
    # Intelligence (decision / ingest)
    # ------------------------------------------------------------------ #

    def decide(self, conversation: str | List[Dict[str, str]], include_rejected: bool = False, user: Optional[str] = None) -> Dict[str, Any]:
        """
        POST /intelligence/decide -- extract memory decisions without storing.
        conversation may be a plain string or list of {role, content}.
        Server accepts {text: "..."} or {conversation: {turns: [...]}}.
        """
        if isinstance(conversation, str):
            payload: Dict[str, Any] = {"text": conversation, "include_rejected": include_rejected}
        else:
            # list of turns -> wrap as conversation.turns
            payload = {"conversation": {"turns": conversation}, "include_rejected": include_rejected}
        body = self._request("POST", "/intelligence/decide", params=self._user_params(user), payload=payload)
        return body.get("data", body)

    def process(self, conversation: str | List[Dict[str, str]], persist: bool = True, user: Optional[str] = None) -> Dict[str, Any]:
        """
        POST /intelligence/process -- decide + lifecycle gate + store.
        """
        if isinstance(conversation, str):
            payload: Dict[str, Any] = {"text": conversation, "persist": persist}
        else:
            payload = {"conversation": {"turns": conversation}, "persist": persist}
        body = self._request("POST", "/intelligence/process", params=self._user_params(user), payload=payload)
        return body.get("data", body)

    # ------------------------------------------------------------------ #
    # Agent chat (the main LLM loop)
    # ------------------------------------------------------------------ #

    def chat(
        self,
        text: str,
        *,
        messages: Optional[List[Dict[str, str]]] = None,
        session_id: Optional[str] = None,
        user: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        POST /agent/chat -- tool-calling loop (recall / remember / decide) in-process.
        This is the primary chat endpoint; it grounds answers with stored memories
        and persists new facts automatically via the LLM tool calls.

        Args:
            text: current user utterance (required)
            messages: prior turns [{role, content}, ...] for stateless history
            session_id: optional persistent session id (create via create_session)
            conversation_id: optional correlation id
        Returns:
            dict with keys: answer, turn, tool_calls, transcript, etc.
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
        # chat involves LLM -> allow longer read (write_timeout)
        timeout = max(self.timeout, self._config.write_timeout, 60)
        body = self._request("POST", "/agent/chat", params=self._user_params(user), payload=payload, timeout=timeout)
        return body.get("data", body)

    # Sessions
    def create_session(self, metadata: Optional[Dict[str, Any]] = None, user: Optional[str] = None) -> Dict[str, Any]:
        payload = {"metadata": metadata or {}}
        body = self._request("POST", "/agent/session", params=self._user_params(user), payload=payload)
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

    # Background
    def background_status(self, user: Optional[str] = None) -> Dict[str, Any]:
        body = self._request("GET", "/background/status", params=self._user_params(user))
        return body.get("data", body)

    def background_memory_run(self, mode: str = "consolidate", user: Optional[str] = None) -> Dict[str, Any]:
        body = self._request("POST", "/background/memory/run", params=self._user_params(user), payload={"mode": mode})
        return body.get("data", body)

    def background_task_run(self, days: int = 7, user: Optional[str] = None) -> Dict[str, Any]:
        body = self._request("POST", "/background/task/run", params=self._user_params(user), payload={"days": days})
        return body.get("data", body)

    # ------------------------------------------------------------------ #
    # Convenience: connectivity test
    # ------------------------------------------------------------------ #

    def test_connection(self, probe_write: bool = True, verbose: bool = True) -> Dict[str, Any]:
        """
        End-to-end connectivity probe:
          1. GET /health/live
          2. GET /health
          3. GET /llm/status
          4. ensure_user (create if needed)
          5. optional write+search round-trip (remember + search)

        Returns a dict with each step's result and a final `ok` boolean.
        Raises BrainError only on catastrophic failure; otherwise returns details.
        """
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
                results["steps"][name] = {"ok": False, "elapsed_ms": elapsed, "error": str(exc), "status": exc.status, "body": exc.body}
                ok = False
                if verbose:
                    logger.warning("[brain] %s FAIL (%d ms): %s", name, elapsed, exc)
                return None
            except Exception as exc:
                elapsed = round((time.time() - start) * 1000)
                results["steps"][name] = {"ok": False, "elapsed_ms": elapsed, "error": str(exc)}
                ok = False
                if verbose:
                    logger.warning("[brain] %s FAIL (%d ms): %s", name, elapsed, exc)
                return None

        _step("health_live", self.health_live)
        _step("health", self.health)
        _step("llm_status", self.llm_status)
        user_data = _step("ensure_user", self.ensure_user)
        # only probe write if earlier steps succeeded
        if probe_write and ok:
            probe_text = f"nak connectivity probe {int(time.time())}"
            def _probe():
                stored = self.remember(probe_text, category="probe", gate=True)
                # small delay for indexing
                time.sleep(0.5)
                hits = self.search(probe_text, top_k=3)
                return {"stored": stored, "hits": hits, "hits_count": len(hits)}
            _step("write_search_probe", _probe)
            # cleanup: best-effort delete if we got an id
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
