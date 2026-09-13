"""Build the Tauri updater manifest from signed release artifacts."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote


REPOSITORY = "tllovesxs/wandao"


def find_signed_artifact(directory: Path, suffixes: tuple[str, ...], keywords: tuple[str, ...]) -> Path:
    candidates = sorted(
        path
        for path in directory.iterdir()
        if path.is_file()
        and path.name.endswith(suffixes)
        and Path(f"{path}.sig").is_file()
        and any(keyword in path.name.lower() for keyword in keywords)
    )
    if not candidates:
        raise SystemExit(f"没有找到带签名的更新资产：{suffixes} / {keywords}")
    if len(candidates) > 1:
        raise SystemExit(f"找到多个候选更新资产：{', '.join(path.name for path in candidates)}")
    return candidates[0]


def target_payload(path: Path, tag: str) -> dict[str, str]:
    signature = Path(f"{path}.sig").read_text(encoding="utf-8").strip()
    url = (
        f"https://github.com/{REPOSITORY}/releases/download/"
        f"{quote(tag, safe='')}/{quote(path.name, safe='')}"
    )
    return {"url": url, "signature": signature}


def build_manifest(directory: Path, tag: str) -> dict[str, object]:
    windows = find_signed_artifact(directory, (".exe", ".zip"), ("setup", ".nsis.", ".msi."))
    macos = find_signed_artifact(directory, (".app.tar.gz",), ("wandao",))
    windows_payload = target_payload(windows, tag)
    return {
        "version": tag.removeprefix("v").removeprefix("V"),
        "notes": "包含 Vditor Markdown 阅读编辑器、长文导出看门狗、输出目录整理和任务恢复优化。",
        "pub_date": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "platforms": {
            "windows-x86_64": windows_payload,
            "windows-x86_64-nsis": windows_payload,
            "darwin-aarch64": target_payload(macos, tag),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = build_manifest(args.directory, args.tag)
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
