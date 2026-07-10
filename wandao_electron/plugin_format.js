const crypto = require('crypto');

const PLUGIN_FORMAT_VERSION = 1;
const REGISTRY_FORMAT_VERSION = 1;
const MAX_PLUGIN_FILES = 2000;
const MAX_PLUGIN_BYTES = 256 * 1024 * 1024;
const PLUGIN_ID_PATTERN = /^[a-z0-9][a-z0-9_-]{1,63}$/;
const VERSION_PATTERN = /^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/;
const ALLOWED_PERMISSIONS = new Set([
  'browser-automation',
  'credentials',
  'filesystem:read',
  'filesystem:write',
  'network',
  'process'
]);

function normalizeJson(value) {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return value;
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) throw new Error('签名内容不能包含非有限数字');
    return value;
  }
  if (Array.isArray(value)) return value.map(normalizeJson);
  if (value && typeof value === 'object') {
    const output = {};
    Object.keys(value).sort().forEach((key) => {
      if (value[key] !== undefined) output[key] = normalizeJson(value[key]);
    });
    return output;
  }
  throw new Error(`签名内容包含不支持的类型：${typeof value}`);
}

function canonicalStringify(value) {
  return JSON.stringify(normalizeJson(value));
}

function sha256Hex(value) {
  return crypto.createHash('sha256').update(value).digest('hex');
}

function unsignedEnvelope(envelope) {
  const copy = { ...envelope };
  delete copy.signature;
  return copy;
}

function signEnvelope(envelope, privateKey, keyId) {
  if (!privateKey) throw new Error('缺少 Ed25519 私钥');
  if (!keyId) throw new Error('缺少签名 keyId');
  const payload = Buffer.from(canonicalStringify(unsignedEnvelope(envelope)), 'utf8');
  return {
    ...unsignedEnvelope(envelope),
    signature: {
      algorithm: 'ed25519',
      keyId,
      value: crypto.sign(null, payload, privateKey).toString('base64')
    }
  };
}

function trustKeyMap(trustStore) {
  const keys = Array.isArray(trustStore?.keys) ? trustStore.keys : [];
  return new Map(keys.map((item) => [String(item.id || ''), item]));
}

function verifyEnvelopeSignature(envelope, trustStore) {
  const signature = envelope?.signature;
  if (!signature || signature.algorithm !== 'ed25519' || !signature.keyId || !signature.value) {
    throw new Error('插件内容没有有效的 Ed25519 签名');
  }
  const key = trustKeyMap(trustStore).get(String(signature.keyId));
  if (!key || key.algorithm !== 'ed25519' || !key.publicKey) {
    throw new Error(`签名密钥不受信任：${signature.keyId}`);
  }
  const payload = Buffer.from(canonicalStringify(unsignedEnvelope(envelope)), 'utf8');
  const valid = crypto.verify(null, payload, key.publicKey, Buffer.from(signature.value, 'base64'));
  if (!valid) throw new Error('插件签名校验失败，文件可能已被篡改');
  return { keyId: signature.keyId, publisher: key.publisher || '' };
}

function parseVersion(version) {
  const match = String(version || '').match(/^(\d+)\.(\d+)\.(\d+)(?:-(.+))?$/);
  if (!match) return null;
  return { numbers: match.slice(1, 4).map(Number), prerelease: match[4] || '' };
}

function compareVersions(left, right) {
  const a = parseVersion(left);
  const b = parseVersion(right);
  if (!a || !b) return String(left || '').localeCompare(String(right || ''));
  for (let index = 0; index < 3; index += 1) {
    if (a.numbers[index] !== b.numbers[index]) return a.numbers[index] - b.numbers[index];
  }
  if (a.prerelease === b.prerelease) return 0;
  if (!a.prerelease) return 1;
  if (!b.prerelease) return -1;
  return a.prerelease.localeCompare(b.prerelease);
}

