const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const source = fs.readFileSync(
  path.join(__dirname, '..', 'wandao_electron', 'renderer', 'markdown_dock.js'),
  'utf8',
);

test('Markdown reader reuses local image data URLs across re-renders', () => {
  assert.match(source, /const markdownAssetCache = new Map\(\)/);
  assert.match(source, /readCachedMarkdownAsset\(markdownPath, assetPath\)/);
  assert.match(source, /cacheMarkdownAsset\(markdownPath, assetPath, result\.dataUrl\)/);
  assert.match(source, /MAX_MARKDOWN_ASSET_CACHE_ENTRIES = 48/);
  assert.match(source, /clearMarkdownAssetCache\(reader\.path\)/);
});
