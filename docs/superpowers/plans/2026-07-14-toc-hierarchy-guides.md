# Shared TOC Hierarchy Guides Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every scan-enabled platform's directory tree show stable per-level indentation and low-contrast connector guides while preserving existing document selection behavior.

**Architecture:** The Electron renderer owns the shared TOC DOM and styles for all `scanToc` providers. Render explicit pixel values from `renderToc()` into CSS custom properties, then let the common stylesheet consume length-only variables and render decorative pseudo-element guides. A source-level Node regression test locks the renderer/CSS contract because this UI does not currently expose `renderToc()` as an importable module.

**Tech Stack:** CommonJS JavaScript, Electron renderer DOM, CSS custom properties, Node.js built-in `node:test`.

## Global Constraints

- Preserve all current node IDs, export IDs, selection arguments, batch-selection behavior, and TOC normalization.
- Use 22px per non-root tree depth and retain root padding at 10px.
- The connector decorations must use `pointer-events: none` and must not alter row height.
- Change the shared renderer only; do not copy platform-specific UI code into plugins.
- Do not include the temporary `.superpowers/` visual-companion data in a commit.
- The independent Yuque attachment HTTP 403 investigation remains out of scope for this UI change.

---

### Task 1: Lock the shared TOC indentation contract with a failing test

**Files:**
- Create: `tests_js/toc_rendering.test.js`
- Read: `wandao_electron/renderer/app.js:4602-4631`
- Read: `wandao_electron/renderer/styles.css:1422-1472`

**Interfaces:**
- Consumes: `renderToc(toolId)` source that recursively supplies a numeric `depth`.
- Produces: a regression test that requires explicit `data-depth` and `--toc-indent` values in the TOC button markup, and length-only indentation CSS.

- [ ] **Step 1: Write the failing source-contract test**

Create `tests_js/toc_rendering.test.js` using `node:test`, `node:assert/strict`, `fs`, and `path`. Read the two renderer source files and assert:

```js
assert.match(appSource, /data-depth="\$\{depth\}"/);
assert.match(appSource, /--toc-indent:\$\{depth \* 22\}px/);
assert.match(cssSource, /padding:\s*7px 10px 7px calc\(10px \+ var\(--toc-indent, 0px\)\)/);
assert.doesNotMatch(cssSource, /var\(--depth, 0\) \* 18px/);
```

Also assert the stylesheet includes non-root connector selectors and `pointer-events: none`.

- [ ] **Step 2: Run the test to verify it fails**

Run:

```powershell
node --test tests_js/toc_rendering.test.js
```

Expected: failure because `app.js` only emits `style="--depth:${depth}"` and `styles.css` still contains the unsupported multiplication expression.

- [ ] **Step 3: Commit the failing regression test**

```powershell
git add tests_js/toc_rendering.test.js
git commit -m "test: cover TOC hierarchy rendering contract"
```

### Task 2: Emit deterministic indentation metadata from the shared renderer

**Files:**
- Modify: `wandao_electron/renderer/app.js:4612-4626`
- Test: `tests_js/toc_rendering.test.js`

**Interfaces:**
- Consumes: recursive `renderNode(node, depth)` with integer depth beginning at 0.
- Produces: each `.toc-item` has `data-depth`, `toc-depth-N`, `--depth`, and `--toc-indent` where `--toc-indent = depth * 22px`.

- [ ] **Step 1: Run the existing failing test again**

Run:

```powershell
node --test tests_js/toc_rendering.test.js
```

Expected: same contract failure before production code is changed.

- [ ] **Step 2: Make the minimal renderer change**

In the TOC button template, emit exactly:

```js
class="toc-item toc-depth-${depth}"
data-node-id="..."
data-depth="${depth}"
style="--depth:${depth};--toc-indent:${depth * 22}px"
```

Keep existing child recursion, selected-count calculation, classes, title escaping, and event delegation unchanged.

- [ ] **Step 3: Run the targeted test**

Run:

```powershell
node --test tests_js/toc_rendering.test.js
```

Expected: it still fails only on the CSS assertions, proving the renderer half is now present.

