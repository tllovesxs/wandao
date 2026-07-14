const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const repoRoot = path.resolve(__dirname, '..');
const appSource = fs.readFileSync(path.join(repoRoot, 'wandao_electron', 'renderer', 'app.js'), 'utf8');
const cssSource = fs.readFileSync(path.join(repoRoot, 'wandao_electron', 'renderer', 'styles.css'), 'utf8');
const renderTocSource = appSource.slice(
  appSource.indexOf('function renderToc(toolId) {'),
  appSource.indexOf('\nfunction selectedTocArgs(toolId) {')
);
const tocItemRule = cssSource.match(/\.toc-item\s*\{[\s\S]*?\n\}/)?.[0] || '';
const guideBaseRule = cssSource.match(/\.toc-item:not\(\.toc-depth-0\)::before,\s*\.toc-item:not\(\.toc-depth-0\)::after\s*\{[\s\S]*?\n\}/)?.[0] || '';
const tocGuideRules = cssSource.slice(cssSource.indexOf('.toc-item:not(.toc-depth-0)::before'));

test('shared TOC renderer emits explicit depth markers and pixel indentation', () => {
  assert.match(renderTocSource, /class="toc-item toc-depth-\$\{depth\}"/);
  assert.match(
    renderTocSource,
    /data-node-id="\$\{escapeHtml\(node\.nodeId\)\}"\s+data-depth="\$\{depth\}"/
  );
  assert.match(renderTocSource, /style="--depth:\$\{depth\};--toc-indent:\$\{depth \* 22\}px"/);
});

test('TOC stylesheet applies valid indentation and non-interactive hierarchy guides', () => {
  assert.match(
    tocItemRule,
    /padding:\s*7px\s+10px\s+7px\s+calc\(10px\s*\+\s*var\(--toc-indent,\s*0px\)\)/
  );
  assert.match(tocItemRule, /position:\s*relative/);
  assert.doesNotMatch(tocItemRule, /var\(--depth,\s*0\)\s*\*\s*18px/);
  assert.match(guideBaseRule, /content:\s*""/);
  assert.match(guideBaseRule, /position:\s*absolute/);
  assert.match(guideBaseRule, /pointer-events:\s*none/);
  assert.match(tocGuideRules, /::before\s*\{[^}]*var\(--toc-indent,\s*0px\)/);
});