// Tool configurations
const TOOLS = {
  'zsxq': {
    title: '知识星球任意项目/专栏导出',
    description: '将知识星球内容导出为本地 Markdown 文件',
    script: 'export_zsxq.py',
    urlParam: '--entry-url',
    outputParam: '--output'
  },
  'yuque': {
    title: '语雀任意知识库导出',
    description: '将语雀知识库导出为 Markdown',
    script: 'export_yuque.py',
    urlParam: '--book-url',
    outputParam: '--output'
  },
  'yuque-import': {
    title: '语雀 Markdown 导入',
    description: '将本地 Markdown 批量导入到语雀知识库',
    script: 'import_yuque.py',
    urlParam: '--target-book-url',
    outputParam: '--source-dir',
    isImport: true
  },
  'feishu-export': {
    title: '飞书 Wiki 知识库导出',
    description: '将飞书 Wiki 导出为 Markdown',
    script: 'export_feishu.py',
    urlParam: '--wiki-url',
    outputParam: '--output'
  },
  'feishu-import': {
    title: '飞书 Wiki Markdown 导入',
    description: '将本地 Markdown 批量导入到飞书 Wiki',
    script: 'import_feishu.py',
    urlParam: '--wiki-url',
    outputParam: '--source-dir',
    isImport: true
  },
  'yinxiang-import': {
    title: '印象笔记 Markdown 导入',
    description: '将本地 Markdown 批量导入到印象笔记',
    script: 'import_yinxiang.py',
    outputParam: '--source-dir',
    isImport: true,
    noUrl: true
  },
  'aliyun': {
    title: '阿里云 Thoughts 工作区导出',
    description: '将阿里云 Thoughts 导出为 Markdown',
    script: 'export_aliyun_thoughts.py',
    urlParam: '--workspace-url',
    outputParam: '--output'
  },
  'yinxiang': {
    title: '印象笔记导出',
    description: '将印象笔记笔记本导出为 Markdown',
    script: 'export_yinxiang.py',
    outputParam: '--output',
    noUrl: true
  }
};

const FEISHU_DEVELOPER_CONSOLE_URL = 'https://open.feishu.cn/app';
const FEISHU_IMPORT_REQUIRED_SCOPES = [
  'drive:drive',
  'drive:file:upload',
  'docs:permission.member:create',
  'docs:document:import',
  'docx:document',
  'docx:document:write_only',
  'wiki:wiki'
];
const FEISHU_SCOPE_PRIORITY = [
  'docx:document:write_only',
  'drive:file:upload',
  'drive:drive',
  'docs:permission.member:create',
  'docs:document:import',
  'docx:document',
  'wiki:wiki'
];

let currentTool = 'zsxq';
let isRunning = false;
let appPaths = null;
let feishuImportConfig = {};
let tocStates = {};
let pythonProgressBuffer = '';
let progressVisible = false;
let latestReleaseUrl = 'https://github.com/tllovesxs/wandao/releases/latest';

function applyTheme(theme) {
  const normalized = theme === 'dark' ? 'dark' : 'light';
  document.body.dataset.theme = normalized;
  const button = document.getElementById('btn-theme-toggle');
  if (button) {
    button.textContent = normalized === 'dark' ? '日间模式' : '夜间模式';
  }
}

function loadTheme() {
  const saved = localStorage.getItem('wandao-theme');
  if (saved === 'dark' || saved === 'light') return saved;
  const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  return prefersDark ? 'dark' : 'light';
}

function toggleTheme() {
  const next = document.body.dataset.theme === 'dark' ? 'light' : 'dark';
  localStorage.setItem('wandao-theme', next);
  applyTheme(next);
  log(next === 'dark' ? '已切换到夜间模式' : '已切换到日间模式', 'info');
}

function showUpdateBanner(info) {
  const banner = document.getElementById('update-banner');
  if (!banner || !info) return;
  latestReleaseUrl = info.releaseUrl || latestReleaseUrl;
  const latestLabel = info.latestTag || (info.latestVersion ? `v${info.latestVersion}` : '-');
  document.getElementById('update-title').textContent = `发现新版本：${latestLabel}`;
  document.getElementById('update-detail').textContent = `当前版本 v${info.currentVersion || '-'}，最新版本 ${latestLabel}。建议前往 Releases 下载新版。`;
  banner.hidden = false;
}

function hideUpdateBanner() {
  const banner = document.getElementById('update-banner');
  if (banner) banner.hidden = true;
}

async function checkForUpdates(silent = false) {
  if (!window.electronAPI.checkForUpdates) {
    if (!silent) alert('当前版本暂不支持在线检查更新。');
    return;
  }
  const button = document.getElementById('btn-check-update');
  if (button && !silent) {
    button.disabled = true;
    button.textContent = '检查中...';
  }
  try {
    const result = await window.electronAPI.checkForUpdates();
    if (!result.success) {
      if (!silent) {
        log(`检查更新失败：${result.error}`, 'error');
        alert(`检查更新失败：${result.error}`);
      }
      return;
    }
    const info = result.data || {};
    latestReleaseUrl = info.releaseUrl || latestReleaseUrl;
    if (info.hasUpdate) {
      showUpdateBanner(info);
      log(`发现新版本：v${info.latestVersion}，当前版本：v${info.currentVersion}`, 'success');
      if (!silent) {
        alert(`发现新版本 v${info.latestVersion}，可以点击顶部提示前往下载。`);
      }
    } else if (!silent) {
      hideUpdateBanner();
      log(`当前已是最新版本：v${info.currentVersion}`, 'success');
      alert(`当前已是最新版本：v${info.currentVersion}`);
    }
  } catch (error) {
    if (!silent) {
      log(`检查更新失败：${formatError(error)}`, 'error');
      alert(`检查更新失败：${formatError(error)}`);
    }
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = '检查更新';
    }
  }
}

// Log functions
function log(message, type = 'info') {
  const logContent = document.getElementById('log-content');
  const entry = document.createElement('div');
  entry.className = `log-entry ${type}`;
  const timestamp = new Date().toLocaleTimeString();
  entry.textContent = `[${timestamp}] ${message}`;
  logContent.appendChild(entry);
  logContent.scrollTop = logContent.scrollHeight;
}

function clearLog() {
  document.getElementById('log-content').innerHTML = '';
}

function progressElements() {
  return {
    section: document.getElementById('progress-section'),
    title: document.getElementById('progress-title'),
    percent: document.getElementById('progress-percent'),
    fill: document.getElementById('progress-fill'),
    detail: document.getElementById('progress-detail')
  };
}

function startProgress(title, detail = '任务启动中，正在等待进度信息...') {
  const els = progressElements();
  if (!els.section) return;
  progressVisible = true;
  pythonProgressBuffer = '';
  els.section.hidden = false;
  els.title.textContent = title || '任务进行中';
  els.percent.textContent = '进行中';
  els.fill.className = 'progress-fill indeterminate';
  els.fill.style.width = '';
  els.detail.textContent = detail;
}

function updateProgress(done, total, detail = '') {
  const els = progressElements();
  if (!els.section) return;
  const safeTotal = Math.max(0, Number(total) || 0);
  const safeDone = Math.max(0, Number(done) || 0);
  if (!progressVisible) startProgress('任务进行中');
  if (!safeTotal) {
    els.percent.textContent = '进行中';
    els.fill.className = 'progress-fill indeterminate';
    els.fill.style.width = '';
    if (detail) els.detail.textContent = detail;
    return;
  }
  const ratio = Math.min(1, safeDone / safeTotal);
  const percent = Math.max(0, Math.min(100, Math.round(ratio * 100)));
  els.percent.textContent = `${percent}%`;
  els.fill.className = 'progress-fill';
  els.fill.style.width = `${percent}%`;
  els.detail.textContent = detail || `已处理 ${safeDone}/${safeTotal}`;
}

function finishProgress(success, detail) {
  const els = progressElements();
  if (!els.section) return;
  if (!progressVisible) {
    els.section.hidden = false;
  }
  progressVisible = false;
  els.percent.textContent = success ? '100%' : '失败';
  els.fill.className = `progress-fill ${success ? 'success' : 'error'}`;
  els.fill.style.width = '100%';
  els.detail.textContent = detail || (success ? '任务已完成' : '任务失败，请查看运行日志');
}

function keyValuesFromProgress(text) {
  const values = {};
  for (const match of text.matchAll(/([A-Za-z_]+)=([^\s]+)/g)) {
    values[match[1]] = match[2];
  }
  return values;
}

