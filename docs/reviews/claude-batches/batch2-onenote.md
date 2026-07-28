# W3 — OneNote C# 桥接：UTF-8 输出 + stderr 管道死锁

方案来源：`sol-p2.md` → **W3**。

## 一、改了什么

改动文件仅两个：

| 文件 | 改动 |
| --- | --- |
| `plugins/onenote/backend/export_onenote.py` | 3 处：`import threading`；C# `Main` 设 `Console.OutputEncoding`；`run_bridge` stream 分支并发抽干 stderr |
| `tests/test_onenote_bridge.py` | 新增，6 个用例 |

### 1. C# 桥接输出编码（`CSHARP_BRIDGE_SOURCE` → `Main`）

在 `try {` 之后、任何 `Console` 写操作之前插入：

```csharp
Console.OutputEncoding = new UTF8Encoding(false);
```

**为什么**：重定向的 stdout 默认用 OEM 代码页（中文 Windows 上是 GBK/936），而 Python 侧
`subprocess` 一律以 `encoding="utf-8", errors="replace"` 解码，导致中文页面标题（`publish` 行里的
输出路径）和 `EncodeMessage(error)` 编码出来的中文 COM 错误信息全部变成替换字符。

**为什么是 `new UTF8Encoding(false)` 而不是 `Encoding.UTF8`**：后者带 BOM，重定向时 preamble 会被
写进管道，Python 读到的第一行会以 `﻿` 开头，`line.startswith("publish\t")` 直接失配，反而
弄坏现有的结果解析。测试里对 `new UTF8Encoding(false)` 做了精确断言，防止后人"简化"成
`Encoding.UTF8`。

`using System.Text;` 原本就在（第 48 行），`new UTF8Encoding(false)` 在 `GetHierarchy` 分支里也已
在用，没有引入新依赖。

改 C# 源字符串会让 `ensure_bridge` 里的 `source_hash = sha1(CSHARP_BRIDGE_SOURCE)` 变化，与
`WandaoOneNoteBridge.sha1` 戳记不一致，从而自动触发一次重新编译——不需要用户手工清缓存。

### 2. stderr 管道死锁（`run_bridge` 的 `stream=True` 分支）

原逻辑：`for line in proc.stdout:` 把 stdout 读到 EOF，**之后**才 `proc.stderr.read()`。
桥接一旦往 stderr 写超过管道缓冲（Windows 上通常 64KB），子进程阻塞在 stderr 写上，就永远不会
关闭 stdout；父进程阻塞在 stdout 读上等 EOF——双方互锁，进程卡死不动。

改为后台线程并发抽干 stderr：

```python
stderr_chunks: list[str] = []

def _drain_stderr() -> None:
    if proc.stderr is not None:
        stderr_chunks.append(proc.stderr.read() or "")

stderr_reader = threading.Thread(target=_drain_stderr, daemon=True)
stderr_reader.start()
# ... 原 for line in proc.stdout 循环一字未动 ...
code = proc.wait()
stderr_reader.join(timeout=5)
stderr = "".join(stderr_chunks)
```

**为什么选并发读取而不是 `stderr=STDOUT` 合并**：合并会把 COM 异常堆栈混进 stdout 行流，
`line.startswith("publish\t")` / `publish-result` 的解析会被污染，且 `ExportError` 里 stdout 与
stderr 就分不开了。并发读取对下游零影响——`stderr` 变量的类型、取值、以及
`raise ExportError(stderr or ...)` 的错误处理路径完全保持原样。

`join(timeout=5)` 是有界等待：`proc.wait()` 返回时子进程已退出、stderr 已 EOF，`read()` 会立刻
返回；加 timeout 只是保证任何异常情况下也不会把死锁从一处挪到另一处。

**未改的地方**：`stream=False` 分支用 `subprocess.run(capture_output=True)`，内部走
`communicate()`，本来就是并发读双管道，无死锁风险，因此原样不动。`ensure_bridge` 里编译 csc 的
`subprocess.run` 同理。

## 二、无法验证的部分（重要）

这个插件依赖 Windows 桌面版 OneNote 的 COM API，`run_bridge` 之前的 `ensure_bridge` 第一行就是
`if sys.platform != "win32": raise ExportError(...)`。沙箱里既没有 `csc.exe` 也没有 OneNote，
**以下各点均为代码审查级别的结论，没有在真机上跑通过**：

1. **C# 代码没有被编译过。** 语法正确性靠人工审查（这是一条独立语句，用的是已 `using` 的类型和
   已在同文件出现过的构造函数写法），但没有 `csc` 验证。
2. **`Console.OutputEncoding` 确实修好乱码——未实测。** 依据是 .NET Framework 的 setter 会同时把
   `_out` 和 `_error` 置空、下次访问时按新编码重建，因此 `Console.WriteLine` 与
   `Console.Error.WriteLine` 都会转成 UTF-8。这一点来自对框架行为的认知，不是实测。
