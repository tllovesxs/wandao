const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const test = require('node:test');
const { resolveProviderScript } = require('../wandao_electron/provider_script_routing');

test('uses an explicit provider default script', () => {
  assert.equal(
    resolveProviderScript('bundled-plugin:demo:backend/default.py', [
      { script: 'bundled-plugin:demo:backend/action.py' }
    ]),
    'bundled-plugin:demo:backend/default.py'
  );
});

test('uses the one validated action script for legacy templates', () => {
  const script = 'bundled-plugin:feishu:backend/export_feishu.py';
  assert.equal(resolveProviderScript('', [{ script }, { script }]), script);
});

test('does not choose an ambiguous action script', () => {
  assert.equal(
    resolveProviderScript('', [
      { script: 'bundled-plugin:demo:backend/one.py' },
      { script: 'bundled-plugin:demo:backend/two.py' }
    ]),
    ''
  );
});

test('all bundled action providers expose one legacy-compatible backend script', () => {
  const pluginsRoot = path.resolve(__dirname, '..', 'plugins');
  for (const pluginId of fs.readdirSync(pluginsRoot)) {
    const providersRoot = path.join(pluginsRoot, pluginId, 'providers');
    if (!fs.existsSync(providersRoot)) continue;
    for (const providerId of fs.readdirSync(providersRoot)) {
      const providerPath = path.join(providersRoot, providerId, 'provider.json');
      if (!fs.existsSync(providerPath)) continue;
      const provider = JSON.parse(fs.readFileSync(providerPath, 'utf8'));
      const scripts = (provider.actions || []).map((action) => action.script || provider.script || '');
      if (!scripts.some(Boolean)) continue;
      const resolvedActions = scripts.map((script) => ({
        script: `bundled-plugin:${pluginId}:${script.replace(/^\.\.\/\.\.\//, '')}`
      }));
      assert.notEqual(resolveProviderScript('', resolvedActions), '', `${provider.id} must retain a safe default script`);
    }
  }
});

test('Tauri routes legacy templates through one validated action script', () => {
  const providerSource = fs.readFileSync(
    path.resolve(__dirname, '..', 'wandao_electron', 'src-tauri', 'src', 'providers.rs'),
    'utf8'
  );
  assert.match(providerSource, /let scripts: HashSet<String> = normalized_actions/);
  assert.match(providerSource, /if scripts\.len\(\) == 1/);
  assert.match(providerSource, /object\.insert\("script"\.into\(\), json!\(resolved_default\)\)/);
  assert.match(providerSource, /if !is_inside\(&plugin\.plugin_root, &resolved\)/);
});
