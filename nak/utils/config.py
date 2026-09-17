"""
NAK Brain Config
================
Loads NebulonMind connection settings for NebulonAK.

Resolution order (highest wins):
    1. explicit args / env vars (NAK_BASE_URL, NAK_USER, NAK_TIMEOUT, NAK_AUTH_TOKEN, NEBULONAK_HOME)
    2. nebulonak.cfg on disk (or custom path)
    3. built-in defaults

The cfg file lives at NebulonAK root: /home/sathya/Codebase/NebulonAK/nebulonak.cfg
It mirrors NebulonMD's nebulonmind.cfg style but simplified for AK usage.

Sections:
    [nebulonmind] base_url, user, timeout, auth_token, ...
    [brain] default_top_k, auto_create_user, ...
    [policy] tool_use, create_agent, max_turns (approvals + loop cap;
             everything runs on NebulonMind only — no external provider)
    [paths] nak_home
    [harness] workspace_dir, verify_commands, verify_timeout, max_retries,
              max_delegation (env NAK_* wins over cfg; cfg wins over defaults)

Secrets (auth_token) may also come from env; they are never written back to logs.
"""

from __future__ import annotations

import os
from pathlib import Path
from configparser import ConfigParser
from dataclasses import dataclass


def _repo_root() -> Path:
    # nak/brain/config.py -> nak/brain -> nak -> NebulonAK
    return Path(__file__).resolve().parents[2]


def _default_cfg_path() -> Path:
    # honor NAK_HOME / NEBULONAK_HOME, then NebulonAK repo root
    nak_home = os.environ.get("NAK_HOME") or os.environ.get("NEBULONAK_HOME")
    if nak_home:
        return Path(nak_home).expanduser().resolve() / "nebulonak.cfg"
    return _repo_root() / "nebulonak.cfg"


@dataclass(frozen=True)
class NAKConfig:
    """Resolved config for the NAK brain."""

    base_url: str           # e.g. http://localhost:9696/api/NebulonMind
    user: str               # NebulonMind username (registered via /user/create_user)
    timeout: float
    connect_timeout: float
    read_timeout: float
    write_timeout: float
    verify_ssl: bool
    auth_token: str
    default_top_k: int
    default_max_characters: int
    auto_create_user: bool
    persist_user: bool
    system_prompt: str
    policy_tool_use: str
    policy_create_agent: str
    policy_max_turns: int
    harness_workspace_dir: str
    harness_verify_commands: str
    harness_verify_timeout: int
    harness_max_retries: int
    harness_max_delegation: int
    harness_project_memory: bool
    multitask_enabled: bool
    multitask_max_agents: int
    multitask_huge_tasks: int
    multitask_huge_files: int
    approval_channel: str
    cfg_path: Path

    @property
    def health_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/health"

    @property
    def health_live_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/health/live"

    @property
    def health_ready_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/health/ready"

    @classmethod
    def load(cls, cfg_path: str | Path | None = None) -> "NAKConfig":
        return load_config(cfg_path)

    def ensure_cfg_exists(self) -> None:
        if not self.cfg_path.exists():
            raise FileNotFoundError(f"nebulonak.cfg not found at {self.cfg_path}. Create it or set NEBULONAK_HOME.")

    def set_user(self, username: str) -> None:
        """Persist username back to nebulonak.cfg if persist_user is true."""
        username = (username or "").strip()
        if not username or username.startswith("/"):
            return
        if not self.persist_user:
            return
        parser = ConfigParser()
        parser.read(self.cfg_path, encoding="utf-8")
        if "nebulonmind" not in parser:
            parser.add_section("nebulonmind")
        parser.set("nebulonmind", "user", username)
        with self.cfg_path.open("w", encoding="utf-8") as f:
            parser.write(f)


