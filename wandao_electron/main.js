const { app, BrowserWindow, ipcMain, dialog, shell, Menu, clipboard, safeStorage } = require('electron');
const path = require('path');
const { spawn, spawnSync } = require('child_process');
const fs = require('fs');
const https = require('https');
const { PluginManager } = require('./plugin_manager');
const { assertSafeRelativePath, validatePluginManifest } = require('./plugin_format');
const { parseLastJson, parseProcessResult } = require('./process_result');
const { createScanStdoutRelay } = require('./scan_stdout_relay');
const { extractSensitiveArguments } = require('./command_security');
const { resolveProviderScript } = require('./provider_script_routing');
const { resolveLegacyTemplateConfig } = require('./provider_legacy_compat');
const { migrateLegacyPluginState } = require('./plugin_state_migration');

let mainWindow;
let pythonProcess = null;
let pythonProcessStopping = false;
let pythonStopFile = '';
let pythonProcessMetadata = null;
let shutdownConfirmed = false;
const pluginRegistryCache = new Map();
const MAX_PROCESS_OUTPUT_CHARS = 32 * 1024 * 1024;

function currentPythonProcessState(extra = {}) {
  return {
    running: Boolean(pythonProcess),
    stopping: Boolean(pythonProcess && pythonProcessStopping),
    providerId: pythonProcessMetadata?.providerId || '',
    taskId: pythonProcessMetadata?.taskId || '',
    startedAt: pythonProcessMetadata?.startedAt || '',
    ...extra
  };
}

function broadcastPythonProcessState(extra = {}) {
  const state = currentPythonProcessState(extra);
  if (mainWindow && !mainWindow.isDestroyed() && !mainWindow.webContents.isDestroyed()) {
    mainWindow.webContents.send('python-process-state', state);
  }
  return state;
}

const PROJECT_INFO = {
  name: '万能导 Wandao',
  version: app.getVersion(),
  slogan: '让知识没有壁垒，多平台文档互转',
  author: 'tllovesxs',
  github: 'https://github.com/tllovesxs/wandao',
  docs: 'https://github.com/tllovesxs/wandao/blob/main/docs/%E4%BD%BF%E7%94%A8%E6%95%99%E7%A8%8B.md',
  releases: 'https://github.com/tllovesxs/wandao/releases',
  latestReleaseApi: 'https://api.github.com/repos/tllovesxs/wandao/releases/latest',
  issues: 'https://github.com/tllovesxs/wandao/issues',
  wechat: 'pressure_spring'
};

const APP_ID = 'com.wandao.app';
const SETTINGS_FILE = 'settings.json';
const SETTINGS_SCHEMA_VERSION = 1;
const BROWSER_DOWNLOAD_URL = 'https://www.google.com/chrome/';
const PLUGIN_REGISTRY_URL = process.env.WANDAO_PLUGIN_REGISTRY_URL
  || 'https://github.com/tllovesxs/wandao/releases/download/plugins-latest/registry.json';
const EXPERIMENTAL_PLUGIN_REGISTRY_URL = process.env.WANDAO_EXPERIMENTAL_PLUGIN_REGISTRY_URL
  || 'https://github.com/tllovesxs/wandao/releases/download/plugins-experimental/registry.json';
let pluginManagerInstance = null;

if (process.env.WANDAO_USER_DATA_DIR) {
  app.setPath('userData', path.resolve(process.env.WANDAO_USER_DATA_DIR));
}

const hasSingleInstanceLock = app.requestSingleInstanceLock();
if (!hasSingleInstanceLock) {
  app.quit();
}

function resolveAppAsset(fileName) {
  const candidates = uniquePaths([
    path.join(__dirname, 'assets', fileName),
    path.join(app.getAppPath(), 'assets', fileName),
    path.join(process.resourcesPath || '', 'assets', fileName)
  ]);
  return candidates.find((candidate) => fs.existsSync(candidate)) || candidates[0];
}

function appIconPath() {
  if (process.platform === 'win32') {
    return resolveAppAsset('icon.ico');
  }
  return resolveAppAsset('icon.png');
}

function configureAppIdentity() {
  app.setName(PROJECT_INFO.name);
  if (process.platform === 'win32') {
    app.setAppUserModelId(APP_ID);
  }
  if (process.platform === 'darwin' && app.dock) {
    app.dock.setIcon(resolveAppAsset('icon.png'));
  }
}

function cleanupPythonProcess() {
  if (!pythonProcess) return false;
  pythonProcessStopping = true;
  try {
    return terminateProcessTree(pythonProcess, { force: true });
  } catch (_error) {
    // Ignore shutdown cleanup errors. The close/error handler owns state release.
    return false;
  }
}

function pluginManager() {
  if (!pluginManagerInstance) {
    const trustStore = readJsonFile(resolveAppAsset('plugin-trust.json'));
    pluginManagerInstance = new PluginManager({
      rootDir: path.join(app.getPath('userData'), 'plugins'),
      trustStore,
      coreVersion: app.getVersion(),
      platform: process.platform,
      registryUrl: PLUGIN_REGISTRY_URL,
      allowLocalHttp: Boolean(process.env.WANDAO_PLUGIN_ALLOW_LOCAL_HTTP)
    });
  }
  return pluginManagerInstance;
}

function terminateProcessTree(proc, { force = false } = {}) {
  if (!proc || !Number.isInteger(proc.pid) || proc.pid <= 0) return false;
  if (process.platform === 'win32') {
    // Windows has no POSIX process groups. taskkill /T explicitly includes all
    // descendants spawned by a provider (Python helpers, browser drivers, etc).
    const result = spawnSync('taskkill', ['/pid', String(proc.pid), '/T', '/F'], {
      windowsHide: true,
      stdio: 'ignore'
    });
    return !result.error && result.status === 0;
  }
  try {
    // Provider tasks are launched detached on POSIX, so the negative PID safely
    // targets the task's own process group instead of the Electron process.
    process.kill(-proc.pid, force ? 'SIGKILL' : 'SIGTERM');
    return true;
  } catch (_error) {
    return proc.kill(force ? 'SIGKILL' : 'SIGTERM');
  }
}

function requestPythonStop() {
  if (!pythonProcess) {
    return { success: false, error: '没有正在运行的任务' };
  }
  if (pythonProcessStopping) {
    return { success: true, stopping: true };
  }
  pythonProcessStopping = true;
  broadcastPythonProcessState();
  if (pythonStopFile) {
    fs.mkdirSync(path.dirname(pythonStopFile), { recursive: true });
    fs.writeFileSync(pythonStopFile, 'stop', 'utf8');
    const processAtRequest = pythonProcess;
    setTimeout(() => {
      if (pythonProcess === processAtRequest) terminateProcessTree(processAtRequest, { force: true });
    }, 8000).unref();
    return { success: true, stopping: true, cooperative: true };
  }
  const signaled = terminateProcessTree(pythonProcess);
  if (!signaled) {
    pythonProcessStopping = false;
    broadcastPythonProcessState();
    return { success: false, error: '无法停止当前任务，请稍后重试。' };
  }
  return { success: true, stopping: true };
}

function writePythonInput(proc, text, { end = false } = {}) {
  if (!proc?.stdin || proc.stdin.destroyed || proc.stdin.writableEnded) {
    return { success: false, error: '没有正在等待输入的任务' };
  }
  if (proc.stdinWriteError) {
    return { success: false, error: proc.stdinWriteError };
  }
  try {
    proc.stdin.write(String(text || '\n'), (error) => {
      if (error) proc.stdinWriteError = error.message || String(error);
    });
    if (end) proc.stdin.end();
    return { success: true };
  } catch (error) {
    proc.stdinWriteError = error.message || String(error);
    return { success: false, error: proc.stdinWriteError };
  }
}

function confirmTaskShutdown() {
  if (!pythonProcess || shutdownConfirmed) return true;
  const response = dialog.showMessageBoxSync(mainWindow || undefined, {
    type: 'warning',
    title: '任务仍在运行',
    message: '当前迁移任务尚未完成。',
    detail: '立即退出会停止任务；已完成文件和 checkpoint 会保留，下次可继续。',
    buttons: ['继续运行', '停止任务并退出'],
    defaultId: 0,
    cancelId: 0,
    noLink: true
  });
  if (response !== 1) return false;
  shutdownConfirmed = true;
  cleanupPythonProcess();
  return true;
}

function bundledPluginRoots() {
  return uniquePaths([
    path.join(__dirname, '..', 'plugins'),
    path.join(app.getAppPath(), '..', 'plugins'),
    path.join(process.resourcesPath || '', 'plugins')
  ]);
}

function readBundledPlugin(pluginId) {
  const expectedId = String(pluginId || '').trim();
  for (const root of bundledPluginRoots()) {
    const pluginRoot = path.join(root, expectedId);
    const manifestPath = path.join(pluginRoot, 'plugin.json');
    if (!fs.existsSync(manifestPath)) continue;
    const manifest = validatePluginManifest(readJsonFile(manifestPath));
    if (manifest.id !== expectedId || path.basename(pluginRoot) !== manifest.id) {
      throw new Error(`内置插件目录与 ID 不一致：${expectedId}`);
    }
    if (manifest.platforms?.length && !manifest.platforms.includes(process.platform)) {
      throw new Error(`内置插件不支持当前系统：${expectedId}`);
    }
    return { pluginRoot, manifestPath, manifest };
  }
  return null;
}

