#!/usr/bin/env python3
"""
NebulonAgentKit Runner
==========================================================

Hosts the NebulonAgentKit API (``nak.api.main``) the same way
``nebulonmd.py`` hosts NebulonMind and ``nebulondb.py`` hosts NebulonDB:
one thin entry point over the ``nak`` package.

This script is a thin entry point; the FastAPI app, laya router and
settings live in the ``nak`` package.

Usage:
    python nebulonagentkit.py            # serve the API (foreground uvicorn)

Host/port/prefix come from ``nebulonak.cfg`` via
``nak.utils.settings.DEFAULT_DECISION_ENDPOINT``; override at run time with
``NAK_SERVE_HOST`` / ``NAK_SERVE_PORT``.
"""

from nak.api.main import main


if __name__ == "__main__":
    main()
