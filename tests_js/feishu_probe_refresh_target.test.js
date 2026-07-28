const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const vm = require('node:vm');

const appJs = fs.readFileSync('wandao_electron/renderer/app.js', 'utf8');

function loadTargetHelper({
  config = {},
  configPath = 'C:/Users/demo/AppData/Roaming/wandao/plugin-data/feishu/feishu_import_config.json',
  writeResult = { success: true },
  appId = 'current-app-id',
  appSecret = 'current-app-secret'
} = {}) {
  const start = appJs.indexOf('async function applyProbedFeishuTarget');
  const end = appJs.indexOf('// Load Feishu Import Tool', start);
  assert.ok(start >= 0, 'applyProbedFeishuTarget must exist');
  assert.ok(end > start, 'target helper must end before the Feishu tool loader');

  const inputs = {
    'feishu-import-app-id': { value: appId },
    'feishu-import-app-secret': { value: appSecret },
    'feishu-import-space-id': { value: 'old-space' },
    'feishu-import-parent-token': { value: 'old-parent' }
  };
  const writes = [];
  const logs = [];
  const alerts = [];
  const context = {
    document: {
      getElementById(id) {
        return inputs[id] || null;
      }
    },
    window: {
      electronAPI: {
        async writeFile(filePath, content) {
          writes.push({ filePath, content });
          return writeResult;
        }
      }
    },
    feishuImportConfig: { ...config },
    feishuImportConfigPath: () => configPath,
    log: (message, level) => logs.push({ message, level }),
    alert: (message) => alerts.push(message)
  };
  vm.createContext(context);
  vm.runInContext(
    `${appJs.slice(start, end)}; globalThis.applyProbedFeishuTarget = applyProbedFeishuTarget;`,
    context
  );
  return { context, inputs, writes, logs, alerts };
}

test('successful probe replaces stale target fields and preserves current credentials', async () => {
  const harness = loadTargetHelper({
    config: {
      app_id: 'old-app-id',
      app_secret: 'old-app-secret',
      drive_folder_token: 'folder-test',
      space_id: 'old-space',
      parent_wiki_token: 'old-parent'
    }
  });

  const result = await harness.context.applyProbedFeishuTarget({
    spaceId: ' space-test ',
    targetWikiToken: ' parent-test '
  });

  assert.equal(result.updated, true);
  assert.equal(result.saved, true);
  assert.equal(harness.inputs['feishu-import-space-id'].value, 'space-test');
  assert.equal(harness.inputs['feishu-import-parent-token'].value, 'parent-test');
  assert.equal(harness.writes.length, 1);
  assert.deepEqual(JSON.parse(harness.writes[0].content), {
    app_id: 'current-app-id',
    app_secret: 'current-app-secret',
    drive_folder_token: 'folder-test',
    space_id: 'space-test',
    parent_wiki_token: 'parent-test',
    obj_type: 'docx'
  });
  assert.match(harness.logs.at(-1).message, /已将目标 Wiki 更新为最新探测结果/);
});

test('partial probe result leaves both stale fields and config untouched', async () => {
  const harness = loadTargetHelper();
  const result = await harness.context.applyProbedFeishuTarget({ spaceId: 'new-space' });

  assert.equal(result.updated, false);
  assert.equal(result.saved, false);
  assert.equal(harness.inputs['feishu-import-space-id'].value, 'old-space');
  assert.equal(harness.inputs['feishu-import-parent-token'].value, 'old-parent');
  assert.equal(harness.writes.length, 0);
  assert.equal(harness.logs.at(-1).level, 'error');
  assert.match(harness.alerts.at(-1), /探测结果不完整/);
});

test('write failure keeps the latest target in the current form and warns', async () => {
  const harness = loadTargetHelper({
    config: { space_id: 'old-space', parent_wiki_token: 'old-parent' },
    writeResult: { success: false, error: 'permission denied' }
  });
  const result = await harness.context.applyProbedFeishuTarget({
    spaceId: 'new-space',
    targetWikiToken: 'new-parent'
  });

  assert.equal(result.updated, true);
  assert.equal(result.saved, false);
  assert.equal(harness.inputs['feishu-import-space-id'].value, 'new-space');
  assert.equal(harness.inputs['feishu-import-parent-token'].value, 'new-parent');
  assert.equal(harness.context.feishuImportConfig.space_id, 'old-space');
  assert.equal(harness.logs.at(-1).level, 'warn');
  assert.match(harness.alerts.at(-1), /未能保存到本机配置/);
});

test('missing config path keeps the current form update without attempting a write', async () => {
  const harness = loadTargetHelper({ configPath: '' });
  const result = await harness.context.applyProbedFeishuTarget({
    spaceId: 'new-space',
    targetWikiToken: 'new-parent'
  });

  assert.equal(result.updated, true);
  assert.equal(result.saved, false);
  assert.equal(harness.writes.length, 0);
  assert.equal(harness.inputs['feishu-import-space-id'].value, 'new-space');
  assert.match(harness.alerts.at(-1), /未能保存到本机配置/);
});

test('the dedicated probe handler awaits the target refresh helper', () => {
  const start = appJs.indexOf("document.getElementById('feishu-import-probe').addEventListener");
  const end = appJs.indexOf("document.getElementById('feishu-import-plan').addEventListener", start);
  const handler = appJs.slice(start, end);

  assert.match(handler, /await applyProbedFeishuTarget\(data\)/);
  assert.doesNotMatch(handler, /!document\.getElementById\('feishu-import-space-id'\)\.value\.trim\(\)/);
  assert.doesNotMatch(handler, /!document\.getElementById\('feishu-import-parent-token'\)\.value\.trim\(\)/);
});
