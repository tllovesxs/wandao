const STRUCTURED_LOG_PREFIX = '@@WANDAO_LOG@@';
const TASK_RESULT_KIND = 'wandao.result';
const TASK_RESULT_SCHEMA_VERSION = 1;

function parseLastJson(stdout) {
  const raw = String(stdout || '');
  const lines = raw.split(/\r?\n/).filter((line) => !line.startsWith(STRUCTURED_LOG_PREFIX));
  const trimmed = lines.join('\n').trim();
  if (!trimmed) return null;
  try {
    return JSON.parse(trimmed);
  } catch (_error) {
    // 回退扫描只认第 0 列的 `{` / `[`：结果 JSON 无论 pretty-print 还是 compact，
    // 顶层括号一定顶格；缩进行都是嵌套片段，逐行 slice+join+parse 会让整体退化成
    // O(n^2)（一万条报告实测阻塞主进程 23 秒）。
    for (let index = lines.length - 1; index >= 0; index -= 1) {
      const firstChar = lines[index].charCodeAt(0);
      if (firstChar !== 0x7b && firstChar !== 0x5b) continue; // '{' / '['
      try {
        return JSON.parse(lines.slice(index).join('\n').trim());
      } catch (_ignored) {
        // Another top-level JSON may start above this line; keep scanning.
      }
    }
    return null;
  }
}

function normalizeProcessResult(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return { ok: false, error: '任务结果必须是 JSON 对象。' };
  }
  if (value.kind === TASK_RESULT_KIND) {
    if (value.schemaVersion !== TASK_RESULT_SCHEMA_VERSION) {
      return { ok: false, error: `不支持的 TaskResult schemaVersion：${value.schemaVersion}` };
    }
    return { ok: true, data: value, legacy: false };
  }
  return {
    ok: true,
    legacy: true,
    data: { ...value, kind: 'wandao.legacy-result', schemaVersion: 0 }
  };
}

function parseProcessResult(stdout) {
  const parsed = parseLastJson(stdout);
  if (parsed === null) {
    return { ok: false, error: '任务进程已正常退出，但没有输出合法的 JSON 结果。' };
  }
  return normalizeProcessResult(parsed);
}

module.exports = {
  STRUCTURED_LOG_PREFIX,
  TASK_RESULT_KIND,
  TASK_RESULT_SCHEMA_VERSION,
  normalizeProcessResult,
  parseLastJson,
  parseProcessResult
};
