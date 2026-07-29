(function initWandaoTauriBridge(root, factory) {
  const bridge = factory(root);
  if (typeof module === 'object' && module.exports) {
    module.exports = bridge;
  }
})(typeof window !== 'undefined' ? window : globalThis, function createBridgeModule(root) {
  'use strict';

  const BRIDGE_PREFIX = '[Wandao Tauri bridge]';
  const COMMAND_NAMES = Object.freeze([
    'select_directory',
    'select_file',
    'select_browser_file',
    'save_file',
    'run_python_command',
    'stop_python_process',
    'get_python_process_state',
    'send_python_input',
    'protect_task_args',
    'restore_task_args',
    'read_file',
    'write_file',
    'file_exists',
    'open_path',
    'open_external',
    'fetch_remote_text',
    'copy_text',
    'show_about',
    'check_for_updates',
    'get_app_settings',
    'save_app_settings',
    'detect_browsers',
    'get_provider_manifests',
    'read_provider_guide_image',
    'get_plugin_catalog',
    'install_plugin',
    'install_plugin_file',
    'set_plugin_enabled',
    'rollback_plugin',
    'uninstall_plugin',
    'get_plugin_ui',
    'get_app_path'
  ]);

  const EVENT_NAMES = Object.freeze({
    onAppInfo: 'app-info',
    onPythonLog: 'python-log',
    onPythonProcessState: 'python-process-state',
    onPluginDownloadProgress: 'plugin-download-progress'
  });

  function unavailableError(operation) {
    const error = new Error(
      `${BRIDGE_PREFIX} 无法执行 ${operation}：未检测到 Tauri 2 运行时。`
      + '请通过 Wandao 桌面应用启动，不要直接在浏览器中打开 renderer/index.html。'
    );
    error.name = 'WandaoTauriBridgeUnavailableError';
    error.code = 'WANDAO_TAURI_UNAVAILABLE';
    return error;
  }

  function reportError(error) {
    const logger = root.console && typeof root.console.error === 'function'
      ? root.console.error.bind(root.console)
      : null;
    if (logger) logger(error);
  }

  function createTauriApi(tauri = root.__TAURI__) {
    const invoke = tauri?.core?.invoke;
    const listen = tauri?.event?.listen;
    const listenerReadiness = new Map();

    const invokeCommand = (command, args = {}) => {
      if (typeof invoke !== 'function') {
        const error = unavailableError(command);
        reportError(error);
        return Promise.reject(error);
      }
      // Tauri maps Rust snake_case parameter names to camelCase JavaScript
      // keys. Nested options are also renderer-owned camelCase contracts.
      return Promise.resolve().then(() => invoke(command, args));
    };

    const invokeAfterListener = (eventName, command, args = {}) => {
      const readiness = listenerReadiness.get(eventName);
      if (!readiness) return invokeCommand(command, args);
      return readiness
        .catch(() => undefined)
        .then(() => invokeCommand(command, args));
    };

    const subscribe = (eventName, callback) => {
      if (typeof callback !== 'function') {
        throw new TypeError(`${BRIDGE_PREFIX} ${eventName} 监听器必须是函数。`);
      }
      if (typeof listen !== 'function') {
        reportError(unavailableError(`listen:${eventName}`));
        return () => {};
      }

      let cancelled = false;
      let unsubscribe = null;
      let unsubscribeStarted = false;

      const stopListening = () => {
        if (unsubscribeStarted || typeof unsubscribe !== 'function') return;
        unsubscribeStarted = true;
        Promise.resolve()
          .then(() => unsubscribe())
          .catch((error) => reportError(error));
      };

      const readiness = Promise.resolve()
        .then(() => listen(eventName, (event) => {
          if (!cancelled) callback(event?.payload);
        }))
        .then((unlisten) => {
          if (typeof unlisten !== 'function') {
            throw new TypeError(`${BRIDGE_PREFIX} ${eventName} 未返回有效的取消监听函数。`);
          }
          unsubscribe = unlisten;
          if (cancelled) stopListening();
        });
      listenerReadiness.set(eventName, readiness);
      readiness.catch((error) => reportError(error));

      return () => {
        if (cancelled) return;
        cancelled = true;
        stopListening();
      };
    };

    return Object.freeze({
      selectDirectory: (options) => invokeCommand('select_directory', { options }),
      selectFile: (options) => invokeCommand('select_file', { options }),
      selectBrowserFile: () => invokeCommand('select_browser_file'),
      saveFile: (options) => invokeCommand('save_file', { options }),

      runPythonCommand: (command, args, options) => invokeCommand('run_python_command', {
        command,
        args,
        options
      }),
      stopPythonProcess: () => invokeCommand('stop_python_process'),
      getPythonProcessState: () => invokeAfterListener(
        EVENT_NAMES.onPythonProcessState,
        'get_python_process_state'
      ),
      sendPythonInput: (text) => invokeCommand('send_python_input', { text }),
      protectTaskArgs: (args) => invokeCommand('protect_task_args', { args }),
      restoreTaskArgs: (payload) => invokeCommand('restore_task_args', { payload }),

      readFile: (filePath) => invokeCommand('read_file', { filePath }),
      writeFile: (filePath, content) => invokeCommand('write_file', { filePath, content }),
      fileExists: (filePath) => invokeCommand('file_exists', { filePath }),
      openPath: (targetPath) => invokeCommand('open_path', { targetPath }),
      openExternal: (url) => invokeCommand('open_external', { url }),
      fetchRemoteText: (url) => invokeCommand('fetch_remote_text', { url }),
      copyText: (text) => invokeCommand('copy_text', { text }),
      showAbout: () => invokeCommand('show_about'),
      checkForUpdates: () => invokeCommand('check_for_updates'),
      getAppSettings: () => invokeCommand('get_app_settings'),
      saveAppSettings: (settings) => invokeCommand('save_app_settings', { settings }),
      detectBrowsers: () => invokeCommand('detect_browsers'),
      getProviderManifests: () => invokeCommand('get_provider_manifests'),
      readProviderGuideImage: (providerId, relativePath) => invokeCommand('read_provider_guide_image', {
        providerId,
        relativePath
      }),
      getPluginCatalog: (options) => invokeCommand('get_plugin_catalog', { options }),
      installPlugin: (pluginId, channel) => invokeCommand('install_plugin', { pluginId, channel }),
      installPluginFile: () => invokeCommand('install_plugin_file'),
      setPluginEnabled: (pluginId, enabled) => invokeCommand('set_plugin_enabled', { pluginId, enabled }),
      rollbackPlugin: (pluginId) => invokeCommand('rollback_plugin', { pluginId }),
      uninstallPlugin: (pluginId) => invokeCommand('uninstall_plugin', { pluginId }),
      getPluginUi: (pluginId, entry) => invokeCommand('get_plugin_ui', { pluginId, entry }),

      getAppPath: () => invokeCommand('get_app_path'),

      onAppInfo: (callback) => subscribe(EVENT_NAMES.onAppInfo, callback),
      onPythonLog: (callback) => subscribe(EVENT_NAMES.onPythonLog, callback),
      onPythonProcessState: (callback) => subscribe(EVENT_NAMES.onPythonProcessState, callback),
      onPluginDownloadProgress: (callback) => subscribe(EVENT_NAMES.onPluginDownloadProgress, callback)
    });
  }

  function installTauriBridge() {
    if (root.electronAPI) return root.electronAPI;
    const api = createTauriApi();
    root.electronAPI = api;

    const tauri = root.__TAURI__;
    if (typeof tauri?.core?.invoke !== 'function' || typeof tauri?.event?.listen !== 'function') {
      reportError(unavailableError('bridge initialization'));
    }
    return api;
  }

  const exports = Object.freeze({
    COMMAND_NAMES,
    EVENT_NAMES,
    createTauriApi,
    installTauriBridge,
    unavailableError
  });

  root.WandaoTauriBridge = exports;
  installTauriBridge();
  return exports;
});