function bundledPluginEntriesWithErrors() {
  const entries = [];
  const errors = [];
  const seenPluginIds = new Set();
  const installed = new Map(pluginManager().listInstalled().map((item) => [item.id, item]));
  for (const root of bundledPluginRoots()) {
    if (!fs.existsSync(root)) continue;
    for (const directory of fs.readdirSync(root, { withFileTypes: true })) {
      if (!directory.isDirectory() || directory.name.startsWith('_') || directory.name.startsWith('.')) continue;
      const pluginRoot = path.join(root, directory.name);
      const manifestPath = path.join(pluginRoot, 'plugin.json');
      if (!fs.existsSync(manifestPath)) continue;
      try {
        const manifest = validatePluginManifest(readJsonFile(manifestPath));
        if (directory.name !== manifest.id) throw new Error(`插件目录名必须等于插件 ID：${manifest.id}`);
        if (seenPluginIds.has(manifest.id)) continue;
        seenPluginIds.add(manifest.id);
        const installedPlugin = installed.get(manifest.id);
        if (installedPlugin && !installedPlugin.enabled) continue;
        // A same-version installed copy may have been left behind by an older
        // desktop build. Prefer the signed plugin that ships with the current
        // app unless the installed plugin is a strictly newer update.
        if (
          installedPlugin?.enabled
          && installedPlugin.compatibility?.compatible
          && compareVersions(installedPlugin.currentVersion, manifest.version) > 0
        ) continue;
        if (manifest.platforms?.length && !manifest.platforms.includes(process.platform)) continue;
        for (const relativePath of manifest.entrypoints.providers) {
          const safe = assertSafeRelativePath(relativePath, 'Provider 入口');
          const providerPath = path.resolve(pluginRoot, ...safe.split('/'));
          if (!isInsidePath(pluginRoot, providerPath) || !fs.existsSync(providerPath)) {
            throw new Error(`Provider 入口不存在或越界：${relativePath}`);
          }
          entries.push({
            pluginId: manifest.id,
            pluginVersion: manifest.version,
            pluginRoot,
            manifestPath: providerPath,
            permissions: manifest.permissions || [],
            uiEntry: manifest.entrypoints.ui || '',
            verified: true,
            bundled: true,
            manifest
          });
        }
      } catch (error) {
        errors.push(`${manifestPath}：${error.message || String(error)}`);
      }
    }
  }
  return { entries, errors };
}

// Bundled plugins are part of the signed desktop artifact.  They deliberately
// remain visible in the plugin center even when the online registry is offline:
// users should be able to tell which platform capabilities ship with Wandao and
// which ones have been overridden by an installed signed update.
function bundledPluginCatalogEntries() {
  const bundled = new Map();
  for (const root of bundledPluginRoots()) {
    if (!fs.existsSync(root)) continue;
    for (const directory of fs.readdirSync(root, { withFileTypes: true })) {
      if (!directory.isDirectory() || directory.name.startsWith('_') || directory.name.startsWith('.')) continue;
      const manifestPath = path.join(root, directory.name, 'plugin.json');
      if (!fs.existsSync(manifestPath) || bundled.has(directory.name)) continue;
      try {
        const manifest = validatePluginManifest(readJsonFile(manifestPath));
        if (manifest.id !== directory.name) throw new Error('插件目录与 manifest ID 不一致');
        bundled.set(manifest.id, {
          ...manifest,
          bundled: true,
          channel: 'stable',
          bundledVersion: manifest.version,
          installed: false,
          enabled: true,
          installedVersion: '',
          updateAvailable: false,
          previousVersions: [],
          compatibility: pluginManager().compatibility(manifest)
        });
      } catch (_error) {
        // Provider discovery will report the concrete manifest error.  A broken
        // manifest is not advertised as a usable plugin in the UI.
      }
    }
  }
  return bundled;
}

function pluginCatalogWithBundled(registry = null) {
  const bundled = bundledPluginCatalogEntries();
  const catalog = pluginManager().listWithRegistry(registry).map((entry) => {
    const builtin = bundled.get(entry.id);
    if (!builtin) return entry;
    bundled.delete(entry.id);
    const installed = Boolean(entry.installed);
    return {
      ...builtin,
      ...entry,
      bundled: true,
      channel: entry.channel || 'stable',
      bundledVersion: builtin.version,
      updateAvailable: installed
        ? entry.updateAvailable
        : compareVersions(entry.version, builtin.version) > 0
    };
  });
  return [...catalog, ...bundled.values()]
    .sort((a, b) => String(a.name || a.id).localeCompare(String(b.name || b.id), 'zh-Hans-CN'));
}

function protectTaskArgs(args) {
  if (!Array.isArray(args)) {
    return { success: false, error: '任务参数格式不正确。' };
  }
  if (!safeStorage.isEncryptionAvailable()) {
    return { success: false, error: '当前系统无法使用安全存储，任务参数不会写入历史记录。' };
  }
  try {
    const encrypted = safeStorage.encryptString(JSON.stringify(args.map((item) => String(item))));
    return { success: true, payload: encrypted.toString('base64') };
  } catch (error) {
    return { success: false, error: error.message || String(error) };
  }
}

function restoreTaskArgs(payload) {
  const encoded = String(payload || '').trim();
  if (!encoded) return { success: true, args: [] };
  if (!safeStorage.isEncryptionAvailable()) {
    return { success: false, error: '当前系统无法解密任务参数，请重新填写参数后执行。', args: [] };
  }
  try {
    const value = JSON.parse(safeStorage.decryptString(Buffer.from(encoded, 'base64')));
    if (!Array.isArray(value)) throw new Error('任务参数不是数组');
    return { success: true, args: value.map((item) => String(item)) };
  } catch (error) {
    return { success: false, error: `任务参数解密失败：${error.message || String(error)}`, args: [] };
  }
}

function appendOutputTail(current, chunk) {
  const combined = current + chunk;
  if (combined.length <= MAX_PROCESS_OUTPUT_CHARS) {
    return { text: combined, omitted: 0 };
  }
  const omitted = combined.length - MAX_PROCESS_OUTPUT_CHARS;
  return { text: combined.slice(omitted), omitted };
}

function outputWithOmissionNotice(text, omitted) {
  if (!omitted) return text;
  return `[前部 ${omitted} 个字符已省略，以下为输出尾部]\n${text}`;
}

function writePrivateTextAtomic(filePath, content) {
  const target = path.resolve(filePath);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  const temporary = path.join(
    path.dirname(target),
    `.${path.basename(target)}.${process.pid}.${Date.now()}.${Math.random().toString(36).slice(2, 8)}.tmp`
  );
  try {
    fs.writeFileSync(temporary, String(content), { encoding: 'utf-8', mode: 0o600 });
    fs.renameSync(temporary, target);
    try {
      fs.chmodSync(target, 0o600);
    } catch (_error) {
      // Windows primarily relies on the current user's profile ACL.
    }
  } finally {
    if (fs.existsSync(temporary)) {
      try {
        fs.unlinkSync(temporary);
      } catch (_error) {
        // Best-effort cleanup after a failed write.
      }
    }
  }
}

function uniquePaths(paths) {
  const seen = new Set();
  const result = [];
  for (const item of paths) {
    if (!item) continue;
    const normalized = path.resolve(item);
    const key = process.platform === 'win32' ? normalized.toLowerCase() : normalized;
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(normalized);
  }
  return result;
}

function expandHomePath(value) {
  const raw = String(value || '').trim();
  if (!raw.startsWith('~')) return raw;
  const home = app.getPath('home') || process.env.USERPROFILE || process.env.HOME || '';
  if (!home) return raw;
  if (raw === '~') return home;
  if (raw.startsWith('~/') || raw.startsWith('~\\')) {
    return path.join(home, raw.slice(2));
  }
  return raw;
}

function isPathLike(value) {
  const raw = String(value || '');
  return path.isAbsolute(raw) || raw.startsWith('~') || raw.includes('/') || raw.includes('\\');
}

function isExecutableFile(filePath) {
  try {
    return fs.existsSync(filePath) && fs.statSync(filePath).isFile();
  } catch (_error) {
    return false;
  }
}

function findExecutableOnPath(command) {
  const raw = String(command || '').trim();
  if (!raw) return '';
  if (isPathLike(raw)) {
    const resolved = path.resolve(expandHomePath(raw));
    return isExecutableFile(resolved) ? resolved : '';
  }

  const pathEntries = String(process.env.PATH || '')
    .split(path.delimiter)
    .filter(Boolean);
  const extensions = process.platform === 'win32'
    ? String(process.env.PATHEXT || '.EXE;.CMD;.BAT;.COM')
      .split(';')
      .filter(Boolean)
    : [''];
  const names = process.platform === 'win32' && !path.extname(raw)
    ? [raw, ...extensions.map((extension) => `${raw}${extension}`)]
    : [raw];

  for (const dir of pathEntries) {
    for (const name of names) {
      const candidate = path.join(dir, name);
      if (isExecutableFile(candidate)) {
        return path.resolve(candidate);
      }
    }
  }
  return '';
}

