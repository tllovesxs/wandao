# Feishu Export Scroll Completeness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Feishu native-doc Markdown export collect the full lazily-mounted document body by hardening scroll-container detection and bottom-stability logic in `FEISHU_CONVERTER_JS` (issue #129).

**Architecture:** Keep the existing DOM → Markdown converter. Extract scroll-container resolution and the scroll/collect loop into named JS helpers inside `FEISHU_CONVERTER_JS`, then drive those helpers from a Node test that builds a synthetic nested-scroller DOM (same pattern as `tests_js/yuque_converter.test.js`). Remove the unused `materialize_doc_dom` helper so only one scroll path remains.

**Tech Stack:** Python exporter string embedding JS (`plugins/feishu/backend/export_feishu.py`), Node `node:test` + `linkedom` via `wandao_electron` deps, existing unittest suite for Feishu session/Markdown paths.

## Global Constraints

- Scope is native docs only (`obj_type=22` / `FEISHU_CONVERTER_JS`); do not change Markdown-file download / preview-fallback behavior.
- Do not add a new user-facing `incomplete` warning contract in this change.
- Do not require live Feishu credentials or a real browser for automated tests.
- Bottom stability requires **4** consecutive rounds with no new blocks and no `scrollHeight` growth.
- Hard ceiling is about **120** scroll iterations.
- Remove the extra `stable += 1` when already at `maxY`.
- Follow the approved design: `docs/superpowers/specs/2026-08-23-feishu-export-scroll-incomplete-design.md`.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `plugins/feishu/backend/export_feishu.py` | Own `FEISHU_CONVERTER_JS` helpers + remove unused `materialize_doc_dom` |
| `tests_js/feishu_converter_scroll.test.js` | Synthetic DOM tests for scroller choice and lazy height growth |
| `tests/test_feishu_session_and_markdown.py` | Existing regression guard; only touch if a call-site rename forces it |
| `plugins/feishu/plugin.json` | Patch version bump when shipping the fix |

---

### Task 1: Failing scroll-collector tests

**Files:**
- Create: `tests_js/feishu_converter_scroll.test.js`
- Modify: `plugins/feishu/backend/export_feishu.py` (only enough to expose named helpers the test can call; keep old loop behavior until Task 2)
- Test: `tests_js/feishu_converter_scroll.test.js`

**Interfaces:**
- Consumes: `FEISHU_CONVERTER_JS` source text from `plugins/feishu/backend/export_feishu.py`
- Produces:
  - `resolveFeishuDocScroller(root, document)` → Element
  - `collectFeishuDocBlocks({ root, scroller, document, sleep, renderBlock, currentBlocks, maxIterations })` → `{ rendered: string[], blockCount: number, scrollIterations: number }`
  - Test harness loads converter helpers the same way `tests_js/yuque_converter.test.js` loads `YUQUE_CONVERTER_JS`

- [ ] **Step 1: Add named helper stubs inside `FEISHU_CONVERTER_JS` without changing behavior yet**

In `plugins/feishu/backend/export_feishu.py`, inside `FEISHU_CONVERTER_JS`, wrap the existing scroller/collection logic behind named functions that the outer async IIFE still calls. Keep today’s termination semantics (`stable >= 2`, extra `stable += 1` at bottom, 80 iterations) so production behavior is unchanged until Task 2.

Shape to expose (exact names required by later tasks/tests):

```javascript
function resolveFeishuDocScroller(root, doc) {
  // current findScroller behavior for now
}

async function collectFeishuDocBlocks(options) {
  // current scroll/collect loop for now
  // options: { root, scroller, document, sleep, setScroll, currentBlocks, renderBlock, maxIterations }
  // return { rendered, blockCount, scrollIterations }
}
```

Also attach them for tests without breaking CDP evaluate:

```javascript
const api = { resolveFeishuDocScroller, collectFeishuDocBlocks };
if (typeof globalThis !== "undefined") globalThis.__feishuConverterScrollApi = api;
```

The existing converter return value must remain `{ title, markdown, images, blockCount, textLength, renderer: "native_doc" }`.

