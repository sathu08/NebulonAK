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
    [paths] nak_home

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

    parser = ConfigParser()
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
        "paths": {
            "nak_home": str(_repo_root()),
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
        cfg_path=cfg_path,
    )


__all__ = ["NAKConfig", "load_config", "_default_cfg_path"]