function normalizeBrowserExecutable(browserPath) {
  const raw = String(browserPath || '').trim();
  if (!raw) return '';
  if (!isPathLike(raw)) {
    return findExecutableOnPath(raw);
  }

  const resolved = path.resolve(expandHomePath(raw));
  if (process.platform === 'darwin' && resolved.toLowerCase().endsWith('.app')) {
    const appName = path.basename(resolved, '.app');
    const executableNames = uniquePaths([
      path.join(resolved, 'Contents', 'MacOS', appName),
      path.join(resolved, 'Contents', 'MacOS', appName.replace(/\s+Browser$/i, '')),
      path.join(resolved, 'Contents', 'MacOS', 'Google Chrome'),
      path.join(resolved, 'Contents', 'MacOS', 'Microsoft Edge'),
      path.join(resolved, 'Contents', 'MacOS', 'Chromium'),
      path.join(resolved, 'Contents', 'MacOS', 'Brave Browser')
    ]);
    const match = executableNames.find(isExecutableFile);
    return match || '';
  }

  return isExecutableFile(resolved) ? resolved : '';
}

function browserCandidateSpecs() {
  const home = app.getPath('home') || process.env.USERPROFILE || process.env.HOME || '';
  if (process.platform === 'win32') {
    const programFiles = process.env.PROGRAMFILES || 'C:\\Program Files';
    const programFilesX86 = process.env['PROGRAMFILES(X86)'] || 'C:\\Program Files (x86)';
    const localAppData = process.env.LOCALAPPDATA || '';
    return [
      {
        id: 'chrome',
        name: 'Google Chrome',
        paths: [
          path.join(programFiles, 'Google', 'Chrome', 'Application', 'chrome.exe'),
          path.join(programFilesX86, 'Google', 'Chrome', 'Application', 'chrome.exe'),
          localAppData && path.join(localAppData, 'Google', 'Chrome', 'Application', 'chrome.exe')
        ],
        commands: ['chrome', 'chrome.exe', 'google-chrome']
      },
      {
        id: 'edge',
        name: 'Microsoft Edge',
        paths: [
          path.join(programFiles, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
          path.join(programFilesX86, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
          localAppData && path.join(localAppData, 'Microsoft', 'Edge', 'Application', 'msedge.exe')
        ],
        commands: ['msedge', 'msedge.exe']
      },
      {
        id: 'chromium',
        name: 'Chromium',
        paths: [
          path.join(programFiles, 'Chromium', 'Application', 'chrome.exe'),
          path.join(programFilesX86, 'Chromium', 'Application', 'chrome.exe'),
          localAppData && path.join(localAppData, 'Chromium', 'Application', 'chrome.exe')
        ],
        commands: ['chromium', 'chromium.exe']
      },
      {
        id: 'brave',
        name: 'Brave',
        paths: [
          path.join(programFiles, 'BraveSoftware', 'Brave-Browser', 'Application', 'brave.exe'),
          path.join(programFilesX86, 'BraveSoftware', 'Brave-Browser', 'Application', 'brave.exe'),
          localAppData && path.join(localAppData, 'BraveSoftware', 'Brave-Browser', 'Application', 'brave.exe')
        ],
        commands: ['brave', 'brave.exe']
      }
    ];
  }

  if (process.platform === 'darwin') {
    const applicationRoots = uniquePaths([
      '/Applications',
      home && path.join(home, 'Applications')
    ]);
    const appPath = (root, appName, executableName) => (
      root ? path.join(root, appName, 'Contents', 'MacOS', executableName) : ''
    );
    return [
      {
        id: 'chrome',
        name: 'Google Chrome',
        paths: applicationRoots.map((root) => appPath(root, 'Google Chrome.app', 'Google Chrome')),
        commands: []
      },
      {
        id: 'edge',
        name: 'Microsoft Edge',
        paths: applicationRoots.map((root) => appPath(root, 'Microsoft Edge.app', 'Microsoft Edge')),
        commands: []
      },
      {
        id: 'chromium',
        name: 'Chromium',
        paths: applicationRoots.map((root) => appPath(root, 'Chromium.app', 'Chromium')),
        commands: []
      },
      {
        id: 'brave',
        name: 'Brave',
        paths: applicationRoots.map((root) => appPath(root, 'Brave Browser.app', 'Brave Browser')),
        commands: []
      }
    ];
  }

  return [
    {
      id: 'chrome',
      name: 'Google Chrome',
      paths: ['/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/opt/google/chrome/chrome'],
      commands: ['google-chrome', 'google-chrome-stable', 'chrome']
    },
    {
      id: 'edge',
      name: 'Microsoft Edge',
      paths: ['/usr/bin/microsoft-edge', '/usr/bin/microsoft-edge-stable'],
      commands: ['microsoft-edge', 'microsoft-edge-stable']
    },
    {
      id: 'chromium',
      name: 'Chromium',
      paths: ['/usr/bin/chromium', '/usr/bin/chromium-browser', '/snap/bin/chromium'],
      commands: ['chromium', 'chromium-browser']
    },
    {
      id: 'brave',
      name: 'Brave',
      paths: ['/usr/bin/brave-browser', '/snap/bin/brave'],
      commands: ['brave-browser', 'brave']
    }
  ];
}

function detectBrowsers() {
  const browsers = [];
  const seen = new Set();
  const pushBrowser = (spec, browserPath, source) => {
    const normalized = normalizeBrowserExecutable(browserPath);
    if (!normalized) return;
    const key = process.platform === 'win32' ? normalized.toLowerCase() : normalized;
    if (seen.has(key)) return;
    seen.add(key);
    browsers.push({
      id: spec.id,
      name: spec.name,
      path: normalized,
      source
    });
  };

  for (const spec of browserCandidateSpecs()) {
    for (const candidatePath of spec.paths || []) {
      pushBrowser(spec, candidatePath, '默认安装位置');
    }
    for (const command of spec.commands || []) {
      const executable = findExecutableOnPath(command);
      if (executable) {
        pushBrowser(spec, executable, 'PATH');
      }
    }
  }
  return browsers;
}

function appSettingsPath() {
  return path.join(app.getPath('userData'), SETTINGS_FILE);
}

function normalizeAppSettings(settings) {
  const next = settings && typeof settings === 'object' ? { ...settings } : {};
  if (!Number.isInteger(next.schemaVersion) || next.schemaVersion < 1) {
    next.schemaVersion = SETTINGS_SCHEMA_VERSION;
  }
  return next;
}

function readAppSettings() {
  try {
    const filePath = appSettingsPath();
    if (!fs.existsSync(filePath)) return normalizeAppSettings({});
    const settings = JSON.parse(fs.readFileSync(filePath, 'utf-8'));
    return normalizeAppSettings(settings);
  } catch (_error) {
    return normalizeAppSettings({});
  }
}

function writeAppSettings(settings) {
  writePrivateTextAtomic(appSettingsPath(), JSON.stringify(normalizeAppSettings(settings), null, 2));
}

function publicAppSettings(settings = readAppSettings()) {
  return {
    schemaVersion: settings.schemaVersion || SETTINGS_SCHEMA_VERSION,
    browserPath: settings.browserPath || '',
    updatedAt: settings.updatedAt || ''
  };
}

function selectedBrowserPath() {
  const settings = readAppSettings();
  const configured = normalizeBrowserExecutable(settings.browserPath || '');
  return configured || '';
}

function saveAppSettings(update) {
  const next = {
    ...readAppSettings()
  };
  next.schemaVersion = SETTINGS_SCHEMA_VERSION;
  if (Object.prototype.hasOwnProperty.call(update || {}, 'browserPath')) {
    const rawBrowserPath = String(update.browserPath || '').trim();
    if (rawBrowserPath) {
      const browserPath = normalizeBrowserExecutable(rawBrowserPath);
      if (!browserPath) {
        return { success: false, error: '没有找到这个浏览器文件，请选择 Chrome、Edge 或 Chromium 的可执行文件。' };
      }
      next.browserPath = browserPath;
    } else {
      delete next.browserPath;
    }
  }
  next.updatedAt = new Date().toISOString();
  writeAppSettings(next);
  return { success: true, settings: publicAppSettings(next) };
}

function providerRoots() {
  return uniquePaths([
    path.join(__dirname, '..', 'providers'),
    path.join(process.cwd(), 'providers'),
    path.join(app.getAppPath(), '..', 'providers'),
    path.join(process.resourcesPath || '', 'providers'),
    path.join(app.getPath('userData'), 'providers')
  ]);
}

function isInsidePath(root, candidate) {
  const rootPath = path.resolve(root);
  const targetPath = path.resolve(candidate);
  const left = process.platform === 'win32' ? rootPath.toLowerCase() : rootPath;
  const right = process.platform === 'win32' ? targetPath.toLowerCase() : targetPath;
  return right === left || right.startsWith(left + path.sep);
}

function managedFileRoots(options = {}) {
  const roots = [app.getPath('userData')];
  if (options.allowProjectRoot) {
    roots.push(pythonLibraryDir());
  }
  return uniquePaths(roots);
}

function resolveManagedFilePath(filePath, options = {}) {
  const raw = String(filePath || '').trim();
  if (!raw) {
    throw new Error('文件路径不能为空');
  }
  const resolved = path.resolve(expandHomePath(raw));
  const roots = managedFileRoots(options);
  if (!roots.some((root) => isInsidePath(root, resolved))) {
    throw new Error('只允许访问万能导应用数据目录中的配置文件。');
  }
  return resolved;
}

function readJsonFile(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf-8'));
}

function readGuideMarkdown(providerRoot, guidePath) {
  if (!guidePath) return '';
  const resolved = path.resolve(providerRoot, guidePath);
  if (!isInsidePath(providerRoot, resolved) || !fs.existsSync(resolved)) return '';
  const stat = fs.statSync(resolved);
  if (!stat.isFile() || stat.size > 512 * 1024) return '';
  return fs.readFileSync(resolved, 'utf-8');
}

function pluginScriptRef(providerId, scriptName, providerRoot, pluginInfo = null) {
  if (pluginInfo) {
    if (!scriptName || /^(?:plugin|bundled-plugin):/.test(String(scriptName))) return String(scriptName || '');
    const resolved = path.resolve(providerRoot, scriptName);
    if (!isInsidePath(pluginInfo.pluginRoot, resolved) || !fs.existsSync(resolved) || path.extname(resolved).toLowerCase() !== '.py') {
      return '';
    }
    const relative = path.relative(pluginInfo.pluginRoot, resolved).replace(/\\/g, '/');
    const prefix = pluginInfo.bundled ? 'bundled-plugin' : 'plugin';
    return `${prefix}:${pluginInfo.pluginId}:${relative}`;
  }
  if (!scriptName || String(scriptName).startsWith('provider:')) {
    return scriptName || '';
  }
  const resolved = path.resolve(providerRoot, scriptName);
  if (!isInsidePath(providerRoot, resolved) || !fs.existsSync(resolved)) {
    return '';
  }
  if (path.extname(resolved).toLowerCase() !== '.py') {
    return '';
  }
  return `provider:${providerId}:${scriptName.replace(/\\/g, '/')}`;
}

const PROVIDER_TYPES = new Set(['automation', 'guide', 'hybrid']);
const PROVIDER_GROUPS = new Set(['export', 'import', 'guide']);
const PROVIDER_TRUST_LEVELS = new Set(['official', 'community', 'local', 'experimental', 'guide']);
const PROVIDER_STATUSES = new Set(['stable', 'beta', 'experimental']);
const PROVIDER_FIELD_TYPES = new Set(['text', 'password', 'number', 'textarea', 'directory', 'file', 'checkbox', 'select', 'notice']);
const PROVIDER_ACTION_KINDS = new Set(['login', 'scan', 'export', 'import', 'plan', 'check', 'custom']);
const PROVIDER_NAME_PATTERN = /^[a-zA-Z][a-zA-Z0-9_-]{0,63}$/;

function assertProviderManifest(condition, message) {
  if (!condition) throw new Error(message);
}

function validateProviderManifestRuntime(raw, providerRoot) {
  assertProviderManifest(raw && typeof raw === 'object' && !Array.isArray(raw), 'provider.json 根节点必须是对象');
  assertProviderManifest(raw.schemaVersion === 1, '只支持 schemaVersion=1');
  const id = String(raw.id || '').trim();
  assertProviderManifest(/^[a-z0-9][a-z0-9_-]{1,63}$/i.test(id), `Provider ID 不合法：${id || '(空)'}`);
  assertProviderManifest(path.basename(providerRoot) === id, `Provider 目录名必须和 ID 一致：${path.basename(providerRoot)} != ${id}`);
  for (const key of ['name', 'title', 'description', 'type', 'group', 'trustLevel', 'status']) {
    assertProviderManifest(typeof raw[key] === 'string' && raw[key].trim(), `缺少必填字段：${key}`);
  }
  assertProviderManifest(PROVIDER_TYPES.has(raw.type), `不支持的 Provider 类型：${raw.type}`);
  assertProviderManifest(PROVIDER_GROUPS.has(raw.group), `不支持的 Provider 分组：${raw.group}`);
  assertProviderManifest(PROVIDER_TRUST_LEVELS.has(raw.trustLevel), `不支持的信任等级：${raw.trustLevel}`);
  assertProviderManifest(PROVIDER_STATUSES.has(raw.status), `不支持的 Provider 状态：${raw.status}`);
  assertProviderManifest(raw.capabilities && typeof raw.capabilities === 'object' && !Array.isArray(raw.capabilities), 'capabilities 必须是对象');
  for (const [key, value] of Object.entries(raw.capabilities)) {
    assertProviderManifest(typeof value === 'boolean', `capabilities.${key} 必须是布尔值`);
  }
  if (raw.capabilities.retryFailures) {
    assertProviderManifest(
      raw.retryFailures && typeof raw.retryFailures.arg === 'string' && raw.retryFailures.arg.startsWith('--'),
      'capabilities.retryFailures=true 时必须声明 retryFailures.arg'
    );
  }
  if (raw.type === 'guide' || raw.type === 'hybrid') {
    const guidePath = String(raw.guide || raw.guidePath || '').trim();
    assertProviderManifest(Boolean(guidePath), 'guide/hybrid Provider 必须声明 guide');
    const resolvedGuide = path.resolve(providerRoot, guidePath);
    assertProviderManifest(isInsidePath(providerRoot, resolvedGuide) && fs.existsSync(resolvedGuide), `guide 文件不存在或路径越界：${guidePath}`);
  }

  const fields = raw.fields ?? [];
  assertProviderManifest(Array.isArray(fields), 'fields 必须是数组');
  const fieldNames = new Set();
  fields.forEach((field, index) => {
    assertProviderManifest(field && typeof field === 'object' && !Array.isArray(field), `fields[${index}] 必须是对象`);
    assertProviderManifest(PROVIDER_NAME_PATTERN.test(String(field.name || '')), `fields[${index}].name 不合法`);
    assertProviderManifest(!fieldNames.has(field.name), `字段名重复：${field.name}`);
    fieldNames.add(field.name);
    assertProviderManifest(PROVIDER_FIELD_TYPES.has(field.type || 'text'), `fields[${index}].type 不支持：${field.type}`);
  });

  const actions = raw.actions ?? [];
  assertProviderManifest(Array.isArray(actions), 'actions 必须是数组');
  assertProviderManifest(raw.type === 'guide' || actions.length > 0, '非教程型 Provider 至少需要一个 action');
  const actionIds = new Set();
  actions.forEach((action, index) => {
    assertProviderManifest(action && typeof action === 'object' && !Array.isArray(action), `actions[${index}] 必须是对象`);
    const actionId = String(action.id || '');
    assertProviderManifest(PROVIDER_NAME_PATTERN.test(actionId), `actions[${index}].id 不合法`);
    assertProviderManifest(!actionIds.has(actionId), `动作 ID 重复：${actionId}`);
    actionIds.add(actionId);
    assertProviderManifest(typeof action.label === 'string' && action.label.trim(), `actions[${index}].label 不能为空`);
    if (action.kind !== undefined) {
      assertProviderManifest(PROVIDER_ACTION_KINDS.has(action.kind), `actions[${index}].kind 不支持：${action.kind}`);
    }
    assertProviderManifest(action.args === undefined || (Array.isArray(action.args) && action.args.every((item) => typeof item === 'string')), `actions[${index}].args 必须是字符串数组`);
    assertProviderManifest(Boolean(action.script || raw.script), `actions[${index}].script 不能为空`);
  });
  if (raw.type !== 'guide' && raw.capabilities.scanToc) {
    assertProviderManifest(
      actions.some((action) => action && (action.kind === 'scan' || action.id === 'scan')),
      'capabilities.scanToc=true 时必须提供 scan action'
    );
  }
  return id;
}

function normalizeProviderManifest(raw, providerRoot, sourceKind, pluginInfo = null) {
  const id = validateProviderManifestRuntime(raw, providerRoot);
  const defaultScript = raw.script ? pluginScriptRef(id, raw.script, providerRoot, pluginInfo) : '';
  if (raw.script && !defaultScript) {
    throw new Error(`Provider 默认脚本无效：${raw.script}`);
  }
  const provider = {
    ...raw,
    id,
    sourceKind,
    trustLevel: raw.trustLevel || (sourceKind === 'user' ? 'local' : 'community'),
    status: raw.status || 'experimental',
    templateId: raw.templateId || '',
    guideMarkdown: readGuideMarkdown(providerRoot, raw.guide || raw.guidePath || 'README.md'),
    pluginId: pluginInfo?.pluginId || '',
    pluginVersion: pluginInfo?.pluginVersion || '',
    pluginPermissions: pluginInfo?.permissions || [],
    pluginVerified: Boolean(pluginInfo?.verified)
  };
  if (pluginInfo && raw.ui?.mode === 'custom' && raw.ui.entry) {
    const uiPath = path.resolve(providerRoot, raw.ui.entry);
    if (!isInsidePath(pluginInfo.pluginRoot, uiPath) || !fs.existsSync(uiPath) || path.extname(uiPath).toLowerCase() !== '.html') {
      throw new Error(`自定义 UI 文件不存在或路径越界：${raw.ui.entry}`);
    }
    const relativeUiPath = path.relative(pluginInfo.pluginRoot, uiPath).replace(/\\/g, '/');
    if (!pluginInfo.uiEntry || pluginInfo.uiEntry !== relativeUiPath) {
      throw new Error('自定义 UI 必须在 plugin.json 的 entrypoints.ui 中显式声明');
    }
    provider.ui = {
      mode: 'custom',
      entry: relativeUiPath
    };
  }
  if (Array.isArray(raw.actions)) {
    provider.actions = raw.actions.map((action, index) => {
      const declaredScript = action && action.script ? action.script : raw.script;
      const actionScript = pluginScriptRef(id, declaredScript, providerRoot, pluginInfo);
      if (!actionScript) {
        throw new Error(`actions[${index}].script 无效：${declaredScript}`);
      }
      return { ...action, script: actionScript };
    });
  }
  provider.script = resolveProviderScript(defaultScript, provider.actions);
  // Bundled Provider v1 manifests keep their CLI field definitions in
  // fields[].arg. Some established built-in templates still read the older
  // urlParam/outputParam/noUrl properties, so project the validated fields
  // back into that compatibility surface instead of emitting "undefined".
  Object.assign(provider, resolveLegacyTemplateConfig(raw));
  return provider;
}

function discoverProviderManifests() {
  const providers = [];
  const errors = [];
  const seen = new Set();
  const installedPluginDiscovery = pluginManager().providerEntriesWithErrors();
  errors.push(...installedPluginDiscovery.errors.map((message) => `插件校验失败：${message}`));
  const bundledPluginDiscovery = bundledPluginEntriesWithErrors();
  errors.push(...bundledPluginDiscovery.errors.map((message) => `内置插件校验失败：${message}`));
  const bundledVersions = new Map(
    bundledPluginDiscovery.entries.map((entry) => [entry.pluginId, entry.pluginVersion])
  );
  const preferredInstalledDiscovery = {
    ...installedPluginDiscovery,
    entries: installedPluginDiscovery.entries.filter((entry) => {
      const bundledVersion = bundledVersions.get(entry.pluginId);
      return !bundledVersion || compareVersions(entry.pluginVersion, bundledVersion) > 0;
    })
  };
  for (const [sourceKind, discovery] of [
    ['plugin', preferredInstalledDiscovery],
    ['bundled-plugin', bundledPluginDiscovery]
  ]) {
    for (const entry of discovery.entries) {
      try {
        const providerRoot = path.dirname(entry.manifestPath);
        const manifest = normalizeProviderManifest(readJsonFile(entry.manifestPath), providerRoot, sourceKind, entry);
        if (seen.has(manifest.id)) {
          errors.push(`${entry.manifestPath}：Provider ID 冲突，已忽略 ${manifest.id}`);
          continue;
        }
        seen.add(manifest.id);
        providers.push(manifest);
      } catch (error) {
        console.warn(`Failed to load ${sourceKind} provider ${entry.manifestPath}:`, error);
        errors.push(`${entry.manifestPath}：${error.message || String(error)}`);
      }
    }
  }
  const userProviderRoot = path.join(app.getPath('userData'), 'providers');
  for (const root of providerRoots()) {
    if (!fs.existsSync(root)) continue;
    const sourceKind = isInsidePath(userProviderRoot, root) ? 'user' : 'bundled';
    for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
      if (!entry.isDirectory() || entry.name.startsWith('_') || entry.name.startsWith('.')) continue;
      const providerRoot = path.join(root, entry.name);
      const manifestPath = path.join(providerRoot, 'provider.json');
      if (!fs.existsSync(manifestPath)) continue;
      try {
        const manifest = normalizeProviderManifest(readJsonFile(manifestPath), providerRoot, sourceKind);
        if (seen.has(manifest.id)) {
          errors.push(`${manifestPath}：Provider ID 冲突，已忽略 ${manifest.id}`);
          continue;
        }
        seen.add(manifest.id);
        providers.push(manifest);
      } catch (error) {
        console.warn(`Failed to load provider manifest ${manifestPath}:`, error);
        errors.push(`${manifestPath}：${error.message || String(error)}`);
      }
    }
  }
  return { providers, errors };
}

function findPluginScript(scriptName) {
  const match = String(scriptName || '').match(/^plugin:([a-z0-9_-]+):(.+)$/i);
  if (!match) throw new Error(`不允许执行的插件脚本：${scriptName}`);
  return pluginManager().resolveScript(match[1], match[2]).path;
}

function findBundledPluginScript(scriptName) {
  const match = String(scriptName || '').match(/^bundled-plugin:([a-z0-9_-]+):(.+)$/i);
  if (!match) throw new Error(`不允许执行的内置插件脚本：${scriptName}`);
  const bundled = readBundledPlugin(match[1]);
  if (!bundled) throw new Error(`无法找到内置插件：${match[1]}`);
  if (!(bundled.manifest.permissions || []).includes('process')) {
    throw new Error(`内置插件没有声明运行进程权限：${match[1]}`);
  }
  const safe = assertSafeRelativePath(match[2], '内置插件脚本');
  const target = path.resolve(bundled.pluginRoot, ...safe.split('/'));
  if (!isInsidePath(bundled.pluginRoot, target) || !fs.existsSync(target) || path.extname(target).toLowerCase() !== '.py') {
    throw new Error(`内置插件脚本不存在或类型不允许：${match[2]}`);
  }
  return { path: target, root: bundled.pluginRoot, manifest: bundled.manifest };
}

function findProviderScript(scriptName) {
  const match = String(scriptName || '').match(/^provider:([a-z0-9_-]+):(.+)$/i);
  if (!match) {
    throw new Error(`不允许执行的脚本：${scriptName}`);
  }
  const providerId = match[1];
  const relativeScript = match[2];
  for (const root of providerRoots()) {
    const providerRoot = path.join(root, providerId);
    if (!fs.existsSync(providerRoot)) continue;
    const scriptPath = path.resolve(providerRoot, relativeScript);
    if (!isInsidePath(providerRoot, scriptPath)) {
      throw new Error(`插件脚本路径越界：${relativeScript}`);
    }
    if (fs.existsSync(scriptPath) && path.extname(scriptPath).toLowerCase() === '.py') {
      return scriptPath;
    }
  }
  throw new Error(`无法找到插件脚本：${scriptName}`);
}

function findPythonScript(scriptName = 'import_yuque.py') {
  if (String(scriptName || '').startsWith('plugin:')) {
    return findPluginScript(scriptName);
  }
  if (String(scriptName || '').startsWith('bundled-plugin:')) {
    return findBundledPluginScript(scriptName).path;
  }
  if (String(scriptName || '').startsWith('provider:')) {
    return findProviderScript(scriptName);
  }
  throw new Error(`平台脚本必须来自 Plugin v1 或文件型 Provider：${scriptName}`);
}

function bundledPythonInfo() {
  const executable = process.platform === 'win32' ? 'python.exe' : path.join('bin', 'python3');
  const possibleRoots = [
    path.join(process.resourcesPath || '', 'python-runtime'),
    path.join(__dirname, 'runtime', 'python-runtime'),
    path.join(app.getAppPath(), 'runtime', 'python-runtime')
  ];

  for (const root of possibleRoots) {
    if (!root) {
      continue;
    }
    const command = path.join(root, executable);
    if (fs.existsSync(command)) {
      return { command, root };
    }
  }

  return null;
}

function pythonCommand() {
  const configuredPython = process.env.WANDAO_PYTHON || process.env.PYTHON;
  if (configuredPython) {
    return configuredPython;
  }
  const bundledPython = bundledPythonInfo();
  if (bundledPython) {
    return bundledPython.command;
  }
  return process.platform === 'win32' ? 'python' : 'python3';
}

function pythonLibraryDir() {
  const candidates = uniquePaths([
    path.join(__dirname, '..'),
    process.cwd(),
    path.join(app.getAppPath(), '..'),
    path.join(process.resourcesPath || '', 'python')
  ]);
  for (const candidate of candidates) {
    if (fs.existsSync(path.join(candidate, 'wandao_logging.py'))) return candidate;
  }
  return '';
}

const PLUGIN_ENV_ALLOWLIST = new Set([
  'PATH', 'PATHEXT', 'SYSTEMROOT', 'WINDIR', 'COMSPEC',
  'TEMP', 'TMP', 'TMPDIR', 'HOME', 'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH',
  'APPDATA', 'LOCALAPPDATA', 'PROGRAMDATA', 'LANG', 'LC_ALL', 'TZ'
]);

function pluginHostEnvironment() {
  const allowed = {};
  for (const [key, value] of Object.entries(process.env)) {
    if (PLUGIN_ENV_ALLOWLIST.has(key.toUpperCase())) allowed[key] = value;
  }
  return allowed;
}

function pythonEnv(extra = {}, baseEnvironment = process.env) {
  const env = {
    ...baseEnvironment,
    PYTHONIOENCODING: 'utf-8',
    PYTHONUNBUFFERED: '1',
    PYTHONUTF8: '1',
    WANDAO_DATA_DIR: app.getPath('userData'),
    WANDAO_STRUCTURED_LOGS: '1',
    ...extra
  };
  const browserPath = selectedBrowserPath();
  if (browserPath) {
    env.WANDAO_BROWSER = browserPath;
  }
  const bundledPython = bundledPythonInfo();
  if (bundledPython) {
    const binDir = process.platform === 'win32' ? bundledPython.root : path.join(bundledPython.root, 'bin');
    const scriptsDir = process.platform === 'win32' ? path.join(bundledPython.root, 'Scripts') : path.join(bundledPython.root, 'bin');
    env.PATH = [binDir, scriptsDir, env.PATH].filter(Boolean).join(path.delimiter);
    env.PYTHONNOUSERSITE = '1';
    env.WANDAO_PYTHON_RUNTIME = bundledPython.root;
  }
  return env;
}

function pluginExecutionContext(scriptName) {
  const value = String(scriptName || '');
  const installedMatch = value.match(/^plugin:([a-z0-9_-]+):(.+)$/i);
  const bundledMatch = value.match(/^bundled-plugin:([a-z0-9_-]+):(.+)$/i);
  const match = installedMatch || bundledMatch;
  if (!match) return null;
  const resolved = installedMatch
    ? pluginManager().resolveScript(match[1], match[2])
    : (() => {
      const bundled = findBundledPluginScript(value);
      return {
        path: bundled.path,
        root: bundled.root,
        plugin: { currentVersion: bundled.manifest.version, manifest: bundled.manifest }
      };
    })();
  const dataDir = path.join(app.getPath('userData'), 'plugin-data', match[1]);
  fs.mkdirSync(dataDir, { recursive: true });
  return { ...resolved, pluginId: match[1], dataDir, bundled: Boolean(bundledMatch) };
}

function executionEnv(options, pluginContext = null, secretEnvironment = {}) {
  const coreLibrary = pythonLibraryDir();
  const pluginPaths = pluginContext ? [pluginContext.root, coreLibrary] : [coreLibrary];
  const env = pythonEnv({
    WANDAO_TASK_ID: options?.runId || options?.taskId || '',
    WANDAO_RUN_ID: options?.runId || options?.taskId || '',
    WANDAO_JOB_ID: options?.jobId || '',
    WANDAO_PARENT_RUN_ID: options?.parentRunId || '',
    WANDAO_PROVIDER_ID: options?.providerId || '',
    ...secretEnvironment,
    PYTHONPATH: [...pluginPaths, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter)
  }, pluginContext ? pluginHostEnvironment() : process.env);
  if (!pluginContext) return env;
  migrateLegacyPluginState({
    pluginId: pluginContext.pluginId,
    legacyRoot: app.getPath('userData'),
    dataRoot: pluginContext.dataDir
  });
  migrateLegacyPluginState({
    pluginId: pluginContext.pluginId,
    legacyRoot: pythonLibraryDir(),
    dataRoot: pluginContext.dataDir
  });
  env.WANDAO_PLUGIN_ID = pluginContext.pluginId;
  env.WANDAO_PLUGIN_VERSION = pluginContext.plugin.currentVersion;
  env.WANDAO_PLUGIN_ROOT = pluginContext.root;
  env.WANDAO_PLUGIN_DATA_DIR = pluginContext.dataDir;
  env.WANDAO_DATA_DIR = pluginContext.dataDir;
  env.WANDAO_PLUGIN_PERMISSIONS = JSON.stringify(pluginContext.plugin.manifest.permissions || []);
  return env;
}

function pluginRegistryUrl(channel = 'stable') {
  if (channel === 'experimental') return EXPERIMENTAL_PLUGIN_REGISTRY_URL;
  if (channel === 'stable') return PLUGIN_REGISTRY_URL;
  throw new Error(`未知插件发布等级：${channel}`);
}

async function currentPluginRegistry(force = false, channel = 'stable') {
  const cached = pluginRegistryCache.get(channel);
  if (!force && cached && Date.now() - cached.cachedAt < 5 * 60 * 1000) return cached.registry;
  const registry = await pluginManager().fetchRegistry(pluginRegistryUrl(channel));
  const tagged = {
    ...registry,
    plugins: registry.plugins.map((plugin) => ({ ...plugin, channel: plugin.channel || channel }))
  };
  pluginRegistryCache.set(channel, { registry: tagged, cachedAt: Date.now() });
  return tagged;
}

function commandLineLength(args) {
  return (args || []).reduce((total, value) => total + String(value || '').length + 3, 0);
}

function compressDocIdArgs(scriptName, args) {
  const scriptBaseName = path.basename(String(scriptName || '').split(':').pop() || '');
  const docIds = [];
  const compactArgs = [];
  for (let index = 0; index < (args || []).length; index += 1) {
    const value = String(args[index]);
    if (value === '--doc-id' && index + 1 < args.length) {
      docIds.push(String(args[index + 1]));
      index += 1;
    } else {
      compactArgs.push(value);
    }
  }

  if (!docIds.length || (docIds.length < 50 && commandLineLength(args) < 12000)) {
    return args;
  }

  const tmpDir = path.join(app.getPath('userData'), 'tmp');
  fs.mkdirSync(tmpDir, { recursive: true });
  const prefix = path.basename(scriptBaseName, '.py').replace(/^export_/, '');
  const fileName = `${prefix}-doc-ids-${Date.now()}-${Math.random().toString(36).slice(2, 8)}.json`;
  const filePath = path.join(tmpDir, fileName);
  fs.writeFileSync(filePath, JSON.stringify({ docIds }, null, 2), 'utf-8');
  return [...compactArgs, '--doc-id-file', filePath];
}

function cleanupTemporaryDocIdFile(args) {
  const values = Array.isArray(args) ? args : [];
  const index = values.indexOf('--doc-id-file');
  if (index < 0 || index + 1 >= values.length) return;
  const candidate = path.resolve(String(values[index + 1] || ''));
  const tmpRoot = path.join(app.getPath('userData'), 'tmp');
  if (!isInsidePath(tmpRoot, candidate) || !/-doc-ids-\d+-[a-z0-9]+\.json$/i.test(path.basename(candidate))) return;
  try {
    fs.unlinkSync(candidate);
  } catch (_error) {
    // The script may already have removed the temporary selection file.
  }
}

function createWindow() {
  const icon = appIconPath();
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 900,
    minHeight: 600,
    icon,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      preload: path.join(__dirname, 'preload.js')
    },
    backgroundColor: '#f7f5f1',
    show: false
  });

  mainWindow.loadFile('renderer/index.html');
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (url !== mainWindow.webContents.getURL()) event.preventDefault();
  });
  mainWindow.webContents.session.setPermissionRequestHandler((_webContents, _permission, callback) => callback(false));

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
  });

  mainWindow.on('close', (event) => {
    if (!confirmTaskShutdown()) event.preventDefault();
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
    cleanupPythonProcess();
  });
}

