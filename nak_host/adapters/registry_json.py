"""JSON registry adapter: RegistryPort over the existing loaders.

Delegation only — no validation or loading logic is re-implemented here:

* workers/teams -> ``nak_host.worker.engine.registry`` (list_workers/list_teams/get_team)
* crafts        -> ``nak_host.harness.craft.craft`` (validated list_crafts)
* tools         -> ``nak_host.harness.engine.registry`` (validated load_registry)
* permissions   -> ``nak_host.harness.engine.permission`` (fail-closed load_permissions)

Added value over calling the loaders directly: one seam, mtime-checked
shared cache (deep-copied on return, so callers can never pollute it), an
explicit ``refresh()``, and fail-loud shape checks for the two registries
whose loaders do not validate entry shape (workers/teams). All loader
exceptions propagate unchanged.
"""

from __future__ import annotations

import copy
import threading

from pathlib import Path
from typing import Any

from nak_host.domain.errors import ValidationError

_MANIFESTS_DIR = Path(__file__).resolve().parents[1] / "manifests"

_CACHE: dict = {}
_CACHE_LOCK = threading.Lock()


def _default_workers() -> Path:
    try:
        from nak_host.utils.constant import DEFAULT_WORKERS_JSON as default

        return Path(default).expanduser()
    except Exception:
        return _MANIFESTS_DIR / "workers.json"


def _resolve_workers(explicit: Any) -> Path:
    if str(explicit or "").strip():
        return Path(str(explicit)).expanduser()
    try:
        from nak_host.utils.settings import load_settings

        return Path(load_settings().workers_path()).expanduser()
    except Exception:
        return _default_workers()


def _resolve_tools(explicit: Any) -> Path:
    if str(explicit or "").strip():
        return Path(str(explicit)).expanduser()
    try:
        from nak_host.utils.settings import load_settings

        return Path(load_settings().registry_path()).expanduser()
    except Exception:
        return _MANIFESTS_DIR / "tools.json"


def _resolve_permissions(explicit: Any) -> Path:
    if str(explicit or "").strip():
        return Path(str(explicit)).expanduser()
    try:
        from nak_host.utils.settings import load_settings

        return Path(load_settings().permissions_path()).expanduser()
    except Exception:
        return _MANIFESTS_DIR / "permissions.json"


def _cache_key(kind: str, path: Path) -> tuple | None:
    try:
        stat = path.stat()
    except OSError:
        return None  # uncacheable: loader raises its own error (parity)
    return (kind, str(path), stat.st_mtime_ns, stat.st_size)


class JsonRegistry:
    """RegistryPort over JSON manifests (shared mtime-checked cache)."""

    def __init__(self, workers_file: Any = None, teams_file: Any = None,
                 tools_file: Any = None, permissions_file: Any = None) -> None:
        self._workers = _resolve_workers(workers_file)
        self._teams = (Path(str(teams_file)).expanduser() if str(teams_file or "").strip()
                       else self._workers.parent / "teams.json")
        self._tools = _resolve_tools(tools_file)
        self._permissions = _resolve_permissions(permissions_file)

    @classmethod
    def refresh(cls) -> None:
        """Clear the shared cache (call after mutating a manifest on disk)."""
        with _CACHE_LOCK:
            _CACHE.clear()

    def _cached(self, kind: str, path: Path, loader: Any) -> Any:
        key = _cache_key(kind, path)
        if key is None:
            return loader()
        with _CACHE_LOCK:
            if key in _CACHE:
                return copy.deepcopy(_CACHE[key])
        value = loader()
        with _CACHE_LOCK:
            _CACHE[key] = value
            return copy.deepcopy(value)

    def workers(self) -> list:
        """Worker entries (the loader validates shape + membership itself)."""
        from nak_host.worker.engine.registry import list_workers

        return self._cached("workers", self._workers,
                            lambda: list_workers(self._workers))

    def teams(self) -> list:
        """Team entries (validated roster + entry shape)."""
        from nak_host.worker.engine.registry import list_teams

        def _load() -> list:
            entries = list_teams(self._teams, self._workers)
            for entry in entries:
                if not isinstance(entry, dict) or not str(entry.get("name", "")).strip():
                    raise ValidationError(
                        f"teams registry {self._teams}: entries need a non-empty 'name'")
                if not isinstance(entry.get("members"), list):
                    raise ValidationError(
                        f"team {entry.get('name')!r}: need a 'members' list")
            return entries

        return self._cached("teams", self._teams, _load)

    def team(self, name: str) -> dict:
        """One normalized team (unknown name raises ValueError)."""
        from nak_host.worker.engine.registry import get_team

        return get_team(name, teams_file=self._teams, workers_file=self._workers)

    def crafts(self) -> list:
        """Validated crafts (ambient registry resolution, as today)."""
        from nak_host.harness.craft.craft import list_crafts

        return list_crafts()

    def tools(self) -> list:
        """Tool catalog entries (validated name/module/function)."""
        from nak_host.harness.engine.registry import load_registry

        return self._cached("tools", self._tools,
                            lambda: load_registry(self._tools))

    def permissions(self) -> dict:
        """Worker<->harness boundary (fail-closed; never raises)."""
        from nak_host.harness.engine.permission import load_permissions

        return self._cached("permissions", self._permissions,
                            lambda: load_permissions(str(self._permissions)))
