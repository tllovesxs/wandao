# Feishu native-doc export scroll completeness (Issue #129)

## Problem

Users report that Feishu Wiki Markdown export sometimes only captures the beginning of a long native document. The suspect document in #129 is a native Feishu doc (`obj_type=22`) exported via DOM scraping, not the Markdown-file download path.

Current native-doc conversion lives in `FEISHU_CONVERTER_JS` inside `plugins/feishu/backend/export_feishu.py`. It:

1. Finds a scroll container from the editor root.
2. Scrolls in steps while collecting `[data-block-type]` children.
3. Stops after a short “stable at bottom” window (max 80 iterations).

Feishu’s editor uses virtualized / lazily mounted blocks. If the wrong scroller is chosen, or `scrollHeight` grows after the loop already decided it was done, later blocks never enter the DOM and never get exported. The exporter still reports success with `contentIncomplete: false`.

There is also an unused helper `materialize_doc_dom()` that only scrolls `window` and is not called from the export path, so it does not mitigate this bug.

## Goals

- Export long native Feishu docs as completely as practical by fixing DOM scroll collection.
- Keep the existing DOM → Markdown rendering (`renderBlock` / `currentBlocks`) intact.
- Stay compatible with current Feishu page structure; minimize behavioral churn outside scroll/collection.
- Cover the fix with deterministic tests that simulate lazy height growth / wrong-scroller risk.

## Non-goals

- Do not switch native docs to a Feishu export/content API in this change.
- Do not change the Markdown-file (`obj_type=12`) download / preview-fallback path.
- Do not add a new user-facing incomplete/warning contract in this change (can be a follow-up).
- Do not require live Feishu credentials for the automated tests.

## Approach (chosen)

Harden the existing DOM scroll collector in `FEISHU_CONVERTER_JS`.

Rejected alternatives:

- **Pre-scroll via `materialize_doc_dom` then convert**: easy to wire, but keeps a second, weaker scroll path and still fails if the container is wrong.
- **Switch to Feishu export APIs**: better long-term completeness potential, but out of agreed scope and higher auth/contract risk.

## Design

### 1. Scroll-container resolution

Replace the current “first ancestor with overflow scroll” heuristic with a small resolver:

1. Walk ancestors of the editor root (`.root-render-unit-container` / `.page-main-item.editor` / `.editor-container`).
2. Collect candidates that look scrollable (`overflowY` auto/scroll and `scrollHeight > clientHeight`).
3. Also consider a short allowlist of common Feishu shell scrollers if present (e.g. page main / wiki content panes), without hard-depending on one brittle class forever.
4. Prefer the candidate that can actually accept a temporary `scrollTop` change and restore it (probe + restore).
5. Fall back to `document.scrollingElement` / `document.documentElement` only after candidates fail.

The probe must restore scroll position so export does not leave the page scrolled mid-document if collection aborts early.

### 2. Scroll + collect loop

Keep incremental collection into `seen` / `rendered`, but change termination:

- Recompute `maxY` every iteration from current `scrollHeight` / viewport.
- After each scroll, wait briefly, then collect.
- Treat “bottom reached” as provisional: require **4** consecutive stable rounds where **both**:
  - no new blocks were collected, and
  - `scrollHeight` did not grow.
- If height grows or new blocks appear, reset the stable counter and continue.
- Keep a hard ceiling of about **120** scroll iterations (up from 80) so a broken page cannot hang forever; surface whatever was collected (same as today) rather than inventing a new failure mode in this change.
- Remove today’s extra `stable += 1` when already at `maxY`. That double-counts bottom contact and exits before lazy mounts finish.

Do not change Markdown rendering rules in this pass.

### 3. Call-site cleanup

- Native-doc path continues to: navigate → `wait_for_doc_ready` → evaluate converter JS.
- Remove unused `materialize_doc_dom` (or fold its intent into the converter only). Do not leave two scroll implementations.

### 4. Observability (minimal)

Return existing converter fields (`blockCount`, `textLength`, `renderer: "native_doc"`). No new incomplete flag in this change. Optional low-noise debug fields (e.g. scroll iterations / final scrollHeight) are allowed if cheap and useful for future triage, but not required for the user-facing report.

### 5. Testing

Extract the scroll-container resolver and scroll/collect loop from `FEISHU_CONVERTER_JS` into named JS helper functions inside the same Python string (or a small adjacent JS snippet loaded by tests). Drive those helpers from Python unit tests with a minimal fake DOM object (no live browser, no Feishu credentials).

Minimum scenarios:

1. Correct nested scroller is chosen over `window` when only the nested pane scrolls.
2. Lazy height growth after first bottom contact continues collection until height stabilizes.
3. Existing Feishu session / Markdown-file tests still pass.

Synthetic fixtures must mimic:

- nested overflow scroller
- blocks mounted only after scrollTop crosses thresholds
- growing `scrollHeight`

### 6. Plugin / release notes

Bump feishu plugin patch version when shipping the fix. Mention issue #129 in changelog / release notes for the plugin or app release that includes it.

## Success criteria

- Synthetic long-doc fixture that previously stopped early now collects all mounted blocks after lazy growth.
- Nested-scroller fixture chooses the inner pane, not `window`.
- Existing `tests/test_feishu_*.py` suite remains green.
- No intentional change to Markdown-file export behavior.

## Follow-ups (out of scope)

- Mark native-doc exports incomplete when heuristics suggest truncation (e.g. suspiciously short body for a tall page).
- Evaluate official/export API path if DOM virtualization keeps changing.
- Re-test against the public sample wiki from #129 once a maintainer with access can verify end-to-end.
