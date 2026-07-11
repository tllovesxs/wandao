#!/usr/bin/env node
const fs = require('fs');
const path = require('path');

const CHANNELS = new Set(['stable', 'experimental']);
const PLUGIN_ID = /^[a-z0-9][a-z0-9_-]{1,63}$/;
const DEFAULT_POLICY_PATH = path.resolve(__dirname, '..', 'plugins', 'release-policy.json');

function readPolicy(policyPath = DEFAULT_POLICY_PATH) {
  return JSON.parse(fs.readFileSync(path.resolve(policyPath), 'utf8'));
}

function validatePolicy(policy, pluginIds = null) {
  if (!policy || policy.schemaVersion !== 1 || policy.defaultChannel !== 'experimental' || !policy.plugins || typeof policy.plugins !== 'object' || Array.isArray(policy.plugins)) {
    throw new Error('发布策略必须使用 schemaVersion=1，并以 experimental 作为默认发布等级');
  }
  for (const [pluginId, entry] of Object.entries(policy.plugins)) {
    if (!PLUGIN_ID.test(pluginId)) throw new Error(`发布策略中的插件 ID 无效：${pluginId}`);
    if (pluginIds && !pluginIds.has(pluginId)) throw new Error(`发布策略引用了不存在的插件：${pluginId}`);
    if (!entry || typeof entry !== 'object' || !CHANNELS.has(entry.channel)) {
      throw new Error(`发布策略中的 channel 无效：${pluginId}`);
    }
    if (entry.channel === 'stable' && (!String(entry.approvedBy || '').trim() || !/^\d{4}-\d{2}-\d{2}$/.test(String(entry.approvedAt || '')))) {
      throw new Error(`稳定插件必须记录维护者批准人和批准日期：${pluginId}`);
    }
  }
  return policy;
}

function channelFor(policy, pluginId) {
  return policy.plugins?.[pluginId]?.channel || policy.defaultChannel;
}

function main() {
  const args = process.argv.slice(2);
  const policyFlag = args.indexOf('--policy');
  const pluginFlag = args.indexOf('--plugin');
  const policyPath = policyFlag >= 0 ? args[policyFlag + 1] : DEFAULT_POLICY_PATH;
  const policy = validatePolicy(readPolicy(policyPath));
  if (pluginFlag >= 0) {
    const pluginId = args[pluginFlag + 1];
    if (!PLUGIN_ID.test(String(pluginId || ''))) throw new Error('用法：plugin_release_policy.js --plugin <plugin-id>');
    process.stdout.write(`${channelFor(policy, pluginId)}\n`);
    return;
  }
  process.stdout.write(`${JSON.stringify(policy, null, 2)}\n`);
}

if (require.main === module) {
  try {
    main();
  } catch (error) {
    console.error(error.message || String(error));
    process.exit(1);
  }
}

module.exports = { CHANNELS, DEFAULT_POLICY_PATH, channelFor, readPolicy, validatePolicy };
