"""ExecutionHarness — runs tool calls suggested by Brain/harness/policy.

Orchestrator only. One focused module per concern (same folder):
  Permission (permission.py): verdicts, load, deny, rule_for.
  Approval (approval.py): ask-verdict -> approver or deny+need_approval.
  Capability (capability.py): tool-spec caps -> dotted permission caps.
  Sandbox (sandbox.py): path safety + Session (create_session_dir,
    resource_bases, check_resources). No separate session.py by design.
  Limits (limits.py): session call/time/size checks.
  Registry (registry.py): tools.json catalog load + validate.
  Tool Execution (execution.py): lazy resolve + run/suggest_and_run.

The harness NEVER imports a tool module directly and NEVER calls any
LLM itself — tool locations come from nak_host/manifests/tools.json and
functions load lazily via importlib only at run time (execution.py).

Sandbox: every run is scoped to a session dir
  <workspace>/<user>_<UTC timestamp>__   (e.g. sathya_20261001T143022__)
created at init (user via NAK_USER or arg) and rotated with
create_session(). File paths must resolve inside the session root;
.. / absolute / symlink escapes and .env, *.key/*.pem, secrets/ +
.git/ are denied even inside root (shared rules in sandbox.py).

Permissions (nak_host/manifests/permissions.json) — worker <-> harness
boundary, fail-closed. Enforced in inspect() BEFORE any run, in order:
worker entry exists+enabled (unbound -> "default" entry;
named-but-unlisted -> deny) -> tool known -> tool verdict allow
(ask -> approver, else deny + need_approval) -> each required
capability allow/ask (a registry tool/capability missing from an entry
stays deny; see audit_tools() for the gaps) -> file resources globs
(deny wins, empty grant denies) -> session limits (calls/time/size).
Must ALSO pass any per-call allow=[...] list (intersection). Bind the
agent via ExecutionHarness(worker="vizhi") or .bind(name).
Missing/unreadable file = deny-all. Session counters reset in
create_session().

Flow (inspect-first):
  harness = ExecutionHarness(user="sathya")
  harness.inspect("file", {"action": "read", "path": "notes.md"})
  harness.run("file", {"action": "read", "path": "notes.md"})
  harness.suggest_and_run({"tool": "file", "arguments": {"action": "read", ...}})

Every run returns a JSON STRING: '{"ok": true, ...}' or
'{"ok": false, "error": ...}'. Never raises on tool failure.

Backward compat: ToolEngine = ExecutionHarness (old name still works).
"""

from __future__ import annotations

import os

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .approval import ask as _ask_fn
from .capability import required_caps as _required_caps_fn
from .execution import resolve_tool as _resolve_tool_fn
from .execution import run_tool as _run_tool_fn
from .execution import suggest_and_run as _suggest_and_run_fn
from .limits import check_limits as _check_limits_fn
from .permission import assert_worker_known as _assert_worker_known_fn
from .permission import deny as _deny_fn
from .permission import load_permissions as _load_permissions
from .permission import load_worker_role as _load_worker_role_fn
from .permission import rule_for as _rule_for_fn
from .registry import load_registry as _load_registry
from .registry import DEFAULT_REGISTRY as _DEFAULT_REGISTRY
from .sandbox import check_resources as _check_resources_fn
from .sandbox import create_session_dir, record_session_history
from .sandbox import default_workspace, safe_join, sanitize_user
from .sandbox import resource_bases as _resource_bases_fn


