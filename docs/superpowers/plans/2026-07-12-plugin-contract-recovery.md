# Plugin Contract Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore a manifest-driven directory selection contract for the Yuque, Aliyun, and Feishu first batch and consolidate their stop/resume regressions into one Draft PR for Issue #33.

**Architecture:** Keep provider-specific source fields in each `provider.json`; normalize them once in `toc_tree.js`; generate CLI selection arguments from normalized export IDs. Python backends remain the authority for filtering and receive the same IDs asserted by the frontend tests. Existing Feishu and Yuque import stop/resume behavior is incorporated without bringing the second-batch Youdao work into this branch.

**Tech Stack:** Node built-in test runner, Electron renderer JavaScript, Python `unittest`, Provider v1 JSON schema, GitHub Draft PR.

## Global Constraints

- Do not modify `main`, release, package, or push an upstream branch directly.
- Keep all scan fixtures sanitized and never commit cookies, tokens, URLs with secrets, or user exports.
- First batch is Yuque, Aliyun Thoughts, and Feishu only; defer Youdao and the remaining providers to Issue #33 batch two.
- Validate with the four Issue #33 commands and provider validation before opening the Draft PR.

---

### Task 1: Make Provider TOC Contracts Schema-Visible

**Files:**
- Modify: `providers/provider.schema.json`
- Modify: `plugins/yuque/providers/yuque/provider.json`
- Modify: `plugins/aliyun_thoughts/providers/aliyun/provider.json`
- Modify: `plugins/feishu/providers/feishu-export/provider.json`
- Test: `tests/test_provider_validation.py`

**Interfaces:**
- Consumes: Provider `toc` fields.
- Produces: `typeKey`, `selectableTypes`, and accurate paths/keys accepted by provider validation.

- [ ] **Step 1: Write the failing provider contract assertions**