function showAboutDialog() {
  const detail = [
    PROJECT_INFO.slogan,
    '',
    `版本：${PROJECT_INFO.version}`,
    `作者：${PROJECT_INFO.author}`,
    `GitHub：${PROJECT_INFO.github}`,
    `微信：${PROJECT_INFO.wechat}`,
    '',
    '请只处理自己有权限访问的内容，并遵守目标平台服务条款。'
  ].join('\n');

  dialog.showMessageBox(mainWindow || undefined, {
    type: 'info',
    title: `关于 ${PROJECT_INFO.name}`,
    message: PROJECT_INFO.name,
    detail,
    buttons: ['知道了'],
    noLink: true
  });
}

function openProjectUrl(url) {
  if (!isAllowedExternalUrl(url)) {
    dialog.showErrorBox('打开链接失败', '只允许打开 HTTPS 链接。');
    return;
  }
  shell.openExternal(url).catch((error) => {
    dialog.showErrorBox('打开链接失败', error.message || String(error));
  });
}

function parseVersion(version) {
  return String(version || '')
    .replace(/^v/i, '')
    .split('.')
    .map((part) => Number.parseInt(part, 10))
    .map((part) => (Number.isFinite(part) ? part : 0));
}

function compareVersions(a, b) {
  const left = parseVersion(a);
  const right = parseVersion(b);
  const length = Math.max(left.length, right.length);
  for (let i = 0; i < length; i += 1) {
    const diff = (left[i] || 0) - (right[i] || 0);
    if (diff > 0) return 1;
    if (diff < 0) return -1;
  }
  return 0;
}

