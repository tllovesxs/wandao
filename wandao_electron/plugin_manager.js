const fs = require('fs');
const http = require('http');
const https = require('https');
const path = require('path');
const {
  assertSafeRelativePath,
  compareVersions,
  sha256Hex,
  verifyPluginEnvelope,
  verifyRegistryEnvelope
} = require('./plugin_format');

const STATE_SCHEMA_VERSION = 1;
const MAX_DOWNLOAD_BYTES = 128 * 1024 * 1024;

function isInside(root, candidate) {
  const relative = path.relative(path.resolve(root), path.resolve(candidate));
  return relative === '' || (!relative.startsWith('..' + path.sep) && relative !== '..' && !path.isAbsolute(relative));
}

function writeJsonAtomic(filePath, value) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  const temporary = `${filePath}.${process.pid}.${Date.now()}.tmp`;
  fs.writeFileSync(temporary, JSON.stringify(value, null, 2), { encoding: 'utf8', mode: 0o600 });
  fs.renameSync(temporary, filePath);
}

function readJson(filePath, fallback = null) {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf8'));
  } catch (_error) {
    return fallback;
  }
}

function requestBuffer(url, options = {}, redirects = 0) {
  return new Promise((resolve, reject) => {
    if (redirects > 5) return reject(new Error('插件下载重定向次数过多'));
    const parsed = new URL(url);
    const localHttp = parsed.protocol === 'http:' && ['127.0.0.1', 'localhost'].includes(parsed.hostname);
    if (parsed.protocol !== 'https:' && !(options.allowLocalHttp && localHttp)) {
      return reject(new Error('插件只允许通过 HTTPS 下载'));
    }
    const transport = parsed.protocol === 'https:' ? https : http;
    const request = transport.get(parsed, {
      headers: { 'User-Agent': options.userAgent || 'Wandao-Plugin-Manager', Accept: 'application/json, application/octet-stream' },
      timeout: options.timeout || 30000
    }, (response) => {
      const status = response.statusCode || 0;
      if ([301, 302, 303, 307, 308].includes(status) && response.headers.location) {
        response.resume();
        return resolve(requestBuffer(new URL(response.headers.location, parsed).toString(), options, redirects + 1));
      }
      if (status < 200 || status >= 300) {
        response.resume();
        return reject(new Error(`插件下载失败 HTTP ${status}`));
      }
      const chunks = [];
      let size = 0;
      response.on('data', (chunk) => {
        size += chunk.length;
        if (size > (options.maxBytes || MAX_DOWNLOAD_BYTES)) {
          request.destroy(new Error('插件下载超过大小限制'));
          return;
        }
        chunks.push(chunk);
      });
      response.on('end', () => resolve(Buffer.concat(chunks)));
    });
    request.on('timeout', () => request.destroy(new Error('插件下载超时')));
    request.on('error', reject);
  });
}

class PluginManager {
  constructor(options) {
    if (!options?.rootDir || !options?.trustStore) throw new Error('PluginManager 缺少 rootDir 或 trustStore');
    this.rootDir = path.resolve(options.rootDir);
    this.pluginsDir = path.join(this.rootDir, 'installed');
    this.stateFile = path.join(this.rootDir, 'state.json');
    this.trustStore = options.trustStore;
    this.coreVersion = String(options.coreVersion || '0.0.0');
    this.platform = options.platform || process.platform;
    this.registryUrl = options.registryUrl || '';
    this.allowLocalHttp = Boolean(options.allowLocalHttp);
    this.verifiedInstallCache = new Map();
    fs.mkdirSync(this.pluginsDir, { recursive: true });
    this.recoverStagingDirectories();
  }

  defaultState() {
    return { schemaVersion: STATE_SCHEMA_VERSION, plugins: {}, updatedAt: new Date().toISOString() };
  }

  readState() {
    const state = readJson(this.stateFile, this.defaultState());
    if (!state || state.schemaVersion !== STATE_SCHEMA_VERSION || typeof state.plugins !== 'object') return this.defaultState();
    return state;
  }

  writeState(state) {
    state.schemaVersion = STATE_SCHEMA_VERSION;
    state.updatedAt = new Date().toISOString();
    writeJsonAtomic(this.stateFile, state);
  }

  pluginRoot(pluginId) {
    if (!/^[a-z0-9][a-z0-9_-]{1,63}$/.test(String(pluginId || ''))) throw new Error('插件 ID 不合法');
    const target = path.join(this.pluginsDir, pluginId);
    if (!isInside(this.pluginsDir, target)) throw new Error('插件路径越界');
    return target;
  }

  versionRoot(pluginId, version) {
    if (!/^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/.test(String(version || ''))) throw new Error('插件版本不合法');
    return path.join(this.pluginRoot(pluginId), version);
  }

