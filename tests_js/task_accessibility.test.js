const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');

const appJs = fs.readFileSync('wandao_electron/renderer/app.js', 'utf8');
const indexHtml = fs.readFileSync('wandao_electron/renderer/index.html', 'utf8');
const styles = fs.readFileSync('wandao_electron/renderer/styles.css', 'utf8');

test('task progress and terminal outcomes are announced atomically to assistive technology', () => {
  assert.match(indexHtml, /id="progress-detail" role="status" aria-live="polite" aria-atomic="true"/);
  assert.match(indexHtml, /id="task-announcements" role="status" aria-live="polite" aria-atomic="true"/);
  assert.match(appJs, /function announceTaskOutcome\(task\)[\s\S]*announcer\.textContent/);
  const finishHistory = appJs.slice(appJs.indexOf('async function finishHistoryTask'), appJs.indexOf('async function runTrackedPythonCommand'));
  assert.match(finishHistory, /renderTaskResultCard\(task\);[\s\S]*announceTaskOutcome\(task\);/);
  assert.match(appJs, /els\.section\.setAttribute\('aria-busy', 'true'\)/);
  assert.match(appJs, /els\.section\.setAttribute\('aria-busy', 'false'\)/);
});

test('terminal WPS task result receives a provider-scoped keyboard focus target and describes failure actions', () => {
  assert.doesNotMatch(indexHtml, /id="task-result-card"/);
  assert.match(appJs, /provider\.id === 'wps-export'[\s\S]*id="wps-task-result-card" aria-labelledby="wps-task-result-title" tabindex="-1"/);
  assert.match(appJs, /function focusTaskResultCard\(\)[\s\S]*getElementById\('wps-task-result-card'\)[\s\S]*card\.focus\(\{ preventScroll: false \}\)/);
  const finishHistory = appJs.slice(appJs.indexOf('async function finishHistoryTask'), appJs.indexOf('async function runTrackedPythonCommand'));
  assert.match(finishHistory, /announceTaskOutcome\(task\);[\s\S]*focusTaskResultCard\(\);/);
  assert.match(appJs, /wps-task-result-failures-title/);
  assert.match(appJs, /data-task-result-action="copy-failures" aria-describedby="wps-task-result-failures-title"/);
  assert.match(appJs, /data-history-action="copy-failures" aria-label="[^"]+"/);
  assert.match(styles, /\.task-result-card:focus-visible/);
  assert.match(styles, /button:focus-visible,[\s\S]*\[tabindex\]:focus-visible/);
});
