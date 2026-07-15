const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const repoRoot = path.resolve(__dirname, '..');
const appSource = fs.readFileSync(path.join(repoRoot, 'wandao_electron', 'renderer', 'app.js'), 'utf8');
const htmlSource = fs.readFileSync(path.join(repoRoot, 'wandao_electron', 'renderer', 'index.html'), 'utf8');
const qualityCheckSource = fs.readFileSync(path.join(repoRoot, 'scripts', 'quality_check.py'), 'utf8');

function sourceBetween(start, end) {
  return appSource.slice(appSource.indexOf(start), appSource.indexOf(end, appSource.indexOf(start)));
}

test('Yuque single and batch imports require a source, target, and overwrite confirmation', () => {
  const confirmation = sourceBetween('function confirmYuqueImportWrite', 'function yuqueImportReportPath');
  const sharedConfirmation = sourceBetween('function confirmImportWrite', 'function confirmYuqueImportWrite');
  const handlers = sourceBetween('function initializeYuqueImportHandlers', 'function buildYinxiangImportArgs');
  const exportHandler = sourceBetween('async function handleExport', '// Handle stop');

  assert.match(sharedConfirmation, /`来源：\${source \|\| '未选择'}`/);
  assert.match(sharedConfirmation, /`目标：\${target \|\| '未选择'}`/);
  assert.match(confirmation, /platform: '语雀'/);
  assert.match(confirmation, /source: sourceDir/);
  assert.match(confirmation, /同名文档存在时会更新其内容/);
  assert.match(handlers, /const args = buildYuqueImportArgs\(\{ single: true \}\);[\s\S]*if \(!confirmYuqueImportWrite\(\{ single: true \}\)\) return;[\s\S]*runYuqueImportCommand\(args/);
  assert.match(exportHandler, /toolId === 'yuque-import' && !confirmYuqueImportWrite\(\)/);
  assert.doesNotMatch(handlers, /retryFailures: true\}\), '语雀重试失败文档'[\s\S]*confirmYuqueImportWrite/);
});

test('IMA, Yinxiang, and Feishu imports show their concrete source and destination before writing', () => {
  const confirmation = sourceBetween('function confirmImaImportWrite', 'function yuqueImportReportPath');
  const imaHandlers = sourceBetween('function initializeImaImportHandlers', 'function buildYuqueImportArgs');
  const yinxiangHandlers = sourceBetween('function initializeYinxiangImportHandlers', 'function createTocState');
  const feishuHandlers = sourceBetween('function initializeFeishuImportHandlers', '// Initialize the shell immediately');

  assert.match(confirmation, /platform: 'ima 知识库'[^]*target: `\${knowledgeBase/);
  assert.match(confirmation, /platform: '印象笔记'[^]*target: stack/);
  assert.match(confirmation, /platform: '飞书'[^]*target: targetUrl/);
  assert.match(imaHandlers, /const args = buildImaImportArgs\(\{ single: true \}\);[^]*confirmImaImportWrite/);
  assert.match(yinxiangHandlers, /const args = buildYinxiangImportArgs\(\{ single: true \}\);[^]*confirmYinxiangImportWrite/);
  assert.match(feishuHandlers, /const args = \[\.\.\.buildFeishuImportArgs\(\), '--api-import-one'[^]*confirmFeishuImportWrite/);
});

test('IMA blocks target-knowledge-base actions until a usable selection exists and offers an inline next step', () => {
  const stateUpdater = sourceBetween('function updateImaImportKnowledgeBaseState', 'async function readImaKnowledgeBases');
  const initializer = sourceBetween('function initializeImaImportHandlers', 'function buildYuqueImportArgs');
  const runner = sourceBetween('async function runImaImportCommand', 'function renderImaKnowledgeBaseOptions');

  assert.match(stateUpdater, /\['ima-import-list-folders', 'ima-import-one', 'ima-import-export'\]/);
  assert.match(stateUpdater, /button\.disabled = blocked/);
  assert.match(stateUpdater, /未读取到可写入的知识库/);
  assert.match(initializer, /ima-import-read-kbs-inline.*readImaKnowledgeBases/);
  assert.match(htmlSource, /id="ima-import-kb-guidance"[\s\S]*id="ima-import-read-kbs-inline"/);
  assert.match(htmlSource, /id="ima-import-one" disabled aria-disabled="true"/);
  assert.match(htmlSource, /id="ima-import-export" disabled aria-disabled="true"/);
  assert.match(runner, /updateImaImportKnowledgeBaseState\(\);[\s\S]*runProviderCommand\(provider\.script, args, \{[\s\S]*providerId: 'ima-import'/);
  assert.match(runner, /finally \{\s*updateImaImportKnowledgeBaseState\(\);/);
  assert.doesNotMatch(runner, /setRunning\(/);
  assert.match(appSource, /#content-area button, #content-area input, #content-area select/);
  assert.match(qualityCheckSource, /"tests_js\/import_write_guidance\.test\.js"/);
});