- [ ] **Step 2: Write the failing Node tests**

Create `tests_js/feishu_converter_scroll.test.js`:

```javascript
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { createRequire } = require('node:module');
const path = require('node:path');
const test = require('node:test');

const repoRoot = path.resolve(__dirname, '..');
const exporterPath = path.join(repoRoot, 'plugins', 'feishu', 'backend', 'export_feishu.py');
const desktopRequire = createRequire(path.join(repoRoot, 'wandao_electron', 'package.json'));
const { parseHTML } = desktopRequire('linkedom');

function loadScrollApi() {
  const source = fs.readFileSync(exporterPath, 'utf8');
  const match = source.match(/FEISHU_CONVERTER_JS = r?"""([\s\S]*?)"""/);
  assert.ok(match, 'FEISHU_CONVERTER_JS was not found');
  const { document, window } = parseHTML('<!doctype html><html><body></body></html>');
  // Provide browser globals used by the helper source.
  global.document = document;
  global.window = window;
  global.getComputedStyle = (el) => {
    const overflowY = el.getAttribute('data-overflow-y') || 'visible';
    return { overflowY };
  };
  // Evaluate only the helper definitions by executing the converter factory once
  // against a tiny document, then reading globalThis.__feishuConverterScrollApi.
  delete globalThis.__feishuConverterScrollApi;
  const factory = Function(
    'document',
    'window',
    'getComputedStyle',
    `"use strict"; return (${match[1]});`
  );
  // Call with a title so the async function starts; ignore the promise and use the API side-effect.
  factory(document, window, getComputedStyle)('title');
  assert.ok(globalThis.__feishuConverterScrollApi, 'scroll api was not exported');
  return globalThis.__feishuConverterScrollApi;
}

function makeLazyDoc() {
  const { document, window } = parseHTML(`<!doctype html><html><body>
    <div class="shell" data-overflow-y="visible" style="height:200px">
      <div class="page-scroller" data-overflow-y="auto" style="height:200px; overflow:auto">
        <div class="root-render-unit-container">
          <div class="render-unit-wrapper"></div>
        </div>
      </div>
    </div>
  </body></html>`);
  const scroller = document.querySelector('.page-scroller');
  const wrapper = document.querySelector('.render-unit-wrapper');
  // Fake geometry because linkedom does not implement layout.
  Object.defineProperty(scroller, 'clientHeight', { configurable: true, get: () => 200 });
  let scrollHeight = 400;
  let scrollTop = 0;
  Object.defineProperty(scroller, 'scrollHeight', { configurable: true, get: () => scrollHeight });
  Object.defineProperty(scroller, 'scrollTop', {
    configurable: true,
    get: () => scrollTop,
    set: (value) => {
      scrollTop = value;
      // After the first time we reach the old bottom, grow and mount more blocks.
      if (scrollTop >= scrollHeight - 200 && wrapper.childElementCount < 4) {
        scrollHeight = 900;
        for (const [type, text] of [
          ['paragraph', 'block-3'],
          ['paragraph', 'block-4'],
        ]) {
          if ([...wrapper.children].some((el) => el.textContent === text)) continue;
          const block = document.createElement('div');
          block.setAttribute('data-block-type', type);
          block.setAttribute('data-block-id', text);
          block.textContent = text;
          wrapper.appendChild(block);
        }
      }
    },
  });
  // Seed the first viewport blocks.
  for (const [id, text] of [
    ['block-1', 'block-1'],
    ['block-2', 'block-2'],
  ]) {
    const block = document.createElement('div');
    block.setAttribute('data-block-type', 'paragraph');
    block.setAttribute('data-block-id', id);
    block.textContent = text;
    wrapper.appendChild(block);
  }
  Object.defineProperty(window, 'innerHeight', { configurable: true, get: () => 200 });
  Object.defineProperty(document.documentElement, 'clientHeight', { configurable: true, get: () => 200 });
  Object.defineProperty(document.documentElement, 'scrollHeight', { configurable: true, get: () => 200 });
  return { document, window, scroller, root: document.querySelector('.root-render-unit-container'), wrapper };
}

test('resolveFeishuDocScroller prefers the nested page scroller over window', () => {
  const api = loadScrollApi();
  const { document, root, scroller } = makeLazyDoc();
  const resolved = api.resolveFeishuDocScroller(root, document);
  assert.equal(resolved, scroller);
});

test('collectFeishuDocBlocks keeps scrolling after lazy scrollHeight growth', async () => {
  const api = loadScrollApi();
  const { document, root, scroller, wrapper } = makeLazyDoc();
  const sleep = async () => {};
  const currentBlocks = () => [...wrapper.children].filter((el) => el.getAttribute('data-block-type'));
  const renderBlock = (el) => el.textContent || '';
  const result = await api.collectFeishuDocBlocks({
    root,
    scroller,
    document,
    sleep,
    currentBlocks,
    renderBlock,
    maxIterations: 120,
  });
  assert.deepEqual(result.rendered, ['block-1', 'block-2', 'block-3', 'block-4']);
  assert.equal(result.blockCount, 4);
});
```

