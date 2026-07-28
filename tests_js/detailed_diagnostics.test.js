const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const { maskArgs } = require('../wandao_electron/renderer/task_report');

const appJs = fs.readFileSync('wandao_electron/renderer/app.js', 'utf8');

test('task diagnostics record a sanitized lifecycle context', () => {
  const start = appJs.indexOf('async function runTrackedPythonCommand');
  const end = appJs.indexOf('async function runProviderCommand', start);
  const commandRunner = appJs.slice(start, end);

  assert.match(commandRunner, /runtime\.command\.started/);
  assert.match(commandRunner, /runtime\.command\.finished/);
  assert.match(commandRunner, /runtime\.command\.exception/);
  assert.match(commandRunner, /args:\s*maskDiagnosticArgs\(commandArgs\)/);
  assert.match(commandRunner, /elapsedMs:\s*Date\.now\(\) - runtimeStartedAt/);
});

test('copied developer reports retain structured event data and redact it', () => {
  const start = appJs.indexOf('function stringifyDiagnosticData');
  const end = appJs.indexOf('function activeToolLabel', start);
  const formatter = appJs.slice(start, end);
  const copyStart = appJs.indexOf('async function copyDeveloperReport');
  const copyEnd = appJs.indexOf('function taskHistoryPath', copyStart);
  const copyReport = appJs.slice(copyStart, copyEnd);

  assert.match(formatter, /JSON\.stringify\(maskSensitiveValue\(value\)\)/);
  assert.match(formatter, /data=\$\{data\}/);
  assert.match(copyReport, /detailLogEntries\.map\(formatDeveloperDetailEntry\)/);
  assert.match(copyReport, /copyText\(maskSensitiveText\(report\)\)/);
});

test('diagnostic command arguments hide known secret flags', () => {
  assert.deepEqual(
    maskArgs(['--wiki-url', 'https://example.test/wiki', '--app-secret', 'secret-value', '--token', 'token-value']),
    ['--wiki-url', 'https://example.test/wiki', '--app-secret', '***', '--token', '***']
  );
});

test('renderer diagnostics capture unhandled errors and rejected promises', () => {
  const start = appJs.indexOf('function initializeRendererDiagnostics');
  const end = appJs.indexOf('function compactDiagnostic', start);
  const diagnostics = appJs.slice(start, end);

  assert.match(diagnostics, /addEventListener\('error'/);
  assert.match(diagnostics, /renderer\.unhandled-error/);
  assert.match(diagnostics, /addEventListener\('unhandledrejection'/);
  assert.match(diagnostics, /renderer\.unhandled-rejection/);
});
