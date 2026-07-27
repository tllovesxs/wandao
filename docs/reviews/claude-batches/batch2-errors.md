# 批次二：错误分类规则（B1 / B2 / B3 / B4）

工作目录 `/tmp/w/b2-errors`。改动只落在 `wandao_electron/renderer/app.js` 与新建的
`tests_js/error_rules.test.js`，共 2 个文件、368 增 / 3 删。四条各自一次提交 + 一个 tag，
每个 tag 单独 checkout 后 `node --check` 与 `node --test tests_js/error_rules.test.js` 均为绿。

| 条目 | commit sha | tag | 测试数（累计） |
| --- | --- | --- | --- |
| B1 新增网络类错误规则 | `e2a7e8cbe7c19e1a9dac51114a826ced4dc4f610` | `sol/B1` | 7 |
| B2 收紧本地路径规则 + 远端内容不存在 | `daf1d4dce92ef87293155cf6ee476263d7df6964` | `sol/B2` | 12 |
| B3 原始摘要改为抓末尾异常行 | `261e468ba59f4487d48875d6d6cbbe62496f2e84` | `sol/B3` | 19 |
| B4 收紧 API 权限规则 | `79bc2f7db0d774ff40882c23f6db3ed84e040621` | `sol/B4` | 23 |

## 回归

基线 Node 127 / Python 535 / passed。四条做完后：

```
Ran 535 tests in 7.640s
# tests 150   # pass 150   # fail 0
Node syntax check passed (25 files, 27 test files).
Git diff whitespace check passed.
Quality check passed.
```

Node 从 127 涨到 150（新增 23 条），Python 535 不变。

## 改动后的 ERROR_RULES 顺序

```
1 本地文件路径问题(收紧,B2)  2 任务参数过长  3 图片或附件下载失败
4 远端内容不存在(新增,B2)     5 未登录或登录失效  6 浏览器自动化启动失败
7 网络连接失败(新增,B1)  8 网络超时(新增,B1)  9 DNS 解析失败(新增,B1)
10 HTTPS 证书或代理问题(新增,B1)
11 目标平台 API 权限不足(收紧,B4)  12 没有访问权限  13 平台额度或数量限制
14 请求过快或平台限流  15 任务参数不合适  16 页面结构变化  17 图片或附件下载失败
```

B5 相关的三处 title 与第 17 条（批次一核查后保留的重复规则）都没有动。

## 与方案 sol-frontend.md 的三处刻意偏离

1. **B1 网络块的位置**。方案要求放在 `ERROR_RULES` 最前面。实测这会抢走
   `wandao_core/browser.py:debug_port_error_message()` 生成的真实报错——它的正文是
   `无法连接浏览器调试端口 9222。\n[Errno 111] Connection refused\n...`，`Connection refused`
   会让"网络连接失败"先命中，把"Chrome 没起来"说成"检查你的网络"，方向反了。
   改为放在"浏览器自动化启动失败"之后。方案担心的 `ECONNREFUSED ... not found` 被文件规则吞掉，
   由 B2 移除裸 `not found` 解决（B2 是紧接着的下一个 commit）。
2. **B1 的 HTTPS 规则去掉裸 `407`**。方案里的 `407` 没有词边界，任何含 407 的数字（文档 ID、
   时间戳、条目计数）都会命中并提示用户去关代理。改为 `HTTP 407|Proxy Authentication Required`。
3. **B2"远端内容不存在"的位置**。方案要求排在第一条之前。收紧后的本地路径规则已经不会再吞
   404，所以不必排到最前面；排在"图片或附件下载失败"之后，可以让
   `图片下载失败：https://cdn.nlark.com/...：HTTP 404` 继续拿到"正文可能已导出、图片没本地化"
   这条更贴切的提示（这是改动前就已经分对的分类）。方案的 `Not Found\b` 与
   `invalid.*(?:token|node_token)` 两个分支也没有照搬：前者带 `/i` 会把
   `Chrome/Edge executable was not found` 抢走（正是 B2 想修的问题换个规则重演），
   后者会把 `invalid access token` 这类登录问题说成"内容不存在"。

另外 B2 没有照搬方案里删掉裸 `无法找到` 的写法，而是收紧成
`无法找到[^。\n]{0,6}(?:插件|脚本|文件|目录|路径)`：`main.js` 的
`无法找到内置插件：x` / `无法找到插件脚本：x` 确实是本地文件问题，必须继续命中；
而 `export_wiz.py` 的`无法找到或创建为知笔记网页标签页`不该再被判成路径问题。

## 测试覆盖清单（tests_js/error_rules.test.js，23 条）

### 正向（该命中的命中）

**B1（5 条）**
- 网络连接失败：`NewConnectionError ... Connection refused`、`connect ECONNREFUSED`、
  `read ECONNRESET`、`('Connection aborted.', RemoteDisconnected...)`、`EHOSTUNREACH`、
  `[WinError 10054] 远程主机强迫关闭了一个现有的连接`
- 网络超时：`ETIMEDOUT`、`ReadTimeout / Read timed out`、`ConnectTimeout`、
  Playwright `TimeoutError: page.waitForSelector: Timeout 30000ms exceeded`、
  `locator.click: Timeout 15000ms exceeded`、`接口请求超时`
- DNS：`getaddrinfo ENOTFOUND`、`EAI_AGAIN`、`Name or service not known`、
  `NameResolutionError`、`域名解析失败`
- HTTPS/代理：`SSLCertVerificationError / CERTIFICATE_VERIFY_FAILED`、`SSLError`、
  `UNABLE_TO_VERIFY_LEAF_SIGNATURE`、`ProxyError`、`net::ERR_PROXY_CONNECTION_FAILED`、
  `HTTP 407 Proxy Authentication Required`
