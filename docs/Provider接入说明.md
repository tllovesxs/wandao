# Provider 接入说明

万能导的桌面端已经开始按 provider 方式组织平台能力。一个 provider 代表一个平台或一个平台下的导入/导出方向，例如“语雀导出”“语雀 Markdown 导入”是两个 provider。

## Provider 放在哪里

桌面端 provider 注册在：

```text
wandao_electron/renderer/providers.js
```

新增平台时，优先在这里注册基础信息：

```js
{
  id: 'demo-export',
  platform: 'demo',
  navLabel: '示例平台导出',
  title: '示例平台导出',
  description: '将示例平台内容导出为 Markdown',
  script: 'export_demo.py',
  urlParam: '--entry-url',
  outputParam: '--output',
  defaults: { output: 'exports/demo' },
  capabilities: {
    login: true,
    scanToc: true,
    export: true,
    import: false,
    stop: true,
    report: true
  }
}
```

## 统一能力

provider 目前统一描述这些能力：

- `login`：是否需要登录并保存凭证。
- `scanToc`：是否支持读取目录并勾选部分内容。
- `export`：是否是导出任务。
- `import`：是否是导入任务。
- `stop`：是否支持停止当前任务。
- `report`：是否进入统一错误报告。

桌面端侧边栏会根据 provider 自动生成，不再需要手动在 HTML 里增加导航按钮。

## 简单平台接入

如果平台是常规“URL + 输出目录 + Python 脚本”的模式，只需要：

1. 写好对应 Python 脚本，例如 `export_demo.py`。
2. 在 `providers.js` 注册 provider。
3. 确认脚本支持统一参数，例如 `--entry-url`、`--output`、`--login`、`--scan-toc`。

没有专属 HTML 模板时，桌面端会自动生成一个基础表单。

## 复杂平台接入

如果平台需要特殊字段，例如 API Key、App Secret、目标知识库下拉选择、文件夹选择等，可以继续使用专属模板：

```text
template-demo-export
```

模板 ID 默认是：

```text
template-${provider.id}
```

专属模板只负责特殊表单，脚本调用、日志、停止、错误报告仍尽量走统一能力。

## 后续演进

后续可以继续把 Python 侧也整理成 provider 结构，让每个平台实现统一接口：

- `login`
- `scan_toc`
- `export`
- `import`
- `stop`
- `report`

这样新增平台时，只需要新增一个 provider 文件，而不是同时修改多处 UI 和调度逻辑。
