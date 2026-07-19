const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const test = require('node:test');
const { resolveLegacyTemplateConfig } = require('../wandao_electron/provider_legacy_compat');

test('maps Plugin v1 URL and output fields for legacy templates', () => {
  assert.deepEqual(resolveLegacyTemplateConfig({ fields: [
    { name: 'wiki_url', arg: '--wiki-url' },
    { name: 'output', arg: '--output' }
  ] }), { urlParam: '--wiki-url', outputParam: '--output', noUrl: false });
});

test('does not confuse a select field containing link in its name with the URL input', () => {
  assert.deepEqual(resolveLegacyTemplateConfig({ fields: [
    { name: 'entry_url', type: 'text', arg: '--entry-url' },
    { name: 'follow_link_scope', type: 'select', arg: '--follow-link-scope' },
    { name: 'output', type: 'directory', arg: '--output' }
  ] }), { urlParam: '--entry-url', outputParam: '--output', noUrl: false });
});

test('preserves explicit legacy settings and recognizes no-URL providers', () => {
  assert.deepEqual(resolveLegacyTemplateConfig({
    urlParam: '--entry-url', outputParam: '--destination', noUrl: false,
    fields: [{ name: 'output', arg: '--output' }]
  }), { urlParam: '--entry-url', outputParam: '--destination', noUrl: false });
  assert.deepEqual(resolveLegacyTemplateConfig({ fields: [{ name: 'database', arg: '--database' }] }), {
    urlParam: '', outputParam: '', noUrl: true
  });
});

test('every bundled provider with an old HTML template resolves legacy parameters', () => {
  const repoRoot = path.resolve(__dirname, '..');
  const html = fs.readFileSync(path.join(repoRoot, 'wandao_electron', 'renderer', 'index.html'), 'utf8');
  const pluginsRoot = path.join(repoRoot, 'plugins');
  for (const pluginId of fs.readdirSync(pluginsRoot)) {
    const providersRoot = path.join(pluginsRoot, pluginId, 'providers');
    if (!fs.existsSync(providersRoot)) continue;
    for (const providerDir of fs.readdirSync(providersRoot)) {
      const manifestPath = path.join(providersRoot, providerDir, 'provider.json');
      if (!fs.existsSync(manifestPath)) continue;
      const provider = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
      const templateId = provider.templateId || `template-${provider.id}`;
      if (!html.includes(`id="${templateId}"`)) continue;
      const config = resolveLegacyTemplateConfig(provider);
      const fields = provider.fields || [];
      const hasUrlField = fields.some((field) => /(?:^|[_-])(?:url|link)(?:$|[_-])/i.test(String(field.name || '')) || /(?:^|-)url(?:$|-)/i.test(String(field.arg || '')));
      const hasOutputField = fields.some((field) => String(field.arg || '').toLowerCase() === '--output');
      if (hasUrlField) {
        assert.match(config.urlParam, /^--/, `${provider.id} must not pass an undefined URL option to its legacy template`);
        assert.equal(config.noUrl, false, `${provider.id} must require its URL field`);
      }
      if (hasOutputField) assert.match(config.outputParam, /^--/, `${provider.id} must not pass an undefined output option to its legacy template`);
    }
  }
});

test('knowledge-star legacy templates explicitly declare their required entry URL', () => {
  const repoRoot = path.resolve(__dirname, '..');
  for (const providerId of ['zsxq-column', 'zsxq-group']) {
    const manifestPath = path.join(repoRoot, 'plugins', 'zsxq', 'providers', providerId, 'provider.json');
    const provider = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
    assert.equal(provider.urlParam, '--entry-url', `${providerId} must be compatible with desktop releases that infer template URL fields`);
    assert.equal(provider.noUrl, false, `${providerId} login must require an entry URL`);
    assert.deepEqual(resolveLegacyTemplateConfig(provider), {
      urlParam: '--entry-url',
      outputParam: '--output',
      noUrl: false
    });
  }
});
test('main process applies compatibility after validating Provider v1 fields', () => {
  const mainSource = fs.readFileSync(path.resolve(__dirname, '..', 'wandao_electron', 'main.js'), 'utf8');
  assert.match(mainSource, /Object\.assign\(provider, resolveLegacyTemplateConfig\(raw\)\)/);
});

test('main process applies compatibility before returning the Provider', () => {
  const mainSource = fs.readFileSync(path.resolve(__dirname, '..', 'wandao_electron', 'main.js'), 'utf8');
  const compatibilityIndex = mainSource.indexOf('Object.assign(provider, resolveLegacyTemplateConfig(raw));');
  const returnIndex = mainSource.indexOf('return provider;', compatibilityIndex);
  assert.ok(compatibilityIndex >= 0, 'Provider compatibility projection must exist');
  assert.ok(returnIndex > compatibilityIndex, 'Provider compatibility projection must run before return');
});