function fetchJson(url) {
  return new Promise((resolve, reject) => {
    const request = https.get(url, {
      headers: {
        Accept: 'application/vnd.github+json',
        'User-Agent': 'wandao-update-checker'
      },
      timeout: 12000
    }, (response) => {
      let body = '';
      response.setEncoding('utf8');
      response.on('data', (chunk) => {
        body += chunk;
      });
      response.on('end', () => {
        if (response.statusCode < 200 || response.statusCode >= 300) {
          reject(new Error(`GitHub 返回 HTTP ${response.statusCode}`));
          return;
        }
        try {
          resolve(JSON.parse(body));
        } catch (error) {
          reject(new Error(`解析更新信息失败：${error.message}`));
        }
      });
    });
    request.on('timeout', () => {
      request.destroy(new Error('检查更新超时'));
    });
    request.on('error', reject);
  });
}

function isAllowedRemoteTextUrl(value) {
  try {
    const parsed = new URL(String(value || ''));
    if (parsed.protocol !== 'https:') return false;
    if (parsed.hostname === 'raw.githubusercontent.com') {
      return parsed.pathname.startsWith('/tllovesxs/wandao/');
    }
    if (parsed.hostname === 'github.com') {
      return parsed.pathname.startsWith('/tllovesxs/wandao/');
    }
    return false;
  } catch (_error) {
    return false;
  }
}

