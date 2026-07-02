<p align="center">
  <img src="docs/images/wandao-logo.png" alt="万能导 Wandao Logo" width="96">
</p>

<h1 align="center">万能导 Wandao ✨</h1>

<p align="center">
  让知识没有壁垒，多平台文档互转。用自动化脚本代替手动复制、整理目录、搬运文档的重复劳动。
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache--2.0-blue.svg" alt="License"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-bundled%20in%20release-blue" alt="Python"></a>
  <a href="#系统要求"><img src="https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey" alt="Platform"></a>
  <a href="https://github.com/tllovesxs/wandao"><img src="https://img.shields.io/badge/GitHub-tllovesxs%2Fwandao-black" alt="GitHub"></a>
  <a href="https://gitee.com/shi-xiansong/wandao"><img src="https://img.shields.io/badge/Gitee-shi--xiansong%2Fwandao-red" alt="Gitee"></a>
</p>

<p align="center">
  <img src="https://cdn.jsdelivr.net/gh/edent/SuperTinyIcons/images/svg/windows.svg" alt="Windows" title="Windows" width="24">
  <img src="https://cdn.jsdelivr.net/gh/edent/SuperTinyIcons/images/svg/apple.svg" alt="macOS" title="macOS" width="24">
  <img src="https://cdn.jsdelivr.net/gh/edent/SuperTinyIcons/images/svg/linux.svg" alt="Linux" title="Linux" width="24">
  <img src="https://cdn.jsdelivr.net/gh/edent/SuperTinyIcons/images/svg/python.svg" alt="Python" title="Python" width="24">
  <img src="https://cdn.jsdelivr.net/gh/edent/SuperTinyIcons/images/svg/markdown.svg" alt="Markdown" title="Markdown" width="24">
  <img src="https://cdn.jsdelivr.net/gh/edent/SuperTinyIcons/images/svg/evernote.svg" alt="Evernote" title="印象笔记 / Evernote" width="24">
</p>

万能导是一个多平台(目前已支持飞书,语雀,阿里云,印象笔记,知识星球,ima,本地md)知识库 Markdown 导入导出工具。你可以把自己有权限访问的项目资料、团队知识库、课程文档导出为本地 Markdown，也可以把整理好的本地 Markdown 再导入到支持的平台中。

它适合用来做知识备份、平台迁移、项目学习资料整理，以及把“教学文档 + 源码项目”放在一起交给 AI 阅读。

Author: `tllovesxs`

## 📌 项目信息

