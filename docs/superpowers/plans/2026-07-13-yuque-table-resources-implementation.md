# Yuque Table Resources and Export Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore Yuque resources embedded in table cells, preserve backward-compatible document selection IDs, and make resource download warnings visible without misclassifying document exports as failed.

**Architecture:** Keep `export_yuque.py` as the source of truth for Yuque selection, conversion, resource collection, download failures, and report events. Reuse the existing JavaScript `inline()` converter path for table cells and the existing Python `normalize_resources()` de-duplication boundary. The renderer normalizes explicit and legacy resource failure fields into one warning-aware report while preserving document failure counts independently.

**Tech Stack:** Python standard-library `unittest`; Node built-in test runner; Electron/Chromium DOM supplied by existing Electron dependencies.

## Global Constraints

- Work only in `C:\Users\devil\Documents\New project\wandao-worktrees\yuque-table-resources-47` on branch `codex/yuque-table-resources-47`.
- Do not modify other platform exporters or perform unrelated refactors.
- Do not add Cookie, Referer, or retry workarounds for resources that the converter failed to collect.
- Do not hide or reclassify deterministic third-party HTTP 403 attachment failures.
- New UI values emit numeric Yuque `doc_id`; matching must also accept historical UUID values.
- A document with resource errors remains a completed document export and must visibly show a resource warning.

---

### Task 1: Lock down Yuque compatible selection IDs

**Files:**
- Modify: `tests/test_backend_selection_contract.py`
- Modify: `plugins/yuque/backend/export_yuque.py`

**Interfaces:**
- Produces `document_selection_ids(item: dict[str, Any]) -> set[str]`.
- `select_export_docs(toc, selected_doc_ids)` returns only `DOC` items matching either nonempty `doc_id` or `uuid`.
- `require_selected_docs` continues to reject an explicit all-invalid selection only when selectable docs exist.

- [ ] **Step 1: Write failing tests** for a document that has both `doc_id` and `uuid`, valid partial UUID selection, invalid explicit selection, and no-doc source.

```python
selected = select_export_docs(
    [{"type": "DOC", "uuid": "legacy-tree-id", "doc_id": 277273010}],
    {"legacy-tree-id"},
)
self.assertEqual([item["uuid"] for item in selected], ["legacy-tree-id"])
```

- [ ] **Step 2: Run the test** and confirm the UUID case fails because `doc_id or uuid` only compares the numeric ID.

Run: `python -m unittest discover -s tests -p "test_backend_selection_contract.py"`
Expected: FAIL in the UUID selection assertion.

- [ ] **Step 3: Implement the minimum helper and route selection checks through it.** Keep numeric `doc_id` as canonical output/path key, but use candidate-ID set intersection for matching.

```python
def document_selection_ids(item: dict[str, Any]) -> set[str]:
    return {str(value) for value in (item.get("doc_id"), item.get("uuid")) if value not in (None, "")}
```

- [ ] **Step 4: Run selection tests** and confirm the new and existing cases pass.

Run: `python -m unittest discover -s tests -p "test_backend_selection_contract.py"`
Expected: PASS.

- [ ] **Step 5: Commit** the test and minimal compatibility correction.

```powershell
git add tests/test_backend_selection_contract.py plugins/yuque/backend/export_yuque.py
git commit -m "fix(yuque): accept legacy UUID selections"
```

### Task 2: Execute the actual Yuque converter against table resources

**Files:**
- Create: `tests_js/yuque_converter.test.js`
- Modify: `plugins/yuque/backend/export_yuque.py`

**Interfaces:**
- Actual `YUQUE_CONVERTER_JS` executes in Electron Chromium and returns `markdown`, `resources`, and `images`.
- Every table cell is rendered through `inline(td)` and retains line breaks/escaped Markdown separators.
- `normalize_resources` remains the only resource de-duplication layer.

- [ ] **Step 1: Write a failing Electron regression test.** Extract the actual converter source from Python, execute it in a hidden `BrowserWindow`, and pass table HTML containing a Yuque image card, duplicate outside image, attachment link, and `A | B` cell text.

```js
assert.match(result.markdown, /!\[table image\]\(https:\/\/cdn\.example\.test\/table\.png\)/);
assert.deepEqual(result.resources.filter((item) => item.url === imageUrl).map((item) => item.kind), ['image', 'image']);
assert.ok(result.resources.some((item) => item.url === attachmentUrl && item.kind === 'attachment'));
assert.match(result.markdown, /A \\| B/);
```

- [ ] **Step 2: Run the test** and confirm the table image and attachment are absent before the converter change.

