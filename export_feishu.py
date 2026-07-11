#!/usr/bin/env python3
"""Compatibility entry point for the installable Feishu plugin."""

import sys
from plugins.feishu.backend import export_feishu as _implementation


if __name__ == "__main__":
    raise SystemExit(_implementation.main(sys.argv[1:]))
sys.modules[__name__] = _implementation