function assertSafeRelativePath(filePath, label = '文件路径') {
  const value = String(filePath || '');
  if (!value || value.includes('\\') || value.startsWith('/') || /^[A-Za-z]:/.test(value)) {
    throw new Error(`${label}必须是 POSIX 相对路径：${value || '(空)'}`);
  }
  const parts = value.split('/');
  if (parts.some((part) => !part || part === '.' || part === '..')) {
    throw new Error(`${label}不能包含空目录、. 或 ..：${value}`);
  }
  return value;
}

function validatePluginManifest(manifest) {
  if (!manifest || typeof manifest !== 'object' || Array.isArray(manifest)) throw new Error('plugin.json 必须是对象');
  const allowedKeys = new Set(['$schema', 'schemaVersion', 'id', 'name', 'description', 'version', 'publisher', 'homepage', 'license', 'core', 'platforms', 'entrypoints', 'permissions']);
  const unknownKeys = Object.keys(manifest).filter((key) => !allowedKeys.has(key));
  if (unknownKeys.length) throw new Error(`plugin.json 包含未知字段：${unknownKeys.join(', ')}`);
  if (manifest.schemaVersion !== 1) throw new Error('只支持 plugin schemaVersion=1');
  if (!PLUGIN_ID_PATTERN.test(String(manifest.id || ''))) throw new Error(`插件 ID 不合法：${manifest.id || '(空)'}`);
  if (!VERSION_PATTERN.test(String(manifest.version || ''))) throw new Error(`插件版本不合法：${manifest.version || '(空)'}`);
  for (const key of ['name', 'description', 'publisher']) {
    if (!String(manifest[key] || '').trim()) throw new Error(`插件缺少字段：${key}`);
  }
  const providers = manifest.entrypoints?.providers;
  if (!Array.isArray(providers) || providers.length < 1) throw new Error('插件至少需要声明一个 Provider 入口');
  providers.forEach((item) => assertSafeRelativePath(item, 'Provider 入口'));
  if (manifest.entrypoints?.ui) assertSafeRelativePath(manifest.entrypoints.ui, '自定义 UI 入口');
  const permissions = manifest.permissions || [];
  if (!Array.isArray(permissions) || permissions.some((item) => !ALLOWED_PERMISSIONS.has(item))) {
    throw new Error('插件声明了不支持的权限');
  }
  if (manifest.core?.minVersion && !VERSION_PATTERN.test(String(manifest.core.minVersion))) {
    throw new Error('core.minVersion 必须是语义化版本号');
  }
  if (manifest.platforms && (!Array.isArray(manifest.platforms) || manifest.platforms.some((item) => !['win32', 'darwin', 'linux'].includes(item)))) {
    throw new Error('platforms 包含不支持的系统');
  }
  return manifest;
}

function createPluginEnvelope(manifest, files) {
  validatePluginManifest(manifest);
  const normalizedFiles = {};
  let totalBytes = 0;
  const entries = Object.entries(files || {});
  if (!entries.length || entries.length > MAX_PLUGIN_FILES) throw new Error('插件文件数量不合法');
  entries.sort(([left], [right]) => left.localeCompare(right)).forEach(([filePath, content]) => {
    const safePath = assertSafeRelativePath(filePath);
    const buffer = Buffer.isBuffer(content) ? content : Buffer.from(content);
    totalBytes += buffer.length;
    if (totalBytes > MAX_PLUGIN_BYTES) throw new Error('插件解包后超过 256 MB 限制');
    normalizedFiles[safePath] = buffer.toString('base64');
  });
  for (const providerPath of manifest.entrypoints.providers) {
    if (!Object.prototype.hasOwnProperty.call(normalizedFiles, providerPath)) throw new Error(`Provider 入口不存在：${providerPath}`);
  }
  if (manifest.entrypoints.ui && !Object.prototype.hasOwnProperty.call(normalizedFiles, manifest.entrypoints.ui)) {
    throw new Error(`自定义 UI 入口不存在：${manifest.entrypoints.ui}`);
  }
  const body = { formatVersion: PLUGIN_FORMAT_VERSION, manifest, files: normalizedFiles };
  return {
    ...body,
    integrity: { algorithm: 'sha256', value: sha256Hex(canonicalStringify(body)) }
  };
}

