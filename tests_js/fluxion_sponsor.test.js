const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');
const appSource = fs.readFileSync(path.join(repoRoot, 'wandao_electron', 'renderer', 'app.js'), 'utf8');
const stylesSource = fs.readFileSync(path.join(repoRoot, 'wandao_electron', 'renderer', 'styles.css'), 'utf8');

function sourceBetween(start, end, source = appSource) {
  const startIndex = source.indexOf(start);
  const endIndex = source.indexOf(end, startIndex);
  assert.notEqual(startIndex, -1, `missing source marker: ${start}`);
  assert.notEqual(endIndex, -1, `missing source marker: ${end}`);
  return source.slice(startIndex, endIndex);
}

test('export sponsor logs only appear for fully completed export actions', () => {
  const logs = [];
  const context = {
    FLUXION_REGISTER_URL: 'https://fluxionai.space/register?source=github&campaign=wandao',
    FLUXION_REDEEM_MESSAGE: '兑换码：WANNENGDAO — 登录后在工作台「兑换」输入，即可获得 $3 API 额度。',
    appendUserLog: (message, type, presentation) => logs.push({ message, type, presentation })
  };
  vm.runInNewContext([
    sourceBetween('function isExportAction(action) {', '\nfunction compactLogSummary('),
    'globalThis.__append = appendExportSuccessSponsorLogs;'
  ].join('\n'), context);

  for (const outcome of ['partial', 'paused', 'stopped', 'failed']) {
    context.__append(outcome, '导出');
  }
  for (const action of ['导入', '登录', '读取目录', 'upload']) {
    context.__append('completed', action);
  }
  assert.equal(logs.length, 0);

  context.__append('completed', '导出');
  assert.deepEqual(logs.map((entry) => entry.presentation), ['fluxion-register', 'fluxion-redeem']);
  logs.length = 0;
  context.__append('completed', { kind: 'export', actionName: '执行导出' });
  assert.equal(logs.length, 2);
});

test('all export completion entry points insert sponsor logs before structured details', () => {
  const resume = sourceBetween('async function resumeTask(task) {', '\nfunction latestResumableTask(');
  const manifest = sourceBetween('function initializeManifestProviderHandlers(provider, actions, fields) {', '\nfunction sandboxPluginHtml(');
  const regular = sourceBetween('async function handleExport(toolId) {', '\n// Handle stop');

  for (const [source, completion] of [
    [resume, '历史任务继续执行完成'],
    [manifest, '完成：${action.label || provider.title}'],
    [regular, '${actionName}完成']
  ]) {
    const completionIndex = source.indexOf(completion);
    const sponsorIndex = source.indexOf('appendExportSuccessSponsorLogs', completionIndex);
    const detailIndex = source.indexOf('JSON.stringify(result.data', sponsorIndex);
    assert.notEqual(completionIndex, -1);
    assert.ok(sponsorIndex > completionIndex);
    assert.ok(detailIndex > sponsorIndex);
  }
});

test('sponsor content is always rendered below the notice layout with safe rich log nodes', () => {
  const noticePage = sourceBetween('function renderNoticeCenterPage() {', '\nfunction renderProviderModeSwitcher(');
  assert.ok(noticePage.indexOf('</section>\n    ${renderFluxionSponsor()}') > noticePage.indexOf('class="notice-layout"'));
  assert.match(appSource, /<details class="notice-sponsor" open>/);
  assert.match(appSource, /Fluxion AI · 为 AI 辅助学习提供支持/);
  assert.match(appSource, /data-notice-image=/);
  assert.match(appSource, /presentation === 'fluxion-register'/);
  assert.match(appSource, /presentation === 'fluxion-redeem'/);
  assert.match(stylesSource, /\.notice-sponsor\s*\{/);
  assert.match(stylesSource, /\.log-entry \.log-external-link\s*\{/);
});

test('sponsor logs stay out of copied developer error reports', () => {
  const copyStart = appSource.indexOf('async function copyDeveloperReport');
  const copyEnd = appSource.indexOf('function taskHistoryPath', copyStart);
  assert.notEqual(copyStart, -1);
  assert.notEqual(copyEnd, -1);
  const copyReport = appSource.slice(copyStart, copyEnd);
  assert.match(appSource, /function isSponsorLogEntry\(entry\)/);
  assert.match(copyReport, /userLogEntries\s*\.filter\(\(entry\) => !isSponsorLogEntry\(entry\)\)/);
  assert.doesNotMatch(copyReport, /userLogEntries\.map\(/);
});
