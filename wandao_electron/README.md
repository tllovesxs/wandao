# Wandao Desktop

万能导 1.4.0 的统一 Tauri 2 桌面端。Rust 主进程负责本地安全边界、插件与任务运行，前端继续使用 `renderer/` 中的 HTML/CSS/JavaScript。

## 功能

- 官方平台以 Plugin v1 提供，覆盖 Markdown 导出、导入和教程。
- 官方插件随应用提供，也可以通过签名插件库独立更新和回滚。
- 支持登录凭证保存、目录读取、勾选导出、增量导出、停止任务和全局进度条。
- 通过 Provider v1 清单调用插件内 Python 后端，桌面核心不硬编码平台。

## 开发运行

```bash
cd wandao_electron
npm ci
npm start
```

源码开发需要 Python 3.10+、Node.js 22.12+ 和 Rust 1.88.0。首次运行前请安装对应系统的 [Tauri 2 前置依赖](https://v2.tauri.app/start/prerequisites/)；若 `tauri` 或 Rust 编译失败，先确认 `npm ci` 成功且 `rustc --version` 为 1.88.0。

## 本地未签名 smoke 打包

Windows：

```bash
npm run build:win:unsigned
```

macOS：

```bash
npm run build:mac:unsigned
```

固定架构的本地构建可使用 `npm run build:mac:x64:unsigned` 或 `npm run build:mac:arm64:unsigned`。Windows 本机只构建 NSIS 包；macOS `.app` 建议在对应架构的 macOS 环境构建。命令会先准备并验证独立 Python runtime，再执行 Tauri release 构建。本地和手动工作流的 `UNSIGNED-SMOKE` 产物只能用于 smoke，不得直接发布；正式 `v*` tag 当前也生成未签名 Windows x64 和 macOS Apple Silicon（arm64，macOS 11+）包，但会通过完整发布门禁后创建 Draft Release。

## 运行依赖

桌面端发行包由 Tauri 2 原生主程序、前端资源和独立 Python 运行时组成。普通用户不需要安装 Node.js、Rust 或 Python；Windows 使用系统 WebView2，macOS 使用系统 WebKit。

## 目录结构

```text
wandao_electron/
├── package.json
├── scripts/
│   └── prepare_python_runtime.py
├── runtime/
│   └── python-runtime/
├── assets/
├── renderer/
│   ├── index.html
│   ├── styles.css
│   ├── tauri_bridge.js
│   └── app.js
└── src-tauri/
    ├── Cargo.toml
    ├── tauri.conf.json
    ├── build.rs
    └── src/
        ├── lib.rs
        ├── commands.rs
        └── tasks.rs
```
