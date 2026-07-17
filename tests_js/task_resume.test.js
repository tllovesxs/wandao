const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const {
  buildResumeArgs,
  isInterruptedTask,
  providerCheckpointArgs,
  providerCheckpointFile,
  shouldRetryFailureItems
} = require('../wandao_electron/renderer/task_resume');

test('manifest providers can derive checkpoint paths from a shared base field', () => {
  const provider = {
    id: 'xiliu-import',
    checkpoint: { supported: true, baseField: 'source_dir', fileName: 'xiliu-import.sqlite' }
  };
  assert.equal(
    providerCheckpointFile(provider, { source_dir: 'E:/notes/demo/' }),
    'E:/notes/demo/.wandao/xiliu-import.sqlite'
  );
  assert.equal(providerCheckpointFile(provider, { source_dir: '' }), '');
});

test('FlowUs import manifest uses a non-TOC target action and shared checkpoint contract', () => {
  const provider = require('../plugins/xiliu/providers/xiliu-import/provider.json');
  const targets = provider.actions.find((action) => action.id === 'targets');
  const sourceDir = provider.fields.find((field) => field.name === 'source_dir');
  const parentId = provider.fields.find((field) => field.name === 'parent_id');
  const spaceId = provider.fields.find((field) => field.name === 'space_id');

  assert.equal(provider.capabilities.scanToc, false);
  assert.equal(targets.kind, 'check');
  assert.ok(targets.args.includes('--scan-targets'));
  assert.equal(targets.includeSelection, false);
  assert.ok(!sourceDir.actions.includes('targets'));
  assert.equal(parentId.type, 'select');
  assert.equal(parentId.required, true);
  assert.equal(spaceId.type, 'select');
  assert.deepEqual(targets.updates.map((update) => [update.field, update.path]), [
    ['parent_id', 'targets'],
    ['space_id', 'spaces']
  ]);
  assert.equal(
    providerCheckpointFile(provider, { source_dir: 'E:/notes/demo/' }),
    'E:/notes/demo/.wandao/xiliu-import.sqlite'
  );
  assert.deepEqual(
    providerCheckpointArgs(provider, { source_dir: 'E:/notes/demo/' }),
    ['--checkpoint-file', 'E:/notes/demo/.wandao/xiliu-import.sqlite', '--resume']
  );
});

test('Yuque and Feishu import builders assign stable provider checkpoint task IDs', () => {
  const appJs = fs.readFileSync('wandao_electron/renderer/app.js', 'utf8');
  const yuqueBuilder = appJs.slice(appJs.indexOf('function buildYuqueImportArgs'), appJs.indexOf('function yuqueImportReportPath'));
  const feishuBuilder = appJs.slice(appJs.indexOf('function buildFeishuImportArgs'), appJs.indexOf('function setFeishuImportRunning'));

  assert.match(yuqueBuilder, /--checkpoint-file[\s\S]*yuque-import\.sqlite[\s\S]*--resume[\s\S]*--checkpoint-task-id[\s\S]*yuque-import/);
  assert.match(feishuBuilder, /--checkpoint-file[\s\S]*feishu-import\.sqlite[\s\S]*--resume[\s\S]*--checkpoint-task-id[\s\S]*feishu-import/);
});

