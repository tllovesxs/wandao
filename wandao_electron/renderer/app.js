const PROVIDER_REGISTRY = window.WandaoProviders;
let TOOLS = PROVIDER_REGISTRY?.tools?.() || {};
const DEFAULT_VIEW_ID = 'home';
const PRIMARY_NAV_ITEMS = [
  { id: 'home', label: '首页', description: '快速开始', icon: 'home' },
  { id: 'platform-center', label: '平台中心', description: '选择平台和操作', icon: 'platforms' },
  { id: 'task-center', label: '任务中心', description: '查看最近任务', icon: 'tasks' },
  { id: 'notice-center', label: '教程公告', description: '公告与教程', icon: 'notice' },
  { id: 'plugin-center', label: '插件中心', description: '安装与更新平台', icon: 'plugins' },
  { id: 'settings', label: '设置', description: '偏好与帮助', icon: 'settings' }
];
const GITHUB_REPO_URL = 'https://github.com/tllovesxs/wandao';
const GITHUB_RAW_BASE = 'https://raw.githubusercontent.com/tllovesxs/wandao/main/';
const GITHUB_BLOB_BASE = 'https://github.com/tllovesxs/wandao/blob/main/';
const NOTICE_CENTER_MANIFEST_URL = `${GITHUB_RAW_BASE}docs/tutorial-announcements.json`;
const DEFAULT_BROWSER_DOWNLOAD_URL = 'https://www.google.com/chrome/';
let pluginCatalogState = { status: 'idle', plugins: [], query: '', error: '', offline: false, experimentalError: '', updatedAt: '' };
let pluginCatalogRequestId = 0;
let customPluginMessageCleanup = null;
const FALLBACK_NOTICE_CENTER = {
  version: 1,
  updatedAt: '2026-07-08',
  repository: GITHUB_REPO_URL,
  items: [
    {
      id: 'provider-co-creation-invite',
      type: 'announcement',
      pinned: true,
      title: '万能导共创邀请：一起接入更多平台',
      summary: '万能导正在开放 Provider v1 共创机制，欢迎从教程、脚本、失败排查或新平台接入开始参与。',
      date: '2026-07-08',
      badge: '置顶',
      tags: ['公告', '共创', 'Provider v1'],
      path: 'docs/announcements/provider-co-creation-invite.md',
      body: '# 万能导共创邀请：一起接入更多平台\n\n万能导正在开放 Provider v1 共创机制。你可以从教程、脚本、失败排查或新平台接入开始参与。\n\n## 推荐参与方式\n\n- 给你常用的平台补教程。\n- 基于标准模板新增导入或导出 Provider。\n- 帮忙复现用户反馈并补充脱敏日志。\n- 优化现有平台的目录结构、图片和附件处理。\n\nProvider v1 会保持向后兼容，按当前规范开发的插件不会在小版本里被随意破坏。'
    },
    {
      id: 'project-learning-ai-prompt',
      type: 'tutorial',
      pinned: false,
      title: 'AI 辅助学习：项目学习导师提示词',
      summary: '把导出的教学文档和源码放在一起，让 AI 像项目学习导师一样带你理解业务流程、核心代码和技术取舍。',
      date: '2026-07-08',
      tags: ['教程', 'AI', '项目学习', '提示词'],
      path: 'prompts/项目学习导师提示词.md',
      body: '# AI 辅助学习：项目学习导师提示词\n\n把万能导导出的教学文档和源码项目放在一起，再把项目学习导师提示词发给 AI，可以让 AI 结合真实代码和课程资料讲解项目。\n\n## 使用方式\n\n1. 用万能导导出你有权限访问的教学文档。\n2. 把 Markdown 文档放到源码项目旁边。\n3. 用 AI 编程工具打开整个项目目录。\n4. 复制 `prompts/项目学习导师提示词.md` 的内容给 AI。\n5. 按章节、功能或技术点继续提问。'
    }
  ]
};
const PLATFORM_ORDER = [
  'feishu',
  'yuque',
  'youdao',
  'aliyun-thoughts',
  'onenote',
  'wiz',
  'zsxq',
  'yinxiang',
  'ima',
  'notion'
];
const PLATFORM_META = {
  feishu: {
    name: '飞书',
    description: '支持 Wiki 知识库导出、Markdown 导入、图片补全和权限检测。',
    tags: ['导出', '导入']
  },
  yuque: {
    name: '语雀',
    description: '支持知识库导出和 Markdown 批量导入，适合本地备份和平台迁移。',
    tags: ['导出', '导入']
  },
  youdao: {
    name: '有道云笔记',
    description: '支持有道云笔记目录读取、批量导出和图片保存。',
    tags: ['导出']
  },
  'aliyun-thoughts': {
    name: '阿里云 Thoughts',
    description: '优先走接口导出正文，失败时回退浏览器渲染，保留目录和图片。',
    tags: ['导出']
  },
  onenote: {
    name: 'OneNote',
    description: '读取 Windows 本地 OneNote，导出为 Markdown 并保留笔记本、分区和页面层级。',
    tags: ['导出']
  },
  wiz: {
    name: '为知笔记',
    description: '支持网页版为知笔记导出，保留目录结构和图片资源。',
    tags: ['导出']
  },
  zsxq: {
    name: '知识星球',
    description: '支持 Group 帖子按数量导出，也支持专栏目录按章节导出。',
    tags: ['导出']
  },
  yinxiang: {
    name: '印象笔记',
    description: '支持印象笔记导出和 Markdown 导入。',
    tags: ['导出', '导入']
  },
  ima: {
    name: 'ima 知识库',
    description: '支持 ima 知识库导出和本地文件导入。',
    tags: ['导出', '导入']
  },
  notion: {
    name: 'Notion',
    description: 'Notion 官方已支持 Markdown 导出，万能导提供迁移教程和注意事项。',
    tags: ['教程']
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

let currentTool = DEFAULT_VIEW_ID;
let isRunning = false;
let appPaths = null;
let feishuImportConfig = {};
let tocStates = {};
let pythonProgressBuffer = '';
let pythonLogSummaryBuffer = '';
let pythonLogProcessor = null;
let progressVisible = false;
let latestReleaseUrl = 'https://github.com/tllovesxs/wandao/releases/latest';
let latestYuqueImportReportFile = '';
let noticeCenterState = {
  status: 'idle',
  manifest: null,
  selectedId: '',
  selectedBodyId: '',
  selectedBody: '',
  selectedBodyStatus: 'idle',
  selectedBodyError: '',
  bodyCache: {},
  bodyRequestSeq: 0,
  error: ''
};
let appSettingsState = {
  settings: {},
  browsers: [],
  browserDetectStatus: 'idle',
  browserDetectError: '',
  browserDownloadUrl: DEFAULT_BROWSER_DOWNLOAD_URL
};
const MAX_LOG_ENTRIES = 2000;
const LOG_PANEL_RENDER_LIMIT = 400;
const MAX_TASK_LOG_ENTRIES = 2000;
const userLogEntries = [];
const detailLogEntries = [];
let activeTaskLogEntries = [];
let logViewMode = localStorage.getItem('wandao-log-view') === 'detail' ? 'detail' : 'user';
const MAX_TASK_HISTORY = 80;
let taskHistory = [];
let activeHistoryTask = null;

function refreshProviderTools() {
  TOOLS = PROVIDER_REGISTRY?.tools?.() || TOOLS || {};
  return TOOLS;
}

const ERROR_RULES = [
  {
    category: '本地文件路径问题',
    pattern: /(ENOENT|no such file|can't open file|系统找不到|路径不存在|目录不存在|文件不存在|无法找到|not found|EACCES|EPERM)/i,
    title: '本地文件或目录有问题',
    suggestion: '请检查输入目录、输出目录或脚本文件是否存在，路径里不要包含已经被移动或删除的文件。'
  },
  {
    category: '任务参数过长',
    pattern: /(ENAMETOOLONG|argument list too long|command line.*too long|spawn.*too long)/i,
    title: '本次选择内容太多，启动参数超过系统限制',
    suggestion: '请更新到新版后重试；新版会把大量文档 ID 写入临时文件，避免 Windows 命令行长度限制。'
  },
  {
    category: '图片或附件下载失败',
    pattern: /(图片下载失败|附件下载失败|download.*image|image.*download|tcs-devops\.aliyuncs\.com|cdn\.nlark\.com|图片.*HTTP 40[134]|HTTP 40[134].*图片|imageFailure|imageFailures)/i,
    title: '图片或附件处理失败',
    suggestion: '正文可能已导出，但这些图片没有成功本地化。请检查网络、重新登录后重试，或确认原文图片在浏览器中可以打开。'
  },
  {
    category: '未登录或登录失效',
    pattern: /(未登录|登录失效|登录已失效|重新登录|登录凭证|没有可用.*凭证|没有可用.*cookie|cookie 中缺少|login required|please login|auth file|cookie|cookies|401|unauthorized|会话|凭证.*失效)/i,
    title: '登录状态可能已失效',
    suggestion: '请重新点击“登录并保存凭证”，确认浏览器中能正常打开目标页面后再继续。'
  },
  {
    category: '浏览器自动化启动失败',
    pattern: /(Chrome remote debugging port|remote debugging port|DevTools|debug port|9222|Chrome\/Edge executable was not found|browser executable|WANDAO_BROWSER|找不到.*Chrome|没有找到.*浏览器|浏览器.*调试)/i,
    title: '没有成功连接到可控制的浏览器',
    suggestion: '请到“设置 > 自动化浏览器”检测并选择 Chrome、Edge 或 Chromium；如果浏览器已打开但仍失败，请关闭后重试。'
  },
  {
    category: '目标平台 API 权限不足',
    pattern: /(scope|required scope|scopes required|OpenAPI|API 权限|应用身份权限|drive:|docx:|docs:|wiki:|tenant_access_token|app ticket|99991672|权限申请)/i,
    title: '目标平台 API 权限不足',
    suggestion: '请按页面提示开通所需 API 权限，并在平台开放后台发布应用新版本后重试。'
  },
  {
    category: '没有访问权限',
    pattern: /(Access denied|permission denied|Forbidden|HTTP 403|无权限|没有权限|权限不足|拒绝访问|not authorized|父节点没有.*权限|131006)/i,
    title: '当前账号或应用没有访问权限',
    suggestion: '请确认当前登录账号能访问该内容；如果是导入任务，还要确认目标知识库给应用或账号写入权限。'
  },
  {
    category: '平台额度或数量限制',
    pattern: /(max_doc_note_number|DOC_NOTE_LIMIT|文档数超过限制|数量.*限制|超过.*数量限制|额度.*不足|quota exceeded|limit exceeded)/i,
    title: '目标平台额度或数量已达上限',
    suggestion: '请清理目标知识库、升级空间、换一个可写知识库，或减少本次导入数量后重试。'
  },
  {
    category: '请求过快或平台限流',
    pattern: /(rate limit|Too Many Requests|HTTP 429|请求过快|请求频率|频率过高|限流|rateLimited|too frequent)/i,
    title: '请求过快或平台限流',
    suggestion: '请调大请求延迟和随机浮动，等待一段时间后再继续，必要时使用增量模式补齐缺失内容。'
  },
  {
    category: '任务参数不合适',
    pattern: /(无效的count|invalid count|code=14001|14001)/i,
    title: '单批读取数量超过平台允许范围',
    suggestion: '请更新到新版后重试；新版会把知识星球 Group 单批读取控制在安全范围内。'
  },
  {
    category: '页面结构变化',
    pattern: /(selector|querySelector|Cannot read properties|页面结构|目录条目|找不到元素|未找到按钮|无法定位|DOM|XPath|element not found)/i,
    title: '页面结构可能变化',
    suggestion: '平台页面可能改版，自动化没有找到对应按钮或正文区域。请复制错误报告给开发者适配。'
  },
  {
    category: '图片或附件下载失败',
    pattern: /(图片|附件|image|attachment|resource|download.*fail|下载失败|上传附件失败|imageFailure|imageFailures)/i,
    title: '图片或附件处理失败',
    suggestion: '正文可能已导出，但图片或附件失败。请检查网络和本地目录权限，必要时重新导出该文档。'
  }
];

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
function normalizeLogMessage(message) {
  if (message === null || message === undefined) return '';
  if (typeof message === 'string') return message;
  try {
    return JSON.stringify(message, null, 2);
  } catch {
    return String(message);
  }
}

function trimLogStore(entries) {
  if (entries.length > MAX_LOG_ENTRIES) {
    entries.splice(0, entries.length - MAX_LOG_ENTRIES);
  }
}

function visibleLogEntries(entries) {
  if (entries.length <= LOG_PANEL_RENDER_LIMIT) {
    return { entries, omitted: 0 };
  }
  return {
    entries: entries.slice(entries.length - LOG_PANEL_RENDER_LIMIT),
    omitted: entries.length - LOG_PANEL_RENDER_LIMIT
  };
}

function appendDetailedLog(source, type, message, meta = {}) {
  const entry = {
    time: new Date().toISOString(),
    source,
    type,
    message: normalizeLogMessage(message),
    event: meta.event || '',
    provider: meta.provider || '',
    data: meta.data || null
  };
  detailLogEntries.push(entry);
  trimLogStore(detailLogEntries);
  if (activeHistoryTask) {
    activeTaskLogEntries.push(entry);
    if (activeTaskLogEntries.length > MAX_TASK_LOG_ENTRIES) {
      activeTaskLogEntries.splice(0, activeTaskLogEntries.length - MAX_TASK_LOG_ENTRIES);
    }
  }
  if (logViewMode === 'detail') renderDetailedLogEntry(entry);
}


function formatUserDateTime(value) {
  const formatter = window.WandaoTime?.formatLocalDateTime;
  if (typeof formatter === 'function') return formatter(value);
  const date = value ? new Date(value) : new Date();
  return Number.isNaN(date.getTime()) ? '无效时间' : date.toLocaleString();
}

function formatUserTimestamp(value) {
  if (!value) return '-';
  const isTimestamp = window.WandaoTime?.isTimestamp;
  if (typeof isTimestamp === 'function' ? isTimestamp(value) : value instanceof Date || /^\d{4}-\d{2}-\d{2}T/.test(String(value))) return formatUserDateTime(value);
  return String(value);
}

function formatLogTime(value) {
  return formatUserDateTime(value);
}

function createLogEntryElement(message, type = 'info', time = new Date().toISOString()) {
  const entry = document.createElement('div');
  entry.className = `log-entry ${type}`;
  const timestamp = formatLogTime(time);
  entry.textContent = `[${timestamp}] ${message}`;
  return entry;
}

function createLogNoticeElement(message) {
  const entry = document.createElement('div');
  entry.className = 'log-entry muted';
  entry.textContent = message;
  return entry;
}

function trimRenderedLogEntries(logContent) {
  while (logContent.children.length > LOG_PANEL_RENDER_LIMIT) {
    logContent.removeChild(logContent.firstElementChild);
  }
}

function renderLogEntry(message, type = 'info', time = new Date().toISOString()) {
  const logContent = document.getElementById('log-content');
  if (!logContent) return;
  logContent.appendChild(createLogEntryElement(message, type, time));
  trimRenderedLogEntries(logContent);
  logContent.scrollTop = logContent.scrollHeight;
}

function renderUserLogEntry(entry) {
  renderLogEntry(entry.message, entry.type, entry.time);
}

function renderDetailedLogEntry(entry) {
  const source = entry.source ? `[${entry.source}] ` : '';
  const event = entry.event ? `[${entry.event}] ` : '';
  renderLogEntry(`${source}${event}${entry.message}`, entry.type, entry.time);
}

function updateLogViewHeader() {
  const title = document.getElementById('log-title');
  const button = document.getElementById('btn-settings');
  if (title) title.textContent = logViewMode === 'detail' ? '详细日志' : '用户日志';
  if (button) button.textContent = logViewMode === 'detail' ? '用户日志' : '详细日志';
}

function renderLogPanel() {
  updateLogViewHeader();
  const logContent = document.getElementById('log-content');
  if (!logContent) return;
  logContent.replaceChildren();
  const allEntries = logViewMode === 'detail' ? detailLogEntries : userLogEntries;
  const { entries, omitted } = visibleLogEntries(allEntries);
  const fragment = document.createDocumentFragment();
  if (omitted > 0) {
    fragment.appendChild(createLogNoticeElement(`为保持界面流畅，仅显示最近 ${LOG_PANEL_RENDER_LIMIT} 条日志；完整日志仍会进入错误报告。`));
  }
  entries.forEach((entry) => {
    if (logViewMode === 'detail') {
      const source = entry.source ? `[${entry.source}] ` : '';
      const event = entry.event ? `[${entry.event}] ` : '';
      fragment.appendChild(createLogEntryElement(`${source}${event}${entry.message}`, entry.type, entry.time));
    } else {
      fragment.appendChild(createLogEntryElement(entry.message, entry.type, entry.time));
    }
  });
  logContent.appendChild(fragment);
  logContent.scrollTop = logContent.scrollHeight;
}

function toggleLogViewMode() {
  logViewMode = logViewMode === 'detail' ? 'user' : 'detail';
  localStorage.setItem('wandao-log-view', logViewMode);
  renderLogPanel();
}

function appendUserLog(message, type = 'info') {
  const text = normalizeLogMessage(message);
  const entry = {
    time: new Date().toISOString(),
    type,
    message: text
  };
  userLogEntries.push(entry);
  trimLogStore(userLogEntries);
  if (logViewMode === 'user') renderUserLogEntry(entry);
}

function compactLogSummary(message, maxLength = 220) {
  const text = normalizeLogMessage(message)
    .replace(/\s+/g, ' ')
    .trim();
  if (!text) return '';
  return text.length > maxLength ? `${text.slice(0, maxLength)}...` : text;
}

function looksLikeStructuredDump(message) {
  const text = normalizeLogMessage(message).trim();
  if (!text) return false;
  if ((text.startsWith('{') && text.endsWith('}')) || (text.startsWith('[') && text.endsWith(']'))) {
    return true;
  }
  const lines = text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  if (lines.length < 3) return false;
  const structuredLines = lines.filter((line) => /^["{}\[\],]/.test(line) || /^[A-Za-z0-9_]+\s*[:=]/.test(line));
  return structuredLines.length / lines.length > 0.6;
}

function classifyError(message) {
  const text = normalizeLogMessage(message);
  for (const rule of ERROR_RULES) {
    if (rule.pattern.test(text)) return rule;
  }
  return {
    category: '未知错误',
    title: '任务执行失败',
    suggestion: '请点击“提交错误报告给开发者”复制详细日志，并说明你刚才点击了哪个功能。'
  };
}

function formatUserError(message) {
  const raw = normalizeLogMessage(message);
  const rule = classifyError(raw);
  const summary = compactLogSummary(raw);
  const suffix = summary ? `\n原始摘要：${summary}` : '';
  return `${rule.category}：${rule.title}。${rule.suggestion}${suffix}`;
}

function log(message, type = 'info', options = {}) {
  const raw = normalizeLogMessage(message);
  appendDetailedLog(options.source || 'ui', type, raw);

  if (looksLikeStructuredDump(raw) && !options.forceDisplay) {
    appendUserLog('任务明细已记录到详细日志；需要反馈问题时请点击“提交错误报告给开发者”。', type === 'error' ? 'error' : 'info');
    return;
  }

  const display = type === 'error' && options.classify !== false ? formatUserError(raw) : raw;
  appendUserLog(display, type);
}

function clearLog() {
  userLogEntries.length = 0;
  detailLogEntries.length = 0;
  pythonLogSummaryBuffer = '';
  pythonLogProcessor?.reset?.();
  renderLogPanel();
}

function maskSensitiveText(value) {
  let text = normalizeLogMessage(value);
  const secretPatterns = [
    /(app[_-]?secret|api[_-]?key|password|passwd|token|cookie|authorization|secret|access[_-]?key)(["'\s:=]+)([^"'\s,}]+)/gi,
    /(飞书 App Secret|印象笔记密码|ima API Key|API Key|密码)(\s*[:：]\s*)([^\s]+)/gi
  ];
  secretPatterns.forEach((pattern) => {
    text = text.replace(pattern, (_match, key, separator) => `${key}${separator}***`);
  });
  text = text.replace(/(Bearer\s+)[A-Za-z0-9._\-+/=]+/gi, '$1***');
  return text;
}

function maskSensitiveValue(value) {
  if (Array.isArray(value)) return value.map(maskSensitiveValue);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [
      key,
      /(cookie|token|secret|password|authorization|signature|access[_-]?key|api[_-]?key)/i.test(key)
        ? '***'
        : maskSensitiveValue(item)
    ]));
  }
  return typeof value === 'string' ? maskSensitiveText(value) : value;
}

function activeToolLabel() {
  const active = document.querySelector('.nav-item.active');
  return active?.textContent?.trim() || TOOLS[currentTool]?.title || currentTool || '未知功能';
}

async function copyDeveloperReport() {
  let paths = appPaths || {};
  if (!paths.userData && window.electronAPI.getAppPath) {
    try {
      paths = await window.electronAPI.getAppPath();
    } catch {
      paths = appPaths || {};
    }
  }

  const userLines = userLogEntries.map((entry) => `[${formatUserDateTime(entry.time)}] [${entry.type}] ${entry.message}`);
  const detailLines = detailLogEntries.map((entry) => `[${formatUserDateTime(entry.time)}] [${entry.source}] [${entry.type}] ${entry.message}`);
  const report = [
    '# 万能导错误报告',
    '',
    `生成时间：${formatUserDateTime(new Date())}`,
    `当前功能：${activeToolLabel()}`,
    `当前工具 ID：${currentTool || '-'}`,
    `系统平台：${navigator.platform || '-'}`,
    `浏览器内核：${navigator.userAgent || '-'}`,
    paths.userData ? `应用数据目录：${paths.userData}` : '',
    paths.projectRoot ? `项目目录：${paths.projectRoot}` : '',
    '',
    '## 用户日志',
    userLines.length ? userLines.join('\n') : '暂无用户日志',
    '',
    '## 详细日志',
    detailLines.length ? detailLines.join('\n') : '暂无详细日志',
    '',
    '## 说明',
    '请把这份内容发给开发者，并补充你正在导入/导出的目标平台、入口链接类型以及点击了哪个按钮。'
  ].filter((line) => line !== '').join('\n');

  await window.electronAPI.copyText(maskSensitiveText(report));
  log('已复制错误报告。你可以直接粘贴给开发者，敏感字段已自动脱敏。', 'success');
}

function taskHistoryPath() {
  const root = appPaths?.userData || appPaths?.dataRoot;
  return root ? `${root}/task_history.json` : '';
}

function makeTaskId() {
  const random = Math.random().toString(36).slice(2, 8);
  return `${Date.now()}-${random}`;
}

function statusText(status) {
  return window.WandaoTaskReport?.statusText(status) || status || '\u672a\u77e5';
}

function taskHistoryStatusText(task) {
  return window.WandaoTaskReport?.taskStatusText(task) || statusText(task?.status);
}

function formatDuration(ms) {
  return window.WandaoTaskReport?.formatDuration(ms) || '';
}

function extractTaskStats(data, errorText = '') {
  const report = window.WandaoTaskReport?.normalizeTaskReport(data, { errorText }) || {};
  return {
    ...(report.stats || {}),
    failureItems: report.failures || []
  };
}

function taskSummary(task) {
  return window.WandaoTaskReport?.summarizeStats(task.report?.stats || task.stats || {}, task.error) || '暂无统计信息';
}

function taskArtifactPaths(task) {
  return window.WandaoTaskReport?.taskArtifactPaths(task) || { output: '', reportFile: '' };
}

function taskFailurePreview(task) {
  return window.WandaoTaskReport?.taskFailurePreview(task, 3) || [];
}

function taskFailureDiagnostics(task, limit = 80) {
  const source = task?.report?.raw || task?.resultData || task?.report || {};
  const lines = window.WandaoTaskReport?.collectFailureDiagnostics(source, limit) || [];
  if (lines.length) return lines;
  if (task?.error) return [compactDiagnostic(task.error, 700)];
  return [];
}

function taskFailureCount(task) {
  return window.WandaoTaskReport?.taskFailureCount(task) || 0;
}

function setLogCollapsed(collapsed) {
  const section = document.getElementById('log-section');
  const button = document.getElementById('btn-toggle-log');
  if (!section || !button) return;
  section.classList.toggle('is-collapsed', collapsed);
  button.textContent = collapsed ? '展开日志' : '收起日志';
  button.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
}

function providerRetryFailureArg(provider) {
  if (!provider?.capabilities?.retryFailures) return '';
  if (typeof provider.retryFailures === 'string') return provider.retryFailures;
  return provider.retryFailures?.arg || '--retry-failures';
}

function canResumeTask(task) {
  if (!task) return false;
  if (task.argsUnavailable) return false;
  if (task.status === 'running') return false;
  if (task.status !== 'completed') return true;
  const provider = TOOLS[task.providerId] || {};
  return Boolean(providerRetryFailureArg(provider) && taskFailureCount(task) > 0);
}

function resumeTaskDisabledReason(task) {
  if (!task) return '没有可继续的任务。';
  if (task.argsUnavailable) return '任务参数无法解密，请回到平台页面重新填写后执行。';
  if (task.status === 'running') return '任务正在运行中，不能重复启动。';
  if (task.status !== 'completed') return '';
  if (taskFailureCount(task) <= 0) return '任务已完成且没有失败项。';
  const provider = TOOLS[task.providerId] || {};
  if (!providerRetryFailureArg(provider)) return '该平台暂未声明失败项重试能力，请复制报告后重新执行或反馈给开发者。';
  return '';
}

function resumeTaskArgs(task) {
  const provider = TOOLS[task?.providerId] || {};
  const retryArg = providerRetryFailureArg(provider);
  const helper = window.WandaoTaskResume?.buildResumeArgs;
  if (typeof helper === 'function') {
    return helper(task, retryArg, taskFailureCount(task));
  }
  const args = Array.isArray(task?.args) ? [...task.args] : [];
  const interrupted = ['stopped', 'interrupted'].includes(String(task?.status || '').toLowerCase());
  if (interrupted && retryArg) return args.filter((arg) => arg !== retryArg);
  if (retryArg && taskFailureCount(task) > 0 && !args.includes(retryArg)) {
    args.push(retryArg);
  }
  return args;
}

async function loadTaskHistory() {
  const filePath = taskHistoryPath();
  if (!filePath) return;
  const data = await readJsonFileIfExists(filePath);
  const storedTasks = Array.isArray(data?.tasks) ? data.tasks : [];
  let needsMigration = false;
  taskHistory = await Promise.all(storedTasks.map(async (storedTask) => {
    const task = { ...storedTask };
    if (task.status === 'running' || task.status === 'stopping') {
      task.status = 'interrupted';
      task.finishedAt = task.finishedAt || new Date().toISOString();
      task.error = task.error || '上次运行未正常结束，可以继续执行。';
      needsMigration = true;
    }
    if (task.protectedArgs && window.electronAPI.restoreTaskArgs) {
      const restored = await window.electronAPI.restoreTaskArgs(task.protectedArgs);
      task.args = restored?.success && Array.isArray(restored.args) ? restored.args : [];
      task.argsUnavailable = !restored?.success;
    } else if (Array.isArray(task.args) && task.args.length) {
      // Legacy history stored raw args. Keep them only in memory and encrypt on the next save.
      needsMigration = true;
    } else {
      task.args = [];
    }
    return task;
  }));
  if (needsMigration) await saveTaskHistory();
  renderTaskHistory();
}

async function saveTaskHistory() {
  const filePath = taskHistoryPath();
  if (!filePath) return;
  const tasks = await Promise.all(taskHistory.slice(0, MAX_TASK_HISTORY).map(async (task) => {
    const { pendingSave, detailStartIndex, ...persistable } = task;
    const rawArgs = Array.isArray(task.args) ? task.args : [];
    if (rawArgs.length && window.electronAPI.protectTaskArgs) {
      const protectedResult = await window.electronAPI.protectTaskArgs(rawArgs);
      if (protectedResult?.success) {
        persistable.protectedArgs = protectedResult.payload;
        persistable.args = [];
        persistable.argsUnavailable = false;
      } else {
        persistable.args = window.WandaoTaskReport?.maskArgs(rawArgs) || [];
        persistable.argsUnavailable = true;
        delete persistable.protectedArgs;
      }
    } else {
      persistable.args = [];
    }
    persistable.resultData = maskSensitiveValue(persistable.resultData);
    persistable.report = maskSensitiveValue(persistable.report);
    persistable.error = maskSensitiveText(persistable.error || '');
    persistable.logs = maskSensitiveValue(persistable.logs || []);
    return persistable;
  }));
  const content = JSON.stringify({
    version: 1,
    updatedAt: new Date().toISOString(),
    tasks
  }, null, 2);
  const result = await window.electronAPI.writeFile(filePath, content);
  if (!result.success) {
    appendDetailedLog('task-history', 'error', result.error || '保存任务历史失败');
  }
}

function renderTaskHistory() {
  const list = document.getElementById('task-history-list');
  if (!list) return;
  const tasks = taskHistory.slice(0, 8);
  if (!tasks.length) {
    list.innerHTML = '<div class="task-history-empty">暂无任务历史。</div>';
    return;
  }
  list.innerHTML = tasks.map((task) => {
    const startedAt = task.startedAt ? formatUserDateTime(task.startedAt) : '-';
    const elapsed = task.elapsedMs ? `，耗时 ${formatDuration(task.elapsedMs)}` : '';
    const canResume = canResumeTask(task);
    const paths = taskArtifactPaths(task);
    const failurePreview = taskFailurePreview(task);
    const failureCount = taskFailureCount(task);
    const resumeReason = resumeTaskDisabledReason(task);
    return `
      <div class="task-history-item" data-task-id="${escapeHtml(task.id)}">
        <div class="task-history-main">
          <div>
            <div class="task-history-title">${escapeHtml(task.title || task.providerTitle || '未命名任务')}</div>
            <div class="task-history-meta">
              <span class="task-status ${escapeHtml(task.status || '')}">${escapeHtml(taskHistoryStatusText(task))}</span>
              <span>${escapeHtml(startedAt)}${escapeHtml(elapsed)}</span>
            </div>
          </div>
          <div class="task-history-buttons">
            <button class="btn-text" type="button" data-history-action="copy">复制报告</button>
            ${failureCount ? '<button class="btn-text" type="button" data-history-action="copy-failures">复制失败项</button>' : ''}
            ${paths.reportFile ? '<button class="btn-text" type="button" data-history-action="open-report">打开报告</button>' : ''}
            ${paths.output ? '<button class="btn-text" type="button" data-history-action="open-output">打开输出</button>' : ''}
            <button class="btn-text" type="button" data-history-action="resume" ${canResume ? '' : 'disabled'} title="${escapeHtml(resumeReason)}">继续/重试</button>
          </div>
        </div>
        <div class="task-history-summary">${escapeHtml(taskSummary(task))}</div>
        ${failurePreview.length ? `
          <div class="task-history-failures">
            ${failurePreview.map((line) => `<div>${escapeHtml(line)}</div>`).join('')}
          </div>
        ` : ''}
      </div>
    `;
  }).join('');
}

function createTaskReport(task) {
  const provider = TOOLS[task.providerId] || {};
  return window.WandaoTaskReport?.createMarkdownTaskReport(task, {
    provider,
    maskSensitiveText
  }) || maskSensitiveText(JSON.stringify(task, null, 2));
}

async function copyTaskReport(taskId) {
  const task = taskHistory.find((item) => item.id === taskId);
  if (!task) return;
  await window.electronAPI.copyText(createTaskReport(task));
  log('已复制任务报告。', 'success');
}

async function copyTaskFailures(taskId) {
  const task = taskHistory.find((item) => item.id === taskId);
  if (!task) return;
  const lines = taskFailureDiagnostics(task);
  if (!lines.length) {
    log('这条任务没有可复制的失败项。', 'info');
    return;
  }
  await window.electronAPI.copyText(maskSensitiveText(lines.join('\n')));
  log('已复制任务失败项。', 'success');
}

async function openTaskArtifact(task, kind) {
  const paths = taskArtifactPaths(task);
  const targetPath = kind === 'report' ? paths.reportFile : paths.output;
  if (!targetPath) {
    log(kind === 'report' ? '这条任务没有报告文件路径。' : '这条任务没有输出目录路径。', 'warn');
    return;
  }
  const result = await window.electronAPI.openPath(targetPath);
  if (result?.success) {
    log(kind === 'report' ? '已打开任务报告文件。' : '已打开任务输出目录。', 'success');
  } else {
    log(`打开任务产物失败：${result?.error || targetPath}`, 'error');
  }
}

function startHistoryTask(script, args, context = {}) {
  if (context.track === false) return null;
  const provider = TOOLS[context.providerId] || {};
  const runId = makeTaskId();
  const task = {
    id: runId,
    runId,
    jobId: context.jobId || runId,
    parentRunId: context.parentRunId || '',
    providerId: context.providerId || currentTool,
    providerTitle: provider.title || context.providerId || currentTool,
    title: context.title || provider.title || script,
    action: context.action || (provider.isImport ? '导入' : '导出'),
    status: 'running',
    script,
    args: Array.isArray(args) ? [...args] : [],
    startedAt: new Date().toISOString(),
    finishedAt: '',
    elapsedMs: 0,
    resultData: null,
    error: '',
    stats: extractTaskStats(null),
    logs: []
  };
  taskHistory.unshift(task);
  taskHistory = taskHistory.slice(0, MAX_TASK_HISTORY);
  activeHistoryTask = task;
  activeTaskLogEntries = [];
  task.pendingSave = saveTaskHistory();
  renderTaskHistory();
  return task;
}

async function finishHistoryTask(task, result, thrownError = null) {
  if (!task) return;
  if (task.pendingSave) {
    await task.pendingSave.catch(() => {});
    delete task.pendingSave;
  }
  const finishedAt = new Date();
  const startedAt = task.startedAt ? new Date(task.startedAt) : finishedAt;
  const success = result?.success && !thrownError;
  const stopped = result?.code === 130 && !thrownError;
  task.status = success ? 'completed' : ((stopped || task.stopRequested) ? 'stopped' : 'failed');
  task.finishedAt = finishedAt.toISOString();
  task.elapsedMs = finishedAt.getTime() - startedAt.getTime();
  task.resultData = result?.data || null;
  task.error = thrownError ? formatError(thrownError) : (result?.error || '');
  task.report = window.WandaoTaskReport?.normalizeTaskReport(task.resultData, {
    errorText: task.error,
    provider: task.providerId,
    mode: task.action
  }) || null;
  task.stats = task.report?.stats ? { ...task.report.stats, failureItems: task.report.failures || [] } : extractTaskStats(task.resultData, task.error);
  task.logs = [...activeTaskLogEntries];
  if (activeHistoryTask?.id === task.id) {
    activeHistoryTask = null;
    activeTaskLogEntries = [];
  }
  await saveTaskHistory();
  renderTaskHistory();
}

async function runTrackedPythonCommand(script, args, context = {}, options = {}) {
  const jobId = context.jobId || makeTaskId();
  const commandArgs = Array.isArray(args) ? [...args] : [];
  if (commandArgs.includes('--checkpoint-file') && !commandArgs.includes('--checkpoint-task-id')) {
    commandArgs.push('--checkpoint-task-id', jobId);
  }
  const task = startHistoryTask(script, commandArgs, { ...context, jobId });
  try {
    const result = await window.electronAPI.runPythonCommand(script, commandArgs, {
      ...options,
      taskId: task?.id || '',
      runId: task?.runId || '',
      jobId: task?.jobId || jobId,
      parentRunId: task?.parentRunId || '',
      providerId: context.providerId || currentTool
    });
    recordPythonResultDiagnostics(script, result);
    await finishHistoryTask(task, result);
    return result;
  } catch (error) {
    await finishHistoryTask(task, null, error);
    throw error;
  }
}

function shouldTrackTask(title) {
  const text = String(title || '');
  if (/(保存|登录|读取|扫描|计划|配置|权限|知识库|文件夹)/.test(text)) return false;
  return /(导出|导入|上传)/.test(text);
}

async function resumeTask(task) {
  if (!task) return;
  if (isRunning) {
    alert('当前已有任务运行中，请等待结束或先停止当前任务。');
    return;
  }
  if (!task.script || !Array.isArray(task.args)) {
    alert('这条任务缺少可继续执行的命令参数。');
    return;
  }
  const args = resumeTaskArgs(task);
  const provider = TOOLS[task.providerId] || {};
  const retryArg = providerRetryFailureArg(provider);
  const shouldRetry = window.WandaoTaskResume?.shouldRetryFailureItems;
  const retryingFailures = typeof shouldRetry === 'function'
    ? shouldRetry(task, retryArg, taskFailureCount(task))
    : Boolean(
      retryArg
      && !['stopped', 'interrupted'].includes(String(task?.status || '').toLowerCase())
      && taskFailureCount(task) > 0
      && args.includes(retryArg)
    );
  const confirmDetail = retryingFailures
    ? `将只重试上次报告中的失败项，共 ${taskFailureCount(task)} 个。`
    : '将按历史命令重新执行，适合增量任务或中断后继续。';
  if (!confirm(`继续任务：${task.title || task.script}\n${confirmDetail}\n\n确认继续吗？`)) {
    return;
  }
  if (task.providerId && TOOLS[task.providerId]) {
    switchTool(task.providerId);
  }
  setProviderRunning(task.providerId || currentTool, true);
  startProgress(`继续任务：${task.title || task.script}`, retryingFailures ? '正在读取上次报告并重试失败项...' : '正在按历史命令重新执行，脚本会根据自身增量能力跳过已完成内容。');
  log(retryingFailures ? `重试失败项：${task.title || task.script}` : `继续任务：${task.title || task.script}`, 'info');
  try {
    const result = await runTrackedPythonCommand(task.script, args, {
      providerId: task.providerId || currentTool,
      title: retryingFailures ? `重试失败项：${task.title || task.script}` : `继续任务：${task.title || task.script}`,
      action: retryingFailures ? '重试失败项' : (task.action || '继续'),
      jobId: task.jobId || task.id,
      parentRunId: task.runId || task.id
    });
    if (result.success) {
      log('历史任务继续执行完成', 'success');
      if (result.data) log(JSON.stringify(result.data, null, 2), 'success');
      finishProgress(true, '历史任务继续执行完成');
    } else if (result.code === 130) {
      log('历史任务继续执行已停止，已完成项目会在下次继续时跳过。', 'warn');
      finishProgress(false, '历史任务继续执行已停止');
    } else {
      log(`历史任务继续执行失败：${result.error}`, 'error');
      finishProgress(false, '历史任务继续执行失败，请查看日志');
    }
  } catch (error) {
    log(`历史任务继续执行出错：${formatError(error)}`, 'error');
    finishProgress(false, '历史任务继续执行出错，请查看日志');
  } finally {
    setProviderRunning(task.providerId || currentTool, false);
  }
}

function latestResumableTask() {
  return taskHistory.find(canResumeTask);
}

function setProviderRunning(providerId, running) {
  if (providerId === 'feishu-import' && typeof setFeishuImportRunning === 'function') {
    setFeishuImportRunning(running);
    return;
  }
  setRunning(running, providerId || currentTool);
}

function progressElements() {
  return {
    section: document.getElementById('progress-section'),
    title: document.getElementById('progress-title'),
    percent: document.getElementById('progress-percent'),
    fill: document.getElementById('progress-fill'),
    detail: document.getElementById('progress-detail'),
    track: document.querySelector('#progress-section .progress-track')
  };
}

function startProgress(title, detail = '任务启动中，正在等待进度信息...') {
  const els = progressElements();
  if (!els.section) return;
  progressVisible = true;
  pythonProgressBuffer = '';
  pythonLogSummaryBuffer = '';
  els.section.hidden = false;
  els.title.textContent = title || '任务进行中';
  els.percent.textContent = '进行中';
  els.fill.className = 'progress-fill indeterminate';
  els.fill.style.width = '';
  els.detail.textContent = detail;
  els.track?.removeAttribute('aria-valuenow');
  setLogCollapsed(false);
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
    els.track?.removeAttribute('aria-valuenow');
    if (detail) els.detail.textContent = detail;
    return;
  }
  const ratio = Math.min(1, safeDone / safeTotal);
  const percent = Math.max(0, Math.min(100, Math.round(ratio * 100)));
  els.percent.textContent = `${percent}%`;
  els.fill.className = 'progress-fill';
  els.fill.style.width = `${percent}%`;
  els.track?.setAttribute('aria-valuenow', String(percent));
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
  els.track?.setAttribute('aria-valuenow', success ? '100' : '0');
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

function isStructuredPythonLine(line) {
  const text = String(line || '').trim();
  if (!text) return true;
  if (/^[{}\[\],]$/.test(text)) return true;
  if (/^"[^"]+"\s*:/.test(text)) return true;
  if (/^-?\d+(\.\d+)?[,]?$/.test(text)) return true;
  return false;
}

function summarizePythonLine(line) {
  const text = String(line || '').trim();
  if (!text || isStructuredPythonLine(text)) return null;
  if (/^progress\b/i.test(text)) return null;

  if (/Loaded\s+\d+\s+auth cookies/i.test(text)) {
    return { type: 'info', message: '已加载登录凭证。' };
  }
  if (/Saved\s+\d+\s+auth cookies/i.test(text)) {
    return { type: 'success', message: '登录凭证已保存。' };
  }
  if (/Chrome .*ready|Chrome opened/i.test(text)) {
    return { type: 'info', message: '浏览器已打开，请按页面提示完成登录或授权。' };
  }

  if (/(Traceback|Error:|HTTP\s+[45]\d\d|失败|错误|Access denied|permission denied|rate limit|限流|Too Many Requests)/i.test(text)) {
    return { type: 'error', message: formatUserError(text) };
  }

  if (/^(开始|完成|目录读取完成|跳过|导出完成|导入完成|已|Created|Uploaded|Saved|Chrome)/i.test(text)) {
    return { type: 'info', message: compactLogSummary(text, 180) };
  }

  if (text.length <= 160 && /[一-龥]/.test(text)) {
    return { type: 'info', message: text };
  }
  return null;
}

function appendPythonUserSummaries(data) {
  pythonLogSummaryBuffer += String(data || '');
  const lines = pythonLogSummaryBuffer.split(/\r?\n/);
  pythonLogSummaryBuffer = lines.pop() || '';
  lines.forEach((line) => {
    const summary = summarizePythonLine(line);
    if (summary) appendUserLog(summary.message, summary.type);
  });
}

function getPythonLogProcessor() {
  if (pythonLogProcessor) return pythonLogProcessor;
  pythonLogProcessor = window.WandaoStructuredLogs?.createProcessor?.({
    appendDetailedLog,
    appendUserLog,
    updateProgress,
    formatUserError,
    summarizePythonLine,
    compactDiagnostic,
    firstNonEmpty,
    formatError,
    onPlainLine(line) {
      appendDetailedLog('python', 'info', line);
      appendPythonUserSummaries(`${line}\n`);
      handlePythonProgress(`${line}\n`);
    }
  }) || null;
  return pythonLogProcessor;
}

function handlePlainPythonLogLine(line) {
  appendDetailedLog('python', 'info', line);
  appendPythonUserSummaries(`${line}\n`);
  handlePythonProgress(`${line}\n`);
}

function handlePythonLogLine(line) {
  if (!line) return;
  const processor = getPythonLogProcessor();
  if (processor) {
    processor.handleLine(line);
    return;
  }
  handlePlainPythonLogLine(line);
}

function handlePythonLogChunk(data) {
  const processor = getPythonLogProcessor();
  if (processor) {
    processor.handleChunk(data);
    return;
  }
  String(data || '').split(/\r?\n/).forEach(handlePlainPythonLogLine);
}

// Listen to Python logs
window.electronAPI.onPythonLog((data) => {
  handlePythonLogChunk(data);
});

function providerList(group) {
  refreshProviderTools();
  if (PROVIDER_REGISTRY?.list) return PROVIDER_REGISTRY.list(group);
  return Object.entries(TOOLS)
    .map(([id, provider]) => ({ id, group: provider.isImport ? 'import' : 'export', ...provider }))
    .filter((provider) => provider.group === group);
}

async function loadProviderManifests() {
  if (!window.electronAPI.getProviderManifests || !PROVIDER_REGISTRY?.replaceExternal) return;
  const result = await window.electronAPI.getProviderManifests();
  if (!result?.success) {
    log(`加载社区 provider 失败：${result?.error || '未知错误'}`, 'error');
    return;
  }
  const manifests = Array.isArray(result.providers) ? result.providers : [];
  const manifestErrors = Array.isArray(result.errors) ? result.errors : [];
  manifestErrors.forEach((message) => appendDetailedLog('provider', 'error', message));
  if (manifestErrors.length) {
    appendUserLog(`有 ${manifestErrors.length} 个本地 Provider 配置无效，已安全忽略。详情请查看详细日志。`, 'warn');
  }
  PROVIDER_REGISTRY.replaceExternal(manifests);
  refreshProviderTools();
  appendDetailedLog('provider', 'info', `已加载 ${manifests.length} 个外部 Provider。`);
}

function renderProviderSafetyNotice(provider) {
  if (!window.WandaoProviderRuntime?.shouldConfirmExecution(provider)) return '';
  const title = window.WandaoProviderRuntime.executionWarningTitle(provider);
  const source = window.WandaoProviderRuntime.sourceText(provider);
  return `
    <div class="info-box provider-safety-notice">
      <strong>${escapeHtml(title)}</strong>
      <p>这个 Provider 来自${escapeHtml(source)}，执行动作时会在本机运行脚本。请确认来源可信，不要运行陌生人提供的未知脚本。</p>
    </div>
  `;
}

function confirmProviderExecution(provider, action = null) {
  if (!window.WandaoProviderRuntime?.shouldConfirmExecution(provider, action)) return true;
  return confirm(window.WandaoProviderRuntime.executionConfirmMessage(provider));
}

function allProviders() {
  refreshProviderTools();
  if (PROVIDER_REGISTRY?.all) return PROVIDER_REGISTRY.all();
  return Object.values(TOOLS || {});
}

function primaryNavIdFor(toolId = currentTool) {
  if (PRIMARY_NAV_ITEMS.some((item) => item.id === toolId)) return toolId;
  if (String(toolId || '').startsWith('platform:')) return 'platform-center';
  if (TOOLS[toolId]) return 'platform-center';
  return DEFAULT_VIEW_ID;
}

function setToolHeading(title, description) {
  const titleNode = document.getElementById('tool-title');
  const descriptionNode = document.getElementById('tool-description');
  const labelNode = document.querySelector('.tool-heading-label');
  if (titleNode) titleNode.textContent = title || '万能导 Wandao';
  if (descriptionNode) descriptionNode.textContent = description || '';
  if (labelNode) labelNode.textContent = primaryNavIdFor() === 'platform-center' ? '平台工作区' : '万能导工作台';
}

function setTaskHistoryVisible(visible) {
  const section = document.querySelector('.task-history-section');
  if (section) section.hidden = !visible;
}

function platformKey(provider) {
  return provider.platform || provider.id;
}

function platformMeta(key, providers = []) {
  const first = providers[0] || {};
  const meta = PLATFORM_META[key] || {};
  return {
    name: meta.name || first.name || first.title || key,
    description: meta.description || first.description || '',
    tags: meta.tags || []
  };
}

function platformSortIndex(key) {
  const index = PLATFORM_ORDER.indexOf(key);
  return index === -1 ? Number.MAX_SAFE_INTEGER : index;
}

function platformGroups() {
  const map = new Map();
  allProviders().forEach((provider) => {
    const key = platformKey(provider);
    if (!map.has(key)) map.set(key, { key, providers: [] });
    map.get(key).providers.push(provider);
  });
  return Array.from(map.values())
    .map((group) => {
      const meta = platformMeta(group.key, group.providers);
      return {
        ...group,
        ...meta,
        providers: group.providers.slice().sort((a, b) => {
          const groupRank = { export: 1, import: 2, guide: 3 };
          return (groupRank[a.group] || 9) - (groupRank[b.group] || 9)
            || String(a.title || a.id).localeCompare(String(b.title || b.id), 'zh-Hans-CN');
        })
      };
    })
    .sort((a, b) => {
      return platformSortIndex(a.key) - platformSortIndex(b.key)
        || String(a.name).localeCompare(String(b.name), 'zh-Hans-CN');
    });
}

function findPlatformGroup(key) {
  return platformGroups().find((group) => group.key === key);
}

function providerActionLabel(provider) {
  if (provider.type === 'guide' || provider.group === 'guide') return '查看教程';
  if (provider.isImport || provider.group === 'import') return '导入 Markdown';
  if (provider.capabilities?.export) return '导出为 Markdown';
  return provider.navLabel || provider.title || provider.id;
}

function providerActionTone(provider) {
  if (provider.isImport || provider.group === 'import') return 'import';
  if (provider.type === 'guide' || provider.group === 'guide') return 'guide';
  return 'export';
}

function providerFeatureTags(provider) {
  const tags = new Set();
  if (provider.capabilities?.export) tags.add('导出');
  if (provider.capabilities?.import || provider.isImport) tags.add('导入');
  if (provider.type === 'guide' || provider.capabilities?.guide) tags.add('教程');
  return Array.from(tags);
}

function platformCapabilityTags(group) {
  const tags = new Set();
  group.providers.forEach((provider) => {
    providerFeatureTags(provider).forEach((tag) => tags.add(tag));
  });
  (group.tags || []).forEach((tag) => {
    if (tag === '导入' || tag === '导出' || tag === '教程') tags.add(tag);
  });
  return Array.from(tags);
}

function providerPlatformSiblings(provider) {
  const group = findPlatformGroup(platformKey(provider));
  return group ? group.providers : [provider];
}

function navigationIcon(name) {
  const paths = {
    home: '<path d="M3 10.5 12 3l9 7.5v9A1.5 1.5 0 0 1 19.5 21h-15A1.5 1.5 0 0 1 3 19.5v-9Z"/><path d="M9 21v-7h6v7"/>',
    platforms: '<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/>',
    tasks: '<path d="M9 6h11M9 12h11M9 18h11"/><path d="m3.5 6 1 1 2-2M3.5 12l1 1 2-2M3.5 18l1 1 2-2"/>',
    notice: '<path d="M5 4h14a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H7l-4 2V6a2 2 0 0 1 2-2Z"/><path d="M8 9h8M8 13h6"/>',
    plugins: '<path d="M8 3v4M16 3v4M5 9h14v4a7 7 0 0 1-14 0V9Z"/><path d="M12 20v-5"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 0 0-.08-1l2-1.5-2-3.46-2.35.95a7 7 0 0 0-1.72-1L14.5 3h-5l-.35 2.99a7 7 0 0 0-1.72 1L5.08 6.04l-2 3.46L5.08 11a7 7 0 0 0 0 2l-2 1.5 2 3.46 2.35-.95a7 7 0 0 0 1.72 1L9.5 21h5l.35-2.99a7 7 0 0 0 1.72-1l2.35.95 2-3.46-2-1.5c.05-.33.08-.66.08-1Z"/>'
  };
  return `<svg class="nav-icon" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${paths[name] || paths.platforms}</svg>`;
}

function platformMark(group) {
  const label = String(group?.name || group?.key || 'W').trim();
  if (/^[A-Za-z]/.test(label)) return label.slice(0, 2).toUpperCase();
  return label.slice(0, 1);
}

function renderProviderNavigation() {
  const sidebar = document.getElementById('provider-sidebar') || document.querySelector('.sidebar');
  if (!sidebar) return;
  const activeId = primaryNavIdFor();
  sidebar.innerHTML = `
    <div class="sidebar-intro">
      <span>知识迁移</span>
      <strong>从这里开始</strong>
    </div>
    <nav class="nav-group" aria-label="工作台">
      <span class="nav-group-label">工作台</span>
      ${PRIMARY_NAV_ITEMS.map((item) => `
        <button class="nav-item ${item.id === activeId ? 'active' : ''}" data-tool="${escapeHtml(item.id)}" type="button" ${item.id === activeId ? 'aria-current="page"' : ''}>
          ${navigationIcon(item.icon)}
          <span class="nav-copy">
            <strong>${escapeHtml(item.label)}</strong>
            <small>${escapeHtml(item.description)}</small>
          </span>
        </button>
      `).join('')}
    </nav>
    <div class="sidebar-footnote">本地优先 · Markdown 归档</div>
  `;
}

function bindWorkbenchActions(root = document.getElementById('content-area')) {
  if (!root) return;
  root.querySelectorAll('[data-switch-view]').forEach((button) => {
    button.addEventListener('click', () => {
      if (!isRunning) switchTool(button.dataset.switchView);
    });
  });
  root.querySelectorAll('[data-platform-key]').forEach((button) => {
    button.addEventListener('click', () => {
      if (!isRunning) switchTool(`platform:${button.dataset.platformKey}`);
    });
  });
  root.querySelectorAll('[data-open-provider]').forEach((button) => {
    button.addEventListener('click', () => {
      if (!isRunning) switchTool(button.dataset.openProvider);
    });
  });
  root.querySelectorAll('[data-open-url]').forEach((button) => {
    button.addEventListener('click', () => {
      window.electronAPI.openExternal(button.dataset.openUrl);
    });
  });
}

function encodedGitHubPath(pathValue) {
  return String(pathValue || '')
    .split('/')
    .map((part) => encodeURIComponent(part))
    .join('/');
}

function noticeRawUrl(item) {
  if (!item) return '';
  if (item.url && String(item.url).startsWith('https://raw.githubusercontent.com/')) return item.url;
  if (item.path) return `${GITHUB_RAW_BASE}${encodedGitHubPath(item.path)}`;
  return '';
}

function noticeGitHubUrl(item) {
  if (!item) return GITHUB_REPO_URL;
  if (item.htmlUrl) return item.htmlUrl;
  if (item.path) return `${GITHUB_BLOB_BASE}${encodedGitHubPath(item.path)}`;
  return GITHUB_REPO_URL;
}

function normalizeNoticeManifest(raw) {
  const manifest = raw && typeof raw === 'object' ? raw : FALLBACK_NOTICE_CENTER;
  const items = Array.isArray(manifest.items) ? manifest.items : [];
  return {
    ...manifest,
    items: items
      .map((item, index) => ({
        id: String(item.id || `notice-${index}`),
        type: item.type === 'tutorial' ? 'tutorial' : 'announcement',
        pinned: Boolean(item.pinned),
        title: String(item.title || '未命名内容'),
        summary: String(item.summary || ''),
        date: String(item.date || manifest.updatedAt || ''),
        badge: String(item.badge || ''),
        tags: Array.isArray(item.tags) ? item.tags.map(String) : [],
        path: item.path ? String(item.path) : '',
        url: item.url ? String(item.url) : '',
        htmlUrl: item.htmlUrl ? String(item.htmlUrl) : '',
        body: item.body ? String(item.body) : ''
      }))
      .sort((a, b) => {
        if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
        return String(b.date).localeCompare(String(a.date)) || a.title.localeCompare(b.title, 'zh-Hans-CN');
      })
  };
}

function noticeItems() {
  return normalizeNoticeManifest(noticeCenterState.manifest || FALLBACK_NOTICE_CENTER).items;
}

function noticeGroups(items = noticeItems()) {
  const announcements = items.filter((item) => item.type !== 'tutorial');
  const tutorials = items.filter((item) => item.type === 'tutorial');
  return { announcements, tutorials };
}

function defaultNoticeId(items = noticeItems()) {
  const groups = noticeGroups(items);
  return groups.announcements[0]?.id || groups.tutorials[0]?.id || items[0]?.id || '';
}

async function readRemoteText(url) {
  if (!url) throw new Error('文档没有配置 GitHub 路径');
  if (window.electronAPI?.fetchRemoteText) {
    const result = await window.electronAPI.fetchRemoteText(url);
    if (!result?.success) throw new Error(result?.error || '读取 GitHub 文档失败');
    return result.content || '';
  }
  const response = await fetch(url);
  if (!response.ok) throw new Error(`GitHub 返回 HTTP ${response.status}`);
  return response.text();
}

function renderNoticeCenterIfActive() {
  if (currentTool === 'notice-center') {
    renderNoticeCenterPage();
  }
}

async function loadNoticeItemBody(item, shouldRender = true) {
  if (!item) return;
  const itemId = String(item.id || '');
  const cached = Object.prototype.hasOwnProperty.call(noticeCenterState.bodyCache, itemId)
    ? noticeCenterState.bodyCache[itemId]
    : null;
  if (cached !== null) {
    noticeCenterState.selectedBodyId = itemId;
    noticeCenterState.selectedBody = cached;
    noticeCenterState.selectedBodyError = '';
    noticeCenterState.selectedBodyStatus = 'ready';
    if (shouldRender) renderNoticeCenterIfActive();
    return;
  }
  const requestSeq = noticeCenterState.bodyRequestSeq + 1;
  noticeCenterState.bodyRequestSeq = requestSeq;
  noticeCenterState.selectedBodyId = itemId;
  noticeCenterState.selectedBodyStatus = 'loading';
  noticeCenterState.selectedBody = '';
  noticeCenterState.selectedBodyError = '';
  if (shouldRender) renderNoticeCenterIfActive();
  try {
    const body = item.body || await readRemoteText(noticeRawUrl(item));
    if (noticeCenterState.bodyRequestSeq !== requestSeq || noticeCenterState.selectedId !== itemId) return;
    noticeCenterState.bodyCache[itemId] = body;
    noticeCenterState.selectedBody = body;
    noticeCenterState.selectedBodyStatus = 'ready';
  } catch (error) {
    if (noticeCenterState.bodyRequestSeq !== requestSeq || noticeCenterState.selectedId !== itemId) return;
    noticeCenterState.selectedBody = '';
    noticeCenterState.selectedBodyError = formatError(error);
    noticeCenterState.selectedBodyStatus = 'error';
  }
  if (shouldRender) renderNoticeCenterIfActive();
}

async function loadNoticeCenter(force = false) {
  if (noticeCenterState.status === 'loading') return;
  if (!force && noticeCenterState.status === 'ready') return;
  noticeCenterState.status = 'loading';
  noticeCenterState.error = '';
  if (force) {
    noticeCenterState.bodyCache = {};
  }
  renderNoticeCenterIfActive();
  try {
    const text = await readRemoteText(NOTICE_CENTER_MANIFEST_URL);
    noticeCenterState.manifest = normalizeNoticeManifest(JSON.parse(text));
    noticeCenterState.status = 'ready';
  } catch (error) {
    noticeCenterState.manifest = normalizeNoticeManifest(FALLBACK_NOTICE_CENTER);
    noticeCenterState.status = 'fallback';
    noticeCenterState.error = formatError(error);
  }
  const items = noticeItems();
  if (!items.some((item) => item.id === noticeCenterState.selectedId)) {
    noticeCenterState.selectedId = defaultNoticeId(items);
  }
  await loadNoticeItemBody(items.find((item) => item.id === noticeCenterState.selectedId), false);
  renderNoticeCenterIfActive();
}

function noticeKindLabel(item) {
  if (item.pinned) return '置顶公告';
  if (item.type === 'tutorial') return '教程';
  return item.badge || '公告';
}

function renderNoticeCard(item) {
  const active = item.id === noticeCenterState.selectedId;
  const classes = ['notice-card'];
  if (active) classes.push('active');
  if (item.pinned) classes.push('pinned');
  return `
    <button class="${classes.join(' ')}" data-notice-id="${escapeHtml(item.id)}" type="button">
      <span class="notice-card-meta">
        <strong>${escapeHtml(noticeKindLabel(item))}</strong>
        <time>${escapeHtml(item.date || '')}</time>
      </span>
      <span class="notice-card-title">${escapeHtml(item.title)}</span>
      ${item.summary ? `<span class="notice-card-summary">${escapeHtml(item.summary)}</span>` : ''}
    </button>
  `;
}

function renderNoticeListSection(title, items, emptyText) {
  return `
    <section class="notice-list-section">
      <div class="notice-list-title">
        <h4>${escapeHtml(title)}</h4>
      </div>
      ${items.length ? items.map(renderNoticeCard).join('') : `<div class="notice-empty">${escapeHtml(emptyText)}</div>`}
    </section>
  `;
}

function bindNoticeCenterActions(root) {
  root.querySelectorAll('[data-notice-id]').forEach((button) => {
    button.addEventListener('click', () => {
      const item = noticeItems().find((entry) => entry.id === button.dataset.noticeId);
      if (!item) return;
      noticeCenterState.selectedId = item.id;
      loadNoticeItemBody(item);
    });
  });
  root.querySelector('[data-notice-action="refresh"]')?.addEventListener('click', () => {
    loadNoticeCenter(true);
  });
  root.querySelectorAll('[data-notice-open]').forEach((button) => {
    button.addEventListener('click', () => {
      window.electronAPI.openExternal(button.dataset.noticeOpen);
    });
  });
  root.querySelectorAll('[data-external-link]').forEach((link) => {
    link.addEventListener('click', (event) => {
      event.preventDefault();
      window.electronAPI.openExternal(link.href);
    });
  });
}

function renderHomePage() {
  setTaskHistoryVisible(false);
  setToolHeading('首页', '选择一个平台，开始导出、导入或继续最近任务。');
  const groups = platformGroups();
  const providers = allProviders();
  const exportCount = providers.filter((provider) => provider.capabilities?.export).length;
  const importCount = providers.filter((provider) => provider.capabilities?.import || provider.isImport).length;
  const guideCount = providers.filter((provider) => provider.type === 'guide' || provider.group === 'guide').length;
  const contentArea = document.getElementById('content-area');
  contentArea.innerHTML = `
    <section class="home-hero">
      <div class="home-hero-copy">
        <p class="view-kicker">本地优先的知识迁移工具</p>
        <h3>让每一份知识，都有可带走的归档。</h3>
        <p>选择来源平台，万能导会尽量保留目录、正文和图片，并整理为清晰的 Markdown。</p>
        <div class="home-hero-actions">
          <button class="btn-primary" data-switch-view="platform-center" type="button">选择平台</button>
          <button class="btn-on-dark" data-switch-view="task-center" type="button">继续最近任务</button>
        </div>
      </div>
      <div class="knowledge-route" aria-label="知识归档流程">
        <span class="route-label">清晰的三步流程</span>
        <div class="route-flow">
          <span class="route-node"><small>第一步</small><strong>选择平台</strong></span>
          <span class="route-connector" aria-hidden="true"></span>
          <span class="route-node"><small>第二步</small><strong>执行任务</strong></span>
          <span class="route-connector" aria-hidden="true"></span>
          <span class="route-node route-node-final"><small>完成</small><strong>本地 Markdown</strong></span>
        </div>
        <p>任务过程、失败原因和断点恢复统一记录。</p>
      </div>
    </section>
    <section class="metric-grid">
      <article class="metric-card"><span>已接入平台</span><strong>${groups.length}</strong></article>
      <article class="metric-card"><span>可用导出</span><strong>${exportCount}</strong></article>
      <article class="metric-card"><span>可用导入</span><strong>${importCount}</strong></article>
      <article class="metric-card"><span>平台教程</span><strong>${guideCount}</strong></article>
    </section>
    <section class="home-grid">
      <article class="home-card home-card-primary">
        <span class="card-eyebrow">开始新任务</span>
        <h4>从常用平台带走知识</h4>
        <p>已安装的平台都从同一个入口开始，更多平台可以按需从插件中心安装。</p>
        <button class="btn-primary" data-switch-view="platform-center" type="button">打开平台中心</button>
      </article>
      <article class="home-card">
        <span class="card-eyebrow">继续处理</span>
        <h4>任务记录不会散落</h4>
        <p>查看最近导入导出记录，复制报告和失败项，继续或重试支持恢复的任务。</p>
        <button class="btn-secondary" data-switch-view="task-center" type="button">查看任务中心</button>
      </article>
    </section>
  `;
  bindWorkbenchActions(contentArea);
}

function renderPlatformCard(group) {
  const tags = platformCapabilityTags(group);
  return `
    <article class="platform-card">
      <div class="platform-card-main">
        <div class="platform-card-header">
          <span class="platform-mark" aria-hidden="true">${escapeHtml(platformMark(group))}</span>
          <div class="platform-card-topline">
            <h3>${escapeHtml(group.name)}</h3>
            <span>${group.providers.length} 个操作</span>
          </div>
        </div>
        <p>${escapeHtml(group.description || '进入后选择具体操作。')}</p>
        <div class="provider-tags">
          ${tags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join('')}
        </div>
      </div>
      <button class="btn-secondary card-action" data-platform-key="${escapeHtml(group.key)}" type="button">查看操作 <span aria-hidden="true">→</span></button>
    </article>
  `;
}

function renderPlatformCenterPage() {
  setTaskHistoryVisible(false);
  setToolHeading('平台中心', '选择平台后，再选择导出、导入或查看教程。');
  const groups = platformGroups();
  const contentArea = document.getElementById('content-area');
  contentArea.innerHTML = `
    <section class="view-panel platform-center-hero">
      <div class="view-panel-header">
        <div>
          <p class="view-kicker">${groups.length} 个平台已经就绪</p>
          <h3>你想从哪个平台开始？</h3>
          <p>进入平台后再选择导出、导入或教程，不同平台只展示自己真正支持的操作。</p>
        </div>
        <button class="btn-secondary" data-switch-view="task-center" type="button">最近任务</button>
      </div>
    </section>
    <section class="platform-grid">
      ${groups.map(renderPlatformCard).join('')}
    </section>
    <section class="view-panel platform-discovery-card">
      <div>
        <p class="view-kicker">持续扩展</p>
        <h3>还没有你需要的平台？</h3>
        <p>更多平台能力会持续由社区插件提供。可在插件中心搜索稳定或带有“实验性”标记的平台。</p>
      </div>
      <button class="btn-primary" data-switch-view="plugin-center" type="button">去插件中心找更多平台</button>
    </section>
  `;
  bindWorkbenchActions(contentArea);
}

function renderProviderActionCard(provider) {
  const tags = providerFeatureTags(provider);
  const tone = providerActionTone(provider);
  return `
    <article class="provider-action-card ${tone}">
      <div>
        <div class="provider-action-label"><span aria-hidden="true"></span>${escapeHtml(providerActionLabel(provider))}</div>
        <h4>${escapeHtml(provider.title || provider.name || provider.id)}</h4>
        <p>${escapeHtml(provider.description || '')}</p>
        <div class="provider-tags compact">
          ${tags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join('')}
        </div>
      </div>
      <button class="${tone === 'export' ? 'btn-primary' : 'btn-secondary'}" data-open-provider="${escapeHtml(provider.id)}" type="button">开始</button>
    </article>
  `;
}

function renderPlatformDetailPage(key) {
  const group = findPlatformGroup(key);
  if (!group) {
    log(`未找到平台：${key}`, 'error');
    switchTool('platform-center');
    return;
  }
  setTaskHistoryVisible(false);
  setToolHeading(group.name, group.description || '选择这个平台支持的动作。');
  const tags = platformCapabilityTags(group);
  const contentArea = document.getElementById('content-area');
  contentArea.innerHTML = `
    <section class="platform-detail-hero">
      <div class="platform-detail-main">
        <button class="btn-text" data-switch-view="platform-center" type="button">返回平台中心</button>
        <div class="platform-detail-title">
          <span class="platform-mark large" aria-hidden="true">${escapeHtml(platformMark(group))}</span>
          <div>
            <p class="view-kicker">平台</p>
            <h3>${escapeHtml(group.name)}</h3>
            <p>${escapeHtml(group.description || '')}</p>
            <div class="provider-tags">
              ${tags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join('')}
            </div>
          </div>
        </div>
      </div>
      <button class="btn-secondary" data-switch-view="task-center" type="button">查看历史任务</button>
    </section>
    <section class="provider-action-grid">
      ${group.providers.map(renderProviderActionCard).join('')}
    </section>
  `;
  bindWorkbenchActions(contentArea);
}

function normalizePathKey(value) {
  const text = String(value || '').trim();
  return navigator.platform.toLowerCase().includes('win') ? text.toLowerCase() : text;
}

function browserNameFromPath(browserPath) {
  const text = String(browserPath || '');
  const lower = text.toLowerCase();
  if (lower.includes('msedge') || lower.includes('microsoft edge')) return 'Microsoft Edge';
  if (lower.includes('chromium')) return 'Chromium';
  if (lower.includes('brave')) return 'Brave';
  if (lower.includes('chrome')) return 'Google Chrome';
  return '自定义浏览器';
}

function browserOptionLabel(browser) {
  const source = browser.source ? ` · ${browser.source}` : '';
  return `${browser.name || browserNameFromPath(browser.path)}${source}`;
}

function browserStatusText() {
  const count = appSettingsState.browsers.length;
  const status = appSettingsState.browserDetectStatus;
  if (status === 'loading') return '检测中';
  if (status === 'success') return `已检测到 ${count} 个`;
  if (status === 'empty') return '未检测到';
  if (status === 'error') return '检测失败';
  return '未检测';
}

function browserStatusClass() {
  const status = appSettingsState.browserDetectStatus;
  if (status === 'success') return 'success';
  if (status === 'empty' || status === 'error') return 'warning';
  if (status === 'loading') return 'loading';
  return '';
}

function selectedBrowserPathForSettings() {
  return appSettingsState.settings?.browserPath || '';
}

function browserSelectionSummary() {
  const selected = selectedBrowserPathForSettings();
  if (selected) {
    return `当前固定使用：${browserNameFromPath(selected)}`;
  }
  const firstBrowser = appSettingsState.browsers[0];
  if (firstBrowser) {
    return `当前使用：自动检测，优先使用 ${firstBrowser.name}`;
  }
  if (appSettingsState.browserDetectStatus === 'empty') {
    return '当前使用：自动检测，但还没有发现可用浏览器。';
  }
  return '当前使用：自动检测。';
}

function renderBrowserOptions() {
  const selected = selectedBrowserPathForSettings();
  const selectedKey = normalizePathKey(selected);
  const seen = new Set(['']);
  const options = [
    `<option value=""${selected ? '' : ' selected'}>自动检测（推荐）</option>`
  ];
  for (const browser of appSettingsState.browsers) {
    const browserPath = browser.path || '';
    const key = normalizePathKey(browserPath);
    if (!browserPath || seen.has(key)) continue;
    seen.add(key);
    options.push(
      `<option value="${escapeHtml(browserPath)}"${key === selectedKey ? ' selected' : ''}>${escapeHtml(browserOptionLabel(browser))}</option>`
    );
  }
  if (selected && !seen.has(selectedKey)) {
    options.push(`<option value="${escapeHtml(selected)}" selected>${escapeHtml(`${browserNameFromPath(selected)} · 手动选择`)}</option>`);
  }
  return options.join('');
}

function renderBrowserList() {
  if (appSettingsState.browserDetectStatus === 'idle') {
    return '<div class="settings-browser-note">打开设置后会自动检测本机可用浏览器，也可以点击下方按钮重新检测。</div>';
  }
  if (appSettingsState.browserDetectStatus === 'loading') {
    return '<div class="settings-browser-note">正在检测 Chrome、Edge、Chromium 等可用浏览器...</div>';
  }
  if (appSettingsState.browserDetectStatus === 'error') {
    return `<div class="settings-browser-note warning">${escapeHtml(appSettingsState.browserDetectError || '检测失败，请稍后重试。')}</div>`;
  }
  if (!appSettingsState.browsers.length) {
    return `
      <div class="setup-card warning">
        <strong>没有检测到可用浏览器</strong>
        <p>请安装 Chrome、Edge 或 Chromium 后重新检测，也可以手动选择浏览器可执行文件。</p>
      </div>
    `;
  }
  return `
    <div class="browser-list">
      ${appSettingsState.browsers.map((browser) => `
        <div class="browser-option">
          <strong>${escapeHtml(browser.name)}</strong>
          <span>${escapeHtml(browser.source || '已检测')}</span>
          <code>${escapeHtml(browser.path)}</code>
        </div>
      `).join('')}
    </div>
  `;
}

async function loadAppSettings() {
  if (!window.electronAPI.getAppSettings) return;
  try {
    const result = await window.electronAPI.getAppSettings();
    if (result?.success) {
      appSettingsState.settings = result.settings || {};
    }
  } catch (error) {
    appendDetailedLog('settings', 'error', formatError(error));
  }
}

async function detectAvailableBrowsers(options = {}) {
  if (!window.electronAPI.detectBrowsers || appSettingsState.browserDetectStatus === 'loading') return;
  const silent = Boolean(options.silent);
  appSettingsState.browserDetectStatus = 'loading';
  appSettingsState.browserDetectError = '';
  if (!silent) log('正在检测可用浏览器...', 'info');
  if (currentTool === 'settings') renderSettingsPage();
  try {
    const result = await window.electronAPI.detectBrowsers();
    if (!result?.success) {
      throw new Error(result?.error || '检测浏览器失败');
    }
    appSettingsState.browsers = result.browsers || [];
    appSettingsState.browserDownloadUrl = result.downloadUrl || DEFAULT_BROWSER_DOWNLOAD_URL;
    if (!selectedBrowserPathForSettings() && result.selectedBrowserPath) {
      appSettingsState.settings.browserPath = result.selectedBrowserPath;
    }
    appSettingsState.browserDetectStatus = appSettingsState.browsers.length ? 'success' : 'empty';
    if (!silent) {
      const count = appSettingsState.browsers.length;
      log(count ? `已检测到 ${count} 个可用浏览器。` : '未检测到可用浏览器，请安装 Chrome 或手动选择浏览器。', count ? 'success' : 'warn');
    }
  } catch (error) {
    appSettingsState.browserDetectStatus = 'error';
    appSettingsState.browserDetectError = formatError(error);
    if (!silent) log(`检测浏览器失败：${appSettingsState.browserDetectError}`, 'error');
  } finally {
    if (currentTool === 'settings') renderSettingsPage();
  }
}

async function saveBrowserSetting(browserPath) {
  if (!window.electronAPI.saveAppSettings) {
    alert('当前版本暂不支持保存浏览器设置。');
    return;
  }
  const button = document.getElementById('settings-browser-save');
  if (button) {
    button.disabled = true;
    button.textContent = '保存中...';
  }
  try {
    const result = await window.electronAPI.saveAppSettings({ browserPath });
    if (!result?.success) {
      throw new Error(result?.error || '保存失败');
    }
    appSettingsState.settings = result.settings || {};
    appSettingsState.browsers = result.browsers || appSettingsState.browsers;
    appSettingsState.browserDownloadUrl = result.downloadUrl || appSettingsState.browserDownloadUrl;
    log(browserPath ? `已保存自动化浏览器：${browserNameFromPath(browserPath)}` : '已恢复为自动检测浏览器。', 'success');
  } catch (error) {
    log(`保存浏览器设置失败：${formatError(error)}`, 'error');
    alert(`保存失败：${formatError(error)}`);
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = '保存选择';
    }
    if (currentTool === 'settings') renderSettingsPage();
  }
}

async function chooseBrowserFile() {
  let browserPath = '';
  if (window.electronAPI.selectBrowserFile) {
    const result = await window.electronAPI.selectBrowserFile();
    if (!result || result.canceled) return;
    if (!result.success) {
      const message = result.error || '没有选择可用浏览器。';
      log(message, 'error');
      alert(message);
      return;
    }
    browserPath = result.path || '';
  } else {
    browserPath = await window.electronAPI.selectFile({
      title: '选择浏览器可执行文件',
      filters: [{ name: '浏览器可执行文件', extensions: ['exe', '*'] }]
    });
  }
  if (browserPath) {
    await saveBrowserSetting(browserPath);
  }
}

function renderSettingsPage() {
  setTaskHistoryVisible(false);
  setToolHeading('设置', '管理自动化浏览器、显示和应用信息。');
  const contentArea = document.getElementById('content-area');
  contentArea.innerHTML = `
    <section class="settings-grid">
      <article class="settings-card settings-card-wide">
        <div class="settings-card-head">
          <div>
            <span class="card-eyebrow">自动化环境</span>
            <h4>自动化浏览器</h4>
            <p>登录和部分网页读取会使用 Chrome、Edge 或 Chromium。</p>
          </div>
          <span class="settings-status ${browserStatusClass()}">${escapeHtml(browserStatusText())}</span>
        </div>
        <div class="form-group">
          <label for="settings-browser-select">使用哪个浏览器</label>
          <select id="settings-browser-select">
            ${renderBrowserOptions()}
          </select>
          <p class="field-hint">${escapeHtml(browserSelectionSummary())}</p>
        </div>
        ${renderBrowserList()}
        <div class="settings-actions">
          <button class="btn-primary" id="settings-browser-save" data-settings-action="save-browser" type="button">保存选择</button>
          <button class="btn-secondary" data-settings-action="detect-browser" type="button">重新检测</button>
          <button class="btn-secondary" data-settings-action="choose-browser" type="button">手动选择浏览器</button>
          <button class="btn-text" data-settings-action="download-browser" type="button">下载 Chrome</button>
        </div>
      </article>
    </section>
    <section class="settings-grid">
      <article class="settings-card settings-card-compact">
        <span class="card-eyebrow">外观</span>
        <h4>显示模式</h4>
        <p>当前主题：${document.body.dataset.theme === 'dark' ? '夜间模式' : '日间模式'}</p>
        <button class="btn-secondary" data-settings-action="theme" type="button">切换主题</button>
      </article>
      <article class="settings-card settings-card-compact">
        <span class="card-eyebrow">应用</span>
        <h4>版本更新</h4>
        <p>从 GitHub Releases 检查新版本。</p>
        <button class="btn-secondary" data-settings-action="check-update" type="button">检查更新</button>
      </article>
      <article class="settings-card settings-card-compact">
        <span class="card-eyebrow">诊断</span>
        <h4>日志显示</h4>
        <p data-settings-log-mode-summary>当前显示：${logViewMode === 'detail' ? '详细日志' : '用户日志'}</p>
        <button class="btn-secondary" data-settings-action="log-mode" type="button">切换日志</button>
      </article>
      <article class="settings-card settings-card-compact">
        <span class="card-eyebrow">帮助</span>
        <h4>关于</h4>
        <p>查看版本、项目地址和许可证。</p>
        <button class="btn-secondary" data-settings-action="about" type="button">关于万能导</button>
      </article>
    </section>
  `;
  if (appSettingsState.browserDetectStatus === 'idle') {
    window.setTimeout(() => detectAvailableBrowsers({ silent: true }), 0);
  }
  contentArea.querySelector('[data-settings-action="detect-browser"]')?.addEventListener('click', () => {
    detectAvailableBrowsers({ silent: false });
  });
  contentArea.querySelector('[data-settings-action="save-browser"]')?.addEventListener('click', () => {
    const browserPath = document.getElementById('settings-browser-select')?.value || '';
    saveBrowserSetting(browserPath);
  });
  contentArea.querySelector('[data-settings-action="choose-browser"]')?.addEventListener('click', () => {
    chooseBrowserFile();
  });
  contentArea.querySelector('[data-settings-action="download-browser"]')?.addEventListener('click', () => {
    window.electronAPI.openExternal(appSettingsState.browserDownloadUrl || DEFAULT_BROWSER_DOWNLOAD_URL);
  });
  contentArea.querySelector('[data-settings-action="theme"]')?.addEventListener('click', () => {
    toggleTheme();
    renderSettingsPage();
  });
  contentArea.querySelector('[data-settings-action="check-update"]')?.addEventListener('click', () => checkForUpdates(false));
  contentArea.querySelector('[data-settings-action="log-mode"]')?.addEventListener('click', () => {
    toggleLogViewMode();
    const summary = contentArea.querySelector('[data-settings-log-mode-summary]');
    if (summary) summary.textContent = `当前显示：${logViewMode === 'detail' ? '详细日志' : '用户日志'}`;
  });
  contentArea.querySelector('[data-settings-action="about"]')?.addEventListener('click', () => {
    window.electronAPI.showAbout();
  });
}

function renderTaskCenterPage() {
  setToolHeading('任务中心', '查看进度、失败原因，并继续支持恢复的任务。');
  const contentArea = document.getElementById('content-area');
  const resumableCount = taskHistory.filter(canResumeTask).length;
  contentArea.innerHTML = `
    <section class="task-center-hero">
      <div>
        <p class="view-kicker">任务记录</p>
        <h3>${taskHistory.length ? `已记录 ${taskHistory.length} 个任务` : '还没有任务记录'}</h3>
        <p>${resumableCount ? `${resumableCount} 个任务可以继续或重试。` : '开始一次导入或导出后，进度和报告会显示在这里。'}</p>
      </div>
      <button class="btn-primary" data-switch-view="platform-center" type="button">开始新任务</button>
    </section>
  `;
  setTaskHistoryVisible(true);
  renderTaskHistory();
  bindWorkbenchActions(contentArea);
}

function renderNoticeDocBody(selected) {
  const selectedId = selected?.id || '';
  const bodyMatchesSelection = noticeCenterState.selectedBodyId === selectedId;
  const status = bodyMatchesSelection ? noticeCenterState.selectedBodyStatus : 'idle';
  if (status === 'loading') {
    return '<div class="notice-doc-loading">正在读取内容...</div>';
  }
  if (status === 'error') {
    const detail = noticeCenterState.selectedBodyError || '';
    const githubUrl = selected ? noticeGitHubUrl(selected) : '';
    return `
      <div class="notice-doc-empty">
        <h4>这篇内容还没有同步到线上</h4>
        <p>作者发布后即可查看。你也可以${githubUrl ? `<a href="${escapeHtml(githubUrl)}" data-external-link="true">在 GitHub 上查看原文</a>，或` : ''}稍后再刷新。</p>
        ${detail ? `<details><summary>查看详细错误</summary><pre>${escapeHtml(detail)}</pre></details>` : ''}
      </div>
    `;
  }
  const source = (bodyMatchesSelection ? noticeCenterState.selectedBody : '') || selected?.body || '';
  if (!source) {
    return `
      <div class="notice-doc-empty">
        <h4>正在准备内容</h4>
        <p>如果长时间没有显示，请点击刷新或在 GitHub 打开原文。</p>
      </div>
    `;
  }
  return markdownToHtml(source);
}

function noticeSourceStatusText(status) {
  if (status === 'loading') return '正在同步';
  if (status === 'fallback') return '暂用内置内容';
  return '来自 GitHub';
}

function renderNoticeCenterPage() {
  setTaskHistoryVisible(false);
  setToolHeading('教程公告', '公告与教程从仓库同步，选择左侧条目阅读。');
  const contentArea = document.getElementById('content-area');
  const manifest = normalizeNoticeManifest(noticeCenterState.manifest || FALLBACK_NOTICE_CENTER);
  const items = manifest.items;
  const groups = noticeGroups(items);
  if (!noticeCenterState.selectedId) {
    noticeCenterState.selectedId = defaultNoticeId(items);
  }
  const selected = items.find((item) => item.id === noticeCenterState.selectedId) || items[0];
  const statusText = noticeSourceStatusText(noticeCenterState.status);
  const bodyHtml = renderNoticeDocBody(selected);
  const selectedBadgeClass = selected?.pinned ? 'notice-doc-badge pinned' : 'notice-doc-badge';
  const selectedTags = selected?.tags || [];

  contentArea.innerHTML = `
    <section class="notice-hero">
      <div class="view-panel-header">
        <div>
          <div class="notice-source-line">
            <span>${escapeHtml(statusText)}</span>
            <span>更新于 ${escapeHtml(formatUserTimestamp(manifest.updatedAt))}</span>
          </div>
        </div>
        <div class="notice-hero-actions">
          <button class="btn-text" data-notice-action="refresh" type="button">刷新</button>
          <button class="btn-text" data-notice-open="${escapeHtml(GITHUB_BLOB_BASE)}docs/tutorial-announcements.json" type="button">在 GitHub 打开索引</button>
        </div>
      </div>
    </section>

    <section class="notice-layout">
      <aside class="notice-list">
        ${renderNoticeListSection('公告', groups.announcements, '暂无公告。')}
        ${renderNoticeListSection('教程', groups.tutorials, '暂无教程。')}
      </aside>
      <article class="notice-document">
        <header class="notice-document-header">
          <div>
            <span class="${selectedBadgeClass}">${escapeHtml(selected ? noticeKindLabel(selected) : '文档')}</span>
            <h3>${escapeHtml(selected?.title || '暂无内容')}</h3>
            <p>${escapeHtml(selected?.date ? `${selected.date}${selected.summary ? ' · ' + selected.summary : ''}` : (selected?.summary || ''))}</p>
            ${selectedTags.length ? `<div class="notice-doc-tags">${selectedTags.map((tag) => `<em>${escapeHtml(tag)}</em>`).join('')}</div>` : ''}
          </div>
          ${selected ? `
            <div class="notice-doc-actions">
              <button class="btn-text" data-notice-open="${escapeHtml(noticeGitHubUrl(selected))}" type="button">在 GitHub 打开</button>
            </div>
          ` : ''}
        </header>
        <div class="guide-content notice-doc-content">
          ${bodyHtml}
        </div>
      </article>
    </section>
  `;
  bindNoticeCenterActions(contentArea);
  if (noticeCenterState.status === 'idle') {
    loadNoticeCenter(false);
  } else if (
    noticeCenterState.status !== 'loading' &&
    selected &&
    (noticeCenterState.selectedBodyId !== selected.id || (!noticeCenterState.selectedBody && noticeCenterState.selectedBodyStatus === 'idle'))
  ) {
    loadNoticeItemBody(selected);
  }
}

function renderProviderModeSwitcher(provider) {
  const siblings = providerPlatformSiblings(provider);
  if (siblings.length <= 1) return;
  const contentArea = document.getElementById('content-area');
  if (!contentArea) return;
  const group = findPlatformGroup(platformKey(provider));
  const switcher = document.createElement('section');
  switcher.className = 'provider-mode-switcher';
  switcher.innerHTML = `
    <div>
      <span>当前平台操作</span>
      <strong>${escapeHtml(group?.name || platformKey(provider))}</strong>
    </div>
    <div class="provider-mode-buttons">
      ${siblings.map((item) => `
        <button class="mode-button ${item.id === provider.id ? 'active' : ''}" data-open-provider="${escapeHtml(item.id)}" type="button">
          ${escapeHtml(providerActionLabel(item))}
        </button>
      `).join('')}
    </div>
  `;
  contentArea.prepend(switcher);
  bindWorkbenchActions(switcher);
}

const PLUGIN_PERMISSION_LABELS = {
  'browser-automation': '浏览器自动化',
  credentials: '登录凭证',
  'filesystem:read': '读取本地文件',
  'filesystem:write': '写入本地文件',
  network: '访问网络',
  process: '运行独立进程'
};

function pluginPermissionTags(plugin) {
  return (plugin.permissions || []).map((item) => PLUGIN_PERMISSION_LABELS[item] || item);
}

function pluginStatusText(plugin) {
  if (!plugin.compatibility?.compatible) return plugin.compatibility?.reason || '与当前版本不兼容';
  if (plugin.bundled && !plugin.installed) {
    return plugin.updateAvailable
      ? `随主程序提供 v${plugin.bundledVersion} · 有可安装更新`
      : `随主程序提供 v${plugin.bundledVersion}`;
  }
  if (!plugin.installed) return '未安装';
  if (!plugin.enabled) return `已安装 ${plugin.installedVersion} · 已停用`;
  if (plugin.updateAvailable) return `已安装 ${plugin.installedVersion} · 可更新到 ${plugin.version}`;
  return `已安装 ${plugin.installedVersion} · 已启用`;
}

function pluginChannelText(plugin) {
  if (plugin.channel === 'experimental') return '实验性 · 主动测试';
  if (plugin.channel === 'local') return '本地安装 · 未在官方库收录';
  return '官方稳定';
}

function normalizePluginSearchText(value) {
  return String(value || '').trim().toLocaleLowerCase('zh-Hans-CN');
}

function filteredPluginCatalog() {
  const query = normalizePluginSearchText(pluginCatalogState.query);
  if (!query) return pluginCatalogState.plugins;
  return pluginCatalogState.plugins.filter((plugin) => {
    const searchable = [
      plugin.id,
      plugin.name,
      plugin.description,
      plugin.publisher,
      plugin.channel === 'experimental' ? '实验 测试' : '稳定 官方',
      ...pluginPermissionTags(plugin)
    ].join(' ');
    return normalizePluginSearchText(searchable).includes(query);
  });
}

function pluginGridHtml() {
  const plugins = filteredPluginCatalog();
  const loading = pluginCatalogState.status === 'loading';
  if (loading) return '<div class="plugin-empty">正在读取插件库...</div>';
  if (plugins.length) return plugins.map(renderPluginCard).join('');
  return `<div class="plugin-empty">${pluginCatalogState.query ? '没有匹配的插件。可尝试平台名称、功能或发布者。' : '暂时没有可显示的插件。你仍可安装经过签名的本地插件包。'}</div>`;
}

function renderPluginCard(plugin) {
  const permissionTags = pluginPermissionTags(plugin);
  const compatible = plugin.compatibility?.compatible !== false;
  const primary = plugin.bundled && !plugin.installed && !plugin.updateAvailable
    ? '<span class="plugin-status">已随主程序提供</span>'
    : plugin.bundled && !plugin.installed
      ? `<button class="btn-primary" data-plugin-action="install" data-plugin-id="${escapeHtml(plugin.id)}" type="button" ${compatible ? '' : 'disabled'}>安装更新</button>`
      : !plugin.installed
    ? `<button class="btn-primary" data-plugin-action="install" data-plugin-id="${escapeHtml(plugin.id)}" type="button" ${compatible ? '' : 'disabled'}>安装</button>`
    : (plugin.updateAvailable
      ? `<button class="btn-primary" data-plugin-action="install" data-plugin-id="${escapeHtml(plugin.id)}" type="button">更新</button>`
      : `<button class="btn-secondary" data-plugin-action="toggle" data-plugin-id="${escapeHtml(plugin.id)}" data-enabled="${plugin.enabled ? 'false' : 'true'}" type="button">${plugin.enabled ? '停用' : '启用'}</button>`);
  return `
    <article class="plugin-card ${plugin.installed ? 'installed' : ''}">
      <div class="plugin-card-heading">
        <div>
          <span class="plugin-publisher">${escapeHtml(plugin.publisher || '社区开发者')} · ${escapeHtml(pluginChannelText(plugin))}</span>
          <h3>${escapeHtml(plugin.name || plugin.id)}</h3>
        </div>
        <span class="plugin-version">v${escapeHtml(plugin.version || plugin.installedVersion || '')}</span>
      </div>
      <p>${escapeHtml(plugin.description || '')}</p>
      <div class="plugin-permissions">
        ${permissionTags.map((item) => `<span>${escapeHtml(item)}</span>`).join('') || '<span>无需额外权限</span>'}
      </div>
      <div class="plugin-status ${compatible ? '' : 'incompatible'}">${escapeHtml(pluginStatusText(plugin))}</div>
      <div class="plugin-card-actions">
        ${primary}
        ${plugin.installed && (plugin.previousVersions || []).length ? `<button class="btn-text" data-plugin-action="rollback" data-plugin-id="${escapeHtml(plugin.id)}" type="button">回滚</button>` : ''}
        ${plugin.installed ? `<button class="btn-text danger-text" data-plugin-action="uninstall" data-plugin-id="${escapeHtml(plugin.id)}" type="button">卸载</button>` : ''}
      </div>
    </article>
  `;
}

async function loadPluginCatalog(refresh = false) {
  if (!window.electronAPI.getPluginCatalog) return;
  const requestId = ++pluginCatalogRequestId;
  pluginCatalogState = { ...pluginCatalogState, status: 'loading', error: '' };
  if (currentTool === 'plugin-center') renderPluginCenterPage();
  const result = await window.electronAPI.getPluginCatalog({ refresh });
  if (requestId !== pluginCatalogRequestId) return;
  pluginCatalogState = {
    status: result?.success ? 'ready' : 'error',
    plugins: Array.isArray(result?.plugins) ? result.plugins : [],
    query: pluginCatalogState.query,
    error: result?.registryError || result?.error || '',
    offline: Boolean(result?.offline),
    experimentalError: result?.experimentalError || '',
    updatedAt: result?.registryUpdatedAt || ''
  };
  if (currentTool === 'plugin-center') renderPluginCenterPage();
}

async function refreshProvidersAfterPluginChange() {
  await loadProviderManifests();
  renderProviderNavigation();
}

async function runPluginCenterAction(action, pluginId, button) {
  button.disabled = true;
  try {
    let result;
    const plugin = pluginCatalogState.plugins.find((item) => item.id === pluginId);
    if (action === 'install') {
      const permissions = pluginPermissionTags(plugin || {});
      const detail = permissions.length ? `\n\n将授予：${permissions.join('、')}` : '';
      if (!confirm(`${plugin?.installed ? '更新' : '安装'}插件“${plugin?.name || pluginId}”？${detail}`)) return;
      result = await window.electronAPI.installPlugin(pluginId, plugin?.channel || 'stable');
    } else if (action === 'toggle') {
      result = await window.electronAPI.setPluginEnabled(pluginId, button.dataset.enabled === 'true');
    } else if (action === 'rollback') {
      if (!confirm('回滚到上一个已安装版本？当前版本会保留，可再次切换。')) return;
      result = await window.electronAPI.rollbackPlugin(pluginId);
    } else if (action === 'uninstall') {
      if (!confirm(`卸载插件“${plugin?.name || pluginId}”？插件生成的导出文件不会删除。`)) return;
      result = await window.electronAPI.uninstallPlugin(pluginId);
    }
    if (!result?.success) throw new Error(result?.error || '插件操作失败');
    log(`插件操作完成：${plugin?.name || pluginId}`, 'success');
    await refreshProvidersAfterPluginChange();
    await loadPluginCatalog(true);
  } catch (error) {
    log(`插件操作失败：${formatError(error)}`, 'error');
    alert(formatError(error));
  } finally {
    button.disabled = false;
  }
}

function bindPluginCenterActions(root) {
  root.querySelector('[data-plugin-refresh]')?.addEventListener('click', () => loadPluginCatalog(true));
  root.querySelector('[data-plugin-local-install]')?.addEventListener('click', async (event) => {
    event.currentTarget.disabled = true;
    try {
      const result = await window.electronAPI.installPluginFile();
      if (result?.canceled) return;
      if (!result?.success) throw new Error(result?.error || '本地插件安装失败');
      await refreshProvidersAfterPluginChange();
      await loadPluginCatalog(true);
    } catch (error) {
      alert(formatError(error));
    } finally {
      event.currentTarget.disabled = false;
    }
  });
  root.querySelector('[data-plugin-search]')?.addEventListener('input', (event) => {
    pluginCatalogState = { ...pluginCatalogState, query: event.currentTarget.value };
    const grid = root.querySelector('[data-plugin-grid]');
    if (grid) {
      grid.innerHTML = pluginGridHtml();
      bindPluginCardActions(grid);
    }
  });
  root.querySelector('[data-plugin-search-clear]')?.addEventListener('click', () => {
    pluginCatalogState = { ...pluginCatalogState, query: '' };
    const input = root.querySelector('[data-plugin-search]');
    if (input) input.value = '';
    const grid = root.querySelector('[data-plugin-grid]');
    if (grid) {
      grid.innerHTML = pluginGridHtml();
      bindPluginCardActions(grid);
    }
  });
  bindPluginCardActions(root);
}

function bindPluginCardActions(root) {
  root.querySelectorAll('[data-plugin-action]').forEach((button) => {
    button.addEventListener('click', () => runPluginCenterAction(button.dataset.pluginAction, button.dataset.pluginId, button));
  });
}

function renderPluginCenterPage() {
  setTaskHistoryVisible(false);
  setToolHeading('插件中心', '按需安装平台能力，插件更新不需要重新安装万能导。');
  const contentArea = document.getElementById('content-area');
  const status = pluginCatalogState.offline
    ? `<div class="info-box plugin-offline"><strong>当前无法连接在线插件库</strong><p>${escapeHtml(pluginCatalogState.error || '仍可管理已安装插件，联网后点击刷新。')}</p></div>`
    : '';
  const experimental = pluginCatalogState.experimentalError
    ? `<div class="info-box plugin-offline"><strong>实验插件库暂时无法读取</strong><p>稳定插件不受影响。${escapeHtml(pluginCatalogState.experimentalError)}</p></div>`
    : '<div class="info-box plugin-experimental-notice"><strong>实验性插件已标注</strong><p>它们会正常显示和搜索，但可能功能不完整或存在兼容性限制。</p></div>';
  contentArea.innerHTML = `
    <section class="view-panel plugin-center-hero">
      <div class="view-panel-header">
        <div>
          <p class="view-kicker">按需扩展</p>
          <h3>只安装你需要的平台</h3>
          <p>插件包会校验官方签名和文件完整性；稳定与实验插件都会显示，并以标签说明成熟度。</p>
        </div>
        <div class="plugin-toolbar">
          <button class="btn-secondary" data-plugin-local-install type="button">安装本地插件</button>
          <button class="btn-primary" data-plugin-refresh type="button">刷新插件库</button>
        </div>
      </div>
      <div class="plugin-search-row">
        <label class="sr-only" for="plugin-search">搜索插件</label>
        <input id="plugin-search" data-plugin-search type="search" value="${escapeHtml(pluginCatalogState.query)}" placeholder="搜索平台、功能、发布者或权限" autocomplete="off">
        ${pluginCatalogState.query ? '<button class="btn-text" data-plugin-search-clear type="button">清除</button>' : ''}
      </div>
    </section>
    ${status}
    ${experimental}
    <section class="plugin-grid" data-plugin-grid>
      ${pluginGridHtml()}
    </section>
  `;
  bindPluginCenterActions(contentArea);
  if (pluginCatalogState.status === 'idle') loadPluginCatalog(false);
}

function normalizeActionHierarchy(root = document.getElementById('content-area')) {
  if (!root) return;
  root.querySelectorAll('.action-section .btn-primary').forEach((button) => {
    const label = String(button.textContent || '').trim();
    const isPrimaryAction = /^(开始|批量)(导出|导入)/.test(label)
      || /^(导出|导入)全部/.test(label)
      || /^(开始处理|执行导出|执行导入)$/.test(label);
    if (!isPrimaryAction) {
      button.classList.remove('btn-primary');
      button.classList.add('btn-secondary');
    }
  });
}

function renderAppView(viewId) {
  if (viewId === 'platform-center') {
    renderPlatformCenterPage();
  } else if (viewId === 'task-center') {
    renderTaskCenterPage();
  } else if (viewId === 'notice-center') {
    renderNoticeCenterPage();
  } else if (viewId === 'plugin-center') {
    renderPluginCenterPage();
  } else if (viewId === 'settings') {
    renderSettingsPage();
  } else {
    renderHomePage();
  }
}

function markdownInline(value) {
  let text = escapeHtml(value);
  text = text.replace(/`([^`]+)`/g, '<code>$1</code>');
  text = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2" data-external-link="true">$1</a>');
  return text;
}

function markdownToHtml(markdown) {
  const lines = String(markdown || '').replace(/\r\n/g, '\n').split('\n');
  const html = [];
  let inCode = false;
  let codeLines = [];
  let inList = false;

  const closeList = () => {
    if (inList) {
      html.push('</ul>');
      inList = false;
    }
  };

  lines.forEach((line) => {
    if (line.trim().startsWith('```')) {
      if (inCode) {
        html.push(`<pre><code>${escapeHtml(codeLines.join('\n'))}</code></pre>`);
        codeLines = [];
        inCode = false;
      } else {
        closeList();
        inCode = true;
      }
      return;
    }
    if (inCode) {
      codeLines.push(line);
      return;
    }
    const trimmed = line.trim();
    if (!trimmed) {
      closeList();
      return;
    }
    const heading = trimmed.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      closeList();
      const level = Math.min(4, heading[1].length + 1);
      html.push(`<h${level}>${markdownInline(heading[2])}</h${level}>`);
      return;
    }
    const bullet = trimmed.match(/^[-*]\s+(.+)$/);
    if (bullet) {
      if (!inList) {
        html.push('<ul>');
        inList = true;
      }
      html.push(`<li>${markdownInline(bullet[1])}</li>`);
      return;
    }
    closeList();
    html.push(`<p>${markdownInline(trimmed)}</p>`);
  });
  closeList();
  if (inCode) {
    html.push(`<pre><code>${escapeHtml(codeLines.join('\n'))}</code></pre>`);
  }
  return html.join('\n');
}