def load_config(cfg_path: str | Path | None = None) -> NAKConfig:
    # resolve cfg file
    if cfg_path is None:
        cfg_path = _default_cfg_path()
    else:
        cfg_path = Path(cfg_path).expanduser().resolve()

    # strict=False: a duplicated key (e.g. two live `channel = ...` lines)
    # takes the last value instead of discarding the whole file.
    parser = ConfigParser(strict=False)
    # defaults first
    parser.read_dict({
        "nebulonmind": {
            "base_url": "http://localhost:9696/api/NebulonMind",
            "user": "nmd_user_01",
            "timeout": "30",
            "connect_timeout": "5",
            "read_timeout": "30",
            "write_timeout": "60",
            "verify_ssl": "false",
            "auth_token": "",
        },
        "brain": {
            "default_top_k": "5",
            "default_max_characters": "6000",
            "auto_create_user": "true",
            "persist_user": "false",
        },
        "policy": {
            "tool_use": "ask",
            "create_agent": "ask",
            "max_turns": "6",
        },
        "paths": {
            "nak_home": str(_repo_root()),
        },
        "harness": {
            "workspace_dir": "",
            "verify_commands": "",
            "verify_timeout": "60",
            "max_retries": "2",
            "max_delegation": "2",
            "project_memory": "true",
        },
        "multitask": {
            # PLACEHOLDER: user turns multitask on ONLY for huge projects.
            # Small tasks skip the board entirely (single-turn prompts stay
            # byte-identical). Env NAK_MULTITASK_* wins over these values.
            "enabled": "false",
            "max_agents": "5",
            "huge_tasks": "5",
            "huge_files": "5",
        },
        "approval": {
            # Single live approval channel (env NAK_APPROVAL_VIA wins).
            "channel": "auto",
        },
    })

    if cfg_path.exists():
        try:
            parser.read(cfg_path, encoding="utf-8")
        except Exception:
            pass  # keep defaults if corrupt

    def _get(section: str, key: str, fallback: str = "") -> str:
        try:
            return parser.get(section, key, fallback=fallback).strip()
        except Exception:
            return fallback

    def _get_bool(section: str, key: str, fallback: bool = False) -> bool:
        val = _get(section, key, str(fallback)).lower()
        return val in ("1", "true", "yes", "on")

    def _get_int(section: str, key: str, fallback: int = 0) -> int:
        try:
            return int(_get(section, key, str(fallback)) or str(fallback))
        except Exception:
            return fallback

    def _get_float(section: str, key: str, fallback: float = 0.0) -> float:
        try:
            return float(_get(section, key, str(fallback)) or str(fallback))
        except Exception:
            return fallback

    # env overrides (highest priority)
    base_url = os.environ.get("NAK_BASE_URL") or os.environ.get("NEBULONMIND_BASE_URL") or _get("nebulonmind", "base_url")
    user = os.environ.get("NAK_USER") or os.environ.get("NAK_USERNAME") or _get("nebulonmind", "user")
    auth_token = os.environ.get("NAK_AUTH_TOKEN") or os.environ.get("NMD_API_AUTH_TOKEN") or _get("nebulonmind", "auth_token")
    timeout = _get_float("nebulonmind", "timeout", 30.0)
    if os.environ.get("NAK_TIMEOUT"):
        try:
            timeout = float(os.environ["NAK_TIMEOUT"])
        except Exception:
            pass
    connect_timeout = _get_float("nebulonmind", "connect_timeout", 5.0)
    read_timeout = _get_float("nebulonmind", "read_timeout", 30.0)
    write_timeout = _get_float("nebulonmind", "write_timeout", 60.0)

    # allow NAK_BASE_URL to be like "http://host:9696" without suffix -> append
    base_url = base_url.strip().rstrip("/")
    if base_url and not base_url.endswith("/api/NebulonMind"):
        # if user gave just host:port, normalize
        if base_url.endswith("/api/NebulonMind/health"):
            base_url = base_url[: -len("/health")]
        elif base_url.endswith("/health"):
            # shouldn't happen but strip
            base_url = base_url[: -len("/health")]
        if "/api/NebulonMind" not in base_url:
            # assume raw host; if it looks like http://host:port
            if base_url.count("/") <= 2:  # e.g. http://localhost:9696
                base_url = base_url + "/api/NebulonMind"

    def _get_mode(section: str, key: str, fallback: str = "ask") -> str:
        val = (os.environ.get(f"NAK_POLICY_{key.upper()}") or _get(section, key, fallback)).strip().lower()
        return val if val in ("ask", "allow_once", "allow_always", "no") else fallback

    try:
        policy_max_turns = int(os.environ.get("NAK_POLICY_MAX_TURNS") or _get("policy", "max_turns", 6))
    except ValueError:
        policy_max_turns = 6

    def _harness_str(key: str, env: str, default: str = "") -> str:
        return (os.environ.get(env) or _get("harness", key, default)).strip()

    def _harness_int(key: str, env: str, default: int, lo: int, hi: int) -> int:
        try:
            val = int(os.environ.get(env) or _get("harness", key, str(default)))
        except ValueError:
            val = default
        return max(lo, min(val, hi))

    def _harness_bool(key: str, env: str, default: bool = True) -> bool:
        raw_env = os.environ.get(env)
        if raw_env is not None:
            v = raw_env.strip().lower()
            if v in ("1", "true", "yes", "on", "enabled"):
                return True
            if v in ("0", "false", "no", "off", "disabled"):
                return False
            return default
        v = _get("harness", key, str(default)).strip().lower()
        if v in ("1", "true", "yes", "on", "enabled"):
            return True
        if v in ("0", "false", "no", "off", "disabled"):
            return False
        return default

    def _multitask_bool(key: str, env: str, default: bool = False) -> bool:
        raw_env = os.environ.get(env)
        if raw_env is not None:
            v = raw_env.strip().lower()
            if v in ("1", "true", "yes", "on", "enabled"):
                return True
            if v in ("0", "false", "no", "off", "disabled"):
                return False
            return default
        v = _get("multitask", key, str(default)).strip().lower()
        if v in ("1", "true", "yes", "on", "enabled"):
            return True
        if v in ("0", "false", "no", "off", "disabled"):
            return False
        return default

    def _multitask_int(key: str, env: str, default: int, lo: int, hi: int) -> int:
        try:
            val = int(os.environ.get(env) or _get("multitask", key, str(default)))
        except ValueError:
            val = default
        return max(lo, min(val, hi))

    def _approval_channel() -> str:
        raw_env = (os.environ.get("NAK_APPROVAL_VIA") or "").strip().lower()
        if raw_env in ("web", "terminal"):
            return raw_env
        val = _get("approval", "channel", "auto").strip().lower()
        return val if val in ("web", "terminal", "auto") else "auto"

    return NAKConfig(
        base_url=base_url,
        user=user.strip() or "nmd_user_01",
        timeout=timeout,
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
        write_timeout=write_timeout,
        verify_ssl=_get_bool("nebulonmind", "verify_ssl", False),
        auth_token=auth_token.strip(),
        default_top_k=_get_int("brain", "default_top_k", 5),
        default_max_characters=_get_int("brain", "default_max_characters", 6000),
        auto_create_user=_get_bool("brain", "auto_create_user", True),
        persist_user=_get_bool("brain", "persist_user", False),
        system_prompt="",  # agent config removed from cfg; agents own their prompts
        policy_tool_use=_get_mode("policy", "tool_use"),
        policy_create_agent=_get_mode("policy", "create_agent"),
        policy_max_turns=max(1, policy_max_turns),
        harness_workspace_dir=_harness_str("workspace_dir", "NAK_WORKSPACE"),
        harness_verify_commands=_harness_str("verify_commands", "NAK_VERIFY_COMMANDS"),
        harness_verify_timeout=_harness_int("verify_timeout", "NAK_VERIFY_TIMEOUT", 60, 1, 300),
        harness_max_retries=_harness_int("max_retries", "NAK_MAX_RETRIES", 2, 0, 5),
        harness_max_delegation=_harness_int("max_delegation", "NAK_MAX_DELEGATION", 2, 1, 5),
        harness_project_memory=_harness_bool("project_memory", "NAK_PROJECT_MEMORY", True),
        multitask_enabled=_multitask_bool("enabled", "NAK_MULTITASK_ENABLED", False),
        multitask_max_agents=_multitask_int("max_agents", "NAK_MULTITASK_MAX_AGENTS", 5, 1, 5),
        multitask_huge_tasks=_multitask_int("huge_tasks", "NAK_MULTITASK_HUGE_TASKS", 5, 2, 100),
        multitask_huge_files=_multitask_int("huge_files", "NAK_MULTITASK_HUGE_FILES", 5, 2, 200),
        approval_channel=_approval_channel(),
        cfg_path=cfg_path,
    )


