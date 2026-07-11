"""Compatibility entrypoint for the Yinxiang export Plugin v1 backend."""

import sys
from plugins.yinxiang.backend import export_yinxiang as _implementation

if __name__ == "__main__":
    raise SystemExit(_implementation.main())
sys.modules[__name__] = _implementation
