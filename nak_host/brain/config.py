"""Brain config — thin compat layer over the central settings hub.

All values live in nebulonak.cfg and connect via nak_host/utils/settings.py.
This module keeps the old names (NAKConfig / load_config) working:
new code may import from nak_host.utils.settings directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from ..utils.settings import (
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT,
    DEFAULT_USER,
    CFG_FILENAME,
    Settings,
    find_cfg,
    load_settings,
)

__all__ = ["NAKConfig", "load_config", "find_cfg", "load_settings",
           "DEFAULT_BASE_URL", "DEFAULT_USER", "DEFAULT_TIMEOUT", "CFG_FILENAME"]


@dataclass
class NAKConfig:
    base_url: str = DEFAULT_BASE_URL
    user: str = DEFAULT_USER
    auth_token: str = ""
    timeout: float = DEFAULT_TIMEOUT
    max_retries: int = 2
    retry_backoff: float = 1.0
    cfg_path: Optional[Path] = None

    @classmethod
    def from_settings(cls, s: Settings) -> "NAKConfig":
        return cls(base_url=s.base_url.rstrip("/"), user=s.user,
                   auth_token=s.auth_token, timeout=float(s.timeout),
                   max_retries=int(s.max_retries), retry_backoff=float(s.retry_backoff),
                   cfg_path=s.cfg_path)

    def validate(self) -> List[str]:
        problems: List[str] = []
        if not self.base_url.startswith(("http://", "https://")):
            problems.append(f"base_url must be http(s): {self.base_url!r}")
        if "/api/NebulonMind" not in self.base_url:
            problems.append(
                f"base_url must include /api/NebulonMind prefix: {self.base_url!r}"
            )
        if not (self.user or "").strip() or (self.user or "").startswith("/"):
            problems.append(f"invalid user: {self.user!r}")
        if self.timeout <= 0:
            problems.append(f"timeout must be > 0: {self.timeout!r}")
        if self.max_retries < 0:
            problems.append(f"max_retries must be >= 0: {self.max_retries!r}")
        if self.retry_backoff < 0:
            problems.append(f"retry_backoff must be >= 0: {self.retry_backoff!r}")
        return problems


def load_config(cfg_path: Optional[str | Path] = None) -> NAKConfig:
    """Load nebulonak.cfg + env via the central hub. Never reads any LLM."""
    return NAKConfig.from_settings(load_settings(cfg_path))
