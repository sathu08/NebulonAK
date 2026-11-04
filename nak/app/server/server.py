"""nak.plugins.tool.api_tool -- shared HTTP boundary for ALL pipelines.

Mirrors terminal_tool (the CLI boundary) for the web: the HTTP route table
lives here (not copied per pipeline), each pipeline serves it with its own
service:

    from nak.plugins.tool import api_tool
    from nak.service import create_service

    api_tool.serve(host="127.0.0.1", port=8080,
                   create_service=create_service)  # blocks, stdlib only

Routes (JSON in/out, single pipeline turn per request like the CLI):
    GET  /api/health    brain health + LLM status + user
    GET  /api/agents    registered agents (offline, no Mind needed)
    POST /api/ask       {text, auto_create?, instant_route?} -> one harness turn
    POST /api/remember  {text} -> store a memory directly
    POST /api/search    {query, top_k?} -> semantic search memories
    POST /api/session-new -> mint a fresh Mind session
    GET  /api/catalog   drag-drop node catalogue (static + registered agents)

Like terminal_tool this is NOT an LLM-callable tool (human/browser
boundary), so it stays OUT of PLUGIN_TOOLS on purpose. Nothing here imports
any pipeline — the caller supplies create_service (same factory the CLI
uses), and dispatch() maps one HTTP request -> one service call.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Dict, Tuple

# Canvas node: the browser as a sink block (fetch -> pipeline -> render).
API_NODE_SPEC = {
    "type": "nak.api",
    "label": "Web API",
    "description": "Human/browser boundary: HTTP JSON API over one pipeline. "
                   "Owns no chat loop; the page calls one pipeline turn per request.",
    "inputs": ["text"],
    "outputs": ["answer"],
    "params": {"host": "str=127.0.0.1", "port": "int=8080"},
}


def _ok(data: Any) -> Tuple[int, Dict[str, Any]]:
    if isinstance(data, dict):
        return 200, data
    return 200, {"result": data}


def _err(message: str, status: int = 400) -> Tuple[int, Dict[str, Any]]:
    return status, {"error": str(message)[:500]}


def handle_ask_api(body: Dict[str, Any], svc) -> Tuple[int, Dict[str, Any]]:
    """POST /api/ask {text, auto_create?, instant_route?} -> harness turn dict."""
    text = str((body or {}).get("text", "") or "").strip()
    if not text:
        return _err("text must be non-empty", 400)
    try:
        res = svc.chat_turn_sync(
            text,
            auto_create=bool((body or {}).get("auto_create", False)),
            instant_route=True if (body or {}).get("instant_route") else None,
        )
    except (ValueError, FileExistsError, FileNotFoundError) as exc:
        return _err(str(exc), 400)
    except Exception as exc:  # noqa: BLE001 — BrainError etc. surface as 502
        return _err(f"pipeline failed: {exc}", 502)
    decision = res.get("decision", {}) if isinstance(res, dict) else {}
    return 200, {
        "answer": res.get("answer", ""),
        "decision": decision,
        "run_on": res.get("run_on") or res.get("routed_via"),
        "needs_confirm": bool(res.get("needs_confirm", False)),
        "workspace": res.get("workspace"),
        "verification": res.get("verification") or [],
        "session_id": res.get("session_id"),
    }


def handle_agents_api(body: Dict[str, Any], svc) -> Tuple[int, Dict[str, Any]]:
    try:
        return _ok(svc.list_agents())
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc), 500)


def handle_health_api(body: Dict[str, Any], svc) -> Tuple[int, Dict[str, Any]]:
    try:
        return _ok(asyncio.run(svc.health()))
    except Exception as exc:  # noqa: BLE001
        return _err(f"health failed: {exc}", 502)


def handle_remember_api(body: Dict[str, Any], svc) -> Tuple[int, Dict[str, Any]]:
    text = str((body or {}).get("text", "") or "").strip()
    if not text:
        return _err("text must be non-empty", 400)
    try:
        return _ok(asyncio.run(svc.remember(text)))
    except Exception as exc:  # noqa: BLE001
        return _err(f"remember failed: {exc}", 502)


def handle_search_api(body: Dict[str, Any], svc) -> Tuple[int, Dict[str, Any]]:
    query = str((body or {}).get("query", "") or "").strip()
    if not query:
        return _err("query must be non-empty", 400)
    try:
        top_k = int((body or {}).get("top_k", 5) or 5)
    except (ValueError, TypeError):
        return _err("top_k must be an integer", 400)
    try:
        return _ok(asyncio.run(svc.search(query, top_k=top_k)))
    except Exception as exc:  # noqa: BLE001
        return _err(f"search failed: {exc}", 502)


def handle_session_new_api(body: Dict[str, Any], svc) -> Tuple[int, Dict[str, Any]]:
    try:
        return _ok(svc.session_new())
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc), 500)


def handle_catalog_api(body: Dict[str, Any], svc) -> Tuple[int, Dict[str, Any]]:
    try:
        from nak.service import get_node_catalog

        return _ok(get_node_catalog(svc.pipe.registry))
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc), 500)


def handle_approvals_list_api(body: Dict[str, Any], svc) -> Tuple[int, Dict[str, Any]]:
    """GET /api/approvals — pending tool approvals for the browser to render."""
    try:
        from nak.plugins.tool.policy import list_pending_approvals

        return _ok({"pending": list_pending_approvals()})
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc), 500)


def handle_approvals_decide_api(body: Dict[str, Any], svc) -> Tuple[int, Dict[str, Any]]:
    """POST /api/approvals {id, allow} — browser decision for a pending approval."""
    try:
        from nak.plugins.tool.policy import resolve_approval

        req_id = str((body or {}).get("id", "") or "")
        if not req_id:
            return _err("id must be non-empty", 400)
        allow = body.get("allow", False)
        allow = bool(allow) if not isinstance(allow, str) else allow.strip().lower() in ("1", "true", "yes", "y")
        if not resolve_approval(req_id, allow):
            return _err(f"unknown approval id {req_id!r}", 404)
        return _ok({"resolved": True, "id": req_id, "allow": allow})
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc), 500)


# (method, path) -> handler(body, svc). Pipelines reuse the table as-is;
# a pipeline needing custom behaviour passes routes={...} overrides to serve().
API_ROUTES: Dict[Tuple[str, str], Callable[[Dict[str, Any], Any], Tuple[int, Dict[str, Any]]]] = {
    ("POST", "/api/ask"): handle_ask_api,
    ("GET", "/api/agents"): handle_agents_api,
    ("GET", "/api/health"): handle_health_api,
    ("POST", "/api/remember"): handle_remember_api,
    ("POST", "/api/search"): handle_search_api,
    ("POST", "/api/session-new"): handle_session_new_api,
    ("GET", "/api/catalog"): handle_catalog_api,
    ("GET", "/api/approvals"): handle_approvals_list_api,
    ("POST", "/api/approvals"): handle_approvals_decide_api,
}


def dispatch(method: str, path: str, body: Dict[str, Any], svc,
             routes=None) -> Tuple[int, Dict[str, Any]]:
    """One HTTP request -> one service call. Never raises (404/500 as data)."""
    table = routes or API_ROUTES
    fn = table.get((method.upper(), path.split("?")[0]))
    if fn is None:
        return _err(f"unknown route {method.upper()} {path!r}", 404)
    try:
        return fn(body or {}, svc)
    except Exception as exc:  # noqa: BLE001
        return _err(f"handler failed: {exc}", 500)


def serve(host: str = "127.0.0.1", port: int = 8080, *, create_service=None,
          routes=None, static_dir=None) -> None:
    """Block serving the API (+ static_dir for the demo page). Stdlib only.

    create_service() builds the pipeline's service (same factory the CLI
    uses). static_dir serves index.html etc. alongside /api/*.
    """
    import functools
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path as _Path

    if create_service is None:
        raise ValueError("create_service factory is required")
    svc = create_service()
    table = routes or API_ROUTES
    root = _Path(static_dir).resolve() if static_dir else None

    class _Handler(BaseHTTPRequestHandler):
        server_version = "NebulonAK-API/1"

        def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
            raw = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(raw)

        def _read_body(self) -> Dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except (ValueError, TypeError):
                length = 0
            if length <= 0:
                return {}
            try:
                return json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            except (ValueError, UnicodeDecodeError):
                return {"_parse_error": True}

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/" and root is not None:
                self.path = "/index.html"
            if root is not None and not self.path.startswith("/api/"):
                target = (root / self.path.lstrip("/")).resolve()
                try:
                    target.relative_to(root)
                except ValueError:
                    return self._send_json(403, {"error": "path escapes static dir"})
                if target.is_file():
                    mime = "text/html" if target.suffix == ".html" else "text/plain"
                    raw = target.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", f"{mime}; charset=utf-8")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    return self.wfile.write(raw)
                return self._send_json(404, {"error": f"no such file {self.path!r}"})
            status, payload = dispatch("GET", self.path, {}, svc, table)
            return self._send_json(status, payload)

        def do_POST(self) -> None:  # noqa: N802
            body = self._read_body()
            if body.pop("_parse_error", False):
                return self._send_json(400, {"error": "invalid JSON body"})
            status, payload = dispatch("POST", self.path, body, svc, table)
            return self._send_json(status, payload)

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def log_message(self, fmt, *args) -> None:  # quieter than default
            print(f"[api] {self.address_string()} {fmt % args}")

    handler = functools.partial(_Handler)
    with ThreadingHTTPServer((host, port), handler) as httpd:
        print(f"NebulonAK API on http://{host}:{httpd.server_port}"
              + (f" (static: {root})" if root else ""))
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[api] stopped")


__all__ = [
    "API_NODE_SPEC",
    "API_ROUTES",
    "dispatch",
    "serve",
    "handle_ask_api",
    "handle_agents_api",
    "handle_health_api",
    "handle_remember_api",
    "handle_search_api",
    "handle_session_new_api",
    "handle_catalog_api",
    "handle_approvals_list_api",
    "handle_approvals_decide_api",
]
