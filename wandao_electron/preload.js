const { contextBridge, ipcRenderer } = require('electron');

// 暴露安全的 API 给渲染进程
contextBridge.exposeInMainWorld('electronAPI', {
  // 文件对话框
  selectDirectory: (options) => ipcRenderer.invoke('select-directory', options),
  selectFile: (options) => ipcRenderer.invoke('select-file', options),
  selectBrowserFile: () => ipcRenderer.invoke('select-browser-file'),
  saveFile: (options) => ipcRenderer.invoke('save-file', options),

  // Python 命令执行
  runPythonCommand: (command, args, options) => ipcRenderer.invoke('run-python-command', command, args, options),
  stopPythonProcess: () => ipcRenderer.invoke('stop-python-process'),
  getPythonProcessState: () => ipcRenderer.invoke('get-python-process-state'),
  sendPythonInput: (text) => ipcRenderer.invoke('send-python-input', text),
  protectTaskArgs: (args) => ipcRenderer.invoke('protect-task-args', args),
  restoreTaskArgs: (payload) => ipcRenderer.invoke('restore-task-args', payload),

  // 文件操作
  readFile: (filePath) => ipcRenderer.invoke('read-file', filePath),
  writeFile: (filePath, content) => ipcRenderer.invoke('write-file', filePath, content),
  fileExists: (filePath) => ipcRenderer.invoke('file-exists', filePath),
  openPath: (targetPath) => ipcRenderer.invoke('open-path', targetPath),
  openExternal: (url) => ipcRenderer.invoke('open-external', url),
  fetchRemoteText: (url) => ipcRenderer.invoke('fetch-remote-text', url),
  copyText: (text) => ipcRenderer.invoke('copy-text', text),
  showAbout: () => ipcRenderer.invoke('show-about'),
  checkForUpdates: () => ipcRenderer.invoke('check-for-updates'),
  getAppSettings: () => ipcRenderer.invoke('get-app-settings'),
  saveAppSettings: (settings) => ipcRenderer.invoke('save-app-settings', settings),
  detectBrowsers: () => ipcRenderer.invoke('detect-browsers'),
  getProviderManifests: () => ipcRenderer.invoke('get-provider-manifests'),
  getPluginCatalog: (options) => ipcRenderer.invoke('get-plugin-catalog', options),
  installPlugin: (pluginId, channel) => ipcRenderer.invoke('install-plugin', pluginId, channel),
  installPluginFile: () => ipcRenderer.invoke('install-plugin-file'),
  setPluginEnabled: (pluginId, enabled) => ipcRenderer.invoke('set-plugin-enabled', pluginId, enabled),
  rollbackPlugin: (pluginId) => ipcRenderer.invoke('rollback-plugin', pluginId),
  uninstallPlugin: (pluginId) => ipcRenderer.invoke('uninstall-plugin', pluginId),
  getPluginUi: (pluginId, entry) => ipcRenderer.invoke('get-plugin-ui', pluginId, entry),

  // 应用路径
  getAppPath: () => ipcRenderer.invoke('get-app-path'),

  // 监听应用菜单消息
  onAppInfo: (callback) => {
    const listener = (event, data) => callback(data);
    ipcRenderer.on('app-info', listener);
    return () => ipcRenderer.removeListener('app-info', listener);
  },

  // 监听 Python 日志
  onPythonLog: (callback) => {
    const listener = (event, data) => callback(data);
    ipcRenderer.on('python-log', listener);
    return () => ipcRenderer.removeListener('python-log', listener);
  },
  onPythonProcessState: (callback) => {
    const listener = (event, data) => callback(data);
    ipcRenderer.on('python-process-state', listener);
    return () => ipcRenderer.removeListener('python-process-state', listener);
  }
});