If the helper-loading approach above is awkward because the converter is `async (fallbackTitle) => { ... }`, adjust `loadScrollApi()` to extract and `Function`-evaluate only the helper function source after a clearer split marker comment such as `/* feishu-scroll-api:start */` … `/* feishu-scroll-api:end */`. Prefer that marker approach if Step 1’s side-effect export proves brittle.

- [ ] **Step 3: Run the new tests and confirm the lazy-growth case fails on current loop semantics**

Run from repo root (PowerShell):

```powershell
node --test tests_js/feishu_converter_scroll.test.js
```

Expected:
- scroller-preference test may pass already with current ancestor walk, or fail if probe/allowlist not present yet
- `collectFeishuDocBlocks keeps scrolling after lazy scrollHeight growth` **FAIL** because the old loop exits after bottom double-count before blocks 3/4 mount/stabilize

If both accidentally pass before Task 2, tighten the fixture so growth happens one iteration after first bottom contact, matching the bug.

- [ ] **Step 4: Commit the failing tests and helper stubs**

```bash
git add plugins/feishu/backend/export_feishu.py tests_js/feishu_converter_scroll.test.js
git commit -m "$(cat <<'EOF'
test(feishu): add failing native-doc scroll collector coverage

Expose scroll helpers and lock in nested-scroller + lazy height
fixtures for issue #129 before changing termination behavior.
EOF
)"
```

---

### Task 2: Harden scroll resolution and collection loop

**Files:**
- Modify: `plugins/feishu/backend/export_feishu.py` (`FEISHU_CONVERTER_JS`, delete `materialize_doc_dom`)
- Test: `tests_js/feishu_converter_scroll.test.js`
- Test: `tests/test_feishu_session_and_markdown.py` (run only; no intentional edits)

**Interfaces:**
- Consumes: helper names from Task 1
- Produces: updated `resolveFeishuDocScroller` / `collectFeishuDocBlocks` behavior per design

- [ ] **Step 1: Implement `resolveFeishuDocScroller`**

Replace the stub with:

1. Walk ancestors from `root` toward `document.body`.
2. Collect elements where `overflowY` matches `/(auto|scroll)/` and `scrollHeight > clientHeight + 30`.
3. Also push any present allowlist matches, e.g. `.page-main`, `[class*="scroll"]` near the editor shell — keep the allowlist short and non-exclusive.
4. Probe each candidate: save `scrollTop`, set `scrollTop = min(42, maxScroll)`, accept if it changed, restore original `scrollTop`.
5. Prefer the deepest successful probed candidate; else fall back to `document.scrollingElement || document.documentElement`.

- [ ] **Step 2: Implement hardened `collectFeishuDocBlocks`**

Required semantics:

