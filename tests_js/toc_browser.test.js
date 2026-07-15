const assert = require('node:assert/strict');
const test = require('node:test');
const {
  expandableNodeIds,
  visibleTocRows
} = require('../wandao_electron/renderer/toc_browser');
const { normalizeProviderTocNodes } = require('../wandao_electron/renderer/toc_tree');

const tree = [
  { nodeId: 'folder', title: '产品文档', parentNodeId: '' },
  { nodeId: 'guide', title: '安装指南', parentNodeId: 'folder' },
  { nodeId: 'api', title: 'API 参考', parentNodeId: 'folder' },
  { nodeId: 'other', title: '其他资料', parentNodeId: '' }
];

test('collapsed folders avoid rendering descendants until explicitly expanded', () => {
  const result = visibleTocRows(tree, { expanded: new Set(), limit: 20 });

  assert.deepEqual(result.rows.map((row) => row.node.nodeId), ['folder', 'other']);
  assert.equal(result.rows[0].hasChildren, true);
  assert.equal(result.rows[0].expanded, false);
  assert.deepEqual(expandableNodeIds(tree), ['folder']);
});

test('TOC search keeps matching nodes and their ancestor path visible', () => {
  const result = visibleTocRows(tree, { query: '安装', expanded: new Set(), limit: 20 });

  assert.equal(result.matchCount, 1);
  assert.deepEqual(result.rows.map((row) => row.node.nodeId), ['folder', 'guide']);
  assert.equal(result.rows[0].expanded, true);
  assert.equal(result.rows[1].matchesQuery, true);
});

test('large expanded directories are rendered in bounded batches', () => {
  const largeTree = [
    { nodeId: 'folder', title: '根目录', parentNodeId: '' },
    ...Array.from({ length: 1000 }, (_, index) => ({
      nodeId: `doc-${index}`,
      title: `文档 ${index}`,
      parentNodeId: 'folder'
    }))
  ];
  const result = visibleTocRows(largeTree, { expanded: new Set(['folder']), limit: 120 });

  assert.equal(result.rows.length, 120);
  assert.equal(result.hasMore, true);
  assert.ok(result.visitedCount <= 121);
  assert.equal(result.rows[0].node.nodeId, 'folder');
});

test('deep Yuque hierarchies preserve every ancestor, depth, and searchable document path', () => {
  const provider = require('../plugins/yuque/providers/yuque/provider.json');
  const nodes = normalizeProviderTocNodes(provider, { toc: [
    { type: 'TITLE', title: '知识库', uuid: 'level-0', parent_uuid: '', doc_id: '' },
    { type: 'TITLE', title: '产品', uuid: 'level-1', parent_uuid: 'level-0', doc_id: '' },
    { type: 'TITLE', title: '客户端', uuid: 'level-2', parent_uuid: 'level-1', doc_id: '' },
    { type: 'TITLE', title: 'Windows', uuid: 'level-3', parent_uuid: 'level-2', doc_id: '' },
    { type: 'TITLE', title: '疑难解答', uuid: 'level-4', parent_uuid: 'level-3', doc_id: '' },
    { type: 'DOC', title: '深层登录指南', uuid: 'deep-doc', parent_uuid: 'level-4', doc_id: 9527 }
  ] });
  const expanded = new Set(expandableNodeIds(nodes));
  const full = visibleTocRows(nodes, { expanded, limit: 20 });

  assert.deepEqual(full.rows.map((row) => row.depth), [0, 1, 2, 3, 4, 5]);
  assert.deepEqual(full.rows.map((row) => row.node.nodeId), [
    'yuque:level-0',
    'yuque:level-1',
    'yuque:level-2',
    'yuque:level-3',
    'yuque:level-4',
    'yuque:deep-doc'
  ]);
  assert.equal(full.rows.at(-1).node.exportId, '9527');

  const search = visibleTocRows(nodes, { query: '深层 登录', expanded: new Set(), limit: 20 });
  assert.deepEqual(search.rows.map((row) => row.depth), [0, 1, 2, 3, 4, 5]);
  assert.equal(search.matchCount, 1);
  assert.equal(search.rows.at(-1).matchesQuery, true);
});
