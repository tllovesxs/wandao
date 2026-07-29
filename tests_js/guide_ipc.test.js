const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const assert = require('node:assert/strict');

const repoRoot = path.resolve(__dirname, '..');
const appSource = fs.readFileSync(path.join(repoRoot, 'wandao_electron', 'renderer', 'app.js'), 'utf8');
const commandsSource = fs.readFileSync(path.join(repoRoot, 'wandao_electron', 'src-tauri', 'src', 'commands.rs'), 'utf8');
const providersSource = fs.readFileSync(path.join(repoRoot, 'wandao_electron', 'src-tauri', 'src', 'providers.rs'), 'utf8');
const bridgeSource = fs.readFileSync(path.join(repoRoot, 'wandao_electron', 'renderer', 'tauri_bridge.js'), 'utf8');

test('Tauri exposes and registers the restricted provider guide image command', () => {
  assert.match(commandsSource, /pub async fn read_provider_guide_image/);
  assert.match(commandsSource, /fetch_remote_guide_image\(&provider_id, remote_url, &spec\)\.await/);
  assert.match(commandsSource, /RedirectUrlPolicy::RemoteGuideImage/);
  assert.match(commandsSource, /CONTENT_TYPE/);
  assert.match(commandsSource, /starts_with\(b"\\x89PNG\\r\\n\\x1a\\n"\)/);
  assert.match(commandsSource, /sha256_hex\(&output\) != spec\.sha256/);
  assert.match(providersSource, /pub fn read_guide_image_data_url/);
  assert.match(providersSource, /if !is_inside\(provider_root, &target\)/);
  assert.match(providersSource, /pub fn is_allowed_remote_guide_image_url/);
  assert.match(providersSource, /82c027b054d9ece8449af30d79600814eb823e46/);
  assert.match(providersSource, /pub fn remote_guide_asset_spec/);
  assert.match(bridgeSource, /readProviderGuideImage:\s*\(providerId, relativePath\)\s*=>\s*invokeCommand\('read_provider_guide_image'/);
});

test('provider guide rendering hydrates image placeholders after inserting Markdown', () => {
  assert.match(appSource, /async function hydrateGuideImages\(container, providerId\)/);
  assert.match(appSource, /window\.electronAPI\.readProviderGuideImage\(providerId, imagePath\)/);
  assert.match(appSource, /hydrateGuideImages\(contentArea, provider\.id\)/);
  assert.match(appSource, /bindCollapsibleGuideImages\(contentArea, provider\.id\)/);
  assert.match(appSource, /Math\.min\(3, pending\.length\)/);
  assert.match(appSource, /safeRemoteGuideImageUrl\(outcome\.result\?\.fallbackUrl \|\| imagePath\)/);
  assert.match(appSource, /className = 'guide-image-retry'/);
  assert.match(appSource, /await requestGuideImage\(providerId, imagePath\)/);
});