| 项目 | 内容 |
|------|------|
| <img src="https://cdn.jsdelivr.net/gh/edent/SuperTinyIcons/images/svg/github.svg" alt="GitHub" width="16"> GitHub | [tllovesxs/wandao](https://github.com/tllovesxs/wandao) |
| Gitee | [shi-xiansong/wandao](https://gitee.com/shi-xiansong/wandao) |
| 📦 发行版下载 | [GitHub Releases](https://github.com/tllovesxs/wandao/releases) |
| 🐛 问题反馈 | [GitHub Issues](https://github.com/tllovesxs/wandao/issues) / [Gitee Issues](https://gitee.com/shi-xiansong/wandao/issues) |
| 📖 使用教程 | [docs/使用教程.md](docs/使用教程.md) |
| 🔌 Provider 接入 | [docs/Provider接入说明.md](docs/Provider接入说明.md) |
| 🧠 项目学习提示词 | [prompts/项目学习导师提示词.md](prompts/项目学习导师提示词.md) |
| 💬 作者微信 | `pressure_spring` |
| 📮 联系邮箱 | `tl200599@163.com` |

## 🖼️ 截图预览

<p align="left">
  <img src="docs/images/wandao-exporter-guis.png" alt="万能导桌面端主界面" width="900">
</p>

## 🚀 支持能力

### 📤 导出到本地

| 平台 | 能力 |
|------|------|
| 🌟 知识星球 | 支持项目、专栏、帖子、文章页导出，可选导出可见评论区 |
| 🪶 语雀 | 支持任意知识库导出，并尽量本地化正文图片和附件 |
| 🪽 飞书 Wiki | 支持 Wiki 知识库导出为 Markdown |
| ☁️ 阿里云 Thoughts | 支持工作区文档导出为 Markdown |
| <img src="https://cdn.jsdelivr.net/gh/edent/SuperTinyIcons/images/svg/evernote.svg" alt="Evernote" width="16"> 印象笔记 | 支持同步后按笔记本导出 Markdown |
| 🤖 ima 知识库 | 支持读取知识库目录树，按知识库、文件夹或文件勾选导出 |

### 📥 导入到平台

| 平台 | 能力 |
|------|------|
| 🪽 飞书 Wiki | 支持本地 Markdown 批量导入，并恢复多层目录结构 |
| 🪶 语雀 | 支持本地 Markdown 创建或更新到语雀知识库，上传图片和附件 |
| <img src="https://cdn.jsdelivr.net/gh/edent/SuperTinyIcons/images/svg/evernote.svg" alt="Evernote" width="16"> 印象笔记 | 支持本地 Markdown 批量导入，并上传本地图片和附件 |
| 🤖 ima 知识库 | 支持本地文件上传到知识库根目录或已有文件夹 |

## ✨ 主要特性

- 🧭 统一桌面端：左侧按“导出 / 导入”分类展示平台入口。
- ✅ 目录选择：先读取目录，再选择全部或部分内容。
- 📊 进度反馈：读取目录、导入、导出都有进度条和实时日志。
- 🧾 任务历史：导入导出会保留最近任务记录，可复制任务报告，也可继续/重试未完成任务。
- 🧩 Provider 架构：平台入口逐步插件化，新增平台时优先注册 provider，降低 UI 维护成本。
- 🧯 错误报告：用户日志和详细日志分离，反馈问题时可一键复制脱敏后的详细报告。
- 🔁 增量更新：已导出的文档可以跳过，只补缺失内容。
- 🖼️ 图片和附件处理：尽量把正文图片、附件下载或上传到目标平台。
- 🔎 浏览器自动查找：自动扫描 Chrome、Edge、Chromium，也支持手动指定。
- ⏱️ 请求节奏控制：内置固定延迟和随机浮动，尽量接近正常手动浏览节奏。
- ⏹️ 停止按钮：任务执行中可以随时停止，已完成文件会保留。
- 🌙 夜间模式和更新检查：桌面端支持主题切换和新版本提示。

## ⚡ 快速开始

### 👤 普通用户

1. 打开 [GitHub Releases](https://github.com/tllovesxs/wandao/releases)。
2. 下载对应系统的发行版。
3. 安装或解压后打开 `Wandao`。
4. 在左侧选择要使用的平台。
5. 按界面提示填写链接、登录、读取目录、选择范围并执行任务。

> **macOS 用户注意**：从 GitHub 或 Gitee 下载的应用可能会被 macOS 标记隔离属性，首次打开可能提示“已损坏，无法打开”。这不是应用本身的问题，而是系统为了防止未签名应用运行所做的拦截。请在终端执行以下命令后再打开，路径需要替换为 `Wandao.app` 实际所在位置：
>
> ```bash
> xattr -cr /Applications/Wandao.app
> ```

发行版已内置 Python 运行时，普通用户不需要额外安装 Python。

### 🧑‍💻 源码一键启动

如果你想改代码、本地调试，或者当前系统暂时没有合适的发行版，可以直接用源码启动。

一键启动脚本会自动完成三件事：

1. 检查本机有没有 Node.js/npm。
2. 如果没有，会下载一个本地便携 Node.js 到 `.dev-runtime`，不污染系统环境。
3. 自动检测官方 npm 源和国内 npmmirror，选择更适合当前网络的方式安装依赖并启动。

Windows：

```powershell
git clone https://github.com/tllovesxs/wandao.git
cd wandao
.\start-wandao.cmd
```

也可以直接双击：

```text
start-wandao.cmd
```

macOS/Linux：

```bash
git clone https://github.com/tllovesxs/wandao.git
cd wandao
chmod +x ./start-wandao.sh
./start-wandao.sh
```

国内网络环境也可以把 clone 地址换成 Gitee：

```powershell
git clone https://gitee.com/shi-xiansong/wandao.git
cd wandao
.\start-wandao.cmd
```

> 如果只想安装依赖、不启动软件，可以运行 `.\start-wandao.cmd -InstallOnly` 或 `./start-wandao.sh --install-only`。

## 🧩 常用流程

### 📤 导出知识库

1. 选择左侧“导出”里的目标平台。
2. 填写入口链接，或填写平台账号/API 配置。
3. 第一次使用时点击“登录并保存凭证”或“保存 API 配置”。
4. 点击“读取目录”。
5. 勾选要导出的目录或文档。
6. 点击“开始导出”。

### 📥 导入 Markdown

1. 选择左侧“导入”里的目标平台。
2. 选择本地 Markdown 目录。
3. 按目标平台要求填写链接、API 配置或账号信息。
4. 先“扫描目录”或“生成计划”。
5. 先做“单篇/单文件导入测试”。
6. 确认效果后再批量导入。

飞书导入、语雀导入、印象笔记导入、ima 导入的详细步骤见 [使用教程](docs/使用教程.md)。

## 🧠 配合 AI 学习项目

万能导适合和 AI 编程/阅读工具一起使用：

1. 用万能导导出你有权限访问的教学文档。
2. 把导出的 Markdown 目录放进源码项目中。
3. 用 AI 打开整个项目目录。
4. 把 [项目学习导师提示词](prompts/项目学习导师提示词.md) 发给 AI。
5. 之后可以按章节、功能或技术点提问，让 AI 结合“教学文档 + 真实源码”讲解。

## 🛠️ 系统要求

| 依赖 | 要求 |
|------|------|
| <img src="https://cdn.jsdelivr.net/gh/edent/SuperTinyIcons/images/svg/python.svg" alt="Python" width="16"> Python | 发行版已内置；源码运行需要 Python 3.10+ |
| 🟩 Node.js | 仅源码运行桌面端、参与开发或本地打包时需要 |
| 🌐 浏览器 | Chrome、Edge 或 Chromium，浏览器类平台登录时使用 |
| 🔐 权限 | 需要拥有目标内容的正常访问权限 |

## 📦 打包说明

```powershell
cd wandao_electron
npm ci
npm run build:win
```

macOS 包建议使用 GitHub Actions 或 macOS 本机打包：

```bash
npm run build:mac:x64
npm run build:mac:arm64
```

## ⚖️ 合规说明

本项目用于减少用户手动复制、整理、迁移文档的机械劳动。它不会破解登录、不绕过权限控制，也不提供未授权内容访问能力。

请确认：

- 你对目标内容拥有访问权限。
- 你的使用方式符合平台服务条款和版权要求。
- 不要将导出的内容用于未获授权的传播、售卖或公开发布。
- 不要调低延迟进行高频请求或批量滥用。

更多说明见 [docs/合规说明.md](docs/合规说明.md)。

## 🤝 参与贡献

欢迎提交 Issue 和 Pull Request。Bug 反馈、平台适配、导入导出效果优化、文档补充和界面体验改进都可以参与。

提交前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。请不要在 Issue、PR、截图或日志里提交 Cookie、账号密码、App Secret、Token、API Key 等敏感信息。

## 🔗 友情链接

- [LINUX DO](https://linux.do)

## License

本项目采用 [Apache License 2.0](LICENSE) 开源。

---
## Star History

<a href="https://www.star-history.com/?repos=tllovesxs%2Fwandao&type=timeline&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=tllovesxs/wandao&type=timeline&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=tllovesxs/wandao&type=timeline&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=tllovesxs/wandao&type=timeline&legend=top-left" />
 </picture>
</a>

如果这个项目对你有帮助，欢迎在 GitHub 给一个 Star。