function isAllowedExternalUrl(value) {
  try {
    const parsed = new URL(String(value || ''));
    return parsed.protocol === 'https:';
  } catch (_error) {
    return false;
  }
}

function fetchText(url) {
  if (!isAllowedRemoteTextUrl(url)) {
    return Promise.reject(new Error('只允许读取万能导 GitHub 仓库中的公告和教程文档'));
  }
  return new Promise((resolve, reject) => {
    const request = https.get(url, {
      headers: {
        Accept: 'text/plain, application/json, text/markdown, */*',
        'User-Agent': 'wandao-docs-center'
      },
      timeout: 12000
    }, (response) => {
      let body = '';
      response.setEncoding('utf8');
      response.on('data', (chunk) => {
        body += chunk;
        if (body.length > 1024 * 1024) {
          request.destroy(new Error('公告文档超过 1MB，已停止读取'));
        }
      });
      response.on('end', () => {
        if (response.statusCode < 200 || response.statusCode >= 300) {
          reject(new Error(`GitHub 返回 HTTP ${response.statusCode}`));
          return;
        }
        resolve(body);
      });
    });
    request.on('timeout', () => {
      request.destroy(new Error('读取 GitHub 文档超时'));
    });
    request.on('error', reject);
  });
}

async function checkForUpdates() {
  const release = await fetchJson(PROJECT_INFO.latestReleaseApi);
  const latestVersion = String(release.tag_name || '').replace(/^v/i, '') || '0.0.0';
  const currentVersion = PROJECT_INFO.version;
  return {
    currentVersion,
    latestVersion,
    latestTag: release.tag_name || `v${latestVersion}`,
    releaseUrl: release.html_url || PROJECT_INFO.releases,
    releaseName: release.name || release.tag_name || latestVersion,
    publishedAt: release.published_at || '',
    hasUpdate: compareVersions(latestVersion, currentVersion) > 0
  };
}

