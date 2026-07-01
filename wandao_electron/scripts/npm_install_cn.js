const { spawnSync } = require('child_process');

const installArgs = [
  'install',
  '--registry=https://registry.npmmirror.com/',
  '--no-audit',
  '--no-fund',
  '--verbose'
];
const env = {
  ...process.env,
  ELECTRON_MIRROR: 'https://npmmirror.com/mirrors/electron/',
  npm_config_electron_mirror: 'https://npmmirror.com/mirrors/electron/',
  ELECTRON_BUILDER_BINARIES_MIRROR: 'https://npmmirror.com/mirrors/electron-builder-binaries/',
  npm_config_electron_builder_binaries_mirror: 'https://npmmirror.com/mirrors/electron-builder-binaries/'
};

const npmExecPath = process.env.npm_execpath;
const command = npmExecPath ? process.execPath : (process.platform === 'win32' ? 'npm.cmd' : 'npm');
const args = npmExecPath ? [npmExecPath, ...installArgs] : installArgs;

const result = spawnSync(command, args, {
  stdio: 'inherit',
  env,
  shell: !npmExecPath && process.platform === 'win32'
});

if (result.error) {
  console.error(result.error.message || result.error);
}

process.exit(result.status ?? 1);
