#!/usr/bin/env python3
"""Compatibility entry point for the installable WizNote plugin."""

import sys
from plugins.wiz.backend import export_wiz as _implementation


if __name__ == "__main__":
    raise SystemExit(_implementation.main())
sys.modules[__name__] = _implementation
