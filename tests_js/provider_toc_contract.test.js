const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {
  normalizeProviderTocNodes,
  selectionArgs,
  valueAtPath
} = require('../wandao_electron/renderer/toc_tree.js');

const REPO_ROOT = path.resolve(__dirname, '..');

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

function pluginProviderEntries() {
  const pluginsRoot = path.join(REPO_ROOT, 'plugins');
  const entries = [];
  for (const pluginName of fs.readdirSync(pluginsRoot)) {
    const providersRoot = path.join(pluginsRoot, pluginName, 'providers');
    if (!fs.existsSync(providersRoot)) continue;
    for (const providerName of fs.readdirSync(providersRoot)) {
      const manifestPath = path.join(providersRoot, providerName, 'provider.json');
      if (!fs.existsSync(manifestPath)) continue;
      entries.push({ manifestPath, provider: readJson(manifestPath) });
    }
  }
  return entries;
}

function templateProviderEntries() {
  const providersRoot = path.join(REPO_ROOT, 'providers');
  return fs.readdirSync(providersRoot)
    .map((providerName) => path.join(providersRoot, providerName, 'provider.json'))
    .filter((manifestPath) => fs.existsSync(manifestPath))
    .map((manifestPath) => ({ manifestPath, provider: readJson(manifestPath) }));
}

function setAtPath(target, pathExpression, value) {
  const parts = String(pathExpression).split('.').filter(Boolean);
  let current = target;
  parts.forEach((part, index) => {
    if (index === parts.length - 1) current[part] = value;
    else current = current[part] ||= {};
  });
}

const fixtures = {
  aliyun: {
    payload: { nodes: [
      { id: 'folder', title: 'Folder', type: 'folder', parent_id: '' },
      { id: 'doc', title: 'Doc', type: 'document', parent_id: 'folder' }
    ] },
    rootId: 'aliyun:folder', docId: 'aliyun:doc', parentId: 'aliyun:folder', exportId: 'doc',
    args: ['--doc-id', 'doc']
  },
  'feishu-export': {
    payload: { ordered: [
      { wiki_token: 'folder', title: 'Folder', parent_wiki_token: '', selectable: false },
      { wiki_token: 'doc', title: 'Doc', parent_wiki_token: 'folder', selectable: true }
    ] },
    rootId: 'feishu-export:folder', docId: 'feishu-export:doc', parentId: 'feishu-export:folder', exportId: 'doc',
    args: ['--doc-id', 'doc']
  },
  'ima-export': {
    payload: { nodes: [
      { nodeId: 'ima-kb:demo', exportId: '', title: 'Knowledge base', parentNodeId: '', selectable: false },
      { nodeId: 'ima-media:demo:doc', exportId: 'kb::doc', title: 'Doc', parentNodeId: 'ima-kb:demo', selectable: true }
    ] },
    rootId: 'ima-kb:demo', docId: 'ima-media:demo:doc', parentId: 'ima-kb:demo', exportId: 'kb::doc',
    args: ['--doc-id', 'kb::doc']
  },
  'obsidian-export': {
    payload: { nodes: [
      { nodeId: 'folder:notes', exportId: '', title: 'Notes', parentNodeId: '', selectable: false },
      { nodeId: 'doc:notes/getting-started.md', exportId: 'notes/getting-started.md', title: 'Getting Started', parentNodeId: 'folder:notes', selectable: true }
    ] },
    rootId: 'folder:notes', docId: 'doc:notes/getting-started.md', parentId: 'folder:notes', exportId: 'notes/getting-started.md',
    args: ['--doc-id', 'notes/getting-started.md']
  },
  onenote: {
    payload: { nodes: [
      { nodeId: 'onenote-notebook:demo', exportId: '', title: 'Notebook', parentNodeId: '', selectable: false },
      { nodeId: 'onenote-page:doc', exportId: 'page-id', title: 'Page', parentNodeId: 'onenote-notebook:demo', selectable: true }
    ] },
    rootId: 'onenote-notebook:demo', docId: 'onenote-page:doc', parentId: 'onenote-notebook:demo', exportId: 'page-id',
    args: ['--doc-id', 'page-id']
  },
  wiz: {
    payload: { nodes: [
      { nodeId: 'wiz-kb:demo', exportId: '', title: 'Knowledge base', parentNodeId: '', selectable: false },
      { nodeId: 'wiz-doc:doc', exportId: 'doc-guid', title: 'Note', parentNodeId: 'wiz-kb:demo', selectable: true }
    ] },
    rootId: 'wiz-kb:demo', docId: 'wiz-doc:doc', parentId: 'wiz-kb:demo', exportId: 'doc-guid',
    args: ['--doc-id', 'doc-guid']
  },
  yinxiang: {
    payload: { notebooks: [{
      guid: 'notebook-guid', name: 'Notebook', stack: 'Stack',
      notes: [{ guid: 'note-guid', title: 'Note' }]
    }] },
    rootId: 'yinxiang-stack:Stack', docId: 'yinxiang-note:note-guid', parentId: 'yinxiang-notebook:notebook-guid', exportId: 'note-guid',
    args: ['--doc-id', 'note-guid']
  },
  youdao: {
    payload: { nodes: [
      { nodeId: 'youdao-folder:root', exportId: '', title: 'Folder', parentNodeId: '', selectable: false, type: 'folder' },
      { nodeId: 'youdao-doc:doc', exportId: 'doc-id', title: 'Note', parentNodeId: 'youdao-folder:root', selectable: true, type: 'document' }
    ] },
    rootId: 'youdao-folder:root', docId: 'youdao-doc:doc', parentId: 'youdao-folder:root', exportId: 'doc-id',
    args: ['--doc-id', 'doc-id']
  },
  yuque: {
    payload: { toc: [
      { type: 'TITLE', title: 'Folder', uuid: 'folder', parent_uuid: '', doc_id: '' },
      { type: 'DOC', title: 'Doc', uuid: 'tree-doc', parent_uuid: 'folder', doc_id: 277273010 }
    ] },
    rootId: 'yuque:folder', docId: 'yuque:tree-doc', parentId: 'yuque:folder', exportId: '277273010',
    args: ['--doc-id', '277273010']
  },
  'zsxq-column': {
    payload: { groups: [{
      groupIndex: 3, groupTitle: 'Section',
      topics: [{ key: 'toc:3:0', title: 'Article' }]
    }] },
    rootId: 'zsxq-column-group:3', docId: 'zsxq-column:toc:3:0', parentId: 'zsxq-column-group:3', exportId: 'toc:3:0',
    args: ['--toc-mode', 'toc', '--toc-key', 'toc:3:0']
  }
};