- 逐个断言 7 个关键 token 不再落到兜底"未知错误"

**B2（2 条）**
- 远端内容不存在：`HTTP 404 Not Found`、`{"code":404,"msg":"not found"}`、`status=404`、
  `OneNote 页面不存在`、`目标帖子不存在、已删除`、`文档已删除`、`invalid node_token`、
  `无效的知识库链接`
- 该规则的 suggestion 不含"输入目录/输出目录/脚本文件"，且包含"链接/浏览器"

**B3（6 条）**
- `[前部 8213 个字符已省略，以下为输出尾部]` + 长进度输出 + Traceback + `ValueError: 具体原因`
  → 摘要出现 `ValueError: 具体原因`，且不含省略提示、不含 `Traceback (most recent call last)`
- 单行写法 `[前部 8213 个字符已省略] ... Traceback ... \nValueError: 具体原因`
  → `原始摘要：ValueError: 具体原因`
- 多次异常时取最后一次（`RuntimeError: 最后一次真正的原因` 覆盖 `ConnectionError: 第一次尝试失败`）
- 超长异常正文在 220 字处截断并以 `...` 结尾
- 没有异常行时退回尾部 3 行，仍不返回开头内容
- 短消息 / 空消息行为不变

**B4（1 条）**
- 真实权限报错仍命中：飞书 403 + `缺失权限：drive:drive, ...`、`建议权限：drive:file:upload`、
  `{"code":99991672}`、`missing required scopes`、`required scope`、`scopes required`、
  `tenant_access_token`、`app ticket`、`应用身份权限`、`API 权限`、`权限申请链接`
- 逐个断言 7 个真实飞书 scope（含两段式 `drive:drive`/`docx:document`/`wiki:wiki`）命中 pattern

### 反向（不该命中的不命中 / 原本命中的仍然命中）

**B1（2 条）**
- `无法连接浏览器调试端口 9222 / [Errno 111] Connection refused` → 仍是"浏览器自动化启动失败"
- `connect ECONNREFUSED 127.0.0.1:9222` → 仍是"浏览器自动化启动失败"
- `图片下载失败：https://cdn.nlark.com/...：Read timed out` → 仍是"图片或附件下载失败"
- `登录凭证已失效`→未登录；`HTTP 429 Too Many Requests`→限流；`Access denied: HTTP 403`→没有访问权限

**B2（3 条）**
- 14 个真实本地路径报错仍命中"本地文件路径问题"：`FileNotFoundError: [Errno 2] No such file or
  directory`、`ENOENT`、`python: can't open file`、`Markdown 目录不存在`、`测试 Markdown 文件不存在`、
  `Markdown 来源目录不存在或不是目录`、`系统找不到指定的文件`、`无法找到内置插件`、
  `无法找到插件脚本`、`Vault directory not found`、`File not found`、`Source file not found`、
  `EACCES`、`EISDIR`
- 每个 404 样本额外断言 `!== '本地文件路径问题'`
- `Chrome/Edge executable was not found` → 浏览器自动化启动失败（改前被第一条吞掉）
- `element not found: .toc-item` → 页面结构变化（改前被第一条吞掉）
- 断言规则顺序：`远端内容不存在` 在 `图片或附件下载失败` 之后、`没有访问权限` 之前，
  且 `图片下载失败：...：HTTP 404` 仍归图片规则

**B3（1 条）**
- `compactLogSummary` 仍在源码里、仍被 `compactLogSummary(text, 180)` 调用；
  `formatUserError` 内不再出现 `compactLogSummary`、改用 `extractErrorSummary(raw)`

**B4（2 条）**
- `usage: export_zsxq.py [--group-scope ...] [--follow-link-scope ...]` + argparse error 行
  → `!== '目标平台 API 权限不足'`
- `unrecognized arguments: --follow-link-scope articles`、`导出统计 docs: 12, notes: 4`、
  `drive: 3 wiki: 8` → 均 `!== '目标平台 API 权限不足'`
- `这不是开放平台 scope 问题…父节点没有给当前飞书应用写入权限` → 回到"没有访问权限"
- 直接对 pattern 断言 6 个噪声串不匹配：`--follow-link-scope`、`--group-scope`、`docs: 12`、
  `drive: 0`、`wiki: 8`、`scope=all`

## 可测性局限

`app.js` 是渲染进程脚本、不是 CommonJS 模块，没有为可测性改动它的结构。测试沿用
`tests_js/guide_markdown.test.js` / `tests_js/toc_rendering.test.js` 已有的手法：用
`indexOf` 按内容切出 4 段源码（`const ERROR_RULES = [`、`normalizeLogMessage`、
`compactLogSummary`、`classifyError`→`log` 之间的整段），拼起来丢进 `vm.runInNewContext`
再取出 `ERROR_RULES` / `classifyError` / `formatUserError`。因此：

- 断言的是**真实源码**的行为，不是复制粘贴的副本；但切片依赖那几个函数名与相对顺序，
  如果以后有人把 `extractErrorSummary` 挪到 `classifyError` 之前、或在
  `formatUserError` 和 `log` 之间插入别的函数，`sourceBetween` 的标记需要同步更新
  （切不到会 `assert.notEqual(-1)` 直接失败，不会静默跳过）。
- 只有 B3 关于"`compactLogSummary` 没被顺手删掉"的那条用的是源码文本断言，
  因为它要验证的是"其它调用点还在"，不是某个函数的返回值。
- `classifyError` 的兜底分支、`ERROR_RULES` 的相对顺序都能覆盖到；没有覆盖 DOM 侧的
  `appendUserLog` 渲染路径（那部分依赖 `document`，属于既有测试的空白，本次未涉及）。
