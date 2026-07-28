# G2 — parseLastJson 回退扫描限定零缩进

**范围**：仅实施方案 (a) 最小改动。(b) 的 `@@WANDAO_RESULT@@` 单行结果帧（需同步改 Python 侧
`wandao_core/logging.py` 与各插件 `main()`，涉及老插件兼容）**未做**，留待批次三。
`wandao_core/logging.py`、插件 Python 文件本次一行未动。

## 改动

`wandao_electron/process_result.js:parseLastJson` 的回退扫描分支：
候选起点判定从「`lines[index].trimStart()` 后以 `{`/`[` 开头」收紧为「第 0 列即 `{`/`[`」
（`charCodeAt(0)` 比对 `0x7b`/`0x5b`）。首次 `JSON.parse(trimmed)` 的快路径、结构化日志行过滤、
返回值语义均未改动。

pretty-print 报告里每个嵌套对象/数组的开括号行原本都是候选起点，一万条报告约有数万个候选，
每个都要做一次 `slice().join().parse()` → O(n²)。顶层括号一定顶格，因此限定零缩进后
候选数从数万降到 1，扫描退化项消失。

## 实测耗时（node --test，同一机器）

| 场景（stdout 混进一行普通输出 + pretty-print indent=2 报告） | 改动前 | 改动后 | 倍数 |
| --- | --- | --- | --- |
| 3000 条（对应实测 3000 帖 2,573ms） | 2,764ms | 12ms | ~230x |
| 10000 条（对应实测 10000 条 31,190ms 主进程阻塞） | 23,270ms | 33ms | ~705x |

门禁阈值取 250ms / 500ms：约为改动前的 1/11 与 1/47，同时给改动后留 15 倍以上余量，
既能稳定抓住二次方回归，也不会在慢机器上抖动。

## 反向断言清单（改严扫描后必须仍能解析）

先 `grep -rn "print(json.dumps" plugins/*/backend/*.py providers/ *.py` 摸清各插件真实输出形态，
确认全部 result 打印路径都是顶格 JSON（无任何缩进/前缀拼接形态），据此设计用例：

| # | 输出形态 | 对应插件 / 来源 | 断言 |
| --- | --- | --- | --- |
| 1 | 干净 stdout，pretty-print(indent=2) 独占 | yuque/zsxq/feishu/wiz/xiliu/youdao/dingtalk/aliyun 等多数插件 | 解析出对象 |
| 2 | 干净 stdout，compact 单行 `separators=(",",":")` | wps `export_wps.py:1489` | 解析出对象 |
| 3 | 普通日志行 + compact 单行 | obsidian `export_obsidian.py:561/589/591` | 解析出对象 |
| 4 | 普通日志行 + 中文 pretty-print（`ensure_ascii=False`） | yinxiang/ima `emit_json` | 解析出对象、中文键值正确 |
| 5 | 普通日志行 + 顶层数组 `[`（pretty-print） | scan_toc 类返回 | 解析出数组、长度正确 |
| 6 | 普通日志行 + 顶层数组 `[`（compact 单行） | 同上 | 解析出数组 |
| 7 | `@@WANDAO_LOG@@` 结构化日志行穿插 + pretty 结果 | `wandao_core/logging.py` 结构化模式 | 过滤后正常解析 |
| 8 | CRLF 行尾 + 普通日志行 | Windows 子进程 stdout | 正常解析 |
| 9 | 结果后带多余空行 | `print(..., flush=True)` 收尾 | 正常解析 |
| 10 | 日志里先 dump 过一份 pretty JSON，末尾再打结果 | aliyun `:2471` / feishu `:2199` 的 `log(json.dumps(summary, indent=2))` | 取**最后**一个结果，不被前一份 JSON 抢走 |
| 11 | TaskResult v1（`kind`/`schemaVersion`）经 `parseProcessResult` | 新结果契约 | `ok=true`、`legacy=false`、字段正确 |
| 12 | 完全没有 JSON | — | 返回 `null` |
| 13 | 空 stdout / 纯空白 / 仅结构化日志 | — | 返回 `null` |

这 13 条在改动前就全部通过（第一次跑：15 用例中 13 功能断言 pass、2 条超时断言 fail），
改动后依旧全部通过，确认没有出现「本该解析到的结果解析不到」。

**已知未变的既有行为**：结果 JSON 之后还跟着普通日志行时，改动前后同样返回 `null`
（回退扫描的每个候选切片都包含尾部噪声）。该场景由 G1 的 relay 拆分处理，本次不碰。

## 测试数

- 新增 `tests_js/parse_last_json_perf.test.js`：15 个用例（2 个性能门禁 + 13 个反向断言）。
- `python3 scripts/quality_check.py`：Node **142 通过**（基线 127 + 新增 15）/ Python **535 通过** /
  `Quality check passed.`
