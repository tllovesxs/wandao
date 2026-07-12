# Yuque Import Resume Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Yuque Markdown imports stop cooperatively and resume only unfinished or failed source files.

**Architecture:** The legacy Yuque template supplies a per-task SQLite checkpoint at `<source-dir>/.wandao/yuque-import.sqlite`. The backend records each source-relative path with `WandaoCheckpoint`, filters completed or failed items for resume, and returns code 130 after a controlled stop. The shared Electron/Python runtime writes a stop marker before the 8-second force-kill fallback.

**Tech Stack:** Electron main/renderer JavaScript, Python 3 standard library, SQLite checkpoint runtime, Node test runner, `unittest`.

## Global Constraints

- Keep `--skip-existing` and `--update-existing` semantics unchanged.
- Store the checkpoint at `<source-dir>/.wandao/yuque-import.sqlite`.
- A new start gets a new checkpoint task id; task-history Continue retains its prior `--checkpoint-task-id`.
- Return code 130 for a controlled stop. Do not report it as a resource failure.
- Do not log cookies, tokens, or full sensitive responses.
- Stage only named paths. Do not modify the existing Feishu PR branch.

---

### Task 1: Lock the Yuque Checkpoint Contract with Failing Tests

**Files:**
- Create: `tests/test_yuque_import_resume.py`
- Modify: `tests/test_checkpoint_provider_args.py`
- Modify: `tests_js/task_resume.test.js`

**Interfaces:**
- Consumes: `import_yuque.parse_args`, `WandaoCheckpoint`, legacy Yuque renderer source.
- Produces: regression assertions for checkpoint args, resume filtering, retry filtering, and stopped UI.

- [ ] **Step 1: Add failing parser and selection tests**

```python
def test_yuque_import_accepts_checkpoint_resume_args():
    args = import_yuque.parse_args([
        '--target-book-url', 'https://www.yuque.com/demo/book',
        '--source-dir', 'source', '--api-import-all', '--yes',
        '--checkpoint-file', 'source/.wandao/yuque-import.sqlite',
        '--checkpoint-task-id', 'task-1', '--resume', '--retry-failures',
    ])
    assert args.checkpoint_file.endswith('yuque-import.sqlite')
    assert args.checkpoint_task_id == 'task-1'
    assert args.resume is True
    assert args.retry_failed is True
```

Create a temporary checkpoint with `a.md` completed and `b.md` failed. Assert the planned `select_checkpoint_docs` helper excludes `a.md` on resume and returns only `b.md` for retry.

- [ ] **Step 2: Add a failing renderer assertion**

```javascript
test('Yuque import preserves checkpoint arguments and displays code 130 as stopped', () => {
  const appJs = fs.readFileSync('wandao_electron/renderer/app.js', 'utf8');
  assert.match(appJs, /yuque-import\.sqlite/);
  assert.match(appJs, /result\.code === 130/);
  assert.match(appJs, /已停止，已完成项目会在下次继续时跳过/);
});
```

- [ ] **Step 3: Verify RED**

Run: `python -m unittest discover -s tests -p "test_yuque_import_resume.py"` and `node --test tests_js/task_resume.test.js`

Expected: missing checkpoint parser fields, missing selection helper, and missing renderer contract fail.

- [ ] **Step 4: Commit the failing tests**

```powershell
git add tests/test_yuque_import_resume.py tests/test_checkpoint_provider_args.py tests_js/task_resume.test.js
git commit -m "test: cover Yuque import resume contract"
```

### Task 2: Persist Yuque Import Items and Return a Stopped Result

**Files:**
- Modify: `plugins/yuque/backend/import_yuque.py:957-1240,1464-1502`
- Modify: `plugins/yuque/providers/yuque-import/provider.json:14-16`
- Test: `tests/test_yuque_import_resume.py`
- Test: `tests/test_checkpoint_provider_args.py`

**Interfaces:**
- Consumes: `add_checkpoint_args(parser, retry_flag="--retry-failures")` and `open_checkpoint_from_args(args, "yuque-import", "import")`.
- Produces: `checkpoint_item_key(doc)`, `select_checkpoint_docs(docs, checkpoint, resume, retry_failed)`, and checkpoint stats in the report.

- [ ] **Step 1: Add checkpoint argument support**

Import the checkpoint helpers. In `parse_args`, replace the bespoke retry parser with:

```python
add_checkpoint_args(parser, retry_flag="--retry-failures")
```

- [ ] **Step 2: Add pure item-selection helpers**

```python
def checkpoint_item_key(doc: dict[str, Any]) -> str:
    return f"yuque-import:{doc['relativePath']}"

def select_checkpoint_docs(docs, checkpoint, *, resume: bool, retry_failed: bool):
    if not checkpoint:
        return docs
    if retry_failed:
        return [doc for doc in docs if checkpoint.item_status(checkpoint_item_key(doc)) == "failed"]
    if resume:
        return [doc for doc in docs if checkpoint.item_status(checkpoint_item_key(doc)) != "completed"]
    return docs
```

- [ ] **Step 3: Record lifecycle in `import_docs`**

Open a checkpoint after scanning source docs. Call `start_task` with resolved source and target, upsert every source-relative path, then call `select_checkpoint_docs`. Around each `import_one_doc` call:

```python
item_key = checkpoint_item_key(doc)
checkpoint.start_item(item_key, "import")
try:
    result, toc = import_one_doc(...)
    checkpoint.complete_item(item_key, target_id=str(result.get("id") or ""), metadata={"action": result["action"]})
except ExportStopped:
    checkpoint.fail_item(item_key, "stopped")
    stopped = True
    break
except Exception as exc:
    checkpoint.fail_item(item_key, compact_error(exc, 1200))
    # retain the existing non-fatal failure recording path
```

