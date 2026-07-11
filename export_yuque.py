"""Compatibility entrypoint for the Yuque export Plugin v1 backend."""

import sys
from plugins.yuque.backend import export_yuque as _implementation

if __name__ == "__main__":
    raise SystemExit(_implementation.main())
sys.modules[__name__] = _implementation
