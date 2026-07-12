const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const {
  buildResumeArgs,
  isInterruptedTask,
  shouldRetryFailureItems
} = require('../wandao_electron/renderer/task_resume');

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

test('Yuque import preserves checkpoint arguments and displays code 130 as stopped', () => {
  const appJs = fs.readFileSync('wandao_electron/renderer/app.js', 'utf8');
  assert.match(appJs, /yuque-import\.sqlite/);
  assert.match(appJs, /result\.code === 130/);
  assert.match(appJs, /已停止，已完成项目会在下次继续时跳过/);
});
