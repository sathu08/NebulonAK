"""
nak.utils.agent_registry -- JSON leader + Memory index for agent discovery.

Hybrid store (approved plan):
- JSON (nak/agents/registry.json) is source of truth: git-tracked, cold-start safe,
  fast scan via Path.glob, used to build Polaris's available_agents prompt.
- Memory (Brain.remember/search) is semantic replica + audit: on registry change,
  Genesis mirrors to NebulonMind for semantic fallback and history.

This module owns the JSON side; the Memory side is orchestrated by Genesis
(calls Brain.remember). Polaris optionally falls back to Brain.search when
confidence is low (not implemented here — see Polaris for that fallback).

Usage:
    from nak.utils.agent_registry import AgentRegistry
    reg = AgentRegistry()
    reg.list_agents()         # -> List[AgentMeta]
    reg.get("ExcelAgent")     # -> AgentMeta | None
    reg.register(AgentMeta(name="ExcelAgent", description="...", ...))
    reg.sync_from_disk()      # rescans nak/agents/*/ for drift
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import List, Optional, Dict, Any


def _repo_root() -> Path:
    # nak/utils/agent_registry.py -> nak/utils -> nak -> NebulonAK
    return Path(__file__).resolve().parents[2]


def _agents_root() -> Path:
    return _repo_root() / "nak" / "agents"


def _registry_path() -> Path:
    return _agents_root() / "registry.json"


_SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")
_VALID_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")


def slugify(name: str) -> str:
    """AgentName -> slug (alphanumeric, capitalized)."""
    name = (name or "").strip()
    if not name:
        return ""
    # keep alphanumeric boundaries, capitalize first letter
    parts = _SLUG_RE.split(name)
    parts = [p for p in parts if p]
    if not parts:
        return ""
    # Preserve CamelCase if single token looks like CamelCase, else Title
    slug = "".join(p[:1].upper() + p[1:] for p in parts)
    # ensure valid Python-ish identifier start
    if not _VALID_NAME_RE.match(slug):
        # fallback: strip invalid leading
        slug = re.sub(r"^[^A-Za-z]+", "", slug)
    return slug


def validate_agent_name(name: str) -> str:
    """Raises ValueError if name is not a valid agent name."""
    n = (name or "").strip()
    if not n:
        raise ValueError("agent name must be non-empty")
    if "/" in n or "\\" in n or ".." in n:
        raise ValueError(f"invalid agent name {n!r}: contains path separator")
    slug = slugify(n)
    if not slug:
        raise ValueError(f"invalid agent name {n!r}: cannot slugify")
    if not _VALID_NAME_RE.match(slug):
        raise ValueError(f"invalid agent name {n!r}: slug {slug!r} invalid")
    return slug


@dataclass
class AgentMeta:
    """Metadata for one agent in the registry (JSON leader)."""

    name: str  # canonical CamelCase name e.g. ExcelAgent
    description: str = ""
    path: str = ""  # relative to repo root e.g. nak/agents/ExcelAgent
    capabilities: List[str] = field(default_factory=list)
    created_via: str = "NAK"  # provenance tag (NAK-built agents)
    version: str = "0.1.0"

    @property
    def slug(self) -> str:
        return slugify(self.name)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AgentMeta":
        return cls(
            name=str(d.get("name", "")).strip(),
            description=str(d.get("description", "") or ""),
            path=str(d.get("path", "") or ""),
            capabilities=list(d.get("capabilities") or []),
            created_via=str(d.get("created_via", "NAK") or "NAK"),
            version=str(d.get("version", "0.1.0") or "0.1.0"),
        )


class AgentRegistry:
    """JSON leader registry for nak/agents/*.

    File: nak/agents/registry.json  (list of AgentMeta dicts)
    Disk scan: nak/agents/*/ (__init__.py or agent.py presence)
    """

    def __init__(
        self,
        registry_path: Optional[Path] = None,
        agents_root: Optional[Path] = None,
    ) -> None:
        self.registry_path = Path(registry_path) if registry_path else _registry_path()
        self.agents_root = Path(agents_root) if agents_root else _agents_root()

    # -- file IO -------------------------------------------------------------

    def _load_raw(self) -> List[Dict[str, Any]]:
        if not self.registry_path.exists():
            return []
        try:
            data = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        if isinstance(data, dict) and "agents" in data:
            data = data["agents"]
        if not isinstance(data, list):
            return []
        return [d for d in data if isinstance(d, dict)]

    def _save_raw(self, items: List[Dict[str, Any]]) -> None:
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        # stable sort by name for git diff friendliness
        items = sorted(items, key=lambda d: str(d.get("name", "")).lower())
        self.registry_path.write_text(
            json.dumps(items, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    # -- public --------------------------------------------------------------

    def list_agents(self) -> List[AgentMeta]:
        """Return agents from registry.json (source of truth)."""
        raw = self._load_raw()
        out: List[AgentMeta] = []
        for d in raw:
            try:
                m = AgentMeta.from_dict(d)
                if m.name:
                    # normalize slug / validate
                    slug = slugify(m.name)
                    if slug:
                        m.name = slug
                        out.append(m)
            except Exception:
                continue
        return out

    def get(self, name: str) -> Optional[AgentMeta]:
        slug = slugify(name)
        for m in self.list_agents():
            if m.name == slug or m.slug == slug or m.name.lower() == slug.lower():
                return m
        return None

    def exists(self, name: str) -> bool:
        return self.get(name) is not None

    def register(self, meta: AgentMeta) -> AgentMeta:
        """Idempotent register/update. Persists to registry.json. Returns normalized meta."""
        slug = validate_agent_name(meta.name)
        meta.name = slug
        if not meta.path:
            meta.path = f"nak/agents/{slug}"
        items = self._load_raw()
        # upsert by name (case-insensitive slug)
        found = False
        for i, d in enumerate(items):
            if slugify(str(d.get("name", ""))).lower() == slug.lower():
                # merge: keep existing version bump? just overwrite
                items[i] = meta.to_dict()
                found = True
                break
        if not found:
            items.append(meta.to_dict())
        self._save_raw(items)
        return meta

    def remove(self, name: str) -> bool:
        slug = slugify(name)
        items = self._load_raw()
        new_items = [d for d in items if slugify(str(d.get("name", ""))).lower() != slug.lower()]
        if len(new_items) == len(items):
            return False
        self._save_raw(new_items)
        return True

    def available_agents_str(self) -> str:
        """One agent per line — 'Name: description (+ capabilities)'.

        Rendered verbatim into the decision prompt's agent list, so the LLM
        judges fit from full descriptions. One-per-line (not ;-separated)
        keeps each agent's scope visually distinct for accurate routing.
        """
        agents = self.list_agents()
        if not agents:
            return "(no agents registered yet)"
        lines = []
        for m in agents:
            desc = (m.description or "").strip()
            caps = ", ".join(m.capabilities) if m.capabilities else ""
            suffix = f" — {desc}" if desc else ""
            if caps and not desc:
                suffix = f" — capabilities: {caps}"
            elif caps and desc:
                suffix += f" (capabilities: {caps})"
            lines.append(f"- {m.name}{suffix}")
        return "\n".join(lines)

    def available_agents_names(self) -> List[str]:
        return [m.name for m in self.list_agents()]

    # -- disk scan -----------------------------------------------------------

    def scan_disk(self) -> List[AgentMeta]:
        """Scan nak/agents/*/ for folders containing __init__.py or agent.py. Does not read registry.json."""
        if not self.agents_root.exists():
            return []
        out: List[AgentMeta] = []
        for child in self.agents_root.iterdir():
            if not child.is_dir():
                continue
            if child.name.startswith(".") or child.name.startswith("_"):
                continue
            # internal folders that are not agents
            if child.name in {"__pycache__", "generated"}:
                continue
            has_init = (child / "__init__.py").exists()
            has_agent = (child / "agent.py").exists() or (child / "base.py").exists()
            if not (has_init or has_agent):
                continue
            # derive description from __init__.py docstring first line if possible
            desc = ""
            try:
                text = (child / "__init__.py").read_text(encoding="utf-8", errors="ignore")[:2000]
                # naive first docstring line
                if '"""' in text:
                    doc = text.split('"""')[1].strip().splitlines()[0][:120]
                    desc = doc.strip()
            except Exception:
                pass
            out.append(
                AgentMeta(
                    name=slugify(child.name),
                    description=desc,
                    path=f"nak/agents/{child.name}",
                    capabilities=[],
                    created_via="NAK",
                )
            )
        return out

    def sync_from_disk(self, overwrite: bool = False) -> List[AgentMeta]:
        """Merge disk scan into registry.json. If overwrite=True, replaces matching entries' description/path."""
        disk = {m.name.lower(): m for m in self.scan_disk()}
        current = {m.name.lower(): m for m in self.list_agents()}
        # merge disk not yet in registry
        merged = list(current.values())
        added: List[AgentMeta] = []
        for key, dm in disk.items():
            if key not in current:
                # auto-add with placeholder description
                merged.append(dm)
                added.append(dm)
            elif overwrite:
                # refresh path/description but keep registry metadata
                cm = current[key]
                cm.path = dm.path
                if dm.description:
                    cm.description = dm.description
        if added or overwrite:
            self._save_raw([m.to_dict() for m in merged])
        return added
