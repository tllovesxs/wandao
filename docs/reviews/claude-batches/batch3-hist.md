# B3 任务历史瘦身（G3）与参数免重复加密（G5）

工作目录 `/tmp/w/b3-hist`，分支 `b3/hist`。
改动文件：`wandao_electron/renderer/app.js`、新增 `tests_js/task_history_slim.test.js`。
`wandao_electron/renderer/task_report.js` **未改动**（原因见"为什么没动 task_report.js"）。

提交：
- `2eda11d` `perf(history): 任务历史落盘只留诊断字段…… [SOL:G3]` → tag `sol/G3`
- `2c990d2` `perf(history): 复用已有 protectedArgs…… [SOL:G5]` → tag `sol/G5`

---

## 一、字段依赖表（第一步产出，决定了什么能删什么不能删）

`resultData` 的访问全部经过 `task?.report?.raw || task?.resultData || task?.report` 这条回退链，
所以「report.raw 读的字段」＝「resultData 读的字段」。下表按 UI 功能列出。

| UI 功能 | 入口 | 实际读取的字段 | 结论 |
|---|---|---|---|
| 打开输出目录 | `app.js openTaskArtifact('output')` → `task_report.js taskArtifactPaths` | `report.output` → `resultData.output` → `resultData.outputDir` | **必须保留** |
| 打开报告文件 | `app.js openTaskArtifact('report')` → `taskArtifactPaths` | `report.reportFile` → `resultData.reportFile` | **必须保留** |
| 重试失败项（按钮可用性） | `app.js canResumeTask` → `taskDocumentFailureCount` | `report.stats.failed`；`raw/resultData` 的 `failureCount`/`failedDocs`/`failed`/`errorCount`（取第一个 own property）、`failures.length`、`resourceFailures`/`imageFailures`/`attachmentFailures` 是否非空 | **必须保留**（含 failures 长度语义） |
| 重试失败项（按钮文案 N） | `app.js taskResumeActionLabel` | 同上的 `taskDocumentFailureCount` | 同上 |
| 重试失败项（命令行） | `app.js resumeTaskArgs` → `task_resume.js buildResumeArgs` | `task.args`（由 `protectedArgs` 解密）、`task.status`、`deferred`、provider 的 `checkpoint`/`retryFailures` | **必须保留** `protectedArgs`、`status`、`deferred` |
| 继续任务（待处理判定与计数） | `app.js taskHasDeferredDocuments` / `taskResumeActionLabel`；`task_resume.js hasDeferredDocuments` | `report.deferred`（正常为 undefined）→ `resultData.deferred`（`.length`） | **必须保留 deferred** |
| 任务状态（完成/部分/暂停/停止） | `task_report.js deriveTaskStatus` | `stopped`、`rateLimitedPaused`、`report.raw.rateLimitedPaused`、`outcome`、全部 stats 标量 | **必须保留**（标量全留） |
| 统计摘要行 | `app.js taskSummary` → `summarizeStats(report.stats)` | `report.stats`（已是标量对象） | **必须保留** |
| 失败项预览 / 复制失败项 | `app.js taskFailurePreview`、`taskFailureDiagnostics` → `collectFailureDiagnostics` → `collectFailureItems` | 顶层 `failures`、`resourceFailures`、`imageFailures`、`attachmentFailures`、`errors`；并递归进入任何**键名匹配 `/fail|error/i`** 的子节点 | **必须保留这几类数组** |
| 资源警告计数 | `taskResourceFailureCount` | `stats.resourceFailed`/`imageFailed`/`attachmentFailed`（来自 `resourceFailureCount`/`imageFailureCount`/`attachmentFailureCount` 与各失败数组长度） | **必须保留** |
| 复制报告 | `app.js copyTaskReport` → `createMarkdownTaskReport` | `task.id/title/status/startedAt/finishedAt/elapsedMs/script/args`、`report.stats/reportFile/output/failures`、`task.error`、**`task.resultData` 整体 JSON**、`task.logs`（`entry.event\|\|entry.data` 进"结构化事件"，全部进"详细日志"） | 见下方"复制报告的取舍" |
| 任务列表筛选 | `app.js taskHistoryProviders` / filters | `task.providerId`、`providerTitle`、`status`、`title` | 顶层字段，未动 |
| 参数可用性 | `canResumeTask` / `resumeTaskDisabledReason` | `task.argsUnavailable`、`task.protectedArgs` | **必须保留** |

**没有任何 UI 读取** `exportedItems`、`skippedItems`、`notebooks`、`docs`、`toc`、`groups`、`nodes`、
`files`、`images` 这类逐项清单 —— 全仓库 `grep` 只在 Python 插件与 Python 测试里出现。

---

## 二、G3：删了什么、保留了什么、为什么

