const test = require('node:test');
const assert = require('node:assert/strict');
const { extractSensitiveArguments } = require('../wandao_electron/command_security');

test('secrets are removed from process arguments and moved to ephemeral env', () => {
  const result = extractSensitiveArguments([
    '--app-id', 'cli_x', '--app-secret', 'secret-a', '--api-key', 'secret-b', '--yes'
  ]);
  assert.deepEqual(result.commandArgs, ['--app-id', 'cli_x', '--yes']);
  assert.deepEqual(result.secretEnvironment, {
    FEISHU_APP_SECRET: 'secret-a',
    IMA_API_KEY: 'secret-b'
  });
  assert.equal(result.commandArgs.includes('secret-a'), false);
  assert.equal(result.commandArgs.includes('secret-b'), false);
});
