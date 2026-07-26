const test = require('node:test');
const assert = require('node:assert/strict');
const {
  canonicalStringify,
  createPluginEnvelope,
  sha256Hex,
  validatePluginManifest,
  verifyPluginEnvelope
} = require('../wandao_electron/plugin_format');

// T3：plugin.json 是 tolerant reader —— v1 内新增的可选字段对旧核心只是一条告警，
// 不再是破坏性变更。但「已知字段」与权限声明的校验必须原样严格。

function baseManifest(overrides = {}) {
  return {
    schemaVersion: 1,
    id: 'demo',
    name: 'Demo',
    description: 'Demo plugin',
    version: '1.0.0',
    publisher: 'Tests',
    core: { minVersion: '1.2.8' },
    entrypoints: { providers: ['providers/demo/provider.json'] },
    permissions: ['filesystem:write'],
    ...overrides
  };
}

function captureUnknownKeys(run) {
  const collected = [];
  const previous = validatePluginManifest.onUnknownKey;
  validatePluginManifest.onUnknownKey = (keys) => collected.push(...keys);
  try {
    run();
  } finally {
    validatePluginManifest.onUnknownKey = previous;
  }
  return collected;
}

test('未知顶层字段被接受，只产生告警', () => {
  const manifest = baseManifest({ futureField: { any: 'shape' }, anotherNewOne: 42 });
  let returned = null;
  const warned = captureUnknownKeys(() => {
    returned = validatePluginManifest(manifest);
  });
  assert.equal(returned, manifest);
  assert.deepEqual(warned.sort(), ['anotherNewOne', 'futureField']);
});

test('没有未知字段时不产生告警', () => {
  const warned = captureUnknownKeys(() => validatePluginManifest(baseManifest()));
  assert.deepEqual(warned, []);
});

test('默认告警走 console.warn 且不抛错', () => {
  const messages = [];
  const originalWarn = console.warn;
  const previousHook = validatePluginManifest.onUnknownKey;
  validatePluginManifest.onUnknownKey = undefined;
  console.warn = (...args) => messages.push(args.join(' '));
  try {
    validatePluginManifest(baseManifest({ experimentalFlag: true }));
  } finally {
    console.warn = originalWarn;
    validatePluginManifest.onUnknownKey = previousHook;
  }
  assert.equal(messages.length, 1);
  assert.match(messages[0], /experimentalFlag/);
});

test('已登记的可选字段不算未知字段', () => {
  const warned = captureUnknownKeys(() => validatePluginManifest(baseManifest({
    $schema: '../plugin.schema.json',
    homepage: 'https://example.com',
    license: 'GPL-3.0',
    platforms: ['win32']
  })));
  assert.deepEqual(warned, []);
});

test('必填字段缺失仍然被拒', () => {
  assert.throws(() => validatePluginManifest(baseManifest({ id: undefined })), /插件 ID 不合法/);
  assert.throws(() => validatePluginManifest(baseManifest({ version: undefined })), /插件版本不合法/);
  assert.throws(() => validatePluginManifest(baseManifest({ name: undefined })), /缺少字段：name/);
  assert.throws(() => validatePluginManifest(baseManifest({ description: '' })), /缺少字段：description/);
  assert.throws(() => validatePluginManifest(baseManifest({ publisher: '   ' })), /缺少字段：publisher/);
  assert.throws(() => validatePluginManifest(baseManifest({ entrypoints: undefined })), /Provider 入口/);
  assert.throws(() => validatePluginManifest(baseManifest({ schemaVersion: undefined })), /schemaVersion=1/);
});

