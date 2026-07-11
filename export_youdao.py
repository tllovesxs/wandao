"""Compatibility entrypoint for the Youdao Plugin v1 backend."""

import sys
from plugins.youdao.backend import export_youdao as _implementation

if __name__ == "__main__":
    raise SystemExit(_implementation.main())
sys.modules[__name__] = _implementation
