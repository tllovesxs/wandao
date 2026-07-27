# 批次二（b2-core）实施报告 — E3 / E4

## 1. 已实施

| 编号 | 文件 | commit sha | tag |
| --- | --- | --- | --- |
| E3 | `wandao_core/checkpoint.py`、`tests/test_checkpoint_lease_renewal.py`（新增） | `be711dd238f01dbbebf272b222051f29301b062f` | `sol/E3` |
| E4 | `wandao_core/browser.py`、`tests/test_cdp_timeout.py`（新增） | `86e2d2d48c4c99fd5f387bebfeea6c4647cf5a4c` | `sol/E4` |

### E3 改动内容
`_renew_lease` 的 UPDATE 语句删掉 `AND lease_expires_at > ?` 一个条件（以及对应的 `now` 绑定参数），
只保留 `WHERE task_id = ? AND lease_id = ?`。所有权证明改为纯粹依赖 `lease_id`：
接管方 `start_task` 会把 `lease_id` 覆盖成新 run_id，`release/complete/fail` 会置空，
因此 `lease_id = self.run_id` 本身即充分。错误信息、`_lease_claimed` 复位逻辑一行未动。

### E4 改动内容
`CDPClient` 新增 `_recv_checked(label, timeout)`，把 `_recv_json` 的 `OSError`
（3.10+ 下 `socket.timeout` 即内建 `TimeoutError`，同属 `OSError`）包装成
`ExportError("CDP {label} 通信超时或中断：…")`。`send()` 与 `wait_for_event()` 的读取调用
改走 `_recv_checked`。`_recv_json` / `_recv_exact` 原本抛的 `ExportError`
（`RuntimeError` 子类，非 `OSError`）不受影响，原样透传。

## 2. 测试先行证据

先写测试、后改实现。改实现**之前**运行
`python3 -m unittest tests.test_checkpoint_lease_renewal tests.test_cdp_timeout -v`
的结果（6 条中 4 条失败）：

```
test_own_expired_lease_can_be_renewed_when_nobody_took_over ... ERROR
test_renewal_still_fails_when_another_run_took_the_lease_over ... ok
test_renewal_still_fails_when_the_lease_row_was_cleared ... ok
test_send_read_timeout_raises_export_error ... ERROR
test_timeout_error_does_not_escape_as_a_builtin ... FAIL
test_wait_for_event_read_timeout_raises_export_error ... ERROR

======================================================================
ERROR: test_own_expired_lease_can_be_renewed_when_nobody_took_over
----------------------------------------------------------------------
  File "/tmp/w/b2-core/wandao_core/checkpoint.py", line 314, in heartbeat
    self._renew_lease()
  File "/tmp/w/b2-core/wandao_core/checkpoint.py", line 308, in _renew_lease
    raise CheckpointLeaseLostError(
wandao_core.checkpoint.CheckpointLeaseLostError: checkpoint lease for task
'task-1' has expired or was taken over by another run

======================================================================
ERROR: test_send_read_timeout_raises_export_error
----------------------------------------------------------------------
  File "/tmp/w/b2-core/wandao_core/browser.py", line 159, in send
    message = self._recv_json(timeout=max(0.5, deadline - time.time()))
  File "/tmp/w/b2-core/wandao_core/browser.py", line 256, in _recv_exact
    chunk = self.sock.recv(size - len(chunks))
TimeoutError: timed out

======================================================================
FAIL: test_timeout_error_does_not_escape_as_a_builtin
----------------------------------------------------------------------
AssertionError: builtin TimeoutError escaped instead of ExportError:
TimeoutError('timed out')

----------------------------------------------------------------------
Ran 6 tests in 1.540s

FAILED (failures=1, errors=3)
```

改实现之后同一条命令：`Ran 6 tests ... OK`。

**关键点**：两条反向断言
（`test_renewal_still_fails_when_another_run_took_the_lease_over`、
`test_renewal_still_fails_when_the_lease_row_was_cleared`）
在改动**前后都是 ok** —— 证明 E3 修复没有退化成「任何人都能抢租约」。
第一条模拟 `lease_id` 被他人接管，第二条模拟 `lease_id` 被置空，两种情况都必须继续抛
`CheckpointLeaseLostError`。

## 3. 跳过表

| 项 | 原因 |
| --- | --- |
| E3 方案里的可选后续「删除 `export_onenote.py:942` 的 `checkpoint.lease_seconds = 15 * 60`，回到默认 5 分钟」 | 该文件实际路径为 `plugins/onenote/backend/export_onenote.py`，**不在本批次允许修改的文件清单内**；且现存测试 `tests/test_onenote_checkpoint_contract.py:61` 断言 `checkpoint.lease_seconds == 15 * 60`，删除会导致该测试失败，而已有测试不允许修改。留给后续批次一并处理。 |

其余 E3 / E4 方案描述的改动点均按内容定位成功，无因搜不到而跳过的项。

## 4. 最终测试数

- 基线：`Ran 535 tests ... OK (skipped=2)`
- 本次：`Ran 541 tests in 9.263s ... OK (skipped=2)`（535 + 新增 6 条，全过，无 FAIL/ERROR）
- `python3 scripts/quality_check.py`：`Quality check passed.`（Node 语法检查 25 文件 / 26 测试文件通过，git diff 空白检查通过）

## 5. 风险提示

1. **E3 的语义变化**：过期租约现在可以自我续期，因此「进程僵死但未退出」的旧 run 在无人接管前
   会一直把租约续下去。这与既有的接管路径不冲突（`start_task` 看到 `lease_expires_at` 已过期
   仍会接管并改写 `lease_id`，随后旧 run 的下一次 `_renew_lease` 立刻抛
   `CheckpointLeaseLostError` 被围栏挡住），但「过期即失效」这条隐式保证不再成立，
   判定活跃与否请一律走 `_lease_is_active`。
2. **E3 与 OneNote 的 15 分钟租约并存**：`plugins/onenote/backend/export_onenote.py` 里的
   `lease_seconds = 15 * 60` 本次未动（见跳过表），只是不再必要，不会造成错误。
3. **E4 的捕获面**：`_recv_checked` 捕获整个 `OSError`，除超时外也会把
   `ConnectionResetError` / `BrokenPipeError` 等连接中断一并包成 `ExportError`
   （消息为「通信超时或中断」）。这正是方案意图，但调用方若原本想区分「超时」与「连接断开」，
   现在需要靠 `__cause__` 而非异常类型。
4. **E4 不影响既有 ExportError**：`_recv_json` / `_recv_exact` 抛出的
   `ExportError("DevTools WebSocket closed")`、`ExportError("Unexpected EOF ...")`
   是 `RuntimeError` 子类，不被 `except OSError` 捕获，语义与消息保持原样。
5. `send()` / `wait_for_event()` 末尾那两行原「死代码」`raise ExportError("Timed out waiting for ...")`
   按方案保留未删（方案未要求删除）。它们现在仍基本不可达（超时会先由 `_recv_checked` 抛出），
   属于无害冗余。