function valueAtPath(source, pathExpression) {
  if (!pathExpression) return source;
  return String(pathExpression)
    .split('.')
    .filter(Boolean)
    .reduce((value, key) => {
      if (value === null || value === undefined) return undefined;
      if (Array.isArray(value) && /^\d+$/.test(key)) return value[Number(key)];
      return value[key];
    }, source);
}

function asArray(value) {
  if (!value) return [];
  return Array.isArray(value) ? value : [value];
}

function renderRequirements(provider) {
  const requirements = provider.requirements || {};
  const python = asArray(requirements.python);
  const system = asArray(requirements.system);
  const notes = asArray(requirements.notes);
  if (!python.length && !system.length && !notes.length) return '';
  const list = [
    ...python.map((item) => `Python: ${item}`),
    ...system.map((item) => `系统: ${item}`),
    ...notes
  ];
  return `
    <div class="requirements-card">
      <strong>运行依赖</strong>
      <p>这个 provider 声明了额外依赖。正式执行前请确认本机环境已满足；万能导不会自动安装社区插件依赖。</p>
      <ul>${list.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul>
    </div>
  `;
}

function renderTrustBadge(provider) {
  const label = window.WandaoProviderRuntime?.providerTypeLabel(provider) || 'Provider';
  const trustClass = window.WandaoProviderRuntime?.providerTrustClass(provider) || 'community';
  return `<span class="trust-badge ${trustClass}">${escapeHtml(label)}</span>`;
}

