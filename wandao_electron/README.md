# Wandao Desktop

万能导的统一 Electron 桌面端。

## 功能

- 10 个官方平台以 Plugin v1 提供，覆盖 Markdown 导出、导入和教程。
- 官方插件随应用提供，也可以通过签名插件库独立更新和回滚。
- 支持登录凭证保存、目录读取、勾选导出、增量导出、停止任务和全局进度条。
- 通过 Provider v1 清单调用插件内 Python 后端，桌面核心不硬编码平台。

## 开发运行

```bash
cd wandao_electron
npm install
npm start
```

如果启动时报 `electron` 不是可执行命令，通常是还没有执行 `npm install`。

## 打包

Windows：

```bash
npm run build:win
```

macOS：

```bash
npm run build:mac
```

在 Windows 本机更推荐只打 Windows 包；macOS 的 `.zip` 包建议在 macOS 或 GitHub Actions 的 `macos-latest` 环境构建。

## 运行依赖

桌面端发行包已内置 Node/Electron 所需内容和独立 Python 运行时，普通用户不需要安装 Node.js 或 Python。源码开发需要 Python 3.10+ 与 Node.js 22.12+。

## 目录结构

```text
wandao_electron/
├── main.js
├── preload.js
├── plugin_manager.js
├── process_result.js
├── package.json
├── assets/
└── renderer/
    ├── index.html
    ├── styles.css
    └── app.js
```
