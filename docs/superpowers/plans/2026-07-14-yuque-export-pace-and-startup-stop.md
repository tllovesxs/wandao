# Yuque Export Pace and Startup Stop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Modestly shorten Yuque document exports without adding concurrency, and classify a user stop during browser/setup work as stopped rather than failed.

**Architecture:** Reduce only the Yuque command-line defaults passed into the existing shared `throttle_request()` helper, preserving its cooperative cancellation and random jitter. Handle `ExportStopped` explicitly in the Yuque CLI before the generic exception handler so Electron receives exit code `130` and `{ "stopped": true }`; update manifest-provider action handling to use its existing `isStoppedResult()` predicate before success/failure handling.

**Tech Stack:** Python 3.10+, `unittest`, Node.js built-in test runner, Electron renderer JavaScript.

## Global Constraints

- Work only in `C:\Users\devil\Documents\New project\wandao-worktrees\fix-yuque-export-diagnostics` on branch `codex/fix-yuque-export-diagnostics`.
- Do not edit `D:\项目\Wandao` or the primary workspace checkout.
- Keep document fetching and resource downloads serial; do not add concurrency or retry loops.
- Keep all diagnostics free of cookies, headers, authorization values, and document body content.
- Preserve exit code `130` and the `stopped` result contract used by Electron task history.

---

### Task 1: Make default detail-request pacing modestly faster

**Files:**
- Modify: `plugins/yuque/backend/export_yuque.py:1514-1515`
- Test: `tests/test_backend_selection_contract.py`

**Interfaces:**
- Consumes: `parse_args(argv)` with no explicit throttle arguments.
- Produces: `args.request_delay == 0.55` and `args.request_jitter == 0.25`.
- Preserves: explicit `--request-delay` and `--request-jitter` values unchanged.

- [ ] **Step 1: Write the failing test** in `BackendSelectionContractTests`:

```python
def test_yuque_cli_uses_modest_default_detail_request_pacing(self) -> None:
    args = export_yuque.parse_args([
        "--book-url", "https://www.yuque.com/example/book",
        "--output", "output",
    ])

    self.assertEqual(args.request_delay, 0.55)
    self.assertEqual(args.request_jitter, 0.25)
```

- [ ] **Step 2: Verify the test fails**:

```powershell
python -m unittest tests.test_backend_selection_contract.BackendSelectionContractTests.test_yuque_cli_uses_modest_default_detail_request_pacing -v
```

Expected: failure showing the old `0.8` and `0.4` defaults.

- [ ] **Step 3: Change the two `argparse` defaults** in `plugins/yuque/backend/export_yuque.py` to `0.55` and `0.25`; do not alter `wandao_core.browser.throttle_request()`.

- [ ] **Step 4: Verify the focused test passes** using the same command.

### Task 2: Return a cooperative-stop result during setup

**Files:**
- Modify: `plugins/yuque/backend/export_yuque.py:1532-1581`
- Test: `tests/test_backend_selection_contract.py`

**Interfaces:**
- Consumes: an `ExportStopped("用户已停止当前任务")` raised by `login_and_save_auth`, `scan_book_toc`, or `export_book`.
- Produces: stdout JSON containing `{ "stopped": true }`, an exit code of `130`, and a `task.stopped` event.
- Preserves: arbitrary non-stop exceptions return `1` and remain task failures.

- [ ] **Step 1: Write the failing test**:

```python
def test_yuque_cli_startup_stop_returns_130_with_stopped_payload(self) -> None:
    args = argparse.Namespace(
        book_url="https://www.yuque.com/example/book",
        login=False,
        scan_toc=False,
        output=Path("output"),
    )
    stdout = io.StringIO()

    with (
        mock.patch.object(export_yuque, "parse_args", return_value=args),
        mock.patch.object(export_yuque, "export_book", side_effect=export_yuque.ExportStopped("用户已停止当前任务")),
        contextlib.redirect_stdout(stdout),
    ):
        self.assertEqual(export_yuque.main(["--book-url", args.book_url, "--output", "output"]), 130)

    self.assertTrue(json.loads(stdout.getvalue())["stopped"])
```

