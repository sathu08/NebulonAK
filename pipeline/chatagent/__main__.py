"""Entry shim for `python -m pipeline.chatagent`.

All logic lives in pipeline.py (terminal boundary: build_parser/main) and
nak.service (headless API). This file only forwards argv so the module stays
runnable without duplicating anything.
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    from .pipeline import build_parser, main
    from nak.service.suggest import capability_options, suggest_agent_name
except ImportError:  # fallback when run as a script file
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from pipeline.chatagent.pipeline import build_parser, main
    from nak.service.suggest import capability_options, suggest_agent_name

__all__ = ["build_parser", "main", "suggest_agent_name", "capability_options"]


if __name__ == "__main__":
    raise SystemExit(main())
