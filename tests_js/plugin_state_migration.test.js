const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const { migrateLegacyPluginState } = require('../wandao_electron/plugin_state_migration');

function withTemporaryRoots(run) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'wandao-plugin-state-'));
  try {
    return run(path.join(root, 'legacy'), path.join(root, 'plugin-data'));
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
}

test('migrates a missing known legacy credential exactly once', () => {
  withTemporaryRoots((legacyRoot, dataRoot) => {
    fs.mkdirSync(legacyRoot, { recursive: true });
    fs.writeFileSync(path.join(legacyRoot, '.youdao_auth.json'), '{"cookies":[]}');

    assert.deepEqual(migrateLegacyPluginState({ pluginId: 'youdao', legacyRoot, dataRoot }), ['.youdao_auth.json']);
    assert.equal(fs.readFileSync(path.join(dataRoot, '.youdao_auth.json'), 'utf8'), '{"cookies":[]}');
    assert.deepEqual(migrateLegacyPluginState({ pluginId: 'youdao', legacyRoot, dataRoot }), []);
  });
});

test('preserves newer plugin state and migrates nested Yinxiang state safely', () => {
  withTemporaryRoots((legacyRoot, dataRoot) => {
    const legacyDb = path.join(legacyRoot, 'yinxiang', 'yinxiang_china.db');
    const existingAuth = path.join(dataRoot, '.yuque_auth.json');
    fs.mkdirSync(path.dirname(legacyDb), { recursive: true });
    fs.mkdirSync(path.dirname(existingAuth), { recursive: true });
    fs.writeFileSync(legacyDb, 'legacy-db');
    fs.writeFileSync(path.join(legacyRoot, '.yuque_auth.json'), 'legacy-cookie');
    fs.writeFileSync(existingAuth, 'newer-cookie');

    assert.deepEqual(migrateLegacyPluginState({ pluginId: 'yuque', legacyRoot, dataRoot }), []);
    assert.equal(fs.readFileSync(existingAuth, 'utf8'), 'newer-cookie');
    assert.deepEqual(migrateLegacyPluginState({ pluginId: 'yinxiang', legacyRoot, dataRoot }), ['yinxiang/yinxiang_china.db']);
    assert.equal(fs.readFileSync(path.join(dataRoot, 'yinxiang', 'yinxiang_china.db'), 'utf8'), 'legacy-db');
  });
});

test('does not migrate state for an unknown plugin', () => {
  withTemporaryRoots((legacyRoot, dataRoot) => {
    fs.mkdirSync(legacyRoot, { recursive: true });
    fs.writeFileSync(path.join(legacyRoot, '.youdao_auth.json'), 'cookie');

    assert.deepEqual(migrateLegacyPluginState({ pluginId: 'third-party', legacyRoot, dataRoot }), []);
    assert.equal(fs.existsSync(path.join(dataRoot, '.youdao_auth.json')), false);
  });
});
