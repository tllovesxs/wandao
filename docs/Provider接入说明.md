# Provider v1 兼容接入说明

> 新平台默认使用 `plugins/<plugin-id>` 在线插件结构，完整流程见 [在线插件开发与发布](在线插件开发与发布.md)。本文说明的是 Provider v1 协议：它仍然是插件内部描述平台页面、字段、动作、目录和能力的稳定契约，也兼容旧 `providers/<id>` 文件型 Provider。

## 当前推荐结构

万能导现在采用两层结构：

- Plugin v1：负责安装、签名、版本、权限、更新、回滚和卸载。
- Provider v1：负责描述平台能力、字段、按钮、目录树、教程和脚本动作。

新增平台建议放在：

```text
plugins/<plugin-id>/
  plugin.json
  backend/
  providers/
    <provider-id>/provider.json
    <provider-id>/README.md
```

旧文件型 Provider 目录仍可被加载：

```text
providers/<provider-id>/provider.json
```

但它主要用于历史功能维护、迁移参考和小范围修复，不再作为新平台首选入口。新平台使用插件结构后，用户可以在插件中心按需安装、更新、停用和回滚。

## Provider v1 稳定契约

Provider v1 使用 `schemaVersion: 1`。贡献者按 v1 编写的 provider，后续小版本会保持向后兼容。

稳定承诺：

- `provider.json` 的核心字段、字段类型、动作协议、目录树协议、日志协议和最终报告字段保持兼容。
- 新增能力优先增加可选字段，不删除或改变已有字段含义。
- 主程序会忽略未知扩展字段，方便社区先声明平台特有信息。
- 如果未来必须做破坏性调整，会新增 `schemaVersion: 2`，不会让 v1 provider 静默失效。
- 标准 UI Provider 不需要修改 Electron 主程序，也不需要注入前端代码。

机器可读 Schema 位于：

```text
providers/provider.schema.json
```

插件内 Provider 也遵守同一份 schema。模板里的 `"$schema"` 可以让编辑器更容易提示字段，也方便维护者检查协议是否漂移。

## Provider 三件套

插件内 Provider 通常包含：

```text
plugins/<plugin-id>/providers/<provider-id>/
  provider.json
  README.md
```

如果需要执行脚本，脚本建议放到插件的 `backend/` 目录，并在 `provider.json` 的 `actions` 中引用：

```text
plugins/<plugin-id>/backend/actions.py
```

- `provider.json`：声明平台信息、能力、字段、按钮、目录树协议和脚本入口。
- `README.md`：展示教程、限制、登录方式、权限要求和测试结果。
- `backend/*.py`：可选，执行读取目录、导出、导入、失败重试等动作。

如果平台只需要教程，可以只有 `provider.json` 和 `README.md`，并把 `type` 设置为 `guide`。

## 标准 UI 和复杂 UI

标准 UI Provider 不需要改 Electron 主程序。贡献者只要声明 `fields` 和 `actions`，主程序会自动生成表单和按钮。

复杂平台可以在同一个插件中拆成多个 Provider，例如 `example-export`、`example-import`、`example-setup`。如果标准 UI 仍然不够，可以在 Plugin v1 中声明沙箱自定义 UI。自定义 UI 没有 Node 权限，不能直接读写本地文件，只能通过宿主允许的 `postMessage` 能力执行已声明动作。

这个设计不是要求所有平台长得一样，而是让每个平台只声明自己支持的能力。飞书可以有权限检测和 Wiki 导入，OneNote 可以只有本地读取和 Markdown 导出，教程型平台也可以只展示文档。

## 已支持的扩展点

- `trustLevel`：标记官方、社区、本地、实验 provider。
- `status`：标记 experimental、beta、stable。
- `requirements`：声明 Python、系统和使用依赖。
- `capabilities`：声明导出、导入、教程、图片、附件、目录树、批量、重试等能力。
- `retryFailures`：当 `capabilities.retryFailures` 为 `true` 时，声明“只重试失败项”的脚本参数，例如 `{ "arg": "--retry-failures" }`。
- `toc`：声明通用目录树字段映射，支持读取目录后勾选。
- `actions.updates`：动作完成后把结果回填到输入框或下拉框。

## 贡献建议

新增平台优先走在线插件：

1. 先搜索已有 Issue/PR，避免重复共创。
2. 没有重复时，使用“新平台插件共创/认领”Issue 模板提交需求或认领。
3. 创建 `plugins/<plugin-id>/plugin.json`，声明入口、权限和版本。
4. 在插件内创建一个或多个 Provider v1。
5. 平台本身有导出导入能力：先做教程型插件。
6. 标准 UI 足够：用 `fields`、`actions` 和 `toc` 自动生成界面。
7. 标准 UI 不够：在插件内声明沙箱自定义 UI，不要把复杂逻辑散落到主程序里。

这样平台能力会集中在自己的插件目录中，后续维护、审查、回滚和共创都会更清楚。

## 提交前校验

新增或修改在线插件后，请运行：

```powershell
node scripts\validate_plugins.js
node --test tests_js/plugin_manager.test.js
python scripts\quality_check.py
```

只维护旧 `providers/` 兼容目录时，可以运行：

```powershell
python scripts\validate_providers.py
```

Provider 校验会检查 `provider.json`、脚本路径、教程路径、字段、动作、目录树协议和公告索引。它只约束安全边界和基本结构，不会限制平台必须使用固定流程。