function parseProgressLine(line) {
  const text = String(line || '').trim();
  if (!text) return;

  let match = text.match(/^progress\s+(\d+)\s*\/\s*(\d+)(.*)$/i);
  if (match) {
    const done = Number(match[1]);
    const total = Number(match[2]);
    const values = keyValuesFromProgress(match[3] || '');
    const detailParts = [`已处理 ${done}/${total}`];
    if (values.exported) detailParts.push(`导出 ${values.exported}`);
    if (values.skipped) detailParts.push(`跳过 ${values.skipped}`);
    if (values.failures) detailParts.push(`失败 ${values.failures}`);
    updateProgress(done, total, detailParts.join('，'));
    return;
  }

  match = text.match(/^progress\s+(.+)$/i);
  if (match) {
    const values = keyValuesFromProgress(match[1]);
    const done = Number(values.done || 0);
    const queued = Number(values.queued || 0);
    const sourceLinks = Number(values.source_links || values.sourceLinkCount || 0);
    const total = Math.max(done + queued, sourceLinks);
    const detailParts = [`已处理 ${done}/${total || '?'}`];
    if (values.exported) detailParts.push(`导出 ${values.exported}`);
    if (values.skipped) detailParts.push(`跳过 ${values.skipped}`);
    if (values.failures) detailParts.push(`失败 ${values.failures}`);
    if (values.eta) detailParts.push(`预计剩余 ${values.eta}`);
    updateProgress(done, total, detailParts.join('，'));
    return;
  }

  match = text.match(/^\[(\d+)\s*\/\s*(\d+)\]\s*(.+)$/);
  if (match) {
    const done = Number(match[1]);
    const total = Number(match[2]);
    updateProgress(done, total, `正在处理 ${done}/${total}：${match[3]}`);
    return;
  }

  match = text.match(/开始批量导入.*total=(\d+)/);
  if (match) {
    updateProgress(0, Number(match[1]), `准备批量导入，共 ${match[1]} 篇`);
  }
}

function handlePythonProgress(data) {
  pythonProgressBuffer += String(data || '');
  const lines = pythonProgressBuffer.split(/\r?\n/);
  pythonProgressBuffer = lines.pop() || '';
  lines.forEach(parseProgressLine);
}

// Listen to Python logs
window.electronAPI.onPythonLog((data) => {
  log(data, 'info');
  handlePythonProgress(data);
});

// Tool switching
function switchTool(toolId) {
  currentTool = toolId;
  const config = TOOLS[toolId];

  // Update header
  document.getElementById('tool-title').textContent = config.title;
  document.getElementById('tool-description').textContent = config.description;

  // Update navigation
  document.querySelectorAll('.nav-item').forEach(item => {
    item.classList.toggle('active', item.dataset.tool === toolId);
  });

  // Load tool template
  const contentArea = document.getElementById('content-area');
  const template = document.getElementById(`template-${toolId}`);

  if (template) {
    contentArea.innerHTML = '';
    const clone = template.content.cloneNode(true);
    contentArea.appendChild(clone);
    initializeToolHandlers(toolId);
  } else if (toolId === 'feishu-import') {
    loadFeishuImportTool();
  }
}

// Initialize tool event handlers
function initializeToolHandlers(toolId) {
  const prefix = toolId;
  ensureTocSelector(toolId);

  const outputInput = document.getElementById(`${prefix}-output`);
  if (outputInput && !outputInput.value.trim()) {
    const defaults = {
      zsxq: 'exports/zsxq',
      yuque: 'exports/yuque',
      'yuque-import': 'exports/yuque',
      'yinxiang-import': 'exports/yinxiang',
      'feishu-export': 'exports/feishu',
      aliyun: 'exports/aliyun-thoughts',
      yinxiang: 'exports/yinxiang'
    };
    const suffix = defaults[toolId];
    const root = appPaths?.dataRoot || appPaths?.userData || appPaths?.projectRoot;
    if (suffix && root) {
      outputInput.value = `${root}/${suffix}`;
    }
  }

  // Browse button
  const browseBtn = document.getElementById(`${prefix}-browse-output`);
  if (browseBtn) {
    browseBtn.addEventListener('click', async () => {
      const dir = await window.electronAPI.selectDirectory({
        title: '选择输出目录',
        defaultPath: document.getElementById(`${prefix}-output`).value
      });
      if (dir) {
        document.getElementById(`${prefix}-output`).value = dir;
      }
    });
  }

  if (toolId === 'yinxiang-import') {
    initializeYinxiangImportHandlers();
    return;
  }

  // Login button
  const loginBtn = document.getElementById(`${prefix}-login`);
  if (loginBtn) {
    loginBtn.addEventListener('click', () => {
      if (toolId === 'yinxiang') {
        handleYinxiangLogin();
      } else {
        handleLogin(toolId);
      }
    });
  }

  const loginDoneBtn = document.getElementById(`${prefix}-login-done`);
  if (loginDoneBtn) {
    loginDoneBtn.addEventListener('click', () => confirmLoginDone(toolId));
  }

  // Export button
  const exportBtn = document.getElementById(`${prefix}-export`);
  if (exportBtn) {
    exportBtn.addEventListener('click', () => handleExport(toolId));
  }

  const scanTocBtn = document.getElementById(`${prefix}-scan-toc`);
  if (scanTocBtn) {
    scanTocBtn.addEventListener('click', () => handleScanToc(toolId));
  }

  // Stop button
  const stopBtn = document.getElementById(`${prefix}-stop`);
  if (stopBtn) {
    stopBtn.addEventListener('click', () => handleStop());
  }

  // Open directory button
  const openDirBtn = document.getElementById(`${prefix}-open-dir`);
  if (openDirBtn) {
    openDirBtn.addEventListener('click', async () => {
      const output = document.getElementById(`${prefix}-output`).value.trim();
      if (output) {
        await window.electronAPI.openPath(output);
      } else {
        alert('请先指定输出目录');
      }
    });
  }

  if (toolId === 'yuque-import') {
    initializeYuqueImportHandlers();
  }
}

// Handle login
async function handleLogin(toolId) {
  const config = TOOLS[toolId];
  const prefix = toolId;

  const url = document.getElementById(`${prefix}-url`).value.trim();
  if (!url) {
    alert('请先填写 URL');
    return;
  }

  const args = [config.urlParam, url, '--login'];

  setRunning(true, toolId);
  startProgress(`登录：${config.title}`, '请在浏览器中完成登录，然后回到工具点击“我已完成登录，保存凭证”。');
  setLoginDoneButton(toolId, true);
  log(`开始登录：${config.title}`, 'info');
  log('请在浏览器中完成登录，登录成功并能看到目标页面后，回到工具点击“我已完成登录，保存凭证”。', 'info');

  try {
    const result = await window.electronAPI.runPythonCommand(config.script, args);
    if (result.success) {
      log('登录成功', 'success');
      finishProgress(true, '登录凭证已保存');
    } else {
      log(`登录失败：${result.error}`, 'error');
      finishProgress(false, '登录失败，请查看运行日志');
    }
  } catch (error) {
    log(`错误：${formatError(error)}`, 'error');
    finishProgress(false, '登录出错，请查看运行日志');
  } finally {
    setLoginDoneButton(toolId, false);
    setRunning(false, toolId);
  }
}

async function handleYinxiangLogin() {
  const config = TOOLS.yinxiang;
  const username = document.getElementById('yinxiang-username')?.value.trim();
  const password = document.getElementById('yinxiang-password')?.value || '';
  if (!username || !password) {
    alert('请先填写印象笔记账号和密码。');
    return;
  }

  const args = ['--init-auth', '--username', username, '--password-stdin'];
  setRunning(true, 'yinxiang');
  startProgress(`登录并同步：${config.title}`, '正在初始化本地同步库并同步笔记...');
  log(`开始登录并同步：${config.title}`, 'info');

  try {
    const result = await window.electronAPI.runPythonCommand(config.script, args, {
      stdinText: `${password}\n`
    });
    if (result.success) {
      log('印象笔记凭证保存并同步完成', 'success');
      if (result.data) log(JSON.stringify(result.data, null, 2), 'success');
      finishProgress(true, '印象笔记已同步，可以读取目录');
    } else {
      log(`登录同步失败：${result.error}`, 'error');
      finishProgress(false, '登录同步失败，请查看运行日志');
    }
  } catch (error) {
    log(`错误：${formatError(error)}`, 'error');
    finishProgress(false, '登录同步出错，请查看运行日志');
  } finally {
    setRunning(false, 'yinxiang');
  }
}

