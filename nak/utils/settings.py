"""Central settings hub for NebulonAK_1.

ALL settings live in ONE cfg file (nebulonak.cfg) and connect through
here. brain / worker / harness read their defaults from Settings —
never from scattered files or hardcoded paths.

RULE: No LLM settings anywhere in AK_1. No [llm] section is read here
(deliberately); LLM provider/model/keys live in NebulonMD only.

Sections in nebulonak.cfg:
  [nebulonmind]  base_url / user / auth_token / timeout
  [worker]       workers_file / language   (no default worker: call GenericWorker(name))
  [harness]      workspace_root / user / registry / permissions / crafts / crafts_prompt

No [decision] section: decision + policy live ONLY in workers.json
entries (edited in the JSON itself). New workers inherit code defaults
(DEFAULT_DECISION_* / DEFAULT_POLICY_*) via default_worker_entry().
[server] holds the user-editable decision endpoint (timeout/auth_token);
per-worker workers.json decision.endpoint wins over it.

Resolution per field (highest wins):
  1. Explicit constructor/load arg
  2. Env (NAK_* — see _ENV below)
  3. nebulonak.cfg value
  4. Built-in default

Relative file/dir paths in cfg resolve against the cfg file's folder.
"""

from __future__ import annotations

import configparser
import os

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

CFG_FILENAME = "nebulonak.cfg"

DEFAULT_BASE_URL = "http://localhost:9696/api/NebulonMind"
DEFAULT_USER = "nmd_user_01"
DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_RETRY_BACKOFF = 1.0
# Relative to the NebulonAK_1 project root (folder holding nebulonak.cfg).
DEFAULT_WORKERS_FILE = "nak/manifests/workers.json"
DEFAULT_CRAFTS = "nak/manifests/crafts.json"
DEFAULT_CRAFTS_PROMPT = "nak/harness/craft"
DEFAULT_REGISTRY = "nak/manifests/tools.json"
DEFAULT_PERMISSIONS = "nak/manifests/permissions.json"
# --- decision (laya System-1) defaults ----------------------------------
# Mirrors nak/utils/constant.py DEFAULT_POLICY / DEFAULT_DECISION_QUESTIONS.
# A newly created worker automatically gets decision+policy from
# default_worker_entry() below. Per-worker workers.json decision.policy
# wins over these Settings.
DEFAULT_DECISION_ENABLED = True
DEFAULT_DECISION_MODEL = "laya"
DEFAULT_DECISION_QUESTIONS = [
    {"name": "priority", "type": "score"},
    {"name": "risk", "type": "score"},
    {"name": "needs_clarification", "type": "boolean"},
]
# Decision now connects via API endpoint (no local laya lib in decider.py).
# Point this at `laya-serve` (POST /api/NebulonAgentKit) or any compatible scorer.
# Per-worker workers.json decision.endpoint wins over this default.
DEFAULT_DECISION_ENDPOINT = "http://localhost:8000/api/NebulonAgentKit"
DEFAULT_DECISION_TIMEOUT = 30.0
DEFAULT_DECISION_PARALLEL = True
DEFAULT_DECISION_MAX_PARALLEL = 4
DEFAULT_DECISION_AUTH_TOKEN = ""
DEFAULT_POLICY_ENABLED = True
DEFAULT_POLICY_MIN_CONFIDENCE = 0.6
DEFAULT_POLICY_MAX_RISK = 2.0
DEFAULT_POLICY_MAX_PRIORITY_AUTO = 2.5
DEFAULT_POLICY_CLARIFY_ON_NEEDS_CLARIFICATION = True
DEFAULT_POLICY_ESCALATE_ON_HIGH_RISK = True


def find_cfg(explicit: Optional[str | Path] = None) -> Optional[Path]:
    """Locate nebulonak.cfg: explicit > NAK_HOME > cwd-tree > project root."""
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_file() else None
    for env_key in ("NAK_HOME", "NEBULONAK_HOME"):
        home = os.environ.get(env_key, "").strip()
        if home:
            cand = Path(home).expanduser() / CFG_FILENAME
            if cand.is_file():
                return cand
    here = Path.cwd()
    for base in (here, *here.parents):
        cand = base / CFG_FILENAME
        if cand.is_file():
            return cand
    root = Path(__file__).resolve().parents[2] / CFG_FILENAME
    return root if root.is_file() else None


def _resolve_near_cfg(value: str, cfg_dir: Optional[Path]) -> str:
    value = (value or "").strip()
    if not value or Path(value).is_absolute():
        return value
    if cfg_dir is None:
        return value
    return str((cfg_dir / value))