function buildApplicationMenu() {
  const template = [
    {
      label: '文件',
      submenu: [
        {
          label: '停止当前任务',
          click: requestPythonStop
        },
        { type: 'separator' },
        process.platform === 'darwin'
          ? { role: 'close', label: '关闭窗口' }
          : { role: 'quit', label: '退出' }
      ]
    },
    {
      label: '编辑',
      submenu: [
        { role: 'undo', label: '撤销' },
        { role: 'redo', label: '重做' },
        { type: 'separator' },
        { role: 'cut', label: '剪切' },
        { role: 'copy', label: '复制' },
        { role: 'paste', label: '粘贴' },
        { role: 'selectAll', label: '全选' }
      ]
    },
    {
      label: '视图',
      submenu: [
        { role: 'reload', label: '刷新' },
        { role: 'toggleDevTools', label: '开发者工具' },
        { type: 'separator' },
        { role: 'resetZoom', label: '实际大小' },
        { role: 'zoomIn', label: '放大' },
        { role: 'zoomOut', label: '缩小' },
        { type: 'separator' },
        { role: 'togglefullscreen', label: '全屏' }
      ]
    },
    {
      label: '帮助',
      submenu: [
        {
          label: '新手模式 / 使用教程',
          click: () => openProjectUrl(PROJECT_INFO.docs)
        },
        { type: 'separator' },
        {
          label: '项目主页 GitHub',
          click: () => openProjectUrl(PROJECT_INFO.github)
        },
        {
          label: '下载发行版',
          click: () => openProjectUrl(PROJECT_INFO.releases)
        },
        {
          label: '检查更新',
          click: async () => {
            try {
              const result = await checkForUpdates();
              if (mainWindow) {
                mainWindow.webContents.send('app-info', result.hasUpdate
                  ? `发现新版本：v${result.latestVersion}`
                  : `当前已是最新版本：v${result.currentVersion}`);
              }
            } catch (error) {
              if (mainWindow) {
                mainWindow.webContents.send('app-info', `检查更新失败：${error.message || String(error)}`);
              }
            }
          }
        },
        {
          label: '问题反馈',
          click: () => openProjectUrl(PROJECT_INFO.issues)
        },
        { type: 'separator' },
        {
          label: '复制微信号',
          click: () => {
            clipboard.writeText(PROJECT_INFO.wechat);
            if (mainWindow) {
              mainWindow.webContents.send('app-info', `已复制微信号：${PROJECT_INFO.wechat}`);
            }
          }
        },
        {
          label: `关于 ${PROJECT_INFO.name}`,
          click: showAboutDialog
        }
      ]
    }
  ];

  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

app.whenReady().then(() => {
  if (!hasSingleInstanceLock) return;
  configureAppIdentity();
  buildApplicationMenu();
  createWindow();
}).catch((error) => {
  dialog.showErrorBox('万能导启动失败', error.message || String(error));
  app.quit();
});

app.on('second-instance', () => {
  if (!mainWindow) {
    createWindow();
    return;
  }
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('before-quit', (event) => {
  if (!confirmTaskShutdown()) event.preventDefault();
});

app.on('activate', () => {
  if (hasSingleInstanceLock && BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});

// IPC handlers
ipcMain.handle('select-directory', async (event, options) => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openDirectory'],
    title: options?.title || '选择目录',
    defaultPath: options?.defaultPath || ''
  });

  return result.canceled ? null : result.filePaths[0];
});

ipcMain.handle('select-file', async (event, options) => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openFile'],
    title: options?.title || '选择文件',
    defaultPath: options?.defaultPath || '',
    filters: options?.filters || []
  });

  return result.canceled ? null : result.filePaths[0];
});

ipcMain.handle('select-browser-file', async () => {
  const properties = process.platform === 'darwin'
    ? ['openFile', 'openDirectory', 'treatPackageAsDirectory']
    : ['openFile'];
  const result = await dialog.showOpenDialog(mainWindow, {
    properties,
    title: '选择浏览器',
    filters: process.platform === 'win32'
      ? [
        { name: '浏览器可执行文件', extensions: ['exe'] },
        { name: '所有文件', extensions: ['*'] }
      ]
      : []
  });

  if (result.canceled || !result.filePaths[0]) {
    return { success: false, canceled: true };
  }
  const browserPath = normalizeBrowserExecutable(result.filePaths[0]);
  if (!browserPath) {
    return { success: false, error: '请选择 Chrome、Edge 或 Chromium 的可执行文件。' };
  }
  return { success: true, path: browserPath };
});

ipcMain.handle('save-file', async (event, options) => {
  const result = await dialog.showSaveDialog(mainWindow, {
    title: options?.title || '保存文件',
    defaultPath: options?.defaultPath || '',
    filters: options?.filters || []
  });

  return result.canceled ? null : result.filePath;
});

ipcMain.handle('fetch-remote-text', async (event, url) => {
  try {
    const content = await fetchText(url);
    return { success: true, content };
  } catch (error) {
    return { success: false, error: error.message || String(error) };
  }
});

