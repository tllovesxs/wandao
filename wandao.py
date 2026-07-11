#!/usr/bin/env python3
"""Wandao unified launcher backed by Plugin v1 manifests."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent


def find_plugins_root() -> Path:
    """Support both source checkout and Electron's ``resources`` layout.

    In a checkout the launcher lives beside ``plugins/``. electron-builder
    places the launcher in ``resources/python`` while bundled Plugin v1
    packages live in ``resources/plugins``.
    """
    for candidate in (ROOT / "plugins", ROOT.parent / "plugins"):
        if candidate.is_dir():
            return candidate
    return ROOT / "plugins"


PLUGINS_ROOT = find_plugins_root()


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def discover_providers() -> dict[str, dict[str, Any]]:
    providers: dict[str, dict[str, Any]] = {}
    for plugin_path in sorted(PLUGINS_ROOT.glob("*/plugin.json")):
        plugin_root = plugin_path.parent
        plugin = json.loads(plugin_path.read_text(encoding="utf-8"))
        if plugin.get("schemaVersion") != 1 or plugin.get("id") != plugin_root.name:
            continue
        platforms = plugin.get("platforms") or []
        platform = "win32" if sys.platform.startswith("win") else "darwin" if sys.platform == "darwin" else "linux"
        if platforms and platform not in platforms:
            continue
        for relative in plugin.get("entrypoints", {}).get("providers", []):
            provider_path = (plugin_root / relative).resolve()
            if not _inside(plugin_root, provider_path) or not provider_path.is_file():
                continue
            provider = json.loads(provider_path.read_text(encoding="utf-8"))
            provider_id = str(provider.get("id") or "")
            if not provider_id or provider_id in providers:
                continue
            actions = provider.get("actions") or []
            script_path = None
            for action in actions:
                script = action.get("script") or provider.get("script")
                if not script:
                    continue
                candidate = (provider_path.parent / script).resolve()
                if _inside(plugin_root, candidate) and candidate.is_file() and candidate.suffix == ".py":
                    script_path = candidate
                    break
            providers[provider_id] = {
                "label": provider.get("title") or provider.get("name") or provider_id,
                "group": provider.get("group") or "guide",
                "plugin": plugin.get("id") or plugin_root.name,
                "script": script_path,
            }
    return providers


def run_provider(provider_id: str, args: list[str], providers: dict[str, dict[str, Any]]) -> int:
    config = providers[provider_id]
    script = config.get("script")
    if not isinstance(script, Path):
        print(f"{provider_id} 是教程型 Provider，没有可执行脚本。", file=sys.stderr)
        return 2
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(ROOT), env.get("PYTHONPATH", "")]))
    return subprocess.call([sys.executable, str(script), *args], cwd=str(ROOT), env=env)


def run_desktop_app() -> int:
    if sys.platform.startswith("win"):
        script = ROOT / "start-wandao.cmd"
        if script.exists():
            return subprocess.call(["cmd", "/c", str(script)], cwd=str(ROOT))
    else:
        script = ROOT / "start-wandao.sh"
        if script.exists():
            return subprocess.call(["bash", str(script)], cwd=str(ROOT))
    print("请使用 Electron 桌面端启动脚本 start-wandao。", file=sys.stderr)
    return 1


def parse_args(argv: list[str], providers: dict[str, dict[str, Any]]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(description="万能导：Plugin v1 多平台知识迁移启动器")
    parser.add_argument("--provider", choices=sorted(providers), help="选择 Provider")
    parser.add_argument("--gui", action="store_true", help="启动 Electron 桌面端")
    parser.add_argument("--list", action="store_true", help="列出已发现的 Provider")
    return parser.parse_known_args(argv)


def main(argv: list[str] | None = None) -> int:
    providers = discover_providers()
    args, rest = parse_args(list(argv or []), providers)
    if args.list:
        for provider_id, config in providers.items():
            print(f"{provider_id}\t{config['group']}\t{config['label']}\t{config['plugin']}")
        return 0
    if not args.provider or args.gui:
        return run_desktop_app()
    if rest and rest[0] == "--":
        rest = rest[1:]
    return run_provider(args.provider, rest, providers)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