@dataclass
class Settings:
    """Every AK_1 setting in one object (see module docstring for sources)."""

    # [nebulonmind]
    base_url: str = DEFAULT_BASE_URL
    user: str = DEFAULT_USER
    auth_token: str = ""
    timeout: float = DEFAULT_TIMEOUT
    max_retries: int = DEFAULT_MAX_RETRIES
    retry_backoff: float = DEFAULT_RETRY_BACKOFF
    # [worker] — locators only; the worker itself is chosen per call.
    workers_file: str = DEFAULT_WORKERS_FILE
    worker_lang: str = ""
    # [harness] — craft registry + craft markdown root.
    crafts: str = DEFAULT_CRAFTS
    crafts_prompt: str = DEFAULT_CRAFTS_PROMPT
    # Code defaults stamped into every NEW worker entry (no cfg section).
    decision_enabled: bool = DEFAULT_DECISION_ENABLED
    decision_model: str = DEFAULT_DECISION_MODEL
    decision_endpoint: str = DEFAULT_DECISION_ENDPOINT
    decision_timeout: float = DEFAULT_DECISION_TIMEOUT
    decision_auth_token: str = DEFAULT_DECISION_AUTH_TOKEN
    policy_enabled: bool = DEFAULT_POLICY_ENABLED
    policy_min_confidence: float = DEFAULT_POLICY_MIN_CONFIDENCE
    policy_max_risk: float = DEFAULT_POLICY_MAX_RISK
    policy_max_priority_auto: float = DEFAULT_POLICY_MAX_PRIORITY_AUTO
    policy_clarify_on_needs_clarification: bool = DEFAULT_POLICY_CLARIFY_ON_NEEDS_CLARIFICATION
    policy_escalate_on_high_risk: bool = DEFAULT_POLICY_ESCALATE_ON_HIGH_RISK
    # [harness]
    workspace_root: str = ""
    harness_user: str = ""
    registry: str = DEFAULT_REGISTRY
    permissions: str = DEFAULT_PERMISSIONS

    cfg_path: Optional[Path] = field(default=None, compare=False)

    def validate(self) -> List[str]:
        problems: List[str] = []
        if not self.base_url.startswith(("http://", "https://")):
            problems.append(f"base_url must be http(s): {self.base_url!r}")
        if "/api/NebulonMind" not in self.base_url:
            problems.append(f"base_url must include /api/NebulonMind prefix: {self.base_url!r}")
        if not (self.user or "").strip() or (self.user or "").startswith("/"):
            problems.append(f"invalid user: {self.user!r}")
        if self.timeout <= 0:
            problems.append(f"timeout must be > 0: {self.timeout!r}")
        if self.max_retries < 0:
            problems.append(f"max_retries must be >= 0: {self.max_retries!r}")
        if self.retry_backoff < 0:
            problems.append(f"retry_backoff must be >= 0: {self.retry_backoff!r}")
        return problems

    # -- resolved helpers (relative cfg paths -> absolute) -------------- #
    def cfg_dir(self) -> Optional[Path]:
        return self.cfg_path.parent if self.cfg_path else None

    def workers_path(self) -> str:
        return _resolve_near_cfg(self.workers_file, self.cfg_dir())

    def crafts_path(self) -> str:
        return _resolve_near_cfg(self.crafts, self.cfg_dir())

    def crafts_prompt_path(self) -> str:
        return _resolve_near_cfg(self.crafts_prompt, self.cfg_dir())

    def registry_path(self) -> str:
        return _resolve_near_cfg(self.registry, self.cfg_dir())

    def permissions_path(self) -> str:
        return _resolve_near_cfg(self.permissions, self.cfg_dir())

    def workspace_path(self) -> str:
        return _resolve_near_cfg(self.workspace_root, self.cfg_dir())

    def session_user(self) -> str:
        """User owning engine sessions: harness.user > nebulonmind.user."""
        return (self.harness_user or self.user or DEFAULT_USER).strip()