test('every official scan provider has an executable TOC fixture and selection contract', () => {
  const scanEntries = pluginProviderEntries().filter(({ provider }) => provider.capabilities?.scanToc === true);
  assert.deepEqual(
    scanEntries.map(({ provider }) => provider.id).sort(),
    Object.keys(fixtures).sort(),
    'a scan-capable provider was added or removed without updating the TOC contract matrix'
  );

  scanEntries.forEach(({ provider, manifestPath }) => {
    const fixture = fixtures[provider.id];
    const scanAction = provider.actions?.find((action) => action.kind === 'scan' || action.id === 'scan');
    assert.ok(scanAction, `${manifestPath}: missing scan action`);
    assert.ok(scanAction.args?.includes('--scan-toc'), `${manifestPath}: scan action must invoke --scan-toc`);
    assert.ok(Array.isArray(valueAtPath(fixture.payload, provider.toc.itemsPath)), `${manifestPath}: itemsPath misses backend payload`);

    const nodes = normalizeProviderTocNodes(provider, fixture.payload);
    assert.equal(new Set(nodes.map((node) => node.nodeId)).size, nodes.length, `${provider.id}: duplicate normalized node IDs`);
    assert.ok(nodes.some((node) => node.nodeId === fixture.rootId), `${provider.id}: root was not normalized`);
    const doc = nodes.find((node) => node.nodeId === fixture.docId);
    assert.ok(doc, `${provider.id}: document was not normalized`);
    assert.equal(doc.parentNodeId, fixture.parentId, `${provider.id}: parent mapping drifted`);
    assert.equal(doc.exportId, fixture.exportId, `${provider.id}: export ID mapping drifted`);
    assert.equal(doc.selectable, true, `${provider.id}: exportable document became unselectable`);
    assert.deepEqual(selectionArgs(provider, [doc.exportId]), fixture.args, `${provider.id}: selection CLI contract drifted`);
  });
});

test('all bundled provider templates exercise the standard TOC renderer contract', () => {
  const templates = templateProviderEntries().filter(({ provider }) => provider.capabilities?.scanToc === true);
  assert.ok(templates.length > 0, 'no scan-capable provider templates were discovered');

  templates.forEach(({ provider, manifestPath }) => {
    assert.equal(provider.toc.adapter, undefined, `${manifestPath}: templates should demonstrate the standard TOC contract`);
    const root = {};
    root[provider.toc.idKey] = `${provider.id}:root`;
    root[provider.toc.exportIdKey] = '';
    root[provider.toc.titleKey] = 'Root';
    root[provider.toc.parentIdKey] = '';
    root[provider.toc.selectableKey] = false;
    const doc = {};
    doc[provider.toc.idKey] = `${provider.id}:doc`;
    doc[provider.toc.exportIdKey] = 'doc-id';
    doc[provider.toc.titleKey] = 'Doc';
    doc[provider.toc.parentIdKey] = `${provider.id}:root`;
    doc[provider.toc.selectableKey] = true;
    const payload = {};
    setAtPath(payload, provider.toc.itemsPath, [root, doc]);

    const nodes = normalizeProviderTocNodes(provider, payload);
    assert.equal(nodes.length, 2, `${manifestPath}: template payload did not normalize`);
    assert.equal(nodes[1].parentNodeId, `${provider.id}:root`);
    assert.equal(nodes[1].selectable, true);
    assert.deepEqual(selectionArgs(provider, [nodes[1].exportId]), [provider.toc.selectionArg, 'doc-id']);
  });
});
