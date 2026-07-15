const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const recent = require('../wandao_electron/renderer/recent_inputs');

function memoryStorage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return {
    getItem(key) { return values.has(key) ? values.get(key) : null; },
    setItem(key, value) { values.set(key, String(value)); },
    removeItem(key) { values.delete(key); },
    raw(key) { return values.get(key); }
  };
}

function field(options = {}) {
  const attributes = {
    'data-history-kind': options.kind || 'url',
    'data-history-key': options.key || 'entry_url',
    ...(options.attributes || {})
  };
  return {
    id: options.id || 'provider-url',
    name: options.name || '',
    type: options.type || (options.kind === 'url' ? 'url' : 'text'),
    tagName: 'INPUT',
    value: options.value || '',
    disabled: Boolean(options.disabled),
    readOnly: Boolean(options.readOnly),
    placeholder: options.placeholder || '',
    autocomplete: options.autocomplete || '',
    getAttribute(name) { return Object.prototype.hasOwnProperty.call(attributes, name) ? attributes[name] : null; }
  };
}

function fakeElement(tagName, ownerDocument) {
  const attributes = {};
  const listeners = new Map();
  return {
    tagName: String(tagName || 'div').toUpperCase(),
    ownerDocument,
    children: [],
    className: '',
    hidden: false,
    textContent: '',
    setAttribute(name, value) { attributes[name] = String(value); },
    getAttribute(name) { return Object.prototype.hasOwnProperty.call(attributes, name) ? attributes[name] : null; },
    appendChild(child) { this.children.push(child); child.parentElement = this; return child; },
    prepend(child) { this.children.unshift(child); child.parentElement = this; return child; },
    replaceChildren(...children) {
      this.children = children;
      children.forEach((child) => { child.parentElement = this; });
    },
    addEventListener(type, listener) {
      const handlers = listeners.get(type) || [];
      handlers.push(listener);
      listeners.set(type, handlers);
    },
    dispatchEvent(event) {
      for (const listener of listeners.get(event?.type) || []) listener.call(this, event);
      return true;
    },
    focus() { this.focused = true; },
    closest() { return null; }
  };
}

function interactiveHistoryField(options = {}) {
  const documentRef = {
    createElement(tagName) { return fakeElement(tagName, documentRef); }
  };
  const container = fakeElement('div', documentRef);
  container.className = 'form-group';
  const input = fakeElement('input', documentRef);
  input.id = options.id || 'zsxq-group-url';
  input.name = options.name || '';
  input.type = options.type || 'url';
  input.value = options.value || '';
  input.disabled = false;
  input.readOnly = false;
  input.placeholder = '';
  input.autocomplete = '';
  input.setAttribute('data-history-kind', options.kind || 'url');
  input.setAttribute('data-history-key', options.key || 'entry_url');
  input.setAttribute('data-history-label', options.label || '知识星球 Group URL');
  input.parentElement = container;
  input.closest = (selector) => selector === '.form-group' ? container : null;
  return { container, input };
}

test('keeps the latest three distinct values and moves a duplicate to the front', () => {
  const storage = memoryStorage();
  const url = field();
  for (const [index, value] of [
    'https://example.com/one',
    'https://example.com/two',
    'https://example.com/three',
    'https://example.com/four'
  ].entries()) {
    recent.recordValue(storage, 'plugin:demo:provider', url, value, 1000 + index);
  }
  assert.deepEqual(recent.listValues(storage, 'plugin:demo:provider', url, 2000), [
    'https://example.com/four',
    'https://example.com/three',
    'https://example.com/two'
  ]);

  recent.recordValue(storage, 'plugin:demo:provider', url, 'https://example.com/two', 3000);
  assert.deepEqual(recent.listValues(storage, 'plugin:demo:provider', url, 4000), [
    'https://example.com/two',
    'https://example.com/four',
    'https://example.com/three'
  ]);
});

test('isolates values by plugin/provider scope and field key', () => {
  const storage = memoryStorage();
  const entry = field({ key: 'entry_url' });
  const output = field({ id: 'provider-output', key: 'output', kind: 'path', type: 'text' });
  recent.recordValue(storage, 'plugin:official:provider', entry, 'https://example.com/official', 100);
  recent.recordValue(storage, 'plugin:community:provider', entry, 'https://example.com/community', 200);
  recent.recordValue(storage, 'plugin:official:provider', output, 'D:/exports/project', 300);

  assert.deepEqual(recent.listValues(storage, 'plugin:official:provider', entry, 400), ['https://example.com/official']);
  assert.deepEqual(recent.listValues(storage, 'plugin:community:provider', entry, 400), ['https://example.com/community']);
  assert.deepEqual(recent.listValues(storage, 'plugin:official:provider', output, 400), ['D:/exports/project']);
});

