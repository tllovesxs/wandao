const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

const repoRoot = path.resolve(__dirname, '..');
const appPath = path.join(repoRoot, 'wandao_electron', 'renderer', 'app.js');
const cssPath = path.join(repoRoot, 'wandao_electron', 'renderer', 'styles.css');
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
  'globalThis.__markdownToHtml = markdownToHtml;'
].join('\n');
const context = {};
vm.runInNewContext(markdownSource, context);
const markdownToHtml = context.__markdownToHtml;

test('guide markdown renders ordered steps as an ordered list', () => {
  assert.equal(markdownToHtml('1. 第一步\n2. 第二步'), '<ol>\n<li>第一步</li>\n<li>第二步</li>\n</ol>');
});

test('guide markdown preserves a step number after an intervening image', () => {
  const html = markdownToHtml('1. 第一步\n![截图](./images/1.png)\n2. 第二步');
  assert.match(html, /<ol>\n<li>第一步<\/li>\n<\/ol>/);
  assert.match(html, /<ol start=\"2\">\n<li>第二步<\/li>\n<\/ol>/);
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
  assert.match(cssSource, /\.guide-image-fallback-link\s*\{/);
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
  assert.equal(plugin.version, '1.0.7');
});

test('guide hydration limits remote IPC concurrency and renders an offline fallback', () => {
  const hydrateSource = sourceBetween('async function hydrateGuideImages(container, providerId) {', '\nfunction bindCollapsibleGuideImages(');
  assert.match(hydrateSource, /Math\.min\(3, pending\.length\)/);
  assert.match(hydrateSource, /guide-image-fallback/);
  assert.match(hydrateSource, /在 GitHub 查看原图/);
  assert.match(hydrateSource, /new Map\(\)/);
});

test('Feishu import providers append their bundled guide after rendering the form', () => {
  const feishuImportBranch = sourceBetween("  if (currentTool === 'feishu-import'", "  } else if (config.type === 'guide'");
  assert.match(feishuImportBranch, /appendProviderGuideSection\(contentArea, config\);/);
});
