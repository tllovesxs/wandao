const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');

const appJs = fs.readFileSync('wandao_electron/renderer/app.js', 'utf8');
const indexHtml = fs.readFileSync('wandao_electron/renderer/index.html', 'utf8');

test('leaving a completed provider page hides its finished progress card without changing the progress UI', () => {
  assert.match(appJs, /function hideProgress\(\)[\s\S]*els\.section\.hidden = true;[\s\S]*progressVisible = false;/);
  const switchSource = appJs.slice(appJs.indexOf('function switchTool'), appJs.indexOf('// Load tool template'));
  assert.match(switchSource, /if \(targetTool !== currentTool && !isRunning\) hideProgress\(\);/);
  assert.match(switchSource, /if \(isRunning && targetTool !== currentTool\)[\s\S]*return false;/);
});


test('WPS partial export failures remain collapsible in the latest task result', () => {
  const resultCardSource = appJs.slice(
    appJs.indexOf('function renderTaskResultCard'),
    appJs.indexOf('async function handleTaskAction')
  );
  assert.match(resultCardSource, /task\.providerId === 'wps-export'/);
  assert.match(resultCardSource, /<details class="advanced-section wps-progress-failures"[^>]*>/);
  assert.match(resultCardSource, /<summary>[^<]*\$\{failureCount\}[^<]*<\/summary>/);
  assert.match(resultCardSource, /taskFailurePreview\(task, 100\)/);
  assert.match(resultCardSource, /escapeHtml\(line\)/);
  assert.match(resultCardSource, /failurePreview\.map/);
});


test('latest task result is an independent outer section before provider content', () => {
  assert.match(
    indexHtml,
    /<\/section>\s*<section\s+class="task-result-card"\s+id="task-result-card"[\s\S]*?<\/section>\s*<div id="content-area"><\/div>/
  );
});

test('latest task result card can expand and keeps unsuccessful results open by default', () => {
  const resultCardSource = appJs.slice(
    appJs.indexOf('function renderTaskResultCard'),
    appJs.indexOf('async function handleTaskAction')
  );
  assert.match(resultCardSource, /const taskResultExpanded = status !== 'completed';/);
  assert.match(resultCardSource, /<details class="task-result-disclosure"\$\{taskResultExpanded \? ' open' : ''\}>/);
  assert.match(resultCardSource, /<summary class="task-result-header">/);
  assert.match(resultCardSource, /<div class="task-result-details">/);
  assert.match(resultCardSource, /task-result-toggle-collapsed[^>]*>\u5c55\u5f00\u8be6\u60c5/);
  assert.match(resultCardSource, /task-result-toggle-expanded[^>]*>\u6536\u8d77\u8be6\u60c5/);
});

test('WPS login confirmation is inserted after login without reordering other providers', () => {
  const manifestSource = appJs.slice(
    appJs.indexOf('function renderManifestProviderForm'),
    appJs.indexOf('function manifestFieldValue')
  );
  assert.match(manifestSource, /const loginDoneButton = actions\.some\(\(action\) => action\.kind === 'login'\)/);
  assert.match(manifestSource, /provider\.id === 'wps-export' && action\.kind === 'login' \? loginDoneButton : ''/);
  assert.match(manifestSource, /provider\.id !== 'wps-export' \? loginDoneButton : ''/);
});
