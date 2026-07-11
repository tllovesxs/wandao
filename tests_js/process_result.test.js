const test = require('node:test');
const assert = require('node:assert/strict');
const { parseProcessResult } = require('../wandao_electron/process_result');

test('TaskResult v1 is accepted', () => {
  const result = parseProcessResult('log line\n{"kind":"wandao.result","schemaVersion":1,"totalDocs":2}\n');
  assert.equal(result.ok, true);
  assert.equal(result.legacy, false);
  assert.equal(result.data.totalDocs, 2);
});

test('legacy JSON is explicitly adapted', () => {
  const result = parseProcessResult('{"ordered":[]}');
  assert.equal(result.ok, true);
  assert.equal(result.legacy, true);
  assert.equal(result.data.kind, 'wandao.legacy-result');
});

test('plain stdout can no longer become a successful output path', () => {
  const result = parseProcessResult('finished without a result');
  assert.equal(result.ok, false);
  assert.match(result.error, /没有输出合法的 JSON 结果/);
});

test('unknown TaskResult schema is rejected', () => {
  const result = parseProcessResult('{"kind":"wandao.result","schemaVersion":99}');
  assert.equal(result.ok, false);
  assert.match(result.error, /schemaVersion/);
});
