const assert = require('node:assert/strict');
const fs = require('node:fs');
const { createRequire } = require('node:module');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

const repoRoot = path.resolve(__dirname, '..');
const desktopRequire = createRequire(path.join(repoRoot, 'wandao_electron', 'package.json'));
const markdownit = desktopRequire('markdown-it');
const { parseHTML } = desktopRequire('linkedom');
const appPath = path.join(repoRoot, 'wandao_electron', 'renderer', 'app.js');
const cssPath = path.join(repoRoot, 'wandao_electron', 'renderer', 'styles.css');
const indexPath = path.join(repoRoot, 'wandao_electron', 'renderer', 'index.html');
const markdownBundlePath = path.join(repoRoot, 'wandao_electron', 'renderer', 'vendor', 'markdown-it.umd.min.js');
const appSource = fs.readFileSync(appPath, 'utf8');
const cssSource = fs.readFileSync(cssPath, 'utf8');

function sourceBetween(start, end) {
  const startIndex = appSource.indexOf(start);
  const endIndex = appSource.indexOf(end, startIndex);
  assert.notEqual(startIndex, -1, `missing source marker: ${start}`);
  assert.notEqual(endIndex, -1, `missing source marker: ${end}`);
  return appSource.slice(startIndex, endIndex);
}

const markdownSource = [
  sourceBetween('function markdownInline(value) {', '\nfunction valueAtPath('),
  sourceBetween('function escapeHtml(value) {', '\nfunction imaConfigPath('),
  'globalThis.__markdownToHtml = markdownToHtml;',
  'globalThis.__safeNoticeImageUrl = safeNoticeImageUrl;'
].join('\n');
const context = { URL, window: { markdownit } };
vm.runInNewContext(markdownSource, context);
const markdownToHtml = context.__markdownToHtml;

function createGuideRetryRuntime(readProviderGuideImage) {
  const { document, window } = parseHTML('<main id="guide"></main>');
  window.electronAPI = {
    readProviderGuideImage,
    openExternal: () => {}
  };
  const retryContext = { document, window };
  const retrySource = [
    'function safeRemoteGuideImageUrl(value) { return String(value || "").startsWith("https://") ? String(value) : ""; }',
    sourceBetween('async function requestGuideImage(providerId, imagePath) {', '\nasync function hydrateGuideImages('),
    'globalThis.__replaceWithGuideImageFallback = replaceWithGuideImageFallback;'
  ].join('\n');
  vm.runInNewContext(retrySource, retryContext);
  return {
    document,
    replaceWithGuideImageFallback: retryContext.__replaceWithGuideImageFallback
  };
}

async function flushAsyncClick() {
  await new Promise((resolve) => setImmediate(resolve));
}

test('guide markdown renders ordered steps as an ordered list', () => {
  assert.match(markdownToHtml('1. 第一步\n2. 第二步'), /^<ol>\n<li>第一步<\/li>\n<li>第二步<\/li>\n<\/ol>\n$/);
});

test('guide markdown preserves a step number after an intervening image', () => {
  const html = markdownToHtml('1. 第一步\n![截图](./images/1.png)\n2. 第二步');
  assert.match(html, /<ol>\n<li>第一步\n<img class="guide-image"/);
  assert.match(html, /<li>第二步<\/li>\n<\/ol>/);
});

test('guide markdown renders a local image placeholder without allowing raw HTML', () => {
  const html = markdownToHtml('![登录截图](./images/1.png)');
  assert.match(html, /<img/);
  assert.match(html, /class="guide-image"/);
  assert.match(html, /alt="登录截图"/);
  assert.match(html, /data-guide-image="\.\/images\/1\.png"/);
  assert.doesNotMatch(html, /src=/);

  const escaped = markdownToHtml('![<script>](./images/1.png&quot; onerror=&quot;alert(1))');
  assert.doesNotMatch(escaped, /<img/);
  assert.match(escaped, /&lt;script&gt;/);
  assert.doesNotMatch(escaped, /<[^>]+onerror=/i);
});

