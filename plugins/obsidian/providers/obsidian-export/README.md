# Obsidian 黑曜石 Vault 归档导出

将 Obsidian Vault 目录批量归档为可离线阅读的本地 Markdown 目录，保留原目录层级，自动识别并复制 Wiki 链接和 Markdown 相对路径引用的图片与附件。

## 使用步骤

1. 进入"平台中心"，选择"Obsidian 黑曜石"。
2. 在"Obsidian Vault 目录"中选择你的 Vault 根目录。
3. 点击"读取目录"，查看 Vault 内的 Markdown 文件结构。
4. 勾选要导出的文件或文件夹。
5. 选择"输出目录"，点击"开始导出"。

## 支持的引用格式

**Wiki 嵌入链接（图片/附件）：**
- `![[image.png]]` — 嵌入图片
- `![[subdir/image.png]]` — 嵌入子目录图片
- `![[image.png|300]]` — 带尺寸的嵌入

**Markdown 链接：**
- `[文本](relative/path.png)` — 相对路径引用
- `[文本](attachments/file.pdf)` — 附件目录引用

外部 URL（`https://...`）不会被下载，保持原样。

## 资源处理策略

1. **解析**：按源文件目录 → Vault 根目录 → 文件名索引的顺序查找资源。
2. **复制**：资源按 Vault 内相对路径复制到 `_resources/` 子目录中，确保不同目录同名文件不会互相覆盖。例如 `attachments/vault-logo.png` 和 `notes/screenshot.png` 分别复制到 `_resources/attachments/vault-logo.png` 和 `_resources/notes/screenshot.png`。
3. **引用重写**：导出后的 Markdown 中，所有引用会被重写为正确的相对路径，指向实际复制的资源位置。Wiki 嵌入语法（`![[...]]`）会被转换为标准 Markdown 图片语法（`![](...)`）以保证跨工具兼容性。

## 安全约束

- `--doc-id` 参数和资源路径均经过解析验证，拒绝 `..` 路径遍历。
- 拒绝符号链接逃逸 Vault 目录。
- **拒绝输出目录位于 Vault 内部**，避免后续扫描将导出结果再次作为源内容。
- 脚本不修改源 Vault。

## 已知限制

- 第一版仅支持本地 Vault 只读归档，不写回源 Vault。
- 不支持 `.canvas`（画布）文件的资源引用解析。
- 不支持 Dataview、Templater 等动态插件生成的内容。
- 不支持 Obsidian 专属语法（Callout 等）的格式转换，原始 Markdown 保持不变。
- 暂不支持断点续跑和失败重试，后续版本将补充。

## 权限说明

- `filesystem:read`：扫描 Vault 目录，读取 Markdown 和资源文件。
- `filesystem:write`：将归档写入输出目录。

本插件不访问网络，不保存凭证，仅操作本地文件系统。

## 测试结果

使用最小样例 Vault（3 个 .md 文件、图片/附件）测试通过：
- 扫描 TOC 正确识别所有 Markdown 文件与文件夹层级。
- 导出后目录结构完整，引用均被重写为正确的相对路径。
- 不同目录同名资源不冲突。
- 路径遍历和输出目录位于 Vault 内的操作被正确拒绝。
- 未找到的资源在报告中列出。