Run: `node --test tests_js/yuque_converter.test.js`
Expected: FAIL on the table image/resource assertion.

- [ ] **Step 3: Replace table-cell `innerText` conversion with `inline(td)`** and escape table column separators in the rendered cell text only.

```js
const tableCell = text(inline(td)).replace(/\n+/g, "<br>").replace(/\|/g, "\\|");
```

- [ ] **Step 4: Run the converter test** and assert table image markdown/resources, attachment collection, duplicate collection behavior, and ordinary paragraph images are all correct.

Run: `node --test tests_js/yuque_converter.test.js`
Expected: PASS.

- [ ] **Step 5: Add a Python behavior check for `normalize_resources` if needed** so the same `(url, kind)` appears exactly once after normalization.

- [ ] **Step 6: Commit** the test and minimal converter correction.

```powershell
git add tests_js/yuque_converter.test.js plugins/yuque/backend/export_yuque.py
git commit -m "fix(yuque): collect resources inside table cells"
```

### Task 3: Make resource failure warnings consistent from backend through renderer

**Files:**
- Modify: `plugins/yuque/backend/export_yuque.py`
- Modify: `wandao_electron/renderer/task_report.js`
- Modify: `wandao_electron/renderer/app.js` only if presentation cannot consume normalized report stats alone.
- Create: `tests_js/task_report.test.js`

**Interfaces:**
- Backend final report and `task.completed.stats` include `resourceFailureCount`.
- `normalizeTaskReport` returns independent `failed`, `imageFailed`, `attachmentFailed`, and `resourceFailed` values.
- Summaries and diagnostics disclose resource warnings without changing document failure count.

- [ ] **Step 1: Write failing renderer tests** using a report with one failed image and one failed attachment but zero document failures.

```js
assert.equal(report.stats.failed, 0);
assert.equal(report.stats.imageFailed, 1);
assert.equal(report.stats.attachmentFailed, 1);
assert.equal(report.stats.resourceFailed, 2);
assert.match(report.summary, /图片失败 1/);
assert.match(report.summary, /附件失败 1/);
assert.match(report.summary, /资源失败 2/);
```

- [ ] **Step 2: Run the test** and confirm current normalization/summary lacks typed resource warning coverage.

Run: `node --test tests_js/task_report.test.js`
Expected: FAIL on total resource failures or warning summary.

- [ ] **Step 3: Implement report totals and warning wording.** Use the greatest trustworthy count from explicit total, failure-list length, or typed counts; preserve document failure separately. Add attachment fallback diagnostics. Add backend total to report and final event; use warning-level completion text when resource failures exist.

- [ ] **Step 4: Run renderer and relevant Python tests.**

Run: `node --test tests_js/task_report.test.js`
Expected: PASS.

Run: `python -m unittest discover -s tests -p "test_yuque_import_resources.py"`
Expected: PASS.

- [ ] **Step 5: Commit** the warning observability work.

```powershell
git add plugins/yuque/backend/export_yuque.py wandao_electron/renderer/task_report.js wandao_electron/renderer/app.js tests_js/task_report.test.js
git commit -m "fix(yuque): surface resource download warnings"
```

### Task 4: Verify, inspect, publish

**Files:**
- Modify only if verification reveals a direct regression.

- [ ] **Step 1: Run targeted checks.**

```powershell
node --test tests_js/toc_tree.test.js tests_js/task_resume.test.js tests_js/yuque_converter.test.js tests_js/task_report.test.js
python -m unittest discover -s tests -p "test_backend_selection_contract.py"
python -m unittest discover -s tests -p "test_yuque_import_resources.py"
```

- [ ] **Step 2: Run full project checks.**

```powershell
python -m unittest discover -s tests -p "test_*.py"
python scripts/quality_check.py
Set-Location wandao_electron; npm run check
Set-Location ..; git diff --check
```

- [ ] **Step 3: Review the final diff and commits.** Confirm only Yuque/report/test/docs files changed, resource failures are distinct from document failures, and no retry/auth workaround was introduced.

- [ ] **Step 4: Push and open the PR.**

```powershell
git push -u origin codex/yuque-table-resources-47
# If upstream rejects direct push:
git push -u fork codex/yuque-table-resources-47
gh pr create --repo tllovesxs/wandao --base main --head lilith0-0lilith:codex/yuque-table-resources-47 --title "fix(yuque): export table resources and show failures" --body "Fixes #47"
```

- [ ] **Step 5: Report** the root cause, files, test evidence, manual-URL status, and clarify that a continuing S3 403 is a third-party attachment access restriction, not a table-image regression.
