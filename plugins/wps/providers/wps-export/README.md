# WPS“我的云文档”原始文件导出

这个 Provider 只读取 WPS 网页中的 **“我的云文档”**，并把可下载的原始文件保存到本地。它不读取设备文档、我的设备、自动上传、团队空间或回收站。

## 使用方式

### 1. 登录

```powershell
python plugins/wps/backend/export_wps.py --login
```

命令会打开一个只供 WPS 使用的独立浏览器配置目录。请在浏览器中自行完成扫码或网页登录，确认页面回到“我的云文档”后按 Enter。插件只保存经过白名单过滤的最小认证状态。

不要把 Cookie、`wps_sid`、Token、CSRF 或 Authorization Header 粘贴到命令行、Issue、日志或报告中。插件不接受手工粘贴这些凭证。

### 2. 扫描目录

```powershell
python plugins/wps/backend/export_wps.py --scan-toc
```

扫描结果供 Wandao 的 Provider/TOC 选择器使用。扫描只通过只读 GET 请求读取个人云文档树，不执行修改、分享、删除或上传操作。

### 3. 导出原始文件

```powershell
python plugins/wps/backend/export_wps.py --output exports/wps
```

也可以重复 `--file-id` 只导出指定文件；在 Wandao 中通常由目录选择器自动传入：

```powershell
python plugins/wps/backend/export_wps.py --output exports/wps --file-id <file-id>
```

导出的文件会保留“我的云文档”下的父目录层级。目标文件已经存在时会跳过，不覆盖原文件。下载使用临时 `.part` 文件，完整写入后再原子替换。

### 4. 断点恢复和失败重试

启用 SQLite checkpoint：

```powershell
python plugins/wps/backend/export_wps.py `
  --output exports/wps `
  --checkpoint-file .wandao/wps-export.sqlite `
  --checkpoint-task-id wps-export
```

恢复同一个任务时继续使用相同的 checkpoint 文件和任务 ID；只重试失败项：

```powershell
python plugins/wps/backend/export_wps.py `
  --output exports/wps `
  --checkpoint-file .wandao/wps-export.sqlite `
  --checkpoint-task-id wps-export `
  --retry-failed
```

停止任务会保留已经完成的状态并清理当前临时文件。重新运行即可恢复；Wandao 任务中心停止任务时使用项目约定的退出码 130。

### 5. 导出报告

每次导出都会在输出目录生成 `00-导出报告.json`，格式为 TaskResult v1，包含成功、跳过、失败和停止状态。报告只记录本地输出路径和安全化后的错误文本，不记录 Cookie、Token、认证头、签名下载 URL、账号标识或远程请求查询参数值。

### 6. 清除登录状态

```powershell
python plugins/wps/backend/export_wps.py --clear-auth
```

这只删除 Wandao 为 WPS 保存的认证文件和独立浏览器 profile，不删除已经导出的文件。

## 常见错误

- **需要重新登录 / HTTP 401 或 403**：重新运行 `--login`，不要手工复制 Cookie。
- **请求过于频繁 / HTTP 429**：稍后重试；插件会读取安全的 `Retry-After` 数值并避免把响应内容写入报告。
- **文件已存在**：这是预期的跳过行为，不会覆盖本地文件。
- **路径名称不适合作为 Windows 文件名**：插件会安全化非法字符、保留名和路径穿越片段，并对冲突名称使用稳定短哈希消歧。
- **找不到浏览器**：请安装 Chrome、Edge 或 Chromium，或由 Wandao 配置自动化浏览器路径。

## 隐私边界

- 只支持 WPS“我的云文档”个人空间。
- 只允许显式的 WPS 官方 HTTPS API 主机和只读接口路径。
- 下载原始文件时不发送 WPS Cookie 或 Authorization Header；签名下载地址仅在内存中使用。
- 测试使用 fake 数据源和脱敏占位符，不连接真实账户，也不包含真实文件名、文件 ID 或认证数据。
