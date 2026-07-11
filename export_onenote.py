"""Compatibility entrypoint for the OneNote Plugin v1 backend."""

import sys
from plugins.onenote.backend import export_onenote as _implementation

if __name__ == "__main__":
    raise SystemExit(_implementation.main())
sys.modules[__name__] = _implementation
