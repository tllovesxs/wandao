# Wandao 1.1.0 Release Notes

## 新增

- 新增 ima 知识库导出：支持读取 ima 知识库目录树，并按知识库、文件夹或文件勾选导出到本地。
- 新增 ima 知识库导入：支持将本地 Markdown、PDF、Word、PPT、Excel、图片、TXT、Xmind、音频等文件上传到 ima 知识库。
- 桌面端左侧“导出 / 导入”分组新增 ima 知识库入口。
- ima 导入新增目标文件夹选择：用户可以先读取目标知识库已有文件夹，再从下拉框选择上传位置。

## 改进

- ima 导入默认跳过 Markdown 正文引用到的本地图片和附件，避免配图被重复当成独立知识库文件上传。
- ima 导入支持扫描本地目录并统计待上传文件，单文件测试通过后再批量上传。
- README 重新整理为更简洁的 GitHub 首页结构，突出支持平台、快速开始、常用流程和合规说明。
- 版本号统一升级到 `1.1.0`，用于这次新增 ima 知识库导入导出能力。

## 当前限制

- ima OpenAPI 当前未提供明确的“创建知识库文件夹”接口，所以导入可以写入知识库根目录或已有文件夹，暂不能自动把本地多级目录完整重建到 ima。
- ima 笔记类内容导出受官方 API 返回内容影响，会尽量保存为 Markdown 文本；普通文件会尽量保存原文件。

## 验证

- 已执行 `python -m py_compile ima_knowledge.py wandao.py`。
- 已执行 `node --check wandao_electron/main.js`。
- 已执行 `node --check wandao_electron/renderer/app.js`。
- 已执行敏感信息扫描，确认仓库文件中不包含本次测试用账号、密码、API Key。

## 下载

- Windows 安装版：下载 `Wandao Setup 1.1.0.exe`。
- Windows 免安装版：下载 `Wandao 1.1.0.exe`。
- macOS Apple Silicon：下载 `Wandao-1.1.0-arm64-mac.zip`，适合 M1 / M2 / M3 / M4 芯片 Mac。
- macOS Intel：下载 `Wandao-1.1.0-x64-mac.zip`，适合 Intel 芯片 Mac。

## 注意

- 普通用户请优先下载发行版，发行版内置 Python 运行时，不需要额外安装 Python。
- 源码运行或参与开发时，仍需要自行安装 Python 3.10+ 和 Node.js。
- 请只处理自己有权限访问的内容，并遵守目标平台服务条款和版权要求。
- 请勿在 Issue、PR、截图或日志里提交 Cookie、账号密码、App Secret、Token、API Key 等敏感信息。
