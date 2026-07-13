# Yuque Table Resources and Export Contract Design

**Issue:** #47  
**Base:** `origin/main` at `dabcf4c` (2026-07-13)  
**Status:** Approved for implementation

## Context

The latest upstream `main` already contains the Plugin v1 TOC contract work: Yuque builds hierarchy with `uuid`, emits `doc_id` to the export CLI, and rejects a nonempty selection that produces no document. This change must preserve that contract.

Three gaps remain:

1. The backend selection predicate uses `str(doc_id or uuid)`, which accepts a numeric ID but rejects an old UUID when a document has both fields.
2. `YUQUE_CONVERTER_JS` converts table cells with `td.innerText`; nested `card`, `lake-card`, `img`, and downloadable links are discarded before resource collection.
3. Reports carry typed resource failure counts, but the normalized UI stats do not consistently derive a total resource failure count or present resource warnings separately from document failures.

## Goals

- Preserve UUID-based tree hierarchy and prefer numeric `doc_id` in newly emitted `--doc-id` values.
- Continue accepting either a numeric `doc_id` or a historical UUID for the same Yuque document.
- Collect images and attachments inside table cells without duplicating a resource that appears multiple times in a document.
- Keep table text and line breaks intact; escape Markdown table separators in rendered cell content.
- Make backend reports, final structured events, renderer task summaries, and task history show document failures separately from image/attachment/total resource failures.
- Keep a document export with resource failures as a completed document export with a visible warning, not a false document failure or a silent all-green completion.

## Non-goals

- Do not change other providers' selection-ID contracts.
- Do not introduce credential, cookie, Referer, or retry workarounds for resources omitted before download starts.
- Do not hide deterministic third-party HTTP 403 attachment failures.
- Do not change the prior cooperative stop/checkpoint behavior unless a direct regression is discovered.

## Design

### 1. Canonical output ID plus compatible matching IDs

Introduce a small Yuque-local helper that returns all usable selection IDs for a `DOC`: nonempty `doc_id` and nonempty `uuid`, normalized to strings. Use it in the document filter and every selected-document path/index filter. New UI selections remain numeric `doc_id`; old saved CLI selections with UUID match the same document.

The existing zero-match guard remains immediately after filtering. It still permits an empty source or a full export without `--doc-id`, and it still allows a partially valid selection to export its valid documents.

### 2. Resource-aware table cell conversion

The converter will render each table cell through `inline(td)`, then normalize cell whitespace and table line breaks. This reuses the established image-card, `<img>`, and downloadable-link branches, so those resource objects enter the same normalization, de-duplication, download, localization, and failure-reporting pipeline as paragraph resources.

`normalize_resources` remains the single de-duplication boundary keyed by `(url, kind)`. The table implementation does not add a second resource queue. Markdown table separators in ordinary cell text will be escaped after conversion while keeping image/link markdown usable.

### 3. Resource warning statistics

The Yuque report will explicitly populate `resourceFailureCount` as the sum of image and attachment failures, and its final `task.completed` event will include that total in `stats`. A successful document export with resource failures will emit a warning-level completion message that names the resource warning condition.

The task-report normalizer will derive total resource failures from the explicit total or the typed counters/lists, preserving the greater value to tolerate partially populated historical reports. Task summaries will render image, attachment, and total resource failures independently from document failures. Failure diagnostics will include attachment-only counts as well as image-only counts.

The task status stays `completed` when the Python command succeeds; the history summary and final progress wording identify the resource warning so users do not interpret it as a completely successful asset export.

## Test Strategy

1. A Node/Electron regression test executes the actual `YUQUE_CONVERTER_JS` in a hidden BrowserWindow. Fixtures cover a table image card, a table attachment link, a normal paragraph image, duplicate URLs, text, line breaks, and a table separator.
2. Python unit tests cover selection by numeric `doc_id`, by legacy UUID, no selection, partial match, and explicit zero-match rejection.
3. Renderer task-report tests feed resource-failure report data and assert consistent image, attachment, total-resource counts, diagnostics, and warning summary.
4. Existing TOC, backend selection, provider validation, quality, and full test suites provide compatibility coverage.

## Acceptance Mapping

- Table image cards enter `resources` as `image`, render Markdown image syntax, and are eligible for `localize_resources`.
- A table attachment link enters `resources` as `attachment`.
- Repeated image URLs appear only once after normalization.
- Numeric and UUID selections identify the same document; an entirely invalid explicit selection raises the existing user-readable refresh-directory error before content requests.
- Image/attachment failures remain distinct from document failures while a completed export visibly reports resource warnings.