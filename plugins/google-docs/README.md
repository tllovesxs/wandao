# Google 文档插件

通过 Wandao 的独立浏览器 profile，导出一篇用户有权访问的 Google Docs 文档。

## 第一版支持

- `https://docs.google.com/document/d/<文档 ID>/edit` 单篇文档 URL；
- 用户在插件独立浏览器中手动登录，或访问公开文档；
- 文档标题、段落、基础标题、粗体、斜体、无序列表、有序列表和普通链接；
- Google Docs HTML 导出中能识别的图片资源，保存到 `assets/`；
- HTML 中明确声明为附件的 Google 资源，保存到 `attachments/`；
- 一个根节点和一个文档节点；
- 一个 Markdown 文件、报告文件、progress 和 TaskResult v1。

## 明确不支持

- Google Drive 文件夹遍历、多篇批量导出；
- Google Docs API OAuth、Cookie 导入；
- 评论、修订历史、复杂表格、视频、音频和复杂嵌入；
- checkpoint、断点续跑和失败重试；
- 自定义 UI；
- 绕过登录、访问权限或安全验证。

## 使用方式

1. 可选：点击“登录并保存会话”，在插件打开的独立浏览器中完成 Google 登录。
2. 填写一篇 Google Docs 文档链接。
3. 可选：点击“读取文档”检查标题和资源数量。
4. 选择输出目录并点击“开始导出”。
5. 导出结果位于输出目录下的文档目录中。

插件不会读取日常 Chrome profile，不复制 Cookie、Token 或密码。资源下载只允许 Google Docs/Drive 受信任资源地址；普通外部链接仍然保留为普通 Markdown 链接。