  recoverStagingDirectories() {
    if (!fs.existsSync(this.pluginsDir)) return;
    for (const plugin of fs.readdirSync(this.pluginsDir, { withFileTypes: true })) {
      if (!plugin.isDirectory()) continue;
      const pluginDir = path.join(this.pluginsDir, plugin.name);
      for (const entry of fs.readdirSync(pluginDir, { withFileTypes: true })) {
        if (entry.isDirectory() && entry.name.startsWith('.staging-')) {
          fs.rmSync(path.join(pluginDir, entry.name), { recursive: true, force: true });
        }
      }
    }
  }

  compatibility(manifest) {
    if (manifest.core?.minVersion && compareVersions(this.coreVersion, manifest.core.minVersion) < 0) {
      return { compatible: false, reason: `需要万能导 ${manifest.core.minVersion} 或更高版本` };
    }
    if (manifest.platforms?.length && !manifest.platforms.includes(this.platform)) {
      return { compatible: false, reason: `插件不支持当前系统 ${this.platform}` };
    }
    return { compatible: true, reason: '' };
  }

  async fetchRegistry(url = this.registryUrl) {
    if (!url) throw new Error('尚未配置插件注册表地址');
    const content = await requestBuffer(url, { allowLocalHttp: this.allowLocalHttp, maxBytes: 4 * 1024 * 1024 });
    let registry;
    try {
      registry = JSON.parse(content.toString('utf8'));
    } catch (error) {
      throw new Error(`插件注册表不是有效 JSON：${error.message}`);
    }
    verifyRegistryEnvelope(registry, this.trustStore);
    return registry;
  }

  async installFromRegistry(pluginId, registry = null) {
    const index = registry || await this.fetchRegistry();
    const entry = index.plugins.find((item) => item.id === pluginId);
    if (!entry) throw new Error(`插件注册表中没有 ${pluginId}`);
    const buffer = await requestBuffer(entry.packageUrl, { allowLocalHttp: this.allowLocalHttp });
    if (sha256Hex(buffer) !== entry.sha256) throw new Error('插件下载文件的 SHA-256 与注册表不一致');
    return this.installBuffer(buffer, { registryEntry: entry });
  }

  installFile(filePath) {
    const resolved = path.resolve(String(filePath || ''));
    if (!fs.existsSync(resolved) || !fs.statSync(resolved).isFile()) throw new Error('插件包文件不存在');
    return this.installBuffer(fs.readFileSync(resolved), { sourceFile: resolved });
  }

  installBuffer(buffer, source = {}) {
    let envelope;
    try {
      envelope = JSON.parse(Buffer.from(buffer).toString('utf8'));
    } catch (error) {
      throw new Error(`插件包不是有效 JSON：${error.message}`);
    }
    const verified = verifyPluginEnvelope(envelope, this.trustStore);
    const compatibility = this.compatibility(verified.manifest);
    if (!compatibility.compatible) throw new Error(compatibility.reason);
    if (source.registryEntry && (source.registryEntry.id !== verified.manifest.id || source.registryEntry.version !== verified.manifest.version)) {
      throw new Error('插件包身份与注册表不一致');
    }
    const manifest = verified.manifest;
    const pluginDir = this.pluginRoot(manifest.id);
    fs.mkdirSync(pluginDir, { recursive: true });
    const target = this.versionRoot(manifest.id, manifest.version);
    const staging = path.join(pluginDir, `.staging-${manifest.version}-${process.pid}-${Date.now()}`);
    if (fs.existsSync(target)) {
      try {
        this.verifyInstalledVersion(manifest.id, manifest.version, { force: true });
      } catch (_error) {
        fs.rmSync(target, { recursive: true, force: true });
      }
    }
    if (!fs.existsSync(target)) {
      fs.mkdirSync(staging, { recursive: true });
      try {
        for (const [relativePath, content] of verified.files.entries()) {
          const output = path.resolve(staging, ...relativePath.split('/'));
          if (!isInside(staging, output)) throw new Error(`插件文件路径越界：${relativePath}`);
          fs.mkdirSync(path.dirname(output), { recursive: true });
          fs.writeFileSync(output, content);
        }
        writeJsonAtomic(path.join(staging, 'plugin.json'), manifest);
        writeJsonAtomic(path.join(staging, '.wandao-install.json'), {
          installedAt: new Date().toISOString(),
          integrity: verified.integrity,
          signer: verified.signer,
          signature: envelope.signature,
          filePaths: Array.from(verified.files.keys()).sort(),
          source
        });
        fs.renameSync(staging, target);
      } finally {
        if (fs.existsSync(staging)) fs.rmSync(staging, { recursive: true, force: true });
      }
    }

    const state = this.readState();
    const previous = state.plugins[manifest.id];
    const previousVersions = Array.from(new Set([
      ...(previous?.previousVersions || []),
      ...(previous?.currentVersion && previous.currentVersion !== manifest.version ? [previous.currentVersion] : [])
    ])).filter((item) => fs.existsSync(this.versionRoot(manifest.id, item)));
    state.plugins[manifest.id] = {
      id: manifest.id,
      enabled: true,
      currentVersion: manifest.version,
      previousVersions: previousVersions.slice(-3),
      installedAt: previous?.installedAt || new Date().toISOString(),
      updatedAt: new Date().toISOString()
    };
    this.writeState(state);
    this.verifiedInstallCache.delete(`${manifest.id}@${manifest.version}`);
    this.verifyInstalledVersion(manifest.id, manifest.version, { force: true });
    return this.describeInstalled(manifest.id);
  }

