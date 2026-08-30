const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const bridgePath = path.resolve(__dirname, '..', 'wandao_electron', 'renderer', 'tauri_bridge.js');

function loadBridge(windowOverrides = {}) {
  const previousWindow = global.window;
  const errors = [];
  const window = {
    console: {
      error: (error) => errors.push(error)
    },
    ...windowOverrides
  };

  global.window = window;
  delete require.cache[bridgePath];
  const bridge = require(bridgePath);

  return {
    bridge,
    errors,
    window,
    restore() {
      delete require.cache[bridgePath];
      if (previousWindow === undefined) delete global.window;
      else global.window = previousWindow;
    }
  };
}

function settleAsyncWork() {
  return new Promise((resolve) => setImmediate(resolve));
}

test('Tauri bridge exposes all Electron-compatible commands', () => {
  const loaded = loadBridge({
    __TAURI__: {
      core: { invoke: async () => null },
      event: { listen: async () => () => {} }
    }
  });
  try {
    assert.equal(loaded.bridge.COMMAND_NAMES.length, 34);
    for (const command of loaded.bridge.COMMAND_NAMES) {
      assert.match(command, /^[a-z][a-z0-9_]*$/);
    }
    assert.equal(Object.keys(loaded.window.electronAPI).length, 39);
  } finally {
    loaded.restore();
  }
});

test('renderer installs the compatibility bridge before application startup', () => {
  const indexHtml = fs.readFileSync(
    path.resolve(__dirname, '..', 'wandao_electron', 'renderer', 'index.html'),
    'utf8'
  );
  const bridgeIndex = indexHtml.indexOf('<script src="tauri_bridge.js"></script>');
  const appIndex = indexHtml.indexOf('<script src="app.js"></script>');
  assert.ok(bridgeIndex >= 0);
  assert.ok(appIndex > bridgeIndex);
});

test('command adapters preserve the renderer API and send Tauri camelCase arguments', async () => {
  const calls = [];
  const loaded = loadBridge({
    __TAURI__: {
      core: {
        invoke: async (command, args) => {
          calls.push({ command, args });
          return { command, args };
        }
      },
      event: { listen: async () => () => {} }
    }
  });

  try {
    const api = loaded.window.electronAPI;
    await api.selectDirectory({ defaultPath: 'C:\\docs', nestedOption: { allowCreate: true } });
    await api.runPythonCommand('backend/export.py', ['--scan'], {
      taskId: 'task-1',
      pluginContext: { dataDir: 'C:\\data' }
    });
    await api.readProviderGuideImage('feishu-export', 'images/guide.png');
    await api.fetchRemoteImage('https://raw.githubusercontent.com/tllovesxs/wandao/main/docs/images/example.png');
    await api.setPluginEnabled('feishu', true);
    await api.writeFile('C:\\out\\result.json', '{}');

    assert.deepEqual(calls, [
      {
        command: 'select_directory',
        args: {
          options: {
            defaultPath: 'C:\\docs',
            nestedOption: { allowCreate: true }
          }
        }
      },
      {
        command: 'run_python_command',
        args: {
          command: 'backend/export.py',
          args: ['--scan'],
          options: {
            taskId: 'task-1',
            pluginContext: { dataDir: 'C:\\data' }
          }
        }
      },
      {
        command: 'read_provider_guide_image',
        args: {
          providerId: 'feishu-export',
          relativePath: 'images/guide.png'
        }
      },
      {
        command: 'fetch_remote_image',
        args: {
          url: 'https://raw.githubusercontent.com/tllovesxs/wandao/main/docs/images/example.png'
        }
      },
      {
        command: 'set_plugin_enabled',
        args: { pluginId: 'feishu', enabled: true }
      },
      {
        command: 'write_file',
        args: { filePath: 'C:\\out\\result.json', content: '{}' }
      }
    ]);
  } finally {
    loaded.restore();
  }
});

test('process state snapshot waits until the state listener is registered', async () => {
  let resolveListen;
  let invokeCount = 0;
  const loaded = loadBridge({
    __TAURI__: {
      core: {
        invoke: async (command) => {
          invokeCount += 1;
          return { command, running: false };
        }
      },
      event: {
        listen: () => new Promise((resolve) => {
          resolveListen = resolve;
        })
      }
    }
  });

  try {
    loaded.window.electronAPI.onPythonProcessState(() => {});
    const statePromise = loaded.window.electronAPI.getPythonProcessState();
    await Promise.resolve();
    await Promise.resolve();
    assert.equal(invokeCount, 0);

    resolveListen(() => {});
    const state = await statePromise;
    assert.equal(invokeCount, 1);
    assert.deepEqual(state, { command: 'get_python_process_state', running: false });
  } finally {
    loaded.restore();
  }
});

test('event adapters forward payloads and return an idempotent cancellation function', async () => {
  const handlers = new Map();
  let unlistenCount = 0;
  const loaded = loadBridge({
    __TAURI__: {
      core: { invoke: async () => null },
      event: {
        listen: async (eventName, handler) => {
          handlers.set(eventName, handler);
          return () => {
            unlistenCount += 1;
          };
        }
      }
    }
  });

  try {
    const received = [];
    const cancel = loaded.window.electronAPI.onPythonLog((payload) => received.push(payload));
    assert.equal(typeof cancel, 'function');
    await Promise.resolve();
    await Promise.resolve();

    handlers.get('python-log')({ payload: { level: 'info', message: 'ready' } });
    assert.deepEqual(received, [{ level: 'info', message: 'ready' }]);

    cancel();
    cancel();
    await settleAsyncWork();
    assert.equal(unlistenCount, 1);

    handlers.get('python-log')({ payload: { message: 'late event' } });
    assert.equal(received.length, 1);
  } finally {
    loaded.restore();
  }
});

test('event cancellation is safe before Tauri listen resolves', async () => {
  let resolveListen;
  let unlistenCount = 0;
  const loaded = loadBridge({
    __TAURI__: {
      core: { invoke: async () => null },
      event: {
        listen: () => new Promise((resolve) => {
          resolveListen = resolve;
        })
      }
    }
  });

  try {
    const cancel = loaded.window.electronAPI.onAppInfo(() => {});
    cancel();
    await Promise.resolve();
    resolveListen(() => {
      unlistenCount += 1;
    });
    await settleAsyncWork();
    assert.equal(unlistenCount, 1);
  } finally {
    loaded.restore();
  }
});

test('an existing Electron preload API is never replaced', () => {
  const electronAPI = { getAppPath: async () => ({ mode: 'electron' }) };
  const loaded = loadBridge({ electronAPI });
  try {
    assert.equal(loaded.window.electronAPI, electronAPI);
    assert.equal(loaded.errors.length, 0);
  } finally {
    loaded.restore();
  }
});

test('missing Tauri runtime fails native calls with an explicit diagnostic', async () => {
  const loaded = loadBridge();
  try {
    assert.equal(loaded.errors.length, 1);
    await assert.rejects(
      loaded.window.electronAPI.getAppPath(),
      (error) => error.code === 'WANDAO_TAURI_UNAVAILABLE'
        && /未检测到 Tauri 2 运行时/.test(error.message)
    );
    assert.equal(loaded.errors.length, 2);

    const cancel = loaded.window.electronAPI.onPythonLog(() => {});
    assert.equal(typeof cancel, 'function');
    assert.equal(loaded.errors.length, 3);
  } finally {
    loaded.restore();
  }
});
