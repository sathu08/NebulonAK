#!/usr/bin/env python3
"""NebulonAK server entry point.

Hosts the NebulonAK API (``nak_host.api.main``) the same way
``nebulondb.py`` hosts NebulonDB and ``nebulonmd.py`` hosts NebulonMD:
one thin entry point over the ``nak_host`` package.

This script is a thin shim; the FastAPI app, laya router, console
routes and settings live in the ``nak_host`` package. No interactive
UI — everything observable lives on the web console.

Usage:
    python nebulonak.py start [--foreground] [--force]
    python nebulonak.py stop [--force]
    python nebulonak.py restart [--foreground] [--force]
    python nebulonak.py status
"""

from nak_host.cli.app import main


if __name__ == "__main__":
    raise SystemExit(main())