In `finally`, mark the task stopped, failed, or completed, add `checkpoint.stats()` to the report, and close it. The CLI main returns 130 when the report is stopped.

- [ ] **Step 4: Declare provider capability**

Add the following while preserving existing fields:

```json
"checkpoint": { "supported": true, "strategy": "items", "resourceTracking": false }
```

- [ ] **Step 5: Verify GREEN**

Run: `python -m unittest discover -s tests -p "test_yuque_import_resume.py"` and `python -m unittest discover -s tests -p "test_checkpoint_provider_args.py"`

Expected: parser, selection, stopped item, and retry behavior all pass.

- [ ] **Step 6: Commit**

```powershell
git add plugins/yuque/backend/import_yuque.py plugins/yuque/providers/yuque-import/provider.json tests/test_yuque_import_resume.py tests/test_checkpoint_provider_args.py
git commit -m "fix: resume Yuque Markdown imports"
```

### Task 3: Make Stop Cooperative in the Shared Runtime

**Files:**
- Modify: `wandao_core/browser.py:72-82`
- Modify: `wandao_electron/main.js:13-20,1530-1655`
- Create: `tests/test_stop_marker.py`
- Modify: `tests/test_electron_health.py`

**Interfaces:**
- Consumes: Electron's `WANDAO_STOP_FILE` environment variable.
- Produces: `stop_requested(args)` support for a file marker and an 8-second force-kill fallback.

- [ ] **Step 1: Add failing stop-marker tests**

```python
def test_stop_requested_uses_environment_marker(monkeypatch, tmp_path):
    marker = tmp_path / "task.stop"
    monkeypatch.setenv("WANDAO_STOP_FILE", str(marker))
    assert stop_requested(None) is False
    marker.write_text("stop", encoding="utf-8")
    assert stop_requested(None) is True
```

Add a static Electron test that requires `pythonStopFile`, `fs.writeFileSync(pythonStopFile, 'stop', 'utf8')`, and an `8000` timeout before forced termination.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest discover -s tests -p "test_stop_marker.py"` and `python -m unittest discover -s tests -p "test_electron_health.py"`

Expected: both fail on upstream `main`.

- [ ] **Step 3: Implement the marker protocol**

```python
def stop_requested(args: argparse.Namespace | None) -> bool:
    event = getattr(args, "stop_event", None) if args is not None else None
    if event and event.is_set():
        return True
    stop_file = os.environ.get("WANDAO_STOP_FILE", "").strip()
    return bool(stop_file and os.path.exists(stop_file))
```

In `main.js`, allocate a per-task file under `runtime/stops`, delete a stale file before spawn, pass it as `WANDAO_STOP_FILE`, remove it on close/error, and on stop write the marker first. Force-kill only if the same process still runs after 8000ms.

- [ ] **Step 4: Verify GREEN and commit**

Run: `python -m unittest discover -s tests -p "test_stop_marker.py"` and `python -m unittest discover -s tests -p "test_electron_health.py"`

```powershell
git add wandao_core/browser.py wandao_electron/main.js tests/test_stop_marker.py tests/test_electron_health.py
git commit -m "fix: stop provider tasks cooperatively"
```

### Task 4: Pass Checkpoint Args and Present Code 130 as Stopped

**Files:**
- Modify: `wandao_electron/renderer/app.js:3815-3915`
- Test: `tests_js/task_resume.test.js`

**Interfaces:**
- Consumes: `runTrackedPythonCommand`, which injects a task id only when no `--checkpoint-task-id` exists.
- Produces: batch imports with a checkpoint path and a stopped outcome before generic failure handling.

- [ ] **Step 1: Add checkpoint args only for write imports**

Before returning batch/single import arguments, add:

```javascript
if (!options.saveConfig && !options.plan && sourceDir) {
  const root = sourceDir.replace(/[\\/]+$/, '');
  args.push('--checkpoint-file', `${root}/.wandao/yuque-import.sqlite`, '--resume');
}
```

Do not add a separate checkpoint task id. Task history Continue reuses the persisted original id.

- [ ] **Step 2: Handle stopped results before generic failure**

After the success branch in `runYuqueImportCommand`:

```javascript
if (result.code === 130) {
  log(`${title}已停止，已完成项目会在下次继续时跳过。`, 'warn');
  finishProgress(false, `${title}已停止`);
  return null;
}
```

- [ ] **Step 3: Verify GREEN and commit**

Run: `node --test tests_js/task_resume.test.js tests_js/toc_tree.test.js`

```powershell
git add wandao_electron/renderer/app.js tests_js/task_resume.test.js
git commit -m "fix: show stopped Yuque imports correctly"
```

### Task 5: Full Verification and Draft PR

**Files:**
- Verify: all changed files and `plugins/yuque/providers/yuque-import/provider.json`

- [ ] **Step 1: Run full checks**

```powershell
node --test tests_js/task_resume.test.js tests_js/toc_tree.test.js
python -m unittest discover -s tests
python scripts/quality_check.py
git diff --check
```

Find and run any provider validation with `rg -n "provider.*valid|validate.*provider" scripts tests .github`.

- [ ] **Step 2: Inspect scope**

```powershell
git status --short
git diff origin/main...HEAD --check
git log --oneline origin/main..HEAD
```

- [ ] **Step 3: Push and open a Draft PR**

```powershell
git push -u fork codex/fix-yuque-import-resume
gh pr create --repo tllovesxs/wandao --base main --head lilith0-0lilith:codex/fix-yuque-import-resume --draft --title "[codex] Fix Yuque import stop and resume"
```

The PR links `Fixes #37`, calls out the shared stop-runtime overlap with #36, lists validation results, and requests manual validation with a non-sensitive test knowledge base.
