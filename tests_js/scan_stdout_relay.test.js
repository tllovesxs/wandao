const test = require('node:test');
const assert = require('node:assert/strict');
const { createScanStdoutRelay } = require('../wandao_electron/scan_stdout_relay');

test('forwards scan progress but suppresses the terminal pretty JSON result', () => {
  const forwarded = [];
  const relay = createScanStdoutRelay((text) => forwarded.push(text));

  relay.push('Loaded 3 auth cookies\n');
  relay.push('Reading Yuque TOC\n{\n  "ordered": [\n');
  relay.push('    { "uuid": "a" }\n  ]\n}\n');
  relay.flush();

  assert.deepEqual(forwarded, ['Loaded 3 auth cookies\n', 'Reading Yuque TOC\n']);
});

test('suppresses a JSON result whose opening brace arrives in a later chunk', () => {
  const forwarded = [];
  const relay = createScanStdoutRelay((text) => forwarded.push(text));

  relay.push('Reading TOC');
  relay.push('\n{\n  "ordered": []\n}\n');
  relay.flush();

  assert.deepEqual(forwarded, ['Reading TOC\n']);
});

test('flushes a non-result trailing log fragment', () => {
  const forwarded = [];
  const relay = createScanStdoutRelay((text) => forwarded.push(text));

  relay.push('Waiting for remote directory');
  relay.flush();

  assert.deepEqual(forwarded, ['Waiting for remote directory']);
});

test('does not hide JSON-looking logs that are followed by normal diagnostics', () => {
  const forwarded = [];
  const relay = createScanStdoutRelay((text) => forwarded.push(text));

  relay.push('{"event":"scan-progress"}\n');
  relay.push('Directory scan failed: retry later\n');
  relay.flush();

  assert.deepEqual(forwarded, ['{"event":"scan-progress"}\nDirectory scan failed: retry later\n']);
});