test('guide markdown supports tables, blockquotes, nested lists, inline code and safe links', () => {
  const html = markdownToHtml([
    '# 标题',
    '',
    '> 这是说明',
    '',
    '| 参数 | 说明 |',
    '| --- | --- |',
    '| `key` | **值** |',
    '',
    '- 一级',
    '  - 二级',
    '',
    '[官方文档](https://example.com/docs)'
  ].join('\n'));
  assert.match(html, /<h1>标题<\/h1>/);
  assert.match(html, /<blockquote>\n<p>这是说明<\/p>\n<\/blockquote>/);
  assert.match(html, /<table>/);
  assert.match(html, /<code>key<\/code>/);
  assert.match(html, /<strong>值<\/strong>/);
  assert.match(html, /<ul>\n<li>一级\n<ul>\n<li>二级<\/li>/);
  assert.match(html, /href="https:\/\/example\.com\/docs" data-external-link="true"/);
  assert.doesNotMatch(markdownToHtml('[不安全链接](http://example.com)'), /<a/);
  assert.doesNotMatch(markdownToHtml('[脚本链接](javascript:alert(1))'), /<a/);
  assert.doesNotMatch(html, /<script>/);
});

test('notice markdown resolves relative images to the safe GitHub docs scope', () => {
  const base = 'https://raw.githubusercontent.com/tllovesxs/wandao/main/docs/tutorials/fluxion-ai-getting-started.md';
  const html = markdownToHtml('![教程截图](../images/fluxion-ai-getting-started/01.png)', {
    resolveImageSource: (source) => new URL(source, base).href,
    allowRemoteImage: context.__safeNoticeImageUrl,
    remoteImageAttribute: 'data-notice-image'
  });
  assert.match(html, /data-notice-image="https:\/\/raw\.githubusercontent\.com\/tllovesxs\/wandao\/main\/docs\/images\/fluxion-ai-getting-started\/01\.png"/);

  const outside = markdownToHtml('![外部图片](https://example.com/image.png)', {
    allowRemoteImage: context.__safeNoticeImageUrl,
    remoteImageAttribute: 'data-notice-image'
  });
  assert.doesNotMatch(outside, /<img/);
});

