const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const {
  buildResumeArgs,
  isInterruptedTask,
  shouldRetryFailureItems
} = require('../wandao_electron/renderer/task_resume');

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

  assert.match(finishHistory, /result\?\.code === 130/);
  assert.match(finishHistory, /'stopped'/);
  assert.match(diagnostics, /result\?\.code === 130[\s\S]*return/);
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

  assert.match(handler, /else if \(result\.code === 130\) \{[\s\S]*?已停止[\s\S]*?finishProgress\(false, [\s\S]*?已停止[\s\S]*?\} else \{[\s\S]*?失败/);
});

test('generic export treats the cooperative stop exit code as stopped, not a resource failure', () => {
  const appJs = fs.readFileSync('wandao_electron/renderer/app.js', 'utf8');
  const start = appJs.indexOf('async function handleExport');
  const end = appJs.indexOf('// Handle stop', start);
  const handler = appJs.slice(start, end);

  assert.match(handler, /result\.code === 130[\s\S]*已停止[\s\S]*finishProgress/);
});

test('Yuque import preserves checkpoint arguments and displays code 130 as stopped', () => {
  const appJs = fs.readFileSync('wandao_electron/renderer/app.js', 'utf8');
  assert.match(appJs, /yuque-import\.sqlite/);
  assert.match(appJs, /result\.code === 130/);
  assert.match(appJs, /已停止，已完成项目会在下次继续时跳过/);
});
