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
from typing import Any


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


def run_launcher_gui() -> int:
    import tkinter as tk
    from tkinter import messagebox, ttk
    from gui_utils import create_scrollable_body

    root = tk.Tk()
    root.title("万能导 Wandao")
    root.geometry("620x360")
    body = create_scrollable_body(root)

    provider_keys = list(PROVIDERS)
    provider_var = tk.StringVar(value=provider_keys[0])

    tk.Label(body, text="选择要使用的知识库工具", anchor="w").pack(fill="x", padx=18, pady=(18, 6))
    selector = ttk.Combobox(
        body,
        textvariable=provider_var,
        state="readonly",
        values=[f"{key} - {PROVIDERS[key]['label']}" for key in provider_keys],
    )
    selector.pack(fill="x", padx=18)

    hint_var = tk.StringVar()

    def sync_hint(*_: Any) -> None:
        selected = provider_var.get().split(" - ", 1)[0]
        config = PROVIDERS[selected]
        hint_var.set(
            f"入口参数：{config['url_arg']}\n"
            f"URL 示例：{config['url_hint']}\n\n"
            "点击下方按钮后，会打开对应平台的工具界面。导出工具通常先登录并保存凭证，"
            "再读取目录、勾选范围并导出；导入工具按界面提示填写目标平台配置。"
        )

    provider_var.trace_add("write", sync_hint)
    selector.current(0)
    sync_hint()

    tk.Label(body, textvariable=hint_var, justify="left", anchor="w").pack(fill="x", padx=18, pady=18)

    def open_provider_gui() -> None:
        selected = provider_var.get().split(" - ", 1)[0]
        try:
            subprocess.Popen([sys.executable, str(ROOT / PROVIDERS[selected]["script"]), "--gui"], cwd=str(ROOT))
        except Exception as exc:
            messagebox.showerror("启动失败", str(exc))

    def open_project_dir() -> None:
        if sys.platform.startswith("win"):
            import os

            os.startfile(ROOT)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(ROOT)])

    actions = tk.Frame(body)
    actions.pack(fill="x", padx=18, pady=8)
    tk.Button(actions, text="打开工具界面", command=open_provider_gui, width=18).pack(side="left", padx=(0, 10))
    tk.Button(actions, text="打开项目目录", command=open_project_dir, width=14).pack(side="left")
    tk.Button(actions, text="退出", command=root.destroy, width=10).pack(side="right")

    tk.Label(
        body,
        text="提示：请只导出自己拥有权限的内容；工具内置可调延迟和停止按钮，避免高频请求。",
        anchor="w",
    ).pack(fill="x", padx=18, pady=(12, 0))

    root.mainloop()
    return 0


def parse_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(description="万能导：多平台知识库导出启动器")
    parser.add_argument("--provider", choices=sorted(PROVIDERS), help="选择导出平台")
    parser.add_argument("--gui", action="store_true", help="打开所选平台 GUI；未选择平台时打开统一启动器")
    parser.add_argument("--list", action="store_true", help="列出支持的平台")
    return parser.parse_known_args(argv)


def main(argv: list[str]) -> int:
    args, rest = parse_args(argv)
    if args.list:
        for key, config in PROVIDERS.items():
            print(f"{key}\t{config['label']}")
        return 0
    if not args.provider:
        return run_launcher_gui()
    if rest and rest[0] == "--":
        rest = rest[1:]
    if args.gui and "--gui" not in rest:
        rest = ["--gui", *rest]
    return run_provider(args.provider, rest)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
