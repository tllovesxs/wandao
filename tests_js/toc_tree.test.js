const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { normalizeStandardTocNodes, tocNodeMaps, selectionArgs } = require('../wandao_electron/renderer/toc_tree.js');

test('restores Aliyun parent_id hierarchy when a legacy manifest still points at ordered', () => {
  const provider = { id: 'aliyun', toc: { itemsPath: 'ordered', idKey: 'id', exportIdKey: 'id', titleKey: 'title', parentIdKey: 'parentId' } };
  const nodes = normalizeStandardTocNodes(provider, { nodes: [
    { id: 'folder', title: '资料', type: 'folder', parent_id: '' },
    { id: 'doc', title: '说明', type: 'document', parent_id: 'folder' }
  ] });
  const tree = tocNodeMaps(nodes);
  assert.deepEqual(tree.children.get('').map((node) => node.nodeId), ['aliyun:folder']);
  assert.deepEqual(tree.children.get('aliyun:folder').map((node) => node.nodeId), ['aliyun:doc']);
});

test('uses canonical Plugin v1 node fields when the manifest predates the normalized schema', () => {
  const provider = { id: 'ima-export', toc: { itemsPath: 'ordered', idKey: 'id', exportIdKey: 'id', titleKey: 'name', parentIdKey: 'parentId' } };
  const nodes = normalizeStandardTocNodes(provider, { nodes: [
    { nodeId: 'ima-kb:demo', exportId: '', title: '知识库', parentNodeId: '', selectable: false },
    { nodeId: 'ima-folder:demo:folder', exportId: '', title: '目录', parentNodeId: 'ima-kb:demo', selectable: false },
    { nodeId: 'ima-media:demo:doc', exportId: 'doc', title: '文档', parentNodeId: 'ima-folder:demo:folder', selectable: true }
  ] });
  const tree = tocNodeMaps(nodes);
  assert.deepEqual(tree.children.get('').map((node) => node.nodeId), ['ima-kb:demo']);
  assert.deepEqual(tree.children.get('ima-kb:demo').map((node) => node.nodeId), ['ima-folder:demo:folder']);
  assert.deepEqual(tree.children.get('ima-folder:demo:folder').map((node) => node.nodeId), ['ima-media:demo:doc']);
  assert.equal(nodes[2].selectable, true);
});

test('keeps provider-specific parent fields for Feishu and Yuque', () => {
  const feishu = normalizeStandardTocNodes({ id: 'feishu-export', toc: { itemsPath: 'ordered', idKey: 'wiki_token', exportIdKey: 'wiki_token', parentIdKey: 'parent_wiki_token' } }, { ordered: [
    { wiki_token: 'root', title: '根目录' }, { wiki_token: 'child', title: '子页', parent_wiki_token: 'root' }
  ] });
  const yuque = normalizeStandardTocNodes({ id: 'yuque', toc: { itemsPath: 'ordered', idKey: 'uuid', exportIdKey: 'uuid', parentIdKey: 'parent_uuid' } }, { ordered: [
    { uuid: 'folder', title: '目录' }, { uuid: 'doc', title: '文章', parent_uuid: 'folder' }
  ] });
  assert.equal(tocNodeMaps(feishu).children.get('feishu-export:root')[0].nodeId, 'feishu-export:child');
  assert.equal(tocNodeMaps(yuque).children.get('yuque:folder')[0].nodeId, 'yuque:doc');
});