改动点：`app.js` `saveTaskHistory()` 里那三行 mask 之前插入瘦身，只影响**落盘**，内存对象一字未动。

### 删掉

1. **`report.raw`**
   `normalizeTaskReport` 返回 `raw: source`，`source` 就是 `task.resultData` 本身 —— 同一个对象引用。
   内存里不额外占空间，但 `JSON.stringify` 会把它完整写第二遍，`maskSensitiveValue` 也会把它深走第二遍。
   删除安全，因为所有读 `raw` 的地方（`taskFailurePreview`、`taskFailureDiagnostics`、
   `taskDocumentFailureCount`、`app.js:896`）写的都是 `report.raw || resultData` 回退链，
   `raw` 缺失时自动落到瘦身后的 `resultData`，而瘦身后的 `resultData` 保留了它们要读的全部字段。

2. **`resultData` 里所有非诊断类数组**
   逐项清单（`exportedItems` 3000 条、`skippedItems` 400 条、`pendingItems` 250 条……）。
   占了单条 95% 以上的体积，且无任何读取方。

3. **每条日志的 `data` 字段**
   结构化事件的原始载荷；`task.completed` 这类事件的 `data` 就是整份报告。
   日志行渲染只用 `time/source/type/event/message`。

4. **日志只留最近 200 条**（内存仍是 `MAX_TASK_LOG_ENTRIES = 2000`）。

### 保留

- **所有标量与嵌套对象，不做白名单。**
  这是与方案原文最大的差异：方案给的是 `PERSIST_KEEP` 允许列表，但插件返回的键名有 400 多个
  （`grep` 统计），允许列表必然漏字段（例如 `rateLimitPauseReason`、`total`/`exported`/`skipped`
  这类 OneNote 风格的旧键、`platform`、`errorCount`、`checkpoint` 内部字段）。
  改成「标量与对象全留、只砍大数组」，`normalizeTaskReport` 读的 30 多个统计键、
  `taskArtifactPaths` 的路径、`deriveTaskStatus` 的三个开关一个都不会丢，风险面小得多。
- **诊断类数组**：`failures`、`errors`、`resourceFailures`、`imageFailures`、`attachmentFailures`、
  `deferred`、`pendingItems`、`resourceWarnings`，外加任何键名含 `fail|error` 的嵌套数组
  （`collectFailureItems` 就是按这个正则递归的）。顶层截断 200 条、嵌套 50 条。
- **截断时补计数**：截断 `failures`/`imageFailures`/`attachmentFailures`/`resourceFailures` 且原报告
  **没有**对应计数字段时，把截断前的长度写进 `failureCount`/`imageFailureCount`/
  `attachmentFailureCount`/`resourceFailureCount`。
  这样 `taskDocumentFailureCount` 的 `Math.max(declared, listed, statistic)` 仍拿到真实值，
  「重试失败文档（N）」的 N 不会因为截断变小。（已有该字段时不覆盖，行为完全不变。）

### 已知且可接受的差异

- `deferred` 超过 200 条时，「继续任务（N 篇待处理）」的 N 会显示 200。
  这是纯文案；`hasDeferredDocuments` 只判 `length > 0`，`buildResumeArgs` 的分支结果不变。
  实测 OneNote 的 deferred 项是 `{id,title,path}`，200 条上限对真实场景基本不触发。
- 复制报告的取舍：重启后再复制的报告，「结果数据」小节是瘦身后的 JSON、「详细日志」是最近 200 行、
  「结构化事件」只剩带 `event` 的条目。任务刚跑完时（内存态）仍是完整内容。
  七个小节全部照常生成，路径、统计、失败项一字不少。

### 为什么没动 `task_report.js`

方案建议删掉第 418 行 `report?.raw?.rateLimitedPaused === true`，理由是第 172 行已把
`rateLimitedPaused` 提到顶层。但这行对**老格式**（`report.raw` 还在）是有效兜底，且是可选链、
`raw` 缺失时求值为 `undefined`，不会崩。按「拿不准就保留」保留原样。
`normalizeTaskReport` 的 `raw: source` 也保留 —— 内存里是同一引用不额外占空间，剥离只发生在落盘。

---

## 三、量化效果

用 3000 条 `exportedItems` + 400 条 `skippedItems` + 250 条 `pendingItems` + 12 条 `failures`
+ 2000 条带完整 `data` 的日志 + `report.raw` 构造的单条任务（贴近实测的 zsxq 3000 帖导出）：

| | 旧格式 | 新格式 | 倍数 |
|---|---|---|---|
| 单条（含 JSON 包装，`indent=2`） | **6,566,873 B ≈ 6.26 MB** | **80,995 B ≈ 79.1 KB** | **81×** |
| 52 条 | **≈ 325.7 MB** | **≈ 4.02 MB** | 81× |
| 52 条 `JSON.stringify` 耗时 | **6,216 ms** | 未再测（数据量 1/81） | — |