__all__ = ["NAKConfig", "load_config", "_default_cfg_path",
           "get_workspace_dir", "get_verify_commands", "get_verify_timeout",
           "get_max_retries", "get_max_delegation", "get_project_memory",
           "get_multitask_enabled", "get_multitask_max_agents",
           "get_multitask_huge_tasks", "get_multitask_huge_files",
           "get_approval_channel"]


# -- harness settings accessors (env > cfg > defaults, never raise) -----------
# Consumers (workspace/verifier/recovery/agent_tools) read through these so a
# user edit to nebulonak.cfg [harness] takes effect with no restarts.

def _harness_cfg() -> "NAKConfig | None":
    try:
        return load_config()
    except Exception:
        return None


def get_workspace_dir() -> str:
    """Workspace base dir override ("" = repo/workspace default)."""
    cfg = _harness_cfg()
    return cfg.harness_workspace_dir if cfg else ""


def get_verify_commands() -> str:
    """Verifier command override: JSON list or ';'-separated ("" = defaults)."""
    cfg = _harness_cfg()
    return cfg.harness_verify_commands if cfg else ""


def get_verify_timeout() -> int:
    """Per-check verifier seconds (default 60, clamped 1..300)."""
    cfg = _harness_cfg()
    return cfg.harness_verify_timeout if cfg else 60


