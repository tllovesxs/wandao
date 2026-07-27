# Wandao Release Notes

## 1.3.0

- 所有十个平台能力已统一迁移到 Plugin v1：官方插件随桌面端离线提供，在线签名包可覆盖、回滚或卸载。
- 任务结果升级为严格的 TaskResult v1，包含稳定的 run/job/retry lineage；没有合法结果帧的成功退出不再被当作成功。
- 浏览器、checkpoint、凭证、日志和报告收敛到 `wandao_core`，并保留兼容导入入口。
- 插件子进程凭证从命令行移至临时环境变量，环境白名单、窗口安全边界、进程树停止和关闭确认得到加强。
- CI 增加跨系统质量矩阵、安全扫描、依赖更新、打包后资源 smoke、SBOM、校验和和 provenance。
- 平台中心增加前往插件中心的入口；插件中心支持搜索、稳定版与实验版标识，并默认展示实验插件。
- 插件发布策略改为默认实验版，稳定版须由维护者显式批准；正式与实验注册表分开发布。

## 1.2.9

本次版本重点完成在线插件共创机制收口，让新增平台可以作为独立签名插件接入、发布和更新。

- 新增 Plugin v1 在线插件机制：支持签名插件包、在线注册表、安装、更新、停用、回滚和卸载。
- 新增插件发布流水线：PR 会校验插件结构并生成预览包，合并到 main 后自动发布正式签名插件和 registry。
- 为知笔记迁出为首个标准 UI 插件，飞书导出/导入迁出为复杂插件实验。
- 共创文档、PR 模板、Issue 模板和 AI 提示词统一改为 `plugins/<id>` 插件开发模式。
- Provider v1 保留为兼容协议和插件内部 entrypoint，不再作为新增平台首选入口。
- 版本号升级到 `1.2.9`。
## 1.2.3

本次版本补齐正式发版流水线，并同步版本号。

- 桌面端和 Python 项目版本升级到 `1.2.3`。
- GitHub Actions 支持推送 `v*` tag 后自动构建 Windows/macOS 安装包。
- 构建完成后自动创建 GitHub Release 并上传安装包产物。
- 普通 `main` 分支推送仍只跑质量检查，不自动发布。

## 1.2.2

本次版本重点补强稳定性和共创质量底座。

- 新增统一结构化日志模块，桌面端可记录任务、文档、资源和失败事件。
- 错误报告会自动脱敏常见 Token、Cookie、Signature、Authorization 等敏感字段。
- 文件型 provider 打包后可稳定引用内置日志模块。
- 新增 Provider 与教程公告配置校验器：`python scripts/validate_providers.py`。
- 新增统一质量检查入口：`python scripts/quality_check.py`。
- CI 新增 Quality Gate，PR、main 分支和 tag 都会先跑质量检查。
- 精简旧 provider 模板，保留标准模板、复杂模板和本地 Markdown 示例。
- Provider v1 契约稳定：`schemaVersion: 1` 将作为当前共创插件协议，后续小版本保持向后兼容。
- 新增 `providers/provider.schema.json`，为 `provider.json` 提供机器可读 Schema 和编辑器提示。
- 新增标准导入 Provider 模板，演示扫描本地 Markdown、复制资源和生成导入报告。
- 任务中心新增统一报告归一化层，不同平台的统计和失败项会先收敛为同一套视图。
- 内置高频平台最终报告接入统一收口，任务中心能更稳定识别总数、成功数、失败数和资源失败。
- 任务报告 Markdown 生成收敛到 `task_report.js`，复制报告逻辑更容易维护。
- 任务历史卡片支持失败预览，并可直接打开输出目录或报告文件。
- 任务历史支持单独复制失败项；不支持重试的平台会给出原因提示。
- 任务中心支持 Provider 声明式失败重试；语雀导入会优先只重试上次报告中的失败项。
- Provider 校验器会检查失败重试声明，避免共创插件声明了能力但缺少实际重试参数。
- 标准导出和标准导入 Provider 模板都已加入烟测，降低社区共创新平台的接入风险。
- 渲染端结构化日志拆出为独立模块，减少主 UI 文件复杂度。
- Provider 信任标签和执行确认规则收敛到独立模块，非官方和本地 Provider 执行脚本前会展示提醒并要求确认。
- 浏览器调试端口不可用时，会提示端口占用、浏览器选择和重试建议。
- 用户日志/详细日志切换改为限量批量渲染，长任务产生大量日志后切换不再明显卡顿。
- 本地设置加入 schema 版本，为后续覆盖安装和配置迁移保留兼容入口。

## 1.2.0

本次版本是一次架构升级，重点是让万能导更适合开源共创：新增文件型 provider 插件机制，支持教程型平台、混合型平台和社区自动化脚本。

## 新增

- 新增 `providers/` 插件目录。
- 新增文件型 provider manifest：`provider.json`。
- 新增教程型 provider：平台可以只提供 `README.md`，不必写脚本。
- 新增混合型 provider：同一个平台可以同时展示教程和自动化动作。
- 新增通用目录树协议：社区 provider 可返回标准 `nodes`，由 UI 自动渲染勾选树。
- 新增动态动作回填：动作结果可自动更新输入框或下拉框。
- 新增 provider 依赖声明：可展示 Python、系统和使用依赖。
- 新增 provider 信任等级：官方、社区、本地、实验等来源会在界面展示。
- 新增平台中心入口，平台能力按卡片集中展示。
- 新增 Notion 迁移指南示例 provider。
- 新增社区插件模板：`providers/_template_standard/` 和 `providers/_template_custom/`。
- 新增插件开发文档：`docs/插件开发指南.md`。
- 应用头部新增万能导 Logo 展示，品牌识别更清晰。

## 架构改进

- Electron 主进程支持自动发现 `providers/*/provider.json`。
- 打包后会把 `providers/` 一起放入应用资源。
- 渲染进程支持从 manifest 自动生成表单字段和动作按钮。
- provider 字段支持 `text`、`password`、`number`、`textarea`、`directory`、`file`、`checkbox`、`select`、`notice`。
- provider 动作支持 `kind: "scan"`，用于读取目录并自动填充目录选择器。
- provider 动作支持 `updates`，用于把脚本返回值写回表单。
- 社区插件脚本使用 `provider:<id>:<script>` 形式执行，并限制脚本只能位于自己的 provider 目录内。
- 保留现有内置 provider 和专属复杂 UI，避免影响已有平台。

## 文档

- 重写 `docs/Provider接入说明.md`，说明内置 provider 与文件型 provider 的关系。
- README 新增插件开发入口。
- README 新增平台中心和插件开发入口说明。
- README 和使用教程补充 OneNote 导出、为知笔记导出说明。
- 贡献指南补充 AGPL 协议说明和 provider PR 规则。

## 协议

- 项目开源协议调整为 AGPL-3.0-only。
- Python 项目元数据、Electron 项目元数据和 LICENSE 文件已同步更新。

## 版本

- 桌面端版本升级到 `1.2.0`。
- Python 项目版本升级到 `1.2.0`。

## 注意

- 社区 provider 的 Python 脚本会在用户本机执行，请只安装可信来源的插件。
- 教程型 provider 不执行脚本，只展示 Markdown 操作说明。
- 复杂平台仍可继续使用内置专属模板，不强制所有平台使用统一表单。
