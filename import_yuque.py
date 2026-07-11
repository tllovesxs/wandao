"""Compatibility entrypoint for the Yuque import Plugin v1 backend."""

import sys
from plugins.yuque.backend import import_yuque as _implementation

if __name__ == "__main__":
    raise SystemExit(_implementation.main())
sys.modules[__name__] = _implementation
