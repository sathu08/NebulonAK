"""Deprecated: use ``python -m pipeline.vetting card`` instead."""

from ..cli.card import main

if __name__ == "__main__":
    raise SystemExit(main())