const OFFICIAL_TOC_CONTRACTS = [
  {
    id: 'aliyun', path: 'plugins/aliyun_thoughts/providers/aliyun/provider.json',
    data: { nodes: [{ id: 'folder', title: 'Folder', parent_id: '', type: 'folder' }, { id: 'doc', title: 'Document', parent_id: 'folder', type: 'document' }] },
    parent: 'aliyun:folder', exportId: 'doc', selection: ['--doc-id', 'doc']
  },
  {
    id: 'feishu-export', path: 'plugins/feishu/providers/feishu-export/provider.json',
    data: { ordered: [{ wiki_token: 'folder', title: 'Folder', parent_wiki_token: '', selectable: false }, { wiki_token: 'doc', title: 'Document', parent_wiki_token: 'folder', selectable: true }] },
    parent: 'feishu-export:folder', exportId: 'doc', selection: ['--doc-id', 'doc']
  },
  {
    id: 'ima-export', path: 'plugins/ima/providers/ima-export/provider.json',
    data: { nodes: [{ nodeId: 'ima:folder', exportId: '', title: 'Folder', parentNodeId: '', selectable: false }, { nodeId: 'ima:doc', exportId: 'doc', title: 'Document', parentNodeId: 'ima:folder', selectable: true }] },
    parent: 'ima:folder', exportId: 'doc', selection: ['--doc-id', 'doc']
  },
  {
    id: 'onenote', path: 'plugins/onenote/providers/onenote/provider.json',
    data: { nodes: [{ nodeId: 'one:section', exportId: '', title: 'Section', parentNodeId: '', selectable: false }, { nodeId: 'one:page', exportId: 'page-id', title: 'Page', parentNodeId: 'one:section', selectable: true }] },
    parent: 'one:section', exportId: 'page-id', selection: ['--doc-id', 'page-id']
  },
  {
    id: 'wiz', path: 'plugins/wiz/providers/wiz/provider.json',
    data: { nodes: [{ nodeId: 'wiz:folder', exportId: '', title: 'Folder', parentNodeId: '', selectable: false }, { nodeId: 'wiz:doc', exportId: 'doc-id', title: 'Note', parentNodeId: 'wiz:folder', selectable: true }] },
    parent: 'wiz:folder', exportId: 'doc-id', selection: ['--doc-id', 'doc-id']
  },
  {
    id: 'yinxiang', path: 'plugins/yinxiang/providers/yinxiang/provider.json',
    data: { notebooks: [{ guid: 'notebook', name: 'Notebook', stack: 'Stack', notes: [{ guid: 'note', title: 'Note' }] }] },
    parent: 'yinxiang:notebook:notebook', exportId: 'note', selection: ['--doc-id', 'note']
  },
  {
    id: 'youdao', path: 'plugins/youdao/providers/youdao/provider.json',
    data: { nodes: [{ nodeId: 'youdao:folder', exportId: '', title: 'Folder', parentNodeId: '', selectable: false }, { nodeId: 'youdao:doc', exportId: 'doc-id', title: 'Note', parentNodeId: 'youdao:folder', selectable: true }] },
    parent: 'youdao:folder', exportId: 'doc-id', selection: ['--doc-id', 'doc-id']
  },
  {
    id: 'yuque', path: 'plugins/yuque/providers/yuque/provider.json',
    data: { toc: [{ uuid: 'title-uuid', doc_id: '', title: 'Folder', parent_uuid: '', type: 'TITLE' }, { uuid: 'node-uuid', doc_id: 'document-id', title: 'Document', parent_uuid: 'title-uuid', type: 'DOC' }] },
    parent: 'yuque:title-uuid', exportId: 'document-id', selection: ['--doc-id', 'document-id']
  },
  {
    id: 'zsxq-column', path: 'plugins/zsxq/providers/zsxq-column/provider.json',
    data: { groups: [{ groupIndex: 0, groupTitle: 'Group', topics: [{ key: 'toc:0:0', title: 'Article' }] }] },
    parent: 'zsxq-column:group:0', exportId: 'toc:0:0', selection: ['--toc-mode', 'toc', '--toc-key', 'toc:0:0']
  }
];

test('official Plugin v1 providers preserve their scan, tree, selection, and CLI contracts', () => {
  for (const contract of OFFICIAL_TOC_CONTRACTS) {
    const provider = JSON.parse(fs.readFileSync(path.join(process.cwd(), contract.path), 'utf8'));
    const nodes = normalizeStandardTocNodes(provider, contract.data);
    const docs = nodes.filter((node) => node.selectable);
    const tree = tocNodeMaps(nodes);
    assert.equal(docs.length, 1, `${contract.id} selects only documents`);
    assert.equal(docs[0].exportId, contract.exportId, `${contract.id} export id`);
    assert.equal(docs[0].parentNodeId, contract.parent, `${contract.id} parent id`);
    assert.equal(tree.children.get(contract.parent)[0].exportId, contract.exportId, `${contract.id} tree child`);
    assert.deepEqual(selectionArgs(provider, [contract.exportId]), contract.selection, `${contract.id} CLI selection`);
  }
});

test('zsxq group intentionally has no directory-selection contract', () => {
  const provider = JSON.parse(fs.readFileSync('plugins/zsxq/providers/zsxq-group/provider.json', 'utf8'));
  assert.equal(provider.capabilities.scanToc, false);
  assert.equal(provider.toc, undefined);
});

test('yuque scan keeps TITLE nodes in the tree but selects only DOC doc_id values', () => {
  const provider = JSON.parse(fs.readFileSync('plugins/yuque/providers/yuque/provider.json', 'utf8'));
  const data = { toc: [
    { type: 'DOC', title: 'Root document', uuid: 'tree-1', doc_id: 1001, parent_uuid: '' },
    { type: 'DOC', title: 'Second document', uuid: 'tree-2', doc_id: 1002, parent_uuid: '' },
    { type: 'DOC', title: 'Third document', uuid: 'tree-3', doc_id: 1003, parent_uuid: '' },
    { type: 'DOC', title: 'Parent document', uuid: 'tree-4', doc_id: 1004, parent_uuid: '' },
    { type: 'TITLE', title: 'Group', uuid: 'group-tree-id', doc_id: '', parent_uuid: 'tree-4' },
    { type: 'DOC', title: 'Nested document', uuid: 'tree-5', doc_id: 1005, parent_uuid: 'group-tree-id' },
    { type: 'DOC', title: 'Sibling document', uuid: 'tree-6', doc_id: 1006, parent_uuid: 'tree-4' }
  ] };

  const nodes = normalizeStandardTocNodes(provider, data);
  const selected = nodes.filter((node) => node.selectable).map((node) => node.exportId);

  assert.equal(nodes.length, 7);
  assert.deepEqual(selected, ['1001', '1002', '1003', '1004', '1005', '1006']);
  assert.equal(tocNodeMaps(nodes).children.get('yuque:group-tree-id')[0].exportId, '1005');
  assert.deepEqual(selectionArgs(provider, selected), [
    '--doc-id', '1001', '--doc-id', '1002', '--doc-id', '1003',
    '--doc-id', '1004', '--doc-id', '1005', '--doc-id', '1006'
  ]);
});
