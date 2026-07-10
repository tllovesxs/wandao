#!/usr/bin/env node
const fs = require('fs');
const path = require('path');
const { createPluginEnvelope, signEnvelope, validatePluginManifest } = require('../wandao_electron/plugin_format');

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

function collectFiles(root) {
  const files = {};
  function visit(dir) {
    fs.readdirSync(dir, { withFileTypes: true })
      .sort((a, b) => a.name.localeCompare(b.name))
      .forEach((entry) => {
        if (entry.name === '__pycache__' || entry.name.startsWith('.')) return;
        const absolute = path.join(dir, entry.name);
        if (entry.isDirectory()) return visit(absolute);
        if (!entry.isFile()) return;
        const relative = path.relative(root, absolute).split(path.sep).join('/');
        if (relative === 'plugin.json' || relative.endsWith('.pyc')) return;
        files[relative] = fs.readFileSync(absolute);
      });
  }
  visit(root);
  return files;
}

function privateKeyFrom(args) {
  if (args['private-key']) return fs.readFileSync(path.resolve(args['private-key']), 'utf8');
  const value = process.env.WANDAO_PLUGIN_PRIVATE_KEY || '';
  return value.includes('\\n') ? value.replace(/\\n/g, '\n') : value;
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  if (!args.source || !args.output) throw new Error('用法：build_plugin.js --source plugins/<id> --output <file> [--private-key <pem>]');
  const source = path.resolve(args.source);
  const manifestPath = path.join(source, 'plugin.json');
  const manifest = validatePluginManifest(JSON.parse(fs.readFileSync(manifestPath, 'utf8')));
  const envelope = createPluginEnvelope(manifest, collectFiles(source));
  const signed = signEnvelope(envelope, privateKeyFrom(args), args['key-id'] || 'wandao-official-2026');
  const output = path.resolve(args.output);
  fs.mkdirSync(path.dirname(output), { recursive: true });
  fs.writeFileSync(output, JSON.stringify(signed));
  process.stdout.write(JSON.stringify({ id: manifest.id, version: manifest.version, output }, null, 2) + '\n');
}

try {
  main();
} catch (error) {
  console.error(error.message || String(error));
  process.exit(1);
}