class ExecutionHarness:
    """Registry runner with session sandbox. Tools resolve from tools.json.

    Worker -> role: pass worker="vizhi" at start; the harness maps the
    name through workers.json to identity.role (e.g. project_manager)
    and enforces the merged entry (roles[role] base + workers[name]
    overlay, worker wins). Every start/rotation appends a record to
    <workspace>/workspace_history.json (worker, role, owner, time).
    """

    def __init__(self, registry: Optional[str | Path] = None,
                 extra_tools: Optional[Dict[str, Callable[[Dict[str, Any]], str]]] = None,
                 workspace_root: Optional[str | Path] = None,
                 user: Optional[str] = None,
                 permissions: Any = None,
                 worker: Optional[str] = None,
                 approver: Optional[Callable[..., bool]] = None,
                 workers_file: Optional[str | Path] = None,
                 ml: Any = None,
                 mcp: Any = None) -> None:
        self.registry_path = str(Path(registry).expanduser() if registry else _DEFAULT_REGISTRY)
        self._specs: Dict[str, Dict[str, Any]] = {e["name"]: e for e in _load_registry(registry)}
        self.permissions: Dict[str, Any] = _load_permissions(permissions)
        self.workers_file: str = str(workers_file).strip() if str(workers_file or "").strip() else ""
        _w = str(worker).strip() if str(worker or "").strip() else ""
        if _w:
            # Fail loud on typos: the worker must exist in workers.json.
            _assert_worker_known_fn(self.workers_file or None, _w)
        self.worker: Optional[str] = _w or None
        self.worker_role: Optional[str] = _load_worker_role_fn(
            self.workers_file or None, self.worker)
        self._approver = approver
        self._calls = 0
        self._exec_seconds = 0.0
        # Placeholders for future packages (stored, never executed here):
        # nak_host/harness/ml/ and nak_host/harness/mcp/ are empty reserves.
        self.ml = ml
        self.mcp = mcp
        self._overrides: Dict[str, Callable[[Dict[str, Any]], str]] = {}
        self._cache: Dict[str, Callable[[Dict[str, Any]], str]] = {}
        self.workspace_root = str(
            Path(workspace_root).expanduser() if workspace_root
            else Path(os.environ.get("NAK_WORKSPACE", "").strip() or default_workspace())
        )
        Path(self.workspace_root).mkdir(parents=True, exist_ok=True)
        self.user = sanitize_user(user or os.environ.get("NAK_USER", "").strip() or "user")
        for name, fn in (extra_tools or {}).items():
            self.register(name, fn)
        self.session_id, self.session_dir = self.create_session(self.user, event="started")

    def create_session(self, user: Optional[str] = None, event: str = "rotated",
                       assigned_workers: Optional[List[str]] = None) -> tuple:
        """New session dir <workspace>/<user>_<UTC timestamp>__. Returns (id, dir).

        Records the session (worker, role, owner, time, assigned_workers) to
        <workspace>/workspace_history.json (best-effort, never raises).
        assigned_workers: sub-workers Vizhi fanned out to (parallel work);
        empty/None when none — reserved for future simultaneous workers.
        """
        from datetime import datetime, timezone
        who = sanitize_user(user or self.user)
        self.user = who
        self.session_id, self.session_dir = create_session_dir(self.workspace_root, who)
        self._calls = 0
        self._exec_seconds = 0.0
        record_session_history(self.workspace_root, {
            "time": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "session_id": self.session_id,
            "worker": self.worker,
            "role": self.worker_role,
            "owner": self.user,
            "assigned_workers": list(assigned_workers or []),
        })
        return self.session_id, self.session_dir

    def prune_sessions(self, keep: int = 20) -> Dict[str, Any]:
        """Delete oldest session dirs beyond `keep` newest. Explicit-only.

        Never runs automatically (a live session must not be collected).
        See sandbox.prune_sessions(). Returns {"pruned": [...], "kept": N}.
        """
        from .sandbox import prune_sessions as _prune_fn

        return _prune_fn(self.workspace_root, keep=keep)

    from nak_host.utils.constant import PLAN_FILENAME as PLAN_FILENAME

    def start_plan(self, goal: str, steps: List[str],
                   assigned_workers: Optional[List[str]] = None,
                   needs_approval: bool = True,
                   decision_evidence: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Write the session plan file (<session>/nak_plan.md). Returns info dict.

        Called when a worker starts to plan: persists goal + numbered steps
        (checkboxes) + assigned workers + approval status, and records a
        'planned' history event (best-effort). Overwrites any earlier plan
        in this session (one current plan per session).
        decision_evidence: optional craft-decision audit block (the merged
        per-step routing + failures); rendered as a "## Decision" section.
        status: 'pending-approval' when needs_approval else 'auto-approved'.
        Raises ValueError on empty goal/steps or sandbox refusal.
        """
        from datetime import datetime, timezone
        g = str(goal or "").strip()
        if not g:
            raise ValueError("plan goal must be non-empty")
        clean = [str(s).strip() for s in (steps or []) if str(s).strip()]
        if not clean:
            raise ValueError("plan needs at least one step")
        assigned = [str(w).strip() for w in (assigned_workers or []) if str(w).strip()]
        status = "pending-approval" if needs_approval else "auto-approved"
        now = datetime.now(timezone.utc).isoformat()
        lines = [f"# Plan — {self.session_id}", "",
                 f"- goal: {g}",
                 f"- worker: {self.worker or 'unbound'} (role: {self.worker_role or '-'})",
                 f"- created: {now}",
                 f"- status: {status}",
                 f"- steps: {len(clean)}", "",
                 "## Steps", ""]
        lines += [f"- [ ] {i}. {s}" for i, s in enumerate(clean, 1)]
        lines += ["", "## Assigned",
                  ("- " + ", ".join(assigned)) if assigned else "- (none)", ""]
        if isinstance(decision_evidence, dict):
            meta = " ".join(str(decision_evidence.get(k, "")).strip()
                            for k in ("scope", "mode", "status")
                            if str(decision_evidence.get(k, "")).strip())
            lines += ["## Decision", f"- {meta or 'enabled'}"]
            items = decision_evidence.get("items")
            for item in (items if isinstance(items, list) else []):
                if not isinstance(item, dict):
                    continue
                who = str(item.get("worker", "") or "(unassigned)").strip()
                why = str(item.get("reason", "") or "").strip()
                why = " ".join(why.split())[:220]
                lines.append(
                    f"- step {item.get('step', '?')}: {who} "
                    f"({item.get('source', 'decision')})"
                    + (f" — {why}" if why else ""))
            lines.append("")
        lines += ["## Approvals", ""]
        if needs_approval:
            lines.append(f"- {now} waiting for approval")
        else:
            lines.append(f"- {now} auto-approved (small plan, no approval asked)")
        ok, resolved = safe_join(self.session_dir, self.PLAN_FILENAME)
        if not ok:
            raise ValueError(resolved)
        Path(resolved).write_text("\n".join(lines) + "\n", encoding="utf-8")
        record_session_history(self.workspace_root, {
            "time": now,
            "event": "planned",
            "session_id": self.session_id,
            "worker": self.worker,
            "role": self.worker_role,
            "owner": self.user,
            "assigned_workers": assigned,
        })
        return {"path": str(resolved), "status": status,
                "steps": len(clean), "assigned_workers": assigned}

    def approve_plan(self, approved: bool = True, by: str = "user",
                     note: str = "", path: str = "") -> Dict[str, Any]:
        """Flip the session plan file to approved/rejected. Returns info dict.

        Appends an approval line; raises ValueError when no plan file
        exists yet (call start_plan first) or the path is refused.
        """
        import re

        from datetime import datetime, timezone
        ok, resolved = safe_join(self.session_dir, str(path or "").strip() or self.PLAN_FILENAME)
        if not ok:
            raise ValueError(resolved)
        p = Path(resolved)
        if not p.is_file():
            raise ValueError(f"no plan file yet: {p} (call start_plan first)")
        text = p.read_text(encoding="utf-8")
        status = "approved" if approved else "rejected"
        now = datetime.now(timezone.utc).isoformat()
        who = str(by or "user").strip() or "user"
        text = re.sub(r"(?m)^- status: .*$", f"- status: {status}", text, count=1)
        if "- status:" not in text:
            text = text.rstrip("\n") + f"\n- status: {status}\n"
        line = f"- {now} {status} by {who}"
        if str(note or "").strip():
            line += f": {str(note).strip()[:200]}"
        p.write_text(text.rstrip("\n") + "\n" + line + "\n", encoding="utf-8")
        return {"path": str(p), "status": status}

    def register(self, name: str, fn: Callable[[Dict[str, Any]], str]) -> None:
        """Add/replace one in-memory tool (wins over the JSON registry). fn(args) -> JSON string."""
        if not str(name or "").strip():
            raise ValueError("tool name must be non-empty")
        if not callable(fn):
            raise ValueError("tool fn must be callable")
        self._overrides[str(name).strip()] = fn
        self._cache.pop(str(name).strip(), None)

    def bind(self, worker: Optional[str]) -> "ExecutionHarness":
        """Bind this harness to one worker (agent). Re-resolves identity.role.

        What bind is: (re)attach the harness to a named worker on the SAME
        instance (chainable: `h.bind("vizhi").run(...)`). It only swaps
        which worker's permissions apply (+ its role) — no new session,
        no history record. Pass None/"" to unbind (falls back to the
        "default" entry). Unknown names raise ValueError (checked against
        workers.json); per-call `inspect(worker=...)` overrides stay
        fail-closed deny instead of raising.
        """
        _w = str(worker).strip() if str(worker or "").strip() else ""
        if _w:
            _assert_worker_known_fn(self.workers_file or None, _w)
        self.worker = _w or None
        self.worker_role = _load_worker_role_fn(self.workers_file or None, self.worker)
        return self

    def _role_for(self, worker: Optional[str] = None) -> Optional[str]:
        """identity.role for a worker name (bound worker when None)."""
        name = str(worker if worker is not None else self.worker or "").strip()
        if not name:
            return None
        if worker is None or name == (self.worker or ""):
            return self.worker_role
        return _load_worker_role_fn(self.workers_file or None, name)

    def _rule_for(self, worker: Optional[str] = None) -> tuple:
        """Effective entry: roles[role] base + workers[name] overlay. See permission.rule_for."""
        bound = self.worker
        if worker is not None:
            name = str(worker).strip()
            role = self._role_for(name if name else None)
            return _rule_for_fn(self.permissions, worker, bound, role)
        return _rule_for_fn(self.permissions, None, bound, self.worker_role)

    @staticmethod
    def _deny(tool: str, worker_label: str, reason: str,
              need_approval: bool = False) -> Dict[str, Any]:
        """See permission.deny (kept as a thin wrapper for backward compat)."""
        return _deny_fn(tool, worker_label, reason, need_approval=need_approval)

    @staticmethod
    def _required_caps(spec: Dict[str, Any]) -> List[str]:
        """See capability.required_caps (kept as a thin wrapper)."""
        return _required_caps_fn(spec)

    @staticmethod
    def _action_caps(spec: Dict[str, Any], given: Dict[str, Any]) -> Any:
        """Dotted caps for THIS call: spec action_capabilities[action].

        Returns the cap list, False when the action is unknown (deny),
        or None when the spec has no per-action map (fall back to
        _required_caps). Single multi-action tools (e.g. "file") declare
        e.g. {"read": ["file.read"], "write": ["file.write"], ...} so
        each action is gated by only its own capability.
        """
        amap = (spec or {}).get("action_capabilities")
        if not isinstance(amap, dict):
            return None
        action = str((given or {}).get("action", "")).strip().lower()
        caps = amap.get(action)
        if not isinstance(caps, list):
            return False
        return [str(c).strip() for c in caps if str(c).strip()]

    def _ask(self, tool: str, given: Dict[str, Any], worker_label: str,
             what: str, approver: Optional[Callable[..., bool]] = None) -> Optional[Dict[str, Any]]:
        """See approval.ask (kept as a thin wrapper)."""
        return _ask_fn(tool, given, worker_label, what,
                       approver=approver, fallback_approver=self._approver)

    def _resource_bases(self) -> List[Path]:
        """See sandbox.resource_bases (session + workspace + project root)."""
        return _resource_bases_fn(self.session_dir, self.workspace_root, __file__)

    def _check_resources(self, resolved: str, op: str,
                         entry: Dict[str, Any]) -> Optional[str]:
        """See sandbox.check_resources (kept as a thin wrapper)."""
        return _check_resources_fn(resolved, op, entry, self._resource_bases())

    def _check_limits(self, given: Dict[str, Any], resolved: Optional[str],
                      entry: Dict[str, Any]) -> Optional[str]:
        """See limits.check_limits (kept as a thin wrapper)."""
        return _check_limits_fn(given, resolved, entry, self._calls, self._exec_seconds)

    def list(self) -> List[str]:
        """Sorted tool names: JSON registry + in-memory overrides."""
        return sorted(set(self._specs) | set(self._overrides))

    def _effective_root(self, args: Dict[str, Any]) -> str:
        given_root = str((args or {}).get("root", "")).strip()
        return given_root or self.session_dir

    def inspect(self, name: str, args: Optional[Dict[str, Any]] = None,
                allow: Optional[List[str]] = None,
                worker: Optional[str] = None,
                approver: Optional[Callable[..., bool]] = None) -> Dict[str, Any]:
        """Check a tool BEFORE running: boundary, spec, sandbox, args, deps.

        Boundary (worker<->harness, fail-closed): worker entry must exist
        (unbound -> "default"; named-but-unlisted -> deny) and be enabled;
        the tool needs an allow verdict (ask -> approver, else deny +
        need_approval); the action's own capabilities (spec
        action_capabilities -> permission capabilities) each need allow
        (ask -> approver) on top; file paths must pass resources globs;
        session limits must hold. Must ALSO pass any per-call allow=[...]
        list. Returns a plan dict (no execution) or {"ok": False, ...}.
        """
        tool = str(name or "").strip()
        if not tool:
            return {"ok": False, "error": "missing tool name"}
        if allow is not None and tool not in [str(x) for x in allow]:
            return {"ok": False, "error": f"tool {tool!r} not allowed"}
        given = dict(args or {})
        # --- boundary: worker entry ---
        entry, who = self._rule_for(worker)
        if entry is None:
            named = str(worker if worker is not None else self.worker or "").strip()
            if named:
                return self._deny(tool, named, f"unknown worker {named!r}")
            return self._deny(tool, "default", "no default permission entry")
        if not entry.get("enabled", True):
            return self._deny(tool, who, f"worker {who!r} disabled")
        # --- boundary: known tool? ---
        known = tool in self._overrides or tool in self._specs
        if not known:
            return self._deny(tool, who, f"unknown tool {tool!r}. Have: {self.list()}")
        # --- boundary: tool verdict (tool name must be granted; the
        # action's own capabilities gate on top of it) ---
        spec = self._specs.get(tool) or {}
        verdict = (entry.get("tools") or {}).get(tool)
        if verdict is None:
            return self._deny(tool, who, f"tool {tool!r} not granted for worker {who!r}")
        if verdict == "deny":
            return self._deny(tool, who, f"tool {tool!r} denied for worker {who!r}")
        if verdict == "ask":
            pending = self._ask(tool, given, who, f"tool {tool!r}", approver)
            if pending:
                return pending
        # --- boundary: capability verdicts (per-action when mapped) ---
        need = self._action_caps(spec, given)
        if need is False:
            return self._deny(tool, who,
                              f"bad action {str(given.get('action', ''))!r} for tool {tool!r}")
        need = need if isinstance(need, list) else self._required_caps(spec)
        for cap in need:
            cv = (entry.get("capabilities") or {}).get(cap)
            if cv is None:
                return self._deny(tool, who, f"capability {cap!r} not granted for worker {who!r}")
            if cv == "deny":
                return self._deny(tool, who, f"capability {cap!r} denied for worker {who!r}")
            if cv == "ask":
                pending = self._ask(tool, given, who, f"capability {cap!r}", approver)
                if pending:
                    return pending
        if tool in self._overrides and tool not in self._specs:
            sandbox = {"root": self._effective_root(given),
                       "session": self.session_id}
            resolved = None
            if "path" in given and str(given.get("path", "")).strip():
                ok, resolved = safe_join(sandbox["root"], str(given["path"]))
                sandbox["resolved"] = resolved
                if not ok:
                    return {"ok": False, "error": resolved, "tool": tool,
                            "description": "caller-provided function",
                            "sandbox": sandbox}
                hit = self._check_resources(resolved, "", entry)
                if hit:
                    return self._deny(tool, who, hit)
            over = self._check_limits(given, resolved, entry)
            if over:
                return self._deny(tool, who, over)
            return {"ok": True, "tool": tool, "source": "in-memory override",
                    "description": "caller-provided function",
                    "dependencies": [], "dependencies_ok": True,
                    "capabilities": [], "args_expected": {},
                    "args_given": sorted(given),
                    "worker": who,
                    "sandbox": sandbox}
        expected = spec.get("args") or {}
        missing = [k for k, v in expected.items()
                   if "required" in str(v).lower()
                   and not str(given.get(k, "")).strip()]
        if missing:
            return {"ok": False, "error": f"tool {tool!r} missing required args: {missing}",
                    "tool": tool, "description": spec.get("description", ""),
                    "args_expected": expected, "args_given": sorted(given),
                    "sandbox": {"root": self._effective_root(given),
                                "session": self.session_id}}
        missing_deps = []
        for dep in spec.get("dependencies") or []:
            try:
                import importlib as _importlib
                _importlib.import_module(str(dep))
            except ImportError:
                missing_deps.append(str(dep))
        if missing_deps:
            return {"ok": False, "error": f"tool {tool!r} missing dependencies: {missing_deps}",
                    "tool": tool, "description": spec.get("description", ""),
                    "dependencies": spec.get("dependencies", [])}
        # Sandbox pre-check for path-taking tools (same rules as the tool).
        sandbox: Dict[str, Any] = {"root": self._effective_root(given),
                                   "session": self.session_id,
                                   "session_dir": self.session_dir}
        if "path" in given and str(given.get("path", "")).strip():
            ok, resolved = safe_join(sandbox["root"], str(given["path"]))
            sandbox["resolved"] = resolved
            if not ok:
                return {"ok": False, "error": resolved, "tool": tool,
                        "description": spec.get("description", ""), "sandbox": sandbox}
        # --- boundary: resources + limits ---
        resolved = sandbox.get("resolved")
        if resolved:
            if any(c.endswith(".write") for c in need):
                op = "write"
            elif any(c.endswith((".read", ".list")) for c in need):
                op = "read"
            else:
                op = ""
            hit = self._check_resources(resolved, op, entry)
            if hit:
                return self._deny(tool, who, hit)
        over = self._check_limits(given, resolved, entry)
        if over:
            return self._deny(tool, who, over)
        return {"ok": True, "tool": tool,
                "description": spec.get("description", ""),
                "module": spec.get("module"), "function": spec.get("function"),
                "version": spec.get("version", ""),
                "dependencies": spec.get("dependencies", []),
                "dependencies_ok": True,
                "capabilities": spec.get("capabilities", []),
                "worker": who,
                "sandbox": {**sandbox, **({"deny": (spec.get("sandbox") or {})} if spec.get("sandbox") else {})},
                "args_expected": expected, "args_given": sorted(given)}

    def _resolve(self, tool: str) -> Callable[[Dict[str, Any]], str]:
        """See execution.resolve_tool (lazy importlib, cached)."""
        return _resolve_tool_fn(tool, self._overrides, self._cache, self._specs)

    def run(self, name: str, args: Optional[Dict[str, Any]] = None,
            allow: Optional[List[str]] = None,
            worker: Optional[str] = None,
            approver: Optional[Callable[..., bool]] = None) -> str:
        """Take tool variable → boundary+spec+sandbox inspect → run accordingly."""
        counters = {"calls": self._calls, "exec_seconds": self._exec_seconds}
        out = _run_tool_fn(name, args, self._specs, self._overrides, self._cache,
                           self.session_dir, self.inspect, counters,
                           allow=allow, worker=worker, approver=approver)
        self._calls = int(counters.get("calls", self._calls))
        self._exec_seconds = float(counters.get("exec_seconds", self._exec_seconds))
        return out

    def suggest_and_run(self, suggestion: Dict[str, Any],
                        allow: Optional[List[str]] = None,
                        worker: Optional[str] = None,
                        approver: Optional[Callable[..., bool]] = None) -> str:
        """Run a Brain-shaped suggestion: {"tool": ..., "arguments": {...}}."""
        counters = {"calls": self._calls, "exec_seconds": self._exec_seconds}
        out = _suggest_and_run_fn(suggestion, self._specs, self._overrides, self._cache,
                                  self.session_dir, self.inspect, counters,
                                  allow=allow, worker=worker, approver=approver)
        self._calls = int(counters.get("calls", self._calls))
        self._exec_seconds = float(counters.get("exec_seconds", self._exec_seconds))
        return out

    def __repr__(self) -> str:
        return (f"ExecutionHarness(tools={self.list()}, session={self.session_id!r}, "
                f"worker={self.worker!r}, role={self.worker_role!r})")


# Backward compat: old class name still works everywhere.
ToolEngine = ExecutionHarness
