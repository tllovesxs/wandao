# WPS Document Format Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Export identified WPS Smart Documents as Markdown while preserving ordinary WPS cloud files in their original formats.

**Architecture:** Preserve WPS source metadata during scan and add a narrow format-routing decision in the WPS exporter. Identified Smart Documents bypass the original-file download path and use the existing structured-content-to-Markdown writer; ordinary and unknown files retain the current download/fallback behavior.

**Tech Stack:** Python 3, unittest/pytest-compatible tests, existing WPS CDP transport and export task.

## Global Constraints

- Only modify the WPS plugin, WPS tests, and these internal design/plan documents.
- Do not modify other providers or shared UI.
- Continue excluding device/local documents.
- Preserve checkpoint, report, directory, and browser-session behavior.

---

### Task 1: Preserve WPS document kind

**Files:**
- Modify: `plugins/wps/backend/export_wps.py`
- Test: `tests/test_wps_smartdoc.py`

**Interfaces:**
- Consumes: raw search records such as `filetype`, `ftype`, `type`, and `kind`.
- Produces: `WPSNode.document_kind: str | None` and `WPSNode.is_smart_document: bool`.

- [ ] **Step 1: Write a failing scan-normalization test**

Add assertions that a `filetype: "o"` record is marked as a Smart Document and a `filetype: "d"` record is not.

- [ ] **Step 2: Run the focused test and verify failure**

Run: `python -m pytest tests/test_wps_smartdoc.py::WPSDocumentTests::test_document_source_includes_smart_and_regular_documents_but_excludes_device_documents -q`

Expected: FAIL because the normalized nodes do not expose the document kind yet.

- [ ] **Step 3: Implement minimal metadata preservation**

Add an optional document-kind field to `WPSNode`, populate it in normalization, and expose an explicit Smart Document predicate using the known WPS `filetype: "o"` marker.

- [ ] **Step 4: Re-run the focused test**

Run the same command and expect PASS.

### Task 2: Route Smart Documents directly to Markdown

**Files:**
- Modify: `plugins/wps/backend/export_wps.py`
- Test: `tests/test_wps_smartdoc.py`

**Interfaces:**
- Consumes: `WPSNode.is_smart_document`.
- Produces: `.md` output through existing `query_content()` and `write_smart_document()` without calling `open_download()` for identified Smart Documents.

- [ ] **Step 1: Write failing exporter tests**

Add one test where an identified Smart Document has a downloadable original URL but must call `query_content()` and generate `.md` without calling `open_download()`. Add a companion test that an ordinary `.docx` still calls `open_download()` and keeps `.docx`.

- [ ] **Step 2: Run both tests and verify the Smart Document case fails**

Run the two focused test names with `python -m pytest ... -q`.

Expected: the Smart Document test FAILS because current code downloads the original file first; the ordinary-file test passes or remains unchanged.

- [ ] **Step 3: Implement the routing branch**

Before the existing original-download branch, route identified Smart Documents to a safe `.md` target and call the existing content writer. Leave the existing original-download and fallback path for ordinary/unknown documents.

- [ ] **Step 4: Re-run both focused tests**

Expected: PASS.

### Task 3: Update WPS-only user-facing metadata and verify

**Files:**
- Modify: `plugins/wps/providers/wps-export/provider.json`
- Modify: `tests/test_wps_manifest_rework.py`

**Interfaces:**
- Produces: WPS-only title/description/progress text that states Smart Documents become Markdown and other documents retain original files.

- [ ] **Step 1: Update WPS manifest assertions and metadata**

Change only the WPS provider copy so it no longer claims every exported item is an original file.

- [ ] **Step 2: Run all WPS tests**

Run: `python -m pytest tests/test_wps_smartdoc.py tests/test_wps_login_regressions.py tests/test_wps_manifest_rework.py -q`

Expected: all tests PASS.

- [ ] **Step 3: Review scope and diff**

Run: `git diff --check` and `git diff --name-only`.

Expected: only WPS plugin/tests and the two internal documents are changed.

- [ ] **Step 4: Commit locally**

Commit message: `fix: export WPS smart documents as markdown`
