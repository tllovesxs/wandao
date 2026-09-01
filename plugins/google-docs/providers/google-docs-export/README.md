# Google Docs 导出使用说明

## 1. 准备浏览器会话

点击“登录并保存会话”。插件会启动自己的浏览器 profile，用户在其中完成 Google 登录。插件不读取或复制日常 Chrome 的 Cookie。

公开文档可以直接导出，不一定需要先登录。

## 2. 填写文档链接

只支持单篇文档链接，例如：

```text
https://docs.google.com/document/d/文档ID/edit
```

不支持 Sheets、Slides、Drive 文件夹和任意外部附件下载地址。

## 3. 先读取文档

“读取文档”只建立最小节点：

```text
root
└── 当前 Google Docs 文档
```

它不会生成最终 Markdown 文件。

## 4. 开始导出

导出会尝试生成：

```text
<输出目录>/<文档标题>/
├── <文档标题>.md
├── assets/
├── attachments/
└── 00-导出报告.json
```

Markdown 中的图片必须引用 `assets/` 下的真实文件，附件必须引用 `attachments/` 下的真实文件。资源下载失败会写入 TaskResult v1 的 `resourceFailures`，不会伪造本地路径。

## 5. 权限和限制

请只导出当前账号有权访问的内容。遇到 Google 登录、访问请求、安全验证或禁止下载/复制时，请按页面提示人工处理；插件不会绕过这些限制。

第一版不提供 OAuth、Cookie 导入、批量、评论、修订历史、checkpoint 或失败重试。