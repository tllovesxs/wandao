from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any


def write_private_json(path: str | Path, data: Any) -> Path:
    """Atomically write local credentials with owner-only permissions where supported."""
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass
    return target