3. **边缘失败模式：`Console.OutputEncoding` 的 setter 在标准输出句柄无效时会抛 `IOException`。**
   本场景下 Python 端总是以 `stdout=PIPE` / `capture_output=True` 启动，句柄一定有效，所以风险很
   低；且这行在既有的 `try` 内，真抛了也会被 `catch (Exception ex)` 接住、写 stderr 后返回 1，
   不会是未处理崩溃——但那会是一个**新的失败模式**。我**没有**给它单加 try/catch：方案里没有要求，
   加了属于超范围改动，且会掩盖真实问题。**这一点请作者在真机上确认后再决定是否加固。**
4. **BOM 是否真的没写出去——未实测。** 靠 `UTF8Encoding(false)` 的语义保证。
5. **重新编译是否顺利触发——未实测。** 靠阅读 `ensure_bridge` 的 hash/戳记逻辑得出。

死锁那一处相反，是**实测过的**：把源文件临时 stash 回改动前，新测试确实挂死（跑满 45s 外层超时），
恢复改动后 6 个用例在 0.03s 内全绿。也就是说该测试对这个 bug 是真实有效的。

## 三、测试

新增 `tests/test_onenote_bridge.py`：

- `test_sets_console_output_encoding` — 断言 `CSHARP_BRIDGE_SOURCE` 含 `Console.OutputEncoding`（防回归，任务明确要求）
- `test_console_encoding_is_bom_free_utf8` — 断言是 `new UTF8Encoding(false)`，防止被改成带 BOM 的 `Encoding.UTF8`
- `test_console_encoding_is_set_before_any_console_write` — 断言设置点在 `Main` 内第一次 `Console.*WriteLine` 之前
- `test_utf8encoding_type_is_imported` — 断言 `using System.Text;` 还在
- `test_large_stderr_does_not_deadlock` — 用 `sys.executable -c <脚本>` 冒充桥接（mock 掉
  `ensure_bridge` 让它返回 python 解释器路径），子进程先写一行 stdout、再往 stderr 灌 200KB、
  再写一行 stdout。断言 `run_bridge` 正常返回且两行 stdout 都收全。带 30s 看门狗线程，回归时是
  **失败**而不是永久挂起。
- `test_large_stderr_is_reported_on_failure` — 同样 200KB stderr 但退出码 3，断言抛 `ExportError`
  且 200KB 内容**一字不少**地进了错误消息（证明并发抽干没有截断 stderr）

不需要真的 C# 编译器，纯 Python 子进程即可复现管道语义。

## 四、回归

| | 基线 | 改动后 |
| --- | --- | --- |
| `python3 -m unittest discover -s tests -q` | 535, OK (skipped=2) | **541, OK (skipped=2)** |
| `python3 scripts/quality_check.py` | Quality check passed. | **Quality check passed.** |

541 = 535 + 6 个新增用例，无既有用例被破坏。

## 五、建议作者在 Windows 上手动验证的场景

按优先级：

1. **中文标题不乱码（主目标）** — 导出一个含中文笔记本/分区/页面名的笔记本，看流式日志里
   `OneNote 正在导出页面 MHT：<路径>` 的中文部分是否正常。改动前应是乱码。
2. **首行没有 BOM** — 确认第一条 `publish` 日志能被正常识别（若 BOM 泄漏，第一页的进度提示会
   退化成裸行 `emit(line)`，而不是"正在导出页面 MHT"）。这是判断 `UTF8Encoding(false)` 是否生效
   的最快信号。
3. **中文 COM 错误信息不乱码** — 人为制造一次失败（导出途中关掉 OneNote，或把某页删掉再导），
   看 `publish-result ... failed` 带回的中文异常文本，以及最终 `ExportError` 的内容。
4. **桥接自动重编译** — 首次跑改动后的版本，确认 `helper_dir` 下 `WandaoOneNoteBridge.exe` 被重新
   生成、`.sha1` 更新，且 csc 无编译错误。**这一步顺带就验证了第 2.1 条（C# 能否编译）。**
5. **`Console.OutputEncoding` 不抛异常** — 即上面第 3 节的风险点。只要第 4 步跑通且能正常导出，
   就说明没抛。若观察到桥接秒退且 stderr 是一条 `System.IO.IOException`，那就是踩中了，
   此时给这一行单独包一个 `try { } catch (IOException) { }` 即可。
6. **大 stderr 不死锁** — 批量导出几百页并让相当一部分页失败（例如导出一个有大量受保护/损坏页的
   笔记本），使 stderr 累积超过 64KB，确认进度条继续走、不再卡住。这是改动前最难复现也最致命的
   症状。
7. **`hierarchy` 路径回归** — 走一次目录树加载。该路径用 `File.WriteAllText(..., new UTF8Encoding(false))`
   写文件，不经过 Console，理论上完全不受影响，做个 smoke 确认即可。