function renderGuideProvider(provider) {
  const contentArea = document.getElementById('content-area');
  const capabilityItems = [
    provider.capabilities?.export ? '支持导出' : '',
    provider.capabilities?.import ? '支持导入' : '',
    provider.capabilities?.images ? '支持图片' : '',
    provider.capabilities?.tree ? '支持目录结构' : '',
    provider.capabilities?.batch ? '支持批量' : ''
  ].filter(Boolean);
  const guide = provider.guideMarkdown || '# 暂无教程\n\n这个 provider 还没有提供 README.md。';
  contentArea.innerHTML = `
    <div class="guide-panel">
      <section class="provider-overview-card">
        <div>
          <div class="provider-kicker">${renderTrustBadge(provider)}</div>
          <h3>${escapeHtml(provider.title || provider.name || provider.id)}</h3>
          <p>${escapeHtml(provider.description || '')}</p>
          <div class="provider-tags">
            ${(provider.tags || []).map((tag) => `<span>${escapeHtml(tag)}</span>`).join('')}
            ${capabilityItems.map((item) => `<span>${escapeHtml(item)}</span>`).join('')}
          </div>
        </div>
        <div class="provider-actions-mini">
          ${provider.homepage ? `<button class="btn-secondary" data-open-url="${escapeHtml(provider.homepage)}" type="button">打开平台官网</button>` : ''}
          ${provider.docs ? `<button class="btn-secondary" data-open-url="${escapeHtml(provider.docs)}" type="button">查看官方文档</button>` : ''}
        </div>
      </section>
      ${renderRequirements(provider)}
      <section class="guide-content">
        ${markdownToHtml(guide)}
      </section>
    </div>
  `;
  contentArea.querySelectorAll('[data-open-url]').forEach((button) => {
    button.addEventListener('click', () => {
      window.electronAPI.openExternal(button.dataset.openUrl);
    });
  });
  contentArea.querySelectorAll('[data-external-link]').forEach((link) => {
    link.addEventListener('click', (event) => {
      event.preventDefault();
      window.electronAPI.openExternal(link.href);
    });
  });
}

