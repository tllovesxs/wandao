# 息流插件

将 FlowUs (息流) 文档导出为 Markdown 文件，保留目录结构。

## 使用步骤

1. 点击"登录并保存凭证"，在浏览器完成 FlowUs 登录后回到万能导确认。
2. 填写 FlowUs 文档 URL（例如 `https://flowus.cn/xxx`）。
3. 点击"读取目录"，查看文档结构。
4. 选择输出目录并开始导出。

## 登录方式

使用浏览器登录 FlowUs，插件会自动保存 JWT Token 和 Cookie 用于后续 API 请求。

登录凭证保存在插件数据目录，不会上传到任何服务器。

## 权限要求

- **browser-automation**：启动 Chrome/Edge 浏览器进行登录。
- **credentials**：保存登录凭证。
- **filesystem:write**：写入导出的 Markdown 文件。
- **network**：访问 FlowUs API。
- **process**：运行导出脚本。

## 已知限制

- 仅支持导出，不支持导入。
- 导出会下载文档中的图片到本地目录。
- 不支持附件导出。
- 依赖浏览器进行登录，需要本地安装 Chrome 或 Edge。
- FlowUs API 可能有请求频率限制，建议保持默认延迟设置。

## 测试结果

- 测试环境：Windows 11, Chrome 150
- 测试文档：包含多层目录的文档空间
- 导出结果：目录结构正确，Markdown 内容完整

## 常见失败原因

| 错误 | 原因 | 解决方案 |
|------|------|----------|
| `未找到 Chrome/Edge/Chromium` | 未安装浏览器 | 安装 Chrome 或 Edge，或在高级设置中指定浏览器路径 |
| `FlowUs 登录已失效` | Token 过期 | 重新点击"登录并保存凭证" |
| `没有读取到 FlowUs 登录凭证` | 未登录 | 确保浏览器中已登录 FlowUs |
| `无法从 URL 中提取文档 ID` | URL 格式错误 | 使用完整 URL，如 `https://flowus.cn/xxx` |

## 来源

本插件参考了 FlowUs Web API 的使用方式。

## 许可证

AGPL-3.0-only
