const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const repoRoot = path.resolve(__dirname, '..');
const read = (relativePath) => fs.readFileSync(path.join(repoRoot, relativePath), 'utf8');

test('developer reports identify the desktop and active plugin versions', () => {
  const appSource = read('wandao_electron/renderer/app.js');
  const commandSource = read('wandao_electron/src-tauri/src/commands.rs');

  assert.match(commandSource, /"appVersion": app\.package_info\(\)\.version\.to_string\(\)/);
  assert.match(appSource, /Wandao 版本：\$\{paths\.appVersion/);
  assert.match(appSource, /当前插件：\$\{activePluginVersionLabel\(\)\}/);
  assert.match(appSource, /插件版本：\$\{plugins\.join/);
});

test('release configuration uses compact Tauri bundles for supported desktop platforms', () => {
  const manifest = JSON.parse(read('wandao_electron/package.json'));
  const tauriConfig = JSON.parse(read('wandao_electron/src-tauri/tauri.conf.json'));
  const cargoSource = read('wandao_electron/src-tauri/Cargo.toml');

  assert.equal(manifest.devDependencies['@tauri-apps/cli'], '2.11.4');
  assert.equal(tauriConfig.bundle.windows.nsis.compression, 'lzma');
  assert.deepEqual(tauriConfig.bundle.windows.nsis.languages, ['SimpChinese', 'TradChinese', 'English']);
  assert.match(cargoSource, /\[profile\.release\][\s\S]*lto = true/);
  assert.match(cargoSource, /\[profile\.release\][\s\S]*opt-level = "s"/);
  assert.match(cargoSource, /\[profile\.release\][\s\S]*strip = true/);
  assert.doesNotMatch(JSON.stringify(manifest), /electron-builder|electronLanguages|electronDist/);
});

test('portable Python preparation removes build-only package tooling after install', () => {
  const source = read('wandao_electron/scripts/prepare_python_runtime.py');
  const prepareRuntime = source.slice(source.indexOf('def prepare_runtime('));

  assert.match(source, /def remove_build_only_runtime_files/);
  assert.match(source, /"pip", "setuptools", "pkg_resources"/);
  assert.match(source, /"Lib\/ensurepip"/);
  assert.match(prepareRuntime, /remove_build_only_runtime_files\(staged_runtime\)/);
  assert.match(prepareRuntime, /verify_runtime_is_release_only\(staged_runtime\)/);
  assert.match(prepareRuntime, /fingerprint = verify_runtime\(py, target\)/);
  assert.match(prepareRuntime, /replace_runtime_output\(staged_runtime, output_dir\)/);
  assert.ok(
    prepareRuntime.indexOf('remove_build_only_runtime_files(staged_runtime)')
      < prepareRuntime.indexOf('verify_runtime_is_release_only(staged_runtime)'),
  );
  assert.ok(
    prepareRuntime.indexOf('verify_runtime_is_release_only(staged_runtime)')
      < prepareRuntime.indexOf('fingerprint = verify_runtime(py, target)'),
  );
  assert.ok(
    prepareRuntime.indexOf('write_build_info(')
      < prepareRuntime.indexOf('replace_runtime_output(staged_runtime, output_dir)'),
  );
});