async function confirmLoginDone(toolId) {
  const result = await window.electronAPI.sendPythonInput('\n');
  const button = document.getElementById(`${toolId}-login-done`);
  if (button) button.disabled = true;
  if (result.success) {
    startProgress('保存登录凭证', '正在从浏览器读取登录 Cookie...');
  } else {
    finishProgress(false, '没有正在等待确认的登录任务');
  }
  log(result.success ? '已确认登录完成，正在保存凭证...' : result.error, result.success ? 'info' : 'error');
}

function setLoginDoneButton(toolId, visible) {
  const button = document.getElementById(`${toolId}-login-done`);
  if (!button) return;
  button.hidden = !visible;
  button.disabled = !visible;
}

function formatError(error) {
  if (!error) return '未知错误';
  if (typeof error === 'string') return error;
  return error.error || error.message || JSON.stringify(error);
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function buildYuqueImportArgs(options = {}) {
  const url = document.getElementById('yuque-import-url')?.value.trim();
  const sourceDir = document.getElementById('yuque-import-output')?.value.trim();
  if (!url) throw new Error('请填写目标语雀知识库 URL');
  if (!sourceDir) throw new Error('请选择 Markdown 目录');

  const args = ['--target-book-url', url, '--source-dir', sourceDir];
  if (options.saveConfig) {
    args.push('--save-config');
    return args;
  }
  if (options.plan) {
    args.push('--plan');
    return args;
  }
  if (options.single) {
    args.push('--api-import-one', '--max-import', '1', '--yes');
  } else {
    args.push('--api-import-all', '--yes');
  }

  const updateExisting = document.getElementById('yuque-import-update-existing');
  if (updateExisting && !updateExisting.checked) {
    args.push('--skip-existing');
  } else {
    args.push('--update-existing');
  }
  return args;
}

async function runYuqueImportCommand(args, title, detail = '正在处理语雀导入任务...') {
  setRunning(true, 'yuque-import');
  startProgress(title, detail);
  log(`开始：${title}`, 'info');
  try {
    const result = await window.electronAPI.runPythonCommand('import_yuque.py', args);
    if (result.success) {
      log(`${title}完成`, 'success');
      if (result.data) log(JSON.stringify(result.data, null, 2), 'success');
      finishProgress(true, `${title}完成`);
    } else {
      log(`${title}失败：${result.error}`, 'error');
      finishProgress(false, `${title}失败，请查看运行日志`);
    }
  } catch (error) {
    log(`错误：${formatError(error)}`, 'error');
    finishProgress(false, `${title}出错，请查看运行日志`);
  } finally {
    setRunning(false, 'yuque-import');
  }
}

function initializeYuqueImportHandlers() {
  document.getElementById('yuque-import-save-config')?.addEventListener('click', async () => {
    try {
      await runYuqueImportCommand(buildYuqueImportArgs({ saveConfig: true }), '保存语雀导入配置', '正在保存本机配置...');
    } catch (error) {
      alert(formatError(error));
    }
  });

  document.getElementById('yuque-import-plan')?.addEventListener('click', async () => {
    try {
      await runYuqueImportCommand(buildYuqueImportArgs({ plan: true }), '生成语雀导入计划', '正在扫描本地 Markdown 并验证目标知识库...');
    } catch (error) {
      alert(formatError(error));
    }
  });

  document.getElementById('yuque-import-one')?.addEventListener('click', async () => {
    try {
      await runYuqueImportCommand(buildYuqueImportArgs({ single: true }), '语雀单篇导入测试', '正在导入第一篇 Markdown...');
    } catch (error) {
      alert(formatError(error));
    }
  });
}

function buildYinxiangImportArgs(options = {}) {
  const sourceDir = document.getElementById('yinxiang-import-source')?.value.trim();
  const sourceFile = document.getElementById('yinxiang-import-source-file')?.value.trim();
  const notebook = document.getElementById('yinxiang-import-notebook')?.value.trim();
  const stack = document.getElementById('yinxiang-import-stack')?.value.trim();
  const maxImport = document.getElementById('yinxiang-import-max')?.value;
  const delay = document.getElementById('yinxiang-import-delay')?.value;

  if (!sourceDir) throw new Error('请选择 Markdown 目录');

  const args = ['--source-dir', sourceDir];
  if (sourceFile) args.push('--source-file', sourceFile);
  if (notebook) args.push('--notebook', notebook);
  if (stack) args.push('--stack', stack);
  if (maxImport && parseInt(maxImport, 10) > 0) args.push('--max-import', maxImport);
  if (delay) args.push('--request-delay', delay);
  args.push('--progress-every', '1');

  const preserveFolders = document.getElementById('yinxiang-import-preserve-folders');
  if (preserveFolders && preserveFolders.checked) {
    args.push('--preserve-folders');
  }

  if (options.plan) {
    args.push('--scan-source');
  } else if (options.single) {
    args.push('--import-one', '--yes');
  } else {
    args.push('--import-all', '--yes');
  }
  return args;
}

async function handleYinxiangImportLogin() {
  const username = document.getElementById('yinxiang-import-username')?.value.trim();
  const password = document.getElementById('yinxiang-import-password')?.value || '';
  if (!username || !password) {
    alert('请填写印象笔记账号和密码。已有凭证时可以直接扫描目录或导入。');
    return;
  }

  const args = ['--init-auth', '--username', username, '--password-stdin'];
  setRunning(true, 'yinxiang-import');
  startProgress('登录并同步印象笔记凭证', '正在初始化本地同步库并同步笔记...');
  log('开始：登录并同步印象笔记凭证', 'info');
  try {
    const result = await window.electronAPI.runPythonCommand('export_yinxiang.py', args, {
      stdinText: `${password}\n`
    });
    if (result.success) {
      log('印象笔记凭证已保存并同步完成', 'success');
      if (result.data) log(JSON.stringify(result.data, null, 2), 'success');
      finishProgress(true, '印象笔记凭证已保存，可以开始导入 Markdown');
    } else {
      log(`登录同步失败：${result.error}`, 'error');
      finishProgress(false, '登录同步失败，请查看运行日志');
    }
  } catch (error) {
    log(`错误：${formatError(error)}`, 'error');
    finishProgress(false, '登录同步出错，请查看运行日志');
  } finally {
    setRunning(false, 'yinxiang-import');
  }
}

async function runYinxiangImportCommand(args, title, detail = '正在处理印象笔记导入任务...') {
  setRunning(true, 'yinxiang-import');
  startProgress(title, detail);
  log(`开始：${title}`, 'info');
  try {
    const result = await window.electronAPI.runPythonCommand('import_yinxiang.py', args);
    if (result.success) {
      log(`${title}完成`, 'success');
      if (result.data) log(JSON.stringify(result.data, null, 2), 'success');
      finishProgress(true, `${title}完成`);
      return result.data || {};
    }
    log(`${title}失败：${result.error}`, 'error');
    finishProgress(false, `${title}失败，请查看运行日志`);
  } catch (error) {
    log(`错误：${formatError(error)}`, 'error');
    finishProgress(false, `${title}出错，请查看运行日志`);
  } finally {
    setRunning(false, 'yinxiang-import');
  }
  return null;
}

function initializeYinxiangImportHandlers() {
  document.getElementById('yinxiang-import-login')?.addEventListener('click', handleYinxiangImportLogin);

  document.getElementById('yinxiang-import-browse-source')?.addEventListener('click', async () => {
    const current = document.getElementById('yinxiang-import-source')?.value || '';
    const dir = await window.electronAPI.selectDirectory({
      title: '选择 Markdown 目录',
      defaultPath: current
    });
    if (dir) document.getElementById('yinxiang-import-source').value = dir;
  });

  document.getElementById('yinxiang-import-browse-file')?.addEventListener('click', async () => {
    const file = await window.electronAPI.selectFile({
      title: '选择 Markdown 文件',
      filters: [{ name: 'Markdown 文件', extensions: ['md'] }, { name: '所有文件', extensions: ['*'] }]
    });
    if (file) document.getElementById('yinxiang-import-source-file').value = file;
  });

  document.getElementById('yinxiang-import-plan')?.addEventListener('click', async () => {
    try {
      await runYinxiangImportCommand(buildYinxiangImportArgs({ plan: true }), '扫描印象笔记导入目录', '正在扫描本地 Markdown 文件...');
    } catch (error) {
      alert(formatError(error));
    }
  });

  document.getElementById('yinxiang-import-one')?.addEventListener('click', async () => {
    try {
      if (confirm('这会在印象笔记中创建一篇测试笔记。确认继续吗？')) {
        await runYinxiangImportCommand(buildYinxiangImportArgs({ single: true }), '印象笔记单篇导入测试', '正在导入第一篇 Markdown...');
      }
    } catch (error) {
      alert(formatError(error));
    }
  });

  document.getElementById('yinxiang-import-export')?.addEventListener('click', async () => {
    try {
      if (confirm('这会向印象笔记批量创建笔记。确认继续吗？')) {
        await runYinxiangImportCommand(buildYinxiangImportArgs(), '印象笔记批量导入', '正在批量导入 Markdown...');
      }
    } catch (error) {
      alert(formatError(error));
    }
  });

  document.getElementById('yinxiang-import-stop')?.addEventListener('click', handleStop);
  document.getElementById('yinxiang-import-open-dir')?.addEventListener('click', async () => {
    const dir = document.getElementById('yinxiang-import-source')?.value.trim();
    if (dir) {
      await window.electronAPI.openPath(dir);
    } else {
      alert('请先选择 Markdown 目录');
    }
  });
}

function ensureTocSelector(toolId) {
  const config = TOOLS[toolId];
  if (!config || config.isImport) return;

  if (!tocStates[toolId]) {
    tocStates[toolId] = { loaded: false, nodes: [], selected: new Set() };
  }

  const panel = document.querySelector('#content-area .tool-panel');
  const actionSection = document.querySelector('#content-area .action-section');
  if (!panel || !actionSection) return;

  if (!document.getElementById(`${toolId}-scan-toc`)) {
    const scanButton = document.createElement('button');
    scanButton.className = 'btn-primary';
    scanButton.id = `${toolId}-scan-toc`;
    scanButton.textContent = '读取目录';
    const exportButton = document.getElementById(`${toolId}-export`);
    actionSection.insertBefore(scanButton, exportButton || null);
  }

  if (!document.getElementById(`${toolId}-toc-section`)) {
    const section = document.createElement('section');
    section.className = 'toc-section';
    section.id = `${toolId}-toc-section`;
    section.innerHTML = `
      <div class="toc-header">
        <div>
          <strong>目录选择</strong>
          <p id="${toolId}-toc-status">目录：未读取，未读取时默认导出全部。</p>
        </div>
        <div class="toc-actions">
          <button class="btn-secondary" id="${toolId}-toc-all" type="button">全选</button>
          <button class="btn-secondary" id="${toolId}-toc-none" type="button">全不选</button>
          <button class="btn-secondary" id="${toolId}-toc-invert" type="button">反选</button>
        </div>
      </div>
      <div class="toc-list" id="${toolId}-toc-list">
        <div class="toc-empty">先点击“读取目录”，再选择要导出的内容。</div>
      </div>
      <p class="helper-note">读取目录后，只会导出已勾选的文档；点击文件夹可批量切换其下所有文档。</p>
    `;
    panel.insertBefore(section, actionSection);
  }

  document.getElementById(`${toolId}-toc-all`)?.addEventListener('click', () => setAllTocSelected(toolId, true));
  document.getElementById(`${toolId}-toc-none`)?.addEventListener('click', () => setAllTocSelected(toolId, false));
  document.getElementById(`${toolId}-toc-invert`)?.addEventListener('click', () => invertTocSelection(toolId));
  document.getElementById(`${toolId}-toc-list`)?.addEventListener('click', (event) => {
    const item = event.target.closest('[data-node-id]');
    if (item) toggleTocNode(toolId, item.dataset.nodeId);
  });

  if (tocStates[toolId]?.loaded) {
    renderToc(toolId);
  }
}

function buildExportArgs(toolId, options = {}) {
  const config = TOOLS[toolId];
  const prefix = toolId;
  const forScan = Boolean(options.forScan);
  const includeSelection = options.includeSelection !== false;
  const url = document.getElementById(`${prefix}-url`)?.value.trim();
  const output = document.getElementById(`${prefix}-output`)?.value.trim();

  if (!config.noUrl && !url) {
    throw new Error('请先填写 URL');
  }

  if (toolId === 'yuque-import') {
    return buildYuqueImportArgs(options);
  }

  const args = config.noUrl ? [] : [config.urlParam, url];
  if (forScan) {
    args.push('--scan-toc');
  } else if (output) {
    args.push(config.outputParam, output);
  }

  const incrementalCheckbox = document.getElementById(`${prefix}-incremental`);
  if (!forScan && incrementalCheckbox && incrementalCheckbox.checked) {
    args.push('--incremental');
  }

  const delayInput = document.getElementById(`${prefix}-delay`);
  if (delayInput && delayInput.value) {
    args.push('--request-delay', delayInput.value);
  }

  const jitterInput = document.getElementById(`${prefix}-jitter`);
  if (jitterInput && jitterInput.value) {
    args.push('--request-jitter', jitterInput.value);
  }

  if (!forScan) {
    args.push('--progress-every', '1');
  }

  if (toolId === 'zsxq') {
    const maxDepth = document.getElementById('zsxq-max-depth')?.value;
    if (maxDepth) args.push('--max-depth', maxDepth);

    const includeComments = document.getElementById('zsxq-include-comments');
    if (!forScan && includeComments && includeComments.checked) {
      args.push('--include-comments');
    }
  }

  if (toolId === 'yuque') {
    const downloadAttachments = document.getElementById('yuque-download-attachments');
    if (!forScan && downloadAttachments && !downloadAttachments.checked) {
      args.push('--skip-attachments');
    }
  }

  if (!forScan && includeSelection) {
    args.push(...selectedTocArgs(toolId));
  }

  return args;
}

function normalizeTocNodes(toolId, data) {
  const nodes = [];
  if (toolId === 'zsxq') {
    (data.groups || []).forEach((group, groupIndex) => {
      const groupId = `zsxq-group:${group.groupIndex ?? groupIndex}`;
      nodes.push({
        nodeId: groupId,
        exportId: '',
        title: group.groupTitle || `分组 ${groupIndex + 1}`,
        parentNodeId: '',
        selectable: false
      });
      (group.topics || []).forEach((topic, topicIndex) => {
        const key = String(topic.key || `toc:${group.groupIndex ?? groupIndex}:${topic.topicIndex ?? topicIndex}`);
        nodes.push({
          nodeId: `zsxq:${key}`,
          exportId: key,
          title: topic.title || `未命名文章 ${topicIndex + 1}`,
          parentNodeId: groupId,
          selectable: true
        });
      });
    });
    return nodes;
  }

  if (toolId === 'yuque') {
    (data.toc || []).forEach((item) => {
      const uuid = String(item.uuid || item.id || item.doc_id || '');
      if (!uuid) return;
      const exportId = String(item.doc_id || item.uuid || '');
      nodes.push({
        nodeId: `yuque:${uuid}`,
        exportId,
        title: item.title || '未命名',
        parentNodeId: item.parent_uuid ? `yuque:${item.parent_uuid}` : '',
        selectable: item.type === 'DOC' && Boolean(exportId)
      });
    });
    return nodes;
  }

  if (toolId === 'feishu-export') {
    (data.ordered || []).forEach((item) => {
      const token = String(item.wiki_token || item.token || '');
      if (!token) return;
      nodes.push({
        nodeId: `feishu:${token}`,
        exportId: token,
        title: item.title || '未命名',
        parentNodeId: item.parent_wiki_token ? `feishu:${item.parent_wiki_token}` : '',
        selectable: Boolean(item.url)
      });
    });
    return nodes;
  }

  if (toolId === 'aliyun') {
    (data.nodes || []).forEach((item) => {
      const id = String(item.id || '');
      if (!id) return;
      nodes.push({
        nodeId: `aliyun:${id}`,
        exportId: id,
        title: item.title || '未命名',
        parentNodeId: item.parent_id ? `aliyun:${item.parent_id}` : '',
        selectable: item.type === 'document'
      });
    });
  }
  if (toolId === 'yinxiang') {
    (data.notebooks || []).forEach((notebook, notebookIndex) => {
      const stack = String(notebook.stack || '');
      let parentNodeId = '';
      if (stack) {
        parentNodeId = `yinxiang-stack:${stack}`;
        if (!nodes.some((node) => node.nodeId === parentNodeId)) {
          nodes.push({
            nodeId: parentNodeId,
            exportId: '',
            title: stack,
            parentNodeId: '',
            selectable: false
          });
        }
      }
      const notebookId = String(notebook.guid || `notebook-${notebookIndex}`);
      const notebookNodeId = `yinxiang-notebook:${notebookId}`;
      nodes.push({
        nodeId: notebookNodeId,
        exportId: '',
        title: notebook.name || `笔记本 ${notebookIndex + 1}`,
        parentNodeId,
        selectable: false
      });
      (notebook.notes || []).forEach((note, noteIndex) => {
        const guid = String(note.guid || '');
        if (!guid) return;
        nodes.push({
          nodeId: `yinxiang-note:${guid}`,
          exportId: guid,
          title: note.title || `未命名笔记 ${noteIndex + 1}`,
          parentNodeId: notebookNodeId,
          selectable: true
        });
      });
    });
  }
  return nodes;
}

function tocNodeMaps(nodes) {
  const byId = new Map(nodes.map((node) => [node.nodeId, node]));
  const children = new Map();
  nodes.forEach((node) => {
    const parent = node.parentNodeId && byId.has(node.parentNodeId) ? node.parentNodeId : '';
    if (!children.has(parent)) children.set(parent, []);
    children.get(parent).push(node);
  });
  return { byId, children };
}

function descendantExportIds(nodes, nodeId) {
  const { children } = tocNodeMaps(nodes);
  const result = [];
  const visit = (id) => {
    (children.get(id) || []).forEach((child) => {
      if (child.selectable && child.exportId) result.push(child.exportId);
      visit(child.nodeId);
    });
  };
  const node = nodes.find((item) => item.nodeId === nodeId);
  if (node?.selectable && node.exportId) result.push(node.exportId);
  visit(nodeId);
  return result;
}

function selectableTocIds(nodes) {
  return nodes.filter((node) => node.selectable && node.exportId).map((node) => node.exportId);
}

function setAllTocSelected(toolId, selected) {
  const state = tocStates[toolId];
  if (!state?.loaded) {
    alert('请先点击“读取目录”。');
    return;
  }
  state.selected = new Set(selected ? selectableTocIds(state.nodes) : []);
  renderToc(toolId);
}

function invertTocSelection(toolId) {
  const state = tocStates[toolId];
  if (!state?.loaded) {
    alert('请先点击“读取目录”。');
    return;
  }
  const all = selectableTocIds(state.nodes);
  state.selected = new Set(all.filter((id) => !state.selected.has(id)));
  renderToc(toolId);
}

function toggleTocNode(toolId, nodeId) {
  const state = tocStates[toolId];
  if (!state?.loaded) return;
  const ids = descendantExportIds(state.nodes, nodeId);
  if (!ids.length) return;
  const allSelected = ids.every((id) => state.selected.has(id));
  ids.forEach((id) => {
    if (allSelected) {
      state.selected.delete(id);
    } else {
      state.selected.add(id);
    }
  });
  renderToc(toolId);
}

function renderToc(toolId) {
  const state = tocStates[toolId];
  const list = document.getElementById(`${toolId}-toc-list`);
  const status = document.getElementById(`${toolId}-toc-status`);
  if (!list || !status || !state?.loaded) return;

  const { children } = tocNodeMaps(state.nodes);
  const allIds = selectableTocIds(state.nodes);
  status.textContent = `目录：共 ${allIds.length} 篇，已选择 ${state.selected.size} 篇`;

  const renderNode = (node, depth) => {
    const ids = descendantExportIds(state.nodes, node.nodeId);
    const selectedCount = ids.filter((id) => state.selected.has(id)).length;
    const checkClass = selectedCount === ids.length && ids.length ? 'checked' : (selectedCount ? 'partial' : '');
    const count = node.selectable ? '' : `<span class="toc-count">${selectedCount}/${ids.length}</span>`;
    const childHtml = (children.get(node.nodeId) || []).map((child) => renderNode(child, depth + 1)).join('');
    return `
      <div class="toc-node">
        <button class="toc-item" type="button" data-node-id="${escapeHtml(node.nodeId)}" style="--depth:${depth}">
          <span class="toc-box ${checkClass}"></span>
          <span class="toc-title">${escapeHtml(node.title)}</span>
          ${count}
        </button>
        ${childHtml}
      </div>
    `;
  };

  const html = (children.get('') || []).map((node) => renderNode(node, 0)).join('');
  list.innerHTML = html || '<div class="toc-empty">没有读取到可选择的目录。</div>';
}

function selectedTocArgs(toolId) {
  const state = tocStates[toolId];
  if (!state?.loaded) return [];
  const selected = Array.from(state.selected);
  if (!selected.length) {
    throw new Error('目录已读取，但没有选择任何文档。请至少勾选一篇，或重新读取目录。');
  }
  const args = [];
  if (toolId === 'zsxq') {
    args.push('--toc-mode', 'toc');
    selected.forEach((id) => args.push('--toc-key', id));
  } else {
    selected.forEach((id) => args.push('--doc-id', id));
  }
  return args;
}

async function handleScanToc(toolId) {
  const config = TOOLS[toolId];
  let args;
  try {
    args = buildExportArgs(toolId, { forScan: true, includeSelection: false });
  } catch (error) {
    alert(formatError(error));
    return;
  }

  setRunning(true, toolId);
  startProgress(`读取目录：${config.title}`, '正在读取远端目录结构...');
  log(`开始读取目录：${config.title}`, 'info');

  try {
    const result = await window.electronAPI.runPythonCommand(config.script, args);
    if (!result.success) {
      log(`读取目录失败：${result.error}`, 'error');
      finishProgress(false, '读取目录失败，请查看运行日志');
      return;
    }
    const nodes = normalizeTocNodes(toolId, result.data || {});
    tocStates[toolId] = {
      loaded: true,
      nodes,
      selected: new Set(selectableTocIds(nodes))
    };
    renderToc(toolId);
    log(`目录读取完成：共 ${selectableTocIds(nodes).length} 篇，默认已全选。`, 'success');
    finishProgress(true, `目录读取完成，共 ${selectableTocIds(nodes).length} 篇`);
  } catch (error) {
    log(`错误：${formatError(error)}`, 'error');
    finishProgress(false, '读取目录出错，请查看运行日志');
  } finally {
    setRunning(false, toolId);
  }
}

// Handle export
async function handleExport(toolId) {
  const config = TOOLS[toolId];
  const actionName = config.isImport ? '导入' : '导出';
  let args;
  try {
    args = buildExportArgs(toolId);
  } catch (error) {
    alert(formatError(error));
    return;
  }

  setRunning(true, toolId);
  startProgress(`${actionName}：${config.title}`, `正在准备${actionName}任务...`);
  log(`开始${actionName}：${config.title}`, 'info');
  const state = tocStates[toolId];
  if (state?.loaded) {
    log(`本次按目录选择导出：已选择 ${state.selected.size} 篇。`, 'info');
    updateProgress(0, state.selected.size, `已选择 ${state.selected.size} 篇，正在读取远端内容...`);
  }

  try {
    const result = await window.electronAPI.runPythonCommand(config.script, args);
    if (result.success) {
      log(`${actionName}完成`, 'success');
      if (result.data) {
        log(JSON.stringify(result.data, null, 2), 'success');
      }
      finishProgress(true, `${actionName}完成`);
    } else {
      log(`${actionName}失败：${result.error}`, 'error');
      finishProgress(false, `${actionName}失败，请查看运行日志`);
    }
  } catch (error) {
    log(`错误：${formatError(error)}`, 'error');
    finishProgress(false, `${actionName}出错，请查看运行日志`);
  } finally {
    setRunning(false, toolId);
  }
}

// Handle stop
async function handleStop() {
  const result = await window.electronAPI.stopPythonProcess();
  if (result.success) {
    startProgress('正在停止任务', '已发送停止请求，等待当前进程退出...');
  }
  log(result.success ? '已发送停止请求' : result.error, result.success ? 'info' : 'error');
}

// Set running state
function setRunning(running, toolId) {
  isRunning = running;
  const prefix = toolId || currentTool;

  // Disable/enable buttons
  const exportBtn = document.getElementById(`${prefix}-export`);
  const loginBtn = document.getElementById(`${prefix}-login`);
  const scanTocBtn = document.getElementById(`${prefix}-scan-toc`);
  const stopBtn = document.getElementById(`${prefix}-stop`);

  if (exportBtn) exportBtn.disabled = running;
  if (loginBtn) loginBtn.disabled = running;
  if (scanTocBtn) scanTocBtn.disabled = running;
  if (stopBtn) stopBtn.disabled = !running;
  ['toc-all', 'toc-none', 'toc-invert', 'open-dir', 'plan', 'one', 'save-config', 'open-token'].forEach((suffix) => {
    const button = document.getElementById(`${prefix}-${suffix}`);
    if (button) button.disabled = running;
  });

  // Disable navigation while running
  document.querySelectorAll('.nav-item').forEach(item => {
    item.disabled = running;
    item.style.opacity = running ? '0.5' : '1';
    item.style.pointerEvents = running ? 'none' : 'auto';
  });
}

function feishuImportConfigPath() {
  if (appPaths?.userData) {
    return `${appPaths.userData}/feishu_import_config.json`;
  }
  if (appPaths?.projectRoot) {
    return `${appPaths.projectRoot}/.feishu_import_config.json`;
  }
  return '';
}

function feishuImportConfigFallbackPath() {
  return appPaths?.projectRoot ? `${appPaths.projectRoot}/.feishu_import_config.json` : '';
}

function buildFeishuPermissionUrl(scopes = FEISHU_IMPORT_REQUIRED_SCOPES) {
  const appId = document.getElementById('feishu-import-app-id')?.value.trim() || feishuImportConfig.app_id || '';
  if (!appId) {
    return '';
  }
  const query = new URLSearchParams({
    q: scopes.join(','),
    op_from: 'openapi',
    token_type: 'tenant'
  });
  return `${FEISHU_DEVELOPER_CONSOLE_URL}/${encodeURIComponent(appId)}/auth?${query.toString()}`;
}

function buildFeishuVersionUrl() {
  const appId = document.getElementById('feishu-import-app-id')?.value.trim() || feishuImportConfig.app_id || '';
  return appId ? `${FEISHU_DEVELOPER_CONSOLE_URL}/${encodeURIComponent(appId)}/version` : '';
}

function extractFeishuPermissionUrl(errorText) {
  const match = String(errorText || '').match(/https:\/\/open\.feishu\.cn\/app\/[^\s"'<>，。]+/);
  return match ? match[0].replace(/[.,，。]+$/, '') : '';
}

function extractFeishuScopes(errorText) {
  const scopes = new Set();
  const text = String(errorText || '');
  for (const match of text.matchAll(/\b(?:drive|docx|docs|wiki|sheets|base):[A-Za-z0-9_.:-]+/g)) {
    scopes.add(match[0]);
  }
  return normalizeFeishuScopes(scopes.size ? Array.from(scopes) : FEISHU_IMPORT_REQUIRED_SCOPES);
}

function normalizeFeishuScopes(scopes) {
  let unique = Array.from(new Set(scopes.filter(Boolean)));
  if (unique.includes('docx:document:write_only')) {
    unique = unique.filter((scope) => !['sheets:spreadsheet:write_only', 'base:app:update'].includes(scope));
  }
  const priority = new Map(FEISHU_SCOPE_PRIORITY.map((scope, index) => [scope, index]));
  return unique.sort((a, b) => (priority.get(a) ?? 999) - (priority.get(b) ?? 999) || a.localeCompare(b));
}

function explainFeishuPermissionError(errorText) {
  const text = String(errorText || '');
  if (text.includes('131006') || text.includes('no destination parent node permission')) {
    log('检测到目标 Wiki 父节点没有给当前飞书应用写入权限。这不是开放平台 scope 问题，请点击“授权目标 Wiki 文档应用”，或在目标 Wiki 右上角选择“... -> 更多 -> 添加文档应用”，把当前应用添加为可编辑。', 'error');
    return true;
  }
  if (text.includes('1061004') && text.includes('forbidden')) {
    log('检测到飞书拒绝上传文件。通常是当前企业自建应用缺少 drive:file:upload / drive:drive 权限、权限开通后没有发布新版本，或云空间文件夹 token 不属于当前应用可写范围。', 'error');
    log('建议先点击“初始化开放平台权限”并发布版本；如果仍失败，把“云空间文件夹 token”留空，让工具自动获取可用目录。', 'info');
    return true;
  }
  return false;
}

async function openFeishuPermissionPage(scopes = FEISHU_IMPORT_REQUIRED_SCOPES) {
  const normalizedScopes = normalizeFeishuScopes(scopes);
  const url = buildFeishuPermissionUrl(normalizedScopes);
  if (!url) {
    alert('请先填写飞书 App ID，再打开 API 权限申请页。');
    return false;
  }
  const result = await window.electronAPI.openExternal(url);
  if (result.success) {
    log(`已打开飞书 API 权限申请页。建议开通：${normalizedScopes.join(', ')}`, 'info');
    log('如果弹窗里的复选框是灰色且“确认开通权限”不可点，通常表示这些权限已经开通过。下一步请进入“版本管理与发布”发布新版本。', 'info');
    return true;
  }
  log(`打开权限申请页失败：${result.error}`, 'error');
  return false;
}

async function openFeishuVersionPage() {
  const url = buildFeishuVersionUrl();
  if (!url) {
    alert('请先填写飞书 App ID，再打开版本发布页。');
    return false;
  }
  const result = await window.electronAPI.openExternal(url);
  log(result.success ? '已打开飞书版本管理与发布页。权限改动需要发布应用新版本后才会生效。' : `打开版本发布页失败：${result.error}`, result.success ? 'info' : 'error');
  return Boolean(result.success);
}

async function openFeishuTargetWikiPage() {
  const wikiUrl = document.getElementById('feishu-import-url')?.value.trim() || '';
  if (!wikiUrl) {
    alert('请先填写目标飞书 Wiki URL。');
    return false;
  }
  const result = await window.electronAPI.openExternal(wikiUrl);
  if (result.success) {
    log('已打开目标 Wiki 页面。若导入时报 131006，请在该页面右上角“... -> 更多 -> 添加文档应用”里添加当前应用为可编辑。', 'info');
    log('这一步属于目标知识库的数据权限，不是开放平台 API scope，所以不会出现在“开通权限”弹窗里。', 'info');
    return true;
  }
  log(`打开目标 Wiki 失败：${result.error}`, 'error');
  return false;
}

async function setupFeishuOpenapiPermissions() {
  return runFeishuImportCommand([...buildFeishuImportArgs(), '--setup-openapi-permissions'], '初始化开放平台权限');
}

async function setupFeishuTargetWikiDocApp() {
  if (!requireFeishuWikiUrl()) return null;
  return runFeishuImportCommand([...buildFeishuImportArgs(), '--setup-target-wiki-doc-app', '--yes'], '授权目标 Wiki 文档应用');
}

async function maybeOpenFeishuPermissionPage(errorText) {
  const text = String(errorText || '');
  if (explainFeishuPermissionError(text)) {
    return;
  }
  if (!text.includes('99991672') && !text.includes('Access denied') && !text.includes('1061004')) {
    return;
  }
  const existingUrl = extractFeishuPermissionUrl(text);
  const scopes = extractFeishuScopes(text);
  const preciseUrl = buildFeishuPermissionUrl(scopes);
  if (preciseUrl) {
    await openFeishuPermissionPage(scopes);
    return;
  }
  if (existingUrl) {
    const result = await window.electronAPI.openExternal(existingUrl);
    log(result.success ? '检测到飞书应用权限不足，已打开飞书返回的权限申请页。' : `打开权限申请页失败：${result.error}`, result.success ? 'info' : 'error');
    return;
  }
  await openFeishuPermissionPage(scopes);
}

function setInputValueIfEmpty(id, value) {
  const input = document.getElementById(id);
  if (input && value && !input.value.trim()) {
    input.value = value;
  }
}

async function readJsonFileIfExists(filePath) {
  if (!filePath) return null;
  const exists = await window.electronAPI.fileExists(filePath);
  if (!exists) return null;
  const result = await window.electronAPI.readFile(filePath);
  if (!result.success) return null;
  try {
    return JSON.parse(result.content);
  } catch (_error) {
    return null;
  }
}

async function loadFeishuImportConfigIntoForm() {
  const primary = feishuImportConfigPath();
  const fallback = feishuImportConfigFallbackPath();
  const config = (await readJsonFileIfExists(primary)) || (fallback !== primary ? await readJsonFileIfExists(fallback) : null);
  if (!config || typeof config !== 'object') return;
  feishuImportConfig = config;
  setInputValueIfEmpty('feishu-import-app-id', config.app_id);
  setInputValueIfEmpty('feishu-import-app-secret', config.app_secret);
  setInputValueIfEmpty('feishu-import-space-id', config.space_id);
  setInputValueIfEmpty('feishu-import-parent-token', config.parent_wiki_token);
  log('已读取本机飞书导入 API 配置', 'info');
}

async function saveFeishuImportConfigFromForm() {
  const appId = document.getElementById('feishu-import-app-id').value.trim();
  const appSecret = document.getElementById('feishu-import-app-secret').value.trim();
  if (!appId || !appSecret) {
    alert('请先填写飞书 App ID 和 App Secret');
    return;
  }
  const configPath = feishuImportConfigPath();
  if (!configPath) {
    alert('无法获取本机配置目录');
    return;
  }
  const config = {
    ...feishuImportConfig,
    app_id: appId,
    app_secret: appSecret,
    space_id: document.getElementById('feishu-import-space-id')?.value.trim() || feishuImportConfig.space_id || '',
    parent_wiki_token: document.getElementById('feishu-import-parent-token')?.value.trim() || feishuImportConfig.parent_wiki_token || '',
    obj_type: feishuImportConfig.obj_type || 'docx'
  };
  const result = await window.electronAPI.writeFile(configPath, JSON.stringify(config, null, 2));
  if (result.success) {
    feishuImportConfig = config;
    log(`飞书导入 API 配置已保存：${configPath}`, 'success');
    alert('已保存到本机配置文件。下次打开会自动读取。');
  } else {
    log(`保存配置失败：${result.error}`, 'error');
    alert(`保存配置失败：${result.error}`);
  }
}

// Load Feishu Import Tool (reuse existing import code)
function loadFeishuImportTool() {
  const contentArea = document.getElementById('content-area');
  contentArea.innerHTML = `
    <div class="tool-panel">
      <section class="form-section">
        <div class="form-group">
          <label for="feishu-import-url">目标飞书 Wiki URL <span class="required">*</span></label>
          <input type="text" id="feishu-import-url" placeholder="https://<tenant>.feishu.cn/wiki/<token>">
        </div>
        <div class="form-group">
          <label for="feishu-import-source">本地 Markdown 目录 <span class="required">*</span></label>
          <div class="input-with-button">
            <input type="text" id="feishu-import-source" placeholder="选择包含 Markdown 文件的目录">
            <button class="btn-secondary" id="feishu-import-browse-source">浏览</button>
          </div>
        </div>
        <div class="form-group">
          <label for="feishu-import-source-file">单篇测试文件（可选）</label>
          <div class="input-with-button">
            <input type="text" id="feishu-import-source-file" placeholder="留空则使用目录内第一篇">
            <button class="btn-secondary" id="feishu-import-browse-file">浏览</button>
          </div>
        </div>
        <div class="form-row">
          <div class="form-group flex-1">
            <label for="feishu-import-app-id">飞书 App ID <span class="required">*</span></label>
            <input type="text" id="feishu-import-app-id" placeholder="从飞书开放平台获取">
          </div>
          <div class="form-group flex-1">
            <label for="feishu-import-app-secret">飞书 App Secret <span class="required">*</span></label>
            <input type="password" id="feishu-import-app-secret" placeholder="保密信息">
          </div>
        </div>
        <div class="setup-card">
          <div>
            <strong>首次配置分三步</strong>
            <p>第一步开通导入需要的开放平台 API scope；权限变化后必须发布应用新版本。</p>
            <p class="muted">第二步检查应用身份。第三步把当前企业自建应用添加到目标 Wiki 的“文档应用”列表，并设置为可编辑。</p>
          </div>
          <div class="setup-actions">
            <button class="btn-secondary" id="feishu-import-open-console">打开飞书开放平台</button>
            <button class="btn-secondary" id="feishu-import-save-config">保存 API 配置</button>
            <button class="btn-secondary" id="feishu-import-setup-permissions">初始化开放平台权限</button>
            <button class="btn-secondary" id="feishu-import-open-version">发布版本页</button>
            <button class="btn-secondary" id="feishu-import-check-app">检查应用身份</button>
            <button class="btn-secondary" id="feishu-import-setup-target-app">授权目标 Wiki 文档应用</button>
          </div>
        </div>
        <label class="checkbox-label">
          <input type="checkbox" id="feishu-import-repair-images" checked>
          <span>修复本地图片</span>
        </label>
        <label class="checkbox-label">
          <input type="checkbox" id="feishu-import-require-image-repair">
          <span>图片修复失败时中断</span>
        </label>
        <details class="advanced-section">
          <summary>高级参数（通常不用改）</summary>
          <div class="advanced-content">
            <div class="form-row">
              <div class="form-group flex-1">
                <label for="feishu-import-delay">请求延迟秒</label>
                <input type="number" id="feishu-import-delay" value="0.8" min="0" step="0.1">
              </div>
              <div class="form-group flex-1">
                <label for="feishu-import-jitter">随机浮动秒</label>
                <input type="number" id="feishu-import-jitter" value="0.4" min="0" step="0.1">
              </div>
              <div class="form-group flex-1">
                <label for="feishu-import-max">最多导入数量</label>
                <input type="number" id="feishu-import-max" value="0" min="0">
              </div>
            </div>
            <div class="form-row">
              <div class="form-group flex-1">
                <label for="feishu-import-space-id">Wiki spaceId（可自动探测）</label>
                <input type="text" id="feishu-import-space-id" placeholder="留空自动探测">
              </div>
              <div class="form-group flex-1">
                <label for="feishu-import-parent-token">父级 Wiki token（可自动探测）</label>
                <input type="text" id="feishu-import-parent-token" placeholder="留空使用 URL 中的 token">
              </div>
            </div>
            <label class="checkbox-label">
              <input type="checkbox" id="feishu-import-move-to-wiki" checked>
              <span>导入后移动到目标 Wiki</span>
            </label>
            <label class="checkbox-label">
              <input type="checkbox" id="feishu-import-skip-rename">
              <span>跳过自动重命名</span>
            </label>
          </div>
        </details>
        <details class="advanced-section">
          <summary>排障工具（一般不用）</summary>
          <div class="advanced-content">
            <p class="muted helper-note">只有权限检测不通过、需要人工检查目标 Wiki，或想打开本地 Markdown 目录时再用。</p>
            <div class="setup-actions utility-actions">
              <button class="btn-secondary" id="feishu-import-open-permission">权限助手</button>
              <button class="btn-secondary" id="feishu-import-open-target-wiki">打开目标 Wiki</button>
              <button class="btn-secondary" id="feishu-import-open-dir">打开本地目录</button>
            </div>
          </div>
        </details>
        <div class="info-box">
          <p>登录、探测、生成计划是只读操作；导入会创建飞书文档，点击前会再次确认。App Secret 只保存到本机配置文件，不会提交到仓库。</p>
        </div>
      </section>
      <section class="action-section">
        <button class="btn-primary" id="feishu-import-login">登录并保存凭证</button>
        <button class="btn-secondary login-done-button" id="feishu-import-login-done" hidden>我已完成登录，保存凭证</button>
        <button class="btn-primary" id="feishu-import-probe">探测目标 Wiki</button>
        <button class="btn-primary" id="feishu-import-plan">生成计划</button>
        <button class="btn-primary" id="feishu-import-one">单篇导入测试</button>
        <button class="btn-primary" id="feishu-import-all">批量导入</button>
        <button class="btn-danger" id="feishu-import-stop" disabled>停止</button>
      </section>
    </div>
  `;
  initializeFeishuImportHandlers();
}

function buildFeishuImportArgs() {
  const args = [];
  const configPath = feishuImportConfigPath();
  const wikiUrl = document.getElementById('feishu-import-url').value.trim();
  const sourceDir = document.getElementById('feishu-import-source').value.trim();
  const sourceFile = document.getElementById('feishu-import-source-file').value.trim();
  const appId = document.getElementById('feishu-import-app-id').value.trim();
  const appSecret = document.getElementById('feishu-import-app-secret').value.trim();
  const spaceId = document.getElementById('feishu-import-space-id').value.trim();
  const parentToken = document.getElementById('feishu-import-parent-token').value.trim();
  const maxImport = document.getElementById('feishu-import-max').value;

  if (configPath) args.push('--config-file', configPath);
  if (wikiUrl) args.push('--wiki-url', wikiUrl);
  if (sourceDir) args.push('--source-dir', sourceDir);
  if (sourceFile) args.push('--source-file', sourceFile);
  if (appId) args.push('--app-id', appId);
  if (appSecret) args.push('--app-secret', appSecret);
  if (spaceId) args.push('--space-id', spaceId);
  if (parentToken) args.push('--parent-wiki-token', parentToken);
  if (maxImport && parseInt(maxImport) > 0) args.push('--max-import', maxImport);
  args.push('--no-auto-open-permission');
  args.push('--request-delay', document.getElementById('feishu-import-delay').value || '0.8');
  args.push('--request-jitter', document.getElementById('feishu-import-jitter').value || '0.4');

  if (document.getElementById('feishu-import-move-to-wiki').checked) args.push('--move-to-wiki');
  if (document.getElementById('feishu-import-skip-rename').checked) args.push('--skip-rename');
  if (!document.getElementById('feishu-import-repair-images').checked) args.push('--skip-image-repair');
  if (document.getElementById('feishu-import-require-image-repair').checked) args.push('--require-image-repair');
  return args;
}

function setFeishuImportRunning(running) {
  ['login', 'probe', 'plan', 'one', 'all', 'open-permission', 'open-version', 'open-target-wiki', 'open-dir', 'setup-permissions', 'setup-target-app', 'check-app', 'save-config'].forEach((key) => {
    const button = document.getElementById(`feishu-import-${key}`);
    if (button) button.disabled = running;
  });
  const stop = document.getElementById('feishu-import-stop');
  if (stop) stop.disabled = !running;
}

async function runFeishuImportCommand(args, taskName) {
  setFeishuImportRunning(true);
  startProgress(taskName, '任务启动中，正在等待进度信息...');
  log(`开始：${taskName}`, 'info');
  try {
    const result = await window.electronAPI.runPythonCommand('import_feishu.py', args);
    if (result.success) {
      log(`完成：${taskName}`, 'success');
      log(JSON.stringify(result.data || {}, null, 2), 'success');
      finishProgress(true, `${taskName}完成`);
      return result.data || {};
    }
    log(`失败：${result.error}`, 'error');
    finishProgress(false, `${taskName}失败，请查看运行日志`);
    await maybeOpenFeishuPermissionPage(result.error);
  } catch (error) {
    const message = formatError(error);
    log(`错误：${message}`, 'error');
    finishProgress(false, `${taskName}出错，请查看运行日志`);
    await maybeOpenFeishuPermissionPage(message);
  } finally {
    setFeishuImportRunning(false);
  }
  return null;
}

function requireFeishuWikiUrl() {
  const wikiUrl = document.getElementById('feishu-import-url')?.value.trim();
  if (!wikiUrl) {
    alert('请先填写目标飞书 Wiki URL');
    return false;
  }
  return true;
}

function setFeishuImportLoginDoneButton(visible) {
  const button = document.getElementById('feishu-import-login-done');
  if (!button) return;
  button.hidden = !visible;
  button.disabled = !visible;
}

function initializeFeishuImportHandlers() {
  loadFeishuImportConfigIntoForm().catch((error) => {
    log(`读取飞书导入 API 配置失败：${error.message || error}`, 'error');
  });
  document.getElementById('feishu-import-open-console').addEventListener('click', async () => {
    await window.electronAPI.openExternal(FEISHU_DEVELOPER_CONSOLE_URL);
    alert('已打开飞书开放平台。请创建企业自建应用，并在“凭证与基础信息”复制 App ID 和 App Secret。');
  });
  document.getElementById('feishu-import-open-permission').addEventListener('click', async () => {
    await openFeishuPermissionPage();
  });
  document.getElementById('feishu-import-open-version').addEventListener('click', async () => {
    await openFeishuVersionPage();
  });
  document.getElementById('feishu-import-open-target-wiki').addEventListener('click', async () => {
    await openFeishuTargetWikiPage();
  });
  document.getElementById('feishu-import-setup-permissions').addEventListener('click', async () => {
    await setupFeishuOpenapiPermissions();
  });
  document.getElementById('feishu-import-setup-target-app').addEventListener('click', async () => {
    await setupFeishuTargetWikiDocApp();
  });
  document.getElementById('feishu-import-check-app').addEventListener('click', async () => {
    await runFeishuImportCommand([...buildFeishuImportArgs(), '--check-app-setup'], '检查飞书应用身份');
  });
  document.getElementById('feishu-import-save-config').addEventListener('click', saveFeishuImportConfigFromForm);
  document.getElementById('feishu-import-login-done').addEventListener('click', async () => {
    const result = await window.electronAPI.sendPythonInput('\n');
    setFeishuImportLoginDoneButton(false);
    if (result.success) {
      startProgress('保存飞书导入登录凭证', '正在从浏览器读取登录 Cookie...');
    } else {
      finishProgress(false, '没有正在等待确认的登录任务');
    }
    log(result.success ? '已确认登录完成，正在保存凭证...' : result.error, result.success ? 'info' : 'error');
  });
  document.getElementById('feishu-import-browse-source').addEventListener('click', async () => {
    const dir = await window.electronAPI.selectDirectory({ title: '选择 Markdown 目录' });
    if (dir) document.getElementById('feishu-import-source').value = dir;
  });
  document.getElementById('feishu-import-browse-file').addEventListener('click', async () => {
    const file = await window.electronAPI.selectFile({
      title: '选择 Markdown 文件',
      filters: [{ name: 'Markdown 文件', extensions: ['md'] }, { name: '所有文件', extensions: ['*'] }]
    });
    if (file) document.getElementById('feishu-import-source-file').value = file;
  });
  document.getElementById('feishu-import-login').addEventListener('click', async () => {
    if (!requireFeishuWikiUrl()) return;
    setFeishuImportLoginDoneButton(true);
    await runFeishuImportCommand([...buildFeishuImportArgs(), '--login'], '飞书导入登录');
    setFeishuImportLoginDoneButton(false);
  });
  document.getElementById('feishu-import-probe').addEventListener('click', async () => {
    if (!requireFeishuWikiUrl()) return;
    const data = await runFeishuImportCommand([...buildFeishuImportArgs(), '--probe'], '探测目标 Wiki');
    if (data) {
      if (data.spaceId && !document.getElementById('feishu-import-space-id').value.trim()) {
        document.getElementById('feishu-import-space-id').value = data.spaceId;
      }
      if (data.targetWikiToken && !document.getElementById('feishu-import-parent-token').value.trim()) {
        document.getElementById('feishu-import-parent-token').value = data.targetWikiToken;
      }
    }
  });
  document.getElementById('feishu-import-plan').addEventListener('click', async () => {
    if (!requireFeishuWikiUrl()) return;
    await runFeishuImportCommand([...buildFeishuImportArgs(), '--plan'], '生成导入计划');
  });
  document.getElementById('feishu-import-one').addEventListener('click', async () => {
    if (!requireFeishuWikiUrl()) return;
    if (confirm('这会在目标 Wiki 创建一篇测试文档。确认继续吗？')) {
      await runFeishuImportCommand([...buildFeishuImportArgs(), '--api-import-one', '--yes'], '单篇导入测试');
    }
  });
  document.getElementById('feishu-import-all').addEventListener('click', async () => {
    if (!requireFeishuWikiUrl()) return;
    if (confirm('这会向目标 Wiki 批量创建文档。确认继续吗？')) {
      await runFeishuImportCommand([...buildFeishuImportArgs(), '--api-import-all', '--yes'], '批量导入');
    }
  });
  document.getElementById('feishu-import-stop').addEventListener('click', handleStop);
  document.getElementById('feishu-import-open-dir').addEventListener('click', async () => {
    const dir = document.getElementById('feishu-import-source').value.trim();
    if (dir) await window.electronAPI.openPath(dir);
  });
}

// Initialize app
document.addEventListener('DOMContentLoaded', () => {
  applyTheme(loadTheme());

  // Setup navigation
  document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', () => {
      if (!isRunning) {
        switchTool(item.dataset.tool);
      }
    });
  });

  // Setup footer buttons
  document.getElementById('btn-clear-log').addEventListener('click', clearLog);
  document.getElementById('btn-theme-toggle')?.addEventListener('click', toggleTheme);
  document.getElementById('btn-check-update')?.addEventListener('click', () => checkForUpdates(false));
  document.getElementById('btn-open-release')?.addEventListener('click', () => {
    window.electronAPI.openExternal(latestReleaseUrl);
  });
  document.getElementById('btn-dismiss-update')?.addEventListener('click', hideUpdateBanner);

  document.getElementById('btn-about').addEventListener('click', () => {
    window.electronAPI.showAbout();
  });

  document.getElementById('btn-settings').addEventListener('click', () => {
    alert('设置功能开发中...');
  });

  window.electronAPI.getAppPath().then((paths) => {
    appPaths = paths;
    switchTool('zsxq');
    log('万能导已启动', 'success');
    window.setTimeout(() => checkForUpdates(true), 1000);
  }).catch(() => {
    switchTool('zsxq');
    log('万能导已启动', 'success');
    window.setTimeout(() => checkForUpdates(true), 1000);
  });

  if (window.electronAPI.onAppInfo) {
    window.electronAPI.onAppInfo((message) => {
      log(message, 'success');
    });
  }
});
