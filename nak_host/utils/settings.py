"""Central settings hub for NebulonAK.

ALL settings live in ONE cfg file (nebulonak.cfg) and connect through
here. brain / worker / harness read their defaults from Settings —
never from scattered files or hardcoded paths.

RULE: No LLM settings anywhere in NebulonAK. No [llm] section is read here
(deliberately); LLM provider/model/keys live in NebulonMD only.

Sections in nebulonak.cfg:
  [paths]        nebulonak_home (empty = folder holding nebulonak.cfg)
  [server]       the AK API itself: host / port / workers / timeout /
                 keep_alive / graceful_timeout / log_level
  [nebulonmind]  base_url / user / auth_token / timeout
  [worker]       workers_file / language   (no default worker: call GenericWorker(name))
  [harness]      workspace_root / user / registry / permissions / crafts / crafts_prompt
  [decision]     endpoint / timeout / auth_token (laya decision endpoint)
  [nebulondb]    resolve_from_mind / base_url / user / corpus / timeout
                 (task store; base_url+user+password resolve from NebulonMD
                 unless overridden; NAK_NDB_PASSWORD is an emergency override,
                 never read from the cfg file)

No [decision]-as-policy section: decision + policy live ONLY in workers.json
entries (edited in the JSON itself). New workers inherit code defaults
(DEFAULT_DECISION_* / DEFAULT_POLICY_*) via default_worker_entry().
[decision] holds only the user-editable decision endpoint (timeout/auth_token);
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
from typing import Any, Dict, List, Optional

# All built-in fallback values live in nak_host.utils.constant (single source);
# Settings only wires them (kwarg > env > cfg > Mind > default). Imported
# here so existing `from nak_host.utils.settings import DEFAULT_*` keeps working.
from nak_host.utils.constant import CFG_FILENAME
from nak_host.utils.constant import DEFAULT_BASE_URL
from nak_host.utils.constant import DEFAULT_CRAFTS
from nak_host.utils.constant import DEFAULT_HOME
from nak_host.utils.constant import DEFAULT_CRAFTS_PROMPT
from nak_host.utils.constant import DEFAULT_DECISION_AUTH_TOKEN
from nak_host.utils.constant import DEFAULT_DECISION_ENABLED
from nak_host.utils.constant import DEFAULT_DECISION_ENDPOINT
from nak_host.utils.constant import DEFAULT_DECISION_MAX_PARALLEL
from nak_host.utils.constant import DEFAULT_DECISION_MODEL
from nak_host.utils.constant import DEFAULT_DECISION_PARALLEL
from nak_host.utils.constant import DEFAULT_DECISION_QUESTIONS
from nak_host.utils.constant import DEFAULT_DECISION_TIMEOUT
from nak_host.utils.constant import DEFAULT_LIMITS_BLOCK as _DEFAULT_LIMITS_BLOCK
from nak_host.utils.constant import DEFAULT_MAX_RETRIES
from nak_host.utils.constant import DEFAULT_NDB_BASE_URL
from nak_host.utils.constant import DEFAULT_NDB_CORPUS
from nak_host.utils.constant import DEFAULT_NDB_MAX_RETRIES
from nak_host.utils.constant import DEFAULT_NDB_RESOLVE_FROM_MIND
from nak_host.utils.constant import DEFAULT_NDB_RETRY_BACKOFF
from nak_host.utils.constant import DEFAULT_NDB_TIMEOUT
from nak_host.utils.constant import DEFAULT_NDB_USER
from nak_host.utils.constant import DEFAULT_PERMISSIONS
from nak_host.utils.constant import DEFAULT_POLICY_CLARIFY_ON_NEEDS_CLARIFICATION
from nak_host.utils.constant import DEFAULT_POLICY_ENABLED
from nak_host.utils.constant import DEFAULT_POLICY_ESCALATE_ON_HIGH_RISK
from nak_host.utils.constant import DEFAULT_POLICY_MAX_PRIORITY_AUTO
from nak_host.utils.constant import DEFAULT_POLICY_MAX_RISK
from nak_host.utils.constant import DEFAULT_POLICY_MIN_CONFIDENCE
from nak_host.utils.constant import DEFAULT_REGISTRY
from nak_host.utils.constant import DEFAULT_RETRY_BACKOFF
from nak_host.utils.constant import DEFAULT_STORE_BACKEND
from nak_host.utils.constant import DEFAULT_TIMEOUT
from nak_host.utils.constant import DEFAULT_USER
from nak_host.utils.constant import DEFAULT_WORKERS_FILE
from nak_host.utils.constant import RESOURCE_DENY_SEED as _RESOURCE_DENY_SEED
from nak_host.utils.constant import SERVE_DEFAULT_GRACEFUL_TIMEOUT
from nak_host.utils.constant import SERVE_DEFAULT_HOST
from nak_host.utils.constant import SERVE_DEFAULT_KEEP_ALIVE
from nak_host.utils.constant import SERVE_DEFAULT_LOG_LEVEL
from nak_host.utils.constant import SERVE_DEFAULT_PORT
from nak_host.utils.constant import SERVE_DEFAULT_TIMEOUT
from nak_host.utils.constant import SERVE_DEFAULT_WORKERS


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
    """Every NebulonAK setting in one object (see module docstring for sources)."""

    # [paths] — project home; everything server-side derives from it.
    home: str = DEFAULT_HOME
    # [server] — the AK API itself (hosted by `python nebulonak.py start`).
    serve_host: str = SERVE_DEFAULT_HOST
    serve_port: int = SERVE_DEFAULT_PORT
    serve_workers: int = SERVE_DEFAULT_WORKERS
    serve_timeout: int = SERVE_DEFAULT_TIMEOUT
    serve_keep_alive: int = SERVE_DEFAULT_KEEP_ALIVE
    serve_graceful_timeout: int = SERVE_DEFAULT_GRACEFUL_TIMEOUT
    serve_log_level: str = SERVE_DEFAULT_LOG_LEVEL
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
    # [nebulondb] — task store connection (password is env-only, never cfg).
    ndb_base_url: str = DEFAULT_NDB_BASE_URL
    ndb_user: str = DEFAULT_NDB_USER
    ndb_password: str = ""
    ndb_corpus: str = DEFAULT_NDB_CORPUS
    ndb_timeout: float = DEFAULT_NDB_TIMEOUT
    ndb_max_retries: int = DEFAULT_NDB_MAX_RETRIES
    ndb_retry_backoff: float = DEFAULT_NDB_RETRY_BACKOFF
    # Resolve base_url/user/password from NebulonMD (single configured
    # connection) unless explicitly overridden per field.
    resolve_from_mind: bool = DEFAULT_NDB_RESOLVE_FROM_MIND
    # Prod store backend: "memory" (tests/offline) or "nebulondb" (durable).
    # Env-only (NAK_STORE); never read from the cfg file by design.
    store_backend: str = DEFAULT_STORE_BACKEND

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
        if not (self.serve_host or "").strip():
            problems.append("serve_host must be non-empty")
        if not 1 <= int(self.serve_port or 0) <= 65535:
            problems.append(f"serve_port must be 1..65535: {self.serve_port!r}")
        if int(self.serve_workers or 0) < 1:
            problems.append(f"serve_workers must be >= 1: {self.serve_workers!r}")
        return problems

    def validate_store(self) -> List[str]:
        """Offline store-config check (no network): backend + NDB shape.

        Prod rule: durable runs use backend "nebulondb". "memory" is valid
        but test/offline-only. Returns problem strings (empty = ok).
        """
        problems: List[str] = []
        backend = str(self.store_backend or "").strip().lower() or DEFAULT_STORE_BACKEND
        if backend not in ("memory", "nebulondb", "ndb", "db", "off", "none"):
            problems.append(f"store_backend must be memory|nebulondb, got {self.store_backend!r}")
            return problems
        if backend in ("memory", "off", "none"):
            return problems
        if not self.ndb_base_url.startswith(("http://", "https://")):
            problems.append(f"ndb_base_url must be http(s): {self.ndb_base_url!r}")
        if "/api/NebulonDB" not in self.ndb_base_url:
            problems.append(f"ndb_base_url must include /api/NebulonDB prefix: {self.ndb_base_url!r}")
        if not (self.ndb_corpus or "").strip():
            problems.append("ndb_corpus must be non-empty (want nak_tasks)")
        if self.ndb_timeout <= 0:
            problems.append(f"ndb_timeout must be > 0: {self.ndb_timeout!r}")
        return problems

    # -- resolved helpers (relative cfg paths -> absolute) -------------- #
    def cfg_dir(self) -> Optional[Path]:
        return self.cfg_path.parent if self.cfg_path else None

    def home_path(self) -> str:
        """Project home: [paths] nebulonak_home or the cfg folder itself."""
        explicit = (self.home or "").strip()
        if explicit:
            return str(Path(explicit).expanduser())
        cfg_dir = self.cfg_dir()
        return str(cfg_dir) if cfg_dir is not None else str(Path.cwd())

    def web_dir(self) -> str:
        """Static web console root: <home>/nak_host/api/web."""
        return str(Path(self.home_path()) / "nak_host" / "api" / "web")

    def logs_dir(self) -> str:
        """Server log root: <home>/logs."""
        return str(Path(self.home_path()) / "logs")

    def pid_file(self) -> str:
        """Server PID file: <home>/nebulonak.pid."""
        return str(Path(self.home_path()) / "nebulonak.pid")

    def runs_dir(self) -> str:
        """Vetting run history shown on the pipeline page."""
        return str(Path(self.home_path()) / "pipeline" / "vetting" / "runs")

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
        home=get("paths", "nebulonak_home", DEFAULT_HOME),
        serve_host=get("server", "host", SERVE_DEFAULT_HOST) or SERVE_DEFAULT_HOST,
        serve_port=getint("server", "port", SERVE_DEFAULT_PORT),
        serve_workers=getint("server", "workers", SERVE_DEFAULT_WORKERS),
        serve_timeout=getint("server", "timeout", SERVE_DEFAULT_TIMEOUT),
        serve_keep_alive=getint("server", "keep_alive", SERVE_DEFAULT_KEEP_ALIVE),
        serve_graceful_timeout=getint("server", "graceful_timeout",
                                      SERVE_DEFAULT_GRACEFUL_TIMEOUT),
        serve_log_level=get("server", "log_level", SERVE_DEFAULT_LOG_LEVEL)
        or SERVE_DEFAULT_LOG_LEVEL,
        base_url=get("nebulonmind", "base_url", DEFAULT_BASE_URL) or DEFAULT_BASE_URL,
        user=get("nebulonmind", "user", DEFAULT_USER) or DEFAULT_USER,
        auth_token=get("nebulonmind", "auth_token", ""),
        timeout=getfloat("nebulonmind", "timeout", DEFAULT_TIMEOUT),
        max_retries=getint("nebulonmind", "max_retries", DEFAULT_MAX_RETRIES),
        retry_backoff=getfloat("nebulonmind", "retry_backoff", DEFAULT_RETRY_BACKOFF),
        workers_file=get("worker", "workers_file", DEFAULT_WORKERS_FILE) or DEFAULT_WORKERS_FILE,
        worker_lang=get("worker", "language", ""),
        # No [decision]-as-policy section by design — decision + policy live
        # ONLY in workers.json entries (edited in the JSON). New workers
        # inherit these code defaults via default_worker_entry().
        # [decision] stays user-editable (endpoint/timeout/auth_token).
        decision_enabled=DEFAULT_DECISION_ENABLED,
        decision_model=DEFAULT_DECISION_MODEL,
        decision_endpoint=get("decision", "endpoint", DEFAULT_DECISION_ENDPOINT)
        or DEFAULT_DECISION_ENDPOINT,
        decision_timeout=getfloat("decision", "timeout", DEFAULT_DECISION_TIMEOUT),
        decision_auth_token=get("decision", "auth_token", DEFAULT_DECISION_AUTH_TOKEN),
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
        ndb_base_url=get("nebulondb", "base_url", DEFAULT_NDB_BASE_URL)
        or DEFAULT_NDB_BASE_URL,
        ndb_user=get("nebulondb", "user", DEFAULT_NDB_USER),
        ndb_corpus=get("nebulondb", "corpus", DEFAULT_NDB_CORPUS)
        or DEFAULT_NDB_CORPUS,
        ndb_timeout=getfloat("nebulondb", "timeout", DEFAULT_NDB_TIMEOUT),
        ndb_max_retries=getint("nebulondb", "max_retries", DEFAULT_NDB_MAX_RETRIES),
        ndb_retry_backoff=getfloat("nebulondb", "retry_backoff", DEFAULT_NDB_RETRY_BACKOFF),
        resolve_from_mind=getbool("nebulondb", "resolve_from_mind",
                                  DEFAULT_NDB_RESOLVE_FROM_MIND),
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
    # Server socket: env wins over [server] (host/port first, then tuning).
    s.serve_host = env("NAK_SERVE_HOST", "").strip() or s.serve_host
    _sp = env("NAK_SERVE_PORT", "").strip()
    if _sp:
        try:
            s.serve_port = int(_sp)
        except ValueError:
            pass
    _sw = env("NAK_SERVE_WORKERS", "").strip()
    if _sw:
        try:
            s.serve_workers = max(1, int(_sw))
        except ValueError:
            pass
    _st = env("NAK_SERVE_TIMEOUT", "").strip()
    if _st:
        try:
            s.serve_timeout = max(1, int(_st))
        except ValueError:
            pass
    s.serve_log_level = env("NAK_SERVE_LOG_LEVEL", "").strip() or s.serve_log_level
    # Project home: env wins over [paths] (also used for cfg discovery).
    s.home = env("NAK_HOME", "").strip() or s.home
    # NOTE: no NAK_DECISION_*/NAK_POLICY_* env by design (see above) —
    # only the user-editable [decision] connection fields below.
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
    s.ndb_base_url = (env("NAK_NDB_BASE_URL", "").strip() or s.ndb_base_url).rstrip("/")
    s.ndb_user = env("NAK_NDB_USER", "").strip() or s.ndb_user
    # Password is env-only by design (never read from the cfg file).
    s.ndb_password = env("NAK_NDB_PASSWORD", "").strip() or s.ndb_password
    s.ndb_corpus = env("NAK_NDB_CORPUS", "").strip() or s.ndb_corpus
    _nt = env("NAK_NDB_TIMEOUT", "").strip()
    if _nt:
        try:
            s.ndb_timeout = float(_nt)
        except ValueError:
            pass
    _rfm = env("NAK_NDB_RESOLVE_FROM_MIND", "").strip().lower()
    if _rfm:
        s.resolve_from_mind = _rfm not in ("0", "false", "no", "off", "")
    # Prod store backend is env-only (NAK_STORE=memory|nebulondb).
    _store = env("NAK_STORE", "").strip().lower()
    if _store:
        s.store_backend = _store

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
