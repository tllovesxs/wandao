#!/usr/bin/env python3
"""Compatibility entry point for the installable WizNote plugin."""

from plugins.wiz.backend.export_wiz import *  # noqa: F401,F403
from plugins.wiz.backend.export_wiz import main as _plugin_main


if __name__ == "__main__":
    raise SystemExit(_plugin_main())
