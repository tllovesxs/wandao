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

test('guide images are constrained to the tutorial panel width', () => {
  const imageRule = cssSource.match(/\.guide-content\s+img\.guide-image\s*\{[\s\S]*?\n\}/)?.[0] || '';
  assert.match(imageRule, /max-width:\s*100%/);
  assert.match(imageRule, /height:\s*auto/);
});
const tutorialRoot = path.join(repoRoot, 'plugins', 'feishu', 'providers', 'feishu-import');
const tutorialPath = path.join(tutorialRoot, 'README.md');

test('Feishu import tutorial bundles all referenced screenshots', () => {
  const markdown = fs.readFileSync(tutorialPath, 'utf8');
  assert.match(markdown, /^# 飞书文档导入教程/m);
  assert.match(markdown, /^## 一、准备工作/m);
  assert.match(markdown, /^## 二、正式导出/m);
  assert.match(markdown, /^## 提示/m);
  const imageReferences = Array.from(markdown.matchAll(/!\[[^\]]*\]\((\.\/images\/(\d+)\.png)\)/g));
  assert.equal(imageReferences.length, 21);
  assert.deepEqual(
    [...new Set(imageReferences.map((match) => Number(match[2])))].sort((left, right) => left - right),
    Array.from({ length: 20 }, (_, index) => index + 1)
  );
  imageReferences.forEach((match) => {
    assert.equal(fs.existsSync(path.join(tutorialRoot, match[1])), true, `missing ${match[1]}`);
  });
});

test('Feishu import providers append their bundled guide after rendering the form', () => {
  const feishuImportBranch = sourceBetween("  if (currentTool === 'feishu-import'", "  } else if (config.type === 'guide'");
  assert.match(feishuImportBranch, /appendProviderGuideSection\(contentArea, config\);/);
});
