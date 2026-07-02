#!/usr/bin/env python3
"""
Wandao unified launcher.

Author: tllovesxs
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent

PROVIDERS: dict[str, dict[str, str]] = {
    "zsxq": {
        "label": "知识星球任意项目/专栏",
        "script": "export_zsxq.py",
        "url_arg": "--entry-url",
        "url_hint": "https://wx.zsxq.com/columns/...",
    },
    "yuque": {
        "label": "语雀任意知识库",
        "script": "export_yuque.py",
        "url_arg": "--book-url",
        "url_hint": "https://www.yuque.com/<namespace>/<book>",
    },
    "yuque-import": {
        "label": "语雀 Markdown 导入",
        "script": "import_yuque.py",
        "url_arg": "--target-book-url",
        "url_hint": "https://www.yuque.com/<namespace>/<book>",
    },
    "feishu": {
        "label": "飞书任意 Wiki 知识库",
        "script": "export_feishu.py",
        "url_arg": "--wiki-url",
        "url_hint": "https://<tenant>.feishu.cn/wiki/<token>",
    },
    "aliyun-thoughts": {
        "label": "阿里云 Thoughts 任意工作区",
        "script": "export_aliyun_thoughts.py",
        "url_arg": "--workspace-url",
        "url_hint": "https://thoughts.aliyun.com/workspaces/<id>/overview",
    },
    "yinxiang": {
        "label": "印象笔记任意笔记本",
        "script": "export_yinxiang.py",
        "url_arg": "无需 URL",
        "url_hint": "首次登录并同步后，可读取本地目录并勾选导出",
    },
    "youdao": {
        "label": "有道云笔记任意目录",
        "script": "export_youdao.py",
        "url_arg": "无需 URL",
        "url_hint": "首次登录并保存凭证后，可读取有道云笔记目录并勾选导出",
    },
    "yinxiang-import": {
        "label": "印象笔记 Markdown 导入",
        "script": "import_yinxiang.py",
        "url_arg": "无需 URL",
        "url_hint": "选择本地 Markdown 目录后，可导入到印象笔记",
    },
    "ima": {
        "label": "ima 知识库导入导出",
        "script": "ima_knowledge.py",
        "url_arg": "无需 URL",
        "url_hint": "填写 ima Client ID / API Key 后，可读取知识库目录、导出或导入文件",
    },
}


def run_provider(provider: str, args: list[str]) -> int:
    config = PROVIDERS[provider]
    script = ROOT / config["script"]
    command = [sys.executable, str(script), *args]
    return subprocess.call(command, cwd=str(ROOT))


def run_desktop_app() -> int:
    if sys.platform.startswith("win"):
        script = ROOT / "start-wandao.cmd"
        if script.exists():
            return subprocess.call(["cmd", "/c", str(script)], cwd=str(ROOT))
    else:
        script = ROOT / "start-wandao.sh"
        if script.exists():
            return subprocess.call(["bash", str(script)], cwd=str(ROOT))
    print("旧版 Python GUI 已废弃。请使用 Electron 桌面端启动脚本 start-wandao。", file=sys.stderr)
    return 1


def parse_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(description="万能导：多平台知识库导出启动器")
    parser.add_argument("--provider", choices=sorted(PROVIDERS), help="选择导出平台")
    parser.add_argument("--gui", action="store_true", help="旧版 Python GUI 已废弃；此选项会启动 Electron 桌面端")
    parser.add_argument("--list", action="store_true", help="列出支持的平台")
    return parser.parse_known_args(argv)


def main(argv: list[str]) -> int:
    args, rest = parse_args(argv)
    if args.list:
        for key, config in PROVIDERS.items():
            print(f"{key}\t{config['label']}")
        return 0
    if not args.provider:
        return run_desktop_app()
    if args.gui:
        print("旧版 Python GUI 已废弃，正在启动 Electron 桌面端。", file=sys.stderr)
        return run_desktop_app()
    if rest and rest[0] == "--":
        rest = rest[1:]
    return run_provider(args.provider, rest)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
