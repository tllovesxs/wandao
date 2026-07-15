const fs = require('fs');
const path = require('path');

// Keep this allowlist in the desktop host.  Plugins receive only their own
// data directory and never need a path to the application's legacy data root.
const LEGACY_PLUGIN_STATE_FILES = Object.freeze({
  aliyun_thoughts: ['.aliyun_thoughts_auth.json'],
  feishu: ['.feishu_auth.json', 'feishu_import_config.json', '.feishu_import_config.json'],
  ima: ['ima_config.json'],
  yinxiang: ['yinxiang/yinxiang_china.db', 'yinxiang/yinxiang_china.db-shm', 'yinxiang/yinxiang_china.db-wal'],
  youdao: ['.youdao_auth.json'],
  yuque: ['.yuque_auth.json', '.yuque_import_config.json'],
  wiz: ['.wiz_auth.json'],
  zsxq: ['.zsxq_auth.json']
});

function isDescendant(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative && !relative.startsWith(`..${path.sep}`) && relative !== '..' && !path.isAbsolute(relative);
}

function migrateLegacyPluginState({ pluginId, legacyRoot, dataRoot }) {
  const stateFiles = LEGACY_PLUGIN_STATE_FILES[pluginId] || [];
  if (!legacyRoot || !dataRoot || !stateFiles.length) return [];

  const legacyBase = path.resolve(legacyRoot);
  const pluginBase = path.resolve(dataRoot);
  const migrated = [];

  for (const relativePath of stateFiles) {
    const source = path.resolve(legacyBase, relativePath);
    const target = path.resolve(pluginBase, relativePath);
    if (!isDescendant(legacyBase, source) || !isDescendant(pluginBase, target)) continue;
    if (fs.existsSync(target)) continue;

    let sourceInfo;
    try {
      sourceInfo = fs.statSync(source);
    } catch (_) {
      continue;
    }
    if (!sourceInfo.isFile()) continue;

    try {
      fs.mkdirSync(path.dirname(target), { recursive: true });
      fs.copyFileSync(source, target, fs.constants.COPYFILE_EXCL);
      if (process.platform !== 'win32') fs.chmodSync(target, 0o600);
      migrated.push(relativePath);
    } catch (_) {
      // Legacy state is a convenience migration only.  A locked or unreadable
      // old file must not prevent the user from logging in again.
    }
  }
  return migrated;
}

module.exports = { LEGACY_PLUGIN_STATE_FILES, migrateLegacyPluginState };