function verifyPluginEnvelope(envelope, trustStore, options = {}) {
  if (!envelope || envelope.formatVersion !== PLUGIN_FORMAT_VERSION) throw new Error('不支持的插件包格式');
  const manifest = validatePluginManifest(envelope.manifest);
  if (!envelope.files || typeof envelope.files !== 'object' || Array.isArray(envelope.files)) throw new Error('插件 files 必须是对象');
  const body = { formatVersion: envelope.formatVersion, manifest, files: envelope.files };
  const expected = sha256Hex(canonicalStringify(body));
  if (envelope.integrity?.algorithm !== 'sha256' || envelope.integrity.value !== expected) {
    throw new Error('插件完整性校验失败');
  }
  const signer = options.allowUnsigned ? null : verifyEnvelopeSignature(envelope, trustStore);
  const decodedFiles = new Map();
  let totalBytes = 0;
  const entries = Object.entries(envelope.files);
  if (!entries.length || entries.length > MAX_PLUGIN_FILES) throw new Error('插件文件数量不合法');
  for (const [filePath, encoded] of entries) {
    const safePath = assertSafeRelativePath(filePath);
    if (typeof encoded !== 'string') throw new Error(`插件文件内容不是 Base64：${safePath}`);
    const buffer = Buffer.from(encoded, 'base64');
    totalBytes += buffer.length;
    if (totalBytes > MAX_PLUGIN_BYTES) throw new Error('插件解包后超过 256 MB 限制');
    decodedFiles.set(safePath, buffer);
  }
  manifest.entrypoints.providers.forEach((item) => {
    if (!decodedFiles.has(item)) throw new Error(`Provider 入口不存在：${item}`);
  });
  return { manifest, files: decodedFiles, signer, integrity: expected, totalBytes };
}

function verifyRegistryEnvelope(registry, trustStore) {
  if (!registry || registry.formatVersion !== REGISTRY_FORMAT_VERSION || !Array.isArray(registry.plugins)) {
    throw new Error('插件注册表格式无效');
  }
  verifyEnvelopeSignature(registry, trustStore);
  const seen = new Set();
  registry.plugins.forEach((plugin) => {
    validatePluginManifest({
      schemaVersion: 1,
      id: plugin.id,
      version: plugin.version,
      name: plugin.name,
      description: plugin.description,
      publisher: plugin.publisher,
      entrypoints: { providers: ['provider.json'] },
      permissions: plugin.permissions || [],
      platforms: plugin.platforms,
      core: { minVersion: plugin.minCoreVersion }
    });
    if (seen.has(plugin.id)) throw new Error(`注册表插件 ID 重复：${plugin.id}`);
    seen.add(plugin.id);
    if (!/^https:\/\//i.test(String(plugin.packageUrl || '')) && !/^http:\/\/127\.0\.0\.1(?::\d+)?\//i.test(String(plugin.packageUrl || ''))) {
      throw new Error(`插件下载地址不安全：${plugin.id}`);
    }
    if (!/^[a-f0-9]{64}$/i.test(String(plugin.sha256 || ''))) throw new Error(`插件缺少 SHA-256：${plugin.id}`);
  });
  return registry;
}

module.exports = {
  ALLOWED_PERMISSIONS,
  PLUGIN_FORMAT_VERSION,
  REGISTRY_FORMAT_VERSION,
  assertSafeRelativePath,
  canonicalStringify,
  compareVersions,
  createPluginEnvelope,
  sha256Hex,
  signEnvelope,
  validatePluginManifest,
  verifyEnvelopeSignature,
  verifyPluginEnvelope,
  verifyRegistryEnvelope
};
