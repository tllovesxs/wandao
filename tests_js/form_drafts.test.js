const assert = require('node:assert/strict');
const test = require('node:test');
const drafts = require('../wandao_electron/renderer/form_drafts');

function memoryStorage() {
  const values = new Map();
  return {
    getItem(key) { return values.has(key) ? values.get(key) : null; },
    setItem(key, value) { values.set(key, String(value)); },
    removeItem(key) { values.delete(key); },
    raw(key) { return values.get(key); }
  };
}

function field(options = {}) {
  const attributes = options.attributes || {};
  return {
    id: options.id || '',
    name: options.name || '',
    type: options.type || 'text',
    tagName: options.tagName || 'INPUT',
    value: options.value || '',
    checked: Boolean(options.checked),
    disabled: Boolean(options.disabled),
    placeholder: options.placeholder || '',
    autocomplete: options.autocomplete || '',
    options: options.selectOptions,
    getAttribute(name) { return attributes[name] || null; }
  };
}

function form(fields) {
  return { querySelectorAll() { return fields; } };
}

test('drafts retain regular inputs and checkbox state', () => {
  const storage = memoryStorage();
  const source = form([
    field({ id: 'youdao-output', value: 'D:/exports' }),
    field({ id: 'include-images', type: 'checkbox', checked: true }),
    field({ id: 'delay', type: 'number', value: '0.5' })
  ]);

  const saved = drafts.saveDraft(storage, 'youdao', 'export', source, 100);
  assert.deepEqual(saved, { saved: true, fieldCount: 3 });

  const output = field({ id: 'youdao-output', value: '' });
  const includeImages = field({ id: 'include-images', type: 'checkbox', checked: false });
  const delay = field({ id: 'delay', type: 'number', value: '1.0' });
  const restored = drafts.restoreDraft(storage, 'youdao', 'export', form([output, includeImages, delay]));

  assert.equal(restored.restored, 3);
  assert.equal(output.value, 'D:/exports');
  assert.equal(includeImages.checked, true);
  assert.equal(delay.value, '0.5');
});

test('drafts never persist credential-like fields, even when type is text', () => {
  const storage = memoryStorage();
  const source = form([
    field({ id: 'normal-output', value: 'D:/safe' }),
    field({ id: 'youdao-cookie', value: 'session=top-secret' }),
    field({ id: 'feishu-app-secret', type: 'password', value: 'never-store' }),
    field({ id: 'ima-api-key', type: 'text', value: 'also-never-store' }),
    field({ id: 'feishu-app-id', type: 'text', value: 'cli_mismatched-id' }),
    field({ id: 'ima-client-id', type: 'text', value: 'client-mismatched-id' }),
    field({ id: 'signing-private-key', type: 'text', value: 'private-key-material' }),
    field({ id: 'accessToken', type: 'text', value: 'camel-case-secret' }),
    field({ id: 'import_mount_key', type: 'text', value: 'mount-secret' }),
    field({ id: 'target-space-id', type: 'text', value: 'space-secret' }),
    field({ id: 'target-folder-id', type: 'text', value: 'folder-secret' }),
    field({ id: 'account-username', type: 'text', value: 'private-account' }),
    field({ id: 'target', value: 'keep', attributes: { 'data-draft-sensitive': 'true' } })
  ]);

  drafts.saveDraft(storage, 'safe-provider', 'run', source, 100);
  const payload = storage.raw(drafts.STORAGE_KEY);
  assert.match(payload, /D:\/safe/);
  assert.doesNotMatch(payload, /top-secret|never-store|also-never-store|mismatched-id|private-key-material|camel-case-secret|mount-secret|space-secret|folder-secret|private-account|keep/);
});

test('latest provider action is restored without mixing drafts between actions', () => {
  const storage = memoryStorage();
  drafts.saveDraft(storage, 'yuque', 'scan', form([field({ id: 'yuque-output', value: 'D:/scan' })]), 100);
  drafts.saveDraft(storage, 'yuque', 'export', form([field({ id: 'yuque-output', value: 'D:/export' })]), 200);
  drafts.saveDraft(storage, 'feishu', 'import', form([field({ id: 'feishu-source', value: 'D:/other' })]), 300);

  const output = field({ id: 'yuque-output', value: '' });
  const restored = drafts.restoreLatestDraft(storage, 'yuque', form([output]));
  assert.deepEqual(restored, { restored: 1, actionId: 'export' });
  assert.equal(output.value, 'D:/export');
});

test('selects do not lose their default when its restored option is unavailable', () => {
  const storage = memoryStorage();
  drafts.saveDraft(storage, 'provider', 'run', form([field({ id: 'scope', tagName: 'SELECT', value: 'all' })]), 100);
  const scope = field({ id: 'scope', tagName: 'SELECT', value: 'current', selectOptions: [{ value: 'current' }] });

  const restored = drafts.restoreDraft(storage, 'provider', 'run', form([scope]));
  assert.equal(restored.restored, 0);
  assert.equal(scope.value, 'current');
});

test('provider-owned config fields never override the freshly loaded config', () => {
  const storage = memoryStorage();
  const source = form([
    field({ id: 'ima-export-kb-id', value: 'kb-old', attributes: { 'data-draft': 'false' } }),
    field({ id: 'ima-export-output', value: 'D:/draft-output' })
  ]);
  drafts.saveDraft(storage, 'ima-export', 'default', source, 100);

  const knowledgeBase = field({
    id: 'ima-export-kb-id',
    value: 'kb-from-config',
    attributes: { 'data-draft': 'false' }
  });
  const output = field({ id: 'ima-export-output', value: '' });
  const restored = drafts.restoreLatestDraft(storage, 'ima-export', form([knowledgeBase, output]));

  assert.equal(restored.restored, 1);
  assert.equal(knowledgeBase.value, 'kb-from-config');
  assert.equal(output.value, 'D:/draft-output');
});

test('drafts reject credential-bearing URLs and can be cleared', () => {
  const storage = memoryStorage();
  const source = form([
    field({ id: 'safe-url', value: 'https://example.com/docs' }),
    field({ id: 'signed-url', value: 'https://example.com/docs?X-Amz-Signature=secret' }),
    field({ id: 'callback-url', value: 'https://example.com/#access_token=secret' })
  ]);
  drafts.saveDraft(storage, 'provider', 'run', source, 100);
  const payload = storage.raw(drafts.STORAGE_KEY);
  assert.match(payload, /https:\/\/example\.com\/docs/);
  assert.doesNotMatch(payload, /Signature|access_token|secret/);

  assert.equal(drafts.clearAll(storage), true);
  assert.equal(storage.raw(drafts.STORAGE_KEY), undefined);
});
