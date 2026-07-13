const assert = require('node:assert/strict');
const test = require('node:test');
const {
  collectFailureDiagnostics,
  normalizeTaskReport,
  summarizeStats,
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
  assert.match(summary, /\u56fe\u7247\u5931\u8d25 1/);
  assert.match(summary, /\u9644\u4ef6\u5931\u8d25 1/);
  assert.match(summary, /\u8d44\u6e90\u5931\u8d25 2/);
  assert.ok(diagnostics.some((line) => line.includes('guide.pdf') && line.includes('HTTP 403')));
  assert.equal(taskStatusText({ status: 'completed', report }), '\u5df2\u5b8c\u6210\uff08\u6709\u8d44\u6e90\u8b66\u544a\uff09');
});

test('attachment-only warning has an explicit fallback diagnostic', () => {
  const diagnostics = collectFailureDiagnostics({
    exportedDocs: 1,
    failureCount: 0,
    attachmentFailureCount: 1
  });

  assert.ok(diagnostics.some((line) => line.includes('attachmentFailureCount=1')));
});
