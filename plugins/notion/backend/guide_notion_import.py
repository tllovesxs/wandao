#!/usr/bin/env python3
"""Guide script for importing Markdown files into Notion.

Since Notion doesn't provide a direct Markdown import API,
this script provides step-by-step guidance for users.
"""

import sys


def show_guide():
    """Display import guide for Notion."""
    guide = """
╔═══════════════════════════════════════════════════════════╗
║         Notion Markdown 导入操作指引                       ║
╚═══════════════════════════════════════════════════════════╝

方法一：复制粘贴（推荐，保留格式最佳）
─────────────────────────────────────────────────────
1. 用文本编辑器打开 .md 文件
2. 全选内容 (Ctrl+A / Cmd+A)
3. 复制 (Ctrl+C / Cmd+C)
4. 在 Notion 中新建页面或选择目标位置
5. 粘贴 (Ctrl+V / Cmd+V)
6. Notion 会自动识别并转换 Markdown 格式

✓ 优点：保留标题、列表、代码块、表格等格式
✗ 缺点：图片需要手动上传或使用图床


方法二：拖拽文件
─────────────────────────────────────────────────────
1. 打开目标 Notion 页面
2. 将 .md 文件从文件管理器拖入页面
3. Notion 会将其作为附件上传
4. 点击文件可在 Notion 中预览

✓ 优点：快速，适合批量
✗ 缺点：不会转换为 Notion 原生块，仅作为附件


方法三：使用第三方工具
─────────────────────────────────────────────────────
• notion-importer (npm): https://github.com/coleam00/notion-importer
• md-to-notion (Python): https://github.com/philippgille/notion-md-converter
• Notion Enhancer: https://notion-enhancer.github.io/

注意：使用第三方工具需谨慎，确保不泄露隐私数据


图片处理建议
─────────────────────────────────────────────────────
1. 使用图床服务（如 imgur、SM.MS、阿里云OSS）
2. 在 Markdown 中使用绝对 URL 引用图片
3. 粘贴到 Notion 后，图片会自动加载

或者：
1. 先上传图片到 Notion（拖拽图片到页面）
2. 复制图片链接
3. 替换 Markdown 中的图片 URL


常见问题
─────────────────────────────────────────────────────
Q: 为什么粘贴后格式乱了？
A: 确保复制的是纯 Markdown 文本，不是渲染后的 HTML

Q: 表格没有正确识别？
A: Notion 对表格语法要求严格，确保使用标准 GitHub Flavored Markdown

Q: 代码块丢失高亮？
A: 在 Markdown 中指定语言标识，如 ```python

═══════════════════════════════════════════════════════════
"""
    print(guide)
    return 0


if __name__ == "__main__":
    sys.exit(show_guide())