- [ ] **Step 4: Commit the renderer change**

```powershell
git add wandao_electron/renderer/app.js
git commit -m "fix: emit explicit TOC indentation values"
```

### Task 3: Replace invalid CSS arithmetic and add non-interactive hierarchy guides

**Files:**
- Modify: `wandao_electron/renderer/styles.css:1422-1472`
- Test: `tests_js/toc_rendering.test.js`

**Interfaces:**
- Consumes: `--toc-indent` as a CSS length and root marker class `toc-depth-0`.
- Produces: visibly indented rows at 0px, 22px, 44px, etc.; guide decorations only for non-root rows.

- [ ] **Step 1: Confirm the CSS contract is still failing**

Run:

```powershell
node --test tests_js/toc_rendering.test.js
```

Expected: failure because CSS still has `var(--depth, 0) * 18px` and lacks connector rules.

- [ ] **Step 2: Implement the stylesheet change**

Replace the current left padding with:

```css
padding: 7px 10px 7px calc(10px + var(--toc-indent, 0px));
position: relative;
```

Add connector rules adjacent to `.toc-item`:

```css
.toc-item:not(.toc-depth-0)::before,
.toc-item:not(.toc-depth-0)::after {
  content: "";
  position: absolute;
  pointer-events: none;
}
```

Use `--toc-indent` to position a subdued vertical guide left of the node checkbox and a short horizontal connection from that guide to the checkbox. The rules must be contained inside the button boundary and use existing border/surface color tokens.

- [ ] **Step 3: Run the targeted test to verify green**

Run:

```powershell
node --test tests_js/toc_rendering.test.js
```

Expected: PASS.

- [ ] **Step 4: Commit the stylesheet change**

```powershell
git add wandao_electron/renderer/styles.css tests_js/toc_rendering.test.js
git commit -m "fix: show TOC hierarchy guides"
```

### Task 4: Verify shared-platform coverage and start the updated source application

**Files:**
- Read: `wandao_electron/renderer/app.js:2823,3021-3028,4602-4631`
- Read: `plugins/*/providers/*/provider.json`
- Test: `tests_js/toc_rendering.test.js`
- Test: `tests_js/toc_tree.test.js`

**Interfaces:**
- Consumes: the shared `renderToc()` path used after every `scanToc` action.
- Produces: verification evidence that scan-enabled providers use the fixed renderer and a new Electron source session for manual testing.

- [ ] **Step 1: Run focused JavaScript regression tests**

Run:

```powershell
node --test tests_js/toc_rendering.test.js tests_js/toc_tree.test.js
```

Expected: all subtests pass.

- [ ] **Step 2: Run syntax and repository checks**

Run:

```powershell
npm run check
python scripts/quality_check.py
```

Expected: exit code 0 for both commands.

- [ ] **Step 3: Verify the shared renderer is used by every scan-enabled provider**

Run:

```powershell
rg -n -S 'scanToc": true' plugins --glob provider.json
rg -n -S 'renderToc\(provider\.id\)' wandao_electron/renderer/app.js
```

Expected: all scan-enabled providers are identified by manifests and their scan completion is routed to the same `renderToc(provider.id)` call.

- [ ] **Step 4: Restart the Electron source application**

Stop any old `wandao_electron` development process, then start:

```powershell
npm run dev
```

from `wandao_electron`, redirecting logs to `.codex-runtime-logs` and retaining the dev data directory. Record the resulting PID and log file.

- [ ] **Step 5: Perform manual acceptance checks**

In the new source app, read the Yuque directory and one additional scan-enabled platform directory. Confirm:

1. root, child, and grandchild nodes have visibly increasing left offsets;
2. only non-root nodes show subtle connectors;
3. full-row click, parent batch-select, full-select, clear-select, invert-select, and exported document IDs behave as before.

- [ ] **Step 6: Commit any final verification-only source adjustment if needed**

If manual checks reveal no source changes, do not create an empty commit. Otherwise, add a regression test first, run it red, apply the smallest correction, rerun all Task 4 checks, and commit with a focused message.
