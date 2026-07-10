#!/usr/bin/env node
const fs = require('fs');
const path = require('path');
const { assertSafeRelativePath, validatePluginManifest } = require('../wandao_electron/plugin_format');

const repoRoot = path.resolve(__dirname, '..');
const pluginsRoot = path.join(repoRoot, 'plugins');

function isInside(root, candidate) {
  const relative = path.relative(path.resolve(root), path.resolve(candidate));
  return relative === '' || (!relative.startsWith('..' + path.sep) && relative !== '..' && !path.isAbsolute(relative));
}

function fail(message) {
  throw new Error(message);
}

function validateProvider(pluginRoot, providerPath, providerIds, pluginManifest) {
  const safe = assertSafeRelativePath(providerPath, 'Provider 入口');
  const absolute = path.resolve(pluginRoot, ...safe.split('/'));
  if (!isInside(pluginRoot, absolute) || !fs.existsSync(absolute)) fail(`Provider 入口不存在：${providerPath}`);
  const provider = JSON.parse(fs.readFileSync(absolute, 'utf8'));
  if (provider.schemaVersion !== 1 || !provider.id) fail(`${providerPath} 不是 Provider v1`);
  if (provider.trustLevel === 'official' && pluginManifest.publisher !== 'Wandao Official') {
    fail(`${providerPath} 只有 Wandao Official 发布者可以声明 official 信任级别`);
  }
  if (path.basename(path.dirname(absolute)) !== provider.id) fail(`${providerPath} 的目录名必须等于 Provider ID`);
  if (providerIds.has(provider.id)) fail(`Provider ID 重复：${provider.id}`);
  providerIds.add(provider.id);
  const providerRoot = path.dirname(absolute);
  const actions = Array.isArray(provider.actions) ? provider.actions : [];
  if (provider.type !== 'guide' && !actions.length) fail(`${providerPath} 至少需要一个动作`);
  actions.forEach((action, index) => {
    const script = action.script || provider.script;
    if (!script) fail(`${providerPath} actions[${index}] 缺少脚本`);
    const scriptPath = path.resolve(providerRoot, script);
    if (!isInside(pluginRoot, scriptPath) || !fs.existsSync(scriptPath) || path.extname(scriptPath) !== '.py') {
      fail(`${providerPath} actions[${index}] 脚本越界或不存在：${script}`);
    }
  });
  const guide = provider.guide || provider.guidePath;
  if (guide) {
    const guidePath = path.resolve(providerRoot, guide);
    if (!isInside(providerRoot, guidePath) || !fs.existsSync(guidePath)) fail(`${providerPath} 教程文件无效：${guide}`);
  }
}

function main() {
  const pluginIds = new Set();
  const providerIds = new Set();
  const manifests = fs.readdirSync(pluginsRoot, { withFileTypes: true })
    .filter((entry) => entry.isDirectory() && !entry.name.startsWith('_'))
    .map((entry) => path.join(pluginsRoot, entry.name, 'plugin.json'))
    .filter(fs.existsSync);
  manifests.forEach((manifestPath) => {
    const pluginRoot = path.dirname(manifestPath);
    const manifest = validatePluginManifest(JSON.parse(fs.readFileSync(manifestPath, 'utf8')));
    if (path.basename(pluginRoot) !== manifest.id) fail(`插件目录名必须等于插件 ID：${manifest.id}`);
    if (pluginIds.has(manifest.id)) fail(`插件 ID 重复：${manifest.id}`);
    pluginIds.add(manifest.id);
    manifest.entrypoints.providers.forEach((providerPath) => validateProvider(pluginRoot, providerPath, providerIds, manifest));
    if (manifest.entrypoints.ui) {
      const uiPath = path.resolve(pluginRoot, ...assertSafeRelativePath(manifest.entrypoints.ui).split('/'));
      if (!isInside(pluginRoot, uiPath) || !fs.existsSync(uiPath) || path.extname(uiPath) !== '.html') fail(`自定义 UI 入口无效：${manifest.id}`);
    }
  });
  process.stdout.write(`Plugin validation passed (${pluginIds.size} plugins, ${providerIds.size} providers).\n`);
}

try {
  main();
} catch (error) {
  console.error(error.message || String(error));
  process.exit(1);
}
