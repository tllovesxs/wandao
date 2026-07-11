#!/usr/bin/env python3
"""Verify that an unpacked desktop artifact contains usable Plugin v1 assets."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def expected_providers() -> set[str]:
    provider_ids: set[str] = set()
    for manifest_path in (REPO_ROOT / "plugins").glob("*/plugin.json"):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for relative_path in manifest.get("entrypoints", {}).get("providers", []):
            provider = json.loads((manifest_path.parent / relative_path).read_text(encoding="utf-8"))
            provider_ids.add(str(provider["id"]))
    return provider_ids


def executable_provider_ids() -> set[str]:
    provider_ids: set[str] = set()
    for manifest_path in (REPO_ROOT / "plugins").glob("*/plugin.json"):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for relative_path in manifest.get("entrypoints", {}).get("providers", []):
            provider_path = manifest_path.parent / relative_path
            provider = json.loads(provider_path.read_text(encoding="utf-8"))
            if any((action.get("script") or provider.get("script")) for action in provider.get("actions", []) if isinstance(action, dict)):
                provider_ids.add(str(provider["id"]))
    return provider_ids


def packaged_python(resources: Path) -> Path:
    candidates = [
        resources / "python-runtime" / "python.exe",
        resources / "python-runtime" / "bin" / "python3",
        resources / "python-runtime" / "bin" / "python",
    ]
    return next((candidate for candidate in candidates if candidate.is_file()), candidates[0])


def discovered_provider_ids(resources: Path) -> set[str]:
    launcher = resources / "python" / "wandao.py"
    python = packaged_python(resources)
    if not launcher.is_file():
        raise RuntimeError(f"缺少打包后的统一启动器：{launcher}")
    if not python.is_file():
        raise RuntimeError(f"缺少打包后的 Python 运行时：{python}")
    result = subprocess.run(
        [str(python), str(launcher), "--list"],
        cwd=resources / "python",
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"打包后的启动器执行失败（{result.returncode}）：{result.stderr.decode(errors='replace')}")
    return {
        line.split(b"\t", 1)[0].decode("ascii")
        for line in result.stdout.splitlines()
        if b"\t" in line
    }


def verify_packaged_backend_help(resources: Path, provider_ids: set[str]) -> None:
    launcher = resources / "python" / "wandao.py"
    python = packaged_python(resources)
    for provider_id in sorted(provider_ids):
        result = subprocess.run(
            [str(python), str(launcher), "--provider", provider_id, "--", "--help"],
            cwd=resources / "python",
            capture_output=True,
            check=False,
            timeout=30,
        )
        if result.returncode:
            stderr = result.stderr.decode(errors="replace")
            stdout = result.stdout.decode(errors="replace")
            raise RuntimeError(f"打包后端无法启动：{provider_id}（{result.returncode}）：{stderr or stdout}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="验证 Electron 解包产物中的 Plugin v1 资源")
    parser.add_argument("--resources", required=True, type=Path, help="解包应用的 resources 目录")
    args = parser.parse_args(argv)
    resources = args.resources.resolve()
    expected_plugins = {path.name for path in (REPO_ROOT / "plugins").iterdir() if path.is_dir() and (path / "plugin.json").is_file()}
    packaged_plugins = {path.name for path in (resources / "plugins").iterdir() if path.is_dir() and (path / "plugin.json").is_file()}
    if packaged_plugins != expected_plugins:
        raise RuntimeError(f"打包插件不一致：期望 {sorted(expected_plugins)}，实际 {sorted(packaged_plugins)}")
    expected = expected_providers()
    discovered = discovered_provider_ids(resources)
    if discovered != expected:
        raise RuntimeError(f"打包 Provider 发现不一致：期望 {sorted(expected)}，实际 {sorted(discovered)}")
    executable = executable_provider_ids()
    verify_packaged_backend_help(resources, executable)
    print(
        f"Packaged resource smoke passed ({len(packaged_plugins)} plugins, "
        f"{len(discovered)} providers, {len(executable)} executable backends)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
