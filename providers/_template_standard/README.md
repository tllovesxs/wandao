# 标准 UI Provider 模板

这个模板适合大多数平台：登录、填写几个参数、读取目录、勾选内容、导出或导入。

复制本目录后，把目录名改成你的平台 ID，例如：

```text
providers/your-provider/
```

注意：以下划线开头的目录不会被万能导自动加载。

## 你需要修改什么

- `provider.json`：改平台名称、能力、字段和动作。
- `README.md`：写清楚使用方法、登录方式、权限要求、已知限制和测试结果。
- `actions.py`：实现平台真实的读取目录、导出或导入逻辑。

## 标准 UI 能做什么

- 展示输入框、密码框、文件选择、目录选择、下拉框、复选框和提示说明。
- 点击按钮后执行 provider 目录内的 Python 脚本。
- 读取目录树后让用户勾选部分文档。
- 根据脚本返回结果回填下拉框或输入框。
- 记录日志、进度和最终 JSON 报告。

## 脚本输出约定

运行过程中可以输出普通日志。进度建议输出：

```text
progress 3/10 exported=3 skipped=0 failures=0
```

任务完成时，最后输出一段 JSON：

```json
{
  "provider": "your-provider",
  "mode": "export",
  "totalDocs": 10,
  "exportedDocs": 10,
  "skippedDocs": 0,
  "failureCount": 0,
  "failures": []
}
```

读取目录时返回：

```json
{
  "provider": "your-provider",
  "nodes": [
    {
      "nodeId": "folder:docs",
      "exportId": "",
      "title": "docs",
      "parentNodeId": "",
      "selectable": false
    },
    {
      "nodeId": "doc:docs/hello.md",
      "exportId": "docs/hello.md",
      "title": "hello",
      "parentNodeId": "folder:docs",
      "selectable": true
    }
  ]
}
```

## AI 辅助开发提示词

可以把下面这段发给 AI 编程工具：

```text
请参考 docs/插件开发指南.md 和 providers/_template_standard，把某某平台接入成万能导文件型 provider。
要求：
1. 不修改 Tauri 2 宿主，优先使用 provider.json 的标准 UI。
2. provider 放在 providers/平台ID/ 下。
3. 支持 README.md 使用教程、provider.json 能力声明、actions.py 脚本。
4. 脚本最后输出 JSON，进度输出 progress x/y。
5. 不提交 Cookie、Token、账号密码或私人测试数据。
```

## 提交 PR 前

- 确认 `provider.json` 是合法 JSON。
- 确认 `actions.py` 可以通过 `python -m py_compile`。
- README 写明是否支持目录结构、图片、附件、批量、断点续跑和失败重试。
- 至少说明一次真实测试结果；无法测试时说明原因。
