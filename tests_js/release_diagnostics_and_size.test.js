const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const repoRoot = path.resolve(__dirname, '..');
const read = (relativePath) => fs.readFileSync(path.join(repoRoot, relativePath), 'utf8');

test('developer reports identify the desktop and active plugin versions', () => {
  const appSource = read('wandao_electron/renderer/app.js');
  const mainSource = read('wandao_electron/main.js');

  assert.match(mainSource, /appVersion:\s*PROJECT_INFO\.version/);
  assert.match(appSource, /Wandao 版本：\$\{paths\.appVersion/);
  assert.match(appSource, /当前插件：\$\{activePluginVersionLabel\(\)\}/);
  assert.match(appSource, /插件版本：\$\{plugins\.join/);
});

test('release configuration keeps only the supported Electron locales', () => {
  const manifest = JSON.parse(read('wandao_electron/package.json'));
  assert.deepEqual(manifest.build.electronLanguages, ['zh-CN', 'zh-TW', 'en-US']);
});

test('portable Python preparation removes build-only package tooling after install', () => {
  const source = read('wandao_electron/scripts/prepare_python_runtime.py');

  assert.match(source, /def remove_build_only_runtime_files/);
  assert.match(source, /"pip", "setuptools", "pkg_resources"/);
  assert.match(source, /"Lib\/ensurepip"/);
  assert.match(source, /remove_build_only_runtime_files\(output_dir\)/);
  assert.match(source, /verify_runtime_is_release_only\(output_dir\)/);
});