```python
self.assertEqual(yuque["toc"]["itemsPath"], "toc")
self.assertEqual(yuque["toc"]["exportIdKey"], "doc_id")
self.assertEqual(yuque["toc"]["selectableTypes"], ["DOC"])
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `python -m unittest tests.test_provider_validation`
Expected: FAIL because the current Yuque mapping is `ordered`/`uuid` and the schema does not declare type selection fields.

- [ ] **Step 3: Add the schema properties and first-batch mappings**

```json
"typeKey": { "type": "string" },
"selectableTypes": { "type": "array", "items": { "type": "string" } }
```

Set Yuque to `toc`, `uuid`, `doc_id`, `parent_uuid`, `type`, and `["DOC"]`. Set Aliyun to its `nodes` array and `parent_id`. Set Feishu to `ordered` with explicit type/selectability constraints that exclude folders and nodes without a usable export URL/token.

- [ ] **Step 4: Run provider validation**

Run: `python scripts/quality_check.py`
Expected: provider validation and its focused suite pass.

### Task 2: Test Normalization And CLI Selection End To End

**Files:**
- Modify: `wandao_electron/renderer/toc_tree.js`
- Modify: `wandao_electron/renderer/app.js`
- Modify: `tests_js/toc_tree.test.js`

**Interfaces:**
- Consumes: `normalizeStandardTocNodes(provider, scanData)`.
- Produces: `selectionArgs(provider, exportIds)` and `selectedTocArgs(toolId)` that emit the manifest `selectionArg` with normalized `exportId` values.

- [ ] **Step 1: Write failing sanitized fixture tests**

```javascript
assert.equal(nodes.find((node) => node.title === 'Folder').selectable, false);
assert.equal(nodes.find((node) => node.title === 'Doc').exportId, '277273010');
assert.deepEqual(selectionArgs(provider, ['277273010']), ['--doc-id', '277273010']);
```

Include one fixture each for Yuque `TITLE` plus `DOC`, Aliyun `nodes` with `parent_id`, and Feishu `ordered` with folder/non-URL/doc cases.

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `node --test tests_js/toc_tree.test.js`
Expected: FAIL because the current Yuque and Aliyun manifests select fallback IDs and paths.

- [ ] **Step 3: Extract pure selection-argument generation**

```javascript
function selectionArgs(provider, exportIds) {
  const selectionArg = provider?.toc?.selectionArg || '--doc-id';
  return exportIds.flatMap((exportId) => [selectionArg, exportId]);
}
```

Make `selectedTocArgs` delegate to this function after the existing ZSXQ special case. Keep default selection based solely on normalized `node.selectable`.

- [ ] **Step 4: Run the focused test to verify it passes**

Run: `node --test tests_js/toc_tree.test.js`
Expected: all contract fixtures pass.

### Task 3: Verify Backend Filtering Uses The Contract Export IDs

**Files:**
- Modify: `tests/test_yuque_provider_contract.py`
- Modify: `tests/test_aliyun_provider_contract.py`
- Modify: `tests/test_feishu_provider_contract.py`
- Modify only if test evidence requires it: `plugins/yuque/backend/export_yuque.py`, `plugins/aliyun_thoughts/backend/export_aliyun_thoughts.py`, `plugins/feishu/backend/export_feishu.py`

**Interfaces:**
- Consumes: scan fixture document IDs and CLI `--doc-id` values.
- Produces: backend document lists whose selected IDs exactly match the frontend fixture export IDs.

- [ ] **Step 1: Write failing filtering tests**

```python
docs = [title, doc]
selected = filter_docs(docs, {"277273010"})
self.assertEqual([item["doc_id"] for item in selected], [277273010])
```

Use each backend's existing public helper where present; otherwise test the smallest extracted pure predicate.

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `python -m unittest tests.test_yuque_provider_contract tests.test_aliyun_provider_contract tests.test_feishu_provider_contract`
Expected: FAIL until the source mapping and selected-ID predicate agree.

- [ ] **Step 3: Apply the minimal backend correction**

Keep Yuque filtering on `doc_id`, Aliyun filtering on its emitted export ID, and Feishu filtering on its emitted document token. Do not add UI fallback fields to make a wrong manifest appear to work.

- [ ] **Step 4: Run the focused tests to verify they pass**

Run: `python -m unittest tests.test_yuque_provider_contract tests.test_aliyun_provider_contract tests.test_feishu_provider_contract`
Expected: all tests pass.

### Task 4: Consolidate Feishu And Yuque Cooperative Stop/Resume

**Files:**
- Modify: `plugins/feishu/backend/import_feishu.py`
- Modify: `plugins/feishu/providers/feishu-import/provider.json`
- Modify: `plugins/yuque/backend/import_yuque.py`
- Modify: `plugins/yuque/providers/yuque-import/provider.json`
- Modify: `wandao_core/browser.py`
- Modify: `wandao_electron/main.js`
- Modify: `wandao_electron/renderer/app.js`
- Test: `tests/test_feishu_import_resume.py`, `tests/test_yuque_import_resume.py`, `tests/test_stop_marker.py`, `tests_js/task_resume.test.js`

**Interfaces:**
- Consumes: a stop marker and `--checkpoint-file`, `--resume`, `--retry-failed` provider arguments.
- Produces: code `130`, persisted item/task states, and a renderer stopped result distinct from resource or permission errors.

- [ ] **Step 1: Import the existing Draft PR tests before implementation**

Fetch the `lilith0-0lilith/wandao` PR heads and apply only the test commits/files for #36 and #38. Run them against this branch before applying their implementation changes.

- [ ] **Step 2: Confirm the new tests fail on the baseline**

Run: `node --test tests_js/task_resume.test.js` and `python -m unittest tests.test_feishu_import_resume tests.test_yuque_import_resume tests.test_stop_marker`
Expected: failures for missing checkpoint declarations, cooperative stop, or stopped-result rendering.

- [ ] **Step 3: Apply the reviewed #36 and #38 implementation changes**

Use `check_stopped` at document boundaries, persist item completion/failure, preserve completed items for resume, return `130` for a user stop, and map `130` to the renderer stopped state. Keep retry-failed restricted to checkpoint failures.

- [ ] **Step 4: Run regression tests**

Run: `node --test tests_js/task_resume.test.js` and `python -m unittest tests.test_feishu_import_resume tests.test_yuque_import_resume tests.test_stop_marker`
Expected: all stop/resume tests pass.

### Task 5: Full Verification And Draft PR

**Files:**
- Modify: `docs/announcements/` only if a repository PR evidence template requires it.

**Interfaces:**
- Consumes: completed first-batch implementation and test output.
- Produces: one Draft PR linked to Issue #33, with sanitized fixture coverage and explicit manual-login gaps.

- [ ] **Step 1: Run required checks**

Run: `node --test tests_js/toc_tree.test.js`, `python -m unittest`, `python scripts/quality_check.py`, and `git diff --check`.
Expected: all commands pass.

- [ ] **Step 2: Review the diff for credentials and scope**

Run: `git diff --check` and `git status --short`.
Expected: no whitespace errors, no credentials, and no second-batch provider changes.

- [ ] **Step 3: Commit intentional files and open one Draft PR**

```text
git add <explicit first-batch files>
git commit -m "fix: restore first-batch plugin export contracts"
git push -u origin codex/plugin-contract-recovery
gh pr create --draft --base main --head codex/plugin-contract-recovery
```

The PR body links #33, lists the three sanitized contract fixtures, gives exact command results, and lists manual login validation for Yuque, Aliyun, and Feishu.
