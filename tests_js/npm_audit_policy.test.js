const assert = require('node:assert/strict');
const test = require('node:test');
const {
  evaluateAudit,
  isAllowedVulnerability
} = require('../wandao_electron/scripts/npm_audit_policy');

function advisory(url, severity = 'high') {
  return { url, severity };
}

const allowedUrl = 'https://github.com/advisories/GHSA-mh99-v99m-4gvg';

test('the exact temporary brace-expansion advisory can flow through dependency chains', () => {
  const vulnerabilities = {
    'brace-expansion': { severity: 'high', via: [advisory(allowedUrl)] },
    minimatch: { severity: 'high', via: ['brace-expansion'] },
    'electron-builder': { severity: 'high', via: ['minimatch'] }
  };

  assert.equal(isAllowedVulnerability('electron-builder', vulnerabilities), true);
  assert.deepEqual(evaluateAudit({ vulnerabilities }).blocked, []);
});

test('an unrelated high advisory remains blocking', () => {
  const vulnerabilities = {
    'brace-expansion': { severity: 'high', via: [advisory(allowedUrl)] },
    'other-package': {
      severity: 'high',
      via: [advisory('https://github.com/advisories/GHSA-test-test-test')]
    }
  };
  const result = evaluateAudit({ vulnerabilities });

  assert.equal(result.passed, false);
  assert.deepEqual(result.ignored, ['brace-expansion']);
  assert.deepEqual(result.blocked, ['other-package (high)']);
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

test('cycles and malformed audit reports fail closed', () => {
  assert.equal(isAllowedVulnerability('a', {
    a: { severity: 'high', via: ['b'] },
    b: { severity: 'high', via: ['a'] }
  }), false);
  assert.equal(evaluateAudit(null).passed, false);
});
