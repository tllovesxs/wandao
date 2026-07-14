# 语雀导出诊断与停止状态修复设计

**日期：** 2026-07-14
**范围：** 语雀任意知识库导出（`yuque`）

## 目标

修复一次语雀导出任务中已确认的三条结果链路：

1. 当脚本报告 `stopped: true` 时，Electron 必须显示“已停止”，不能显示“导出完成”。
2. 语雀文档详情 API 未返回 `data` 时，导出器必须产生安全且可诊断的接口错误，不能在页面上下文中因读取 `j.data.content` 崩溃。
3. 图片和附件的逐项下载失败详情必须从 Python 最终结果传到 Electron 任务报告，保留所属文档、资源类型、URL 和失败原因。

## 已验证的根因

- `plugins/yuque/backend/export_yuque.py` 无论 `report['stopped']` 是否为真，主函数都返回 `0`；Electron 将它当成功执行。
- 同文件的详情 API 页面表达式直接读取 `j.data.content`，未检查 HTTP 状态、JSON 结构或 `data` 是否存在。
- 导出报告本身已有 `resourceFailures`、`imageFailures`、`attachmentFailures`、`failures`、`reportFile` 等字段，但 CLI 最终 stdout 白名单只输出计数，导致任务报告只能显示“没有逐项原因”。

## 方案

### 后端

- 在页面表达式内先保存 `Response` 和安全解析后的 JSON；若 HTTP 非成功或没有 `data`，返回 `apiError`，其中只含 status、statusText、平台 code/message、顶层字段名和 `dataPresent`。不记录 Cookie、Authorization 或正文。
- Python 收到 `apiError` 后抛出 `ExportError`，错误文本包含文档标题、接口状态与平台消息，便于 UI 分类和用户反馈。
- 新增用于 CLI 终态输出的报告字段集合，保留文档失败、资源失败及报告文件路径。导出停止时主函数仍打印 JSON，再返回 `130`。

### Electron

- 统一将 `result.code === 130` 或 `result.data.stopped === true` 视作停止结果。
- 在任务历史中停止状态优先于成功状态；结果数据仍被保存，以便显示本次已导出、跳过和资源警告。
- 现有 `task_report.js` 已能解析资源失败列表；将由后端完整字段触发其现有展示逻辑。

## 非目标

- 不新增无限重试，不伪造请求头或认证信息。
- 外部资源的 403、404、302 仍作为资源下载警告，不误报为本地路径问题。
- 不修改已安装发布版目录。

## 验收标准

- `stopped: true` 的语雀 CLI 终态退出码为 `130`，且 stdout 包含结构化结果。
- 缺失 `data` 的详情接口不会抛出 `undefined.content`，错误包含安全的 API 诊断。
- CLI stdout 中包含资源失败列表；任务报告能列出对应 URL 与失败原因。
- Electron 任务状态和进度文案将停止优先于成功。
