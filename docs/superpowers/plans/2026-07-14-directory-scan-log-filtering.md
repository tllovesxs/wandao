# Directory Scan Log Filtering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent large directory scan result JSON from reaching the renderer's real-time log channel while preserving logs and result parsing.

**Architecture:** Add a small, testable stdout relay helper in `wandao_electron` that is enabled only for `--scan-toc`. The helper buffers line fragments, forwards ordinary complete lines until the final JSON object begins, and suppresses that terminal object. `main.js` continues accumulating raw stdout unchanged for `parseProcessResult`.

**Tech Stack:** Node.js CommonJS, Electron IPC, `node:test`.

## Global Constraints
- Preserve stdout byte content for `parseProcessResult`.
- Preserve stderr forwarding and non-scan behavior.
- Change no Python provider protocol.
- Use test-driven development and observe a failing test before production code.

---

### Task 1: Scan stdout relay

**Files:**
- Create: `wandao_electron/scan_stdout_relay.js`
- Modify: `wandao_electron/main.js`
- Modify: `wandao_electron/package.json`
- Create: `tests_js/scan_stdout_relay.test.js`

**Interfaces:**
- Produces `createScanStdoutRelay(send)` with `push(chunk)` and `flush()` methods.
- `push` forwards complete ordinary log lines to `send`; after the first terminal JSON object line starting at column zero (`{`), it suppresses the remainder.
- `flush` forwards an unfinished ordinary log fragment only when no JSON result has started.

- [ ] Write tests for ordinary logs, pretty JSON, chunk-split JSON start, and incomplete log flush.
- [ ] Run the new test and verify it fails because the helper is absent.
- [ ] Implement the minimal relay helper.
- [ ] Update `main.js` to use the helper only when command args contain `--scan-toc`, while retaining raw stdout and existing stderr behavior.
- [ ] Run focused and complete JS tests plus syntax checks.
- [ ] Inspect the diff and restart the source Electron application.
