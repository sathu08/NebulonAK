"""nak.plugins.tool.workspace -- per-session isolated workspaces (Phase 2).

Every harness task gets its own directory so agent file/shell work never
pollutes the repo root (old behaviour wrote nak_plan.md / tokyo.md at root):

    workspace/
        session_<id>/
            project/     agent file work goes here by default
            logs/        tool / verifier logs
            artifacts/   plans, patches, generated reports
            state.json   serialised HarnessState (written by runtime)

Resolution:
    - root defaults to <repo>/workspace (env NAK_WORKSPACE wins)
    - session dirs are sanitised (alnum + dash/underscore only)
    - resolve_in_workspace() sandboxes paths like file_tool._resolve
"""

from __future__ import annotations

import re
from pathlib import Path
from contextvars import ContextVar
from typing import Iterator, Optional

from contextlib import contextmanager

_SESSION_SAFE = re.compile(r"[^A-Za-z0-9_-]+")

# Ambient file sandbox for the current task. AgentExecutor sets this to the
# task's workspace project dir while an agent runs, so agent-internal tool
# calls (which go straight to execute_plugin without explicit _root/_cwd)
# stay isolated. Async-safe (contextvars); None = legacy repo-root behaviour.
_ambient_root: ContextVar[Optional[str]] = ContextVar("nak_workspace_root", default=None)

# Ambient harness turn state (Phase 6). HarnessRuntime.handle() sets this so
# stateful tools (create_plan / update_plan) can reach the current
# HarnessState without changing the LLM-visible schemas. None outside a turn.
# Stored as opaque object to avoid a plugins -> harness import cycle.
_ambient_state: ContextVar[Optional[object]] = ContextVar("nak_harness_state", default=None)


@contextmanager
def set_current_workspace(path: Optional[str | Path]) -> Iterator[None]:
    """Scope ambient file sandbox to `path` (reset on exit)."""
    token = _ambient_root.set(str(path) if path else None)
    try:
        yield
    finally:
        _ambient_root.reset(token)


def current_workspace_root() -> Optional[Path]:
    """Ambient sandbox dir, or None when no harness task is active."""
    val = _ambient_root.get()
    return Path(val).expanduser().resolve() if val else None


def set_current_harness_state(state: Optional[object]) -> None:
    """Point plan tools at the active turn's HarnessState (or clear)."""
    _ambient_state.set(state)


def current_harness_state() -> Optional[object]:
    """Active turn's HarnessState, or None outside a harness turn."""
    return _ambient_state.get()


def _repo_root() -> Path:
    # nak/plugins/tool/workspace.py -> tool -> plugins -> nak -> NebulonAK
    return Path(__file__).resolve().parents[3]


def get_workspace_root(root: Optional[str | Path] = None) -> Path:
    """Base workspace dir. Explicit arg wins, else config (env NAK_WORKSPACE,
    then cfg [harness] workspace_dir), else <repo>/workspace."""
    if root is not None:
        return Path(root).expanduser().resolve()
    try:
        from nak.utils.config import get_workspace_dir

        configured = get_workspace_dir()
    except Exception:
        configured = ""
    if configured:
        return Path(configured).expanduser().resolve()
    return _repo_root() / "workspace"


def sanitise_session_id(session_id: str) -> str:
    """Make a filesystem-safe session dirname fragment."""
    cleaned = _SESSION_SAFE.sub("_", (session_id or "default").strip())
    cleaned = cleaned.strip("_") or "default"
    return cleaned[:64]


def ensure_session_workspace(
    session_id: Optional[str],
    root: Optional[str | Path] = None,
) -> Path:
    """Create (if needed) and return workspace/session_<id>/project/."""
    base = get_workspace_root(root)
    sid = sanitise_session_id(session_id or "default")
    project = base / f"session_{sid}" / "project"
    logs = base / f"session_{sid}" / "logs"
    artifacts = base / f"session_{sid}" / "artifacts"
    for d in (project, logs, artifacts):
        d.mkdir(parents=True, exist_ok=True)
    return project


def session_dir(
    session_id: Optional[str],
    root: Optional[str | Path] = None,
) -> Path:
    """Return workspace/session_<id>/ (created on demand)."""
    project = ensure_session_workspace(session_id, root)
    return project.parent


def resolve_in_workspace(
    path: str,
    workspace: str | Path,
) -> Path:
    """Resolve `path` under `workspace`, rejecting `..` escapes."""
    base = Path(workspace).expanduser().resolve()
    p = Path(path).expanduser()
    target = p.resolve() if p.is_absolute() else (base / p).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        raise ValueError(f"path escapes workspace {base}: {path!r}")
    return target


__all__ = [
    "get_workspace_root",
    "ensure_session_workspace",
    "session_dir",
    "resolve_in_workspace",
    "sanitise_session_id",
    "set_current_workspace",
    "current_workspace_root",
    "set_current_harness_state",
    "current_harness_state",
]
