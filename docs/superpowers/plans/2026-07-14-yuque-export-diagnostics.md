# Yuque Export Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Yuque export stop, API failure, and resource download diagnostics accurately flow from the Python exporter into Electron.

**Architecture:** The exporter safely normalizes the browser-side document API response and prints a complete terminal result. Electron treats an explicit stopped payload as stopped before considering process success, while its existing task-report machinery receives the resource failure arrays it already understands.

**Tech Stack:** Python 3.10+, unittest, Node.js built-in test runner, Electron renderer JavaScript.

## Global Constraints

- Work only in `C:\Users\devil\Documents\New project\wandao-worktrees\fix-yuque-export-diagnostics` on branch `codex/fix-yuque-export-diagnostics`.
- Do not edit `D:\项目\Wandao` or the primary workspace checkout.
- Never include credentials, authorization headers, cookies, or document body content in diagnostics.
- Preserve resource warnings separately from document export failures.

---

### Task 1: Exporter response safety and terminal result

**Files:**
- Modify: `plugins/yuque/backend/export_yuque.py`
- Modify: `tests/test_backend_selection_contract.py`

**Interfaces:**
- Produces: `fetch_doc_markdown()` raises `ExportError` with safe API diagnostics when the browser response has `apiError`.
- Produces: `main()` prints detailed terminal report fields and returns `130` if `report['stopped']` is true.

- [ ] **Step 1: Write failing tests** for missing API data, stopped exit status, and complete terminal resource result.
- [ ] **Step 2: Run** `python -m unittest tests.test_backend_selection_contract -v` and confirm the new assertions fail before implementation.
- [ ] **Step 3: Add minimal response validation and terminal-report serialization** without logging secrets or content.
- [ ] **Step 4: Re-run** `python -m unittest tests.test_backend_selection_contract -v` and confirm all assertions pass.

### Task 2: Renderer stopped-result precedence

**Files:**
- Modify: `wandao_electron/renderer/app.js`
- Modify: `tests_js/task_resume.test.js`

**Interfaces:**
- Produces: `isStoppedResult(result)` that accepts exit code `130` and `data.stopped === true`.
- Consumes: results returned by `runTrackedPythonCommand()`.

- [ ] **Step 1: Write failing source-contract tests** asserting explicit stopped payloads are handled before success in history finalization and generic export handling.
- [ ] **Step 2: Run** `node --test tests_js/task_resume.test.js` and confirm the new assertions fail.
- [ ] **Step 3: Introduce `isStoppedResult()` and replace the relevant success/stop decision points.**
- [ ] **Step 4: Re-run** `node --test tests_js/task_resume.test.js` and confirm all assertions pass.

### Task 3: Integration verification and source launch

**Files:**
- Verify: `tests_js/task_report.test.js`
- Verify: `wandao_electron/package.json`

- [ ] **Step 1: Run exporter and renderer focused suites.**
- [ ] **Step 2: Run `npm run check` in `wandao_electron`.**
- [ ] **Step 3: Inspect `git diff --check` and the complete diff for scope and secret safety.**
- [ ] **Step 4: Start the isolated source Electron client with `npm run dev` for user testing.**
