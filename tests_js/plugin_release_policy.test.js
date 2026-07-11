const test = require('node:test');
const assert = require('node:assert/strict');
const { channelFor, readPolicy, validatePolicy } = require('../scripts/plugin_release_policy');

test('release policy keeps new plugins experimental by default', () => {
  const policy = validatePolicy(readPolicy());
  assert.equal(policy.defaultChannel, 'experimental');
  assert.equal(channelFor(policy, 'future-community-plugin'), 'experimental');
  assert.equal(channelFor(policy, 'feishu'), 'stable');
});

test('stable plugins require an accountable approval record', () => {
  assert.throws(
    () => validatePolicy({ schemaVersion: 1, defaultChannel: 'experimental', plugins: { demo: { channel: 'stable' } } }),
    /批准人和批准日期/
  );
});

test('policy cannot silently promote an unknown channel', () => {
  assert.throws(
    () => validatePolicy({ schemaVersion: 1, defaultChannel: 'experimental', plugins: { demo: { channel: 'nightly' } } }),
    /channel 无效/
  );
});
