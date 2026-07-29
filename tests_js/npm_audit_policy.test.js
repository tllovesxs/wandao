const assert = require('node:assert/strict');
const test = require('node:test');
const { evaluateAudit } = require('../wandao_electron/scripts/npm_audit_policy');

function advisory(url, severity = 'high') {
  return { url, severity };
}

test('high-severity advisories and their reported dependency chains are blocking', () => {
  const vulnerabilities = {
    dependency: { severity: 'high', via: [advisory('https://example.test/high')] },
    'build-tool': { severity: 'high', via: ['dependency'] }
  };
  const result = evaluateAudit({ vulnerabilities });

  assert.equal(result.passed, false);
  assert.deepEqual(result.ignored, []);
  assert.deepEqual(result.blocked, ['dependency (high)', 'build-tool (high)']);
});

test('multiple unrelated high advisories all remain blocking', () => {
  const vulnerabilities = {
    'first-package': { severity: 'high', via: [advisory('https://example.test/first')] },
    'other-package': {
      severity: 'high',
      via: [advisory('https://example.test/second')]
    }
  };
  const result = evaluateAudit({ vulnerabilities });

  assert.equal(result.passed, false);
  assert.deepEqual(result.ignored, []);
  assert.deepEqual(result.blocked, ['first-package (high)', 'other-package (high)']);
});

test('moderate findings stay below the high severity gate', () => {
  const result = evaluateAudit({
    vulnerabilities: {
      moderate: { severity: 'moderate', via: [advisory('https://example.test/moderate', 'moderate')] }
    }
  });

  assert.equal(result.passed, true);
  assert.deepEqual(result.ignored, []);
  assert.deepEqual(result.blocked, []);
});

test('malformed audit reports fail closed', () => {
  assert.equal(evaluateAudit(null).passed, false);
});
