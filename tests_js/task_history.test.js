const assert = require('node:assert/strict');
const test = require('node:test');
const {
  filterAndSortTasks,
  matchesTask,
  selectVisibleTasks
} = require('../wandao_electron/renderer/task_history');

const tasks = [
  {
    id: 'older',
    title: '飞书知识库导出',
    providerId: 'feishu-export',
    providerTitle: '飞书',
    action: '导出',
    status: 'completed',
    startedAt: '2026-07-01T08:00:00.000Z'
  },
  {
    id: 'newer',
    title: '语雀团队文档导入',
    providerId: 'yuque-import',
    providerTitle: '语雀',
    action: '导入',
    status: 'completed',
    startedAt: '2026-07-03T08:00:00.000Z',
    error: '图片资源下载失败'
  },
  {
    id: 'middle',
    title: '飞书失败重试',
    providerId: 'feishu-export',
    providerTitle: '飞书',
    action: '导出',
    status: 'failed',
    startedAt: '2026-07-02T08:00:00.000Z'
  }
];

test('task history filters keywords, platform, and derived status before sorting newest first', () => {
  const derivedStatus = (task) => task.id === 'newer' ? 'partial' : task.status;

  assert.deepEqual(
    filterAndSortTasks(tasks, { query: '飞书 导出', status: 'all', providerId: 'all' }, { getStatus: derivedStatus })
      .map((task) => task.id),
    ['middle', 'older']
  );
  assert.deepEqual(
    filterAndSortTasks(tasks, { status: 'partial', providerId: 'yuque-import' }, { getStatus: derivedStatus })
      .map((task) => task.id),
    ['newer']
  );
  assert.equal(matchesTask(tasks[1], { query: '资源 下载', status: 'partial' }, derivedStatus), true);
});

test('task history display limits long lists but reports the matching total', () => {
  const result = selectVisibleTasks(tasks, {}, { limit: 2 });

  assert.equal(result.total, 3);
  assert.equal(result.hasMore, true);
  assert.deepEqual(result.tasks.map((task) => task.id), ['newer', 'middle']);
});

test('task history keeps original ordering when dates are unavailable', () => {
  const result = filterAndSortTasks([
    { id: 'first', title: 'A', startedAt: 'not-a-date' },
    { id: 'second', title: 'B' }
  ]);

  assert.deepEqual(result.map((task) => task.id), ['first', 'second']);
});
