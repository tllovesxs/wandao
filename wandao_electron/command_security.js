const SENSITIVE_ARGUMENT_ENV = new Map([
  ['--app-secret', 'FEISHU_APP_SECRET'],
  ['--api-key', 'IMA_API_KEY']
]);

function extractSensitiveArguments(args) {
  const commandArgs = [];
  const secretEnvironment = {};
  for (let index = 0; index < (args || []).length; index += 1) {
    const value = String(args[index]);
    const envName = SENSITIVE_ARGUMENT_ENV.get(value);
    if (envName && index + 1 < args.length) {
      secretEnvironment[envName] = String(args[index + 1]);
      index += 1;
      continue;
    }
    commandArgs.push(value);
  }
  return { commandArgs, secretEnvironment };
}

module.exports = { SENSITIVE_ARGUMENT_ENV, extractSensitiveArguments };
