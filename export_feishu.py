#!/usr/bin/env python3
"""Compatibility entry point for the installable Feishu plugin."""

import sys

from plugins.feishu.backend.export_feishu import *  # noqa: F401,F403
from plugins.feishu.backend.export_feishu import main as _plugin_main


if __name__ == "__main__":
    raise SystemExit(_plugin_main(sys.argv[1:]))