def load_settings(cfg_path: Optional[str | Path] = None) -> Settings:
    """Read nebulonak.cfg + env into Settings. Never reads any [llm]."""
    found = find_cfg(cfg_path)
    cfg_dir = found.parent if found else None
    parser = configparser.ConfigParser()
    if found is not None:
        try:
            parser.read(found, encoding="utf-8")
        except Exception:
            parser = configparser.ConfigParser()

    def get(section: str, key: str, fallback: str = "") -> str:
        try:
            return parser.get(section, key, fallback=fallback).strip()
        except Exception:
            return fallback.strip()

    def getfloat(section: str, key: str, fallback: float) -> float:
        try:
            return parser.getfloat(section, key, fallback=fallback)
        except ValueError:
            return fallback

    def getint(section: str, key: str, fallback: int) -> int:
        try:
            return parser.getint(section, key, fallback=fallback)
        except ValueError:
            return fallback

    def getbool(section: str, key: str, fallback: bool) -> bool:
        try:
            return parser.getboolean(section, key, fallback=fallback)
        except ValueError:
            raw = get(section, key, "")
            if not raw:
                return fallback
            return raw.strip().lower() not in ("0", "false", "no", "off", "")

    s = Settings(
        base_url=get("nebulonmind", "base_url", DEFAULT_BASE_URL) or DEFAULT_BASE_URL,
        user=get("nebulonmind", "user", DEFAULT_USER) or DEFAULT_USER,
        auth_token=get("nebulonmind", "auth_token", ""),
        timeout=getfloat("nebulonmind", "timeout", DEFAULT_TIMEOUT),
        max_retries=getint("nebulonmind", "max_retries", DEFAULT_MAX_RETRIES),
        retry_backoff=getfloat("nebulonmind", "retry_backoff", DEFAULT_RETRY_BACKOFF),
        workers_file=get("worker", "workers_file", DEFAULT_WORKERS_FILE) or DEFAULT_WORKERS_FILE,
        worker_lang=get("worker", "language", ""),
        # No [decision] cfg section by design — decision + policy live ONLY
        # in workers.json entries (edited in the JSON). New workers inherit
        # these code defaults via default_worker_entry().
        # [server] stays user-editable (endpoint/timeout/auth_token).
        decision_enabled=DEFAULT_DECISION_ENABLED,
        decision_model=DEFAULT_DECISION_MODEL,
        decision_endpoint=get("server", "endpoint", DEFAULT_DECISION_ENDPOINT)
        or DEFAULT_DECISION_ENDPOINT,
        decision_timeout=getfloat("server", "timeout", DEFAULT_DECISION_TIMEOUT),
        decision_auth_token=get("server", "auth_token", DEFAULT_DECISION_AUTH_TOKEN),
        policy_enabled=DEFAULT_POLICY_ENABLED,
        policy_min_confidence=DEFAULT_POLICY_MIN_CONFIDENCE,
        policy_max_risk=DEFAULT_POLICY_MAX_RISK,
        policy_max_priority_auto=DEFAULT_POLICY_MAX_PRIORITY_AUTO,
        policy_clarify_on_needs_clarification=DEFAULT_POLICY_CLARIFY_ON_NEEDS_CLARIFICATION,
        policy_escalate_on_high_risk=DEFAULT_POLICY_ESCALATE_ON_HIGH_RISK,
        workspace_root=get("harness", "workspace_root", ""),
        harness_user=get("harness", "user", ""),
        registry=get("harness", "registry", DEFAULT_REGISTRY) or DEFAULT_REGISTRY,
        permissions=get("harness", "permissions", DEFAULT_PERMISSIONS) or DEFAULT_PERMISSIONS,
        crafts=get("harness", "crafts", DEFAULT_CRAFTS) or DEFAULT_CRAFTS,
        crafts_prompt=get("harness", "crafts_prompt", DEFAULT_CRAFTS_PROMPT)
        or DEFAULT_CRAFTS_PROMPT,
        cfg_path=found,
    )
    # NOTE: any [llm] section is intentionally IGNORED (NebulonMD owns LLM).

    # Env wins over file (per-field).
    env = os.environ.get
    s.base_url = (env("NAK_BASE_URL", "").strip() or s.base_url).rstrip("/")
    s.user = env("NAK_USER", "").strip() or s.user
    s.auth_token = env("NAK_AUTH_TOKEN", "").strip() or env("NMD_API_AUTH_TOKEN", "").strip() or s.auth_token
    try:
        s.timeout = float(env("NAK_TIMEOUT", "").strip() or s.timeout)
    except ValueError:
        pass
    try:
        s.max_retries = int(env("NAK_MAX_RETRIES", "").strip() or s.max_retries)
    except ValueError:
        pass
    try:
        s.retry_backoff = float(env("NAK_RETRY_BACKOFF", "").strip() or s.retry_backoff)
    except ValueError:
        pass
    s.workers_file = env("NAK_WORKERS_FILE", "").strip() or s.workers_file
    s.crafts = (env("NAK_CRAFTS", "").strip()
                or env("NAK_CRAFTS_FILE", "").strip() or s.crafts)
    s.crafts_prompt = (env("NAK_CRAFTS_PROMPT", "").strip()
                       or s.crafts_prompt)
    s.worker_lang = env("NAK_WORKER_LANG", "").strip() or s.worker_lang
    # NOTE: no NAK_DECISION_*/NAK_POLICY_* env by design (see above) —
    # only the user-editable [server] connection fields below.
    s.decision_endpoint = env("NAK_DECISION_ENDPOINT", "").strip() or s.decision_endpoint
    s.decision_auth_token = env("NAK_DECISION_AUTH_TOKEN", "").strip() or s.decision_auth_token
    _dt = env("NAK_DECISION_TIMEOUT", "").strip()
    if _dt:
        try:
            s.decision_timeout = float(_dt)
        except ValueError:
            pass
    s.workspace_root = env("NAK_WORKSPACE", "").strip() or s.workspace_root
    s.harness_user = env("NAK_HARNESS_USER", "").strip() or s.harness_user
    s.registry = env("NAK_REGISTRY", "").strip() or s.registry
    s.permissions = env("NAK_PERMISSIONS", "").strip() or s.permissions

    # Re-anchor relative paths against the cfg folder after env overrides.
    for attr in ("workers_file", "workspace_root", "registry"):
        val = (getattr(s, attr) or "").strip()
        if val and not Path(val).is_absolute() and Path(val).parent != Path("."):
            # keep cfg-relative strings as-is; helpers resolve them.
            setattr(s, attr, val)
    _ = cfg_dir  # resolved lazily via cfg_path in helpers
    return s