test('guide markdown only accepts the pinned Wandao Feishu screenshot URLs', () => {
  const pinned = 'https://raw.githubusercontent.com/tllovesxs/wandao/82c027b054d9ece8449af30d79600814eb823e46/plugins/feishu/providers/feishu-import/images/20.png';
  const html = markdownToHtml(`![飞书截图](${pinned})`);
  assert.match(html, /data-guide-image="https:\/\/raw\.githubusercontent\.com/);

  const mutable = markdownToHtml('![截图](https://raw.githubusercontent.com/tllovesxs/wandao/main/plugins/feishu/providers/feishu-import/images/20.png)');
  const outside = markdownToHtml('![截图](https://raw.githubusercontent.com/other/wandao/82c027b054d9ece8449af30d79600814eb823e46/plugins/feishu/providers/feishu-import/images/20.png)');
  assert.doesNotMatch(mutable, /<img/);
  assert.doesNotMatch(outside, /<img/);
});

test('guide images are constrained to the tutorial panel width', () => {
  const imageRule = cssSource.match(/\.guide-content\s+img\.guide-image\s*\{[\s\S]*?\n\}/)?.[0] || '';
  assert.match(imageRule, /max-width:\s*100%/);
  assert.match(imageRule, /height:\s*auto/);
  assert.match(cssSource, /\.guide-image-fallback\s*\{/);
  assert.match(cssSource, /\.guide-image-retry\s*\{/);
  assert.match(cssSource, /\.guide-image-fallback-link\s*\{/);
});

test('desktop renderer bundles Markdown-it before the application code', () => {
  const indexHtml = fs.readFileSync(indexPath, 'utf8');
  const bundle = fs.readFileSync(markdownBundlePath, 'utf8');
  assert.ok(indexHtml.indexOf('<script src="vendor/markdown-it.umd.min.js"></script>') < indexHtml.indexOf('<script src="app.js"></script>'));
  assert.match(bundle, /markdownit/);
});
const tutorialRoot = path.join(repoRoot, 'plugins', 'feishu', 'providers', 'feishu-import');
const tutorialPath = path.join(tutorialRoot, 'README.md');
const remoteAssetRoot = path.join(repoRoot, 'docs', 'images', 'feishu-import');
const remotePrefix = 'https://raw.githubusercontent.com/tllovesxs/wandao/82c027b054d9ece8449af30d79600814eb823e46/plugins/feishu/providers/feishu-import/images/';

test('Feishu import tutorial pins all screenshots remotely and excludes them from the plugin', () => {
  const markdown = fs.readFileSync(tutorialPath, 'utf8');
  const provider = JSON.parse(fs.readFileSync(path.join(tutorialRoot, 'provider.json'), 'utf8'));
  const plugin = JSON.parse(fs.readFileSync(path.join(tutorialRoot, '..', '..', 'plugin.json'), 'utf8'));
  assert.match(markdown, /^# 飞书文档导入教程/m);
  assert.match(markdown, /^## 一、准备工作/m);
  assert.match(markdown, /^## 二、正式导入/m);
  assert.match(markdown, /^## 提示/m);
  assert.doesNotMatch(markdown, /进行导出了/);
  const imageReferences = Array.from(markdown.matchAll(/!\[[^\]]*\]\((https:\/\/[^)]+\/(\d+)\.png)\)/g));
  assert.equal(imageReferences.length, 21);
  assert.deepEqual(
    [...new Set(imageReferences.map((match) => Number(match[2])))].sort((left, right) => left - right),
    Array.from({ length: 20 }, (_, index) => index + 1)
  );
  imageReferences.forEach((match) => {
    assert.equal(match[1], `${remotePrefix}${match[2]}.png`);
  });
  assert.equal(fs.existsSync(path.join(tutorialRoot, 'images')), false);
  const assets = fs.readdirSync(remoteAssetRoot).filter((name) => name.endsWith('.png'));
  assert.equal(assets.length, 20);
  assert.equal(assets.reduce((total, name) => total + fs.statSync(path.join(remoteAssetRoot, name)).size, 0), 17317358);
  assert.deepEqual(new Set(Object.keys(provider.guideAssets)), new Set(imageReferences.map((match) => match[1])));
  assert.equal(Object.values(provider.guideAssets).every((asset) => asset.mime === 'image/png' && asset.bytes <= 3 * 1024 * 1024 && /^[a-f0-9]{64}$/.test(asset.sha256)), true);
  assert.equal(plugin.version, '1.0.11');
});

test('guide hydration limits remote IPC concurrency and renders an offline fallback', () => {
  const hydrateSource = sourceBetween('async function requestGuideImage(providerId, imagePath) {', '\nfunction bindCollapsibleGuideImages(');
  assert.match(hydrateSource, /Math\.min\(3, pending\.length\)/);
  assert.match(hydrateSource, /guide-image-fallback/);
  assert.match(hydrateSource, /guide-image-retry/);
  assert.match(hydrateSource, /重新加载这张教程图片/);
  assert.match(hydrateSource, /await requestGuideImage\(providerId, imagePath\)/);
  assert.match(hydrateSource, /retryButton\.disabled = false/);
  assert.match(hydrateSource, /在 GitHub 查看原图/);
  assert.match(hydrateSource, /new Map\(\)/);
});

test('guide image retry restores the failed image in place', async () => {
  let attempts = 0;
  const runtime = createGuideRetryRuntime(async () => {
    attempts += 1;
    return { success: true, dataUrl: 'data:image/png;base64,cG5n' };
  });
  const host = runtime.document.getElementById('guide');
  const image = runtime.document.createElement('img');
  image.className = 'guide-image';
  image.alt = '登录';
  host.appendChild(image);

  runtime.replaceWithGuideImageFallback(image, 'feishu-import', 'https://example.test/1.png', {
    success: false,
    result: { fallbackUrl: 'https://example.test/1.png' },
    errorMessage: 'offline'
  });
  const retry = host.querySelector('.guide-image-retry');
  assert.ok(retry);
  assert.equal(retry.getAttribute('aria-label'), '重新加载这张教程图片');

  retry.click();
  await flushAsyncClick();

  assert.equal(attempts, 1);
  assert.equal(host.querySelector('.guide-image-fallback'), null);
  assert.equal(host.querySelector('img.guide-image')?.getAttribute('src'), 'data:image/png;base64,cG5n');
});

test('guide image retry remains available after another network failure', async () => {
  const runtime = createGuideRetryRuntime(async () => ({
    success: false,
    error: 'still offline',
    fallbackUrl: 'https://example.test/1.png'
  }));
  const host = runtime.document.getElementById('guide');
  const image = runtime.document.createElement('img');
  image.className = 'guide-image';
  host.appendChild(image);

  runtime.replaceWithGuideImageFallback(image, 'feishu-import', 'https://example.test/1.png', {
    success: false,
    result: { fallbackUrl: 'https://example.test/1.png' },
    errorMessage: 'offline'
  });
  const retry = host.querySelector('.guide-image-retry');
  retry.click();
  await flushAsyncClick();

  assert.equal(host.querySelector('.guide-image-fallback')?.title, 'still offline');
  assert.equal(retry.disabled, false);
  assert.equal(retry.classList.contains('is-loading'), false);
});

test('Feishu import providers append their bundled guide after rendering the form', () => {
  const feishuImportBranch = sourceBetween("  if (currentTool === 'feishu-import'", "  } else if (config.type === 'guide'");
  assert.match(feishuImportBranch, /appendProviderGuideSection\(contentArea, config\);/);
});
