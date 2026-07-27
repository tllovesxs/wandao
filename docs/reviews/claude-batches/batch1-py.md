# Python 批次实施报告

## 已实施
| 编号 | 改了哪个文件 | 改了什么 | 新增测试 |
|---|---|---|---|
| W1 | `wandao_core/logging.py` | `from typing import Any` 之后、`LOG_PREFIX` 之前插入模块级循环：对 `sys.stdout` / `sys.stderr` 调用 `reconfigure(encoding="utf-8", errors="replace")`，捕获 `AttributeError / ValueError / OSError` 后静默跳过（pythonw、已替换的流不会炸） | `tests/test_logging_encoding.py` |
| W1 | `wandao.py` | `run_provider()` 中 `env["PYTHONPATH"] = ...` 之后追加 `env["PYTHONUTF8"] = "1"` 与 `env.setdefault("PYTHONIOENCODING", "utf-8")`（setdefault 保留调用方显式指定的编码） | `tests/test_logging_encoding.py` |
| W2 | `wandao_core/browser.py` | `FORBIDDEN_FILENAME_CHARS` 之后新增 `WINDOWS_RESERVED_NAMES = {"CON","PRN","AUX","NUL"} \| COM1-9 \| LPT1-9`；`sanitize_filename()` 末尾改为先 `[:max_len].rstrip(". ") or fallback`（截断后再去一次尾部点/空格），再判断 `cleaned.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES` 时前缀 `_` | `tests/test_sanitize_filename_reserved.py` |

## 已跳过
| 编号 | 原因 |
|---|---|
| （无） | W1、W2 的锚点文本均按内容定位成功，全部实施 |

## 测试结果
- 基线：524 passed（`python3 -m unittest discover -s tests -q` → `Ran 524 tests ... OK (skipped=2)`）
- 改动后：535 passed / 0 failed（新增 11 条：W1 5 条 + W2 6 条）
- quality_check：passed（`Python compile passed` / `Provider validation passed` / `Node syntax check passed (25 files)` / `Git diff whitespace check passed` / `Quality check passed`，exit=0）

### 负向对照（确认测试真的能抓到问题）
把三处改动在 `/tmp/w/py` 之外的一份临时副本里回退后重跑新增测试：11 条测试出现 16 failures + 2 errors，回退副本已删除。说明新增断言不是恒真。

## 反例验证（W2）
以下普通文件名断言 `sanitize_filename(x) == x`，全部原样保留、未被加下划线：

- `console.md`（以 CON 开头）
- `companion`（以 COM 开头）
- `中文标题`
- `会议纪要 2026`
- `nullable`（以 NUL 开头）
- `auxiliary.md`（以 AUX 开头）
- `printer.txt`（以 PRN 开头）
- `CONTENTS`（全大写，以 CON 开头）
- `com.example.note`（含点，主干 `com` 不在保留集）
- `lpt10.txt` / `COM10` / `COM0`（编号越界，保留集只含 1-9）
- `my-con.md` / `con-notes.md`（保留名出现在中间或作为前缀但主干不等）

同时验证被正确转义的：`CON`→`_CON`、`con.md`→`_con.md`、`COM1`→`_COM1`、`lpt9.txt`→`_lpt9.txt`、`NUL`→`_NUL`，以及大小写不敏感（`con` / `Con` / `cOn` / `PRN` / `prn.md` / `AuX` / `nul.markdown`）。

## 备注
- 仅改动了允许清单内的 4 个路径：`wandao_core/logging.py`、`wandao_core/browser.py`、`wandao.py`、新建的两个 `tests/` 文件（外加本报告 `REPORT.md`）。未改动任何既有测试，未执行任何 git 命令。
- 仓库根目录的 `out/`（`Doc 1.md` 等）是既有测试套件运行时自己写出的产物，基线跑测试时就已生成，与本次改动无关，未做删除。
