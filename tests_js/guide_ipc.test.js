const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const assert = require('node:assert/strict');

const repoRoot = path.resolve(__dirname, '..');
const appSource = fs.readFileSync(path.join(repoRoot, 'wandao_electron', 'renderer', 'app.js'), 'utf8');
const mainSource = fs.readFileSync(path.join(repoRoot, 'wandao_electron', 'main.js'), 'utf8');
const preloadSource = fs.readFileSync(path.join(repoRoot, 'wandao_electron', 'preload.js'), 'utf8');

test('Electron exposes and registers the restricted provider guide image IPC', () => {
  assert.match(mainSource, /require\('\.\/guide_assets'\)/);
  assert.match(mainSource, /ipcMain\.handle\('read-provider-guide-image'/);
  assert.match(mainSource, /readGuideImageDataUrl\(providerRoot, relativePath\)/);
  assert.match(preloadSource, /readProviderGuideImage:\s*\(providerId, relativePath\)\s*=>\s*ipcRenderer\.invoke\('read-provider-guide-image'/);
});

test('provider guide rendering hydrates image placeholders after inserting Markdown', () => {
  assert.match(appSource, /async function hydrateGuideImages\(container, providerId\)/);
  assert.match(appSource, /window\.electronAPI\.readProviderGuideImage\(providerId, imagePath\)/);
  assert.match(appSource, /hydrateGuideImages\(contentArea, provider\.id\)/);
  assert.match(appSource, /bindCollapsibleGuideImages\(contentArea, provider\.id\)/);
});