- [ ] **Step 2: Verify the test fails**:

```powershell
python -m unittest tests.test_backend_selection_contract.BackendSelectionContractTests.test_yuque_cli_startup_stop_returns_130_with_stopped_payload -v
```

Expected: failure because the old generic exception handler returns `1`.

- [ ] **Step 3: Add an `except ExportStopped:` block before `except KeyboardInterrupt:` and the generic `except Exception:` block.** Emit `task.stopped`, set `report = {"stopped": True}`, and allow the existing JSON-output/return path to return `130`.

- [ ] **Step 4: Verify the focused test passes** using the same command.

### Task 3: Display manifest-provider startup stops as stopped

**Files:**
- Modify: `wandao_electron/renderer/app.js:3021-3041`
- Test: `tests_js/task_resume.test.js`

**Interfaces:**
- Consumes: `runTrackedPythonCommand()` result where `code === 130` or `data.stopped === true`.
- Produces: a warning log and `finishProgress(false, "…已停止")` without the generic failure branch.
- Preserves: `result.success === true` continues through the existing successful scan/update path.

- [ ] **Step 1: Write the failing source-contract test**:

```javascript
test('manifest-provider actions treat code 130 as stopped before the failure branch', () => {
  const appJs = fs.readFileSync('wandao_electron/renderer/app.js', 'utf8');
  const start = appJs.indexOf("actions.forEach((action) => {");
  const end = appJs.indexOf('function sandboxPluginHtml', start);
  const handler = appJs.slice(start, end);

  assert.match(handler, /if \(isStoppedResult\(result\)\) \{[\s\S]*finishProgress\(false,[\s\S]*已停止[\s\S]*\} else if \(result\.success\)/);
});
```

- [ ] **Step 2: Verify the test fails**:

```powershell
node --test tests_js/task_resume.test.js
```

Expected: the handler currently begins with `if (result.success)` and does not classify code `130`.

- [ ] **Step 3: Place an `isStoppedResult(result)` branch before the existing `result.success` branch** in the manifest action callback. It must log a warning and complete progress with an “已停止” message, without calling `applyActionUpdates()`.

- [ ] **Step 4: Verify the focused JavaScript suite passes**:

```powershell
node --test tests_js/task_resume.test.js
```

### Task 4: Full verification and source launch

**Files:**
- Verify: `tests/test_backend_selection_contract.py`
- Verify: `tests/test_stop_marker.py`
- Verify: `tests/test_youdao_stop.py`
- Verify: `tests_js/task_resume.test.js`
- Verify: `tests_js/task_report.test.js`
- Verify: `wandao_electron/package.json`

- [ ] **Step 1: Run Python suites**:

```powershell
python -m unittest tests.test_backend_selection_contract tests.test_stop_marker tests.test_youdao_stop -v
```

Expected: all tests pass.

- [ ] **Step 2: Run JavaScript suites**:

```powershell
node --test tests_js/task_resume.test.js tests_js/task_report.test.js
```

Expected: all tests pass.

- [ ] **Step 3: Run Electron static checks and diff validation**:

```powershell
Push-Location wandao_electron
npm run check
Pop-Location
git diff --check
git diff -- plugins/yuque/backend/export_yuque.py tests/test_backend_selection_contract.py wandao_electron/renderer/app.js tests_js/task_resume.test.js
```

Expected: checks pass; the diff only contains diagnostics work already in progress plus the two approved behavior changes.

- [ ] **Step 4: Stop the prior isolated source Electron process, then start**:

```powershell
Push-Location wandao_electron
npm run dev
```

Expected: Electron starts from the isolated worktree and can be used for a manual Yuque export test.