ipcMain.handle('run-python-command', async (event, scriptName, args, options = {}) => {
  return new Promise((resolve, reject) => {
    if (pythonProcess) {
      resolve({ success: false, error: '已有任务正在运行，请先停止当前任务或等待完成。' });
      return;
    }
    let scriptPath;
    let commandArgs;
    let pluginContext;
    try {
      pluginContext = pluginExecutionContext(scriptName);
      scriptPath = findPythonScript(scriptName);
      const prepared = extractSensitiveArguments(compressDocIdArgs(scriptName, args || []));
      commandArgs = prepared.commandArgs;
      options.commandSecretEnvironment = prepared.secretEnvironment;
    } catch (error) {
      resolve({ success: false, error: error.message || String(error) });
      return;
    }
    const pythonArgs = [scriptPath, ...commandArgs];
    const stopFile = path.join(app.getPath('userData'), 'runtime', 'stops', `${options.taskId || Date.now()}.stop`);
    try { fs.unlinkSync(stopFile); } catch (_error) { /* no stale marker */ }
    const env = executionEnv(options, pluginContext, options.commandSecretEnvironment || {});
    env.WANDAO_STOP_FILE = stopFile;

    const proc = spawn(pythonCommand(), pythonArgs, {
      cwd: path.dirname(scriptPath),
      stdio: ['pipe', 'pipe', 'pipe'],
      detached: process.platform !== 'win32',
      env
    });
    pythonProcess = proc;
    pythonProcessStopping = false;
    pythonStopFile = stopFile;
    pythonProcessMetadata = {
      providerId: String(options.providerId || ''),
      taskId: String(options.taskId || ''),
      startedAt: new Date().toISOString()
    };
    broadcastPythonProcessState();
    const sendPythonLog = (text) => {
      if (mainWindow) {
        mainWindow.webContents.send('python-log', text);
      }
    };
    proc.stdinWriteError = '';
    proc.stdin.on('error', (error) => {
      proc.stdinWriteError = error.message || String(error);
      sendPythonLog(`任务输入通道已关闭：${proc.stdinWriteError}`, 'warn');
    });

    if (options?.stdinText) {
      const stdinResult = writePythonInput(proc, options.stdinText, { end: true });
      if (!stdinResult.success) {
        sendPythonLog(`无法写入任务初始输入：${stdinResult.error}`, 'warn');
      }
    }

    let stdout = '';
    let stderr = '';
    let stdoutOmitted = 0;
    let stderrOmitted = 0;
    const scanStdoutRelay = commandArgs.includes('--scan-toc')
      ? createScanStdoutRelay(sendPythonLog)
      : null;

    proc.stdout.on('data', (data) => {
      const text = data.toString();
      const next = appendOutputTail(stdout, text);
      stdout = next.text;
      stdoutOmitted += next.omitted;
      if (scanStdoutRelay) {
        scanStdoutRelay.push(text);
      } else {
        sendPythonLog(text);
      }
    });

    proc.stderr.on('data', (data) => {
      const text = data.toString();
      const next = appendOutputTail(stderr, text);
      stderr = next.text;
      stderrOmitted += next.omitted;
      sendPythonLog(text);
    });

    proc.on('close', (code) => {
      scanStdoutRelay?.flush();
      cleanupTemporaryDocIdFile(commandArgs);
      const wasStopping = pythonProcess === proc && pythonProcessStopping;
      if (pythonProcess === proc) {
        const finishedMetadata = pythonProcessMetadata;
        pythonProcess = null;
        pythonProcessStopping = false;
        pythonStopFile = '';
        pythonProcessMetadata = null;
        broadcastPythonProcessState({
          running: false,
          stopping: false,
          providerId: finishedMetadata?.providerId || '',
          taskId: finishedMetadata?.taskId || '',
          lastStatus: wasStopping ? 'stopped' : 'finished'
        });
      }
      try { fs.unlinkSync(stopFile); } catch (_error) { /* marker is best-effort */ }
      if (code !== 0 && wasStopping) {
        const parsed = parseLastJson(stdout);
        resolve({
          success: false,
          error: '任务已由用户停止。',
          code: 130,
          data: parsed && Object.keys(parsed).length ? { ...parsed, stopped: true } : { stopped: true }
        });
        return;
      }
      if (code === 0) {
        const result = parseProcessResult(stdout);
        if (result.ok) {
          resolve({ success: true, data: result.data, legacyResult: result.legacy });
        } else {
          resolve({ success: false, error: result.error, code: 'protocol_error', data: null });
        }
      } else {
        const parsed = parseLastJson(stdout);
        resolve({
          success: false,
          error: stderr
            ? outputWithOmissionNotice(stderr, stderrOmitted)
            : (stdout ? outputWithOmissionNotice(stdout, stdoutOmitted) : `Python exited with code ${code}`),
          code,
          data: parsed && Object.keys(parsed).length ? parsed : null
        });
      }
    });

    proc.on('error', (error) => {
      cleanupTemporaryDocIdFile(commandArgs);
      const wasStopping = pythonProcess === proc && pythonProcessStopping;
      if (pythonProcess === proc) {
        const finishedMetadata = pythonProcessMetadata;
        pythonProcess = null;
        pythonProcessStopping = false;
        pythonStopFile = '';
        pythonProcessMetadata = null;
        broadcastPythonProcessState({
          running: false,
          stopping: false,
          providerId: finishedMetadata?.providerId || '',
          taskId: finishedMetadata?.taskId || '',
          lastStatus: wasStopping ? 'stopped' : 'failed'
        });
      }
      try { fs.unlinkSync(stopFile); } catch (_error) { /* marker is best-effort */ }
      resolve(wasStopping
        ? { success: false, error: '任务已由用户停止。', code: 130, data: { stopped: true } }
        : { success: false, error: error.message || String(error) });
    });
  });
});

ipcMain.handle('stop-python-process', async () => requestPythonStop());

ipcMain.handle('get-python-process-state', async () => currentPythonProcessState());

ipcMain.handle('protect-task-args', async (event, args) => protectTaskArgs(args));

ipcMain.handle('restore-task-args', async (event, payload) => restoreTaskArgs(payload));

ipcMain.handle('send-python-input', async (event, text) => {
  return writePythonInput(pythonProcess, text);
});

ipcMain.handle('read-file', async (event, filePath) => {
  try {
    const managedPath = resolveManagedFilePath(filePath, { allowProjectRoot: true });
    const content = fs.readFileSync(managedPath, 'utf-8');
    return { success: true, content };
  } catch (error) {
    return { success: false, error: error.message };
  }
});

ipcMain.handle('write-file', async (event, filePath, content) => {
  try {
    const managedPath = resolveManagedFilePath(filePath);
    writePrivateTextAtomic(managedPath, content);
    return { success: true };
  } catch (error) {
    return { success: false, error: error.message };
  }
});

ipcMain.handle('file-exists', async (event, filePath) => {
  try {
    return fs.existsSync(resolveManagedFilePath(filePath, { allowProjectRoot: true }));
  } catch (_error) {
    return false;
  }
});

ipcMain.handle('open-path', async (event, targetPath) => {
  const error = await shell.openPath(targetPath);
  return error ? { success: false, error } : { success: true };
});

ipcMain.handle('open-external', async (event, url) => {
  try {
    if (!isAllowedExternalUrl(url)) {
      return { success: false, error: '只允许打开 HTTPS 链接。' };
    }
    await shell.openExternal(url);
    return { success: true };
  } catch (error) {
    return { success: false, error: error.message };
  }
});

ipcMain.handle('show-about', async () => {
  showAboutDialog();
  return { success: true };
});

ipcMain.handle('check-for-updates', async () => {
  try {
    const result = await checkForUpdates();
    return { success: true, data: result };
  } catch (error) {
    return { success: false, error: error.message || String(error) };
  }
});

ipcMain.handle('get-app-settings', async () => {
  return { success: true, settings: publicAppSettings() };
});

ipcMain.handle('save-app-settings', async (event, update) => {
  const result = saveAppSettings(update || {});
  if (!result.success) return result;
  return {
    ...result,
    browsers: detectBrowsers(),
    downloadUrl: BROWSER_DOWNLOAD_URL
  };
});

ipcMain.handle('detect-browsers', async () => {
  return {
    success: true,
    browsers: detectBrowsers(),
    selectedBrowserPath: selectedBrowserPath(),
    downloadUrl: BROWSER_DOWNLOAD_URL
  };
});

ipcMain.handle('get-provider-manifests', async () => {
  try {
    return { success: true, ...discoverProviderManifests() };
  } catch (error) {
    return { success: false, error: error.message || String(error), providers: [] };
  }
});

ipcMain.handle('get-plugin-catalog', async (event, options = {}) => {
  try {
    const registry = await currentPluginRegistry(Boolean(options?.refresh), 'stable');
    const registries = [registry];
    let experimentalError = '';
    try {
      registries.push(await currentPluginRegistry(Boolean(options?.refresh), 'experimental'));
    } catch (error) {
      // Stable plugins remain usable when the experimental registry is offline.
      experimentalError = error.message || String(error);
    }
    const combined = {
      plugins: registries.flatMap((item) => item.plugins),
      generatedAt: registry.generatedAt || ''
    };
    return {
      success: true,
      plugins: pluginCatalogWithBundled(combined),
      registryUpdatedAt: combined.generatedAt,
      experimentalError
    };
  } catch (error) {
    return {
      success: true,
      plugins: pluginCatalogWithBundled(),
      registryError: error.message || String(error),
      offline: true
    };
  }
});

ipcMain.handle('install-plugin', async (event, pluginId, channel = 'stable') => {
  try {
    const registry = await currentPluginRegistry(true, channel === 'experimental' ? 'experimental' : 'stable');
    const plugin = await pluginManager().installFromRegistry(String(pluginId || ''), registry);
    return { success: true, plugin };
  } catch (error) {
    return { success: false, error: error.message || String(error) };
  }
});

ipcMain.handle('install-plugin-file', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: '安装万能导插件',
    properties: ['openFile'],
    filters: [{ name: 'Wandao Plugin', extensions: ['wandao-plugin'] }]
  });
  if (result.canceled || !result.filePaths[0]) return { success: false, canceled: true };
  try {
    return { success: true, plugin: pluginManager().installFile(result.filePaths[0]) };
  } catch (error) {
    return { success: false, error: error.message || String(error) };
  }
});

ipcMain.handle('set-plugin-enabled', async (event, pluginId, enabled) => {
  try {
    return { success: true, plugin: pluginManager().setEnabled(String(pluginId || ''), Boolean(enabled)) };
  } catch (error) {
    return { success: false, error: error.message || String(error) };
  }
});

ipcMain.handle('rollback-plugin', async (event, pluginId) => {
  try {
    return { success: true, plugin: pluginManager().rollback(String(pluginId || '')) };
  } catch (error) {
    return { success: false, error: error.message || String(error) };
  }
});

ipcMain.handle('uninstall-plugin', async (event, pluginId) => {
  try {
    return { success: true, removed: pluginManager().uninstall(String(pluginId || '')) };
  } catch (error) {
    return { success: false, error: error.message || String(error) };
  }
});

ipcMain.handle('get-plugin-ui', async (event, pluginId, entry) => {
  try {
    return { success: true, html: pluginManager().readUi(String(pluginId || ''), String(entry || '')) };
  } catch (error) {
    return { success: false, error: error.message || String(error) };
  }
});

ipcMain.handle('copy-text', async (event, text) => {
  clipboard.writeText(String(text || ''));
  return { success: true };
});

ipcMain.handle('get-app-path', async () => {
  const userData = app.getPath('userData');
  return {
    appPath: app.getAppPath(),
    userData,
    dataRoot: userData,
    projectRoot: pythonLibraryDir()
  };
});