function manifestFieldId(provider, field) {
  return `${provider.id}-field-${field.name}`;
}

function renderManifestField(provider, field) {
  const id = manifestFieldId(provider, field);
  const label = escapeHtml(field.label || field.name);
  const required = field.required ? ' <span class="required">*</span>' : '';
  const placeholder = escapeHtml(field.placeholder || '');
  const value = escapeHtml(field.default ?? '');
  if (field.type === 'notice') {
    return `<div class="info-box">${markdownToHtml(field.markdown || field.text || '')}</div>`;
  }
  if (field.type === 'textarea') {
    return `
      <div class="form-group">
        <label for="${id}">${label}${required}</label>
        <textarea id="${id}" placeholder="${placeholder}" rows="${field.rows || 6}">${value}</textarea>
      </div>
    `;
  }
  if (field.type === 'checkbox') {
    return `
      <label class="checkbox-label">
        <input type="checkbox" id="${id}" ${field.default ? 'checked' : ''}>
        <span>${label}</span>
      </label>
    `;
  }
  if (field.type === 'select') {
    const options = (field.options || []).map((option) => {
      const optionValue = typeof option === 'string' ? option : option.value;
      const optionLabel = typeof option === 'string' ? option : option.label;
      return `<option value="${escapeHtml(optionValue)}" ${optionValue === field.default ? 'selected' : ''}>${escapeHtml(optionLabel)}</option>`;
    }).join('');
    return `
      <div class="form-group">
        <label for="${id}">${label}${required}</label>
        <select id="${id}">${options}</select>
      </div>
    `;
  }
  if (field.type === 'directory' || field.type === 'file') {
    const buttonLabel = field.type === 'directory' ? '选择目录' : '选择文件';
    return `
      <div class="form-group">
        <label for="${id}">${label}${required}</label>
        <div class="input-with-button">
          <input type="text" id="${id}" placeholder="${placeholder}" value="${value}">
          <button class="btn-secondary" id="${id}-browse" type="button">${buttonLabel}</button>
        </div>
      </div>
    `;
  }
  const inputType = field.type === 'password' ? 'password' : (field.type === 'number' ? 'number' : 'text');
  return `
    <div class="form-group">
      <label for="${id}">${label}${required}</label>
      <input type="${inputType}" id="${id}" placeholder="${placeholder}" value="${value}" ${field.step ? `step="${escapeHtml(field.step)}"` : ''} ${field.min !== undefined ? `min="${escapeHtml(field.min)}"` : ''}>
    </div>
  `;
}

