# 万能导共创通用提示词

如果你想用 AI 编程工具给万能导新增一个平台，可以直接复制下面的提示词。把括号内容换成你的平台信息即可。

## 标准插件提示词

适合大多数导入导出平台：填写参数、登录、读取目录、导出 Markdown、导入 Markdown。

```text
你现在要在开源项目“万能导 Wandao”中新增一个平台插件。

项目目标：
万能导用于把用户自己有权限访问的知识内容导出、导入或迁移到其他平台。新增平台必须遵守项目规范，不绕过登录、不绕过权限、不提交任何敏感凭证。

请先阅读并遵守这些文件：
1. docs/在线插件开发与发布.md
2. docs/共创流程.md
3. docs/Provider接入说明.md
4. CONTRIBUTING.md
5. plugins/wiz/plugin.json 和 plugins/wiz/providers/wiz/provider.json

我要接入的平台是：
- 平台名称：（填写平台名）
- 平台官网或入口 URL：（填写 URL）
- 我想实现的能力：（导出 Markdown / 导入 Markdown / 教程说明 / 图片处理 / 附件处理 / 目录结构）
- 登录方式：（不需要登录 / 浏览器登录 / Cookie / API Key / OAuth / 其他）
- 目标范围：（整个平台 / 单个空间 / 单个知识库 / 单个目录 / 单篇文章）
- 是否需要保留目录结构：（是 / 否）
- 是否需要处理图片和附件：（是 / 否）

实现要求：
1. 新平台优先使用 Plugin v1，放在 plugins/平台ID/ 目录下，不要修改 Electron 主程序。
2. 插件至少包含 plugin.json、providers/平台ID/provider.json 和 README.md。
3. 如果需要自动化能力，把 Python 脚本放到 plugins/平台ID/backend/ 下，并在 provider.json 的 actions 中引用。
4. plugin.json 必须声明 id、name、description、version、publisher、core.minVersion、entrypoints 和 permissions。
5. provider.json 必须声明 id、name、title、description、type、group、trustLevel、status、guide、capabilities、fields、actions。
6. 如果支持读取目录，使用通用 toc 协议，脚本返回 nodes 数组。
7. 脚本运行时输出 progress x/y，任务结束时最后输出 JSON。
8. README.md 必须写清楚使用步骤、登录方式、权限要求、已知限制、测试结果和常见失败原因。
9. 不要提交 Cookie、Token、账号密码、App Secret、私人知识库内容或未脱敏日志。
10. 如果参考第三方项目，请在 README.md 或 PR 说明中写清楚来源和许可证。
11. 完成后运行 node scripts/validate_plugins.js、node --test tests_js/plugin_manager.test.js 和 python scripts/quality_check.py。

请先分析这个平台适合做标准 UI 插件、复杂流程插件还是教程型插件，然后给出实现方案。确认方案后，再创建或修改对应文件。
```

## 复杂平台提示词

适合飞书导入、语雀导入这类流程很复杂的平台：需要权限检测、空间选择、图片补全、失败重试、专属报告或自定义 UI。

```text
你现在要为万能导 Wandao 接入一个复杂平台插件。这个插件可以包含多个 Provider，例如 export、import、setup、repair-images，也可以在标准 UI 不够时声明沙箱自定义 UI。

请先阅读并遵守这些文件：
1. docs/在线插件开发与发布.md
2. docs/Provider接入说明.md
3. plugins/feishu/plugin.json
4. plugins/feishu/providers/feishu-export/provider.json
5. plugins/feishu/providers/feishu-import/provider.json
6. CONTRIBUTING.md

平台信息：
- 平台名称：（填写平台名）
- 目标能力：（导出 / 导入 / 迁移 / 图片补全 / 权限检测 / 失败重试）
- 登录方式：（填写）
- 需要的步骤：（例如登录、读取空间、读取目录、选择范围、执行导出、生成报告）
- 最容易失败的地方：（例如权限不足、登录过期、图片上传失败、接口限流）

实现要求：
1. 平台目录放在 plugins/平台ID/，一个插件内可以声明多个 providers/*/provider.json。
2. 先不要改 Electron 主程序，优先把流程拆成 provider.json actions。
3. 如果标准 UI 不够，请在 plugin.json 中声明 ui/index.html，并确保自定义 UI 只通过 postMessage 调用宿主允许的能力。
4. Python 脚本必须可以独立运行，支持清晰的命令行参数。
5. 脚本必须输出 progress x/y，并在最后输出 JSON 报告。
6. 失败时要给出用户能理解的错误信息和下一步建议。
7. 保留目录结构、图片和附件；暂不支持的能力要在 capabilities 和 README.md 中如实说明。
8. 不提交任何敏感凭证、私人数据或未脱敏日志。
9. 不要导入其他平台插件的业务脚本，公共能力需要提取成稳定 SDK 后再复用。

请先输出功能拆分和文件结构，再开始实现。
```

## 教程型平台提示词

适合平台本身已经支持导出或导入，只需要把正确操作步骤展示给用户。

```text
请为万能导 Wandao 新增一个教程型平台插件。

平台名称：（填写平台名）
平台已有能力：（官方导出 / 官方导入 / 官方 Markdown 导出 / 其他）
我要写的教程内容：（填写）

要求：
1. 插件放在 plugins/平台ID/。
2. plugin.json 声明 entrypoints.providers。
3. provider.json 的 type 设置为 guide，group 设置为 guide。
4. README.md 写清楚操作步骤、推荐格式、图片和附件注意事项、权限要求和常见问题。
5. 不需要写 Python 脚本，除非确实有自动化能力。
6. 不提交任何私人数据、账号信息或未脱敏截图。

请参考 plugins/wiz/ 的目录结构完成这个教程型插件。
```

## 提交前自查提示词

完成后，可以再让 AI 按下面这段做一次自查：

```text
请按照万能导 Plugin v1 共创规范检查本次改动：
1. plugin.json 是否是合法 JSON，id 是否和 plugins/<id> 目录名一致。
2. plugin.json.version 是否已按改动类型提升。
3. entrypoints.providers 是否指向插件目录内的 provider.json。
4. permissions 是否最小化，是否真实反映网络、文件、凭证、浏览器自动化和进程执行需求。
5. provider.json 是否合法，capabilities 是否只声明已经实现的能力。
6. README.md 是否包含使用步骤、登录方式、权限要求、限制和测试结果。
7. Python 脚本是否能通过 python -m py_compile。
8. 脚本最后是否输出 JSON，过程中是否输出 progress x/y。
9. 是否保留目录结构、图片、附件；如果没有，是否已经说明。
10. 是否包含 Cookie、Token、账号密码、App Secret、私人数据或未脱敏日志。
11. 是否参考第三方项目，是否说明来源和许可证。
12. 是否已运行 node scripts/validate_plugins.js、node --test tests_js/plugin_manager.test.js 和 python scripts/quality_check.py。
```

## 给贡献者的小建议

先做最小可用版本，不要一开始追求“大而全”。能稳定跑通一个平台的导出或导入，再逐步补图片、附件、目录结构、失败重试和断点续跑，会更容易合并，也更容易维护。
