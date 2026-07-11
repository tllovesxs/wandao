"""Compatibility entrypoint for the ima Plugin v1 backend."""

import sys
from plugins.ima.backend import ima_knowledge as _implementation

if __name__ == "__main__":
    raise SystemExit(_implementation.main())
sys.modules[__name__] = _implementation