function renderManifestProviderForm(provider) {
  const contentArea = document.getElementById('content-area');
  const fields = Array.isArray(provider.fields) ? provider.fields : [];
  const primaryFields = fields.filter((field) => !field.advanced);
  const advancedFields = fields.filter((field) => field.advanced);
  const actions = Array.isArray(provider.actions) && provider.actions.length
    ? provider.actions
    : [{ id: 'run', label: provider.isImport ? '开始导入' : '开始导出', script: provider.script }];
  const guideHtml = provider.guideMarkdown ? `
    <details class="advanced-section plugin-guide-section">
      <summary>平台说明 / 操作教程</summary>
      <div class="guide-content compact">${markdownToHtml(provider.guideMarkdown)}</div>
    </details>
  ` : '';
  contentArea.innerHTML = `
    <div class="tool-panel manifest-tool-panel">
      <section class="form-section">
        <div class="provider-mini-header">
          ${renderTrustBadge(provider)}
          <strong>${escapeHtml(provider.name || provider.platform || provider.id)}</strong>
        </div>
        ${renderProviderSafetyNotice(provider)}
        ${renderRequirements(provider)}
        ${primaryFields.map((field) => renderManifestField(provider, field)).join('')}
        ${advancedFields.length ? `
          <details class="advanced-section">
            <summary>高级参数</summary>
            <div class="advanced-content">
              ${advancedFields.map((field) => renderManifestField(provider, field)).join('')}
            </div>
          </details>
        ` : ''}
        ${guideHtml}
      </section>
      ${provider.capabilities?.scanToc ? renderTocShell(provider.id, provider.toc?.note || '读取目录后，后续动作会自动带上已勾选的文档 ID。') : ''}
      <section class="action-section">
        ${actions.map((action) => `
          <button class="${action.danger ? 'btn-danger' : (action.secondary ? 'btn-secondary' : 'btn-primary')}" data-manifest-action="${escapeHtml(action.id || action.label)}" type="button">
            ${escapeHtml(action.label || action.id || '执行')}
          </button>
        `).join('')}
        ${actions.some((action) => action.kind === 'login') ? `<button class="btn-secondary" id="${provider.id}-login-done" type="button" hidden disabled>我已完成登录，保存凭证</button>` : ''}
        <button class="btn-danger" id="${provider.id}-stop" disabled>停止</button>
      </section>
    </div>
  `;
  initializeManifestProviderHandlers(provider, actions, fields);
}

