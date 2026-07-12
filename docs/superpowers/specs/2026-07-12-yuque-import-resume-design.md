# Yuque Import Resume Design

## Goal

Make `yuque-import` stop cooperatively, persist per-Markdown import state, and resume only unfinished or failed items without changing the existing `--skip-existing` and `--update-existing` semantics.

## Scope

This design addresses GitHub Issue #37. It does not change Yuque export behavior, document matching rules, or the user-selected existing-document policy.

## Architecture

The importer will use the existing `wandao_core.checkpoint.WandaoCheckpoint` item store. The legacy Yuque import template will always pass a checkpoint file at `<source-dir>/.wandao/yuque-import.sqlite` and `--resume` for batch imports. The provider manifest declares that capability so task history can retain and reconstruct the same options.

Each item key is the Markdown file's normalized relative path. Before calling the Yuque API, the importer records an item as running. It records completed items only after `import_one_doc` returns successfully. A stopped item and all API failures are recorded as failed. On a resume run, completed items are skipped before considering `--skip-existing` or `--update-existing`; unfinished and failed items are imported using the user-selected existing-document policy. With `--retry-failures`, only checkpoint items marked failed are selected.

The Electron main process will set a per-task stop-marker file before terminating a child process. `wandao_core.browser.stop_requested` will read that marker. Providers can then exit through `ExportStopped`, flush the checkpoint, emit a stopped result, and return code 130. Force-killing remains only the timeout fallback. The Yuque legacy template treats code 130 as a stopped outcome instead of a resource-download error.

## User-visible Behavior

- Stopping after four of sixteen completed documents shows the task as stopped.
- Restarting the same import with resume enabled skips the four completed checkpoint items and processes the other twelve.
- The document interrupted while in progress remains eligible on resume.
- Selecting "only retry failures" processes only failed or interrupted checkpoint items.
- Leaving "update existing documents" unchecked keeps its existing behavior: existing Yuque documents are skipped. It does not mean resume is disabled.

## Failure Handling

`ExportStopped` is a controlled state, not a resource failure. The partial JSON report includes `stopped: true`, and the checkpoint task is marked stopped. An individual import exception remains a failed checkpoint item and continues to be reported with its sanitized path and error. A completed remote update is never written as completed until the API call returns, so an interrupted item may be retried once; completed checkpoint items are never repeated.

## Testing

- Add parser and checkpoint lifecycle tests for Yuque import: completed items are skipped on resume and failed items are selected for retry.
- Add a stopped-path test proving the legacy renderer supplies checkpoint arguments and treats code 130 as stopped.
- Add a main-process static contract test that a stop marker is written before tree termination, plus a browser helper test that recognizes the marker.
- Run the focused JS and Python tests, full project checks, provider validation, and `git diff --check` before opening the Draft PR.

## Merge Relationship

The generic cooperative-stop change overlaps with Draft PR #36 because it belongs in the shared Electron/Python runtime. This branch includes the same runtime behavior so the Yuque fix is independently reviewable. If #36 merges first, the identical shared change can be reconciled during rebase; the Yuque checkpoint and UI changes remain independent.
