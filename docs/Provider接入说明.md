# Provider 接入说明

> Provider v1 仍然向后兼容，但新平台建议放入 `plugins/<id>`，通过在线插件库独立安装和更新。完整流程见 [在线插件开发与发布](在线插件开发与发布.md)。

万能导支持两类平台接入方式：

- 内置 provider：由主程序维护专属 UI 和专属逻辑，适合飞书导入、语雀导入这类复杂长期功能。
- 文件型 provider：放在 `providers/<provider-id>/provider.json`，适合社区共创、教程型平台、实验平台和大多数标准导入导出流程。

详细开发规范见：

```text
docs/共创流程.md
docs/插件开发指南.md
providers/provider.schema.json
```

## Provider v1 稳定契约

从当前版本开始，文件型 Provider 使用 `schemaVersion: 1`，也就是 Provider v1 契约。贡献者按 v1 编写的 provider，后续小版本会保持向后兼容。

稳定承诺：

- `provider.json` 的核心字段、字段类型、动作协议、目录树协议、日志协议和最终报告字段保持兼容。
- 新增能力优先增加可选字段，不删除或改变已有字段含义。
- 主程序会忽略未知扩展字段，方便社区先声明平台特有信息。
- 如果未来必须做破坏性调整，会新增 `schemaVersion: 2`，不会让 v1 provider 静默失效。
- 贡献者不需要修改 Electron 主程序，也不需要注入前端代码，就可以接入大多数新平台。

机器可读 Schema 位于：

```text
providers/provider.schema.json
```

模板里的 `"$schema": "../provider.schema.json"` 可以让编辑器更容易提示字段，也方便维护者检查协议是否漂移。

## 目录约定

```text
providers/
  _template_standard/
  _template_import/
  _template_custom/
  _demo_local_export/
  notion/
  your-provider/
```

以下划线开头的目录不会自动加载，用来放模板、示例和草稿。真正要展示给用户的平台目录不要以下划线开头。

## 文件型 provider 三件套

```text
providers/your-provider/
  provider.json
  README.md
  actions.py
```

- `provider.json`：声明平台信息、能力、字段、按钮、目录树协议和脚本入口。
- `README.md`：展示教程、限制、登录方式、权限要求和测试结果。
- `actions.py`：可选，执行读取目录、导出、导入、失败重试等动作。

如果平台只需要教程，可以只有 `provider.json` 和 `README.md`，并把 `type` 设置为 `guide`。

## 标准 UI 和复杂 UI

标准 UI provider 不需要改 Electron 主程序。贡献者只要声明 `fields` 和 `actions`，主程序会自动生成表单和按钮。

复杂平台可以先用 `providers/_template_custom/` 把流程拆成多个动作。当前文件型 provider 不直接注入任意 HTML；如果确实需要专属 UI，请在 PR 中说明 UI 需求，由维护者评估是否升级为内置 provider 或后续沙箱自定义 UI。

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

新增平台优先走文件型 provider：

1. 先搜索已有 Issue/PR，避免重复共创。
2. 没有重复时，使用“新平台共创/认领”Issue 模板提交需求或认领。
3. 平台本身有导出导入能力：先做教程型 provider。
4. 标准导出平台：用 `_template_standard`。
5. 标准导入平台：用 `_template_import`。
6. 平台流程很复杂：用 `_template_custom` 提交核心脚本和流程说明。
7. 标准 UI 不够：在 PR 中说明需要的专属 UI，不要直接把复杂逻辑散落到主程序里。

这样平台能力会集中在自己的目录中，后续维护、审查、回滚和共创都会更清楚。

## 提交前校验

新增或修改文件型 provider 后，请在项目根目录运行：

```powershell
python scripts\validate_providers.py
```

这个校验会检查 `provider.json`、脚本路径、教程路径、字段、动作、目录树协议和公告索引。它只约束安全边界和基本结构，不会限制平台必须使用固定流程。