def get_max_retries() -> int:
    """Auto-recovery budget (default 2, clamped 0..5)."""
    cfg = _harness_cfg()
    return cfg.harness_max_retries if cfg else 2


def get_max_delegation() -> int:
    """Delegate nesting cap (default 2, clamped 1..5)."""
    cfg = _harness_cfg()
    return cfg.harness_max_delegation if cfg else 2


def get_project_memory() -> bool:
    """R15 kill-switch: persistent project memory on/off (default True)."""
    cfg = _harness_cfg()
    if cfg is None:
        return True
    try:
        return bool(cfg.harness_project_memory)
    except Exception:  # noqa: BLE001
        return True


def get_multitask_enabled() -> bool:
    """Multitask board kill-switch: ONLY huge projects (default False)."""
    cfg = _harness_cfg()
    if cfg is None:
        return False
    try:
        return bool(cfg.multitask_enabled)
    except Exception:  # noqa: BLE001
        return False


def get_multitask_max_agents() -> int:
    """Max parallel agents for huge tasks (default 5, clamped 1..5)."""
    cfg = _harness_cfg()
    if cfg is None:
        return 5
    try:
        return max(1, min(int(cfg.multitask_max_agents), 5))
    except Exception:  # noqa: BLE001
        return 5


def get_multitask_huge_tasks() -> int:
    """Plan steps at/above which a task counts as huge (default 5)."""
    cfg = _harness_cfg()
    if cfg is None:
        return 5
    try:
        return max(2, int(cfg.multitask_huge_tasks))
    except Exception:  # noqa: BLE001
        return 5


def get_multitask_huge_files() -> int:
    """File count at/above which a task counts as huge (default 5)."""
    cfg = _harness_cfg()
    if cfg is None:
        return 5
    try:
        return max(2, int(cfg.multitask_huge_files))
    except Exception:  # noqa: BLE001
        return 5


def get_approval_channel() -> str:
    """Live approval channel: terminal | web | auto (default auto).

    terminal = input() prompts; web = browser Approvals queue;
    auto = terminal when stdin is a TTY, else web. Env NAK_APPROVAL_VIA wins.
    """
    cfg = _harness_cfg()
    if cfg is None:
        return "auto"
    try:
        val = str(cfg.approval_channel or "auto").strip().lower()
        return val if val in ("web", "terminal", "auto") else "auto"
    except Exception:  # noqa: BLE001
        return "auto"