  verifyInstalledVersion(pluginId, version, options = {}) {
    const cacheKey = `${pluginId}@${version}`;
    if (!options.force && this.verifiedInstallCache.has(cacheKey)) return this.verifiedInstallCache.get(cacheKey);
    const root = this.versionRoot(pluginId, version);
    const manifest = readJson(path.join(root, 'plugin.json'));
    const receipt = readJson(path.join(root, '.wandao-install.json'));
    if (!manifest || manifest.id !== pluginId || manifest.version !== version || !receipt?.signature || !Array.isArray(receipt.filePaths)) {
      throw new Error(`插件安装记录无效：${pluginId}@${version}`);
    }
    const files = {};
    const fileHashes = new Map();
    for (const relativePath of receipt.filePaths) {
      const safe = assertSafeRelativePath(relativePath, '已安装插件文件');
      const absolute = path.resolve(root, ...safe.split('/'));
      if (!isInside(root, absolute) || !fs.existsSync(absolute) || !fs.statSync(absolute).isFile()) {
        throw new Error(`插件文件缺失：${pluginId}/${safe}`);
      }
      const content = fs.readFileSync(absolute);
      files[safe] = content.toString('base64');
      fileHashes.set(safe, sha256Hex(content));
    }
    const envelope = {
      formatVersion: 1,
      manifest,
      files,
      integrity: { algorithm: 'sha256', value: receipt.integrity },
      signature: receipt.signature
    };
    const verified = verifyPluginEnvelope(envelope, this.trustStore);
    const result = { ...verified, root, fileHashes };
    this.verifiedInstallCache.set(cacheKey, result);
    return result;
  }

  verifyInstalledFile(pluginId, version, relativePath) {
    const verified = this.verifyInstalledVersion(pluginId, version);
    const safe = assertSafeRelativePath(relativePath, '已安装插件文件');
    const expected = verified.fileHashes.get(safe);
    if (!expected) throw new Error(`文件不在已签名插件包中：${safe}`);
    const absolute = path.resolve(verified.root, ...safe.split('/'));
    if (!isInside(verified.root, absolute) || !fs.existsSync(absolute) || sha256Hex(fs.readFileSync(absolute)) !== expected) {
      this.verifiedInstallCache.delete(`${pluginId}@${version}`);
      throw new Error(`插件文件在安装后被修改：${pluginId}/${safe}`);
    }
    return absolute;
  }

  describeInstalled(pluginId) {
    const state = this.readState().plugins[pluginId];
    if (!state) return null;
    const root = this.versionRoot(pluginId, state.currentVersion);
    const manifest = readJson(path.join(root, 'plugin.json'));
    if (!manifest) return null;
    return { ...state, manifest, compatibility: this.compatibility(manifest) };
  }

  listInstalled() {
    const state = this.readState();
    return Object.keys(state.plugins).sort().map((id) => this.describeInstalled(id)).filter(Boolean);
  }

  listWithRegistry(registry = null) {
    const installed = new Map(this.listInstalled().map((item) => [item.id, item]));
    const remote = registry?.plugins || [];
    const merged = remote.map((entry) => {
      const local = installed.get(entry.id);
      installed.delete(entry.id);
      return {
        ...entry,
        installed: Boolean(local),
        enabled: local?.enabled || false,
        installedVersion: local?.currentVersion || '',
        updateAvailable: Boolean(local && compareVersions(entry.version, local.currentVersion) > 0),
        previousVersions: local?.previousVersions || [],
        compatibility: this.compatibility({ core: { minVersion: entry.minCoreVersion }, platforms: entry.platforms })
      };
    });
    installed.forEach((local) => merged.push({
      id: local.id,
      name: local.manifest.name,
      description: local.manifest.description,
      publisher: local.manifest.publisher,
      version: local.currentVersion,
      permissions: local.manifest.permissions || [],
      installed: true,
      enabled: local.enabled,
      installedVersion: local.currentVersion,
      updateAvailable: false,
      previousVersions: local.previousVersions,
      compatibility: local.compatibility,
      unavailableFromRegistry: true
    }));
    return merged.sort((a, b) => String(a.name || a.id).localeCompare(String(b.name || b.id), 'zh-Hans-CN'));
  }