- Start at `y = 0`, collect once.
- Loop up to `maxIterations` default **120**.
- Each iteration:
  - `viewport = scrollWindow ? window.innerHeight : scroller.clientHeight`
  - `maxY = max(0, scroller.scrollHeight - viewport)`
  - if `y >= maxY` **and** `stable >= 4`: break
  - else advance `y = min(maxY, y + max(360, floor(viewport * 0.7)))`, scroll, sleep (~260ms in production; tests inject no-op sleep)
  - remember `heightBefore = scroller.scrollHeight`
  - `collect()`
  - if new blocks collected **or** `scroller.scrollHeight > heightBefore`: `stable = 0`
  - else: `stable += 1`
- Do **not** add the old extra `stable += 1` when `y >= maxY`.
- Return `{ rendered, blockCount: rendered.length, scrollIterations }`.

Wire the outer converter to:

```javascript
const scroller = resolveFeishuDocScroller(initialRoot, document);
const collected = await collectFeishuDocBlocks({...});
const body = collected.rendered.filter(Boolean).join("\n\n").replace(/\n{3,}/g, "\n\n").trim();
```

Keep Markdown rendering helpers unchanged.

- [ ] **Step 3: Delete unused `materialize_doc_dom`**

Remove `def materialize_doc_dom(...):` from `plugins/feishu/backend/export_feishu.py`. Grep the repo to confirm no remaining references:

```powershell
rg -n "materialize_doc_dom" .
```

Expected: no matches.

- [ ] **Step 4: Run scroll tests**

```powershell
node --test tests_js/feishu_converter_scroll.test.js
```

Expected: both tests PASS.

- [ ] **Step 5: Run Feishu Python regressions**

```powershell
python -m unittest tests.test_feishu_session_and_markdown tests.test_feishu_readiness tests.test_feishu_selection_mismatch -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add plugins/feishu/backend/export_feishu.py tests_js/feishu_converter_scroll.test.js
git commit -m "$(cat <<'EOF'
fix(feishu): harden native-doc scroll collection for long pages

Probe the real nested scroller, wait for lazy scrollHeight growth,
and drop the unused window-only materialize helper (#129).
EOF
)"
```

---

### Task 3: Plugin version bump and issue note

**Files:**
- Modify: `plugins/feishu/plugin.json`
- Optional note only if this branch will be released alone: mention #129 in the PR body (no separate release-notes file required unless cutting a release)

**Interfaces:**
- Consumes: completed Task 2 behavior
- Produces: `feishu` plugin patch bump `1.0.7` → `1.0.8`

- [ ] **Step 1: Bump plugin version**

In `plugins/feishu/plugin.json`:

```json
"version": "1.0.8"
```

- [ ] **Step 2: Confirm provider manifests do not hardcode the old version**

```powershell
rg -n "1\.0\.7" plugins/feishu
```

Update any same-plugin version pins if present; leave unrelated docs alone.

- [ ] **Step 3: Commit**

```bash
git add plugins/feishu/plugin.json
git commit -m "$(cat <<'EOF'
chore(feishu): bump plugin to 1.0.8 for scroll export fix

Ship the native-doc scroll completeness fix for issue #129.
EOF
)"
```

- [ ] **Step 4: Final verification**

```powershell
node --test tests_js/feishu_converter_scroll.test.js
python -m unittest tests.test_feishu_session_and_markdown tests.test_feishu_readiness tests.test_feishu_selection_mismatch -v
```

Expected: all PASS.

---

## Spec coverage checklist

| Spec requirement | Task |
|------------------|------|
| Harden scroller resolution with probe + restore | Task 2 Step 1 |
| Bottom stability needs no new blocks **and** no height growth | Task 2 Step 2 |
| 4 stable rounds; ~120 iteration ceiling; remove bottom double-count | Task 2 Step 2 |
| Keep Markdown rendering unchanged | Task 2 |
| Remove / avoid dual scroll path (`materialize_doc_dom`) | Task 2 Step 3 |
| No new incomplete warning contract | All tasks |
| Synthetic nested-scroller + lazy-growth tests | Task 1 |
| Existing Feishu tests remain green | Task 2 Step 5 / Task 3 Step 4 |
| Plugin patch bump + #129 mention on ship | Task 3 |