def default_policy_block(s: Optional[Settings] = None) -> dict:
    """Policy block stamped into new workers (Settings > built-in)."""
    s = s or Settings()
    return {
        "enabled": bool(s.policy_enabled),
        "min_confidence": float(s.policy_min_confidence),
        "max_risk": float(s.policy_max_risk),
        "max_priority_auto": float(s.policy_max_priority_auto),
        "clarify_on_needs_clarification": bool(s.policy_clarify_on_needs_clarification),
        "escalate_on_high_risk": bool(s.policy_escalate_on_high_risk),
    }


def default_decision_block(s: Optional[Settings] = None) -> dict:
    """Decision block stamped into new workers (questions + policy)."""
    import copy

    s = s or Settings()
    base = (s.decision_endpoint or DEFAULT_DECISION_ENDPOINT).strip() or DEFAULT_DECISION_ENDPOINT
    # Full scoring URL: server base + /laya/ (canonical trailing slash —
    # the router also accepts the bare variant, but exact avoids a 307 hop).
    endpoint = base.rstrip("/") + "/laya/"
    return {
        "enabled": bool(s.decision_enabled),
        "model": (s.decision_model or DEFAULT_DECISION_MODEL).strip() or DEFAULT_DECISION_MODEL,
        "endpoint": endpoint,
        "timeout": float(s.decision_timeout or DEFAULT_DECISION_TIMEOUT),
        "parallel": DEFAULT_DECISION_PARALLEL,
        "max_parallel": DEFAULT_DECISION_MAX_PARALLEL,
        "questions": copy.deepcopy(DEFAULT_DECISION_QUESTIONS),
        "policy": default_policy_block(s),
    }


