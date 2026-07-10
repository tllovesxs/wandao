#!/usr/bin/env node
const fs = require('fs');
const path = require('path');
const { compareVersions, sha256Hex, signEnvelope, verifyPluginEnvelope } = require('../wandao_electron/plugin_format');

function parseArgs(argv) {
  const result = {};
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (!key.startsWith('--')) continue;
    result[key.slice(2)] = argv[index + 1];
    index += 1;
  }
  return result;
}

function privateKeyFrom(args) {
  if (args['private-key']) return fs.readFileSync(path.resolve(args['private-key']), 'utf8');
  const value = process.env.WANDAO_PLUGIN_PRIVATE_KEY || '';
  return value.includes('\\n') ? value.replace(/\\n/g, '\n') : value;
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  if (!args.packages || !args.output || !args['base-url']) {
    throw new Error('用法：build_plugin_registry.js --packages <dir> --base-url <https-url> --output <file>');
  }
  const packageDir = path.resolve(args.packages);
  const trustStore = JSON.parse(fs.readFileSync(path.resolve(args.trust || 'wandao_electron/assets/plugin-trust.json'), 'utf8'));
  const latest = new Map();
  fs.readdirSync(packageDir)
    .filter((name) => name.endsWith('.wandao-plugin'))
    .sort()
    .forEach((name) => {
      const buffer = fs.readFileSync(path.join(packageDir, name));
      const envelope = JSON.parse(buffer.toString('utf8'));
      const { manifest } = verifyPluginEnvelope(envelope, trustStore);
      const current = latest.get(manifest.id);
      if (current && compareVersions(current.version, manifest.version) >= 0) return;
      latest.set(manifest.id, {
        id: manifest.id,
        name: manifest.name,
        description: manifest.description,
        publisher: manifest.publisher,
        version: manifest.version,
        minCoreVersion: manifest.core?.minVersion || '0.0.0',
        platforms: manifest.platforms || ['win32', 'darwin', 'linux'],
        permissions: manifest.permissions || [],
        packageUrl: `${args['base-url'].replace(/\/$/, '')}/${encodeURIComponent(name)}`,
        sha256: sha256Hex(buffer),
        homepage: manifest.homepage || ''
      });
    });
  const body = {
    formatVersion: 1,
    generatedAt: new Date().toISOString(),
    plugins: Array.from(latest.values()).sort((a, b) => a.id.localeCompare(b.id))
  };
  const registry = signEnvelope(body, privateKeyFrom(args), args['key-id'] || 'wandao-official-2026');
  const output = path.resolve(args.output);
  fs.mkdirSync(path.dirname(output), { recursive: true });
  fs.writeFileSync(output, JSON.stringify(registry, null, 2) + '\n');
  process.stdout.write(JSON.stringify({ output, plugins: body.plugins.map((item) => `${item.id}@${item.version}`) }, null, 2) + '\n');
}

try {
  main();
} catch (error) {
  console.error(error.message || String(error));
  process.exit(1);
}