  setEnabled(pluginId, enabled) {
    const state = this.readState();
    if (!state.plugins[pluginId]) throw new Error(`插件尚未安装：${pluginId}`);
    state.plugins[pluginId].enabled = Boolean(enabled);
    state.plugins[pluginId].updatedAt = new Date().toISOString();
    this.writeState(state);
    return this.describeInstalled(pluginId);
  }

  rollback(pluginId) {
    const state = this.readState();
    const current = state.plugins[pluginId];
    if (!current) throw new Error(`插件尚未安装：${pluginId}`);
    const candidates = (current.previousVersions || []).filter((version) => fs.existsSync(this.versionRoot(pluginId, version)));
    const targetVersion = candidates.pop();
    if (!targetVersion) throw new Error('没有可回滚的插件版本');
    const previousCurrent = current.currentVersion;
    current.currentVersion = targetVersion;
    current.previousVersions = Array.from(new Set([...candidates, previousCurrent])).slice(-3);
    current.enabled = true;
    current.updatedAt = new Date().toISOString();
    this.writeState(state);
    return this.describeInstalled(pluginId);
  }

  uninstall(pluginId) {
    const state = this.readState();
    if (!state.plugins[pluginId]) return false;
    const root = this.pluginRoot(pluginId);
    if (!isInside(this.pluginsDir, root)) throw new Error('拒绝删除越界插件目录');
    fs.rmSync(root, { recursive: true, force: true });
    Array.from(this.verifiedInstallCache.keys()).forEach((key) => {
      if (key.startsWith(`${pluginId}@`)) this.verifiedInstallCache.delete(key);
    });
    delete state.plugins[pluginId];
    this.writeState(state);
    return true;
  }

  activePlugins() {
    return this.listInstalled().filter((item) => item.enabled && item.compatibility.compatible);
  }

  providerEntriesWithErrors() {
    const entries = [];
    const errors = [];
    this.activePlugins().forEach((plugin) => {
      let verified;
      try {
        verified = this.verifyInstalledVersion(plugin.id, plugin.currentVersion, { force: true });
      } catch (error) {
        errors.push(`${plugin.id}@${plugin.currentVersion}：${error.message || String(error)}`);
        return;
      }
      const root = verified.root;
      plugin.manifest.entrypoints.providers.forEach((relativePath) => {
        const safe = assertSafeRelativePath(relativePath, 'Provider 入口');
        const manifestPath = path.resolve(root, ...safe.split('/'));
        if (!isInside(root, manifestPath) || !fs.existsSync(manifestPath)) return;
        entries.push({
          pluginId: plugin.id,
          pluginVersion: plugin.currentVersion,
          pluginRoot: root,
          manifestPath,
          permissions: plugin.manifest.permissions || [],
          uiEntry: plugin.manifest.entrypoints.ui || '',
          verified: true
        });
      });
    });
    return { entries, errors };
  }

  providerEntries() {
    return this.providerEntriesWithErrors().entries;
  }

  resolveScript(pluginId, relativePath) {
    const plugin = this.describeInstalled(pluginId);
    if (!plugin || !plugin.enabled || !plugin.compatibility.compatible) throw new Error(`插件未启用：${pluginId}`);
    if (!(plugin.manifest.permissions || []).includes('process')) throw new Error(`插件没有声明运行进程权限：${pluginId}`);
    const safe = assertSafeRelativePath(relativePath, '插件脚本');
    const root = this.versionRoot(pluginId, plugin.currentVersion);
    const target = this.verifyInstalledFile(pluginId, plugin.currentVersion, safe);
    if (!isInside(root, target) || !fs.existsSync(target) || path.extname(target).toLowerCase() !== '.py') {
      throw new Error(`插件脚本不存在或类型不允许：${relativePath}`);
    }
    return { path: target, plugin, root };
  }

  readUi(pluginId, relativePath) {
    const plugin = this.describeInstalled(pluginId);
    if (!plugin || !plugin.enabled) throw new Error(`插件未启用：${pluginId}`);
    const safe = assertSafeRelativePath(relativePath, '插件 UI');
    const root = this.versionRoot(pluginId, plugin.currentVersion);
    const target = this.verifyInstalledFile(pluginId, plugin.currentVersion, safe);
    if (!isInside(root, target) || !fs.existsSync(target) || path.extname(target).toLowerCase() !== '.html') {
      throw new Error('插件自定义 UI 文件无效');
    }
    const stat = fs.statSync(target);
    if (stat.size > 2 * 1024 * 1024) throw new Error('插件自定义 UI 超过 2 MB 限制');
    return fs.readFileSync(target, 'utf8');
  }
}

module.exports = { PluginManager, isInside, requestBuffer, writeJsonAtomic };
