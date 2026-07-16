const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');

const appJs = fs.readFileSync('wandao_electron/renderer/app.js', 'utf8');
const indexHtml = fs.readFileSync('wandao_electron/renderer/index.html', 'utf8');

function sourceBetween(startMarker, endMarker) {
  const start = appJs.indexOf(startMarker);
  const end = appJs.indexOf(endMarker, start + startMarker.length);
  assert.notEqual(start, -1, `missing start marker: ${startMarker}`);
  assert.notEqual(end, -1, `missing end marker: ${endMarker}`);
  return appJs.slice(start, end);
}

test('WPS result card is not a global page section', () => {
  assert.doesNotMatch(indexHtml, /id="task-result-card"/);
  assert.doesNotMatch(indexHtml, /id="wps-task-result-card"/);
});

test('manifest provider form owns the result card only for WPS', () => {
  const manifestSource = sourceBetween(
    'function renderManifestProviderForm',
    'function manifestFieldValue'
  );
  assert.match(manifestSource, /provider\.id === 'wps-export'/);
  assert.match(manifestSource, /id="wps-task-result-card"/);
  assert.match(manifestSource, /wps-task-result-card/);
});

test('WPS result renderer is provider-scoped and excludes global navigation actions', () => {
  const resultCardSource = sourceBetween(
    'function renderTaskResultCard',
    'async function handleTaskAction'
  );
  assert.match(resultCardSource, /currentTool !== 'wps-export'/);
  assert.match(resultCardSource, /task\.providerId !== 'wps-export'/);
  assert.match(resultCardSource, /document\.getElementById\('wps-task-result-card'\)/);
  assert.doesNotMatch(resultCardSource, /data-task-result-action="task-center"/);
  assert.doesNotMatch(resultCardSource, /data-task-result-action="open-report"/);
});

test('WPS unsuccessful result and failure list are expandable and open by default', () => {
  const resultCardSource = sourceBetween(
    'function renderTaskResultCard',
    'async function handleTaskAction'
  );
  assert.match(resultCardSource, /const taskResultExpanded = status !== 'completed';/);
  assert.match(resultCardSource, /<details class="task-result-disclosure"\$\{taskResultExpanded \? ' open' : ''\}>/);
  assert.match(resultCardSource, /<details class="advanced-section wps-progress-failures"[^>]*\$\{failureCount > 0 \? ' open' : ''\}[^>]*>/);
  assert.match(resultCardSource, /taskFailurePreview\(task, 100\)/);
});

test('WPS login confirmation follows login without reordering other providers', () => {
  const manifestSource = sourceBetween(
    'function renderManifestProviderForm',
    'function manifestFieldValue'
  );
  assert.match(manifestSource, /const loginDoneButton = actions\.some\(\(action\) => action\.kind === 'login'\)/);
  assert.match(manifestSource, /provider\.id === 'wps-export' && action\.kind === 'login' \? loginDoneButton : ''/);
  assert.match(manifestSource, /provider\.id !== 'wps-export' \? loginDoneButton : ''/);
});
