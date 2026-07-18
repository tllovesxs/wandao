(function (root) {
  function isInterruptedTask(task) {
    const status = String(task?.status || '').toLowerCase();
    return status === 'stopped' || status === 'interrupted';
  }

  function hasDeferredDocuments(task) {
    const report = task?.report || task?.resultData || {};
    return Array.isArray(report?.deferred) && report.deferred.length > 0;
  }

  function isCursorContinuation(task, provider = {}) {
    const checkpoint = provider?.checkpoint || {};
    const args = Array.isArray(task?.args) ? task.args : [];
    return Boolean(
      checkpoint.supported
      && checkpoint.strategy === 'cursor'
      && args.includes('--resume')
      && !args.includes(provider?.retryFailures?.arg || '--retry-failed')
    );
  }

  function buildResumeArgs(task, retryArg, failureCount = 0, provider = {}) {
    const args = Array.isArray(task?.args) ? [...task.args] : [];
    if (isInterruptedTask(task) || hasDeferredDocuments(task) || isCursorContinuation(task, provider)) {
      return args.filter((arg) => arg !== retryArg);
    }
    if (!retryArg || !Number.isFinite(Number(failureCount)) || Number(failureCount) <= 0) return args;
    if (!args.includes(retryArg)) args.push(retryArg);
    return args;
  }

  function shouldRetryFailureItems(task, retryArg, failureCount = 0, provider = {}) {
    return Boolean(
      retryArg
      && !isInterruptedTask(task)
      && !hasDeferredDocuments(task)
      && !isCursorContinuation(task, provider)
      && Number(failureCount) > 0
    );
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
    hasDeferredDocuments,
    isInterruptedTask,
    isCursorContinuation,
    providerCheckpointArgs,
    providerCheckpointFile,
    shouldRetryFailureItems
  };
  root.WandaoTaskResume = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
