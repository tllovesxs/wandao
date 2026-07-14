# Directory Scan Result Log Filtering Design

**Date:** 2026-07-14
**Status:** Approved by user (方案 A)

## Problem
Directory scans return their full TOC as pretty-printed JSON on Python stdout. Electron currently forwards every stdout chunk to the renderer as a real-time log. Large TOCs therefore enqueue thousands of IPC/log/DOM operations after the scan has completed, causing UI stalls and huge diagnostic reports.

## Decision
For a Python command whose arguments include `--scan-toc`, the Electron main process will retain all stdout for `parseProcessResult`, but it will stop forwarding stdout to the renderer once it reaches the terminal JSON object. Stderr remains forwarded unchanged. Ordinary pre-result stdout and structured `@@WANDAO_LOG@@` entries remain live.

## Scope
- Shared Electron execution path only; applies to every provider with a `--scan-toc` action.
- No Python provider output or TOC parsing contract changes.
- Do not filter final JSON from exports, imports, login, or failures.

## Success criteria
1. Logs before the result remain visible.
2. Terminal scan JSON is never sent over `python-log`.
3. Complete stdout is still parsed into the exact same result data.
4. Chunk boundaries and a JSON object beginning after a partial line are handled.
5. Existing test suite stays green.
