const test = require('node:test');
const assert = require('node:assert/strict');
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

test('maps Yuque toc DOC nodes to doc_id selection arguments', () => {
  const provider = require('../plugins/yuque/providers/yuque/provider.json');
  const nodes = normalizeStandardTocNodes(provider, { toc: [
    { type: 'TITLE', title: 'Folder', uuid: 'folder', parent_uuid: '', doc_id: '' },
    { type: 'DOC', title: 'Doc', uuid: 'tree-doc', parent_uuid: 'folder', doc_id: 277273010 }
  ] });
  const tree = tocNodeMaps(nodes);

  assert.deepEqual(tree.children.get('').map((node) => node.nodeId), ['yuque:folder']);
  assert.deepEqual(tree.children.get('yuque:folder').map((node) => node.nodeId), ['yuque:tree-doc']);
  assert.equal(nodes[0].selectable, false);
  assert.equal(nodes[1].selectable, true);
  assert.equal(nodes[1].exportId, '277273010');
  assert.deepEqual(selectionArgs(provider, [nodes[1].exportId]), ['--doc-id', '277273010']);
});

test('does not emit a dangling selection argument for empty export IDs', () => {
  const provider = require('../plugins/yuque/providers/yuque/provider.json');

  assert.deepEqual(selectionArgs(provider, ['', null, undefined, '277273010']), ['--doc-id', '277273010']);
});

test('maps Aliyun nodes and parent_id to document selection arguments', () => {
  const provider = require('../plugins/aliyun_thoughts/providers/aliyun/provider.json');
  const nodes = normalizeStandardTocNodes(provider, { nodes: [
    { id: 'folder', title: 'Folder', type: 'folder', parent_id: '' },
    { id: 'doc', title: 'Doc', type: 'document', parent_id: 'folder' }
  ] });
  const tree = tocNodeMaps(nodes);

  assert.deepEqual(tree.children.get('').map((node) => node.nodeId), ['aliyun:folder']);
  assert.deepEqual(tree.children.get('aliyun:folder').map((node) => node.nodeId), ['aliyun:doc']);
  assert.equal(nodes[0].selectable, false);
  assert.equal(nodes[1].selectable, true);
  assert.deepEqual(selectionArgs(provider, [nodes[1].exportId]), ['--doc-id', 'doc']);
});

test('maps Feishu ordered nodes with explicit document selectability', () => {
  const provider = require('../plugins/feishu/providers/feishu-export/provider.json');
  const nodes = normalizeStandardTocNodes(provider, { ordered: [
    { wiki_token: 'folder', title: 'Folder', parent_wiki_token: '', selectable: false },
    { wiki_token: 'non-url', title: 'No URL', parent_wiki_token: 'folder', selectable: false },
    { wiki_token: 'doc', title: 'Doc', parent_wiki_token: 'folder', url: 'https://example.test/wiki/doc', obj_type: 22, selectable: true }
  ] });
  const tree = tocNodeMaps(nodes);

  assert.deepEqual(tree.children.get('').map((node) => node.nodeId), ['feishu-export:folder']);
  assert.deepEqual(tree.children.get('feishu-export:folder').map((node) => node.nodeId), ['feishu-export:non-url', 'feishu-export:doc']);
  assert.deepEqual(nodes.filter((node) => node.selectable).map((node) => node.exportId), ['doc']);
  assert.deepEqual(selectionArgs(provider, ['doc']), ['--doc-id', 'doc']);
});