test('fails closed for credential fields and provider-owned identifiers', () => {
  const storage = memoryStorage();
  const unsafeFields = [
    field({ id: 'feishu-app-id', kind: 'text', key: 'app_id', type: 'text' }),
    field({ id: 'ima-api-key', kind: 'text', key: 'api_key', type: 'text' }),
    field({ id: 'target-space-id', kind: 'text', key: 'space_id', type: 'text' }),
    field({ id: 'folder', kind: 'text', key: 'folder_id', type: 'text' }),
    field({ id: 'account', kind: 'text', key: 'username', type: 'text' }),
    field({ id: 'secret', kind: 'text', key: 'title', type: 'password' }),
    field({ id: 'provider-url', attributes: { 'data-draft': 'false' } })
  ];
  for (const unsafe of unsafeFields) {
    assert.equal(recent.recordValue(storage, 'scope', unsafe, 'never-store', 100).saved, false);
  }
  assert.equal(storage.raw(recent.STORAGE_KEY), undefined);
});

test('rejects credential-bearing, signed, encoded, and userinfo URLs', () => {
  const storage = memoryStorage();
  const url = field();
  const unsafeUrls = [
    'https://user:pass@example.com/docs',
    'https://example.com/docs?token=secret',
    'https://example.com/docs?X-Amz-Signature=secret',
    'https://example.com/docs#access_token=secret',
    'https://example.com/callback?redirect=https%3A%2F%2Fother.test%2F%3Fcode%3Dsecret'
  ];
  for (const value of unsafeUrls) {
    assert.equal(recent.recordValue(storage, 'scope', url, value, 100).saved, false, value);
  }
  assert.equal(storage.raw(recent.STORAGE_KEY), undefined);

  assert.equal(
    recent.recordValue(storage, 'scope', url, 'https://tenant.feishu.cn/wiki/resource-token', 200).saved,
    true
  );
});

test('does not retain generated defaults and expires old values', () => {
  const storage = memoryStorage();
  const output = field({
    id: 'provider-output',
    key: 'output',
    kind: 'path',
    type: 'text',
    attributes: { 'data-history-default-value': 'D:/app/default-export' }
  });
  assert.equal(recent.recordValue(storage, 'scope', output, 'D:/app/default-export', 100).saved, false);
  assert.equal(recent.recordValue(storage, 'scope', output, 'D:/exports/custom', 200).saved, true);
  assert.deepEqual(recent.listValues(storage, 'scope', output, 200 + recent.TTL_MS - 1), ['D:/exports/custom']);
  assert.deepEqual(recent.listValues(storage, 'scope', output, 200 + recent.TTL_MS + 1), []);
});

test('supports per-value, per-field, and global clearing', () => {
  const storage = memoryStorage();
  const url = field();
  recent.recordValue(storage, 'scope', url, 'https://example.com/one', 100);
  recent.recordValue(storage, 'scope', url, 'https://example.com/two', 200);
  recent.removeValue(storage, 'scope', url, 'https://example.com/two', 300);
  assert.deepEqual(recent.listValues(storage, 'scope', url, 400), ['https://example.com/one']);
  assert.equal(recent.clearField(storage, 'scope', url), true);
  assert.deepEqual(recent.listValues(storage, 'scope', url, 500), []);

  recent.recordValue(storage, 'scope', url, 'https://example.com/three', 600);
  assert.equal(recent.clearAll(storage), true);
  assert.equal(storage.raw(recent.STORAGE_KEY), undefined);
});

test('malformed local data is ignored without breaking the form', () => {
  const storage = memoryStorage({ [recent.STORAGE_KEY]: '{bad json' });
  const url = field();
  assert.deepEqual(recent.listValues(storage, 'scope', url, 100), []);
  assert.equal(recent.recordValue(storage, 'scope', url, 'https://example.com/recovered', 200).saved, true);
});

test('reports unavailable or failing local storage instead of claiming success', () => {
  const url = field();
  assert.equal(recent.recordValue(null, 'scope', url, 'https://example.com/one', 100).saved, false);
  assert.equal(recent.clearAll(null), false);

  const failingStorage = {
    getItem() { return null; },
    setItem() { throw new Error('quota exceeded'); },
    removeItem() { throw new Error('storage unavailable'); }
  };
  assert.equal(recent.recordValue(failingStorage, 'scope', url, 'https://example.com/two', 200).saved, false);
  assert.equal(recent.clearAll(failingStorage), false);
});

