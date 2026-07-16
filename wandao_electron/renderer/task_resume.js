(function (root) {
  function isInterruptedTask(task) {
    const status = String(task?.status || '').toLowerCase();
    return status === 'stopped' || status === 'interrupted';
  }

  function buildResumeArgs(task, retryArg, failureCount = 0) {
    const args = Array.isArray(task?.args) ? [...task.args] : [];
    if (isInterruptedTask(task)) return args.filter((arg) => arg !== retryArg);
    if (!retryArg || !Number.isFinite(Number(failureCount)) || Number(failureCount) <= 0) return args;
    if (!args.includes(retryArg)) args.push(retryArg);
    return args;
  }

  function shouldRetryFailureItems(task, retryArg, failureCount = 0) {
    return Boolean(retryArg && !isInterruptedTask(task) && Number(failureCount) > 0);
  }

  function providerCheckpointFile(provider, values = {}) {
    if (!provider?.checkpoint?.supported) return '';
    const configuredField = String(provider.checkpoint.baseField || '').trim();
    const candidateFields = configuredField ? [configuredField] : ['output', 'output_dir', 'output-dir'];
    const rootValue = candidateFields.map((name) => values[name]).find((value) => String(value || '').trim());
    if (!rootValue) return '';
    const rootPath = String(rootValue).trim().replace(/[\\\/]+$/, '');
    const fileName = String(provider.checkpoint.fileName || 'checkpoint.sqlite').trim() || 'checkpoint.sqlite';
    return `${rootPath}/.wandao/${fileName}`;
  }

  function providerCheckpointArgs(provider, values = {}) {
    const checkpointFile = providerCheckpointFile(provider, values);
    return checkpointFile ? ['--checkpoint-file', checkpointFile, '--resume'] : [];
  }

  const api = {
    buildResumeArgs,
    isInterruptedTask,
    providerCheckpointArgs,
    providerCheckpointFile,
    shouldRetryFailureItems
  };
  root.WandaoTaskResume = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