def _scan_permission_vocabulary(s: Optional[Settings] = None) -> tuple:
    """Collect (tool names, dotted capabilities) for permission generation.

    Sources (raw JSON reads only, never imports worker/harness/engine):
      workers.json entries' capabilities (worker traits),
      tools.json tool names + capabilities (mapped read/write/delete/
      execute -> file.read/file.write/file.delete/system.execute,
      others -> cap.<name>),
      permissions.json existing tools/capability keys (vocabulary stays
      stable across generations). Any read failure -> that source is empty.
    """
    import json as _json

    s = s or Settings()
    tools: List[str] = []
    caps: List[str] = []
    _cap_map = {"read": "file.read", "write": "file.write",
                "delete": "file.delete", "execute": "system.execute"}

    def _read(p: str) -> Any:
        try:
            return _json.loads(Path(p).expanduser().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    workers_raw = _read(s.workers_path())
    if isinstance(workers_raw, list):
        for e in workers_raw:
            if not isinstance(e, dict):
                continue
            for c in (e.get("capabilities") or []):
                c = str(c or "").strip()
                if c and c not in caps:
                    caps.append(c)
    registry_raw = _read(s.registry_path())
    reg_entries = registry_raw if isinstance(registry_raw, list) else []
    if isinstance(registry_raw, dict) and isinstance(registry_raw.get("tools"), list):
        reg_entries = registry_raw["tools"]
    for e in reg_entries:
        if not isinstance(e, dict):
            continue
        t = str(e.get("name", "") or "").strip()
        if t and t not in tools:
            tools.append(t)
        for c in (e.get("capabilities") or []):
            c = str(c or "").strip()
            if not c:
                continue
            dotted = _cap_map.get(c, f"cap.{c}")
            if dotted not in caps:
                caps.append(dotted)
    perm_raw = _read(s.permissions_path())
    perm_workers = perm_raw.get("workers") if isinstance(perm_raw, dict) else None
    if isinstance(perm_workers, dict):
        for e in perm_workers.values():
            if not isinstance(e, dict):
                continue
            for t in (e.get("tools") or {}):
                t = str(t or "").strip()
                if t and t not in tools:
                    tools.append(t)
            for c in (e.get("capabilities") or {}):
                c = str(c or "").strip()
                if c and c not in caps:
                    caps.append(c)
    return sorted(tools), sorted(caps)


# Mirrors sandbox.py deny rules (kept in sync by hand).
_RESOURCE_DENY_SEED = ["**/.env", "**/.git/**", "**/.ssh/**",
                       "**/*password*", "**/*secret*", "**/*credential*"]

_DEFAULT_LIMITS_BLOCK = {"max_tool_calls": 30, "max_execution_time": 120,
                         "max_file_size": 10485760}


def default_permission_entry(name: str,
                             worker_capabilities: Optional[List[str]] = None,
                             s: Optional[Settings] = None) -> dict:
    """permissions.json workers.<name> block for a NEW worker.

    No capabilities.json catalog (removed): every new worker starts
    deny-all (fail-closed). Tool/capability vocabulary is scanned from
    workers.json + tools.json + permissions.json (see
    _scan_permission_vocabulary) so every known action is explicitly
    listed as deny. Edit the generated block to grant actions. Shape
    mirrors permissions.json (version 1.0.0).
    """
    s = s or Settings()
    clean = str(name or "").strip() or "worker"
    have = [str(c or "").strip() for c in (worker_capabilities or [])]
    have = [c for c in have if c]
    tools, caps = _scan_permission_vocabulary(s)
    granted_tools: Dict[str, str] = {}
    granted_caps: Dict[str, str] = {}
    read_grants: List[str] = []
    write_grants: List[str] = []
    tool_verdicts = {t: granted_tools.get(t, "deny")
                     for t in sorted(set(tools) | set(granted_tools))}
    cap_verdicts = {c: granted_caps.get(c, "deny")
                    for c in sorted(set(caps) | set(granted_caps))}
    return {
        "enabled": True,
        "description": f"{clean}: deny-all starter (traits {have or ['(none)']}); edit verdicts to grant.",
        "tools": tool_verdicts,
        "capabilities": cap_verdicts,
        "resources": {
            "files": {
                "read": sorted(read_grants),
                "write": sorted(write_grants),
                "deny": list(_RESOURCE_DENY_SEED),
            }
        },
        "limits": dict(_DEFAULT_LIMITS_BLOCK),
    }


def default_worker_entry(
    name: str,
    role: str = "Assistant",
    language: str = "en",
    gender: str = "unspecified",
    s: Optional[Settings] = None,
    capabilities: Optional[List[str]] = None,
) -> dict:
    """Full workers.json entry for `nak-worker --create NAME`.

    Identity + memory + decision/policy come from code defaults
    (DEFAULT_DECISION_* / DEFAULT_POLICY_*); per-worker tuning happens
    in workers.json itself (no cfg section by design). capabilities
    defaults to ["identity", "decision"]; extra names are stored as-is
    (permissions start deny-all — see default_permission_entry). crafts
    defaults to ["plan"] (plan-only, least privilege; add "assign" by
    editing workers.json).
    """
    s = s or Settings()
    clean = str(name or "").strip() or "worker"
    caps = ["identity", "decision"]
    for c in (capabilities or []):
        c = str(c or "").strip()
        if c and c not in caps:
            caps.append(c)
    return {
        "name": clean,
        "type": "coordinator",
        "description": f"{role}. decision/policy auto-stamped from Settings.",
        "capabilities": caps,
        "prompt_file": f"prompts/{clean}.md",
        "crafts": ["plan"],
        "decision": default_decision_block(s),
        "version": "1.0.0",
        "identity": {
            "role": role,
            "personality": ["Calm", "practical", "proactive"],
            "tone": "Friendly and professional",
            "formality": "professional",
            "humor": "none",
            "gender": (str(gender or "").strip() or "unspecified"),
            "values": ["Quality", "deadlines", "transparency"],
            "do_not": [],
            "language": (s.worker_lang or language or "en").strip() or "en",
        },
        "memory": {"enabled": True},
    }