function manifestFieldValue(provider, field) {
  const element = document.getElementById(manifestFieldId(provider, field));
  if (!element) return '';
  if (field.type === 'checkbox') return Boolean(element.checked);
  return String(element.value || '').trim();
}

function manifestActionKey(action) {
  return String(action.id || action.kind || action.label || '').trim();
}

function manifestFieldActionList(value) {
  return asArray(value).map((item) => String(item || '').trim()).filter(Boolean);
}

function isManifestOutputField(field) {
  const name = String(field.name || '').toLowerCase();
  const arg = String(field.arg || '').toLowerCase();
  return arg === '--output' || name === 'output' || name === 'output_dir' || name === 'output-dir';
}

function manifestActionUsesOutput(action) {
  const key = manifestActionKey(action).toLowerCase();
  const kind = String(action.kind || '').toLowerCase();
  return ['export', 'import', 'run'].includes(kind) || ['export', 'import', 'run', 'start'].includes(key);
}

function manifestFieldAppliesToAction(field, action) {
  const key = manifestActionKey(action);
  const kind = String(action.kind || '').trim();
  const include = manifestFieldActionList(field.actions || field.includeActions || field.onlyActions);
  if (include.length && !include.includes(key) && !include.includes(kind)) return false;
  const exclude = manifestFieldActionList(field.excludeActions || field.skipActions);
  if (exclude.includes(key) || exclude.includes(kind)) return false;
  if (isManifestOutputField(field) && !manifestActionUsesOutput(action)) return false;
  return true;
}

function buildManifestActionArgs(provider, action, fields) {
  const args = [...(action.args || [])];
  for (const field of fields) {
    if (field.type === 'notice') continue;
    if (!manifestFieldAppliesToAction(field, action)) continue;
    const value = manifestFieldValue(provider, field);
    if (field.required && (value === '' || value === false)) {
      throw new Error(`请填写：${field.label || field.name}`);
    }
    if (field.type === 'checkbox') {
      if (value && field.arg) {
        args.push(field.arg);
        if (field.checkedValue !== undefined) args.push(String(field.checkedValue));
      } else if (!value && field.falseArg) {
        args.push(field.falseArg);
      }
      continue;
    }
    if (value === '') continue;
    if (field.arg) {
      args.push(field.arg, value);
    } else if (field.positional) {
      args.push(value);
    }
  }
  const isScanAction = action.kind === 'scan' || action.scanToc || action.id === 'scan';
  if (!isScanAction && action.includeSelection !== false && provider.capabilities?.scanToc && tocStates[provider.id]?.loaded) {
    args.push(...selectedTocArgs(provider.id));
  }
  const actionKind = String(action.kind || action.id || '').toLowerCase();
  if (provider.checkpoint?.supported && ['export', 'import', 'run'].includes(actionKind) && !args.includes('--checkpoint-file')) {
    const outputField = fields.find(isManifestOutputField);
    const output = outputField ? manifestFieldValue(provider, outputField) : '';
    const checkpointFile = providerCheckpointFile(provider.id, output);
    if (checkpointFile) args.push('--checkpoint-file', checkpointFile, '--resume');
  }
  return args;
}

function applyActionUpdates(provider, action, data) {
  const updates = Array.isArray(action.updates) ? action.updates : [];
  updates.forEach((update) => {
    const fieldName = update.field || update.name;
    if (!fieldName) return;
    const target = document.getElementById(manifestFieldId(provider, { name: fieldName }));
    if (!target) return;
    const value = valueAtPath(data, update.path);
    if (update.type === 'options' || target.tagName === 'SELECT') {
      const items = Array.isArray(value) ? value : [];
      const placeholder = update.placeholder ? `<option value="">${escapeHtml(update.placeholder)}</option>` : '';
      const options = items.map((item) => {
        const optionValue = typeof item === 'object' ? valueAtPath(item, update.valueKey || 'id') : item;
        const optionLabel = typeof item === 'object' ? valueAtPath(item, update.labelKey || 'name') : item;
        return `<option value="${escapeHtml(optionValue ?? '')}">${escapeHtml(optionLabel ?? optionValue ?? '')}</option>`;
      }).join('');
      target.innerHTML = placeholder + options;
      return;
    }
    if (target.type === 'checkbox') {
      target.checked = Boolean(value);
    } else if (value !== undefined && value !== null) {
      target.value = String(value);
    }
  });
}

function initializeManifestProviderHandlers(provider, actions, fields) {
  if (provider.capabilities?.scanToc) {
    if (!tocStates[provider.id]) {
      tocStates[provider.id] = { loaded: false, nodes: [], selected: new Set() };
    }
    initializeTocInteraction(provider.id);
  }
  fields.forEach((field) => {
    const id = manifestFieldId(provider, field);
    const input = document.getElementById(id);
    if (input && isManifestOutputField(field) && !input.value && provider.defaults?.output) {
      const root = appPaths?.dataRoot || appPaths?.userData || appPaths?.projectRoot;
      if (root) input.value = `${root}/${provider.defaults.output}`;
    }
    const browse = document.getElementById(`${id}-browse`);
    if (!browse) return;
    browse.addEventListener('click', async () => {
      const current = document.getElementById(id)?.value || '';
      if (field.type === 'directory') {
        const dir = await window.electronAPI.selectDirectory({ title: field.dialogTitle || field.label || '选择目录', defaultPath: current });
        if (dir) document.getElementById(id).value = dir;
      } else {
        const file = await window.electronAPI.selectFile({ title: field.dialogTitle || field.label || '选择文件', filters: field.filters || [] });
        if (file) document.getElementById(id).value = file;
      }
    });
  });
  const loginDoneButton = document.getElementById(`${provider.id}-login-done`);
  loginDoneButton?.addEventListener('click', async () => {
    loginDoneButton.disabled = true;
    loginDoneButton.textContent = '正在保存凭证...';
    const result = await window.electronAPI.sendPythonInput('\n');
    if (!result?.success) {
      loginDoneButton.disabled = false;
      loginDoneButton.textContent = '我已完成登录，保存凭证';
      alert(result?.error || '当前登录任务没有等待确认');
    }
  });
  actions.forEach((action) => {
    const button = document.querySelector(`[data-manifest-action="${CSS.escape(action.id || action.label)}"]`);
    if (!button) return;
    button.addEventListener('click', async () => {
      if (action.confirm && !confirm(action.confirm)) return;
      if (action.openUrl) {
        await window.electronAPI.openExternal(action.openUrl);
        return;
      }
      const script = action.script || provider.script;
      if (!script) {
        alert('这个动作没有配置脚本，可能只是教程型 provider。');
        return;
      }
      if (!confirmProviderExecution(provider, action)) return;
      let args;
      try {
        args = buildManifestActionArgs(provider, action, fields);
      } catch (error) {
        alert(formatError(error));
        return;
      }
      setRunning(true, provider.id);
      if (action.kind === 'login' && loginDoneButton) {
        loginDoneButton.hidden = false;
        loginDoneButton.disabled = false;
        loginDoneButton.textContent = '我已完成登录，保存凭证';
      }
      startProgress(action.progressTitle || action.label || provider.title, action.progressDetail || '正在执行 provider 动作...');
      log(`开始：${action.label || provider.title}`, 'info');
      try {
        const result = await runTrackedPythonCommand(script, args, {
          providerId: provider.id,
          title: action.label || provider.title,
          action: action.actionName || action.label || '执行',
          track: action.track !== false
        });
        if (result.success) {
          log(`完成：${action.label || provider.title}`, 'success');
          if (result.data) log(JSON.stringify(result.data, null, 2), 'success');
          applyActionUpdates(provider, action, result.data || {});
          if (action.kind === 'scan' || action.scanToc || action.id === 'scan') {
            const nodes = normalizeTocNodes(provider.id, result.data || {});
            tocStates[provider.id] = {
              loaded: true,
              nodes,
              selected: new Set(selectableTocIds(nodes))
            };
            renderToc(provider.id);
            log(`目录读取完成：共 ${selectableTocIds(nodes).length} 篇，默认已全选。`, 'success');
            finishProgress(true, `目录读取完成，共 ${selectableTocIds(nodes).length} 篇`);
          } else {
            finishProgress(true, `${action.label || '任务'}完成`);
          }
        } else {
          log(`失败：${result.error}`, 'error');
          finishProgress(false, `${action.label || '任务'}失败，请查看运行日志`);
        }
      } catch (error) {
        log(`错误：${formatError(error)}`, 'error');
        finishProgress(false, `${action.label || '任务'}出错，请查看运行日志`);
      } finally {
        if (action.kind === 'login' && loginDoneButton) {
          loginDoneButton.hidden = true;
          loginDoneButton.disabled = true;
        }
        setRunning(false, provider.id);
      }
    });
  });
  document.getElementById(`${provider.id}-stop`)?.addEventListener('click', handleStop);
}

function sandboxPluginHtml(html) {
  const policy = "default-src 'none'; img-src data:; media-src data:; font-src data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; form-action 'none'; base-uri 'none';";
  const meta = `<meta http-equiv="Content-Security-Policy" content="${policy}">`;
  const source = String(html || '');
  if (/<head(?:\s[^>]*)?>/i.test(source)) {
    return source.replace(/<head(?:\s[^>]*)?>/i, (match) => `${match}${meta}`);
  }
  return `<!doctype html><html><head>${meta}</head><body>${source}</body></html>`;
}

async function executeCustomPluginAction(provider, actionId, rawArgs) {
  const action = (provider.actions || []).find((item) => item.id === actionId);
  if (!action || !(action.script || provider.script)) throw new Error('自定义 UI 请求了未声明的动作');
  const args = Array.isArray(rawArgs) ? rawArgs.map(String) : [];
  const totalLength = args.reduce((sum, item) => sum + item.length, 0);
  if (args.length > 500 || args.some((item) => item.length > 16000) || totalLength > 128000) {
    throw new Error('自定义 UI 提交的参数超过安全限制');
  }
  if (!confirmProviderExecution(provider, action)) throw new Error('用户取消执行');
  setRunning(true, provider.id);
  startProgress(action.progressTitle || action.label || provider.title, action.progressDetail || '正在执行插件动作...');
  try {
    const result = await runTrackedPythonCommand(action.script || provider.script, [...(action.args || []), ...args], {
      providerId: provider.id,
      title: action.label || provider.title,
      action: action.actionName || action.label || '执行',
      track: action.track !== false
    });
    finishProgress(Boolean(result?.success), result?.success ? '插件动作完成' : '插件动作失败');
    return result;
  } finally {
    setRunning(false, provider.id);
  }
}

