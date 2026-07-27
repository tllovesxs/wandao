# 文案批次实施报告

唯一改动文件：`wandao_electron/renderer/app.js`（18 处字符串字面量内容替换，总行数 6782 → 6782 未变）。

## 已实施

| 编号 | 原文案 | 新文案 | 定位方式 |
| --- | --- | --- | --- |
| B5 | `title: '目标平台 API 权限不足'`（第 6 条规则，与同条 `category` 完全相同） | `title: '当前应用还没有拿到这个接口的授权'` | 在 `ERROR_RULES` 内按整行 `    title: '目标平台 API 权限不足',` 唯一匹配（`category:` 行前缀不同，不会误伤） |
| B5 | `title: '请求过快或平台限流'`（第 9 条规则，与同条 `category` 完全相同） | `title: '短时间内请求太多，被平台临时拦截'` | 同上，整行 `    title: '请求过快或平台限流',` 唯一匹配 |
| B5 | `title: '页面结构可能变化'`（第 11 条规则，与 `category: '页面结构变化'` 仅差“可能”二字） | `title: '自动化没有在页面上找到预期的元素'` | 整行 `    title: '页面结构可能变化',` 唯一匹配 |
| F8 | `throw new Error(result?.error \|\| '????????')` | `throw new Error(result?.error \|\| '教程图片读取失败')` | grep 确认基线中仍存在（`hydrateGuideImages` 内，唯一一处 8 连问号字面量），按 `result?.error \|\| '????????'` 匹配 |
| F9 | `` `加载社区 provider 失败：${...}` `` | `` `加载社区平台插件失败：${...}` `` | 按模板串前缀 `` `加载社区 provider 失败：`` 匹配 |
| F9 | `有 ${manifestErrors.length} 个本地 Provider 配置无效，已安全忽略。` | `…个本地平台配置无效，已安全忽略。` | 按 `个本地 Provider 配置无效，已安全忽略。` 匹配 |
| F9 | `<p>这个 Provider 来自${escapeHtml(source)}，…` | `<p>这个平台插件来自${escapeHtml(source)}，…` | 按 `<p>这个 Provider 来自${escapeHtml(source)}` 匹配 |
| F9 | `<p>这个 provider 声明了额外依赖。…` | `<p>这个平台插件声明了额外依赖。…` | 按 `<p>这个 provider 声明了额外依赖。` 匹配 |
| F9 | `'# 暂无教程\n\n这个 provider 还没有提供 README.md。'` | `'# 暂无教程\n\n这个平台还没有提供教程文档。'` | 只替换 `这个 provider 还没有提供 README.md。` 子串，`# 暂无教程\n\n` 原样保留 |
| F9 | `alert('这个动作没有配置脚本，可能只是教程型 provider。')` | `alert('这个动作没有配置脚本，可能只是纯教程型平台。')` | 按整个字面量匹配 |
| F9 | `'正在执行 provider 动作...'` | `'正在执行平台动作...'` | 按整个字面量匹配 |
| F9 | `` `未找到平台 provider：${currentTool}` `` | `` `未找到这个平台：${currentTool}` `` | 按整个模板串匹配 |
| F9 | `` `ima Provider 未提供脚本：${prefix}` `` | `` `ima 平台未提供脚本：${prefix}` `` | 按整个模板串匹配 |
| F9 | `'ima 导入 Provider 未提供脚本'` | `'ima 导入未提供脚本'` | 按整个字面量匹配 |
| F9 | `'语雀导入 Provider 未提供脚本'` | `'语雀导入未提供脚本'` | 按整个字面量匹配 |
| F9 | `'印象笔记导出 Provider 未提供凭证初始化脚本'` | `'印象笔记导出未提供凭证初始化脚本'` | 按整个字面量匹配 |
| F9 | `'印象笔记导入 Provider 未提供脚本'` | `'印象笔记导入未提供脚本'` | 按整个字面量匹配 |
| F9 | `'飞书导入 Provider 未提供脚本'` | `'飞书导入未提供脚本'` | 按整个字面量匹配 |

