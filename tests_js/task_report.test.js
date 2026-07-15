const assert = require('node:assert/strict');
const test = require('node:test');
const {
  collectFailureDiagnostics,
  deriveTaskStatus,
  normalizeTaskReport,
  summarizeStats,
  taskDocumentFailureCount,
  taskFailureCount,
  taskResourceFailureCount,
  taskStatusText
} = require('../wandao_electron/renderer/task_report');

const resourceFailureReport = {
  exportedDocs: 1,
  failureCount: 0,
  imageFailureCount: 1,
  attachmentFailureCount: 1,
  resourceFailures: [
    {
      document: 'doc.md',
      failures: [
        { kind: 'image', url: 'https://cdn.example.test/image.png', error: 'HTTP 500' },
        { kind: 'attachment', url: 'https://files.example.test/guide.pdf', error: 'HTTP 403' }
      ]
    }
  ]
};

test('resource download warnings stay separate from document export failures', () => {
  const report = normalizeTaskReport(resourceFailureReport);
  const summary = summarizeStats(report.stats);
  const diagnostics = collectFailureDiagnostics(resourceFailureReport);

  assert.equal(report.stats.failed, 0);
  assert.equal(report.stats.imageFailed, 1);
  assert.equal(report.stats.attachmentFailed, 1);
  assert.equal(report.stats.resourceFailed, 2);
  assert.equal(taskDocumentFailureCount({ status: 'completed', report }), 0);
  assert.equal(taskResourceFailureCount({ status: 'completed', report }), 2);
  assert.equal(taskFailureCount({ status: 'completed', report }), 2);
  assert.match(summary, /\u56fe\u7247\u5931\u8d25 1/);
  assert.match(summary, /\u9644\u4ef6\u5931\u8d25 1/);
  assert.match(summary, /\u8d44\u6e90\u5931\u8d25 2/);
  assert.ok(diagnostics.some((line) => line.includes('guide.pdf') && line.includes('HTTP 403')));
  assert.equal(deriveTaskStatus({ status: 'completed', report }), 'partial');
  assert.equal(taskStatusText({ status: 'completed', report }), '\u90e8\u5206\u5b8c\u6210');
});

test('attachment-only warning has an explicit fallback diagnostic', () => {
  const diagnostics = collectFailureDiagnostics({
    exportedDocs: 1,
    failureCount: 0,
    attachmentFailureCount: 1
  });

  assert.ok(diagnostics.some((line) => line.includes('attachmentFailureCount=1')));
});

test('task status preserves partial work from non-zero process exits', () => {
  const report = normalizeTaskReport(resourceFailureReport);

  assert.equal(deriveTaskStatus({ status: 'completed', report }), 'partial');
  assert.equal(deriveTaskStatus({ status: 'completed', report }, { result: { success: false, code: 1 } }), 'partial');
  assert.equal(
    deriveTaskStatus(
      { status: 'completed', report: normalizeTaskReport({ totalDocs: 1, failureCount: 1 }) },
      { result: { success: false, code: 1 } }
    ),
    'failed'
  );
  assert.equal(deriveTaskStatus({ status: 'completed', report }, { result: { success: false, code: 130 } }), 'stopped');
  assert.equal(deriveTaskStatus({ status: 'stopping', report }), 'stopping');
});

test('document failures and resource warnings stay distinct for retry decisions', () => {
  const report = normalizeTaskReport({
    totalDocs: 3,
    failureCount: 1,
    failures: [{ relativePath: 'broken.md', error: 'HTTP 500' }],
    imageFailureCount: 2
  });
  const task = { status: 'completed', report };

  assert.equal(taskDocumentFailureCount(task), 1);
  assert.equal(taskResourceFailureCount(task), 2);
  assert.equal(taskFailureCount(task), 3);
  assert.equal(deriveTaskStatus(task), 'partial');
});
