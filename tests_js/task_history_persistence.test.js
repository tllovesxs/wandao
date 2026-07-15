const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const vm = require('node:vm');

const appJs = fs.readFileSync('wandao_electron/renderer/app.js', 'utf8');
const loadSource = appJs.slice(
  appJs.indexOf('async function performTaskHistoryLoad'),
  appJs.indexOf('async function saveTaskHistory')
);
const saveSource = appJs.slice(
  appJs.indexOf('async function saveTaskHistory'),
  appJs.indexOf('function currentTaskHistoryFilters')
);

function createHarness(stored, { restoreTaskArgs, protectTaskArgs } = {}) {
  const writes = [];
  let protectCalls = 0;
  const sandbox = {
    stored: JSON.parse(JSON.stringify(stored)),
    writes,
    console,
    window: {
      WandaoTaskReport: { maskArgs: (args) => args.map(() => '***') },
      electronAPI: {
        restoreTaskArgs: restoreTaskArgs || (async () => ({ success: false })),
        protectTaskArgs: async (args) => {
          protectCalls += 1;
          return protectTaskArgs ? protectTaskArgs(args) : { success: true, payload: 'ciphertext' };
        },
        writeFile: async (_filePath, content) => {
          writes.push(JSON.parse(content));
          return { success: true };
        }
      }
    }
  };
  vm.runInNewContext(`
    const MAX_TASK_HISTORY = 80;
    let taskHistory = [];
    let taskHistoryLoadPromise = null;
    let taskHistoryLoadError = '';
    let mainPythonProcessState = { running: false, taskId: '' };
    function taskHistoryPath() { return 'task-history.json'; }
    async function readJsonFileIfExists() { return JSON.parse(JSON.stringify(stored)); }
    function renderTaskHistory() {}
    function maskSensitiveValue(value) { return value; }
    function maskSensitiveText(value) { return value; }
    function appendDetailedLog() {}
    function formatError(error) { return error?.message || String(error || ''); }
    ${loadSource}
    ${saveSource}
    globalThis.api = {
      loadTaskHistory,
      saveTaskHistory,
      getTasks: () => taskHistory,
      setTasks: (tasks) => { taskHistory = tasks; },
      getProtectCalls: () => ${'protectCalls'}
    };
  `, sandbox);
  // Functions created inside the VM cannot close over this outer counter, so
  // expose it through the mock itself for assertions.
  sandbox.api.getProtectCalls = () => protectCalls;
  return sandbox;
}

test('masked unavailable arguments are cleared and never encrypted as legacy raw arguments', async () => {
  const harness = createHarness({
    tasks: [{ id: 'masked', status: 'failed', args: ['--api-key', '***'], argsUnavailable: true }]
  });

  await harness.api.loadTaskHistory();

  assert.equal(harness.api.getProtectCalls(), 0);
  assert.deepEqual(Array.from(harness.api.getTasks()[0].args), []);
  assert.equal(harness.writes.length, 1);
  assert.deepEqual(harness.writes[0].tasks[0].args, []);
  assert.equal(harness.writes[0].tasks[0].argsUnavailable, true);
});

test('a temporarily unavailable protected payload is retained and can decrypt later', async () => {
  const first = createHarness({
    tasks: [{ id: 'protected', status: 'failed', protectedArgs: 'ciphertext', args: [] }]
  }, { restoreTaskArgs: async () => ({ success: false, error: 'temporarily unavailable' }) });

  await first.api.loadTaskHistory();
  await first.api.saveTaskHistory();
  const persisted = first.writes.at(-1);
  assert.equal(persisted.tasks[0].protectedArgs, 'ciphertext');
  assert.equal(persisted.tasks[0].argsUnavailable, true);

  const second = createHarness(persisted, {
    restoreTaskArgs: async () => ({ success: true, args: ['--api-key', 'real-key'] })
  });
  await second.api.loadTaskHistory();
  assert.deepEqual(Array.from(second.api.getTasks()[0].args), ['--api-key', 'real-key']);
  assert.equal(second.api.getTasks()[0].argsUnavailable, false);
});

test('a new protection failure persists neither secrets nor resumable placeholders', async () => {
  const harness = createHarness({ tasks: [] }, {
    protectTaskArgs: async () => ({ success: false, error: 'storage unavailable' })
  });
  harness.api.setTasks([{
    id: 'new-task',
    status: 'failed',
    args: ['--client-secret', 'top-secret'],
    resultData: null,
    logs: [],
    error: ''
  }]);

  await harness.api.saveTaskHistory();

  const serialized = JSON.stringify(harness.writes[0]);
  assert.equal(harness.api.getProtectCalls(), 1);
  assert.doesNotMatch(serialized, /top-secret|client-secret|\*\*\*/);
  assert.equal(harness.writes[0].tasks[0].argsUnavailable, true);
  assert.deepEqual(harness.writes[0].tasks[0].args, []);
});