test('已知字段类型错误仍然被拒', () => {
  assert.throws(() => validatePluginManifest(baseManifest({ schemaVersion: '1' })), /schemaVersion=1/);
  assert.throws(() => validatePluginManifest(baseManifest({ schemaVersion: 2 })), /schemaVersion=1/);
  assert.throws(() => validatePluginManifest(baseManifest({ name: [] })), /缺少字段：name/);
  assert.throws(() => validatePluginManifest(baseManifest({ version: 100 })), /插件版本不合法/);
  assert.throws(() => validatePluginManifest(baseManifest({ id: 'A' })), /插件 ID 不合法/);
  assert.throws(() => validatePluginManifest(baseManifest({ permissions: 'network' })), /不支持的权限/);
  assert.throws(() => validatePluginManifest(baseManifest({ entrypoints: { providers: 'a.json' } })), /Provider 入口/);
  assert.throws(() => validatePluginManifest(baseManifest({ entrypoints: { providers: [] } })), /Provider 入口/);
  assert.throws(() => validatePluginManifest(baseManifest({ platforms: 'win32' })), /platforms/);
  assert.throws(() => validatePluginManifest(baseManifest({ platforms: ['win32', 'solaris'] })), /platforms/);
  assert.throws(() => validatePluginManifest('{}'), /必须是对象/);
  assert.throws(() => validatePluginManifest([]), /必须是对象/);
  assert.throws(() => validatePluginManifest(null), /必须是对象/);
});

// 安全红线：宽松只针对「完全不认识的顶层键」，安全相关字段的取值校验一律不放松。
test('红线：未知字段不能松动 permissions 校验', () => {
  assert.throws(
    () => validatePluginManifest(baseManifest({ permissions: ['filesystem:write', 'root'], futureField: 1 })),
    /不支持的权限/
  );
  assert.throws(
    () => validatePluginManifest(baseManifest({ permissions: ['*'], futureField: 1 })),
    /不支持的权限/
  );
});

test('红线：未知字段不能松动 entrypoints 路径安全校验', () => {
  assert.throws(
    () => validatePluginManifest(baseManifest({ entrypoints: { providers: ['../escape/provider.json'] }, futureField: 1 })),
    /Provider 入口/
  );
  assert.throws(
    () => validatePluginManifest(baseManifest({ entrypoints: { providers: ['/etc/provider.json'] }, futureField: 1 })),
    /Provider 入口/
  );
  assert.throws(
    () => validatePluginManifest(baseManifest({ entrypoints: { providers: ['a.json'], ui: '../evil.html' }, futureField: 1 })),
    /自定义 UI 入口/
  );
});

test('红线：未知字段不能松动 core.minVersion / id / version 校验', () => {
  assert.throws(
    () => validatePluginManifest(baseManifest({ core: { minVersion: 'latest' }, futureField: 1 })),
    /core\.minVersion/
  );
  assert.throws(
    () => validatePluginManifest(baseManifest({ id: 'Demo Plugin', futureField: 1 })),
    /插件 ID 不合法/
  );
  assert.throws(
    () => validatePluginManifest(baseManifest({ id: '../../etc', futureField: 1 })),
    /插件 ID 不合法/
  );
  assert.throws(
    () => validatePluginManifest(baseManifest({ version: 'v1', futureField: 1 })),
    /插件版本不合法/
  );
});

test('红线：未知字段仍进入签名内容，不产生绕过', () => {
  const manifest = baseManifest({ futureField: 'original' });
  const previous = validatePluginManifest.onUnknownKey;
  validatePluginManifest.onUnknownKey = () => {};
  let envelope;
  try {
    envelope = createPluginEnvelope(manifest, {
      'providers/demo/provider.json': JSON.stringify({ schemaVersion: 1, id: 'demo' })
    });
    verifyPluginEnvelope(envelope, null, { allowUnsigned: true });
    const tampered = {
      ...envelope,
      manifest: { ...envelope.manifest, futureField: 'tampered' }
    };
    assert.throws(() => verifyPluginEnvelope(tampered, null, { allowUnsigned: true }), /完整性/);
  } finally {
    validatePluginManifest.onUnknownKey = previous;
  }
  const body = { formatVersion: envelope.formatVersion, manifest: envelope.manifest, files: envelope.files };
  assert.match(canonicalStringify(body), /futureField/);
  assert.equal(envelope.integrity.value, sha256Hex(canonicalStringify(body)));
});

test('plugin.schema.json 允许未来的可选字段', () => {
  const schema = require('../plugins/plugin.schema.json');
  assert.equal(schema.additionalProperties, true);
});
