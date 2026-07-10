const assert = require('node:assert/strict');
const crypto = require('crypto');
const fs = require('fs');
const http = require('http');
const os = require('os');
const path = require('path');
const test = require('node:test');
const { PluginManager } = require('../wandao_electron/plugin_manager');
const {
  canonicalStringify,
  createPluginEnvelope,
  sha256Hex,
  signEnvelope,
  verifyPluginEnvelope,
  verifyRegistryEnvelope
} = require('../wandao_electron/plugin_format');

function fixture() {
  const pair = crypto.generateKeyPairSync('ed25519');
  const privateKey = pair.privateKey.export({ type: 'pkcs8', format: 'pem' });
  const publicKey = pair.publicKey.export({ type: 'spki', format: 'pem' });
  const trustStore = { keys: [{ id: 'test-key', algorithm: 'ed25519', publicKey }] };
  return { privateKey, trustStore };
}

function pluginBuffer(version, fixtureData, overrides = {}) {
  const manifest = {
    schemaVersion: 1,
    id: 'demo',
    name: 'Demo',
    description: 'Demo plugin',
    version,
    publisher: 'Tests',
    core: { minVersion: '1.2.8' },
    entrypoints: { providers: ['providers/demo/provider.json'] },
    permissions: ['filesystem:write', 'process'],
    ...overrides
  };
  const provider = JSON.stringify({ schemaVersion: 1, id: 'demo' });
  const envelope = createPluginEnvelope(manifest, {
    'providers/demo/provider.json': provider,
    'backend/run.py': 'print("ok")\n'
  });
  return Buffer.from(JSON.stringify(signEnvelope(envelope, fixtureData.privateKey, 'test-key')));
}

test('signed plugin detects tampering', () => {
  const keys = fixture();
  const buffer = pluginBuffer('1.0.0', keys);
  const envelope = JSON.parse(buffer.toString('utf8'));
  verifyPluginEnvelope(envelope, keys.trustStore);
  envelope.files['backend/run.py'] = Buffer.from('print("tampered")').toString('base64');
  assert.throws(() => verifyPluginEnvelope(envelope, keys.trustStore), /完整性/);
});

test('signed plugin still rejects path traversal', () => {
  const keys = fixture();
  const original = JSON.parse(pluginBuffer('1.0.0', keys).toString('utf8'));
  const files = { ...original.files, '../escape.py': Buffer.from('bad').toString('base64') };
  const body = { formatVersion: 1, manifest: original.manifest, files };
  const envelope = signEnvelope({
    ...body,
    integrity: { algorithm: 'sha256', value: sha256Hex(canonicalStringify(body)) }
  }, keys.privateKey, 'test-key');
  assert.throws(() => verifyPluginEnvelope(envelope, keys.trustStore), /相对路径|\.\./);
});

