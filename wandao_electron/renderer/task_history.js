(function (root) {
  function asText(value) {
    if (value === undefined || value === null) return '';
    if (typeof value === 'string') return value;
    if (typeof value === 'number' || typeof value === 'boolean') return String(value);
    return '';
  }

  function normalizeText(value) {
    return asText(value)
      .normalize('NFKC')
      .toLocaleLowerCase()
      .replace(/\s+/g, ' ')
      .trim();
  }

  function historySearchText(task = {}) {
    return normalizeText([
      task.title,
      task.providerTitle,
      task.providerId,
      task.action,
      task.error
    ].map(asText).filter(Boolean).join(' '));
  }

  function taskTimestamp(task = {}) {
    for (const value of [task.startedAt, task.finishedAt, task.createdAt]) {
      const timestamp = Date.parse(value || '');
      if (Number.isFinite(timestamp)) return timestamp;
    }
    return 0;
  }

  function taskStatus(task, getStatus) {
    const status = typeof getStatus === 'function' ? getStatus(task) : task?.status;
    return normalizeText(status || 'failed');
  }

  function normalizeFilters(filters = {}) {
    return {
      query: normalizeText(filters.query),
      status: normalizeText(filters.status || 'all') || 'all',
      providerId: normalizeText(filters.providerId || 'all') || 'all'
    };
  }

  function matchesTask(task, filters = {}, getStatus) {
    const normalized = normalizeFilters(filters);
    if (normalized.status !== 'all' && taskStatus(task, getStatus) !== normalized.status) return false;
    if (normalized.providerId !== 'all' && normalizeText(task?.providerId) !== normalized.providerId) return false;
    if (!normalized.query) return true;
    const text = historySearchText(task);
    return normalized.query.split(' ').every((term) => text.includes(term));
  }

  function filterAndSortTasks(tasks, filters = {}, options = {}) {
    const source = Array.isArray(tasks) ? tasks : [];
    const getStatus = options.getStatus;
    return source
      .map((task, index) => ({ task, index }))
      .filter(({ task }) => matchesTask(task, filters, getStatus))
      .sort((left, right) => {
        const timeDifference = taskTimestamp(right.task) - taskTimestamp(left.task);
        return timeDifference || left.index - right.index;
      })
      .map(({ task }) => task);
  }

  function selectVisibleTasks(tasks, filters = {}, options = {}) {
    const limit = Math.max(1, Number(options.limit) || 20);
    const matched = filterAndSortTasks(tasks, filters, options);
    return {
      tasks: matched.slice(0, limit),
      total: matched.length,
      hasMore: matched.length > limit
    };
  }

  const api = {
    normalizeText,
    historySearchText,
    taskTimestamp,
    normalizeFilters,
    matchesTask,
    filterAndSortTasks,
    selectVisibleTasks
  };

  root.WandaoTaskHistory = api;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
})(typeof window !== 'undefined' ? window : globalThis);