test('cooperative stops neither produce error diagnostics nor failed history', () => {
  const appJs = fs.readFileSync('wandao_electron/renderer/app.js', 'utf8');
  const finishHistory = appJs.slice(appJs.indexOf('async function finishHistoryTask'), appJs.indexOf('async function runTrackedPythonCommand'));
  const diagnostics = appJs.slice(appJs.indexOf('function recordPythonResultDiagnostics'), appJs.indexOf('function clearDetailedLogs'));

  assert.match(appJs, /function isStoppedResult\(result\) \{[\s\S]*result\?\.code === 130[\s\S]*result\?\.data\?\.stopped === true/);
  assert.match(finishHistory, /const stopped = isStoppedResult\(result\) && !thrownError/);
  assert.match(finishHistory, /'stopped'/);
  assert.match(diagnostics, /isStoppedResult\(result\)[\s\S]*return/);
});

test('an explicit stopped payload wins over a successful process result', () => {
  const appJs = fs.readFileSync('wandao_electron/renderer/app.js', 'utf8');
  const finishHistory = appJs.slice(appJs.indexOf('async function finishHistoryTask'), appJs.indexOf('async function runTrackedPythonCommand'));
  const start = appJs.indexOf('async function handleExport');
  const end = appJs.indexOf('// Handle stop', start);
  const handler = appJs.slice(start, end);

  assert.match(finishHistory, /const stopped = isStoppedResult\(result\) && !thrownError/);
  assert.match(finishHistory, /const fallbackStatus = stopped \? 'stopped' : \(success \? 'completed' :/);
  assert.match(finishHistory, /WandaoTaskReport\?\.deriveTaskStatus/);
  assert.match(handler, /if \(isStoppedResult\(result\)\) \{[\s\S]*finishProgress\('stopped'/);
});

test('a manually stopped task keeps checkpoint resume mode instead of switching to retry-failed', () => {
  const task = { status: 'stopped', args: ['--checkpoint-file', 'out/.wandao/checkpoint.sqlite', '--resume'] };
  assert.deepEqual(buildResumeArgs(task, '--retry-failed', 1), task.args);
  assert.equal(shouldRetryFailureItems(task, '--retry-failed', 1), false);
});

test('an interrupted task removes a stale retry-failed argument before continuing', () => {
  const task = { status: 'interrupted', args: ['--resume', '--retry-failed'] };
  assert.deepEqual(buildResumeArgs(task, '--retry-failed', 1), ['--resume']);
  assert.equal(isInterruptedTask(task), true);
});

test('a stopped task removes a stale retry argument even when no failures were recorded', () => {
  const task = { status: 'stopped', args: ['--resume', '--retry-failed'] };
  assert.deepEqual(buildResumeArgs(task, '--retry-failed', 0), ['--resume']);
  assert.equal(shouldRetryFailureItems(task, '--retry-failed', 0), false);
});

test('a real failed task still retries only its failed items when supported', () => {
  const task = { status: 'failed', args: ['--resume'] };
  assert.deepEqual(buildResumeArgs(task, '--retry-failed', 2), ['--resume', '--retry-failed']);
  assert.equal(shouldRetryFailureItems(task, '--retry-failed', 2), true);
});

test('the renderer loads and uses the resume helper before app startup', () => {
  const indexHtml = fs.readFileSync('wandao_electron/renderer/index.html', 'utf8');
  const appJs = fs.readFileSync('wandao_electron/renderer/app.js', 'utf8');
  assert.ok(indexHtml.indexOf('task_resume.js') < indexHtml.indexOf('app.js'));
  assert.match(appJs, /WandaoTaskResume\?\.buildResumeArgs/);
  assert.match(appJs, /WandaoTaskResume\?\.shouldRetryFailureItems/);
});

test('resuming historical task treats code 130 as stopped without entering failure handling', () => {
  const appJs = fs.readFileSync('wandao_electron/renderer/app.js', 'utf8');
  const start = appJs.indexOf('async function resumeTask');
  const end = appJs.indexOf('function latestResumableTask', start);
  const handler = appJs.slice(start, end);

  assert.match(handler, /else if \(outcome === 'stopped'\) \{[\s\S]*?已停止[\s\S]*?finishProgress\('stopped', [\s\S]*?已停止[\s\S]*?\} else \{[\s\S]*?失败/);
});

test('generic export treats the cooperative stop exit code as stopped, not a resource failure', () => {
  const appJs = fs.readFileSync('wandao_electron/renderer/app.js', 'utf8');
  const start = appJs.indexOf('async function handleExport');
  const end = appJs.indexOf('// Handle stop', start);
  const handler = appJs.slice(start, end);

  assert.match(handler, /isStoppedResult\(result\)[\s\S]*已停止[\s\S]*finishProgress/);
});

test('Yuque import preserves checkpoint arguments and displays code 130 as stopped', () => {
  const appJs = fs.readFileSync('wandao_electron/renderer/app.js', 'utf8');
  assert.match(appJs, /yuque-import\.sqlite/);
  const start = appJs.indexOf('async function runYuqueImportCommand');
  const end = appJs.indexOf('async function handleYuqueImport', start);
  const handler = appJs.slice(start, end);
  assert.match(handler, /if \(isStoppedResult\(result\)\)/);
  assert.match(handler, /已停止，已完成项目会在下次继续时跳过/);
  assert.match(handler, /finishProgress\('stopped'/);
});

test('manifest-provider actions treat code 130 as stopped before the failure branch', () => {
  const appJs = fs.readFileSync('wandao_electron/renderer/app.js', 'utf8');
  const start = appJs.indexOf('actions.forEach((action) => {');
  const end = appJs.indexOf('function sandboxPluginHtml', start);
  const handler = appJs.slice(start, end);

  assert.match(handler, /if \(isStoppedResult\(result\)\) \{[\s\S]*finishProgress\('stopped',[\s\S]*\} else if \(result\.success\) \{/);
});

test('resource warnings are not converted into retry-failed document commands', () => {
  const appJs = fs.readFileSync('wandao_electron/renderer/app.js', 'utf8');
  const canResume = appJs.slice(appJs.indexOf('function canResumeTask'), appJs.indexOf('function resumeTaskDisabledReason'));
  const resumeArgs = appJs.slice(appJs.indexOf('function resumeTaskArgs'), appJs.indexOf('async function performTaskHistoryLoad'));
  const resumeTaskHandler = appJs.slice(appJs.indexOf('async function resumeTask'), appJs.indexOf('function latestResumableTask'));

  assert.match(canResume, /taskDocumentFailureCount\(task\)/);
  assert.doesNotMatch(canResume, /taskFailureCount\(task\)/);
  assert.match(resumeArgs, /taskDocumentFailureCount\(task\)/);
  assert.match(resumeTaskHandler, /const documentFailures = taskDocumentFailureCount\(task\)/);
  assert.match(resumeTaskHandler, /失败文档，共 \$\{documentFailures\} 个/);
});

test('latest task result card is persistent, actionable, and never overlays long forms', () => {
  const appJs = fs.readFileSync('wandao_electron/renderer/app.js', 'utf8');
  const indexHtml = fs.readFileSync('wandao_electron/renderer/index.html', 'utf8');
  const styles = fs.readFileSync('wandao_electron/renderer/styles.css', 'utf8');
  const finishHistory = appJs.slice(appJs.indexOf('async function finishHistoryTask'), appJs.indexOf('async function runTrackedPythonCommand'));

  assert.ok(indexHtml.indexOf('id="progress-section"') < indexHtml.indexOf('id="content-area"'));
  assert.match(indexHtml, /id="task-result-card"/);
  assert.match(appJs, /function renderTaskResultCard\(task = latestFinishedTask\(\)\)/);
  assert.match(appJs, /data-task-result-action="open-output"/);
  assert.match(appJs, /data-task-result-action="open-report"/);
  assert.match(appJs, /data-task-result-action="copy-failures"/);
  assert.match(appJs, /data-task-result-action="resume"/);
  assert.match(finishHistory, /latestFinishedTaskId = task\.id[\s\S]*renderTaskResultCard\(task\)/);
  assert.match(styles, /\.progress-section \{[\s\S]*position: static/);
  assert.match(styles, /\.action-section \{[\s\S]*position: static/);
  assert.match(styles, /\.provider-mode-switcher \{[\s\S]*position: static/);
});

test('manual stop remains stopping until command completion records its terminal state', () => {
  const appJs = fs.readFileSync('wandao_electron/renderer/app.js', 'utf8');
  const handler = appJs.slice(appJs.indexOf('async function handleStop'), appJs.indexOf('// Set running state'));

  assert.match(handler, /const task = activeHistoryTask/);
  assert.match(handler, /task\.status = 'stopping'/);
  assert.doesNotMatch(handler, /task\.status = 'stopped'/);
  assert.match(handler, /等待当前进程退出/);
});