补充说明：

- 每处替换在打补丁前都做了「命中次数必须恰好为 1」的断言，任一处不满足即整体中止，避免误伤同名文本。
- 公告文案里的 `Provider v1`（3 处）按方案要求保留，属对外规范专有名词。
- 全程未改动变量名、函数名、控制流、正则表达式；`diff` 显示 18 行改动全部落在字符串字面量内部，无增删行。

## 删除的规则（如有）

无。

方案 B5 的「副作用」段建议整条删除第 12 条规则（`category: '图片或附件下载失败'` / `title: '图片或附件处理失败'`），理由是「第 12 条永远匹配不到，因为第 3 条的 `图片|附件` 已覆盖」。**核查后未采纳，因为该不可达判断与基线代码事实相反：**

- 广义的 `图片|附件|image|attachment|resource|下载失败` 这些宽泛分支在**第 12 条**里，不在第 3 条里；
- 第 3 条的 pattern 反而是**窄**的：`图片下载失败|附件下载失败|download.*image|image.*download|tcs-devops\.aliyuncs\.com|cdn\.nlark\.com|图片.*HTTP 40[134]|HTTP 40[134].*图片|imageFailure|imageFailures`；
- 因此第 3 条无法覆盖第 12 条。实测（按 `classifyError` 的首次命中语义逐条跑 `ERROR_RULES`）以下消息全部落到**第 12 条**，且不被第 1–11 条任何一条拦截：
  `上传附件失败` / `图片处理异常` / `attachment error` / `resource missing` / `下载失败` / `image broken` / `download failed`。

删掉第 12 条会让这些消息穿透到 `classifyError` 的兜底分支（`未知错误 / 任务执行失败`），属于**分类行为变更**，违反“纯文案、不改逻辑”的前提，故保留。第 3 条与第 12 条 `category`+`title` 组合完全重复这一现象属实，但那是重复而非不可达，需要另开一条带回归测试的行为项处理。

## 已跳过

| 编号 | 原因 |
| --- | --- |
| B5 · `formatUserError` 兜底 | 方案建议加 `const head = rule.title.startsWith(rule.category) ? rule.title : ...` 三元判断。这是新增控制流，不属于“只改字符串字面量内容”，按铁律跳过。三条 `title` 改完后 `category !== title`，输出已不再重复，兜底不影响本批次效果。 |
| B5 · 删除第 12 条规则 | 见上节：不可达前提不成立，删除会改变分类行为。 |
| F8 · `main.js:1922` / `:1925` | 方案同时点名 `wandao_electron/main.js` 的 `'Provider ID ????'`、`'?????? Provider ????'`。本批次只允许改 `renderer/app.js`，未触碰。 |
| F9 · `provider_runtime.js:51/52/56-57/62-63` | 同上，不在允许修改的文件范围内。 |
| F9 · `app.js` 中 `` `已加载 ${manifests.length} 个外部 Provider。插件版本：…` `` | 面向用户且含 “Provider”，但不在方案 F9 给出的位置清单里，按“不许顺手改进”未动。建议后续补入清单。 |
| F9 · `app.js` 中 `providerTypeLabel(provider) \|\| 'Provider'` | 该兜底字面量对应方案里 `provider_runtime.js:51-52` 那一项，不在 app.js 清单内且文件受限，未动。 |

## 自检

- `node --check wandao_electron/renderer/app.js`：**通过**（改动前基线通过，改动后同样通过）
- `python3 scripts/quality_check.py`：**passed**
  - Provider validation passed.
  - Python compile passed (134 files).
  - Node syntax check passed (25 files).
  - Git diff whitespace check passed.
  - `Quality check passed.`
- 附加校验：`diff` 对比改动前后，共 18 行变更、无增删行（6782 行不变）；`ERROR_RULES` 仍为 12 条，第 6/9/11 条已无 `category === title` 或近似重复。