落在方案预期区间（单条 60–200 KB）内。剩余 79 KB 主要是 200 条日志行（约 50 KB）与
诊断数组，属于「必须留下的诊断信息」。

`maskSensitiveValue` 现在只深走瘦身后的对象，不再把 3000 条清单走两遍；
`writeFileSync` 与 IPC 的载荷同比例下降，也就不会再撞上 IPC 传 200MB 抛错的场景。

---

## 四、G5：参数免重复加密

`saveTaskHistory` 原本无条件把全部 80 条历史的 `args` 重新 DPAPI 加密，
一次任务两次保存 = 160 次 `safeStorage.encryptString` + 160 次 IPC。

改法（内存态脏标记，不落盘）：
- `startHistoryTask` 建的任务 `argsDirty: true` —— 参数是新的，必须加密一次。
- `performTaskHistoryLoad` 读到 `protectedArgs` 时 `task.argsDirty = false` —— 盘上的密文本来就对应这批参数。
- `saveTaskHistory` 新增分支：`task.protectedArgs && task.argsDirty === false` → 直接复用密文，零次 OS 加密。
- 真正加密成功后把密文与 `argsDirty = false` **回写内存**，同一任务的第二次保存即命中复用。
- `argsDirty` 与 `pendingSave`/`detailStartIndex` 一起从 `persistable` 解构剔除，不落盘。

分支顺序保持 `argsUnavailable` 优先，所以「密文暂时解不开」的历史条目仍原样保留 `protectedArgs`、
不会被当成明文重新加密，也不会写出可被误认为遗留明文的占位符（原有的
`tests_js/task_history_persistence.test.js` 三个用例全绿，未改动该文件）。

效果：一次任务的 DPAPI 调用 **160 → 1**（只有新任务的参数是脏的）；
`Promise.all` 里的跨进程往返同样从 80 次降到 1 次。

---

## 五、老格式兼容性验证

**读取路径 `performTaskHistoryLoad` 除了新增一行 `task.argsDirty = false` 之外没有任何改动**，
它只是 `{ ...storedTask }` 展开加上 args 解密，不碰 `resultData`/`report`/`logs`。
所以老格式文件不会崩、不会丢历史 —— 老条目原封不动进内存，只有下一次保存时才转成瘦身格式。

`tests_js/task_history_slim.test.js` 11 个用例（全绿），其中兼容性相关：

1. **老格式可读**：构造完整老格式条目（3000 `exportedItems` + `report.raw` + 2000 条日志），
   `loadTaskHistory` 后断言 `resultData.exportedItems.length === 3000`、`logs.length === 2000`、
   `report.raw` 仍在、`args` 正确解密、`argsUnavailable === false`；
   并断言四个动作在老格式上照常工作：`taskDocumentFailureCount === 12`、
   `taskArtifactPaths().output` 正确、`hasDeferredDocuments === true`、`deriveTaskStatus === 'partial'`。
2. **老格式 → 新格式往返一致**：老条目 load → save（瘦身）→ 再 load，断言重试计数、资源失败计数、
   报告文件路径、deferred 判定、状态判定全部与老格式相同，且
   `buildResumeArgs` 产出的命令行与老格式**逐项相等**（`deepEqual`）。
3. **截断不缩小失败计数**：900 条 `failures` 且报告未带 `failureCount` 的极端条目，
   落盘后 `failures.length === 200` 但 `failureCount === 900`，`taskDocumentFailureCount === 900`。
4. **风控暂停条目**：`rateLimitedPaused` 在 `resultData` 与 `report` 顶层都保留，
   `report.raw` 删除后 `deriveTaskStatus` 仍返回 `'paused'`。
5. **`report.raw` 删除后复制报告完整**：`createMarkdownTaskReport` 生成的 Markdown
   仍包含全部七个小节，且含任务 ID、报告文件路径、输出目录、失败项内容、`"exportedDocs": 3000`、日志行。
6. **运行中的空任务**：`resultData: null` / `report: null` / `logs: []` 原样落盘，不被瘦身逻辑改写。

## 六、回归

```
node --check wandao_electron/renderer/app.js         # ok
node --check wandao_electron/renderer/task_report.js # ok
python3 scripts/quality_check.py
  Ran 547 tests in 9.382s / OK (skipped=2)      # Python 基线 547 ✔
  # pass 194 / # fail 0                          # Node 183 → 194（+11 新用例）
  Node syntax check passed (25 files, 31 test files).
  Git diff whitespace check passed.
  Quality check passed.                          # ✔
```
