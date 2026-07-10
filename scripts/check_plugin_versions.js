#!/usr/bin/env node
const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const { compareVersions, validatePluginManifest } = require('../wandao_electron/plugin_format');

const base = process.argv[2];
const allowMultiple = process.argv.includes('--allow-multiple');
if (!base) {
  console.error('用法：check_plugin_versions.js <base-ref>');
  process.exit(1);
}

function git(args) {
  return execFileSync('git', args, { encoding: 'utf8' }).trim();
}

const changed = git(['diff', '--name-only', `${base}...HEAD`, '--', 'plugins'])
  .split(/\r?\n/)
  .filter(Boolean);
const pluginIds = new Set(
  changed
    .map((file) => file.replace(/\\/g, '/').match(/^plugins\/([^/]+)\//)?.[1])
    .filter((id) => id && !id.startsWith('_'))
);

if (pluginIds.size > 1 && !allowMultiple) {
  console.error(`一个 PR 只能修改一个插件，当前包含：${Array.from(pluginIds).join(', ')}`);
  process.exit(1);
}

for (const pluginId of pluginIds) {
  const manifestPath = `plugins/${pluginId}/plugin.json`;
  if (!fs.existsSync(manifestPath)) {
    console.error(`插件目录缺少 plugin.json：${pluginId}`);
    process.exit(1);
  }
  const current = validatePluginManifest(JSON.parse(fs.readFileSync(manifestPath, 'utf8')));
  let previous = null;
  try {
    previous = JSON.parse(git(['show', `${base}:${manifestPath}`]));
  } catch (_error) {
    if (current.version !== '1.0.0') {
      console.error(`新插件初始版本必须是 1.0.0：${pluginId}@${current.version}`);
      process.exit(1);
    }
  }
  if (previous && compareVersions(current.version, previous.version) <= 0) {
    console.error(`插件版本必须提升：${pluginId} ${previous.version} -> ${current.version}`);
    process.exit(1);
  }
  console.log(previous
    ? `Plugin version passed: ${pluginId} ${previous.version} -> ${current.version}`
    : `New plugin version passed: ${pluginId}@${current.version}`);
}