test('committing a URL reveals recent history without starting a task', () => {
  const storage = memoryStorage();
  const { input } = interactiveHistoryField();
  const root = {
    querySelectorAll(selector) {
      return selector === 'input[data-history-kind]' ? [input] : [];
    }
  };
  const [controller] = recent.enhanceRoot(storage, 'plugin:zsxq:zsxq-group', root);
  assert.ok(controller);
  assert.equal(controller.panel.hidden, true);

  const value = 'https://wx.zsxq.com/group/48411118851818';
  input.value = value;
  input.dispatchEvent({ type: 'change' });

  assert.deepEqual(recent.listValues(storage, 'plugin:zsxq:zsxq-group', input), [value]);
  assert.equal(controller.panel.hidden, false);
  assert.equal(controller.panel.children[0].children[0].textContent, '最近使用（仅保存在本机）');
  assert.equal(controller.panel.children[1].children[0].children[0].title, value);
});

test('official reusable inputs opt in explicitly and credential fields never do', () => {
  const root = path.join(__dirname, '..');
  const manifests = fs.readdirSync(path.join(root, 'plugins'), { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .flatMap((entry) => {
      const providerRoot = path.join(root, 'plugins', entry.name, 'providers');
      if (!fs.existsSync(providerRoot)) return [];
      return fs.readdirSync(providerRoot, { withFileTypes: true })
        .filter((provider) => provider.isDirectory())
        .map((provider) => path.join(providerRoot, provider.name, 'provider.json'))
        .filter((file) => fs.existsSync(file));
    });
  const declared = [];
  for (const manifestPath of manifests) {
    const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
    for (const manifestField of manifest.fields || []) {
      if (!manifestField.history) continue;
      declared.push(`${manifest.id}.${manifestField.name}`);
      assert.notEqual(manifestField.type, 'password');
      assert.doesNotMatch(
        `${manifestField.name} ${manifestField.label} ${manifestField.arg}`,
        /(?:app.?id|client.?id|api.?key|secret|cookie|password|token|space.?id|folder.?id|knowledge.?base.?id|username)/i
      );
    }
  }
  for (const expected of [
    'zsxq-group.entry_url', 'zsxq-column.entry_url', 'yuque.book_url',
    'yuque-import.target_book_url', 'feishu-export.wiki_url', 'feishu-import.wiki_url',
    'aliyun.workspace_url', 'onenote.output', 'wiz.output', 'youdao.output'
  ]) {
    assert.ok(declared.includes(expected), `${expected} should declare recent-input history`);
  }
});

test('dedicated pages use the shared history contract and detailed import confirmations', () => {
  const root = path.join(__dirname, '..');
  const html = fs.readFileSync(path.join(root, 'wandao_electron', 'renderer', 'index.html'), 'utf8');
  const app = fs.readFileSync(path.join(root, 'wandao_electron', 'renderer', 'app.js'), 'utf8');
  assert.match(html, /<script src="recent_inputs\.js"><\/script>/);
  for (const id of [
    'zsxq-group-url', 'zsxq-column-url', 'yuque-url', 'yuque-import-url',
    'feishu-export-url', 'aliyun-url', 'yinxiang-import-source', 'ima-import-source'
  ]) {
    assert.match(html, new RegExp(`id="${id}"[^>]*data-history-kind=`), id);
  }
  assert.match(app, /function confirmImaImportWrite/);
  assert.match(app, /function confirmYinxiangImportWrite/);
  assert.match(app, /function confirmFeishuImportWrite/);
  assert.match(app, /clear-form-memory/);

  const switchStart = app.indexOf('function switchTool(toolId)');
  const switchEnd = app.indexOf('\nfunction ', switchStart + 1);
  const switchSource = app.slice(switchStart, switchEnd);
  const recordBeforeSwitch = switchSource.indexOf('recordCurrentRecentInputs();');
  const saveBeforeSwitch = switchSource.indexOf('saveCurrentFormDraft();');
  assert.ok(recordBeforeSwitch >= 0, 'switchTool should save recent inputs before leaving a provider');
  assert.ok(saveBeforeSwitch > recordBeforeSwitch, 'recent inputs should be captured before the form is replaced');

  assert.match(
    app,
    /window\.addEventListener\('beforeunload', \(\) => \{\s*recordCurrentRecentInputs\(\);\s*saveCurrentFormDraft\(\);\s*\}\);/,
    'closing the application should save both recent inputs and the form draft'
  );

  const restoreStart = app.indexOf('function restoreFormDraftForProvider(providerId)');
  const restoreEnd = app.indexOf('\nfunction ', restoreStart + 1);
  const restoreSource = app.slice(restoreStart, restoreEnd);
  const restoreDraft = restoreSource.indexOf('FORM_DRAFTS.restoreLatestDraft');
  const migrateRecent = restoreSource.indexOf('recordCurrentRecentInputs(providerId);');
  assert.ok(restoreDraft >= 0 && migrateRecent > restoreDraft,
    'restored URL drafts should be migrated into recent history on reopen');
});