async function renderCustomPluginProvider(provider) {
  const contentArea = document.getElementById('content-area');
  contentArea.innerHTML = '<div class="plugin-empty">正在加载插件界面...</div>';
  const result = await window.electronAPI.getPluginUi(provider.pluginId, provider.ui.entry);
  if (currentTool !== provider.id) return;
  if (!result?.success) {
    contentArea.innerHTML = `<div class="info-box"><strong>插件界面加载失败</strong><p>${escapeHtml(result?.error || '未知错误')}</p></div>`;
    return;
  }
  contentArea.innerHTML = `
    <section class="custom-plugin-shell">
      <div class="custom-plugin-banner">
        <div>${renderTrustBadge(provider)}<strong>${escapeHtml(provider.title || provider.name)}</strong></div>
        <span>沙箱界面 · 无 Node 权限 · 默认断网</span>
      </div>
      <iframe class="custom-plugin-frame" title="${escapeHtml(provider.title || provider.name)}" sandbox="allow-scripts" referrerpolicy="no-referrer"></iframe>
    </section>
  `;
  const frame = contentArea.querySelector('.custom-plugin-frame');
  frame.srcdoc = sandboxPluginHtml(result.html);
  const listener = async (event) => {
    if (event.source !== frame.contentWindow || event.data?.source !== 'wandao-plugin') return;
    const { requestId, method, payload = {} } = event.data;
    try {
      let response;
      if (method === 'selectDirectory') {
        response = await window.electronAPI.selectDirectory({ title: String(payload.title || '选择目录'), defaultPath: String(payload.defaultPath || '') });
      } else if (method === 'selectFile') {
        response = await window.electronAPI.selectFile({ title: String(payload.title || '选择文件'), filters: Array.isArray(payload.filters) ? payload.filters : [] });
      } else if (method === 'openExternal') {
        if (!/^https:\/\//i.test(String(payload.url || ''))) throw new Error('插件界面只允许打开 HTTPS 链接');
        response = await window.electronAPI.openExternal(payload.url);
      } else if (method === 'runAction') {
        response = await executeCustomPluginAction(provider, String(payload.actionId || ''), payload.args);
      } else {
        throw new Error(`不支持的插件界面方法：${method}`);
      }
      frame.contentWindow?.postMessage({ source: 'wandao-host', requestId, success: true, data: response }, '*');
    } catch (error) {
      frame.contentWindow?.postMessage({ source: 'wandao-host', requestId, success: false, error: formatError(error) }, '*');
    }
  };
  window.addEventListener('message', listener);
  customPluginMessageCleanup = () => window.removeEventListener('message', listener);
}

function renderGenericProviderForm(provider) {
  if ((provider.fields && provider.fields.length) || (provider.actions && provider.actions.length)) {
    renderManifestProviderForm(provider);
    return;
  }
  const contentArea = document.getElementById('content-area');
  const actionName = provider.isImport ? '导入' : '导出';
  const sourceLabel = provider.isImport ? '本地目录' : '输出目录';
  const sourcePlaceholder = provider.isImport ? '选择要导入的本地 Markdown 目录' : '留空使用默认输出目录';
  const delayDefault = provider.defaults?.delay ?? '1.0';
  const jitterDefault = provider.defaults?.jitter ?? '0.5';
  const urlField = provider.noUrl ? '' : `
    <div class="form-group">
      <label for="${provider.id}-url">入口 URL <span class="required">*</span></label>
      <input type="text" id="${provider.id}-url" placeholder="粘贴目标平台页面 URL">
    </div>
  `;
  const loginButton = provider.capabilities?.login ? `
        <button class="btn-secondary" id="${provider.id}-login">登录并保存凭证</button>
        <button class="btn-secondary" id="${provider.id}-login-done" hidden disabled>我已完成登录，保存凭证</button>
  ` : '';
  contentArea.innerHTML = `
    <div class="tool-panel">
      <section class="form-section">
        ${renderProviderSafetyNotice(provider)}
        ${urlField}
        <div class="form-group">
          <label for="${provider.id}-output">${sourceLabel}</label>
          <div class="input-with-button">
            <input type="text" id="${provider.id}-output" placeholder="${sourcePlaceholder}">
            <button class="btn-secondary" id="${provider.id}-browse-output">浏览</button>
          </div>
        </div>
        <details class="advanced-section">
          <summary>${actionName}选项</summary>
          <div class="advanced-content">
            <div class="form-row">
              <div class="form-group flex-1">
                <label for="${provider.id}-delay">请求延迟秒</label>
                <input type="number" id="${provider.id}-delay" value="${delayDefault}" min="0" step="0.1">
              </div>
              <div class="form-group flex-1">
                <label for="${provider.id}-jitter">随机浮动秒</label>
                <input type="number" id="${provider.id}-jitter" value="${jitterDefault}" min="0" step="0.1">
              </div>
            </div>
            <label class="checkbox-label">
              <input type="checkbox" id="${provider.id}-incremental" checked>
              <span>增量${actionName}</span>
            </label>
          </div>
        </details>
      </section>
      <section class="action-section">
        ${loginButton}
        <button class="btn-primary" id="${provider.id}-export">开始${actionName}</button>
        <button class="btn-danger" id="${provider.id}-stop" disabled>停止</button>
        <button class="btn-secondary" id="${provider.id}-open-dir">打开目录</button>
      </section>
    </div>
  `;
  initializeToolHandlers(provider.id);
}

// Tool switching
function switchTool(toolId) {
  if (customPluginMessageCleanup) {
    customPluginMessageCleanup();
    customPluginMessageCleanup = null;
  }
  refreshProviderTools();
  currentTool = toolId || DEFAULT_VIEW_ID;
  renderProviderNavigation();

  if (String(currentTool).startsWith('platform:')) {
    renderPlatformDetailPage(String(currentTool).slice('platform:'.length));
    return;
  }

  if (PRIMARY_NAV_ITEMS.some((item) => item.id === currentTool)) {
    renderAppView(currentTool);
    return;
  }

  const config = TOOLS[currentTool];
  if (!config) {
    log(`未找到平台 provider：${currentTool}`, 'error');
    switchTool(DEFAULT_VIEW_ID);
    return;
  }

  setTaskHistoryVisible(false);
  setToolHeading(config.title, config.description);

  // Load tool template
  const contentArea = document.getElementById('content-area');
  const template = document.getElementById(config.templateId || `template-${currentTool}`);

  if (config.sourceKind === 'plugin' && config.ui?.mode === 'custom') {
    renderCustomPluginProvider(config);
  } else if (config.type === 'guide' || (!config.script && !template && !(config.actions || []).length)) {
    renderGuideProvider(config);
  } else if (template) {
    contentArea.innerHTML = '';
    const clone = template.content.cloneNode(true);
    contentArea.appendChild(clone);
    initializeToolHandlers(currentTool);
  } else if (currentTool === 'feishu-import' && config.sourceKind !== 'plugin') {
    loadFeishuImportTool();
  } else {
    renderGenericProviderForm(config);
  }
  renderProviderModeSwitcher(config);
  normalizeActionHierarchy(contentArea);
}

// Initialize tool event handlers
function initializeToolHandlers(toolId) {
  const prefix = toolId;
  ensureTocSelector(toolId);

  const outputInput = document.getElementById(`${prefix}-output`);
  if (outputInput && !outputInput.value.trim()) {
    const suffix = TOOLS[toolId]?.defaults?.output;
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

  if (toolId === 'ima-import') {
    initializeImaImportHandlers();
    return;
  }

  if (toolId === 'ima-export') {
    initializeImaExportHandlers();
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

  const url = document.getElementById(`${prefix}-url`)?.value.trim() || '';
  if (!config.noUrl && !url) {
    alert('请先填写 URL');
    return;
  }
  try {
    validateZsxqUrlForTool(toolId, url);
  } catch (error) {
    alert(formatError(error));
    return;
  }

  const args = config.noUrl ? ['--login'] : [config.urlParam, url, '--login'];
  if (!confirmProviderExecution(config)) return;

  setRunning(true, toolId);
  startProgress(`登录：${config.title}`, '请在浏览器中完成登录，然后回到工具点击“我已完成登录，保存凭证”。');
  setLoginDoneButton(toolId, true);
  log(`开始登录：${config.title}`, 'info');
  log('请在浏览器中完成登录，登录成功并能看到目标页面后，回到工具点击“我已完成登录，保存凭证”。', 'info');

  try {
    const result = await window.electronAPI.runPythonCommand(config.script, args, {
      providerId: toolId
    });
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

function compactDiagnostic(value, maxLength = 420) {
  const text = normalizeLogMessage(value)
    .replace(/\s+/g, ' ')
    .trim();
  if (!text) return '';
  return text.length > maxLength ? `${text.slice(0, maxLength)}...` : text;
}

function firstNonEmpty(...values) {
  for (const value of values) {
    const text = compactDiagnostic(value, 260);
    if (text) return text;
  }
  return '';
}

function describeFailureItem(item, parent = '') {
  return window.WandaoTaskReport?.describeFailureItem(item, parent) || compactDiagnostic(item);
}

function collectFailureDiagnostics(data, limit = 80) {
  return window.WandaoTaskReport?.collectFailureDiagnostics(data, limit) || [];
}

function recordPythonResultDiagnostics(script, result) {
  if (result?.code === 130) return;
  const data = result?.data;
  const lines = collectFailureDiagnostics(data);
  const source = script ? `diagnostic:${script}` : 'diagnostic';
  if (result && !result.success && result.error) {
    appendDetailedLog(source, 'error', `脚本执行失败：${compactDiagnostic(result.error, 1200)}`);
  }
  if (!lines.length) return;
  appendDetailedLog(source, 'error', [
    '脚本返回失败详情摘要：',
    ...lines.map((line) => `- ${line}`)
  ].join('\n'));
  appendUserLog('详细失败原因已写入“详细日志”和任务报告，可点击“提交错误报告给开发者”复制。', 'error');
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function imaConfigPath() {
  if (appPaths?.userData) {
    return `${appPaths.userData}/ima_config.json`;
  }
  if (appPaths?.projectRoot) {
    return `${appPaths.projectRoot}/.ima_config.json`;
  }
  return '';
}

async function loadImaConfigIntoForm(prefix) {
  const configPath = imaConfigPath();
  const config = await readJsonFileIfExists(configPath);
  if (!config || typeof config !== 'object') return;
  setInputValueIfEmpty(`${prefix}-client-id`, config.client_id);
  setInputValueIfEmpty(`${prefix}-api-key`, config.api_key);
  setInputValueIfEmpty(`${prefix}-kb-id`, config.knowledge_base_id);
  if (prefix === 'ima-import' && config.knowledge_base_id) {
    const select = document.getElementById('ima-import-kb-select');
    if (select && !select.value) {
      const label = config.knowledge_base_name || config.knowledge_base_id;
      select.innerHTML = `<option value="${escapeHtml(config.knowledge_base_id)}">${escapeHtml(label)}</option>`;
    }
    const folderSelect = document.getElementById('ima-import-folder-id');
    if (folderSelect && config.folder_id) {
      folderSelect.innerHTML = [
        '<option value="">知识库根目录</option>',
        `<option value="${escapeHtml(config.folder_id)}">${escapeHtml(config.folder_name || config.folder_id)}</option>`
      ].join('');
      folderSelect.value = config.folder_id;
    }
  } else {
    setInputValueIfEmpty(`${prefix}-folder-id`, config.folder_id);
  }
  log('已读取本机 ima API 配置', 'info');
}

function buildImaCredentialArgs(prefix) {
  const args = [];
  const configPath = imaConfigPath();
  const clientId = document.getElementById(`${prefix}-client-id`)?.value.trim();
  const apiKey = document.getElementById(`${prefix}-api-key`)?.value.trim();
  if (configPath) args.push('--config-file', configPath);
  if (clientId) args.push('--client-id', clientId);
  if (apiKey) args.push('--api-key', apiKey);
  return args;
}

function requireImaCredentials(prefix) {
  const clientId = document.getElementById(`${prefix}-client-id`)?.value.trim();
  const apiKey = document.getElementById(`${prefix}-api-key`)?.value.trim();
  if (!clientId || !apiKey) {
    throw new Error('请先填写 ima Client ID 和 API Key，或保存过本机配置后再操作。');
  }
}

function buildImaExportArgs(options = {}) {
  const prefix = 'ima-export';
  const forScan = Boolean(options.forScan);
  const includeSelection = options.includeSelection !== false;
  requireImaCredentials(prefix);
  const args = buildImaCredentialArgs(prefix);
  const kbId = document.getElementById('ima-export-kb-id')?.value.trim();
  const output = document.getElementById('ima-export-output')?.value.trim();
  if (kbId) args.push('--knowledge-base-id', kbId);
  if (forScan) {
    args.push('--scan-toc');
  } else {
    if (output) args.push('--output', output);
    args.push('--progress-every', '1');
    if (includeSelection) args.push(...selectedTocArgs(prefix));
  }
  const delay = document.getElementById('ima-export-delay')?.value;
  const jitter = document.getElementById('ima-export-jitter')?.value;
  if (delay) args.push('--request-delay', delay);
  if (jitter) args.push('--request-jitter', jitter);
  return args;
}

async function saveImaConfig(prefix) {
  try {
    requireImaCredentials(prefix);
  } catch (error) {
    alert(formatError(error));
    return;
  }
  const args = buildImaCredentialArgs(prefix);
  const kbId = document.getElementById(`${prefix}-kb-id`)?.value.trim()
    || document.getElementById('ima-import-kb-select')?.value.trim()
    || '';
  const folderId = document.getElementById(`${prefix}-folder-id`)?.value.trim() || '';
  if (kbId) args.push('--knowledge-base-id', kbId);
  if (folderId) args.push('--folder-id', folderId);
  args.push('--save-config');
  const title = '保存 ima API 配置';
  setRunning(true, prefix);
  startProgress(title, '正在保存本机配置...');
  log(`开始：${title}`, 'info');
  try {
    const provider = TOOLS[prefix];
    if (!provider?.script) throw new Error(`ima Provider 未提供脚本：${prefix}`);
    const result = await window.electronAPI.runPythonCommand(provider.script, args, { providerId: prefix });
    if (result.success) {
      log(`${title}完成`, 'success');
      finishProgress(true, `${title}完成`);
    } else {
      log(`${title}失败：${result.error}`, 'error');
      finishProgress(false, `${title}失败，请查看运行日志`);
    }
  } catch (error) {
    log(`错误：${formatError(error)}`, 'error');
    finishProgress(false, `${title}出错，请查看运行日志`);
  } finally {
    setRunning(false, prefix);
  }
}

function initializeImaExportHandlers() {
  loadImaConfigIntoForm('ima-export').catch((error) => {
    log(`读取 ima 配置失败：${formatError(error)}`, 'error');
  });
  document.getElementById('ima-export-save-config')?.addEventListener('click', () => saveImaConfig('ima-export'));
}

function selectedImaKnowledgeBaseId() {
  const select = document.getElementById('ima-import-kb-select');
  return select?.value?.trim() || '';
}

function buildImaImportArgs(options = {}) {
  const prefix = 'ima-import';
  if (!options.plan) {
    requireImaCredentials(prefix);
  }
  const args = options.plan ? [] : buildImaCredentialArgs(prefix);
  const sourceDir = document.getElementById('ima-import-source')?.value.trim();
  const sourceFile = document.getElementById('ima-import-source-file')?.value.trim();
  const kbId = selectedImaKnowledgeBaseId();
  const folderId = document.getElementById('ima-import-folder-id')?.value.trim();
  const maxImport = document.getElementById('ima-import-max')?.value;
  const delay = document.getElementById('ima-import-delay')?.value;
  const jitter = document.getElementById('ima-import-jitter')?.value;

  if (options.listKbs) {
    args.push('--list-knowledge-bases', '--addable-only');
    return args;
  }

  if (options.listFolders) {
    if (!kbId) throw new Error('请先点击“读取知识库”并选择目标知识库');
    args.push('--scan-toc', '--knowledge-base-id', kbId);
    return args;
  }

  if (!sourceDir) throw new Error('请选择本地文件目录');
  args.push('--source-dir', sourceDir);
  if (sourceFile) args.push('--source-file', sourceFile);
  if (kbId) args.push('--knowledge-base-id', kbId);
  if (folderId) args.push('--folder-id', folderId);
  if (delay) args.push('--request-delay', delay);
  if (jitter) args.push('--request-jitter', jitter);
  if (maxImport && parseInt(maxImport, 10) > 0) args.push('--max-import', maxImport);
  const includeAssets = document.getElementById('ima-import-include-assets');
  if (includeAssets && includeAssets.checked) args.push('--include-referenced-assets');
  args.push('--progress-every', '1');

  if (options.plan) {
    args.push('--scan-source');
  } else {
    if (!kbId) throw new Error('请先点击“读取知识库”并选择目标知识库');
    args.push(options.single ? '--import-one' : '--import-all', '--yes');
    const skipExisting = document.getElementById('ima-import-skip-existing');
    if (skipExisting && !skipExisting.checked) args.push('--overwrite-existing');
  }
  return args;
}

async function runImaImportCommand(args, title, detail = '正在处理 ima 知识库任务...') {
  setRunning(true, 'ima-import');
  startProgress(title, detail);
  log(`开始：${title}`, 'info');
  try {
    const provider = TOOLS['ima-import'];
    if (!provider?.script) throw new Error('ima 导入 Provider 未提供脚本');
    const result = await runTrackedPythonCommand(provider.script, args, {
      providerId: 'ima-import',
      title,
      action: '导入',
      track: shouldTrackTask(title)
    });
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
    setRunning(false, 'ima-import');
  }
  return null;
}

function renderImaKnowledgeBaseOptions(kbs) {
  const select = document.getElementById('ima-import-kb-select');
  if (!select) return;
  const options = (kbs || []).map((kb) => {
    const id = String(kb.id || '');
    const name = String(kb.name || id || '未命名知识库');
    return `<option value="${escapeHtml(id)}">${escapeHtml(name)}</option>`;
  }).join('');
  select.innerHTML = options || '<option value="">没有读取到可写入的知识库</option>';
  renderImaFolderOptions([]);
}

function renderImaFolderOptions(nodes) {
  const select = document.getElementById('ima-import-folder-id');
  if (!select) return;
  const folders = (nodes || []).filter((node) => node.nodeType === 'folder' && node.folderId);
  const byId = new Map((nodes || []).map((node) => [node.nodeId, node]));
  const titlePath = (node) => {
    const parts = [String(node.title || '未命名文件夹')];
    let parentId = node.parentNodeId;
    let guard = 0;
    while (parentId && byId.has(parentId) && guard < 20) {
      const parent = byId.get(parentId);
      if (parent?.nodeType === 'folder') parts.unshift(String(parent.title || '未命名文件夹'));
      parentId = parent?.parentNodeId;
      guard += 1;
    }
    return parts.join(' / ');
  };
  const options = ['<option value="">知识库根目录</option>'];
  folders.forEach((folder) => {
    options.push(`<option value="${escapeHtml(folder.folderId)}">${escapeHtml(titlePath(folder))}</option>`);
  });
  select.innerHTML = options.join('');
}

function initializeImaImportHandlers() {
  loadImaConfigIntoForm('ima-import').catch((error) => {
    log(`读取 ima 配置失败：${formatError(error)}`, 'error');
  });
  document.getElementById('ima-import-save-config')?.addEventListener('click', () => saveImaConfig('ima-import'));
  document.getElementById('ima-import-browse-source')?.addEventListener('click', async () => {
    const current = document.getElementById('ima-import-source')?.value || '';
    const dir = await window.electronAPI.selectDirectory({
      title: '选择本地文件目录',
      defaultPath: current
    });
    if (dir) document.getElementById('ima-import-source').value = dir;
  });
  document.getElementById('ima-import-browse-file')?.addEventListener('click', async () => {
    const file = await window.electronAPI.selectFile({
      title: '选择单文件测试',
      filters: [
        { name: 'ima 支持文件', extensions: ['md', 'markdown', 'pdf', 'doc', 'docx', 'ppt', 'pptx', 'xls', 'xlsx', 'csv', 'png', 'jpg', 'jpeg', 'webp', 'txt', 'xmind', 'mp3', 'm4a', 'wav', 'aac'] },
        { name: '所有文件', extensions: ['*'] }
      ]
    });
    if (file) document.getElementById('ima-import-source-file').value = file;
  });
  document.getElementById('ima-import-list-kbs')?.addEventListener('click', async () => {
    try {
      const data = await runImaImportCommand(buildImaImportArgs({ listKbs: true }), '读取 ima 可写知识库', '正在读取可导入的知识库列表...');
      if (data) renderImaKnowledgeBaseOptions(data.knowledgeBases || []);
    } catch (error) {
      alert(formatError(error));
    }
  });
  document.getElementById('ima-import-kb-select')?.addEventListener('change', () => {
    renderImaFolderOptions([]);
  });
  document.getElementById('ima-import-list-folders')?.addEventListener('click', async () => {
    try {
      const data = await runImaImportCommand(buildImaImportArgs({ listFolders: true }), '读取 ima 目标文件夹', '正在读取目标知识库里的已有文件夹...');
      if (data) {
        renderImaFolderOptions(data.nodes || []);
        const folderCount = (data.nodes || []).filter((node) => node.nodeType === 'folder').length;
        log(`目标文件夹读取完成：共 ${folderCount} 个文件夹。`, 'success');
      }
    } catch (error) {
      alert(formatError(error));
    }
  });
  document.getElementById('ima-import-plan')?.addEventListener('click', async () => {
    try {
      await runImaImportCommand(buildImaImportArgs({ plan: true }), '扫描 ima 导入目录', '正在扫描本地可导入文件...');
    } catch (error) {
      alert(formatError(error));
    }
  });
  document.getElementById('ima-import-one')?.addEventListener('click', async () => {
    try {
      if (confirm('这会向 ima 知识库上传一个测试文件。确认继续吗？')) {
        await runImaImportCommand(buildImaImportArgs({ single: true }), 'ima 单文件导入测试', '正在上传第一个文件...');
      }
    } catch (error) {
      alert(formatError(error));
    }
  });
  document.getElementById('ima-import-export')?.addEventListener('click', async () => {
    try {
      if (confirm('这会向 ima 知识库批量上传本地文件。确认继续吗？')) {
        await runImaImportCommand(buildImaImportArgs(), 'ima 批量导入', '正在批量上传文件...');
      }
    } catch (error) {
      alert(formatError(error));
    }
  });
  document.getElementById('ima-import-stop')?.addEventListener('click', handleStop);
  document.getElementById('ima-import-open-dir')?.addEventListener('click', async () => {
    const dir = document.getElementById('ima-import-source')?.value.trim();
    if (dir) {
      await window.electronAPI.openPath(dir);
    } else {
      alert('请先选择本地文件目录');
    }
  });
}

function buildYuqueImportArgs(options = {}) {
  const url = document.getElementById('yuque-import-url')?.value.trim();
  const sourceDir = document.getElementById('yuque-import-output')?.value.trim();
  if (!url) throw new Error('请填写目标语雀知识库 URL');
  if (!sourceDir) throw new Error('请选择 Markdown 目录');

  const args = ['--target-book-url', url, '--source-dir', sourceDir];
  const requestTimeout = document.getElementById('yuque-import-request-timeout')?.value;
  const uploadTimeout = document.getElementById('yuque-import-upload-timeout')?.value;
  const retryAttempts = document.getElementById('yuque-import-retry-attempts')?.value;
  const retryDelay = document.getElementById('yuque-import-retry-delay')?.value;
  const uploadConcurrency = document.getElementById('yuque-import-upload-concurrency')?.value;
  const keepRemoteImages = document.getElementById('yuque-import-keep-remote-images')?.checked;
  if (requestTimeout) args.push('--request-timeout', requestTimeout);
  if (uploadTimeout) args.push('--upload-timeout', uploadTimeout);
  if (retryAttempts) args.push('--retry-attempts', retryAttempts);
  if (retryDelay) args.push('--retry-delay', retryDelay);
  if (uploadConcurrency) args.push('--upload-concurrency', uploadConcurrency);
  args.push('--remote-image-policy', keepRemoteImages ? 'keep' : 'link');
  const retryFailures = options.retryFailures || document.getElementById('yuque-import-retry-failures')?.checked;
  if (retryFailures) args.push('--retry-failures');
  if (options.saveConfig) {
    args.push('--save-config');
    return args;
  }
  if (options.plan) {
    args.push('--plan');
    return args;
  }
  const checkpointRoot = sourceDir.replace(/[\\/]+$/, '');
  args.push('--checkpoint-file', `${checkpointRoot}/.wandao/yuque-import.sqlite`, '--resume', '--checkpoint-task-id', 'yuque-import');
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

function yuqueImportReportPath() {
  const sourceDir = document.getElementById('yuque-import-output')?.value.trim();
  if (!sourceDir) throw new Error('请先选择 Markdown 目录');
  const separator = sourceDir.includes('\\') ? '\\' : '/';
  return `${sourceDir.replace(/[\\/]+$/, '')}${separator}00-语雀导入报告.json`;
}

function latestYuqueImportReportPath() {
  return latestYuqueImportReportFile || yuqueImportReportPath();
}

async function runYuqueImportCommand(args, title, detail = '正在处理语雀导入任务...') {
  setRunning(true, 'yuque-import');
  startProgress(title, detail);
  log(`开始：${title}`, 'info');
  try {
    const provider = TOOLS['yuque-import'];
    if (!provider?.script) throw new Error('语雀导入 Provider 未提供脚本');
    const result = await runTrackedPythonCommand(provider.script, args, {
      providerId: 'yuque-import',
      title,
      action: '导入',
      track: shouldTrackTask(title)
    });
    if (result.success) {
      log(`${title}完成`, 'success');
      if (result.data) log(JSON.stringify(result.data, null, 2), 'success');
      if (result.data?.reportFile) {
        latestYuqueImportReportFile = result.data.reportFile;
        log(`报告已生成：${result.data.reportFile}`, 'info');
      }
      const missingCount = Number(result.data?.missingLocalResourceCount || 0);
      const remoteCount = Number(result.data?.remoteImageCount || 0);
      const largeCount = Number(result.data?.largeImageCount || 0);
      const remoteConvertedCount = Number(result.data?.remoteImageConvertedCount || 0);
      const remoteWillConvertCount = Number(result.data?.remoteImageWillConvertCount || 0);
      if (missingCount || remoteCount || largeCount) {
        log(`资源提示：缺失本地文件 ${missingCount} 个，远程图片 ${remoteCount} 个，大图 ${largeCount} 个。可打开报告查看详情。`, missingCount ? 'error' : 'info');
      }
      if (remoteConvertedCount) {
        log(`远程图片提示：已将 ${remoteConvertedCount} 个远程图片转为普通链接，避免语雀抓取 403 图片导致文档创建失败。`, 'info');
      } else if (remoteWillConvertCount && result.data?.readOnly) {
        log(`远程图片提示：正式导入时会将 ${remoteWillConvertCount} 个远程图片转为普通链接；如需保留原样，请在高级选项中勾选“保留远程图片原样”。`, 'info');
      }
      if (result.data?.failureCount) {
        const reportFile = result.data.reportFile || yuqueImportReportPath();
        log(`有 ${result.data.failureCount} 个文档失败，完整原因见：${reportFile}`, 'error');
      }
      finishProgress(true, `${title}完成`);
    } else if (result.code === 130) {
      log(`${title}已停止，已完成项目会在下次继续时跳过。`, 'warn');
      finishProgress(false, `${title}已停止`);
      return null;
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

  document.getElementById('yuque-import-retry-failed')?.addEventListener('click', async () => {
    try {
      if (confirm('将只重试上次导入报告中的失败文档。确认继续吗？')) {
        await runYuqueImportCommand(buildYuqueImportArgs({ retryFailures: true }), '语雀重试失败文档', '正在读取上次报告并重试失败项...');
      }
    } catch (error) {
      alert(formatError(error));
    }
  });

  document.getElementById('yuque-import-open-report')?.addEventListener('click', async () => {
    try {
      await window.electronAPI.openPath(latestYuqueImportReportPath());
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
    const exportProvider = TOOLS.yinxiang;
    if (!exportProvider?.script) throw new Error('印象笔记导出 Provider 未提供凭证初始化脚本');
    const result = await window.electronAPI.runPythonCommand(exportProvider.script, args, {
      providerId: 'yinxiang-import',
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
    const provider = TOOLS['yinxiang-import'];
    if (!provider?.script) throw new Error('印象笔记导入 Provider 未提供脚本');
    const result = await runTrackedPythonCommand(provider.script, args, {
      providerId: 'yinxiang-import',
      title,
      action: '导入',
      track: shouldTrackTask(title)
    });
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
  if (config.capabilities?.scanToc === false) return;

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

function renderTocShell(toolId, note = '读取目录后，只会处理已勾选的文档；点击文件夹可批量切换其下所有文档。') {
  return `
    <section class="toc-section" id="${toolId}-toc-section">
      <div class="toc-header">
        <div>
          <strong>目录选择</strong>
          <p id="${toolId}-toc-status">目录：未读取，未读取时默认处理全部。</p>
        </div>
        <div class="toc-actions">
          <button class="btn-secondary" id="${toolId}-toc-all" type="button">全选</button>
          <button class="btn-secondary" id="${toolId}-toc-none" type="button">全不选</button>
          <button class="btn-secondary" id="${toolId}-toc-invert" type="button">反选</button>
        </div>
      </div>
      <div class="toc-list" id="${toolId}-toc-list">
        <div class="toc-empty">先点击“读取目录”，再选择要处理的内容。</div>
      </div>
      <p class="helper-note">${escapeHtml(note)}</p>
    </section>
  `;
}

function initializeTocInteraction(toolId) {
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

function normalizeZsxqTimingArg(input, fallback = 2.5, min = 1) {
  const raw = input?.value ?? '';
  if (raw === '') return String(fallback);
  const value = Number.parseFloat(raw);
  if (!Number.isFinite(value)) return String(fallback);
  return String(Math.max(min, value));
}

function zsxqGroupLimitValue() {
  const raw = document.getElementById('zsxq-group-limit')?.value || '50';
  const limit = Number.parseInt(raw, 10);
  return Number.isFinite(limit) ? limit : 50;
}

function confirmLargeZsxqGroupExport(toolId) {
  if (toolId !== 'zsxq-group') return true;
  const limit = zsxqGroupLimitValue();
  if (limit <= 1000) return true;
  return window.confirm(
    `本次计划导出 ${limit} 条知识星球帖子。\n\n` +
    '连续长时间导出可能触发平台风控，严重时可能影响账号使用甚至被封号。\n' +
    '建议分批导出，并尽量不要让单次任务超过 24 小时。\n\n' +
    '确认继续导出吗？'
  );
}

function providerCheckpointFile(toolId, output) {
  const provider = TOOLS[toolId] || {};
  if (!provider.checkpoint?.supported || !output) return '';
  const root = String(output).replace(/[\\\/]+$/, '');
  return `${root}/.wandao/checkpoint.sqlite`;
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
  validateZsxqUrlForTool(toolId, url);

  if (toolId === 'yuque-import') {
    return buildYuqueImportArgs(options);
  }

  if (toolId === 'ima-export') {
    return buildImaExportArgs(options);
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
    args.push('--request-delay', isZsxqProvider(toolId) ? normalizeZsxqTimingArg(delayInput) : delayInput.value);
  }

  const jitterInput = document.getElementById(`${prefix}-jitter`);
  if (jitterInput && jitterInput.value) {
    args.push('--request-jitter', isZsxqProvider(toolId) ? normalizeZsxqTimingArg(jitterInput) : jitterInput.value);
  }

  if (!forScan) {
    args.push('--progress-every', '1');
  }

  const checkpointFile = providerCheckpointFile(toolId, output);
  if (!forScan && checkpointFile) {
    args.push('--checkpoint-file', checkpointFile, '--resume');
  }

  if (isZsxqProvider(toolId)) {
    const maxDepth = document.getElementById(`${prefix}-max-depth`)?.value;
    if (maxDepth) args.push('--max-depth', maxDepth);

    const followLinkScope = document.getElementById(`${prefix}-follow-link-scope`)?.value;
    if (followLinkScope) args.push('--follow-link-scope', followLinkScope);

    const groupScope = document.getElementById(`${prefix}-group-scope`)?.value;
    if (groupScope) args.push('--group-scope', groupScope);

    const limitInput = document.getElementById(`${prefix}-limit`)?.value;
    if (!forScan && toolId === 'zsxq-group') {
      const limit = zsxqGroupLimitValue();
      if (!Number.isFinite(limit) || limit < 1) {
        throw new Error('知识星球 Group 单次导出数量至少为 1 条。');
      }
      args.push('--limit', String(limit));
    } else if (!forScan && limitInput !== undefined && limitInput !== '') {
      args.push('--limit', limitInput);
    }

    const includeComments = document.getElementById(`${prefix}-include-comments`);
    if (!forScan && includeComments && includeComments.checked) {
      args.push('--include-comments');
    }

    const downloadFiles = document.getElementById(`${prefix}-download-files`);
    if (!forScan && downloadFiles && downloadFiles.checked) {
      args.push('--download-files');
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

function normalizeStandardTocNodes(provider, data) {
  return window.WandaoTocTree.normalizeProviderTocNodes(provider, data);
}

function normalizeTocNodes(toolId, data) {
  const provider = TOOLS[toolId];
  if (provider && (provider.sourceKind || provider.toc?.itemsPath || provider.toc?.standard)) {
    const nodes = normalizeStandardTocNodes(provider, data);
    if (nodes.length) return nodes;
  }
  const nodes = [];
  if (toolId === 'zsxq-column') {
    (data.groups || []).forEach((group, groupIndex) => {
      const groupId = `zsxq-column-group:${group.groupIndex ?? groupIndex}`;
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
          nodeId: `zsxq-column:${key}`,
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
        selectable: Boolean(item.url) && Number(item.obj_type ?? 22) !== 0
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
  if (toolId === 'ima-export') {
    (data.nodes || []).forEach((item, index) => {
      const nodeId = String(item.nodeId || `ima-node:${index}`);
      nodes.push({
        nodeId,
        exportId: String(item.exportId || ''),
        title: item.title || '未命名',
        parentNodeId: item.parentNodeId || '',
        selectable: Boolean(item.selectable && item.exportId)
      });
    });
  }
  if (toolId === 'youdao') {
    (data.nodes || []).forEach((item, index) => {
      const nodeId = String(item.nodeId || `youdao-node:${index}`);
      nodes.push({
        nodeId,
        exportId: String(item.exportId || ''),
        title: item.title || '未命名',
        parentNodeId: item.parentNodeId || '',
        selectable: Boolean(item.selectable && item.exportId)
      });
    });
  }
  if (toolId === 'wiz') {
    (data.nodes || []).forEach((item, index) => {
      const nodeId = String(item.nodeId || `wiz-node:${index}`);
      nodes.push({
        nodeId,
        exportId: String(item.exportId || ''),
        title: item.title || '未命名',
        parentNodeId: item.parentNodeId || '',
        selectable: Boolean(item.selectable && item.exportId)
      });
    });
  }
  if (toolId === 'onenote') {
    (data.nodes || []).forEach((item, index) => {
      const nodeId = String(item.nodeId || `onenote-node:${index}`);
      nodes.push({
        nodeId,
        exportId: String(item.exportId || ''),
        title: item.title || '未命名',
        parentNodeId: item.parentNodeId || '',
        selectable: Boolean(item.selectable && item.exportId)
      });
    });
  }
  return nodes;
}

function tocNodeMaps(nodes) {
  return window.WandaoTocTree.tocNodeMaps(nodes);
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

function isZsxqProvider(toolId) {
  return toolId === 'zsxq-group' || toolId === 'zsxq-column';
}

function validateZsxqUrlForTool(toolId, url) {
  const text = String(url || '');
  if (toolId === 'zsxq-group' && /\/columns\//.test(text)) {
    throw new Error('这是知识星球专栏 URL，请切换到“知识星球专栏导出”。');
  }
  if (toolId === 'zsxq-column' && !/\/columns\//.test(text)) {
    throw new Error('这是知识星球 Group/帖子 URL，请切换到“知识星球 Group 帖子导出”。');
  }
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
    const hasSelectableItems = ids.length > 0;
    const selectionAttributes = hasSelectableItems ? '' : ' disabled aria-disabled="true" title="该目录不包含可导出的文档"';
    const count = node.selectable ? '' : (hasSelectableItems
      ? `<span class="toc-count">${selectedCount}/${ids.length}</span>`
      : `<span class="toc-count">无可导出文档</span>`);
    const childHtml = (children.get(node.nodeId) || []).map((child) => renderNode(child, depth + 1)).join('');
    return `
      <div class="toc-node">
        <button class="toc-item toc-depth-${depth}${hasSelectableItems ? '' : ' toc-item-empty'}" type="button" data-node-id="${escapeHtml(node.nodeId)}" data-depth="${depth}" style="--depth:${depth};--toc-indent:${depth * 30}px"${selectionAttributes}>
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
  return window.WandaoTocTree.selectionArgs(TOOLS[toolId], selected);
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
  if (!confirmProviderExecution(config)) return;

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
  if (!confirmLargeZsxqGroupExport(toolId)) return;
  if (!confirmProviderExecution(config)) return;

  setRunning(true, toolId);
  startProgress(`${actionName}：${config.title}`, `正在准备${actionName}任务...`);
  log(`开始${actionName}：${config.title}`, 'info');
  const state = tocStates[toolId];
  if (state?.loaded) {
    log(`本次按目录选择导出：已选择 ${state.selected.size} 篇。`, 'info');
    updateProgress(0, state.selected.size, `已选择 ${state.selected.size} 篇，正在读取远端内容...`);
  }

  try {
    const result = await runTrackedPythonCommand(config.script, args, {
      providerId: toolId,
      title: `${actionName}：${config.title}`,
      action: actionName,
      track: true
    });
    if (result.success) {
      log(`${actionName}完成`, 'success');
      if (result.data) {
        log(JSON.stringify(result.data, null, 2), 'success');
      }
      finishProgress(true, `${actionName}完成`);
    } else if (result.code === 130) {
      log(`${actionName}已停止，已完成项目会在下次继续时跳过。`, 'warn');
      finishProgress(false, `${actionName}已停止`);
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
    if (activeHistoryTask) {
      if (activeHistoryTask.pendingSave) {
        await activeHistoryTask.pendingSave.catch(() => {});
        delete activeHistoryTask.pendingSave;
      }
      activeHistoryTask.stopRequested = true;
      activeHistoryTask.status = 'stopped';
      activeHistoryTask.error = '用户手动停止任务';
      await saveTaskHistory();
      renderTaskHistory();
    }
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
  const globalStopButton = document.getElementById('btn-global-stop');
  if (globalStopButton) globalStopButton.disabled = !running;
  document.querySelectorAll('#content-area [data-manifest-action]').forEach((button) => {
    button.disabled = running;
  });
  ['toc-all', 'toc-none', 'toc-invert', 'open-dir', 'open-report', 'plan', 'one', 'retry-failed', 'save-config', 'open-token', 'list-kbs', 'list-folders'].forEach((suffix) => {
    const button = document.getElementById(`${prefix}-${suffix}`);
    if (button) button.disabled = running;
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
  if (sourceDir) args.push('--checkpoint-file', `${sourceDir.replace(/[\\/]+$/, '')}/.wandao/feishu-import.sqlite`, '--resume', '--checkpoint-task-id', 'feishu-import');
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
    const provider = TOOLS['feishu-import'];
    if (!provider?.script) throw new Error('飞书导入 Provider 未提供脚本');
    const result = await runTrackedPythonCommand(provider.script, args, {
      providerId: 'feishu-import',
      title: taskName,
      action: '导入',
      track: shouldTrackTask(taskName)
    });
    if (result.success) {
      log(`完成：${taskName}`, 'success');
      log(JSON.stringify(result.data || {}, null, 2), 'success');
      finishProgress(true, `${taskName}完成`);
      return result.data || {};
    }
    if (result.code === 130) {
      log(`${taskName}已停止，已完成项目会在下次继续时跳过。`, 'warn');
      finishProgress(false, `${taskName}已停止`);
      return null;
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

// Initialize the shell immediately; slower provider discovery continues in the background.
document.addEventListener('DOMContentLoaded', () => {
  applyTheme(loadTheme());
  renderProviderNavigation();

  loadAppSettings().then(() => {
    if (currentTool === 'settings' && !isRunning) renderSettingsPage();
  }).catch((error) => {
    appendDetailedLog('settings', 'error', formatError(error));
  });

  loadProviderManifests().then(() => {
    renderProviderNavigation();
    if ((currentTool === 'home' || currentTool === 'platform-center') && !isRunning) {
      renderAppView(currentTool);
    }
  }).catch((error) => {
    appendDetailedLog('provider', 'error', formatError(error));
  });

  // Setup navigation
  document.getElementById('provider-sidebar')?.addEventListener('click', (event) => {
    const item = event.target.closest('.nav-item');
    if (!item) return;
    switchTool(item.dataset.tool);
  });

  // Setup footer buttons
  document.getElementById('btn-clear-log').addEventListener('click', clearLog);
  document.getElementById('btn-global-stop')?.addEventListener('click', handleStop);
  document.getElementById('btn-toggle-log')?.addEventListener('click', () => {
    const section = document.getElementById('log-section');
    setLogCollapsed(!section?.classList.contains('is-collapsed'));
  });
  document.getElementById('btn-copy-error-report')?.addEventListener('click', () => {
    copyDeveloperReport().catch((error) => {
      log(`复制错误报告失败：${formatError(error)}`, 'error');
    });
  });
  document.getElementById('btn-history-refresh')?.addEventListener('click', () => {
    loadTaskHistory().catch((error) => log(`刷新任务历史失败：${formatError(error)}`, 'error'));
  });
  document.getElementById('btn-history-resume-last')?.addEventListener('click', () => {
    const task = latestResumableTask();
    if (!task) {
      alert('没有可继续的失败或中断任务。');
      return;
    }
    resumeTask(task);
  });
  document.getElementById('task-history-list')?.addEventListener('click', (event) => {
    const button = event.target.closest('[data-history-action]');
    const item = event.target.closest('[data-task-id]');
    if (!button || !item) return;
    const task = taskHistory.find((entry) => entry.id === item.dataset.taskId);
    if (!task) return;
    if (button.dataset.historyAction === 'copy') {
      copyTaskReport(task.id).catch((error) => log(`复制任务报告失败：${formatError(error)}`, 'error'));
    } else if (button.dataset.historyAction === 'copy-failures') {
      copyTaskFailures(task.id).catch((error) => log(`复制失败项失败：${formatError(error)}`, 'error'));
    } else if (button.dataset.historyAction === 'open-report') {
      openTaskArtifact(task, 'report').catch((error) => log(`打开任务报告失败：${formatError(error)}`, 'error'));
    } else if (button.dataset.historyAction === 'open-output') {
      openTaskArtifact(task, 'output').catch((error) => log(`打开任务输出失败：${formatError(error)}`, 'error'));
    } else if (button.dataset.historyAction === 'resume') {
      resumeTask(task);
    }
  });
  document.getElementById('btn-theme-toggle')?.addEventListener('click', toggleTheme);
  document.getElementById('btn-check-update')?.addEventListener('click', () => checkForUpdates(false));
  document.getElementById('btn-open-release')?.addEventListener('click', () => {
    window.electronAPI.openExternal(latestReleaseUrl);
  });
  document.getElementById('btn-dismiss-update')?.addEventListener('click', hideUpdateBanner);

  document.getElementById('btn-about').addEventListener('click', () => {
    window.electronAPI.showAbout();
  });

  document.getElementById('btn-settings').addEventListener('click', toggleLogViewMode);
  renderLogPanel();
  setLogCollapsed(true);

  window.electronAPI.getAppPath().then((paths) => {
    appPaths = paths;
    loadTaskHistory().catch((error) => log(`读取任务历史失败：${formatError(error)}`, 'error'));
    if (currentTool === DEFAULT_VIEW_ID) switchTool(DEFAULT_VIEW_ID);
    log('万能导已启动', 'success');
    window.setTimeout(() => checkForUpdates(true), 1000);
  }).catch(() => {
    renderTaskHistory();
    if (currentTool === DEFAULT_VIEW_ID) switchTool(DEFAULT_VIEW_ID);
    log('万能导已启动', 'success');
    window.setTimeout(() => checkForUpdates(true), 1000);
  });

  if (window.electronAPI.onAppInfo) {
    window.electronAPI.onAppInfo((message) => {
      log(message, 'success');
    });
  }
});