test('plugin manager installs, updates, disables, rolls back and uninstalls', () => {
  const keys = fixture();
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'wandao-plugin-test-'));
  try {
    const manager = new PluginManager({ rootDir: root, trustStore: keys.trustStore, coreVersion: '1.2.8', platform: 'win32' });
    manager.installBuffer(pluginBuffer('1.0.0', keys));
    assert.equal(manager.listInstalled()[0].currentVersion, '1.0.0');
    assert.equal(manager.providerEntries().length, 1);
    manager.installBuffer(pluginBuffer('1.1.0', keys));
    assert.equal(manager.describeInstalled('demo').currentVersion, '1.1.0');
    assert.deepEqual(manager.describeInstalled('demo').previousVersions, ['1.0.0']);
    assert.equal(manager.resolveScript('demo', 'backend/run.py').plugin.currentVersion, '1.1.0');
    manager.setEnabled('demo', false);
    assert.equal(manager.providerEntries().length, 0);
    manager.setEnabled('demo', true);
    assert.equal(manager.rollback('demo').currentVersion, '1.0.0');
    assert.equal(manager.uninstall('demo'), true);
    assert.equal(manager.listInstalled().length, 0);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('registry signature and package digest are verifiable', () => {
  const keys = fixture();
  const pkg = pluginBuffer('1.0.0', keys);
  const registry = signEnvelope({
    formatVersion: 1,
    generatedAt: '2026-07-10T00:00:00Z',
    plugins: [{
      id: 'demo', name: 'Demo', description: 'Demo plugin', publisher: 'Tests', version: '1.0.0',
      minCoreVersion: '1.2.8', permissions: ['filesystem:write', 'process'], platforms: ['win32'],
      packageUrl: 'https://example.com/demo.wandao-plugin', sha256: sha256Hex(pkg)
    }]
  }, keys.privateKey, 'test-key');
  assert.equal(verifyRegistryEnvelope(registry, keys.trustStore).plugins[0].id, 'demo');
  const altered = JSON.parse(canonicalStringify(registry));
  altered.plugins[0].version = '9.9.9';
  assert.throws(() => verifyRegistryEnvelope(altered, keys.trustStore), /签名/);
});

test('incompatible core and platform are rejected', () => {
  const keys = fixture();
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'wandao-plugin-compat-'));
  try {
    const manager = new PluginManager({ rootDir: root, trustStore: keys.trustStore, coreVersion: '1.2.8', platform: 'win32' });
    assert.throws(() => manager.installBuffer(pluginBuffer('1.0.0', keys, { core: { minVersion: '2.0.0' } })), /需要万能导/);
    assert.throws(() => manager.installBuffer(pluginBuffer('1.0.0', keys, { platforms: ['darwin'] })), /不支持当前系统/);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('installed plugin tampering is blocked before discovery and execution', () => {
  const keys = fixture();
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'wandao-plugin-tamper-'));
  try {
    const manager = new PluginManager({ rootDir: root, trustStore: keys.trustStore, coreVersion: '1.2.8', platform: 'win32' });
    manager.installBuffer(pluginBuffer('1.0.0', keys));
    const script = path.join(root, 'installed', 'demo', '1.0.0', 'backend', 'run.py');
    fs.writeFileSync(script, 'print("tampered")\n');
    const discovery = manager.providerEntriesWithErrors();
    assert.equal(discovery.entries.length, 0);
    assert.match(discovery.errors[0], /完整性|签名/);
    assert.throws(() => manager.resolveScript('demo', 'backend/run.py'), /修改|完整性|签名/);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('signed registry installs a plugin over the network', async () => {
  const keys = fixture();
  const pkg = pluginBuffer('1.0.0', keys);
  let registry;
  const server = http.createServer((request, response) => {
    if (request.url === '/registry.json') {
      response.setHeader('content-type', 'application/json');
      response.end(JSON.stringify(registry));
    } else if (request.url === '/demo.wandao-plugin') {
      response.end(pkg);
    } else {
      response.statusCode = 404;
      response.end();
    }
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  registry = signEnvelope({
    formatVersion: 1,
    generatedAt: '2026-07-10T00:00:00Z',
    plugins: [{
      id: 'demo', name: 'Demo', description: 'Demo plugin', publisher: 'Tests', version: '1.0.0',
      minCoreVersion: '1.2.8', permissions: ['filesystem:write', 'process'], platforms: ['win32'],
      packageUrl: `http://127.0.0.1:${port}/demo.wandao-plugin`, sha256: sha256Hex(pkg)
    }]
  }, keys.privateKey, 'test-key');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'wandao-plugin-network-'));
  try {
    const manager = new PluginManager({
      rootDir: root,
      trustStore: keys.trustStore,
      coreVersion: '1.2.8',
      platform: 'win32',
      registryUrl: `http://127.0.0.1:${port}/registry.json`,
      allowLocalHttp: true
    });
    const index = await manager.fetchRegistry();
    await manager.installFromRegistry('demo', index);
    assert.equal(manager.describeInstalled('demo').currentVersion, '1.0.0');
  } finally {
    await new Promise((resolve) => server.close(resolve));
    fs.rmSync(root, { recursive: true, force: true });
  }
});
