"""Compatibility entrypoint for the ZSXQ Plugin v1 backend."""

import sys
from plugins.zsxq.backend import export_zsxq as _implementation

if __name__ == "__main__":
    raise SystemExit(_implementation.main())
sys.modules[__name__] = _implementation
