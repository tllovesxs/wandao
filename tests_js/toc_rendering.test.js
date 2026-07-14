const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

const repoRoot = path.resolve(__dirname, '..');
const appSource = fs.readFileSync(path.join(repoRoot, 'wandao_electron', 'renderer', 'app.js'), 'utf8');
const cssSource = fs.readFileSync(path.join(repoRoot, 'wandao_electron', 'renderer', 'styles.css'), 'utf8');
const qualityCheckSource = fs.readFileSync(path.join(repoRoot, 'scripts', 'quality_check.py'), 'utf8');
const renderTocSource = appSource.slice(
  appSource.indexOf('function renderToc(toolId) {'),
  appSource.indexOf('\nfunction selectedTocArgs(toolId) {')
);
const tocItemRule = cssSource.match(/\.toc-item\s*\{[\s\S]*?\n\}/)?.[0] || '';
const guideBaseRule = cssSource.match(/\.toc-item:not\(\.toc-depth-0\)::before,\s*\.toc-item:not\(\.toc-depth-0\)::after\s*\{[\s\S]*?\n\}/)?.[0] || '';
const tocGuideRules = cssSource.slice(cssSource.indexOf('.toc-item:not(.toc-depth-0)::before'));
const tocNodeMapsSource = appSource.slice(
  appSource.indexOf('function tocNodeMaps(nodes) {'),
  appSource.indexOf('\nfunction descendantExportIds(nodes, nodeId) {')
);
const descendantExportIdsSource = appSource.slice(
  appSource.indexOf('function descendantExportIds(nodes, nodeId) {'),
  appSource.indexOf('\nfunction selectableTocIds(nodes) {')
);
const descendantExportIds = vm.runInNewContext(
  `${tocNodeMapsSource}\n${descendantExportIdsSource}\ndescendantExportIds;`,
  { window: { WandaoTocTree: require(path.join(repoRoot, 'wandao_electron', 'renderer', 'toc_tree.js')) } }
);

test('shared TOC renderer emits explicit depth markers and pixel indentation', () => {
  assert.match(renderTocSource, /class="toc-item toc-depth-\$\{depth\}/);
  assert.match(
    renderTocSource,
    /data-node-id="\$\{escapeHtml\(node\.nodeId\)\}"\s+data-depth="\$\{depth\}"/
  );
  assert.match(renderTocSource, /style="--depth:\$\{depth\};--toc-indent:\$\{depth \* 30\}px"/);
});

test('TOC selection includes a document itself and all selectable descendants', () => {
  const nodes = [
    { nodeId: 'folder', exportId: '', selectable: false, parentNodeId: '' },
    { nodeId: 'leaf', exportId: 'leaf-export-id', selectable: true, parentNodeId: 'folder' },
    { nodeId: 'nested-folder', exportId: '', selectable: false, parentNodeId: 'folder' },
    { nodeId: 'nested-leaf', exportId: 'nested-export-id', selectable: true, parentNodeId: 'nested-folder' }
  ];

  assert.deepEqual(Array.from(descendantExportIds(nodes, 'leaf')), ['leaf-export-id']);
  assert.deepEqual(Array.from(descendantExportIds(nodes, 'folder')), ['leaf-export-id', 'nested-export-id']);

  const selectableParentNodes = [
    { nodeId: 'document-with-children', exportId: 'parent-export-id', selectable: true, parentNodeId: '' },
    { nodeId: 'child-document', exportId: 'child-export-id', selectable: true, parentNodeId: 'document-with-children' }
  ];
  assert.deepEqual(
    Array.from(descendantExportIds(selectableParentNodes, 'document-with-children')),
    ['parent-export-id', 'child-export-id']
  );
});

test('TOC clearly disables modules without exportable documents', () => {
  assert.match(renderTocSource, /const hasSelectableItems = ids\.length > 0;/);
  assert.match(renderTocSource, /const selectionAttributes = hasSelectableItems \? '' : ' disabled aria-disabled="true" title=/);
  assert.match(renderTocSource, /<button class="toc-item toc-depth-\$\{depth\}\$\{hasSelectableItems \? '' : ' toc-item-empty'\}"[^>]*\$\{selectionAttributes\}>/);
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
  assert.match(
    tocGuideRules,
    /\.toc-item:not\(\.toc-depth-0\)::before\s*\{[^}]*top:\s*0;[^}]*bottom:\s*0;[^}]*left:\s*calc\(10px\s*\+\s*var\(--toc-indent,\s*0px\)\s*-\s*11px\);[^}]*width:\s*1px;[^}]*background:\s*var\(--border\);/
  );
  assert.match(
    tocGuideRules,
    /\.toc-item:not\(\.toc-depth-0\)::after\s*\{[^}]*top:\s*50%;[^}]*left:\s*calc\(10px\s*\+\s*var\(--toc-indent,\s*0px\)\s*-\s*11px\);[^}]*width:\s*11px;[^}]*border-top:\s*1px\s+solid\s+var\(--border\);/
  );
});

test('quality checks run the shared TOC rendering regression', () => {
  assert.match(qualityCheckSource, /"tests_js\/toc_rendering\.test\.js"/);
});