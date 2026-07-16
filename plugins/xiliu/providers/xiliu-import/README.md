# 息流导入插件

将本地 Markdown 文档导入到 FlowUs (息流) 空间。

## 使用步骤

1. 点击"登录并保存凭证"，在浏览器完成 FlowUs 登录后回到万能导确认。
2. 点击"列出可导入目标"，获取可写入的空间和页面 ID。
3. 填写本地 Markdown 目录，空间 ID 和目标页面 ID 可选（留空自动检测）。
4. 开始导入。

## 导入方式

使用 FlowUs Web API 进行导入：
1. 创建空页面（`POST /api/blocks/transactions`）
2. 将 Markdown 转换为 HTML 并上传为临时文件（`POST /api/import_temp_file`）
3. 创建导入任务（`POST /api/enqueueTask`）
4. 轮询任务结果（`POST /api/getTasks`）

## 权限要求

- **browser-automation**：启动 Chrome/Edge 浏览器进行登录。
- **credentials**：保存登录凭证。
- **network**：访问 FlowUs API。
- **process**：运行导入脚本。

## 已知限制

- 仅支持导入，不支持导出（导出请使用"息流导出"插件）。
- 本地图片会以 base64 数据内嵌到 HTML 中导入，不单独上传。
- 不支持附件导入。
- 目标空间需要有 editor 或 writer 权限。
- FlowUs API 可能有请求频率限制，建议保持默认延迟设置。

## 常见失败原因

| 错误 | 原因 | 解决方案 |
|------|------|----------|
| `FlowUs 登录已失效` | Token 过期 | 重新点击"登录并保存凭证" |
| `没有读取到 FlowUs 登录凭证` | 未登录 | 确保浏览器中已登录 FlowUs |
| `没有找到可写入的页面` | 未登录或无权限 | 先点击"列出可导入目标"确认有写入权限 |
| `源目录不存在` | 路径错误 | 检查本地 Markdown 目录路径 |
| `导入任务超时` | 网络问题或内容过大 | 增加超时时间或检查网络 |

## 许可证

AGPL-3.0-only
