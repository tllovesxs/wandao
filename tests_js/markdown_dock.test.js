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
  assert.match(source, /cacheMarkdownImage\(value\)/);
  assert.match(source, /resolveMarkdownImage\(markdownPath, source\)/);
  assert.match(source, /is-images-loading/);
  assert.match(source, /正在加载文档图片/);
});

test('live Markdown reader waits for export completion before showing a document', () => {
  assert.match(source, /LIVE_READER_IDLE_TIMEOUT_MS = 120000/);
  assert.match(source, /LIVE_READER_HARD_TIMEOUT_MS = 1800000/);
  assert.match(source, /pendingContent/);
  assert.match(source, /state\.reader\.status = 'loading'/);
  assert.match(source, /refreshLiveReader\(true,/);
  assert.match(source, /导出中的正文、图片和附件尚未完成/);
  assert.match(source, /导出内容长时间没有完成/);
  assert.match(source, /state\.liveExport\.mode === 'running'/);
});
