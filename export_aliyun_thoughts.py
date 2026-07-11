"""Compatibility entrypoint for the Aliyun Thoughts Plugin v1 backend."""

import sys
from plugins.aliyun_thoughts.backend import export_aliyun_thoughts as _implementation

if __name__ == "__main__":
    raise SystemExit(_implementation.main())
sys.modules[__name__] = _implementation
