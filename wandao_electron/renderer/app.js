const PROVIDER_REGISTRY = window.WandaoProviders;
let TOOLS = PROVIDER_REGISTRY?.tools?.() || {};
const DEFAULT_VIEW_ID = 'home';
const SIDEBAR_COLLAPSED_STORAGE_KEY = 'wandao-sidebar-collapsed-v1';
const SIDEBAR_WIDTH_STORAGE_KEY = 'wandao-sidebar-width-v1';
const DEFAULT_SIDEBAR_WIDTH = 208;
const MIN_SIDEBAR_WIDTH = 156;
const MAX_SIDEBAR_WIDTH = 320;
const SIDEBAR_COLLAPSE_SNAP_WIDTH = 120;
const COLLAPSED_SIDEBAR_WIDTH = 72;
const PRIMARY_NAV_ITEMS = [
  { id: 'home', label: '首页', description: '快速开始', icon: 'home' },
  { id: 'platform-center', label: '平台中心', description: '选择平台和操作', icon: 'platforms' },
  { id: 'task-center', label: '任务中心', description: '查看最近任务', icon: 'tasks' },
  { id: 'markdown-reader', label: 'Markdown 阅读器', description: '打开本地文档', icon: 'reader' },
  { id: 'notice-center', label: '教程公告', description: '公告与教程', icon: 'notice' },
  { id: 'plugin-center', label: '插件中心', description: '安装与更新平台', icon: 'plugins' },
  { id: 'settings', label: '设置', description: '偏好与帮助', icon: 'settings' }
];
const GITHUB_REPO_URL = 'https://github.com/tllovesxs/wandao';
const GITHUB_RAW_BASE = 'https://raw.githubusercontent.com/tllovesxs/wandao/main/';
const GITHUB_BLOB_BASE = 'https://github.com/tllovesxs/wandao/blob/main/';
const NOTICE_CENTER_MANIFEST_URL = `${GITHUB_RAW_BASE}docs/tutorial-announcements.json`;
const FLUXION_REGISTER_URL = 'https://fluxionai.space/register?source=github&campaign=wandao';
const FLUXION_EXPORT_SUCCESS_MESSAGE = '完成导出啦！送你一个 3 美元兑换码，用于 AI 辅助学习。';
const FLUXION_REDEEM_MESSAGE = '兑换码：WANNENGDAO — 登录后在工作台「兑换」输入，即可获得 $3 API 额度。';
const DEFAULT_BROWSER_DOWNLOAD_URL = 'https://www.google.com/chrome/';
let pluginCatalogState = { status: 'idle', plugins: [], query: '', error: '', offline: false, experimentalError: '', updatedAt: '' };
let pluginCatalogRequestId = 0;
const pluginOperationState = new Map();
let pluginBulkUpdateRunning = false;
let pluginOperationNotice = null;
let updateCheckPromise = null;
let updateCheckAnnounce = false;
let customPluginMessageCleanup = null;
let sidebarCollapsed = false;
let sidebarWidth = DEFAULT_SIDEBAR_WIDTH;
let sidebarResizeSession = null;
const FALLBACK_NOTICE_CENTER = {
  version: 1,
  updatedAt: '2026-08-30',
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
      type: 'announcement',
      pinned: false,
      title: 'AI 辅助学习：项目学习导师提示词',
      summary: '把导出的教学文档和源码放在一起，让 AI 像项目学习导师一样带你理解业务流程、核心代码和技术取舍。',
      date: '2026-07-28',
      badge: 'AI 学习',
      tags: ['公告', 'AI', '项目学习', '提示词'],
      path: 'prompts/项目学习导师提示词.md',
      body: '# AI 辅助学习：项目学习导师提示词\n\n把万能导导出的教学文档和源码项目放在一起，再把项目学习导师提示词发给 AI，可以让 AI 结合真实代码和课程资料讲解项目。\n\n## 使用方式\n\n1. 用万能导导出你有权限访问的教学文档。\n2. 把 Markdown 文档放到源码项目旁边。\n3. 用 AI 编程工具打开整个项目目录。\n4. 复制 `prompts/项目学习导师提示词.md` 的内容给 AI。\n5. 按章节、功能或技术点继续提问。'
    },
    {
      id: 'fluxion-ai-sponsor',
      type: 'sponsor',
      pinned: false,
      title: 'Fluxion AI · 为 AI 辅助学习提供支持',
      summary: '统一 API 接入和管理全球主流 AI 模型，支持多线路调度、用量与费用集中管理。',
      date: '2026-08-30',
      badge: '赞助商',
      tags: ['赞助商', 'AI 辅助学习', 'API'],
      path: 'docs/announcements/fluxion-ai-sponsor.md',
      body: '# Fluxion AI · 为 AI 辅助学习提供支持\n\n[![Fluxion AI：统一接入与管理全球主流 AI 模型；多线路动态路由、价格与用量透明。兑换码 WANNENGDAO 可获得 3 美元 API 额度](../images/fluxion-ai-sponsor-banner.png)](https://fluxionai.space/register?source=github&campaign=wandao)\n\n> 本区为赞助信息。万能导始终完全开源免费，AI 辅助学习不绑定任何模型或 API 服务。\n\n[**Fluxion AI**](https://fluxionai.space/register?source=github&campaign=wandao) 面向个人开发者、技术团队与企业，通过统一 API 接入全球主流 AI 模型，并以多线路调度提升可用性；模型质量、使用情况和费用可在一个平台集中管理。\n\n灵活的线路与计费方案带来更具竞争力的调用成本，实时价格和每笔消费均可查阅。\n\n## 兑换福利\n\n登录后在工作台「兑换」输入兑换码 `WANNENGDAO`，即可获得 $3 API 额度。\n\n[访问 Fluxion AI](https://fluxionai.space/register?source=github&campaign=wandao)'
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
let activeCommandOwner = null;
let recoveredCommandOwner = null;
let mainPythonProcessState = { running: false, stopping: false, providerId: '', taskId: '' };
let pythonProcessStateReady = Promise.resolve();
let appPaths = null;
let appPathsStatus = 'loading';
let appPathsError = '';
let appPathsLoadPromise = null;
let pendingProviderTool = '';
let startupAnnounced = false;
let feishuImportConfig = {};
let tocStates = {};
let pythonProgressBuffer = '';
let pythonLogSummaryBuffer = '';
let pythonLogProcessor = null;
let progressVisible = false;
let progressStartedAt = 0;
let progressHeartbeatTimer = null;
let progressBaseDetail = '';
let progressPhase = 'task';
let progressHasRealProgress = false;
let latestReleaseUrl = 'https://github.com/tllovesxs/wandao/releases/latest';
let updateInstalling = false;
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
// Keep enough history for long batch exports; the panel still renders in
// bounded pages so retaining history does not make the UI sluggish.
const MAX_LOG_ENTRIES = 10000;
const LOG_PANEL_RENDER_LIMIT = 400;
const LOG_INLINE_PREVIEW_LIMIT = 640;
const MAX_TASK_LOG_ENTRIES = 10000;
const userLogEntries = [];
const detailLogEntries = [];
let activeTaskLogEntries = [];
let logViewMode = localStorage.getItem('wandao-log-view') === 'detail' ? 'detail' : 'user';
let logPanelRenderCount = LOG_PANEL_RENDER_LIMIT;
const MAX_TASK_HISTORY = 80;
const TASK_HISTORY_RENDER_LIMIT = 20;
const TASK_FAILURE_RENDER_LIMIT = 12;
const TASK_STATUS_ORB_POSITION_KEY = 'wandao-task-status-orb-position-v1';
let taskHistory = [];
let taskHistoryFilters = { query: '', status: 'all', providerId: 'all' };
let taskHistoryVisibleLimit = TASK_HISTORY_RENDER_LIMIT;
let activeHistoryTask = null;
let latestFinishedTaskId = '';
let taskHistoryLoadPromise = null;
let taskHistoryLoadError = '';
let dismissedTaskStatusOrbId = '';
let taskStatusOrbDrag = null;
let taskStatusOrbSuppressClickUntil = 0;
const ONBOARDING_DISMISSED_STORAGE_KEY = 'wandao-onboarding-v1-dismissed';
const FORM_DRAFTS = window.WandaoFormDrafts;
const RECENT_INPUTS = window.WandaoRecentInputs;
let activeFormDraftContext = null;
let formDraftSaveTimer = null;
let recentInputControllers = [];
let recentInputStorageWarningShown = false;
let formDraftStorageWarningShown = false;

function draftStorage() {
  try {
    return window.localStorage;
  } catch (_) {
    return null;
  }
}

function formDraftRoot() {
  return document.getElementById('content-area');
}

function recentInputScope(providerId) {
  const provider = TOOLS?.[providerId] || {};
  const owner = provider.pluginId
    ? `plugin:${provider.pluginId}`
    : `${provider.sourceKind || 'core'}:${provider.platformKey || 'builtin'}`;
  return `${owner}:${providerId}`;
}

function enhanceRecentInputsForProvider(providerId) {
  recentInputControllers = [];
  if (!RECENT_INPUTS || currentTool !== providerId) return [];
  recentInputControllers = RECENT_INPUTS.enhanceRoot(
    draftStorage(),
    recentInputScope(providerId),
    formDraftRoot()
  );
  return recentInputControllers;
}

function recordCurrentRecentInputs(providerId = currentTool) {
  if (!RECENT_INPUTS || currentTool !== providerId) return { saved: 0, failed: 0 };
  const result = RECENT_INPUTS.recordRoot(
    draftStorage(),
    recentInputScope(providerId),
    formDraftRoot()
  );
  recentInputControllers.forEach((controller) => controller.render?.());
  if (result.failed > 0 && !recentInputStorageWarningShown) {
    recentInputStorageWarningShown = true;
    log('最近输入未能保存到本机；当前任务不受影响。可检查磁盘空间后重试。', 'warn');
  }
  return result;
}

async function clearRememberedFormInputs() {
  if (!(await confirmUserAction('清除最近输入和未提交表单草稿？\n\n登录凭证、API 配置和任务历史不会被删除。', {
    title: '清除表单记忆',
    confirmLabel: '清除',
    danger: true
  }))) return;
  const recentCleared = RECENT_INPUTS?.clearAll?.(draftStorage()) ?? true;
  const draftsCleared = FORM_DRAFTS?.clearAll?.(draftStorage()) ?? true;
  if (!recentCleared || !draftsCleared) {
    notifyUser('部分本机表单记录清除失败，请重启应用后重试。', 'warn');
    return;
  }
  recentInputControllers.forEach((controller) => controller.render?.());
  log('已清除最近输入和未提交表单草稿。登录凭证、API 配置和任务历史未受影响。', 'success');
  notifyUser('表单记忆已清除。', 'success');
}

function saveCurrentFormDraft() {
  if (!activeFormDraftContext || !FORM_DRAFTS) return { saved: false, fieldCount: 0 };
  const result = FORM_DRAFTS.saveDraft(
    draftStorage(),
    activeFormDraftContext.providerId,
    activeFormDraftContext.actionId,
    formDraftRoot()
  );
  if (result.fieldCount > 0 && !result.saved && !formDraftStorageWarningShown) {
    formDraftStorageWarningShown = true;
    log('未提交表单草稿保存失败；离开当前页面可能丢失填写内容。', 'warn');
  }
  return result;
}

function restoreFormDraftForProvider(providerId) {
  if (!FORM_DRAFTS || currentTool !== providerId) return { restored: 0, actionId: '' };
  const restored = FORM_DRAFTS.restoreLatestDraft(draftStorage(), providerId, formDraftRoot());
  activeFormDraftContext = {
    providerId,
    actionId: restored.actionId || 'default'
  };
  recordCurrentRecentInputs(providerId);
  if (restored.restored > 0) {
    const formSection = formDraftRoot()?.querySelector?.('.form-section');
    if (formSection && !formSection.querySelector('.draft-restore-notice')) {
      const notice = document.createElement('p');
      notice.className = 'draft-restore-notice';
      notice.setAttribute('role', 'status');
      notice.textContent = `已恢复上次未提交的填写（${restored.restored} 项）。请在执行前核对目标；可在“设置 > 表单记忆”中清除。`;
      formSection.prepend(notice);
    }
  }
  return restored;
}

function activeTaskForProvider(providerId) {
  const candidates = [];
  if (activeHistoryTask) candidates.push(activeHistoryTask);
  const runningTaskId = String(mainPythonProcessState?.taskId || '').trim();
  if (runningTaskId) {
    const recoveredTask = taskHistory.find((task) => task.id === runningTaskId);
    if (recoveredTask) candidates.push(recoveredTask);
  }
  return candidates.find((task) => (
    task?.providerId === providerId
    && ['running', 'stopping'].includes(String(task.status || '').toLowerCase())
    && Array.isArray(task.args)
  )) || null;
}

function taskArgValue(args, parameter) {
  const option = String(parameter || '').trim();
  if (!option) return '';
  const values = Array.isArray(args) ? args : [];
  const inlinePrefix = `${option}=`;
  const inline = values.find((arg) => String(arg).startsWith(inlinePrefix));
  if (inline) return String(inline).slice(inlinePrefix.length).trim();
  const index = values.findIndex((arg) => String(arg) === option);
  if (index < 0 || index + 1 >= values.length) return '';
  const value = String(values[index + 1] || '').trim();
  return value.startsWith('--') ? '' : value;
}

function providerFieldElement(provider, field) {
  const manifestElement = document.getElementById(manifestFieldId(provider, field));
  if (manifestElement) return manifestElement;
  const key = String(field?.name || '').trim();
  if (!key) return null;
  const root = formDraftRoot();
  const fields = root?.querySelectorAll?.('input, textarea, select') || [];
  return Array.from(fields).find((element) => (
    String(element.getAttribute?.('data-history-key') || '') === key
    || String(element.id || '') === `${provider.id}-${key}`
  )) || null;
}

function restoreActiveTaskFormValues(provider) {
  if (!provider || currentTool !== provider.id) return 0;
  const task = activeTaskForProvider(provider.id);
  if (!task) return 0;
  let restored = 0;
  const setIfEmpty = (element, value) => {
    if (!element || !value || String(element.value || '').trim()) return;
    element.value = value;
    restored += 1;
  };
  if (Array.isArray(provider.fields) && provider.fields.length) {
    for (const field of provider.fields) {
      if (field.type === 'notice' || field.type === 'checkbox') continue;
      const value = taskArgValue(task.args, field.arg);
      if (value) setIfEmpty(providerFieldElement(provider, field), value);
    }
  } else {
    if (!provider.noUrl) {
      setIfEmpty(document.getElementById(`${provider.id}-url`), taskArgValue(task.args, provider.urlParam));
    }
    setIfEmpty(document.getElementById(`${provider.id}-output`), taskArgValue(task.args, provider.outputParam));
  }
  if (restored > 0) {
    log('已恢复正在运行任务的目标参数。', 'info');
  }
  return restored;
}

function activateFormDraftAction(providerId, actionId = 'default') {
  if (!FORM_DRAFTS || currentTool !== providerId) return { restored: 0, actionId: '' };
  saveCurrentFormDraft();
  const normalizedActionId = String(actionId || 'default');
  activeFormDraftContext = { providerId, actionId: normalizedActionId };
  // Do not restore a previous action draft during an execution click: doing so
  // could replace parameters the user just typed before the handler reads them.
  return { restored: 0, actionId: normalizedActionId };
}

function scheduleFormDraftSave() {
  if (!activeFormDraftContext) return;
  if (formDraftSaveTimer) window.clearTimeout(formDraftSaveTimer);
  formDraftSaveTimer = window.setTimeout(() => {
    formDraftSaveTimer = null;
    saveCurrentFormDraft();
  }, 300);
}

function formDraftActionId(button) {
  const explicit = button?.dataset?.manifestAction;
  if (explicit) return explicit;
  const id = String(button?.id || '');
  const prefix = `${currentTool}-`;
  if (id.startsWith(prefix)) return id.slice(prefix.length) || 'default';
  return String(button?.textContent || '').trim() || 'default';
}

function isFormDraftAction(button) {
  if (!button || !activeFormDraftContext) return false;
  if (button.matches?.('[data-manifest-action]')) return true;
  if (!button.closest?.('.action-section')) return false;
  return !/(?:-stop|-open-dir|-open-console|-open-permission|-open-version|-open-target-wiki|-browse(?:-|$))$/.test(String(button.id || ''));
}

function initializeFormDraftPersistence() {
  const root = formDraftRoot();
  if (!root) return;
  root.addEventListener('input', scheduleFormDraftSave);
  root.addEventListener('change', scheduleFormDraftSave);
  root.addEventListener('click', (event) => {
    const button = event.target?.closest?.('button');
    if (!isFormDraftAction(button)) return;
    activateFormDraftAction(currentTool, formDraftActionId(button));
  }, true);
  window.addEventListener('beforeunload', () => {
    recordCurrentRecentInputs();
    saveCurrentFormDraft();
  });
}

function refreshProviderTools() {
  TOOLS = PROVIDER_REGISTRY?.tools?.() || TOOLS || {};
  return TOOLS;
}

const ERROR_RULES = [
  {
    category: '本地文件路径问题',
    // 裸 not found / 无法找到 会把平台 404 和"Chrome executable was not found"
    // 一起吞进来，排查方向完全反了，这里收紧成明确指向本地文件系统的写法。
    pattern: /(ENOENT|EACCES|EPERM|EISDIR|ENOTDIR|no such file(?: or directory)?|no such directory|can't open file|file not found|path not found|directory not found|系统找不到|路径不存在|目录不存在|文件不存在|无法找到[^。\n]{0,6}(?:插件|脚本|文件|目录|路径))/i,
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
    pattern: /(图片下载失败|附件下载失败|download.*(?:image|attachment)|(?:图片|附件|资源).{0,40}(?:HTTP 40[134]|响应|下载失败|上传失败|处理失败|失败|错误)|(?:image|attachment|resource).{0,40}(?:HTTP 40[134]|response|download|upload|fail|error)|tcs-devops\.aliyuncs\.com|cdn\.nlark\.com|imageFailure|imageFailures|上传附件失败)/i,
    title: '图片或附件处理失败',
    suggestion: '正文可能已导出，但这些图片没有成功本地化。请检查网络、重新登录后重试，或确认原文图片在浏览器中可以打开。'
  },
  {
    category: '远端内容不存在',
    pattern: /(\b404\b|(?:页面|内容|文档|笔记|帖子|主题|资源)不存在|文档已删除|已被删除|invalid[^。\n]{0,16}node_token|node_token[^。\n]{0,16}(?:invalid|不存在)|无效的[^。\n]{0,8}链接)/i,
    title: '目标平台上找不到这个内容',
    suggestion: '链接可能填错、内容已被删除或迁移，也可能当前账号看不到它。请在浏览器打开同一个链接确认后重试。'
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
    category: '网络连接失败',
    pattern: /(ECONNREFUSED|ECONNRESET|EHOSTUNREACH|ENETUNREACH|EPIPE|connection refused|connection reset|Connection aborted|远程主机强迫关闭)/i,
    title: '无法连接到目标平台',
    suggestion: '请检查本机网络、VPN 或代理是否正常，确认能在浏览器打开目标站点后重试。'
  },
  {
    category: '网络超时',
    pattern: /(ETIMEDOUT|ESOCKETTIMEDOUT|Read timed out|ReadTimeout|ConnectTimeout|timed out|Timeout \d+ms exceeded|TimeoutError|请求超时)/i,
    title: '请求超时',
    suggestion: '目标平台响应过慢或网络不稳定。请稍后重试；如果是导入任务，可在高级选项里调大“接口超时秒”和“图片上传超时秒”。'
  },
  {
    category: 'DNS 解析失败',
    pattern: /(ENOTFOUND|EAI_AGAIN|getaddrinfo|Name or service not known|NameResolutionError|域名解析)/i,
    title: '域名解析失败',
    suggestion: '本机 DNS 无法解析目标域名。请检查网络连接、更换 DNS，或确认链接里的域名拼写正确。'
  },
  {
    category: 'HTTPS 证书或代理问题',
    pattern: /(SSLError|SSLCertVerificationError|CERTIFICATE_VERIFY_FAILED|UNABLE_TO_VERIFY_LEAF_SIGNATURE|self signed certificate|ProxyError|ERR_PROXY|TunnelError|HTTP 407|Proxy Authentication Required)/i,
    title: 'HTTPS 证书或代理校验失败',
    suggestion: '通常是公司网络代理或安全软件在中间拦截。请暂时关闭代理/抓包工具，或把目标域名加入直连白名单后重试。'
  },
  {
    category: '目标平台 API 权限不足',
    // 裸 scope 会命中 argparse 的 --group-scope/--follow-link-scope，裸 docs:/drive:
    // 会命中日志里的 "docs: 12"，所以 scope 必须与 required/missing 同现，飞书 scope
    // 收紧成"域:资源[:动作]"（与 extractFeishuScopes 的口径一致）。
    pattern: /(required scopes?|scopes? required|missing scopes?|应用身份权限|API 权限|权限申请|tenant_access_token|app ticket|99991672|\b(?:drive|docx|docs|wiki|sheets|base):[a-z_][a-z0-9_.]*(?::[a-z0-9_.]+)?)/i,
    title: '当前应用还没有拿到这个接口的授权',
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
    title: '短时间内请求太多，被平台临时拦截',
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
    title: '自动化没有在页面上找到预期的元素',
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
  window.WandaoMarkdownDock?.render?.();
  log(next === 'dark' ? '已切换到夜间模式' : '已切换到日间模式', 'info');
}

function showUpdateBanner(info) {
  const banner = document.getElementById('update-banner');
  if (!banner || !info) return;
  latestReleaseUrl = info.releaseUrl || latestReleaseUrl;
  const latestLabel = info.latestTag || (info.latestVersion ? `v${info.latestVersion}` : '-');
  document.getElementById('update-title').textContent = `发现新版本：${latestLabel}`;
  document.getElementById('update-detail').textContent = info.canInstall
    ? `当前版本 v${info.currentVersion || '-'}，最新版本 ${latestLabel}。可在应用内下载并安装。`
    : `当前版本 v${info.currentVersion || '-'}，最新版本 ${latestLabel}。请前往 Releases 下载新版。`;
  const installButton = document.getElementById('btn-install-update');
  if (installButton) {
    installButton.hidden = !info.canInstall;
    installButton.disabled = updateInstalling;
    installButton.textContent = updateInstalling ? '更新中…' : '下载并安装';
  }
  banner.hidden = false;
}

function hideUpdateBanner() {
  const banner = document.getElementById('update-banner');
  if (banner) banner.hidden = true;
}

function setUpdateCheckControls(checking) {
  document.querySelectorAll('#btn-check-update, [data-settings-action="check-update"]').forEach((button) => {
    if (!button.dataset.updateCheckLabel) button.dataset.updateCheckLabel = button.textContent || '检查更新';
    button.disabled = checking;
    button.textContent = checking ? '检查中…' : button.dataset.updateCheckLabel;
  });
}

async function checkApplicationUpdate() {
  if (!window.electronAPI.checkForUpdates) {
    return { success: false, unsupported: true, error: '当前版本暂不支持在线检查万能导更新。' };
  }
  try {
    const result = await window.electronAPI.checkForUpdates();
    if (!result?.success) return { success: false, error: result?.error || '未知错误' };
    const info = result.data || {};
    latestReleaseUrl = info.releaseUrl || latestReleaseUrl;
    if (info.hasUpdate) {
      showUpdateBanner(info);
      log(`发现万能导新版本：v${info.latestVersion}，当前版本：v${info.currentVersion}`, 'success');
    } else {
      hideUpdateBanner();
    }
    return { success: true, info };
  } catch (error) {
    return { success: false, error: formatError(error) };
  }
}

function updateCheckSummary(application, plugins) {
  const appInfo = application.info || {};
  const appText = application.success
    ? (appInfo.hasUpdate
      ? `万能导：发现 v${appInfo.latestVersion}，已显示更新提示。`
      : `万能导：已是最新版本 v${appInfo.currentVersion || '未知'}。`)
    : `万能导：检查失败（${application.error || '未知错误'}）。`;
  const pluginUpdateCount = pluginUpdateCandidates().length;
  const pluginText = plugins?.offline
    ? '插件：暂时无法连接在线插件库，已保留本地插件状态。'
    : plugins?.success
    ? (pluginUpdateCount
      ? `插件：${pluginUpdateCount} 个可更新，已在平台卡片和插件中心标记。`
      : '插件：平台插件均为最新。')
    : `插件：检查失败（${plugins?.error || '未知错误'}）。`;
  return { appText, pluginText, pluginUpdateCount };
}

async function checkForUpdates(silent = false) {
  if (updateCheckPromise) {
    if (!silent) {
      updateCheckAnnounce = true;
      setUpdateCheckControls(true);
    }
    return updateCheckPromise;
  }
  updateCheckAnnounce = !silent;
  if (!silent) setUpdateCheckControls(true);
  updateCheckPromise = (async () => {
    const [application, plugins] = await Promise.all([
      checkApplicationUpdate(),
      loadPluginCatalog(true)
    ]);
    const summary = updateCheckSummary(application, plugins);
    if (updateCheckAnnounce) {
      const level = application.success && plugins?.success ? 'success' : 'warn';
      log(`更新检查完成：${summary.appText} ${summary.pluginText}`, level);
      notifyUser(`${summary.appText}\n${summary.pluginText}`, level, { title: '更新检查完成', duration: 7000 });
    }
    return { application, plugins, ...summary };
  })();
  try {
    return await updateCheckPromise;
  } finally {
    updateCheckPromise = null;
    updateCheckAnnounce = false;
    setUpdateCheckControls(false);
  }
}

async function installUpdate() {
  if (!window.electronAPI.installUpdate) {
    notifyUser('当前版本暂不支持程序内更新，请前往 Releases 下载。', 'info');
    return;
  }
  if (isRunning || mainPythonProcessState.running) {
    notifyUser('当前有迁移任务正在运行，请先等待任务完成或停止任务后再更新。', 'warn');
    return;
  }
  const button = document.getElementById('btn-install-update');
  updateInstalling = true;
  if (button) {
    button.disabled = true;
    button.textContent = '准备更新…';
  }
  const detail = document.getElementById('update-detail');
  if (detail) detail.textContent = '正在准备签名校验和更新下载…';
  try {
    const result = await window.electronAPI.installUpdate();
    if (!result?.success) throw new Error(result?.error || '程序更新失败');
  } catch (error) {
    updateInstalling = false;
    if (button) {
      button.disabled = false;
      button.textContent = '下载并安装';
    }
    if (detail) detail.textContent = `更新失败：${formatError(error)}`;
    log(`程序更新失败：${formatError(error)}`, 'error');
    notifyError(error, { title: '程序更新失败' });
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
  const limit = Math.max(LOG_PANEL_RENDER_LIMIT, logPanelRenderCount);
  if (entries.length <= limit) {
    return { entries, omitted: 0 };
  }
  return {
    entries: entries.slice(entries.length - limit),
    omitted: entries.length - limit
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
    errorInfo: meta.errorInfo || meta.data?.errorInfo || null,
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

function isSafeExternalHttpUrl(value) {
  const text = String(value || '').trim();
  if (!/^https?:\/\//i.test(text)) return false;
  if (typeof URL === 'function') {
    try {
      const parsed = new URL(text);
      return parsed.protocol === 'http:' || parsed.protocol === 'https:';
    } catch {
      return false;
    }
  }
  // Keep stripped-down test hosts and older embedded runtimes safe too.
  return /^https?:\/\/[^\s<>"'`]+$/i.test(text);
}

function createLogExternalLink(url) {
  const link = document.createElement('a');
  link.className = 'log-external-link';
  link.href = url;
  link.textContent = url;
  link.rel = 'noreferrer noopener';
  link.addEventListener('click', (event) => {
    event.preventDefault();
    if (typeof window.electronAPI?.openExternal === 'function') {
      window.electronAPI.openExternal(url);
    }
  });
  return link;
}

function appendResourceRecoveryMessage(parent, message) {
  const text = String(message || '');
  // Resource recovery messages use Markdown links in copied reports. Parse
  // that small, fixed subset here so the in-app log keeps meaningful labels.
  const urlPattern = /\[([^\]\r\n]+)\]\((https?:\/\/[^\s<>"'`，。；;（）(){}\[\]]+)\)|(https?:\/\/[^\s<>"'`，。；;（）(){}\[\]]+)/gi;
  let cursor = 0;
  let match;
  while ((match = urlPattern.exec(text))) {
    const url = match[2] || match[3];
    const label = match[1] || url;
    const start = match.index;
    if (start > cursor) parent.appendChild(document.createTextNode(text.slice(cursor, start)));
    if (isSafeExternalHttpUrl(url)) {
      const link = createLogExternalLink(url);
      link.textContent = label;
      parent.appendChild(link);
    } else {
      parent.appendChild(document.createTextNode(match[0]));
    }
    cursor = start + match[0].length;
  }
  if (cursor < text.length) parent.appendChild(document.createTextNode(text.slice(cursor)));
}

function createLogEntryElement(message, type = 'info', time = new Date().toISOString(), presentation = '') {
  const entry = document.createElement('div');
  entry.className = `log-entry ${type}`;
  const timestamp = formatLogTime(time);
  entry.appendChild(document.createTextNode(`[${timestamp}] `));

  // 广告日志只渲染预定义的安全结构，不解析普通日志中的 Markdown 或 HTML。
  if (presentation === 'fluxion-register' && message === FLUXION_REGISTER_URL) {
    const link = document.createElement('a');
    link.className = 'log-external-link';
    link.href = FLUXION_REGISTER_URL;
    link.textContent = FLUXION_REGISTER_URL;
    link.addEventListener('click', (event) => {
      event.preventDefault();
      window.electronAPI.openExternal(FLUXION_REGISTER_URL);
    });
    entry.appendChild(link);
  } else if (presentation === 'fluxion-redeem' && message === FLUXION_REDEEM_MESSAGE) {
    const label = document.createElement('strong');
    label.textContent = '兑换码：';
    const code = document.createElement('code');
    code.textContent = 'WANNENGDAO';
    entry.append(label, code, document.createTextNode(' — 登录后在工作台「兑换」输入，即可获得 $3 API 额度。'));
  } else {
    const text = String(message || '');
    if (text.length > LOG_INLINE_PREVIEW_LIMIT) {
      const preview = document.createElement('span');
      preview.className = 'log-entry-preview';
      preview.textContent = `${text.slice(0, LOG_INLINE_PREVIEW_LIMIT)}…`;
      const details = document.createElement('details');
      details.className = 'log-entry-details';
      const summary = document.createElement('summary');
      summary.textContent = `查看完整内容（${text.length} 字）`;
      const full = document.createElement('pre');
      full.className = 'log-entry-full';
      if (presentation === 'export-resource-recovery') {
        appendResourceRecoveryMessage(full, text);
      } else {
        full.textContent = text;
      }
      details.append(summary, full);
      entry.append(preview, details);
    } else {
      if (presentation === 'export-resource-recovery') {
        appendResourceRecoveryMessage(entry, text);
      } else {
        entry.appendChild(document.createTextNode(text));
      }
    }
  }
  return entry;
}

function createLogNoticeElement(message) {
  const entry = document.createElement('div');
  entry.className = 'log-entry muted';
  entry.textContent = message;
  return entry;
}

function trimRenderedLogEntries(logContent) {
  while (logContent.children.length > logPanelRenderCount) {
    logContent.removeChild(logContent.firstElementChild);
  }
}

function renderLogEntry(message, type = 'info', time = new Date().toISOString(), presentation = '') {
  const logContent = document.getElementById('log-content');
  if (!logContent) {
    syncLoadEarlierLogControl();
    return;
  }
  logContent.appendChild(createLogEntryElement(message, type, time, presentation));
  trimRenderedLogEntries(logContent);
  logContent.scrollTop = logContent.scrollHeight;
  syncLoadEarlierLogControl();
}

function renderUserLogEntry(entry) {
  renderLogEntry(entry.message, entry.type, entry.time, entry.presentation || '');
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

function updateLoadEarlierLogControl(omitted) {
  const button = document.getElementById('btn-load-earlier-log');
  if (!button) return;
  button.hidden = omitted <= 0;
  if (omitted > 0) {
    button.textContent = `加载更早（${Math.min(LOG_PANEL_RENDER_LIMIT, omitted)} 条）`;
  }
}

function syncLoadEarlierLogControl() {
  const entries = logViewMode === 'detail' ? detailLogEntries : userLogEntries;
  const logContent = document.getElementById('log-content');
  const renderedEntryCount = logContent
    ? Array.from(logContent.children).filter((child) => !child.classList.contains('muted')).length
    : 0;
  const omitted = Math.max(0, entries.length - Math.max(logPanelRenderCount, renderedEntryCount));
  updateLoadEarlierLogControl(omitted);
}

function renderLogPanel(scrollToEnd = true) {
  updateLogViewHeader();
  const logContent = document.getElementById('log-content');
  if (!logContent) return;
  logContent.replaceChildren();
  const allEntries = logViewMode === 'detail' ? detailLogEntries : userLogEntries;
  const { entries, omitted } = visibleLogEntries(allEntries);
  updateLoadEarlierLogControl(omitted);
  const fragment = document.createDocumentFragment();
  if (omitted > 0) {
    fragment.appendChild(createLogNoticeElement(`为保持界面流畅，当前已加载最近 ${entries.length} 条日志；可点击“加载更早”继续查看。`));
  }
  entries.forEach((entry) => {
    if (logViewMode === 'detail') {
      const source = entry.source ? `[${entry.source}] ` : '';
      const event = entry.event ? `[${entry.event}] ` : '';
      fragment.appendChild(createLogEntryElement(`${source}${event}${entry.message}`, entry.type, entry.time));
    } else {
      fragment.appendChild(createLogEntryElement(entry.message, entry.type, entry.time, entry.presentation || ''));
    }
  });
  logContent.appendChild(fragment);
  logContent.scrollTop = scrollToEnd ? logContent.scrollHeight : 0;
}

function loadEarlierLogEntries() {
  const entries = logViewMode === 'detail' ? detailLogEntries : userLogEntries;
  if (logPanelRenderCount >= entries.length) return;
  logPanelRenderCount = Math.min(entries.length, logPanelRenderCount + LOG_PANEL_RENDER_LIMIT);
  renderLogPanel(false);
}

function toggleLogViewMode() {
  logViewMode = logViewMode === 'detail' ? 'user' : 'detail';
  localStorage.setItem('wandao-log-view', logViewMode);
  renderLogPanel();
}

function appendUserLog(message, type = 'info', presentation = '') {
  const text = normalizeLogMessage(message);
  const entry = {
    time: new Date().toISOString(),
    type,
    message: text,
    presentation
  };
  userLogEntries.push(entry);
  trimLogStore(userLogEntries);
  if (logViewMode === 'user') renderUserLogEntry(entry);
}

function isExportAction(action) {
  if (action && typeof action === 'object') {
    return String(action.kind || '').toLowerCase() === 'export'
      || String(action.actionName || '').trim() === '导出';
  }
  return String(action || '').trim().toLowerCase() === 'export'
    || String(action || '').trim() === '导出';
}

function exportCompletionRate(report = {}) {
  const stats = report?.stats || report || {};
  const number = (value) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) && parsed >= 0 ? parsed : 0;
  };
  const resourceSuccess = number(stats.imageSuccess) + number(stats.attachmentSuccess);
  const resourceFailed = number(stats.imageFailed) + number(stats.attachmentFailed);
  const resourceTotal = resourceSuccess + resourceFailed;
  if (resourceTotal > 0) return resourceSuccess / resourceTotal;

  const failed = number(stats.failed);
  const completed = Math.max(
    number(stats.success),
    number(stats.exported),
    number(stats.imported),
    number(stats.created) + number(stats.updated),
    number(stats.skipped)
  );
  const total = Math.max(number(stats.total), completed + failed);
  return total > 0 ? completed / total : 0;
}

function shouldShowExportSuccessSponsor(outcome, report = {}) {
  return outcome === 'completed' || (outcome === 'partial' && exportCompletionRate(report) >= 0.9);
}

function appendExportSuccessSponsorLogs(outcome, action, report = {}) {
  if (!shouldShowExportSuccessSponsor(outcome, report) || !isExportAction(action)) return;
  appendUserLog(FLUXION_EXPORT_SUCCESS_MESSAGE, 'success', 'fluxion-export-success');
  appendUserLog(FLUXION_REGISTER_URL, 'success', 'fluxion-register');
  appendUserLog(FLUXION_REDEEM_MESSAGE, 'success', 'fluxion-redeem');
}

function appendExportResourceRecoveryLog(outcome, action, report = {}, retryingFailures = false) {
  if (!isExportAction(action) || outcome !== 'partial') return;
  const stats = report?.stats || {};
  const resourceFailures = Array.isArray(report?.resourceFailures) ? report.resourceFailures : [];
  const documentFailures = Array.isArray(report?.documentFailures) ? report.documentFailures : [];
  if (!resourceFailures.length) return;
  const isResourceOnlyPartial = !documentFailures.length && Number(stats.failed || 0) <= 0;
  const isHighCompletionPartial = isResourceOnlyPartial && exportCompletionRate(report) >= 0.9;

  const value = (item, keys) => {
    for (const key of keys) {
      const text = String(item?.[key] ?? '').replace(/\s+/g, ' ').trim();
      if (text) return text;
    }
    return '';
  };
  const lines = resourceFailures.map((item, index) => {
    const page = value(item, ['document', 'documentTitle', 'page', 'relativePath', 'path', 'title', 'docId', 'nodeId']) || '未返回页面信息';
    const legacyPageUrl = value(item, ['documentUrl', 'pageUrl', 'sourceUrl', 'documentHref']);
    const legacyPageKind = value(item, ['documentUrlKind', 'pageUrlKind', 'sourceUrlKind', 'documentHrefKind']);
    const reportPageLink = (typeof window !== 'undefined' && window.WandaoTaskReport?.resourcePageLink?.(item)) || {
      url: legacyPageUrl,
      kind: legacyPageKind === 'platform_entry' ? 'platform_entry' : 'direct_page',
      label: value(item, ['documentUrlLabel', 'pageUrlLabel', 'sourceUrlLabel', 'documentHrefLabel'])
    };
    const link = value(item, ['url', 'href', 'src', 'source', 'target', 'file', 'resource']) || '未返回资源链接';
    const reason = value(item, ['error', 'reason', 'message', 'code']);
    const documentId = value(item, ['documentId', 'docGuid', 'docId', 'nodeId']);
    const pageLabel = page.replace(/([\\\[\]])/g, '\\$1');
    // Detailed-log tests intentionally run this formatter without the DOM
    // helpers. Actual click handling still performs full URL validation.
    const hasSafePageLink = /^https?:\/\/[^\s<>"'`]+$/i.test(reportPageLink.url);
    const pageReference = hasSafePageLink && reportPageLink.kind === 'direct_page'
      ? `[${pageLabel}](${reportPageLink.url})`
      : page;
    const platformReference = hasSafePageLink && reportPageLink.kind === 'platform_entry'
      ? `；平台：[${reportPageLink.label || '打开平台'}](${reportPageLink.url})${documentId ? `（文档 ID：${documentId}；可在任务中心展开失败项后点击“定位到为知笔记”）` : ''}`
      : '';
    const resourceReference = /^https?:\/\//i.test(link) ? `[打开资源](${link})` : link;
    return `${index + 1}. 页面：${pageReference}${platformReference}；资源：${resourceReference}${reason ? `；原因：${reason}` : ''}`;
  });
  const headline = retryingFailures
    ? `重试后仍有 ${resourceFailures.length} 个资源未导出，请确认资源本身是否不可导出：`
    : (isHighCompletionPartial
      ? '由于网络波动或资源不存在，部分资源未导出；如影响阅读效果，可以点击“重试失败项”再次尝试。'
      : `本次导出有 ${resourceFailures.length} 个资源未导出，以下是失败资源明细：`);
  appendUserLog(
    `${headline}${isHighCompletionPartial || retryingFailures ? '\n失败资源明细：' : ''}\n${lines.join('\n')}`,
    'warn',
    'export-resource-recovery'
  );
}

function isSponsorLogEntry(entry) {
  return entry?.presentation === 'fluxion-export-success'
    || entry?.presentation === 'fluxion-register'
    || entry?.presentation === 'fluxion-redeem';
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

// 任务运行时会在超长输出最前面拼
// "[前部 N 个字符已省略，以下为输出尾部]"，真正的 "XxxError: 原因" 永远在末尾，
// 因此摘要必须从尾部抓最后一条异常行，不能 slice(0, 220) 取开头。
function extractErrorSummary(raw, maxLength = 220) {
  const text = String(raw || '').replace(/^\[前部 \d+ 个字符已省略[^\]]*\]\r?\n?/, '');
  const lines = text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const exceptionLine = /^[\w.]*(?:Error|Exception|Failure)\s*:\s*\S/;
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    if (!exceptionLine.test(lines[i])) continue;
    const tail = lines.slice(i).join(' ').replace(/\s+/g, ' ').trim();
    return tail.length > maxLength ? `${tail.slice(0, maxLength)}...` : tail;
  }
  const last = lines.slice(-3).join(' ').replace(/\s+/g, ' ').trim();
  return last.length > maxLength ? `...${last.slice(-maxLength)}` : last;
}

function formatUserError(message) {
  const runtimeRoot = typeof window !== 'undefined' ? window : globalThis;
  const protocol = runtimeRoot.WandaoErrorProtocol?.normalizeError;
  if (typeof protocol === 'function') {
    const info = protocol(message);
    const raw = normalizeLogMessage(message);
    const summary = extractErrorSummary(raw);
    const suffix = summary && !info.explicit ? `\n原始摘要：${summary}` : '';
    const category = runtimeRoot.WandaoErrorProtocol?.categoryLabel?.(info.category, info.categoryLabel || '操作失败')
      || info.categoryLabel
      || info.userMessage
      || '操作失败';
    return `${category}：${info.userMessage}。${info.recovery || ''}${suffix}`.trim();
  }
  const raw = normalizeLogMessage(message);
  const rule = classifyError(raw);
  const summary = extractErrorSummary(raw);
  const suffix = summary ? `\n原始摘要：${summary}` : '';
  return `${rule.category}：${rule.title}。${rule.suggestion}${suffix}`;
}

function normalizeErrorInfo(error, context = {}) {
  const runtimeRoot = typeof window !== 'undefined' ? window : globalThis;
  const protocol = runtimeRoot.WandaoErrorProtocol?.normalizeError;
  if (typeof protocol === 'function') return protocol(error, context);
  return {
    kind: 'wandao.error',
    schemaVersion: 1,
    code: 'UNKNOWN_ERROR',
    category: 'unknown',
    userMessage: formatError(error),
    recovery: '',
    retryable: true,
    correlationId: ''
  };
}

function notifyUser(message, type = 'info', options = {}) {
  const runtimeRoot = typeof window !== 'undefined' ? window : globalThis;
  const feedback = runtimeRoot.WandaoFeedback;
  if (feedback?.notify) return feedback.notify(message, { type, ...options });
  log(message, type, { forceDisplay: true });
  return null;
}

function notifyError(error, options = {}) {
  const info = normalizeErrorInfo(error, options);
  const runtimeRoot = typeof window !== 'undefined' ? window : globalThis;
  const feedback = runtimeRoot.WandaoFeedback;
  if (feedback?.error) return feedback.error(info, {
    ...options,
    title: options.title || runtimeRoot.WandaoErrorProtocol?.categoryLabel?.(info.category, info.categoryLabel || '操作失败') || info.categoryLabel || '操作失败'
  });
  log(`${info.userMessage}。${info.recovery || ''}`.trim(), 'error', { forceDisplay: true });
  return null;
}

async function confirmUserAction(message, options = {}) {
  const runtimeRoot = typeof window !== 'undefined' ? window : globalThis;
  const feedback = runtimeRoot.WandaoFeedback;
  if (feedback?.confirm) return feedback.confirm(message, options);
  // The desktop feedback layer is loaded before app.js.  If a stripped-down
  // host omits it, fail open as a Promise instead of resurrecting a blocking
  // native confirm dialog in the middle of a task.
  return true;
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
  logPanelRenderCount = LOG_PANEL_RENDER_LIMIT;
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

function maskDiagnosticArgs(args) {
  const maskArgs = window.WandaoTaskReport?.maskArgs;
  const masked = typeof maskArgs === 'function'
    ? maskArgs(Array.isArray(args) ? args : [])
    : (Array.isArray(args) ? args : []);
  return maskSensitiveValue(masked);
}

function stringifyDiagnosticData(value) {
  if (value === undefined || value === null) return '';
  try {
    return JSON.stringify(maskSensitiveValue(value));
  } catch (error) {
    return JSON.stringify({
      serializationError: formatError(error),
      valueType: typeof value
    });
  }
}

function formatDeveloperDetailEntry(entry) {
  const event = entry.event ? ` [${entry.event}]` : '';
  const provider = entry.provider ? ` [provider:${entry.provider}]` : '';
  const data = stringifyDiagnosticData(entry.data);
  const suffix = data ? ` | data=${data}` : '';
  const errorInfo = entry.errorInfo?.code ? ` | errorCode=${entry.errorInfo.code}${entry.errorInfo.correlationId ? ` | correlationId=${entry.errorInfo.correlationId}` : ''}` : '';
  return `[${formatUserDateTime(entry.time)}] [${entry.source}] [${entry.type}]${event}${provider} ${entry.message}${errorInfo}${suffix}`;
}

function activeToolLabel() {
  const active = document.querySelector('.nav-item.active');
  return active?.textContent?.trim() || TOOLS[currentTool]?.title || currentTool || '未知功能';
}

function activePluginVersionLabel() {
  const provider = TOOLS[currentTool];
  if (!provider?.pluginId) return '主程序内置功能';
  return provider.pluginVersion
    ? `${provider.pluginId} v${provider.pluginVersion}`
    : `${provider.pluginId}（版本未知）`;
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

  const userLines = userLogEntries
    .filter((entry) => !isSponsorLogEntry(entry))
    .map((entry) => `[${formatUserDateTime(entry.time)}] [${entry.type}] ${entry.message}`);
  const detailLines = detailLogEntries.map(formatDeveloperDetailEntry);
  const report = [
    '# 万能导错误报告',
    '',
    `生成时间：${formatUserDateTime(new Date())}`,
    `当前功能：${activeToolLabel()}`,
    `当前工具 ID：${currentTool || '-'}`,
    `Wandao 版本：${paths.appVersion || '未知'}`,
    `系统平台：${navigator.platform || '-'}`,
    `浏览器内核：${navigator.userAgent || '-'}`,
    `当前插件：${activePluginVersionLabel()}`,
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
  const report = normalizedTaskReport(task);
  return window.WandaoTaskReport?.summarizeStats(report.stats || task.stats || {}, task.error) || '暂无统计信息';
}

function taskHistoryBreakdownHtml(task) {
  const report = normalizedTaskReport(task);
  const stats = report?.stats || {};
  const documentFailures = taskDocumentFailureCount(task);
  const parts = [];
  if (Number(stats.success) > 0) parts.push(['正文成功', stats.success, 'success']);
  if (documentFailures > 0) parts.push(['文档失败', documentFailures, 'danger']);
  if (Number(stats.imageSuccess) > 0) parts.push(['图片成功', stats.imageSuccess, 'success']);
  if (Number(stats.imageFailed) > 0) parts.push(['图片失败', stats.imageFailed, 'warning']);
  if (Number(stats.attachmentSuccess) > 0) parts.push(['附件成功', stats.attachmentSuccess, 'success']);
  if (Number(stats.attachmentFailed) > 0) parts.push(['附件失败', stats.attachmentFailed, 'warning']);
  const typedResourceCount = Math.max(0, Number(stats.imageFailed) || 0) + Math.max(0, Number(stats.attachmentFailed) || 0);
  const genericResourceCount = Math.max(0, Number(stats.resourceFailed) || 0) - typedResourceCount;
  if (genericResourceCount > 0) parts.push(['其他资源失败', genericResourceCount, 'warning']);
  if (!parts.length) return '';
  return `<div class="task-history-breakdown" aria-label="任务分项统计">${parts.map(([label, count, tone]) => `<span class="task-history-breakdown-item ${tone}"><span>${escapeHtml(label)}</span><strong>${escapeHtml(String(count))}</strong></span>`).join('')}</div>`;
}

function taskArtifactPaths(task) {
  return window.WandaoTaskReport?.taskArtifactPaths(task) || { output: '', reportFile: '' };
}

function syncMarkdownReaderExportDirectories() {
  const seen = new Set();
  const directories = taskHistory
    .filter((task) => /导出|export/i.test(String(task?.action || '')))
    .map((task) => {
      const output = taskArtifactPaths(task).output;
      if (!output || seen.has(output)) return null;
      seen.add(output);
      const provider = TOOLS[task.providerId] || {};
      return {
        path: output,
        title: task.title || task.providerTitle || provider.title || 'Markdown 导出',
        provider: task.providerTitle || provider.title || task.providerId || '',
        finishedAt: task.finishedAt || task.startedAt || '',
        status: taskDisplayStatus(task)
      };
    })
    .filter(Boolean)
    .slice(0, 12);
  window.WandaoMarkdownDock?.setExportDirectories(directories);
}

function normalizedDirectoryPath(value) {
  return String(value || '')
    .trim()
    .replace(/[\\\/]+/g, '\\')
    .replace(/\\+$/, '')
    .toLowerCase();
}

function latestExportOutputForTool(toolId, requestedRoot = '') {
  const expectedRoot = normalizedDirectoryPath(requestedRoot);
  const task = taskHistory.find((item) => {
    if (item?.providerId !== toolId || !/导出|export/i.test(String(item?.action || ''))) return false;
    return Boolean(taskArtifactPaths(item).output);
  });
  const output = task ? taskArtifactPaths(task).output : '';
  if (!output) return '';
  if (!expectedRoot) return output;
  const actualPath = normalizedDirectoryPath(output);
  return actualPath === expectedRoot || actualPath.startsWith(`${expectedRoot}\\`)
    ? output
    : '';
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

function taskDocumentFailureCount(task) {
  return window.WandaoTaskReport?.taskDocumentFailureCount(task) || 0;
}

function taskResourceFailureCount(task) {
  return window.WandaoTaskReport?.taskResourceFailureCount(task) || 0;
}

function taskDisplayStatus(task) {
  return window.WandaoTaskReport?.deriveTaskStatus(task) || task?.status || 'failed';
}

function taskReportForResult(result, options = {}) {
  return window.WandaoTaskReport?.normalizeTaskReport(result?.data, {
    errorText: options.errorText || result?.error || '',
    errorInfo: options.errorInfo || result?.errorInfo || result?.data?.errorInfo,
    provider: options.provider,
    mode: options.mode
  }) || null;
}

function taskResultStatus(result, options = {}) {
  const report = options.report || taskReportForResult(result, options);
  const fallbackStatus = isStoppedResult(result)
    ? 'stopped'
    : (result?.success ? 'completed' : 'failed');
  return window.WandaoTaskReport?.deriveTaskStatus({
    status: fallbackStatus,
    result,
    report,
    error: options.errorText || result?.error || ''
  }, {
    status: fallbackStatus,
    result,
    errorText: options.errorText || result?.error || ''
  }) || fallbackStatus;
}

function taskResultCompletionState(result, completedDetail, options = {}) {
  const report = options.report || taskReportForResult(result, options);
  const status = taskResultStatus(result, { ...options, report });
  if (status === 'partial') {
    const summary = window.WandaoTaskReport?.summarizeStats(report?.stats || {}, '') || '部分项目未完成';
    const provider = typeof options.provider === 'string' ? TOOLS[options.provider] : options.provider;
    const recovery = providerRetryFailureArg(provider)
      ? '可点击任务提示中的“重试失败项”直接重试，也可在任务中心查看失败项。'
      : '请在任务中心查看失败项。';
    return { report, status, detail: completedDetail + '，' + summary + '。' + recovery };
  }
  if (status === 'stopped') {
    return { report, status, detail: options.stoppedDetail || (completedDetail + '已停止') };
  }
  if (status === 'paused') {
    return { report, status, detail: options.pausedDetail || (completedDetail + '因频率限制安全暂停，可在任务中心继续') };
  }
  if (status === 'completed') {
    return { report, status, detail: completedDetail };
  }
  return { report, status: 'failed', detail: options.failedDetail || (completedDetail + '失败，请查看运行日志') };
}

function finishProgressForTaskResult(result, completedDetail, options = {}) {
  const state = taskResultCompletionState(result, completedDetail, options);
  finishProgress(state.status, state.detail);
  return state.status;
}

function logTaskResultCompletion(result, completedDetail, options = {}) {
  const state = taskResultCompletionState(result, completedDetail, options);
  const type = state.status === 'completed'
    ? 'success'
    : (['partial', 'stopped', 'paused'].includes(state.status) ? 'warn' : 'error');
  log(state.detail, type);
  return state.status;
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

function taskHasDeferredDocuments(task) {
  const helper = window.WandaoTaskResume?.hasDeferredDocuments;
  if (typeof helper === 'function') return helper(task);
  const report = task?.report || task?.resultData || {};
  return Array.isArray(report?.deferred) && report.deferred.length > 0;
}

function canResumeTask(task) {
  if (!task) return false;
  if (task.argsUnavailable) return false;
  // Keep the persisted lifecycle state authoritative while a task is active.
  // `taskDisplayStatus` additionally derives the final outcome from its report.
  if (task.status === 'running' || task.status === 'stopping') return false;
  const status = taskDisplayStatus(task);
  if (status === 'running' || status === 'stopping') return false;
  if (status !== 'completed' && status !== 'partial') return true;
  const provider = TOOLS[task.providerId] || {};
  return Boolean(providerRetryFailureArg(provider) && taskFailureCount(task) > 0);
}

function resumeTaskDisabledReason(task) {
  if (!task) return '没有可继续的任务。';
  if (task.argsUnavailable) return '任务参数无法解密，请回到平台页面重新填写后执行。';
  const status = taskDisplayStatus(task);
  if (status === 'running' || status === 'stopping') return '任务正在运行或停止中，不能重复启动。';
  if (status !== 'completed' && status !== 'partial') return '';
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
    return helper(task, retryArg, taskFailureCount(task), provider);
  }
  const args = Array.isArray(task?.args) ? [...task.args] : [];
  const interrupted = ['stopped', 'interrupted'].includes(String(task?.status || '').toLowerCase());
  if ((interrupted || taskHasDeferredDocuments(task)) && retryArg) return args.filter((arg) => arg !== retryArg);
  if (retryArg && taskFailureCount(task) > 0 && !args.includes(retryArg)) {
    args.push(retryArg);
  }
  return args;
}

function taskResumeActionLabel(task) {
  const status = taskDisplayStatus(task);
  const retryableFailures = taskFailureCount(task);
  if (taskHasDeferredDocuments(task)) {
    const count = (task.report?.deferred || task.resultData?.deferred || []).length;
    return `继续任务（${count} 篇待处理）`;
  }
  const supportsFailureRetry = Boolean(providerRetryFailureArg(TOOLS[task?.providerId] || {}));
  if (supportsFailureRetry && (status === 'completed' || status === 'partial') && retryableFailures > 0) {
    return `重试失败项${retryableFailures > 1 ? `（${retryableFailures}）` : ''}`;
  }
  return '继续任务';
}

function taskResumeSubject(task) {
  const raw = String(task?.title || task?.providerTitle || task?.script || '未命名任务').trim();
  const withoutPriorAction = raw.replace(/^(?:(?:继续任务|重试失败项)(?:（[^）]*）|\([^)]*\))?\s*[:：]\s*)+/u, '').trim();
  return withoutPriorAction || raw;
}

async function performTaskHistoryLoad() {
  const filePath = taskHistoryPath();
  if (!filePath) return;
  const data = await readJsonFileIfExists(filePath);
  const storedTasks = Array.isArray(data?.tasks) ? data.tasks : [];
  let needsMigration = false;
  taskHistory = await Promise.all(storedTasks.map(async (storedTask) => {
    const task = { ...storedTask };
    const stillRunningInMain = Boolean(
      mainPythonProcessState.running
      && mainPythonProcessState.taskId
      && mainPythonProcessState.taskId === task.id
    );
    if ((task.status === 'running' || task.status === 'stopping') && !stillRunningInMain) {
      task.status = 'interrupted';
      task.finishedAt = task.finishedAt || new Date().toISOString();
      task.error = task.error || '上次运行未正常结束，可以继续执行。';
      needsMigration = true;
    }
    if (task.protectedArgs) {
      const restored = window.electronAPI.restoreTaskArgs
        ? await window.electronAPI.restoreTaskArgs(task.protectedArgs)
        : null;
      task.args = restored?.success && Array.isArray(restored.args) ? restored.args : [];
      task.argsUnavailable = !restored?.success;
    } else if (task.argsUnavailable) {
      // A previous protection failure may have persisted masked placeholders.
      // Never treat them as legacy raw arguments on a later startup.
      if (Array.isArray(task.args) && task.args.length) needsMigration = true;
      task.args = [];
    } else if (Array.isArray(task.args) && task.args.length) {
      // Legacy history stored raw args. Keep them only in memory and encrypt on the next save.
      needsMigration = true;
    } else {
      task.args = [];
    }
    if (!task.errorInfo && task.error) {
      task.errorInfo = normalizeErrorInfo(task.error, {
        provider: task.providerId,
        operation: task.action
      });
      needsMigration = true;
    }
    if (!task.originView) {
      task.originView = task.providerId || 'platform-center';
      needsMigration = true;
    }
    const savedProgress = task.progress && typeof task.progress === 'object' ? task.progress : {};
    if (!task.progress || typeof task.progress !== 'object') needsMigration = true;
    task.progress = {
      current: Math.max(0, Number(savedProgress.current) || 0),
      total: Math.max(0, Number(savedProgress.total) || 0),
      detail: String(savedProgress.detail || '')
    };
    if (
      (Array.isArray(task.logs) && task.logs.length > 500)
      || String(task.error || '').length > 4000
      || String(task.errorInfo?.technicalMessage || '').length > 4000
      || String(task.errorInfo?.legacyMessage || '').length > 4000
    ) needsMigration = true;
    return task;
  }));
  const runningTaskId = mainPythonProcessState.taskId;
  activeHistoryTask = taskHistory.find((task) => (
    ['running', 'stopping'].includes(task.status)
    && (!runningTaskId || task.id === runningTaskId)
  )) || null;
  if (needsMigration) await saveTaskHistory();
  if (typeof syncMarkdownReaderExportDirectories === 'function') {
    syncMarkdownReaderExportDirectories();
  }
  renderTaskHistory();
  renderTaskStatusOrb();
}

function loadTaskHistory() {
  if (taskHistoryLoadPromise) return taskHistoryLoadPromise;
  const operation = performTaskHistoryLoad()
    .then(() => {
      taskHistoryLoadError = '';
    })
    .catch((error) => {
      taskHistoryLoadError = formatError(error) || '读取任务历史失败';
      throw error;
    })
    .finally(() => {
      if (taskHistoryLoadPromise === operation) taskHistoryLoadPromise = null;
    });
  taskHistoryLoadPromise = operation;
  return operation;
}

async function saveTaskHistory() {
  const filePath = taskHistoryPath();
  if (!filePath) return;
  const persistedLogLimit = 500;
  const persistedErrorLimit = 4000;
  const compactText = (value, maxLength) => {
    const text = (value === null || value === undefined
      ? ''
      : typeof value === 'string' ? value : JSON.stringify(value))
      .replace(/\s+/g, ' ').trim();
    return text.length > maxLength ? `${text.slice(0, maxLength)}...` : text;
  };
  const compactErrorInfo = (info) => {
    if (!info || typeof info !== 'object') return null;
    const compact = {};
    [
      'kind', 'schemaVersion', 'code', 'category', 'categoryLabel', 'userMessage',
      'recovery', 'retryable', 'correlationId', 'provider', 'operation', 'status'
    ].forEach((key) => {
      if (info[key] !== undefined && info[key] !== null && info[key] !== '') compact[key] = info[key];
    });
    ['technicalMessage', 'legacyMessage'].forEach((key) => {
      const value = compactText(info[key], persistedErrorLimit);
      if (value) compact[key] = value;
    });
    return compact;
  };
  const compactLogs = (logs) => {
    if (!Array.isArray(logs) || logs.length <= persistedLogLimit) return logs || [];
    const important = logs.filter((entry) => (
      entry?.type === 'error'
      || entry?.type === 'warn'
      || /(?:fail|error)/i.test(String(entry?.event || ''))
    ));
    const selected = [];
    const seen = new Set();
    for (const entry of [...logs.slice(0, 150), ...important.slice(-200), ...logs.slice(-150)]) {
      if (seen.has(entry)) continue;
      seen.add(entry);
      selected.push(entry);
    }
    return selected.slice(0, persistedLogLimit);
  };
  const tasks = await Promise.all(taskHistory.slice(0, MAX_TASK_HISTORY).map(async (task) => {
    const { pendingSave, detailStartIndex, ...persistable } = task;
    const rawArgs = Array.isArray(task.args) ? task.args : [];
    if (task.argsUnavailable) {
      persistable.args = [];
    } else if (rawArgs.length && window.electronAPI.protectTaskArgs) {
      const protectedResult = await window.electronAPI.protectTaskArgs(rawArgs);
      if (protectedResult?.success) {
        persistable.protectedArgs = protectedResult.payload;
        persistable.args = [];
        persistable.argsUnavailable = false;
      } else {
        // Do not persist masked placeholders: a later release could mistake
        // them for legacy raw arguments and offer an invalid resume action.
        persistable.args = [];
        persistable.argsUnavailable = true;
        delete persistable.protectedArgs;
      }
    } else {
      persistable.args = [];
    }
    persistable.resultData = maskSensitiveValue(persistable.resultData);
    persistable.report = maskSensitiveValue(persistable.report);
    persistable.errorInfo = maskSensitiveValue(compactErrorInfo(persistable.errorInfo));
    persistable.error = maskSensitiveText(compactText(persistable.error || '', persistedErrorLimit));
    persistable.logs = maskSensitiveValue(compactLogs(persistable.logs || []));
    if (Array.isArray(task.logs) && task.logs.length > persistedLogLimit) {
      persistable.logsTruncated = true;
      persistable.logsTotal = task.logs.length;
    }
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

function currentTaskHistoryFilters() {
  const search = document.getElementById('task-history-search');
  const status = document.getElementById('task-history-status');
  const provider = document.getElementById('task-history-provider');
  taskHistoryFilters = {
    query: search ? search.value : taskHistoryFilters.query,
    status: status ? status.value : taskHistoryFilters.status,
    providerId: provider ? provider.value : taskHistoryFilters.providerId
  };
  return taskHistoryFilters;
}

function hasTaskHistoryFilters(filters = taskHistoryFilters) {
  const normalized = window.WandaoTaskHistory?.normalizeFilters(filters) || filters;
  return Boolean(normalized.query) || normalized.status !== 'all' || normalized.providerId !== 'all';
}

function taskHistoryProviders() {
  const providers = new Map();
  taskHistory.forEach((task) => {
    const providerId = String(task?.providerId || '').trim();
    if (!providerId || providers.has(providerId)) return;
    providers.set(providerId, String(task.providerTitle || providerId));
  });
  return [...providers.entries()].sort((left, right) => left[1].localeCompare(right[1], 'zh-CN'));
}

function renderTaskHistoryFilterControls(filters) {
  const search = document.getElementById('task-history-search');
  const status = document.getElementById('task-history-status');
  const provider = document.getElementById('task-history-provider');
  const clear = document.getElementById('btn-history-clear-filters');
  if (search && search.value !== filters.query) search.value = filters.query;
  if (status && status.value !== filters.status) status.value = filters.status;
  if (provider) {
    const options = taskHistoryProviders();
    const signature = JSON.stringify(options);
    if (provider.dataset.options !== signature) {
      provider.dataset.options = signature;
      provider.innerHTML = [
        '<option value="all">全部平台</option>',
        ...options.map(([id, title]) => `<option value="${escapeHtml(id)}">${escapeHtml(title)}</option>`)
      ].join('');
    }
    if ([...provider.options].some((option) => option.value === filters.providerId)) {
      provider.value = filters.providerId;
    } else {
      provider.value = 'all';
      taskHistoryFilters.providerId = 'all';
    }
  }
  if (clear) clear.disabled = !hasTaskHistoryFilters(taskHistoryFilters);
}

function resetTaskHistoryVisibleLimit() {
  taskHistoryVisibleLimit = TASK_HISTORY_RENDER_LIMIT;
}

function clearTaskHistoryFilters() {
  taskHistoryFilters = { query: '', status: 'all', providerId: 'all' };
  const search = document.getElementById('task-history-search');
  const status = document.getElementById('task-history-status');
  const provider = document.getElementById('task-history-provider');
  if (search) search.value = '';
  if (status) status.value = 'all';
  if (provider) provider.value = 'all';
  resetTaskHistoryVisibleLimit();
  renderTaskHistory();
}

function taskHistorySelection(filters) {
  const selector = window.WandaoTaskHistory?.selectVisibleTasks;
  if (typeof selector === 'function') {
    return selector(taskHistory, filters, {
      limit: taskHistoryVisibleLimit,
      getStatus: taskDisplayStatus
    });
  }
  const tasks = taskHistory.slice(0, taskHistoryVisibleLimit);
  return { tasks, total: taskHistory.length, hasMore: taskHistory.length > taskHistoryVisibleLimit };
}

function renderTaskHistory() {
  const list = document.getElementById('task-history-list');
  if (!list) return;
  const filters = currentTaskHistoryFilters();
  renderTaskHistoryFilterControls(filters);
  const { tasks, total, hasMore } = taskHistorySelection(taskHistoryFilters);
  const summary = document.getElementById('task-history-filter-summary');
  if (summary) {
    summary.textContent = total
      ? `找到 ${total} 个任务，按最近开始时间排序。`
      : (hasTaskHistoryFilters(taskHistoryFilters) ? '没有任务符合当前筛选条件。' : '暂无任务历史。');
  }
  if (!tasks.length) {
    const filtered = hasTaskHistoryFilters(taskHistoryFilters);
    list.innerHTML = filtered
      ? '<div class="task-history-empty"><p>没有找到匹配任务。</p><p>请检查关键词，或清除筛选后查看全部历史。</p><button class="btn-secondary" type="button" data-history-action="clear-filters">清除筛选</button></div>'
      : '<div class="task-history-empty">暂无任务历史。开始一次导入或导出后，记录会显示在这里。</div>';
    return;
  }
  list.innerHTML = tasks.map((task) => {
    const startedAt = task.startedAt ? formatUserDateTime(task.startedAt) : '-';
    const elapsed = task.elapsedMs ? `，耗时 ${formatDuration(task.elapsedMs)}` : '';
    const displayStatus = taskDisplayStatus(task);
    const canResume = canResumeTask(task);
    const paths = taskArtifactPaths(task);
    const failureCount = taskFailureCount(task);
    const resumeReason = resumeTaskDisabledReason(task);
    const canExportFailureLog = taskNeedsFailureLog(task);
    return `
      <div class="task-history-item" data-task-id="${escapeHtml(task.id)}">
        <div class="task-history-main">
          <div>
            <div class="task-history-title">${escapeHtml(task.title || task.providerTitle || '未命名任务')}</div>
            <div class="task-history-meta">
              <span class="task-status ${escapeHtml(displayStatus)}">${escapeHtml(taskHistoryStatusText(task))}</span>
              <span>${escapeHtml(startedAt)}${escapeHtml(elapsed)}</span>
            </div>
          </div>
          <div class="task-history-buttons">
            <button class="btn-text" type="button" data-history-action="copy">复制报告</button>
            ${failureCount ? '<button class="btn-text" type="button" data-history-action="copy-failures" aria-label="复制此任务的失败项">复制失败项</button>' : ''}
            ${canExportFailureLog ? '<button class="btn-text" type="button" data-history-action="export-failure-log">导出失败日志</button>' : ''}
            ${paths.reportFile ? '<button class="btn-text" type="button" data-history-action="open-report">打开报告</button>' : ''}
            ${paths.output ? '<button class="btn-text" type="button" data-history-action="open-output">打开输出</button>' : ''}
            <button class="btn-text" type="button" data-history-action="resume" ${canResume ? '' : 'disabled'} title="${escapeHtml(resumeReason)}">${escapeHtml(taskResumeActionLabel(task))}</button>
          </div>
        </div>
        <div class="task-history-summary">${escapeHtml(taskSummary(task))}</div>
        ${taskHistoryBreakdownHtml(task)}
        ${taskHistoryDetailsHtml(task)}
      </div>
    `;
  }).join('') + (hasMore
    ? `<div class="task-history-more" role="status">
        <span>已显示 ${tasks.length} / ${total} 条任务</span>
        <button class="btn-secondary" type="button" data-history-action="load-more">加载更多任务（还有 ${total - tasks.length} 条）</button>
      </div>`
    : '');
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

function latestFinishedTask() {
  if (!latestFinishedTaskId) return null;
  return taskHistory.find((task) => task.id === latestFinishedTaskId) || null;
}

function announceTaskOutcome(task) {
  const announcer = document.getElementById('task-announcements');
  if (!announcer || !task) return;
  const status = taskHistoryStatusText(task);
  const summary = taskSummary(task);
  announcer.textContent = `任务“${task.title || task.providerTitle || '未命名任务'}”${status}。${summary}`;
}

function normalizedTaskReport(task) {
  const storedReport = task?.report && typeof task.report === 'object' ? task.report : {};
  const reportRaw = storedReport.raw && typeof storedReport.raw === 'object' ? storedReport.raw : {};
  const resultData = task?.resultData && typeof task.resultData === 'object' ? task.resultData : {};
  const source = {
    ...resultData,
    ...reportRaw,
    ...storedReport,
    stats: {
      ...(resultData.stats && typeof resultData.stats === 'object' ? resultData.stats : {}),
      ...(reportRaw.stats && typeof reportRaw.stats === 'object' ? reportRaw.stats : {}),
      ...(storedReport.stats && typeof storedReport.stats === 'object' ? storedReport.stats : {})
    }
  };
  return window.WandaoTaskReport?.normalizeTaskReport(source, {
    errorText: task?.error,
    errorInfo: task?.errorInfo,
    provider: task?.providerId,
    mode: task?.action
  }) || {
    stats: {},
    documentFailures: [],
    resourceFailures: [],
    imageFailures: [],
    attachmentFailures: [],
    errorInfo: null
  };
}

function taskFailureGroups(task) {
  const report = normalizedTaskReport(task);
  const documentFailures = Array.isArray(report.documentFailures) ? report.documentFailures : [];
  const imageFailures = Array.isArray(report.imageFailures) ? report.imageFailures : [];
  const attachmentFailures = Array.isArray(report.attachmentFailures) ? report.attachmentFailures : [];
  const otherResourceFailures = Array.isArray(report.resourceFailures)
    ? report.resourceFailures.filter((item) => !['image', 'attachment'].includes(String(item?.type || item?.kind || '').toLowerCase()))
    : [];
  return { document: documentFailures, image: imageFailures, attachment: attachmentFailures, resource: otherResourceFailures };
}

function describeTaskFailureItem(item) {
  const describe = window.WandaoTaskReport?.describeFailureItem || ((value) => JSON.stringify(value));
  const parent = item?.document || item?.relativePath || '';
  const resourceKind = String(item?.type || item?.kind || '').toLowerCase();
  const subject = resourceKind === 'image' || resourceKind === 'attachment' || resourceKind === 'resource'
    ? item?.url || item?.target || item?.file || item?.resource || item?.relativePath || item?.path || item?.document || ''
    : item?.relativePath || item?.document || item?.title || item?.path || item?.id || item?.docId || item?.nodeId || item?.url || '';
  // Resource descriptions already prefer their URL/file reference. Pass the
  // document parent only when it adds context, avoiding a repeated path
  // when a provider uses the same value for both fields.
  const parentContext = parent && String(parent) !== String(subject) ? parent : '';
  return describe(item, parentContext);
}

function renderTaskFailureItem(item, providerId = '') {
  const description = describeTaskFailureItem(item);
  const pageLink = window.WandaoTaskReport?.resourcePageLink?.(item) || {
    url: window.WandaoTaskReport?.resourcePageUrl?.(item)
      || item?.documentUrl || item?.pageUrl || item?.sourceUrl || item?.documentHref || '',
    kind: 'direct_page',
    label: ''
  };
  const nestedResource = item?.resource && typeof item.resource === 'object' ? item.resource : {};
  const resourceUrl = item?.url || item?.href || item?.src || item?.target || item?.file
    || nestedResource.url || nestedResource.href || nestedResource.src || '';
  const documentId = item?.documentId || item?.docGuid || item?.docId || item?.nodeId || '';
  const knowledgeBaseId = item?.knowledgeBaseId || item?.kbGuid || '';
  const linkifyFailureText = (text) => {
    const value = String(text || '');
    const urlPattern = /https?:\/\/[^\s<>"'`，。；;（）(){}\[\]]+/gi;
    let cursor = 0;
    let html = '';
    let match;
    while ((match = urlPattern.exec(value))) {
      const url = match[0];
      html += escapeHtml(value.slice(cursor, match.index));
      if (isSafeExternalHttpUrl(url)) {
        html += `<a class="task-history-failure-link" href="${escapeHtml(url)}" data-external-link="true" rel="noreferrer noopener">${escapeHtml(url)}</a>`;
      } else {
        html += escapeHtml(url);
      }
      cursor = match.index + url.length;
    }
    return html + escapeHtml(value.slice(cursor));
  };
  const externalFailureLink = (url, label) => isSafeExternalHttpUrl(url)
    ? `<a class="task-history-failure-link" href="${escapeHtml(url)}" data-external-link="true" rel="noreferrer noopener">${escapeHtml(label)}</a>`
    : escapeHtml(url);
  const canLocateWizNote = providerId === 'wiz'
    && pageLink.kind === 'platform_entry'
    && pageLink.url === 'https://www.wiz.cn/xapp'
    && /^[A-Za-z0-9][A-Za-z0-9-]{0,127}$/.test(String(documentId))
    && (!knowledgeBaseId || /^[A-Za-z0-9][A-Za-z0-9-]{0,127}$/.test(String(knowledgeBaseId)));
  const pageMarker = pageLink.url && pageLink.kind === 'direct_page' && !description.includes(pageLink.url)
    ? `；页面：${externalFailureLink(pageLink.url, pageLink.label || '打开页面')}`
    : '';
  const platformMarker = pageLink.url && pageLink.kind === 'platform_entry'
    ? `；平台：${externalFailureLink(pageLink.url, pageLink.label || '打开平台')}${documentId ? `（文档 ID：${escapeHtml(documentId)}）` : ''}`
    : '';
  const resourceMarker = resourceUrl && !description.includes(resourceUrl)
    ? `；资源：${externalFailureLink(resourceUrl, '打开资源')}`
    : '';
  const locateControl = canLocateWizNote
    ? ` <button class="btn-text task-history-locate-note" type="button" data-history-action="locate-wiz-note" data-document-id="${escapeHtml(documentId)}" data-knowledge-base-id="${escapeHtml(knowledgeBaseId)}" title="在已登录的为知笔记页面中按文档 ID 精确定位">定位到为知笔记</button>`
    : '';
  const canLocateSourcePage = providerId !== 'wiz'
    && pageLink.kind === 'direct_page'
    && isSafeExternalHttpUrl(pageLink.url);
  const sourceLocateControl = canLocateSourcePage
    ? ` <button class="btn-text task-history-locate-source" type="button" data-history-action="locate-source-page" data-document-url="${escapeHtml(pageLink.url)}" title="在系统浏览器中打开这篇失败文档的原始页面">定位到原文</button>`
    : '';
  return `${linkifyFailureText(description)}${resourceMarker}${pageMarker}${platformMarker}${locateControl}${sourceLocateControl}`;
}

function renderTaskFailureDetails(title, items, className, limit = TASK_FAILURE_RENDER_LIMIT, options = {}) {
  const list = Array.isArray(items) ? items : [];
  if (!list.length) return '';
  const shown = list.slice(0, limit);
  const more = list.length > shown.length
    ? `<button class="btn-text task-history-detail-more" type="button"
        data-history-action="expand-failures"
        data-failure-kind="${escapeHtml(className || 'resource')}"
        data-failure-shown="${shown.length}"
        data-failure-page-size="${limit}"
        aria-label="继续展开${escapeHtml(title)}">继续展开（还有 ${list.length - shown.length} 项）</button>`
    : '';
  return `
    <section class="task-history-detail-block" data-failure-kind="${escapeHtml(className || 'resource')}">
      <h4>${escapeHtml(title)}（${list.length}）</h4>
      <ul>${shown.map((item) => `<li>${renderTaskFailureItem(item, options.providerId)}</li>`).join('')}</ul>
      ${more}
    </section>
  `;
}

function expandTaskFailureDetails(button, task) {
  if (!button || !task) return;
  const section = button.closest('.task-history-detail-block');
  const listElement = section?.querySelector('ul');
  const kind = String(button.dataset.failureKind || 'resource');
  const items = taskFailureGroups(task)[kind] || [];
  if (!listElement || !items.length) {
    button.remove();
    return;
  }
  const shown = Math.min(items.length, Math.max(0, Number.parseInt(button.dataset.failureShown || '0', 10)) || 0);
  const pageSize = Math.max(1, Number.parseInt(button.dataset.failurePageSize || String(TASK_FAILURE_RENDER_LIMIT), 10) || TASK_FAILURE_RENDER_LIMIT);
  const nextItems = items.slice(shown, shown + pageSize);
  nextItems.forEach((item) => {
    const listItem = document.createElement('li');
    listItem.innerHTML = renderTaskFailureItem(item, task.providerId);
    listElement.appendChild(listItem);
  });
  const nextShown = shown + nextItems.length;
  if (nextShown >= items.length) {
    button.remove();
    return;
  }
  button.dataset.failureShown = String(nextShown);
  button.textContent = `继续展开（还有 ${items.length - nextShown} 项）`;
}

function renderTaskErrorProtocol(info) {
  if (!info) return '';
  return `
    <section class="task-history-error-protocol">
      <h4>错误信息：${escapeHtml(info.code || 'UNKNOWN_ERROR')}</h4>
      <div class="task-history-error-grid">
        <span>用户提示</span><strong>${escapeHtml(info.userMessage || '任务执行失败')}</strong>
        <span>恢复建议</span><strong>${escapeHtml(info.recovery || '请查看详细日志后重试。')}</strong>
        <span>可重试</span><strong>${info.retryable ? '是' : '否'}</strong>
        ${info.correlationId ? `<span>关联 ID</span><code>${escapeHtml(info.correlationId)}</code>` : ''}
      </div>
    </section>
  `;
}

function taskNeedsFailureLog(task) {
  const report = normalizedTaskReport(task);
  const status = taskDisplayStatus(task);
  return Boolean(
    taskFailureCount(task)
    || task?.error
    || report?.errorInfo
    || ['partial', 'failed', 'interrupted', 'paused'].includes(status)
  );
}

function taskHistoryDetailsHtml(task) {
  if (!taskNeedsFailureLog(task)) return '';
  const report = normalizedTaskReport(task);
  const { document: documentFailures, image: imageFailures, attachment: attachmentFailures, resource: otherResourceFailures } = taskFailureGroups(task);
  const errorInfo = report.errorInfo || task.errorInfo || null;
  const failurePreview = taskFailureDiagnostics(task, 12);
  const canResume = canResumeTask(task);
  const recovery = canResume
    ? `${taskResumeActionLabel(task)}后，万能导会尽量保留已完成内容，只处理仍需处理的项目。`
    : (errorInfo?.recovery || '请查看失败日志，确认输入、权限或网络后重新执行。');
  return `
    <details class="task-history-details">
      <summary>展开失败项与恢复建议</summary>
      <div class="task-history-details-content">
        ${renderTaskFailureDetails('文档失败', documentFailures, 'document', 12, { providerId: task.providerId })}
        ${renderTaskFailureDetails('图片失败', imageFailures, 'image', 12, { providerId: task.providerId })}
        ${renderTaskFailureDetails('附件失败', attachmentFailures, 'attachment', 12, { providerId: task.providerId })}
        ${renderTaskFailureDetails('其他资源失败', otherResourceFailures, 'resource', 12, { providerId: task.providerId })}
        ${!documentFailures.length && !imageFailures.length && !attachmentFailures.length && !otherResourceFailures.length && failurePreview.length ? `
          <section class="task-history-detail-block">
            <h4>关键失败摘要</h4>
            <ul>${failurePreview.map((line) => `<li>${escapeHtml(line)}</li>`).join('')}</ul>
          </section>
        ` : ''}
        ${renderTaskErrorProtocol(errorInfo)}
        <section class="task-history-recovery">
          <h4>下一步</h4>
          <p>${escapeHtml(recovery)}</p>
        </section>
      </div>
    </details>
  `;
}

function activeTaskStatusOrbTask() {
  return activeHistoryTask || latestFinishedTask();
}

function canRetryFailureItems(task) {
  if (!task || taskHasDeferredDocuments(task)) return false;
  const status = taskDisplayStatus(task);
  if (!['completed', 'partial', 'failed'].includes(status)) return false;
  return Boolean(
    providerRetryFailureArg(TOOLS[task.providerId] || {})
    && taskFailureCount(task) > 0
    && canResumeTask(task)
  );
}

function taskOriginView(task) {
  const origin = String(task?.originView || task?.providerId || '').trim();
  if (origin && (TOOLS[origin] || PRIMARY_NAV_ITEMS.some((item) => item.id === origin) || origin.startsWith('platform:'))) {
    return origin;
  }
  return 'platform-center';
}

function taskStatusOrbState(task) {
  const status = taskDisplayStatus(task);
  const active = ['running', 'stopping'].includes(status);
  const current = Math.max(0, Number(task?.progress?.current) || 0);
  const total = Math.max(0, Number(task?.progress?.total) || 0);
  if (active && total > 0) return { status: 'running', label: `${Math.min(current, total)}/${total}`, detail: '任务进行中' };
  if (active) return { status: 'running', label: '处理中', detail: task?.progress?.detail || '任务进行中' };
  if (status === 'completed') return { status: 'completed', label: '完成', detail: taskSummary(task) };
  if (status === 'partial' || status === 'paused') return { status: 'partial', label: '部分完成', detail: taskSummary(task) };
  if (status === 'stopped' || status === 'interrupted') return { status: 'stopped', label: '已停止', detail: taskSummary(task) };
  return { status: 'failed', label: '失败', detail: taskSummary(task) };
}

function renderTaskStatusOrb() {
  const orb = document.getElementById('task-status-orb');
  if (!orb) return;
  const task = activeTaskStatusOrbTask();
  const status = task ? taskDisplayStatus(task) : '';
  const isActive = ['running', 'stopping'].includes(status);
  if (!task || (!isActive && dismissedTaskStatusOrbId === task.id)) {
    if (!orb.hidden || orb.childElementCount) {
      orb.hidden = true;
      orb.replaceChildren();
      delete orb.dataset.renderKey;
    }
    return;
  }
  const state = taskStatusOrbState(task);
  const origin = taskOriginView(task);
  const originName = TOOLS[origin]?.title || PRIMARY_NAV_ITEMS.find((item) => item.id === origin)?.label || '任务页面';
  const mark = state.status === 'completed' ? '✓' : (state.status === 'partial' || state.status === 'stopped' ? '!' : (state.status === 'failed' ? '×' : '•'));
  const retryCount = taskFailureCount(task);
  const canRetry = canRetryFailureItems(task);
  const renderKey = [task.id, origin, state.status, isActive, canRetry].join('|');
  orb.hidden = false;
  if (orb.dataset.renderKey !== renderKey) {
    orb.className = `task-status-orb ${state.status}`;
    orb.innerHTML = `
      <button class="task-status-orb-grip" type="button" data-task-orb-drag aria-label="拖动任务状态提示" title="拖动以调整位置">⠿</button>
      <button class="task-status-orb-main" type="button" data-task-orb-action="return" aria-label="返回任务发起页面">
        <span class="task-status-orb-mark" aria-hidden="true"></span>
        <span class="task-status-orb-copy"><strong></strong><span></span></span>
      </button>
      ${canRetry ? '<button class="task-status-orb-retry" type="button" data-task-orb-action="retry" title="只重新处理这次任务失败的文档或资源"></button>' : ''}
      <button class="task-status-orb-center" type="button" data-task-orb-action="task-center">任务中心</button>
      ${isActive ? '' : '<button class="task-status-orb-dismiss" type="button" data-task-orb-action="dismiss" aria-label="隐藏最近任务提示">×</button>'}
    `;
    orb.dataset.renderKey = renderKey;
  }
  bindTaskStatusOrbDrag(orb);
  restoreTaskStatusOrbPosition(orb);
  const mainButton = orb.querySelector('.task-status-orb-main');
  const markElement = orb.querySelector('.task-status-orb-mark');
  const copy = orb.querySelector('.task-status-orb-copy');
  const label = copy?.querySelector('strong');
  const title = copy?.querySelector('span');
  if (mainButton) mainButton.setAttribute('aria-label', `返回任务发起页面：${originName}`);
  if (markElement) markElement.textContent = mark;
  if (label) label.textContent = state.label;
  if (title) title.textContent = task.title || task.providerTitle || '最近任务';
  const retryButton = orb.querySelector('.task-status-orb-retry');
  if (retryButton) retryButton.textContent = `重试失败项${retryCount > 1 ? `（${retryCount}）` : ''}`;
}

function readTaskStatusOrbPosition() {
  try {
    const value = JSON.parse(localStorage.getItem(TASK_STATUS_ORB_POSITION_KEY) || 'null');
    if (Number.isFinite(value?.left) && Number.isFinite(value?.top)) return value;
  } catch (_) {
    // Ignore corrupted display preferences and use the default position.
  }
  return null;
}

function saveTaskStatusOrbPosition(left, top) {
  try {
    localStorage.setItem(TASK_STATUS_ORB_POSITION_KEY, JSON.stringify({ left, top }));
  } catch (_) {
    // The task status indicator must remain usable if storage is unavailable.
  }
}

function clampTaskStatusOrbPosition(orb, left, top) {
  const margin = 8;
  return {
    left: Math.max(margin, Math.min(Math.max(margin, window.innerWidth - orb.offsetWidth - margin), left)),
    top: Math.max(margin, Math.min(Math.max(margin, window.innerHeight - orb.offsetHeight - margin), top))
  };
}

function applyTaskStatusOrbPosition(orb, left, top, persist = false) {
  if (!orb) return;
  const position = clampTaskStatusOrbPosition(orb, Number(left) || 0, Number(top) || 0);
  orb.style.left = `${Math.round(position.left)}px`;
  orb.style.top = `${Math.round(position.top)}px`;
  orb.style.right = 'auto';
  orb.style.bottom = 'auto';
  orb.dataset.positionApplied = 'true';
  if (persist) saveTaskStatusOrbPosition(position.left, position.top);
}

function restoreTaskStatusOrbPosition(orb) {
  if (!orb || orb.dataset.positionApplied === 'true') return;
  const saved = readTaskStatusOrbPosition();
  if (saved) applyTaskStatusOrbPosition(orb, saved.left, saved.top);
}

function bindTaskStatusOrbDrag(orb) {
  if (!orb || orb.dataset.dragBound === 'true') return;
  const grip = orb.querySelector('[data-task-orb-drag]');
  if (!grip) return;
  orb.dataset.dragBound = 'true';
  grip.addEventListener('pointerdown', (event) => {
    if (event.button !== 0) return;
    const rect = orb.getBoundingClientRect();
    taskStatusOrbDrag = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      offsetX: event.clientX - rect.left,
      offsetY: event.clientY - rect.top,
      moved: false
    };
    grip.setPointerCapture?.(event.pointerId);
    orb.classList.add('is-dragging');
    event.preventDefault();
  });
  grip.addEventListener('pointermove', (event) => {
    if (!taskStatusOrbDrag || taskStatusOrbDrag.pointerId !== event.pointerId) return;
    const movedX = Math.abs(event.clientX - taskStatusOrbDrag.startX);
    const movedY = Math.abs(event.clientY - taskStatusOrbDrag.startY);
    taskStatusOrbDrag.moved = taskStatusOrbDrag.moved || movedX > 4 || movedY > 4;
    if (!taskStatusOrbDrag.moved) return;
    applyTaskStatusOrbPosition(
      orb,
      event.clientX - taskStatusOrbDrag.offsetX,
      event.clientY - taskStatusOrbDrag.offsetY
    );
    event.preventDefault();
  });
  const finishDrag = (event) => {
    if (!taskStatusOrbDrag || taskStatusOrbDrag.pointerId !== event.pointerId) return;
    const moved = taskStatusOrbDrag.moved;
    taskStatusOrbDrag = null;
    orb.classList.remove('is-dragging');
    if (moved) {
      const rect = orb.getBoundingClientRect();
      applyTaskStatusOrbPosition(orb, rect.left, rect.top, true);
      taskStatusOrbSuppressClickUntil = Date.now() + 260;
    }
  };
  grip.addEventListener('pointerup', finishDrag);
  grip.addEventListener('pointercancel', finishDrag);
  grip.addEventListener('keydown', (event) => {
    const rect = orb.getBoundingClientRect();
    const step = event.shiftKey ? 80 : 24;
    const next = { left: rect.left, top: rect.top };
    if (event.key === 'ArrowLeft') next.left -= step;
    else if (event.key === 'ArrowRight') next.left += step;
    else if (event.key === 'ArrowUp') next.top -= step;
    else if (event.key === 'ArrowDown') next.top += step;
    else return;
    event.preventDefault();
    applyTaskStatusOrbPosition(orb, next.left, next.top, true);
  });
}

function dismissTaskStatusOrb() {
  const task = activeTaskStatusOrbTask();
  if (!task || ['running', 'stopping'].includes(taskDisplayStatus(task))) return;
  dismissedTaskStatusOrbId = task.id;
  renderTaskStatusOrb();
}

function openTaskOrigin(task) {
  if (!task) return;
  switchTool(taskOriginView(task));
}

function taskFailureLogFilename(task) {
  const source = String(task?.providerTitle || task?.providerId || 'task')
    .replace(/[\\/:*?"<>|]+/g, '-')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '') || 'task';
  const date = new Date(task?.finishedAt || task?.startedAt || Date.now());
  const safeDate = Number.isNaN(date.getTime()) ? new Date() : date;
  const pad = (value) => String(value).padStart(2, '0');
  const stamp = `${safeDate.getFullYear()}${pad(safeDate.getMonth() + 1)}${pad(safeDate.getDate())}-${pad(safeDate.getHours())}${pad(safeDate.getMinutes())}${pad(safeDate.getSeconds())}`;
  return `wandao-失败日志-${source}-${stamp}.log`;
}

function createTaskFailureLog(task) {
  const report = normalizedTaskReport(task);
  const paths = taskArtifactPaths(task);
  const failures = taskFailureDiagnostics(task, 500);
  const errorInfo = report.errorInfo || task.errorInfo || null;
  const taskLogs = Array.isArray(task.logs) ? task.logs : [];
  const detailLines = taskLogs.map((entry) => formatDeveloperDetailEntry(entry));
  return maskSensitiveText([
    '万能导失败日志',
    `生成时间：${formatUserDateTime(new Date())}`,
    `任务 ID：${task.id || '-'}`,
    `平台：${task.providerTitle || task.providerId || '-'}`,
    `任务：${task.title || '-'}`,
    `状态：${taskHistoryStatusText(task)}`,
    `开始时间：${formatUserDateTime(task.startedAt)}`,
    `结束时间：${formatUserDateTime(task.finishedAt)}`,
    task.elapsedMs ? `耗时：${formatDuration(task.elapsedMs)}` : '',
    `统计：${taskSummary(task)}`,
    paths.output ? `输出目录：${paths.output}` : '',
    paths.reportFile ? `报告文件：${paths.reportFile}` : '',
    '',
    '## 失败项',
    failures.length ? failures.join('\n') : '脚本未返回逐项失败信息。',
    '',
    '## 错误协议',
    errorInfo ? [
      `错误代码：${errorInfo.code || '-'}`,
      `用户提示：${errorInfo.userMessage || '-'}`,
      `恢复建议：${errorInfo.recovery || '-'}`,
      `可重试：${errorInfo.retryable ? '是' : '否'}`,
      errorInfo.correlationId ? `关联 ID：${errorInfo.correlationId}` : ''
    ].filter(Boolean).join('\n') : (task.error || '无'),
    '',
    '## 本任务详细日志',
    detailLines.length ? detailLines.join('\n') : '无',
    '',
    '## 建议',
    '请将此日志连同任务中心中的截图一并反馈。日志已自动脱敏；如有输出报告，也请一并提供。'
  ].filter((line) => line !== '').join('\n'));
}

async function exportTaskFailureLog(taskId) {
  const task = taskHistory.find((item) => item.id === taskId);
  if (!task || !taskNeedsFailureLog(task)) {
    notifyUser('这条任务没有可导出的失败日志。', 'info');
    return;
  }
  const target = await window.electronAPI.saveFile({
    title: '导出失败日志',
    defaultPath: taskFailureLogFilename(task),
    filters: [{ name: '日志文件', extensions: ['log'] }, { name: '文本文件', extensions: ['txt'] }]
  });
  if (!target) return;
  const result = await window.electronAPI.writeFile(target, createTaskFailureLog(task));
  if (!result?.success) throw new Error(result?.error || '写入失败日志失败');
  log('已导出失败日志。', 'success');
}

async function locateWizFailureDocument(task, documentId, knowledgeBaseId = '') {
  if (task?.providerId !== 'wiz') throw new Error('只有为知笔记失败项支持页面内精确定位。');
  if (isRunning || activeCommandOwner) {
    notifyUser('当前已有任务运行中，请等待结束后再定位为知笔记。', 'warn');
    return;
  }
  const docId = String(documentId || '').trim();
  const kbId = String(knowledgeBaseId || '').trim();
  if (!/^[A-Za-z0-9][A-Za-z0-9-]{0,127}$/.test(docId)) {
    throw new Error('失败项没有可用于定位的有效为知文档 ID。');
  }
  if (kbId && !/^[A-Za-z0-9][A-Za-z0-9-]{0,127}$/.test(kbId)) {
    throw new Error('失败项包含无效的为知知识库 ID。');
  }
  const script = String(task.script || '').trim();
  if (!script) throw new Error('这条任务未记录为知导出脚本，无法定位笔记。');
  const args = ['--locate-doc', docId];
  if (kbId) args.push('--locate-kb', kbId);
  const result = await runProviderCommand(script, args, {
    providerId: 'wiz',
    title: '定位为知笔记',
    action: 'locate',
    track: false,
    progressDetail: '正在打开为知笔记并定位失败文档...'
  });
  if (!result?.success) throw new Error(result?.error || '为知笔记定位失败。');
  const title = String(result?.data?.title || '').trim();
  notifyUser(title ? `已在为知笔记中定位到“${title}”。` : '已在为知笔记中定位到目标笔记。', 'success');
}

async function locateSourcePage(url) {
  const sourceUrl = String(url || '').trim();
  if (!isSafeExternalHttpUrl(sourceUrl)) {
    throw new Error('失败项没有可用于定位的有效原文链接。');
  }
  const result = await window.electronAPI.openExternal(sourceUrl);
  if (!result?.success) throw new Error(result?.error || '打开原文页面失败。');
}

async function handleTaskAction(task, action, options = {}) {
  if (!task && action !== 'task-center') return;
  if (action === 'copy') return copyTaskReport(task.id);
  if (action === 'copy-failures') return copyTaskFailures(task.id);
  if (action === 'export-failure-log') return exportTaskFailureLog(task.id);
  if (action === 'open-report') return openTaskArtifact(task, 'report');
  if (action === 'open-output') return openTaskArtifact(task, 'output');
  if (action === 'locate-wiz-note') return locateWizFailureDocument(task, options.documentId, options.knowledgeBaseId);
  if (action === 'locate-source-page') return locateSourcePage(options.documentUrl);
  if (action === 'resume') return resumeTask(task);
  if (action === 'task-center') switchTool('task-center');
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
    errorInfo: null,
    stats: extractTaskStats(null),
    originView: context.originView || currentTool,
    progress: { current: 0, total: 0, detail: '' },
    logs: []
  };
  taskHistory.unshift(task);
  taskHistory = taskHistory.slice(0, MAX_TASK_HISTORY);
  activeHistoryTask = task;
  latestFinishedTaskId = '';
  dismissedTaskStatusOrbId = '';
  activeTaskLogEntries = [];
  task.pendingSave = saveTaskHistory();
  renderTaskHistory();
  renderTaskStatusOrb();
  return task;
}

function isStoppedResult(result) {
  return result?.code === 130 || result?.data?.stopped === true;
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
  const stopped = isStoppedResult(result) && !thrownError;
  task.finishedAt = finishedAt.toISOString();
  task.elapsedMs = finishedAt.getTime() - startedAt.getTime();
  task.resultData = result?.data || null;
  const rawTaskErrorInfo = result?.errorInfo || result?.data?.errorInfo || thrownError || result?.error || '';
  task.errorInfo = rawTaskErrorInfo
    ? normalizeErrorInfo(rawTaskErrorInfo, { provider: task.providerId, operation: task.action })
    : null;
  task.error = thrownError
    ? formatError(thrownError)
    : (result?.error || task.errorInfo?.technicalMessage || '');
  task.report = window.WandaoTaskReport?.normalizeTaskReport(task.resultData, {
    errorText: task.error,
    errorInfo: result?.errorInfo || result?.data?.errorInfo || task.errorInfo,
    provider: task.providerId,
    mode: task.action
  }) || null;
  task.stats = task.report?.stats ? { ...task.report.stats, failureItems: task.report.failures || [] } : extractTaskStats(task.resultData, task.error);
  const fallbackStatus = stopped ? 'stopped' : (success ? 'completed' : (task.stopRequested ? 'stopped' : 'failed'));
  task.status = window.WandaoTaskReport?.deriveTaskStatus({
    ...task,
    status: fallbackStatus
  }, {
    status: fallbackStatus,
    result,
    thrownError,
    errorText: task.error,
    errorInfo: task.errorInfo
  }) || fallbackStatus;
  task.logs = [...activeTaskLogEntries];
  if (activeHistoryTask?.id === task.id) {
    activeHistoryTask = null;
    activeTaskLogEntries = [];
  }
  latestFinishedTaskId = task.id;
  await saveTaskHistory();
  if (typeof syncMarkdownReaderExportDirectories === 'function') {
    syncMarkdownReaderExportDirectories();
  }
  renderTaskHistory();
  renderTaskStatusOrb();
  announceTaskOutcome(task);
}

async function runTrackedPythonCommand(script, args, context = {}, options = {}) {
  if (context.track !== false) {
    if (taskHistoryLoadPromise) await taskHistoryLoadPromise;
    if (taskHistoryLoadError) {
      throw new Error(`任务历史尚未安全加载：${taskHistoryLoadError}`);
    }
  }
  const jobId = context.jobId || makeTaskId();
  const runtimeStartedAt = Date.now();
  const commandArgs = Array.isArray(args) ? [...args] : [];
  if (commandArgs.includes('--checkpoint-file') && !commandArgs.includes('--checkpoint-task-id')) {
    commandArgs.push('--checkpoint-task-id', jobId);
  }
  const task = startHistoryTask(script, commandArgs, { ...context, jobId });
  const providerId = context.providerId || currentTool;
  const runtimeContext = {
    taskId: task?.id || '',
    runId: task?.runId || '',
    jobId: task?.jobId || jobId,
    parentRunId: task?.parentRunId || context.parentRunId || '',
    providerId,
    pluginVersion: TOOLS[providerId]?.pluginVersion || '',
    action: context.action || '',
    script,
    args: maskDiagnosticArgs(commandArgs)
  };
  appendDetailedLog('runtime', 'info', `启动任务：${context.title || script}`, {
    event: 'runtime.command.started',
    provider: providerId,
    data: runtimeContext
  });
  try {
    const result = await window.electronAPI.runPythonCommand(script, commandArgs, {
      ...options,
      taskId: task?.id || '',
      runId: task?.runId || '',
      jobId: task?.jobId || jobId,
      parentRunId: task?.parentRunId || '',
      providerId
    });
    recordPythonResultDiagnostics(script, result);
    appendDetailedLog('runtime', result?.success ? 'success' : (isStoppedResult(result) ? 'warn' : 'error'), `任务进程结束：${context.title || script}`, {
      event: 'runtime.command.finished',
      provider: providerId,
      data: {
        ...runtimeContext,
        elapsedMs: Date.now() - runtimeStartedAt,
        success: Boolean(result?.success),
        stopped: isStoppedResult(result),
        code: result?.code ?? 0,
        legacyResult: Boolean(result?.legacyResult),
        error: result?.error ? compactDiagnostic(result.error, 1600) : '',
        errorInfo: result?.errorInfo || result?.data?.errorInfo || null
      }
    });
    await finishHistoryTask(task, result);
    return result;
  } catch (error) {
    appendDetailedLog('runtime', 'error', `任务调用异常：${context.title || script}`, {
      event: 'runtime.command.exception',
      provider: providerId,
      data: {
        ...runtimeContext,
        elapsedMs: Date.now() - runtimeStartedAt,
        error: formatError(error),
        errorInfo: normalizeErrorInfo(error, { provider: providerId, operation: context.action }),
        stack: error?.stack || ''
      }
    });
    await finishHistoryTask(task, null, error);
    throw error;
  }
}

async function runProviderCommand(script, args, context = {}, options = {}) {
  const providerId = context.providerId || currentTool;
  if (isRunning || activeCommandOwner) {
    throw new Error('当前已有任务运行中，请等待结束或先停止当前任务。');
  }
  if (!progressVisible) {
    startProgress(
      context.title || TOOLS[providerId]?.title || '任务进行中',
      context.progressDetail || '任务启动中，正在等待进度信息...'
    );
  }
  if (!context.parentRunId && currentTool === providerId) {
    recordCurrentRecentInputs(providerId);
  }
  const owner = Symbol(providerId || 'provider-command');
  activeCommandOwner = owner;
  setProviderRunning(providerId, true);
  try {
    return await runTrackedPythonCommand(script, args, context, options);
  } finally {
    if (activeCommandOwner === owner) {
      activeCommandOwner = null;
      setProviderRunning(providerId, false);
    }
  }
}

function shouldTrackTask(title) {
  const text = String(title || '');
  if (/(保存|登录|读取|扫描|计划|配置|权限|知识库|文件夹)/.test(text)) return false;
  return /(导出|导入|上传)/.test(text);
}

function shouldTrackManifestAction(action) {
  if (typeof action?.track === 'boolean') return action.track;
  return ['import', 'export', 'upload'].includes(String(action?.kind || '').toLowerCase());
}

async function resumeTask(task) {
  if (!task) return;
  if (isRunning) {
    notifyUser('当前已有任务运行中，请等待结束或先停止当前任务。', 'warn');
    return;
  }
  if (!task.script || !Array.isArray(task.args)) {
    notifyUser('这条任务缺少可继续执行的命令参数。', 'warn');
    return;
  }
  const args = resumeTaskArgs(task);
  const provider = TOOLS[task.providerId] || {};
  const retryArg = providerRetryFailureArg(provider);
  const retryableFailures = taskFailureCount(task);
  const shouldRetry = window.WandaoTaskResume?.shouldRetryFailureItems;
  const retryingFailures = typeof shouldRetry === 'function'
    ? shouldRetry(task, retryArg, retryableFailures, provider)
    : Boolean(
      retryArg
      && !['stopped', 'interrupted'].includes(String(task?.status || '').toLowerCase())
      && !taskHasDeferredDocuments(task)
      && retryableFailures > 0
      && args.includes(retryArg)
    );
  const resumeSubject = taskResumeSubject(task);
  const confirmDetail = retryingFailures
    ? `将只重试上次报告中的失败项，共 ${retryableFailures} 个。`
    : '将按历史命令重新执行，适合增量任务或中断后继续。';
  if (!(await confirmUserAction(`继续任务：${resumeSubject}\n${confirmDetail}\n\n确认继续吗？`, {
    title: retryingFailures ? '确认重试失败项' : '确认继续任务',
    confirmLabel: retryingFailures ? '开始重试' : '继续任务'
  }))) {
    return;
  }
  if (task.providerId && TOOLS[task.providerId] && currentTool !== task.providerId) {
    if (!switchTool(task.providerId)) {
      notifyUser('暂时无法打开这条任务对应的平台页面，请稍后重试。', 'warn');
      return;
    }
  }
  startProgress(`继续任务：${resumeSubject}`, retryingFailures ? '正在读取上次报告并重试失败项...' : '正在按历史命令重新执行，脚本会根据自身增量能力跳过已完成内容。');
  log(retryingFailures ? `重试失败项：${resumeSubject}` : `继续任务：${resumeSubject}`, 'info');
  const isExportTask = /导出|export/i.test(String(task.action || ''));
  if (isExportTask && providerSupportsMarkdownPreview(provider)) {
    const outputPath = taskArtifactPaths(task).output
      || taskArgValue(args, provider.outputParam || '--output');
    window.WandaoMarkdownDock?.prepareLiveExport({
      outputPath,
      title: resumeSubject,
      originTool: task.providerId || currentTool
    });
  }
  try {
    const result = await runProviderCommand(task.script, args, {
      providerId: task.providerId || currentTool,
      title: retryingFailures ? `重试失败项：${resumeSubject}` : `继续任务：${resumeSubject}`,
      action: retryingFailures ? '重试失败项' : (task.action || '继续'),
      jobId: task.jobId || task.id,
      parentRunId: task.runId || task.id
    });
    const outcome = taskResultStatus(result, { provider: task.providerId, mode: task.action });
    if ((outcome === 'completed' || outcome === 'partial' || outcome === 'paused') && !isStoppedResult(result)) {
      appendExportSuccessSponsorLogs(outcome, task.action, taskReportForResult(result, { provider: task.providerId, mode: task.action }));
      appendExportResourceRecoveryLog(
        outcome,
        task.action,
        taskReportForResult(result, { provider: task.providerId, mode: task.action }),
        retryingFailures
      );
      if (result.data) log(JSON.stringify(result.data, null, 2), 'info');
      finishProgressForTaskResult(result, '历史任务继续执行完成', { provider: task.providerId, mode: task.action });
      logTaskResultCompletion(result, '历史任务继续执行完成', { provider: task.providerId, mode: task.action });
    } else if (outcome === 'stopped') {
      log('历史任务继续执行已停止，已完成项目会在下次继续时跳过。', 'warn');
      finishProgress('stopped', '历史任务继续执行已停止');
    } else {
      log(`历史任务继续执行失败：${result.error}`, 'error');
      finishProgress(false, '历史任务继续执行失败，请查看日志');
    }
  } catch (error) {
    log(`历史任务继续执行出错：${formatError(error)}`, 'error');
    finishProgress(false, '历史任务继续执行出错，请查看日志');
  } finally {
    if (isExportTask && providerSupportsMarkdownPreview(provider)) {
      window.WandaoMarkdownDock?.stopLiveExport();
    }
  }
}

function latestResumableTask() {
  return taskHistory.find(canResumeTask);
}

function setProviderRunning(providerId, running) {
  setRunning(running, providerId || currentTool);
}

function progressElements() {
  return {
    section: document.getElementById('progress-section'),
    title: document.getElementById('progress-title'),
    percent: document.getElementById('progress-percent'),
    fill: document.getElementById('progress-fill'),
    detail: document.getElementById('progress-detail'),
    elapsed: document.getElementById('progress-elapsed'),
    track: document.querySelector('#progress-section .progress-track')
  };
}

function formatProgressElapsed(ms) {
  const seconds = Math.max(0, Math.floor((Number(ms) || 0) / 1000));
  if (seconds < 60) return `已用时 ${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return remainder ? `已用时 ${minutes} 分 ${remainder} 秒` : `已用时 ${minutes} 分`;
}

function inferProgressPhase(title, detail, explicitPhase = '') {
  if (explicitPhase) return String(explicitPhase);
  const text = `${title || ''} ${detail || ''}`;
  if (/(读取|扫描|加载).*目录|目录.*(读取|扫描|加载)/.test(text)) return 'directory';
  if (/(登录|凭证|cookie|授权)/i.test(text)) return 'auth';
  if (/(导入|导出|上传|下载)/.test(text)) return 'transfer';
  return 'task';
}

function progressHeartbeatDetail(elapsedMs) {
  const elapsed = formatProgressElapsed(elapsedMs);
  const base = progressBaseDetail || '任务正在处理中';
  const seconds = Math.max(0, Math.floor((Number(elapsedMs) || 0) / 1000));
  if (progressPhase === 'directory') {
    if (seconds < 5) return `${base}（${elapsed}）`;
    if (/已发现\s*\d+\s*个节点/.test(base)) return `${base}（${elapsed}，仍在整理目录结构）`;
    if (seconds < 15) return `正在连接远端服务并读取目录结构（${elapsed}）`;
    return `仍在读取远端目录，远端暂未返回细分进度（${elapsed}）`;
  }
  if (seconds < 8) return `${base}（${elapsed}）`;
  if (seconds < 20) return `${base}（${elapsed}，任务仍在进行）`;
  return `${base}（${elapsed}，任务仍在进行，请稍候）`;
}

function refreshProgressFeedback({ refreshDetail = true } = {}) {
  if (!progressVisible || !progressStartedAt) return;
  const els = progressElements();
  const elapsedMs = Date.now() - progressStartedAt;
  if (els.elapsed) els.elapsed.textContent = formatProgressElapsed(elapsedMs);
  if (refreshDetail && !progressHasRealProgress && els.detail) {
    els.detail.textContent = progressHeartbeatDetail(elapsedMs);
  }
}

function stopProgressHeartbeat() {
  if (progressHeartbeatTimer) {
    window.clearInterval(progressHeartbeatTimer);
    progressHeartbeatTimer = null;
  }
}

function startProgress(title, detail = '任务启动中，正在等待进度信息...', options = {}) {
  const els = progressElements();
  if (!els.section) return;
  stopProgressHeartbeat();
  progressVisible = true;
  progressStartedAt = Date.now();
  progressBaseDetail = detail || '任务正在处理中';
  progressPhase = inferProgressPhase(title, detail, options.phase);
  progressHasRealProgress = false;
  pythonProgressBuffer = '';
  pythonLogSummaryBuffer = '';
  els.section.hidden = false;
  els.section.setAttribute('aria-busy', 'true');
  els.title.textContent = title || '任务进行中';
  els.percent.textContent = '进行中';
  els.fill.className = 'progress-fill indeterminate';
  els.fill.style.width = '';
  els.detail.textContent = progressBaseDetail;
  if (els.elapsed) els.elapsed.textContent = '已用时 0 秒';
  els.track?.removeAttribute('aria-valuenow');
  els.track?.removeAttribute('aria-valuetext');
  progressHeartbeatTimer = window.setInterval(() => refreshProgressFeedback(), 1000);
  if (activeHistoryTask) {
    activeHistoryTask.progress = { current: 0, total: 0, detail: progressBaseDetail };
    renderTaskStatusOrb();
  }
  setLogCollapsed(false);
}

function updateProgress(done, total, detail = '') {
  const els = progressElements();
  if (!els.section) return;
  const safeTotal = Math.max(0, Number(total) || 0);
  const safeDone = Math.max(0, Number(done) || 0);
  if (!progressVisible) startProgress('任务进行中');
  if (detail) progressBaseDetail = detail;
  progressHasRealProgress = safeTotal > 0;
  if (!safeTotal) {
    els.percent.textContent = '进行中';
    els.fill.className = 'progress-fill indeterminate';
    els.fill.style.width = '';
    els.track?.removeAttribute('aria-valuenow');
    els.track?.setAttribute('aria-valuetext', detail || '任务进行中');
    if (detail) els.detail.textContent = detail;
    if (activeHistoryTask) {
      activeHistoryTask.progress = { current: safeDone, total: 0, detail: detail || progressBaseDetail || '任务进行中' };
      renderTaskStatusOrb();
    }
    window.WandaoMarkdownDock?.updateLiveStatus(safeDone, safeTotal, detail);
    refreshProgressFeedback({ refreshDetail: false });
    return;
  }
  const ratio = Math.min(1, safeDone / safeTotal);
  const percent = Math.max(0, Math.min(100, Math.round(ratio * 100)));
  els.percent.textContent = `${percent}%`;
  els.fill.className = 'progress-fill';
  els.fill.style.width = `${percent}%`;
  els.track?.setAttribute('aria-valuenow', String(percent));
  const progressDetail = detail || `已处理 ${safeDone}/${safeTotal}`;
  els.track?.setAttribute('aria-valuetext', progressDetail);
  els.detail.textContent = progressDetail;
  if (activeHistoryTask) {
    activeHistoryTask.progress = { current: safeDone, total: safeTotal, detail: progressDetail };
    renderTaskStatusOrb();
  }
  window.WandaoMarkdownDock?.updateLiveStatus(safeDone, safeTotal, progressDetail);
  refreshProgressFeedback({ refreshDetail: false });
}

function finishProgress(outcome, detail) {
  const els = progressElements();
  const elapsedMs = progressStartedAt ? Date.now() - progressStartedAt : 0;
  stopProgressHeartbeat();
  if (!els.section) return;
  if (!progressVisible) {
    els.section.hidden = false;
  }
  const status = outcome === true ? 'completed' : (outcome === false ? 'failed' : String(outcome || 'failed'));
  const config = {
    completed: { label: '100%', className: 'success', ariaValue: '100', fallback: '任务已完成' },
    partial: { label: '部分完成', className: 'partial', ariaValue: '', fallback: '任务部分完成，请查看失败项' },
    attention: { label: '需操作', className: 'partial', ariaValue: '', fallback: '还需要完成后续操作' },
    stopped: { label: '已停止', className: 'stopped', ariaValue: '', fallback: '任务已停止，可在任务历史继续' },
    paused: { label: '因风控暂停', className: 'partial', ariaValue: '', fallback: '任务因频率限制暂停，可在任务历史继续' },
    failed: { label: '失败', className: 'error', ariaValue: '', fallback: '任务失败，请查看运行日志' }
  }[status] || { label: '失败', className: 'error', ariaValue: '', fallback: '任务失败，请查看运行日志' };
  progressVisible = false;
  progressStartedAt = 0;
  progressBaseDetail = '';
  progressPhase = 'task';
  progressHasRealProgress = false;
  els.section.setAttribute('aria-busy', 'false');
  els.percent.textContent = config.label;
  els.fill.className = 'progress-fill ' + config.className;
  els.fill.style.width = '100%';
  if (config.ariaValue) els.track?.setAttribute('aria-valuenow', config.ariaValue);
  else els.track?.removeAttribute('aria-valuenow');
  els.track?.setAttribute('aria-valuetext', detail || config.fallback);
  els.detail.textContent = detail || config.fallback;
  if (els.elapsed) els.elapsed.textContent = elapsedMs ? formatProgressElapsed(elapsedMs) : '已用时 0 秒';
  window.setTimeout(() => {
    if (!progressVisible) els.section.hidden = true;
  }, 260);
  renderTaskStatusOrb();
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
    log(`加载社区平台插件失败：${result?.error || '未知错误'}`, 'error');
    return;
  }
  const manifests = Array.isArray(result.providers) ? result.providers : [];
  const manifestErrors = Array.isArray(result.errors) ? result.errors : [];
  manifestErrors.forEach((message) => appendDetailedLog('provider', 'error', message));
  if (manifestErrors.length) {
    appendUserLog(`有 ${manifestErrors.length} 个本地平台配置无效，已安全忽略。详情请查看详细日志。`, 'warn');
  }
  PROVIDER_REGISTRY.replaceExternal(manifests);
  refreshProviderTools();
  const plugins = [...new Map(manifests
    .filter((provider) => provider?.pluginId)
    .map((provider) => [provider.pluginId, provider.pluginVersion || '未知']))
    .entries()]
    .map(([id, version]) => `${id} v${version}`);
  appendDetailedLog(
    'provider',
    'info',
    `已加载 ${manifests.length} 个外部 Provider。插件版本：${plugins.join('，') || '无'}`
  );
}

function renderProviderSafetyNotice(provider) {
  if (!window.WandaoProviderRuntime?.shouldConfirmExecution(provider)) return '';
  const title = window.WandaoProviderRuntime.executionWarningTitle(provider);
  const source = window.WandaoProviderRuntime.sourceText(provider);
  return `
    <div class="info-box provider-safety-notice">
      <strong>${escapeHtml(title)}</strong>
      <p>这个平台插件来自${escapeHtml(source)}，执行动作时会在本机运行脚本。请确认来源可信，不要运行陌生人提供的未知脚本。</p>
    </div>
  `;
}

async function confirmProviderExecution(provider, action = null) {
  if (!window.WandaoProviderRuntime?.shouldConfirmExecution(provider, action)) return true;
  return confirmUserAction(window.WandaoProviderRuntime.executionConfirmMessage(provider), {
    title: '确认运行本地插件脚本',
    confirmLabel: '运行脚本',
    danger: true
  });
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

function providerSupportsMarkdownPreview(provider) {
  if (!provider?.capabilities?.export || !provider?.capabilities?.tree) return false;
  const hasDirectoryOutput = Array.isArray(provider.fields)
    && provider.fields.some((field) => field?.name === 'output' && field?.type === 'directory');
  const hasSelectableTree = Boolean(provider.toc?.itemsPath && provider.toc?.selectionArg);
  return hasDirectoryOutput && hasSelectableTree;
}

function markdownPreviewOutputPath(provider, args) {
  const outputFlag = provider?.outputParam || '--output';
  const values = Array.isArray(args) ? args : [];
  const index = values.findIndex((value) => value === outputFlag);
  return index >= 0 ? String(values[index + 1] || '').trim() : '';
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

function platformPluginUpdateCandidates(group) {
  const helper = window.WandaoPluginUpdates?.platformUpdateCandidates;
  if (typeof helper === 'function') return helper(group, pluginCatalogState.plugins);
  const pluginIds = new Set((group?.providers || []).map((provider) => String(provider?.pluginId || '').trim()).filter(Boolean));
  return pluginUpdateCandidates().filter((plugin) => pluginIds.has(String(plugin.id || '').trim()));
}

function platformUpdateBadgeHtml(updates) {
  if (!updates.length) return '';
  const label = updates.length === 1 ? '插件可更新' : `${updates.length} 个插件可更新`;
  const title = updates.map((plugin) => plugin.name || plugin.id).join('、');
  return `<span class="platform-update-badge" title="${escapeHtml(title)}" aria-label="${escapeHtml(label)}"><span aria-hidden="true">↑</span>${escapeHtml(label)}</span>`;
}

function platformUpdateActionHtml(group, updates) {
  if (!updates.length) return '';
  const updating = updates.some((plugin) => pluginOperationState.has(plugin.id));
  const catalogLoading = pluginCatalogState.status === 'loading';
  const label = updating
    ? '正在更新…'
    : (catalogLoading ? '检查更新中…' : (updates.length === 1 ? '更新插件' : `更新插件（${updates.length}）`));
  const title = updates.map((plugin) => `${plugin.name || plugin.id} ${plugin.installedVersion ? `v${plugin.installedVersion} → ` : ''}v${plugin.version || '最新'}`).join('；');
  return `<button class="btn-primary platform-update-action" data-platform-update="${escapeHtml(group.key)}" type="button" title="${escapeHtml(title)}" ${updating || catalogLoading ? 'disabled' : ''}>${escapeHtml(label)}</button>`;
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
    reader: '<path d="M6 3.5h9.5L19 7v13.5H6a2 2 0 0 1-2-2v-13a2 2 0 0 1 2-2Z"/><path d="M15 3.5V8h4M8 12h8M8 15.5h8"/>',
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

function loadSidebarState() {
  try {
    sidebarCollapsed = localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY) === 'true';
    const savedWidth = Number(localStorage.getItem(SIDEBAR_WIDTH_STORAGE_KEY));
    if (Number.isFinite(savedWidth)) sidebarWidth = clampSidebarWidth(savedWidth);
  } catch (_) {
    sidebarCollapsed = false;
  }
  applySidebarLayout();
}

function clampSidebarWidth(value) {
  const viewportMax = Math.max(MIN_SIDEBAR_WIDTH, Math.floor(window.innerWidth * 0.36));
  return Math.max(MIN_SIDEBAR_WIDTH, Math.min(MAX_SIDEBAR_WIDTH, viewportMax, Number(value) || DEFAULT_SIDEBAR_WIDTH));
}

function applySidebarLayout() {
  sidebarWidth = clampSidebarWidth(sidebarWidth);
  document.documentElement.style.setProperty(
    '--sidebar-width',
    `${sidebarCollapsed ? COLLAPSED_SIDEBAR_WIDTH : sidebarWidth}px`
  );
  document.body.classList.toggle('sidebar-collapsed', sidebarCollapsed);
}

function saveSidebarState() {
  try {
    localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, sidebarCollapsed ? 'true' : 'false');
    localStorage.setItem(SIDEBAR_WIDTH_STORAGE_KEY, String(sidebarWidth));
  } catch (_) {
    // A restricted localStorage must not prevent navigation from working.
  }
}

function toggleSidebar() {
  sidebarCollapsed = !sidebarCollapsed;
  applySidebarLayout();
  saveSidebarState();
  renderProviderNavigation();
}

function startSidebarResize(event) {
  const handle = event.target.closest('[data-sidebar-resizer]');
  if (!handle) return false;
  sidebarResizeSession = {
    pointerId: event.pointerId,
    startX: event.clientX,
    startWidth: sidebarCollapsed ? COLLAPSED_SIDEBAR_WIDTH : sidebarWidth,
    handle
  };
  handle.setPointerCapture?.(event.pointerId);
  handle.classList.add('is-dragging');
  event.preventDefault();
  return true;
}

function moveSidebarResize(event) {
  const session = sidebarResizeSession;
  if (!session || session.pointerId !== event.pointerId) return false;
  const nextWidth = session.startWidth + event.clientX - session.startX;
  if (nextWidth <= SIDEBAR_COLLAPSE_SNAP_WIDTH) {
    sidebarCollapsed = true;
  } else {
    sidebarCollapsed = false;
    sidebarWidth = clampSidebarWidth(nextWidth);
  }
  applySidebarLayout();
  session.handle.setAttribute('aria-valuenow', String(sidebarCollapsed ? COLLAPSED_SIDEBAR_WIDTH : sidebarWidth));
  event.preventDefault();
  return true;
}

function finishSidebarResize(event) {
  const session = sidebarResizeSession;
  if (!session || session.pointerId !== event.pointerId) return false;
  session.handle.classList.remove('is-dragging');
  sidebarResizeSession = null;
  applySidebarLayout();
  saveSidebarState();
  renderProviderNavigation();
  return true;
}

function handleSidebarResizeKeydown(event) {
  const handle = event.target.closest('[data-sidebar-resizer]');
  if (!handle) return;
  const step = event.shiftKey ? 48 : 20;
  if (event.key === 'ArrowLeft') {
    if (sidebarCollapsed) return;
    if (sidebarWidth - step <= SIDEBAR_COLLAPSE_SNAP_WIDTH) sidebarCollapsed = true;
    else sidebarWidth = clampSidebarWidth(sidebarWidth - step);
  } else if (event.key === 'ArrowRight') {
    if (sidebarCollapsed) {
      sidebarCollapsed = false;
      sidebarWidth = clampSidebarWidth(DEFAULT_SIDEBAR_WIDTH);
    } else {
      sidebarWidth = clampSidebarWidth(sidebarWidth + step);
    }
  } else {
    return;
  }
  event.preventDefault();
  applySidebarLayout();
  saveSidebarState();
  renderProviderNavigation();
}

function renderProviderNavigation() {
  const sidebar = document.getElementById('provider-sidebar') || document.querySelector('.sidebar');
  if (!sidebar) return;
  const activeId = primaryNavIdFor();
  sidebar.innerHTML = `
    <div class="sidebar-toolbar">
      <span class="sidebar-toolbar-label">主导航</span>
      <button class="sidebar-collapse-toggle" data-sidebar-toggle type="button" aria-label="${sidebarCollapsed ? '展开主导航' : '收起主导航'}" title="${sidebarCollapsed ? '展开主导航' : '收起主导航'}">
        <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="4" width="16" height="16" rx="2"/><path d="M9 4v16M13 9h4M13 12h4M13 15h4"/></svg>
      </button>
    </div>
    <div class="sidebar-intro">
      <span>知识迁移</span>
      <strong>从这里开始</strong>
    </div>
      <nav class="nav-group" aria-label="工作台">
      <span class="nav-group-label">工作台</span>
      ${PRIMARY_NAV_ITEMS.map((item) => `
        <button class="nav-item ${item.id === activeId ? 'active' : ''}" data-tool="${escapeHtml(item.id)}" type="button" aria-label="${escapeHtml(item.label)}" title="${escapeHtml(item.label)}" ${item.id === activeId ? 'aria-current="page"' : ''}>
          ${navigationIcon(item.icon)}
          <span class="nav-copy">
            <strong>${escapeHtml(item.label)}</strong>
            <small>${escapeHtml(item.description)}</small>
          </span>
        </button>
      `).join('')}
      </nav>
    <div class="sidebar-footnote">本地优先 · Markdown 归档</div>
    <div class="sidebar-resizer" data-sidebar-resizer role="separator" aria-orientation="vertical" tabindex="0" aria-label="拖动调整主导航宽度" aria-valuemin="156" aria-valuemax="320" aria-valuenow="${sidebarCollapsed ? COLLAPSED_SIDEBAR_WIDTH : sidebarWidth}"></div>
  `;
}

function bindWorkbenchActions(root = document.getElementById('content-area')) {
  if (!root) return;
  root.querySelectorAll('[data-switch-view]').forEach((button) => {
    button.addEventListener('click', () => switchTool(button.dataset.switchView));
  });
  root.querySelectorAll('[data-platform-key]').forEach((button) => {
    button.addEventListener('click', () => {
      if (!isRunning) switchTool(`platform:${button.dataset.platformKey}`);
    });
  });
  root.querySelectorAll('[data-platform-update]').forEach((button) => {
    button.addEventListener('click', () => runPlatformPluginUpdate(button.dataset.platformUpdate, button));
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
      .map((item, index) => {
        const noticeType = String(item.type || '');
        return {
          id: String(item.id || `notice-${index}`),
          type: noticeType === 'tutorial' || noticeType === 'sponsor' ? noticeType : 'announcement',
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
        };
      })
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
  const announcements = items.filter((item) => item.type === 'announcement');
  const sponsors = items.filter((item) => item.type === 'sponsor');
  const tutorials = items.filter((item) => item.type === 'tutorial');
  return { announcements, sponsors, tutorials };
}

function defaultNoticeId(items = noticeItems()) {
  const groups = noticeGroups(items);
  return groups.announcements[0]?.id || groups.tutorials[0]?.id || groups.sponsors[0]?.id || items[0]?.id || '';
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
  if (item.type === 'sponsor') return item.badge || '赞助商';
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
  const updates = platformPluginUpdateCandidates(group);
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
        ${updates.length ? `<div class="platform-card-status">${platformUpdateBadgeHtml(updates)}</div>` : ''}
      </div>
      <div class="platform-card-actions">
        ${platformUpdateActionHtml(group, updates)}
        <button class="btn-secondary card-action" data-platform-key="${escapeHtml(group.key)}" type="button">查看操作 <span aria-hidden="true">→</span></button>
      </div>
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
    notifyUser('当前版本暂不支持保存浏览器设置。', 'warn');
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
    notifyError(error, { title: '保存浏览器设置失败' });
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
      notifyError(new Error(message), { title: '浏览器选择失败' });
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
          <select id="settings-browser-select" ${appSettingsState.browserDetectStatus === 'loading' ? 'disabled aria-disabled="true" title="浏览器检测完成后可修改"' : ''}>
            ${renderBrowserOptions()}
          </select>
          <p class="field-hint">${escapeHtml(browserSelectionSummary())}</p>
        </div>
        ${renderBrowserList()}
        <div class="settings-actions">
          <button class="btn-primary" id="settings-browser-save" data-settings-action="save-browser" type="button" ${appSettingsState.browserDetectStatus === 'loading' ? 'disabled' : ''}>保存选择</button>
          <button class="btn-secondary" data-settings-action="detect-browser" type="button" ${appSettingsState.browserDetectStatus === 'loading' ? 'disabled' : ''}>重新检测</button>
          <button class="btn-secondary" data-settings-action="choose-browser" type="button" ${appSettingsState.browserDetectStatus === 'loading' ? 'disabled' : ''}>手动选择浏览器</button>
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
        <p>同时检查万能导与平台插件的新版本。</p>
        <button class="btn-secondary" data-settings-action="check-update" type="button">检查更新</button>
      </article>
      <article class="settings-card settings-card-compact">
        <span class="card-eyebrow">诊断</span>
        <h4>日志显示</h4>
        <p data-settings-log-mode-summary>当前显示：${logViewMode === 'detail' ? '详细日志' : '用户日志'}</p>
        <button class="btn-secondary" data-settings-action="log-mode" type="button">切换日志</button>
      </article>
      <article class="settings-card settings-card-compact">
        <span class="card-eyebrow">隐私与输入</span>
        <h4>表单记忆</h4>
        <p>清除各插件最近 3 条输入和未提交草稿；不会删除登录凭证、API 配置或任务历史。</p>
        <button class="btn-secondary" data-settings-action="clear-form-memory" type="button">清除表单记忆</button>
      </article>
      <article class="settings-card settings-card-compact">
        <span class="card-eyebrow">帮助</span>
        <h4>快速教程</h4>
        <p>了解登录、导入、导出和图片处理方式。</p>
        <button class="btn-secondary" data-settings-action="tutorial" type="button">查看快速教程</button>
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
  contentArea.querySelector('[data-settings-action="tutorial"]')?.addEventListener('click', () => {
    switchTool('notice-center');
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
    <section class="task-center-summary" aria-label="任务概览">
      <div class="task-center-summary-copy">
        <span class="view-kicker">任务记录</span>
        <strong>${taskHistory.length}</strong><span>条任务</span>
        <small>${resumableCount ? `${resumableCount} 条可继续或重试` : '任务记录保存在本机'}</small>
      </div>
      <button class="btn-secondary" data-switch-view="platform-center" type="button">开始新任务</button>
    </section>
  `;
  setTaskHistoryVisible(true);
  renderTaskHistory();
  bindWorkbenchActions(contentArea);
}

function renderMarkdownReaderPage() {
  setTaskHistoryVisible(false);
  setToolHeading('Markdown 阅读器', '打开本地 Markdown 文件夹，按目录层次阅读文档。');
  const contentArea = document.getElementById('content-area');
  contentArea.innerHTML = '';
  window.WandaoMarkdownDock?.renderReaderPage(contentArea);
}

async function requestNoticeImage(imageUrl) {
  const safeUrl = safeNoticeImageUrl(imageUrl);
  if (!safeUrl) {
    return { success: false, errorMessage: '公告图片地址不在允许的 GitHub 文档范围内' };
  }
  try {
    const result = await window.electronAPI.fetchRemoteImage(safeUrl);
    if (!result?.success || !result.dataUrl) {
      throw new Error(result?.error || '公告图片读取失败');
    }
    return { success: true, result };
  } catch (error) {
    return { success: false, errorMessage: error?.message || String(error) };
  }
}

function replaceWithNoticeImageFallback(image, imageUrl, errorMessage) {
  const placeholder = document.createElement('div');
  placeholder.className = 'guide-image-fallback';
  placeholder.setAttribute('role', 'status');
  placeholder.title = errorMessage || '公告图片读取失败';

  const title = document.createElement('strong');
  title.textContent = image.alt ? `${image.alt}暂时无法加载` : '公告图片暂时无法加载';
  placeholder.appendChild(title);

  const detail = document.createElement('span');
  detail.textContent = '请检查网络连接，公告文字仍可继续阅读。';
  placeholder.appendChild(detail);

  const retryButton = document.createElement('button');
  retryButton.type = 'button';
  retryButton.className = 'guide-image-retry';
  retryButton.title = '重新加载这张图片';
  retryButton.setAttribute('aria-label', '重新加载这张公告图片');
  const retryIcon = document.createElement('span');
  retryIcon.className = 'guide-image-retry-icon';
  retryIcon.textContent = '\u21bb';
  retryIcon.setAttribute('aria-hidden', 'true');
  retryButton.appendChild(retryIcon);
  retryButton.addEventListener('click', async () => {
    retryButton.disabled = true;
    retryButton.classList.add('is-loading');
    const outcome = await requestNoticeImage(imageUrl);
    if (outcome.success) {
      image.src = outcome.result.dataUrl;
      placeholder.replaceWith(image);
      return;
    }
    placeholder.title = outcome.errorMessage;
    retryButton.disabled = false;
    retryButton.classList.remove('is-loading');
  });
  placeholder.appendChild(retryButton);

  const openButton = document.createElement('button');
  openButton.type = 'button';
  openButton.className = 'guide-image-fallback-link';
  openButton.textContent = '在 GitHub 查看原图';
  openButton.addEventListener('click', () => window.electronAPI.openExternal(imageUrl));
  placeholder.appendChild(openButton);
  image.replaceWith(placeholder);
}

async function hydrateNoticeImages(container) {
  const images = Array.from(container?.querySelectorAll?.('img[data-notice-image]') || []);
  const pending = images.map((image) => ({
    image,
    imageUrl: image.dataset.noticeImage || ''
  }));
  const loadNext = async () => {
    while (pending.length) {
      const { image, imageUrl } = pending.shift();
      const outcome = await requestNoticeImage(imageUrl);
      if (outcome.success) {
        image.src = outcome.result.dataUrl;
        image.removeAttribute('data-notice-image');
      } else if (image.isConnected) {
        replaceWithNoticeImageFallback(image, imageUrl, outcome.errorMessage);
      }
    }
  };
  const workerCount = Math.min(3, pending.length);
  await Promise.all(Array.from({ length: workerCount }, () => loadNext()));
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
  return markdownToHtml(source, {
    resolveImageSource: (imageSource) => resolveNoticeImageSource(imageSource, selected),
    allowRemoteImage: safeNoticeImageUrl,
    remoteImageAttribute: 'data-notice-image'
  });
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
        ${renderNoticeListSection('赞助商', groups.sponsors, '暂无赞助商信息。')}
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
  mountMarkdownPreviews(contentArea).then(() => {
    hydrateNoticeImages(contentArea);
    bindRenderedMarkdownLinks(contentArea);
  });
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

function formatPluginBytes(bytes) {
  const value = Math.max(0, Number(bytes) || 0);
  if (value < 1024) return `${value} B`;
  const units = ['KiB', 'MiB', 'GiB'];
  let size = value / 1024;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size >= 10 ? size.toFixed(0) : size.toFixed(1)} ${units[unit]}`;
}

function pluginProgressDetails(operation) {
  if (!operation) return null;
  if (operation.phase === 'verifying') return { text: '下载完成，正在校验签名和完整性…', percent: 100, indeterminate: false };
  if (operation.phase === 'preparing') return { text: '正在连接插件库…', percent: 0, indeterminate: true };
  const receivedBytes = Math.max(0, Number(operation.receivedBytes) || 0);
  const totalBytes = Math.max(0, Number(operation.totalBytes) || 0);
  if (totalBytes > 0) {
    const percent = Math.min(100, Math.round(receivedBytes / totalBytes * 100));
    return { text: `正在下载 ${formatPluginBytes(receivedBytes)} / ${formatPluginBytes(totalBytes)}（${percent}%）`, percent, indeterminate: false };
  }
  return { text: `正在下载 ${formatPluginBytes(receivedBytes)}`, percent: 0, indeterminate: true };
}

function pluginProgressHtml(pluginId) {
  const details = pluginProgressDetails(pluginOperationState.get(pluginId));
  if (!details) return '';
  return `<div class="plugin-download-progress" data-plugin-progress="${escapeHtml(pluginId)}" role="status" aria-live="polite">
    <div class="plugin-download-progress-track"><span class="${details.indeterminate ? 'is-indeterminate' : ''}" data-progress-percent="${details.indeterminate ? 42 : details.percent}"></span></div>
    <span>${escapeHtml(details.text)}</span>
  </div>`;
}

function applyPluginProgressStyle(element) {
  if (!element) return;
  const indicator = element.querySelector('.plugin-download-progress-track span');
  const details = pluginProgressDetails(pluginOperationState.get(element.dataset.pluginProgress));
  if (!indicator || !details) return;
  indicator.style.setProperty('--plugin-progress-width', `${details.indeterminate ? 42 : details.percent}%`);
}

function hydratePluginProgressUi(root = document) {
  root.querySelectorAll?.('[data-plugin-progress]').forEach(applyPluginProgressStyle);
}

function refreshPluginProgressUi(pluginId) {
  document.querySelectorAll(`[data-plugin-progress="${pluginId}"]`).forEach((element) => {
    element.outerHTML = pluginProgressHtml(pluginId);
    applyPluginProgressStyle(document.querySelector(`[data-plugin-progress="${pluginId}"]`));
  });
}

function pluginUpdateCandidates() {
  const helper = window.WandaoPluginUpdates?.availablePluginUpdates;
  if (typeof helper === 'function') return helper(pluginCatalogState.plugins);
  return pluginCatalogState.plugins.filter((plugin) => plugin.updateAvailable && plugin.compatibility?.compatible !== false);
}

function runningTaskProviderId() {
  return String(activeHistoryTask?.providerId || mainPythonProcessState.providerId || '').trim();
}

function runningTaskPluginId() {
  const providerId = runningTaskProviderId();
  return String(TOOLS[providerId]?.pluginId || '').trim();
}

function pluginOperationBlocked(pluginId) {
  const runningPluginId = runningTaskPluginId();
  return Boolean(
    (isRunning || mainPythonProcessState.running)
    && runningPluginId
    && String(pluginId || '').trim() === runningPluginId
  );
}

function pluginOperationBlockedTitle(pluginId) {
  const providerId = runningTaskProviderId();
  const providerTitle = TOOLS[providerId]?.title || providerId || '当前平台';
  return `当前正在使用“${providerTitle}”插件，任务结束后才能操作。`;
}

function pluginOperationAttributes(pluginId) {
  return pluginOperationBlocked(pluginId)
    ? `disabled aria-disabled="true" title="${escapeHtml(pluginOperationBlockedTitle(pluginId))}"`
    : '';
}

function renderPluginCard(plugin) {
  const permissionTags = pluginPermissionTags(plugin);
  const compatible = plugin.compatibility?.compatible !== false;
  const operationRunning = pluginOperationState.has(plugin.id);
  const operationAttributes = pluginOperationAttributes(plugin.id);
  const primary = operationRunning
    ? `<button class="btn-primary" type="button" disabled>${plugin.installed ? '正在更新…' : '正在安装…'}</button>`
    : plugin.bundled && !plugin.installed && !plugin.updateAvailable
    ? '<span class="plugin-status">已随主程序提供</span>'
    : plugin.bundled && !plugin.installed
      ? `<button class="btn-primary" data-plugin-action="install" data-plugin-id="${escapeHtml(plugin.id)}" type="button" ${compatible ? operationAttributes : 'disabled aria-disabled="true"'}>安装更新</button>`
      : !plugin.installed
    ? `<button class="btn-primary" data-plugin-action="install" data-plugin-id="${escapeHtml(plugin.id)}" type="button" ${compatible ? operationAttributes : 'disabled aria-disabled="true"'}>安装</button>`
    : (plugin.updateAvailable
      ? `<button class="btn-primary" data-plugin-action="install" data-plugin-id="${escapeHtml(plugin.id)}" type="button" ${compatible ? operationAttributes : 'disabled aria-disabled="true"'}>更新</button>`
      : `<button class="btn-secondary" data-plugin-action="toggle" data-plugin-id="${escapeHtml(plugin.id)}" data-enabled="${plugin.enabled ? 'false' : 'true'}" type="button" ${operationAttributes}>${plugin.enabled ? '停用' : '启用'}</button>`);
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
      ${pluginProgressHtml(plugin.id)}
      <div class="plugin-card-actions">
        ${primary}
        ${plugin.installed && (plugin.previousVersions || []).length ? `<button class="btn-text" data-plugin-action="rollback" data-plugin-id="${escapeHtml(plugin.id)}" type="button" ${operationAttributes}>回滚</button>` : ''}
        ${plugin.installed ? `<button class="btn-text danger-text" data-plugin-action="uninstall" data-plugin-id="${escapeHtml(plugin.id)}" type="button" ${operationAttributes}>卸载</button>` : ''}
      </div>
    </article>
  `;
}

async function loadPluginCatalog(refresh = false) {
  if (!window.electronAPI.getPluginCatalog) {
    return { success: false, unsupported: true, error: '当前版本暂不支持在线检查插件更新。' };
  }
  const requestId = ++pluginCatalogRequestId;
  const previousPlugins = pluginCatalogState.plugins;
  pluginCatalogState = { ...pluginCatalogState, status: 'loading', error: '' };
  renderPluginCatalogViews();
  try {
    const result = await window.electronAPI.getPluginCatalog({ refresh });
    if (requestId !== pluginCatalogRequestId) return { success: false, stale: true };
    const success = result?.success === true;
    const error = result?.registryError || result?.error || '';
    pluginCatalogState = {
      status: success ? 'ready' : 'error',
      plugins: success && Array.isArray(result?.plugins) ? result.plugins : previousPlugins,
      query: pluginCatalogState.query,
      error,
      offline: Boolean(result?.offline),
      experimentalError: result?.experimentalError || '',
      updatedAt: result?.registryUpdatedAt || pluginCatalogState.updatedAt || ''
    };
    renderPluginCatalogViews();
    return { success, offline: Boolean(result?.offline), error };
  } catch (error) {
    if (requestId !== pluginCatalogRequestId) return { success: false, stale: true };
    const message = formatError(error);
    pluginCatalogState = {
      ...pluginCatalogState,
      status: 'error',
      plugins: previousPlugins,
      error: message,
      offline: false
    };
    renderPluginCatalogViews();
    return { success: false, error: message };
  }
}

async function refreshPluginCatalogFromUi() {
  const result = await loadPluginCatalog(true);
  if (result?.success) {
    notifyUser(result.offline ? '已读取本机插件状态；在线插件库暂不可用。' : '插件库已刷新。', result.offline ? 'warn' : 'success');
  } else if (!result?.stale) {
    notifyError(new Error(result?.error || '刷新插件库失败'), { title: '刷新插件库失败' });
  }
  return result;
}

function renderPluginCatalogViews() {
  if (currentTool === 'plugin-center') renderPluginCenterPage();
  if (currentTool === 'platform-center') renderPlatformCenterPage();
}

async function refreshProvidersAfterPluginChange() {
  await loadProviderManifests();
  renderProviderNavigation();
}

async function installPluginFromCatalog(plugin) {
  if (!plugin?.id) throw new Error('插件信息不完整，无法执行安装或更新。');
  if (pluginOperationBlocked(plugin.id)) {
    throw new Error(pluginOperationBlockedTitle(plugin.id));
  }
  pluginOperationState.set(plugin.id, { phase: 'preparing', receivedBytes: 0, totalBytes: 0 });
  renderPluginCatalogViews();
  try {
    const result = await window.electronAPI.installPlugin(plugin.id, plugin.channel || 'stable');
    if (!result?.success) throw new Error(result?.error || '插件操作失败');
    pluginOperationState.set(plugin.id, { phase: 'verifying', receivedBytes: 0, totalBytes: 0 });
    if (currentTool === 'plugin-center') refreshPluginProgressUi(plugin.id);
    return result;
  } finally {
    pluginOperationState.delete(plugin.id);
    renderPluginCatalogViews();
  }
}

async function runPlatformPluginUpdate(groupKey, button) {
  const group = findPlatformGroup(groupKey);
  if (!group) return;
  const allCandidates = platformPluginUpdateCandidates(group);
  const candidates = allCandidates.filter((plugin) => !pluginOperationBlocked(plugin.id));
  if (allCandidates.length !== candidates.length) {
    log(`当前任务正在使用 ${allCandidates.length - candidates.length} 个待更新插件，已跳过。`, 'warn');
  }
  if (!candidates.length) {
    await loadPluginCatalog(true);
    return;
  }
  const originalButtonText = button.textContent;
  button.disabled = true;
  button.textContent = candidates.length === 1 ? '正在更新…' : `正在更新 0/${candidates.length}…`;
  const failures = [];
  let updatedCount = 0;
  try {
    for (const plugin of candidates) {
      if (candidates.length > 1) button.textContent = `正在更新 ${updatedCount + 1}/${candidates.length}…`;
      try {
        await installPluginFromCatalog(plugin);
        updatedCount += 1;
        log(`插件已更新：${plugin.name || plugin.id}`, 'success');
      } catch (error) {
        failures.push(`${plugin.name || plugin.id}：${formatError(error)}`);
        log(`插件更新失败：${plugin.name || plugin.id}：${formatError(error)}`, 'error');
      }
    }
    if (updatedCount) await refreshProvidersAfterPluginChange();
    await loadPluginCatalog(true);
    if (failures.length) {
      pluginOperationNotice = {
        type: 'warning',
        title: `${group.name} 更新部分完成`,
        message: failures.join('\n')
      };
      notifyUser(`“${group.name}”插件更新完成，但 ${failures.length} 个失败：\n${failures.join('\n')}`, 'warn', {
        title: '部分插件更新失败',
        duration: 0
      });
    } else if (updatedCount) {
      pluginOperationNotice = null;
      log(`“${group.name}”的插件已更新`, 'success');
    }
  } catch (error) {
    log(`插件更新失败：${formatError(error)}`, 'error');
    notifyError(error, { title: '插件更新失败' });
  } finally {
    button.disabled = false;
    button.textContent = originalButtonText;
    if (currentTool === 'platform-center') renderPlatformCenterPage();
  }
}

async function runPluginCenterAction(action, pluginId, button) {
  if (pluginOperationBlocked(pluginId)) {
    notifyUser(pluginOperationBlockedTitle(pluginId), 'warn');
    return;
  }
  button.disabled = true;
  const originalButtonText = button.textContent;
  if (action === 'uninstall') button.textContent = '卸载中…';
  try {
    let result;
    const plugin = pluginCatalogState.plugins.find((item) => item.id === pluginId);
    if (action === 'install') {
      const permissions = pluginPermissionTags(plugin || {});
      const detail = permissions.length ? `\n\n将授予：${permissions.join('、')}` : '';
      if (!(await confirmUserAction(`${plugin?.installed ? '更新' : '安装'}插件“${plugin?.name || pluginId}”？${detail}`, {
        title: plugin?.installed ? '确认更新插件' : '确认安装插件',
        confirmLabel: plugin?.installed ? '更新插件' : '安装插件'
      }))) return;
      result = await installPluginFromCatalog(plugin);
    } else if (action === 'toggle') {
      result = await window.electronAPI.setPluginEnabled(pluginId, button.dataset.enabled === 'true');
    } else if (action === 'rollback') {
      if (!(await confirmUserAction('回滚到上一个已安装版本？当前版本会保留，可再次切换。', {
        title: '确认回滚插件',
        confirmLabel: '回滚'
      }))) return;
      result = await window.electronAPI.rollbackPlugin(pluginId);
    } else if (action === 'uninstall') {
      if (!(await confirmUserAction(`卸载插件“${plugin?.name || pluginId}”？插件生成的导出文件不会删除。`, {
        title: '确认卸载插件',
        confirmLabel: '卸载插件',
        danger: true
      }))) return;
      const clearData = await confirmUserAction('是否同时删除这个插件保存的登录凭证、配置和缓存？\n\n选择“仅卸载插件”会保留以后可能需要的配置；导出文件始终不会删除。', {
        title: '是否删除插件数据？',
        confirmLabel: '删除插件数据',
        cancelLabel: '仅卸载插件',
        danger: true
      });
      result = await window.electronAPI.uninstallPlugin(pluginId, clearData);
    }
    if (!result?.success) throw new Error(result?.error || '插件操作失败');
    if (action === 'uninstall' && result.removed === false) {
      throw new Error('没有找到可卸载的插件版本；如果这是随主程序提供的平台，它会继续保留为内置插件。');
    }
    if (result.warning) {
      log(`插件操作提醒：${result.warning}`, 'warn');
      notifyUser(result.warning, 'warn', { title: '插件操作提醒', duration: 0 });
    }
    const operationLabel = action === 'uninstall'
      ? (result.dataRemoved ? '插件及其本地配置已卸载' : '插件本体已卸载，配置和缓存已保留')
      : (action === 'rollback' ? '插件已回滚' : action === 'toggle' ? (button.dataset.enabled === 'true' ? '插件已启用' : '插件已停用') : '插件已安装/更新');
    log(`插件操作完成：${plugin?.name || pluginId}（${operationLabel}）`, 'success');
    notifyUser(`${plugin?.name || pluginId}：${operationLabel}。导出文件不会被删除。`, 'success');
    pluginOperationNotice = null;
    await refreshProvidersAfterPluginChange();
    await loadPluginCatalog(action === 'uninstall' ? false : true);
  } catch (error) {
    log(`插件操作失败：${formatError(error)}`, 'error');
    notifyError(error, { title: '插件操作失败' });
  } finally {
    button.disabled = false;
    button.textContent = originalButtonText;
  }
}

async function runPluginCenterUpdateAll(button) {
  const allCandidates = pluginUpdateCandidates();
  const candidates = allCandidates.filter((plugin) => !pluginOperationBlocked(plugin.id));
  const blockedCount = allCandidates.length - candidates.length;
  if (!candidates.length) return;
  if (blockedCount) {
    log(`当前任务正在使用 ${blockedCount} 个待更新插件，批量更新将跳过它们。`, 'warn');
  }
  if (!(await confirmUserAction(`更新全部 ${candidates.length} 个可更新插件？将逐个下载、校验并安装，已安装的插件数据不会删除。`, {
    title: '确认批量更新插件',
    confirmLabel: '全部更新'
  }))) return;
  pluginBulkUpdateRunning = true;
  button.disabled = true;
  if (currentTool === 'plugin-center') renderPluginCenterPage();
  const failed = [];
  try {
    for (const plugin of candidates) {
      try {
        await installPluginFromCatalog(plugin);
        log(`插件已更新：${plugin.name || plugin.id}`, 'success');
      } catch (error) {
        failed.push(`${plugin.name || plugin.id}：${formatError(error)}`);
        log(`插件更新失败：${plugin.name || plugin.id}：${formatError(error)}`, 'error');
      }
    }
    await refreshProvidersAfterPluginChange();
    await loadPluginCatalog(true);
    if (failed.length) {
      pluginOperationNotice = {
        type: 'warning',
        title: '批量更新部分完成',
        message: failed.join('\n')
      };
      notifyUser(`已完成批量更新，但 ${failed.length} 个插件失败：\n${failed.join('\n')}`, 'warn', {
        title: '批量更新部分完成',
        duration: 0
      });
    } else {
      pluginOperationNotice = null;
      log(`已完成 ${candidates.length} 个插件的更新`, 'success');
    }
  } catch (error) {
    log(`批量更新失败：${formatError(error)}`, 'error');
    notifyError(error, { title: '批量更新失败' });
  } finally {
    pluginBulkUpdateRunning = false;
    if (currentTool === 'plugin-center') renderPluginCenterPage();
  }
}

function bindPluginCenterActions(root) {
  root.querySelector('[data-plugin-refresh]')?.addEventListener('click', () => refreshPluginCatalogFromUi());
  root.querySelector('[data-plugin-local-install]')?.addEventListener('click', async (event) => {
    event.currentTarget.disabled = true;
    try {
      const result = await window.electronAPI.installPluginFile();
      if (result?.canceled) return;
      if (!result?.success) throw new Error(result?.error || '本地插件安装失败');
      await refreshProvidersAfterPluginChange();
      await refreshPluginCatalogFromUi();
    } catch (error) {
      notifyError(error, { title: '本地插件安装失败' });
    } finally {
      event.currentTarget.disabled = false;
    }
  });
  root.querySelector('[data-plugin-update-all]')?.addEventListener('click', (event) => runPluginCenterUpdateAll(event.currentTarget));
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
  const catalogError = pluginCatalogState.error && !pluginCatalogState.offline
    ? `<div class="info-box plugin-offline"><strong>插件库读取失败</strong><p>${escapeHtml(pluginCatalogState.error)} 已保留本机插件状态，可稍后重试。</p></div>`
    : '';
  const operationNotice = pluginOperationNotice
    ? `<div class="info-box plugin-operation-notice ${pluginOperationNotice.type === 'warning' ? 'warning' : ''}"><strong>${escapeHtml(pluginOperationNotice.title)}</strong><pre>${escapeHtml(pluginOperationNotice.message)}</pre></div>`
    : '';
  const experimental = pluginCatalogState.experimentalError
    ? `<div class="info-box plugin-offline"><strong>实验插件库暂时无法读取</strong><p>稳定插件不受影响。${escapeHtml(pluginCatalogState.experimentalError)}</p></div>`
    : '<div class="info-box plugin-experimental-notice"><strong>实验性插件已标注</strong><p>它们会正常显示和搜索，但可能功能不完整或存在兼容性限制。</p></div>';
  const allUpdates = pluginUpdateCandidates();
  const updateCount = allUpdates.filter((plugin) => !pluginOperationBlocked(plugin.id)).length;
  const blockedUpdateCount = allUpdates.length - updateCount;
  const updateAllDisabled = !updateCount || pluginCatalogState.status === 'loading' || pluginCatalogState.offline || pluginBulkUpdateRunning;
  const runningPluginNotice = blockedUpdateCount
    ? `<div class="info-box plugin-operation-notice"><strong>当前任务仍在运行</strong><p>正在使用的插件暂时不能升级、停用、回滚或卸载；任务结束后可继续操作。批量更新会跳过 ${blockedUpdateCount} 个相关更新。</p></div>`
    : '';
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
          <button class="btn-secondary" data-plugin-update-all type="button" ${updateAllDisabled ? 'disabled' : ''}>${pluginBulkUpdateRunning ? '正在更新全部…' : `一键更新全部${updateCount ? `（${updateCount}）` : ''}`}</button>
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
    ${catalogError}
    ${operationNotice}
    ${experimental}
    ${runningPluginNotice}
    <section class="plugin-grid" data-plugin-grid>
      ${pluginGridHtml()}
    </section>
  `;
  bindPluginCenterActions(contentArea);
  hydratePluginProgressUi(contentArea);
  applyRunningControlState();
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
  } else if (viewId === 'markdown-reader') {
    renderMarkdownReaderPage();
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
  return escapeHtml(value);
}

function safeRemoteGuideImageUrl(value) {
  const remoteUrl = String(value || '').trim();
  return /^https:\/\/raw\.githubusercontent\.com\/tllovesxs\/wandao\/82c027b054d9ece8449af30d79600814eb823e46\/plugins\/feishu\/providers\/feishu-import\/images\/(?:[1-9]|1\d|20)\.png$/.test(remoteUrl)
    ? remoteUrl
    : '';
}

function safeGuideImagePath(value) {
  const imagePath = String(value || '').trim();
  const remoteUrl = safeRemoteGuideImageUrl(imagePath);
  if (remoteUrl) return remoteUrl;
  if (!imagePath || imagePath.startsWith('/') || imagePath.startsWith('\\')) return '';
  if (/^[a-z][a-z0-9+.-]*:/i.test(imagePath) || /[<>"'&\x00-\x1f]/.test(imagePath)) return '';
  const segments = imagePath.replace(/\\/g, '/').split('/');
  if (segments.includes('..')) return '';
  return imagePath;
}

function safeNoticeImageUrl(value) {
  const rawUrl = String(value || '').trim();
  if (!rawUrl || typeof URL === 'undefined') return '';
  let url;
  try {
    url = new URL(rawUrl);
  } catch {
    return '';
  }
  if (url.protocol !== 'https:' || url.hostname !== 'raw.githubusercontent.com') return '';
  if (!url.pathname.startsWith('/tllovesxs/wandao/main/docs/')) return '';
  if (!/\.(?:png|jpe?g|gif|webp)$/i.test(url.pathname)) return '';
  return url.href;
}

function resolveNoticeImageSource(source, item) {
  const baseUrl = noticeRawUrl(item);
  if (!baseUrl || typeof URL === 'undefined') return '';
  try {
    return new URL(String(source || '').trim(), baseUrl).href;
  } catch {
    return '';
  }
}

function markdownToHtml(markdown, options = {}) {
  const runtime = window.WandaoVditor;
  if (!runtime?.placeholder) return `<p>${escapeHtml(markdown || '')}</p>`;
  const allowImageSource = typeof options.allowRemoteImage === 'function'
    ? options.allowRemoteImage
    : safeGuideImagePath;
  return runtime.placeholder(markdown, {
    resolveImageSource: options.resolveImageSource,
    allowImageSource,
    imageAttribute: options.remoteImageAttribute || options.localImageAttribute || 'data-guide-image',
    imageClass: 'guide-image',
    externalLinkAttribute: options.externalLinkAttribute || 'data-external-link'
  });
}

function mountMarkdownPreviews(container) {
  return window.WandaoVditor?.mountQueued(container) || Promise.resolve();
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
      <p>这个平台插件声明了额外依赖。正式执行前请确认本机环境已满足；万能导不会自动安装社区插件依赖。</p>
      <ul>${list.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul>
    </div>
  `;
}

function renderTrustBadge(provider) {
  const label = window.WandaoProviderRuntime?.providerTypeLabel(provider) || 'Provider';
  const trustClass = window.WandaoProviderRuntime?.providerTrustClass(provider) || 'community';
  return `<span class="trust-badge ${trustClass}">${escapeHtml(label)}</span>`;
}

async function requestGuideImage(providerId, imagePath) {
  let result;
  try {
    result = await window.electronAPI.readProviderGuideImage(providerId, imagePath);
    if (!result?.success || !result.dataUrl) throw new Error(result?.error || '教程图片读取失败');
    return { success: true, result };
  } catch (error) {
    return {
      success: false,
      result,
      errorMessage: error?.message || String(error)
    };
  }
}

function replaceWithGuideImageFallback(image, providerId, imagePath, outcome) {
  const fallbackUrl = safeRemoteGuideImageUrl(outcome.result?.fallbackUrl || imagePath);
  const placeholder = document.createElement('div');
  placeholder.className = 'guide-image-fallback';
  placeholder.setAttribute('role', 'status');
  placeholder.title = outcome.errorMessage;

  const title = document.createElement('strong');
  title.textContent = image.alt ? `${image.alt}暂时无法加载` : '教程截图暂时无法加载';
  placeholder.appendChild(title);

  const detail = document.createElement('span');
  detail.textContent = '请检查网络连接，教程文字与后续步骤仍可继续阅读。';
  placeholder.appendChild(detail);

  const retryButton = document.createElement('button');
  retryButton.type = 'button';
  retryButton.className = 'guide-image-retry';
  retryButton.title = '重新加载这张图片';
  retryButton.setAttribute('aria-label', '重新加载这张教程图片');
  const retryIcon = document.createElement('span');
  retryIcon.className = 'guide-image-retry-icon';
  retryIcon.textContent = '\u21bb';
  retryIcon.setAttribute('aria-hidden', 'true');
  retryButton.appendChild(retryIcon);
  retryButton.addEventListener('click', async () => {
    retryButton.disabled = true;
    retryButton.classList.add('is-loading');
    const retryOutcome = await requestGuideImage(providerId, imagePath);
    if (retryOutcome.success) {
      const replacement = document.createElement('img');
      replacement.className = image.className || 'guide-image';
      replacement.alt = image.alt || '';
      replacement.src = retryOutcome.result.dataUrl;
      placeholder.replaceWith(replacement);
      return;
    }
    placeholder.title = retryOutcome.errorMessage;
    retryButton.disabled = false;
    retryButton.classList.remove('is-loading');
  });
  placeholder.appendChild(retryButton);

  if (fallbackUrl) {
    const openButton = document.createElement('button');
    openButton.type = 'button';
    openButton.className = 'guide-image-fallback-link';
    openButton.textContent = '在 GitHub 查看原图';
    openButton.addEventListener('click', () => window.electronAPI.openExternal(fallbackUrl));
    placeholder.appendChild(openButton);
  }
  image.replaceWith(placeholder);
}

async function hydrateGuideImages(container, providerId) {
  const images = Array.from(container?.querySelectorAll?.('img[data-guide-image]') || []);
  const grouped = new Map();
  images.forEach((image) => {
    const imagePath = image.dataset.guideImage || '';
    if (!grouped.has(imagePath)) grouped.set(imagePath, []);
    grouped.get(imagePath).push(image);
  });
  const pending = Array.from(grouped, ([imagePath, targets]) => ({ imagePath, targets }));

  const loadNext = async () => {
    while (pending.length) {
      const { imagePath, targets } = pending.shift();
      const outcome = await requestGuideImage(providerId, imagePath);
      if (outcome.success) {
        targets.forEach((image) => {
          image.src = outcome.result.dataUrl;
          image.removeAttribute('data-guide-image');
        });
        continue;
      }
      targets.forEach((image) => replaceWithGuideImageFallback(image, providerId, imagePath, outcome));
    }
  };

  const workerCount = Math.min(3, pending.length);
  await Promise.all(Array.from({ length: workerCount }, () => loadNext()));
}

function bindCollapsibleGuideImages(container, providerId) {
  const details = container?.querySelector?.('.plugin-guide-section');
  if (!details) return;
  const loadImages = () => {
    if (details.open) hydrateGuideImages(details, providerId);
  };
  details.addEventListener('toggle', loadImages);
  loadImages();
}

function bindRenderedMarkdownLinks(container) {
  container?.querySelectorAll?.('[data-external-link]').forEach((link) => {
    if (link.dataset.wandaoExternalLinkBound === 'true') return;
    link.dataset.wandaoExternalLinkBound = 'true';
    link.addEventListener('click', (event) => {
      event.preventDefault();
      window.electronAPI.openExternal(link.href);
    });
  });
}

function appendProviderGuideSection(container, provider) {
  if (!container || !provider?.guideMarkdown) return;
  const guideHost = container.querySelector('.form-section') || container;
  guideHost.insertAdjacentHTML('beforeend', `
    <details class="advanced-section plugin-guide-section">
      <summary>\u5e73\u53f0\u8bf4\u660e / \u64cd\u4f5c\u6559\u7a0b</summary>
      <div class="guide-content compact">${markdownToHtml(provider.guideMarkdown)}</div>
    </details>
  `);
  mountMarkdownPreviews(container).then(() => {
    bindCollapsibleGuideImages(container, provider.id);
    bindRenderedMarkdownLinks(container);
  });
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
  const guide = provider.guideMarkdown || '# 暂无教程\n\n这个平台还没有提供教程文档。';
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
  mountMarkdownPreviews(contentArea).then(() => {
    hydrateGuideImages(contentArea, provider.id);
    bindRenderedMarkdownLinks(contentArea);
  });
  contentArea.querySelectorAll('[data-open-url]').forEach((button) => {
    button.addEventListener('click', () => {
      window.electronAPI.openExternal(button.dataset.openUrl);
    });
  });
  bindRenderedMarkdownLinks(contentArea);
}

function manifestFieldId(provider, field) {
  return `${provider.id}-field-${field.name}`;
}

function manifestFieldErrorId(provider, field) {
  return `${manifestFieldId(provider, field)}-error`;
}

function manifestFieldInputAttributes(provider, field, options = {}) {
  const attributes = [`aria-describedby="${manifestFieldErrorId(provider, field)}"`];
  if (field.required) attributes.push('required', 'aria-required="true"');
  if (options.number && field.min !== undefined) attributes.push(`min="${escapeHtml(field.min)}"`);
  if (options.number && field.max !== undefined) attributes.push(`max="${escapeHtml(field.max)}"`);
  if (options.number && field.step !== undefined) attributes.push(`step="${escapeHtml(field.step)}"`);
  return ` ${attributes.join(' ')}`;
}

function renderManifestFieldError(provider, field) {
  return `<p class="form-field-error" id="${manifestFieldErrorId(provider, field)}" role="alert" hidden></p>`;
}

function renderManifestField(provider, field) {
  const id = manifestFieldId(provider, field);
  const label = escapeHtml(field.label || field.name);
  const required = field.required ? ' <span class="required">*</span>' : '';
  const placeholder = escapeHtml(field.placeholder || '');
  const value = escapeHtml(field.default ?? '');
  const sensitiveMetadata = [field.name, field.label, field.arg, field.autocomplete].join(' ');
  const draftSensitive = field.sensitive === true || field.secret === true
    || /(?:(?:app|client)[-_.\s]*id|api[-_.\s]*key|access[-_.\s]*key|private[-_.\s]*key|import[-_.\s]*mount[-_.\s]*key|(?:knowledge[-_.\s]*base|folder|space)[-_.\s]*id|token|cookie|password|passwd|secret|signature|authorization|credential|session|bearer|csrf|username|user[-_.\s]*name|email|phone|account|webhook|dsn)/i.test(sensitiveMetadata);
  const draftSensitiveAttr = draftSensitive ? ' data-draft-sensitive="true"' : '';
  const recentKind = ['url', 'path', 'text'].includes(field.history) ? field.history : '';
  const recentAttrs = recentKind && !draftSensitive
    ? ` data-history-kind="${recentKind}" data-history-key="${escapeHtml(field.name)}" data-history-label="${label}"${value ? ` data-history-default-value="${value}"` : ''}`
    : '';
  if (field.type === 'notice') {
    return `<div class="info-box">${markdownToHtml(field.markdown || field.text || '')}</div>`;
  }
  if (field.type === 'textarea') {
    return `
      <div class="form-group">
        <label for="${id}">${label}${required}</label>
        <textarea id="${id}" placeholder="${placeholder}" rows="${field.rows || 6}"${manifestFieldInputAttributes(provider, field)}${draftSensitiveAttr}>${value}</textarea>
        ${renderManifestFieldError(provider, field)}
      </div>
    `;
  }
  if (field.type === 'checkbox') {
    return `
      <div class="form-group form-group-checkbox">
        <label class="checkbox-label">
          <input type="checkbox" id="${id}" ${field.default ? 'checked' : ''}${manifestFieldInputAttributes(provider, field)}${draftSensitiveAttr}>
          <span>${label}${required}</span>
        </label>
        ${renderManifestFieldError(provider, field)}
      </div>
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
        <select id="${id}"${manifestFieldInputAttributes(provider, field)}${draftSensitiveAttr}>${options}</select>
        ${renderManifestFieldError(provider, field)}
      </div>
    `;
  }
  if (field.type === 'directory' || field.type === 'file') {
    const buttonLabel = field.type === 'directory' ? '选择目录' : '选择文件';
    return `
      <div class="form-group">
        <label for="${id}">${label}${required}</label>
        <div class="input-with-button">
          <input type="text" id="${id}" placeholder="${placeholder}" value="${value}"${manifestFieldInputAttributes(provider, field)}${draftSensitiveAttr}${recentAttrs}>
          <button class="btn-secondary" id="${id}-browse" type="button">${buttonLabel}</button>
        </div>
        ${renderManifestFieldError(provider, field)}
      </div>
    `;
  }
  const inputType = field.type === 'password' ? 'password' : (field.type === 'number' ? 'number' : (recentKind === 'url' ? 'url' : 'text'));
  return `
    <div class="form-group">
      <label for="${id}">${label}${required}</label>
      <input type="${inputType}" id="${id}" placeholder="${placeholder}" value="${value}"${manifestFieldInputAttributes(provider, field, { number: field.type === 'number' })}${draftSensitiveAttr}${recentAttrs}>
      ${renderManifestFieldError(provider, field)}
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
  mountMarkdownPreviews(contentArea).then(() => {
    bindCollapsibleGuideImages(contentArea, provider.id);
    hydrateGuideImages(contentArea, provider.id);
    bindRenderedMarkdownLinks(contentArea);
  });
}

function manifestFieldValue(provider, field) {
  const element = document.getElementById(manifestFieldId(provider, field));
  if (!element) return '';
  if (field.type === 'checkbox') return Boolean(element.checked);
  return String(element.value || '').trim();
}

function manifestFieldValidationMessage(field, value) {
  const label = field.label || field.name;
  if (field.required && (value === '' || value === false)) {
    return field.type === 'checkbox' ? `请勾选：${label}` : `请填写：${label}`;
  }
  if (field.type !== 'number' || value === '') return '';
  const numberValue = Number(value);
  if (!Number.isFinite(numberValue)) return `${label}必须是数字`;
  const min = Number(field.min);
  if (field.min !== undefined && Number.isFinite(min) && numberValue < min) {
    return `${label}不能小于 ${field.min}`;
  }
  const max = Number(field.max);
  if (field.max !== undefined && Number.isFinite(max) && numberValue > max) {
    return `${label}不能大于 ${field.max}`;
  }
  return '';
}

function clearManifestFieldError(provider, field) {
  const input = document.getElementById(manifestFieldId(provider, field));
  const error = document.getElementById(manifestFieldErrorId(provider, field));
  input?.removeAttribute('aria-invalid');
  if (error) {
    error.hidden = true;
    error.textContent = '';
  }
  input?.closest('.form-group')?.classList.remove('has-error');
}

function clearManifestFieldErrorIfValid(provider, field) {
  const value = manifestFieldValue(provider, field);
  if (!manifestFieldValidationMessage(field, value)) clearManifestFieldError(provider, field);
}

function showManifestFieldError(provider, field, message) {
  const input = document.getElementById(manifestFieldId(provider, field));
  const error = document.getElementById(manifestFieldErrorId(provider, field));
  if (!input || !error) return;
  input.setAttribute('aria-invalid', 'true');
  error.hidden = false;
  error.textContent = message;
  input.closest('.form-group')?.classList.add('has-error');
  input.closest('details.advanced-section')?.setAttribute('open', '');
  input.focus();
}

function manifestFieldValidationError(field, message) {
  const error = new Error(message);
  error.manifestField = field;
  return error;
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
    const validationMessage = manifestFieldValidationMessage(field, value);
    if (validationMessage) throw manifestFieldValidationError(field, validationMessage);
    clearManifestFieldError(provider, field);
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
    const fieldValues = Object.fromEntries(fields.map((field) => [field.name, manifestFieldValue(provider, field)]));
    const checkpointArgs = window.WandaoTaskResume?.providerCheckpointArgs(provider, fieldValues) || [];
    args.push(...checkpointArgs);
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
      if (root) {
        input.value = `${root}/${provider.defaults.output}`;
        input.setAttribute('data-history-default-value', input.value);
      }
    }
    input?.addEventListener('input', () => clearManifestFieldErrorIfValid(provider, field));
    input?.addEventListener('change', () => clearManifestFieldErrorIfValid(provider, field));
    const browse = document.getElementById(`${id}-browse`);
    if (!browse) return;
    browse.addEventListener('click', async () => {
      const current = document.getElementById(id)?.value || '';
      if (field.type === 'directory') {
        const dir = await window.electronAPI.selectDirectory({ title: field.dialogTitle || field.label || '选择目录', defaultPath: current });
        if (dir) {
          document.getElementById(id).value = dir;
          clearManifestFieldErrorIfValid(provider, field);
        }
      } else {
        const file = await window.electronAPI.selectFile({ title: field.dialogTitle || field.label || '选择文件', filters: field.filters || [] });
        if (file) {
          document.getElementById(id).value = file;
          clearManifestFieldErrorIfValid(provider, field);
        }
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
      notifyUser(result?.error || '当前登录任务没有等待确认', 'warn');
    }
  });
  actions.forEach((action) => {
    const button = document.querySelector(`[data-manifest-action="${CSS.escape(action.id || action.label)}"]`);
    if (!button) return;
    button.addEventListener('click', async () => {
      if (action.openUrl) {
        if (action.confirm && !(await confirmUserAction(action.confirm, {
          title: '确认打开外部页面',
          confirmLabel: '打开页面'
        }))) return;
        await window.electronAPI.openExternal(action.openUrl);
        return;
      }
      const script = action.script || provider.script;
      if (!script) {
        notifyUser('这个动作没有配置脚本，可能只是纯教程型平台。', 'info');
        return;
      }
      let args;
      try {
        args = buildManifestActionArgs(provider, action, fields);
      } catch (error) {
        if (error?.manifestField) {
          showManifestFieldError(provider, error.manifestField, formatError(error));
        } else {
          notifyError(error, { title: '参数检查失败' });
        }
        return;
      }
      if (action.confirm && !(await confirmUserAction(action.confirm, {
        title: '确认执行平台动作',
        confirmLabel: '继续'
      }))) return;
      if (!(await confirmProviderExecution(provider, action))) return;
      if (action.kind === 'login' && loginDoneButton) {
        loginDoneButton.hidden = false;
        loginDoneButton.disabled = false;
        loginDoneButton.textContent = '我已完成登录，保存凭证';
      }
      startProgress(action.progressTitle || action.label || provider.title, action.progressDetail || '正在执行平台动作...');
      log(`开始：${action.label || provider.title}`, 'info');
      try {
        const result = await runProviderCommand(script, args, {
          providerId: provider.id,
          title: action.label || provider.title,
          action: action.actionName || action.label || '执行',
          track: shouldTrackManifestAction(action)
        });
        if (isStoppedResult(result)) {
          log(`${action.label || provider.title}\u5df2\u505c\u6b62\uff0c\u5df2\u5b8c\u6210\u9879\u76ee\u4f1a\u5728\u4e0b\u6b21\u7ee7\u7eed\u65f6\u8df3\u8fc7\u3002`, 'warn');
          finishProgress('stopped', `${action.label || '\u4efb\u52a1'}\u5df2\u505c\u6b62`);
        } else if (result.success) {
          const actionMode = action.actionName || action.label || '执行';
          const outcome = taskResultStatus(result, { provider: provider.id, mode: actionMode });
          appendExportSuccessSponsorLogs(outcome, action, taskReportForResult(result, { provider: provider.id, mode: actionMode }));
          appendExportResourceRecoveryLog(outcome, action, taskReportForResult(result, { provider: provider.id, mode: actionMode }));
          if (result.data) log(JSON.stringify(result.data, null, 2), 'info');
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
            finishProgressForTaskResult(result, `${action.label || '任务'}完成`, {
              provider: provider.id,
              mode: action.actionName || action.label
            });
            logTaskResultCompletion(result, `${action.label || '任务'}完成`, {
              provider: provider.id,
              mode: action.actionName || action.label
            });
          }
        } else {
          log(`失败：${result.error}`, 'error');
          finishProgressForTaskResult(result, `${action.label || '任务'}完成`, {
            provider: provider.id,
            mode: action.actionName || action.label,
            failedDetail: `${action.label || '任务'}失败，请查看运行日志`
          });
        }
      } catch (error) {
        log(`错误：${formatError(error)}`, 'error');
        finishProgress(false, `${action.label || '任务'}出错，请查看运行日志`);
      } finally {
        if (action.kind === 'login' && loginDoneButton) {
          loginDoneButton.hidden = true;
          loginDoneButton.disabled = true;
        }
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
  if (isRunning || activeCommandOwner) {
    throw new Error('当前已有任务运行中，请等待结束或先停止当前任务。');
  }
  if (!(await confirmProviderExecution(provider, action))) throw new Error('用户取消执行');
  startProgress(action.progressTitle || action.label || provider.title, action.progressDetail || '正在执行插件动作...');
  const result = await runProviderCommand(action.script || provider.script, [...(action.args || []), ...args], {
    providerId: provider.id,
    title: action.label || provider.title,
    action: action.actionName || action.label || '执行',
    track: shouldTrackManifestAction(action)
  });
  contentArea.querySelector('[data-settings-action="clear-form-memory"]')?.addEventListener('click', clearRememberedFormInputs);
  if (isStoppedResult(result)) {
    finishProgress('stopped', '插件动作已停止');
  } else {
    finishProgressForTaskResult(result, '插件动作完成', {
      provider: provider.id,
      mode: action.actionName || action.label,
      failedDetail: '插件动作失败，请查看运行日志'
    });
  }
  return result;
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
      <input type="url" id="${provider.id}-url" data-history-kind="url" data-history-key="url" data-history-label="入口 URL" placeholder="粘贴目标平台页面 URL">
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
            <input type="text" id="${provider.id}-output" data-history-kind="path" data-history-key="${provider.isImport ? 'source_dir' : 'output'}" data-history-label="${sourceLabel}" placeholder="${sourcePlaceholder}">
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

function renderAppPathsInitializationState() {
  const contentArea = document.getElementById('content-area');
  if (!contentArea) return;
  const failed = appPathsStatus === 'error';
  setTaskHistoryVisible(false);
  setToolHeading(failed ? '本机数据目录初始化失败' : '正在初始化本机数据目录', failed
    ? '无法安全读取凭证和配置，平台功能暂不可用。'
    : '正在准备凭证、配置和任务历史，请稍候。');
  contentArea.innerHTML = `
    <div class="plugin-empty" role="status" aria-live="polite">
      <strong>${failed ? '平台功能尚未启动' : '正在准备平台配置...'}</strong>
      <p>${escapeHtml(failed ? appPathsError : '完成后会自动打开你刚才选择的平台。')}</p>
      ${failed ? '<button class="btn-primary" id="retry-app-paths" type="button">重试初始化</button>' : ''}
    </div>
  `;
  document.getElementById('retry-app-paths')?.addEventListener('click', () => loadAppPaths());
}

function announceStartupOnce() {
  if (startupAnnounced) return;
  startupAnnounced = true;
  log(`万能导已启动（Wandao v${appPaths?.appVersion || '未知'}）`, 'success');
  window.setTimeout(() => checkForUpdates(true), 1000);
}

function loadAppPaths() {
  if (appPathsLoadPromise) return appPathsLoadPromise;
  appPathsStatus = 'loading';
  appPathsError = '';
  if (pendingProviderTool) renderAppPathsInitializationState();
  appPathsLoadPromise = window.electronAPI.getAppPath().then(async (paths) => {
    if (!paths?.userData) throw new Error('主进程未返回可用的用户数据目录');
    appPaths = paths;
    await pythonProcessStateReady;
    try {
      await loadTaskHistory();
    } catch (error) {
      const historyError = formatError(error) || '读取任务历史失败';
      appendDetailedLog('task-history', 'error', `任务历史读取失败：${historyError}`);
      log('任务历史未能完整读取，但平台页面仍可正常打开。可在任务中心刷新后重试。', 'warn');
    }
    appPathsStatus = 'ready';
    const pendingTool = pendingProviderTool;
    pendingProviderTool = '';
    if (pendingTool && !isRunning) {
      switchTool(pendingTool);
    } else if (currentTool === DEFAULT_VIEW_ID) {
      switchTool(DEFAULT_VIEW_ID);
    }
    announceStartupOnce();
    return paths;
  }).catch((error) => {
    appPaths = null;
    appPathsStatus = 'error';
    appPathsError = formatError(error) || '无法获取本机数据目录';
    renderAppPathsInitializationState();
    log(`本机数据目录初始化失败：${appPathsError}`, 'error');
    return null;
  }).finally(() => {
    appPathsLoadPromise = null;
  });
  return appPathsLoadPromise;
}

// Tool switching
function switchTool(toolId) {
  const targetTool = toolId || DEFAULT_VIEW_ID;
  if (targetTool === 'markdown-reader' && currentTool === 'markdown-reader') {
    window.WandaoMarkdownDock?.showReaderLanding();
    return true;
  }
  const allowsActiveTaskNavigation = Boolean(
    isRunning
    && activeHistoryTask
    && targetTool === taskOriginView(activeHistoryTask)
  );
  const allowsWorkbenchNavigation = PRIMARY_NAV_ITEMS.some((item) => item.id === targetTool);
  if (isRunning && targetTool !== currentTool && !allowsWorkbenchNavigation && !allowsActiveTaskNavigation) {
    log('任务仍在运行中。为避免丢失当前表单和任务上下文，请等待任务结束或先停止任务。', 'warn');
    return false;
  }
  refreshProviderTools();
  const opensProvider = !String(targetTool).startsWith('platform:')
    && !PRIMARY_NAV_ITEMS.some((item) => item.id === targetTool)
    && Boolean(TOOLS[targetTool]);
  if (opensProvider && appPathsStatus !== 'ready') {
    pendingProviderTool = targetTool;
    renderAppPathsInitializationState();
    if (appPathsStatus === 'error') {
      log('平台配置目录尚未就绪，请点击“重试初始化”。', 'warn');
    }
    return false;
  }
  recordCurrentRecentInputs();
  saveCurrentFormDraft();
  if (formDraftSaveTimer) {
    window.clearTimeout(formDraftSaveTimer);
    formDraftSaveTimer = null;
  }
  activeFormDraftContext = null;
  recentInputControllers = [];
  if (customPluginMessageCleanup) {
    customPluginMessageCleanup();
    customPluginMessageCleanup = null;
  }
  currentTool = targetTool;
  renderProviderNavigation();
  window.WandaoMarkdownDock?.close();
  document.body.classList.toggle('markdown-reader-mode', currentTool === 'markdown-reader');

  if (String(currentTool).startsWith('platform:')) {
    renderPlatformDetailPage(String(currentTool).slice('platform:'.length));
    applyRunningControlState();
    return;
  }

  if (PRIMARY_NAV_ITEMS.some((item) => item.id === currentTool)) {
    renderAppView(currentTool);
    applyRunningControlState();
    return;
  }

  const config = TOOLS[currentTool];
  if (!config) {
    log(`未找到这个平台：${currentTool}`, 'error');
    switchTool(DEFAULT_VIEW_ID);
    return;
  }

  setTaskHistoryVisible(false);
  setToolHeading(config.title, config.description);

  // Load tool template
  const contentArea = document.getElementById('content-area');
  const template = document.getElementById(config.templateId || `template-${currentTool}`);

  if (config.sourceKind === 'plugin' && config.ui?.mode === 'custom') {
    renderCustomPluginProvider(config).then(() => {
      if (currentTool !== config.id) return;
      renderProviderModeSwitcher(config);
      normalizeActionHierarchy(contentArea);
      applyRunningControlState();
    }).catch((error) => {
      if (currentTool !== config.id) return;
      contentArea.innerHTML = `<div class="info-box"><strong>插件界面加载失败</strong><p>${escapeHtml(formatError(error))}</p></div>`;
      renderProviderModeSwitcher(config);
      applyRunningControlState();
      log(`插件界面加载失败：${formatError(error)}`, 'error');
    });
    return true;
  }
  if (currentTool === 'feishu-import') {
    // The dedicated page preserves the saved API configuration and the
    // browser-login completion handoff for both bundled and installed plugins.
    loadFeishuImportTool();
    appendProviderGuideSection(contentArea, config);
  } else if (config.type === 'guide' || (!config.script && !template && !(config.actions || []).length)) {
    renderGuideProvider(config);
  } else if (template) {
    contentArea.innerHTML = '';
    const clone = template.content.cloneNode(true);
    contentArea.appendChild(clone);
    initializeToolHandlers(currentTool);
  } else {
    renderGenericProviderForm(config);
  }
  renderProviderModeSwitcher(config);
  normalizeActionHierarchy(contentArea);
  const configLoadsSavedValues = currentTool === 'ima-import' || currentTool === 'ima-export'
    || currentTool === 'feishu-import';
  if (!configLoadsSavedValues && config.type !== 'guide') {
    restoreFormDraftForProvider(currentTool);
  }
  restoreActiveTaskFormValues(config);
  if (config.type !== 'guide') {
    enhanceRecentInputsForProvider(currentTool);
  }
  applyRunningControlState();
  return true;
}

// Initialize tool event handlers
function initializeToolHandlers(toolId) {
  const prefix = toolId;
  ensureTocSelector(toolId);
  syncAutoOutputFolderControl(toolId);

  const outputInput = document.getElementById(`${prefix}-output`);
  if (outputInput && !outputInput.value.trim()) {
    const suffix = TOOLS[toolId]?.defaults?.output;
    const root = appPaths?.dataRoot || appPaths?.userData || appPaths?.projectRoot;
    if (suffix && root) {
      outputInput.value = `${root}/${suffix}`;
      outputInput.setAttribute('data-history-default-value', outputInput.value);
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
      const requestedOutput = document.getElementById(`${prefix}-output`)?.value.trim() || '';
      const output = latestExportOutputForTool(toolId, requestedOutput) || requestedOutput;
      if (output) {
        await window.electronAPI.openPath(output);
      } else {
        notifyUser('请先指定输出目录。', 'warn');
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
    notifyUser('请先填写 URL。', 'warn');
    return;
  }
  try {
    validateZsxqUrlForTool(toolId, url);
  } catch (error) {
    notifyError(error, { title: '参数检查失败' });
    return;
  }

  const args = config.noUrl ? ['--login'] : [config.urlParam, url, '--login'];
  if (!(await confirmProviderExecution(config))) return;

  startProgress(`登录：${config.title}`, '请在浏览器中完成登录，然后回到工具点击“我已完成登录，保存凭证”。');
  setLoginDoneButton(toolId, true);
  log(`开始登录：${config.title}`, 'info');
  log('请在浏览器中完成登录，登录成功并能看到目标页面后，回到工具点击“我已完成登录，保存凭证”。', 'info');

  try {
    const result = await runProviderCommand(config.script, args, {
      providerId: toolId,
      title: `登录：${config.title}`,
      action: '登录',
      track: false
    });
    if (isStoppedResult(result)) {
      log('登录已停止。', 'warn');
      finishProgress('stopped', '登录已停止');
    } else if (result.success) {
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
  }
}

async function handleYinxiangLogin() {
  const config = TOOLS.yinxiang;
  const username = document.getElementById('yinxiang-username')?.value.trim();
  const password = document.getElementById('yinxiang-password')?.value || '';
  if (!username || !password) {
    notifyUser('请先填写印象笔记账号和密码。', 'warn');
    return;
  }

  const args = ['--init-auth', '--username', username, '--password-stdin'];
  startProgress(`登录并同步：${config.title}`, '正在初始化本地同步库并同步笔记...');
  log(`开始登录并同步：${config.title}`, 'info');

  try {
    const result = await runProviderCommand(config.script, args, {
      providerId: 'yinxiang',
      title: `登录并同步：${config.title}`,
      action: '登录',
      track: false
    }, {
      stdinText: `${password}\n`
    });
    if (isStoppedResult(result)) {
      log('登录同步已停止。', 'warn');
      finishProgress('stopped', '登录同步已停止');
    } else if (result.success) {
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

let rendererDiagnosticsInitialized = false;

function initializeRendererDiagnostics() {
  if (rendererDiagnosticsInitialized) return;
  rendererDiagnosticsInitialized = true;
  window.addEventListener('error', (event) => {
    appendDetailedLog('renderer', 'error', '前端发生未处理异常', {
      event: 'renderer.unhandled-error',
      data: {
        message: event.message || formatError(event.error),
        source: event.filename || '',
        line: event.lineno || 0,
        column: event.colno || 0,
        stack: event.error?.stack || ''
      }
    });
  });
  window.addEventListener('unhandledrejection', (event) => {
    const reason = event.reason;
    appendDetailedLog('renderer', 'error', '前端 Promise 发生未处理异常', {
      event: 'renderer.unhandled-rejection',
      data: {
        message: formatError(reason),
        stack: reason?.stack || ''
      }
    });
  });
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
  if (isStoppedResult(result)) return;
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
    return `${appPaths.userData}/plugin-data/ima/ima_config.json`;
  }
  if (appPaths?.projectRoot) {
    return `${appPaths.projectRoot}/.ima_config.json`;
  }
  return '';
}

async function loadImaConfigIntoForm(prefix) {
  const configPath = imaConfigPath();
  const legacyPaths = [
    appPaths?.userData ? `${appPaths.userData}/ima_config.json` : '',
    appPaths?.projectRoot ? `${appPaths.projectRoot}/.ima_config.json` : ''
  ];
  const config = await readJsonConfigWithMigration(configPath, legacyPaths, 'ima API 配置');
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

function appendAutoOutputFolderArg(args, prefix, forScan = false) {
  if (forScan || !providerSupportsAutoOutputFolder(prefix)) return;
  const checkbox = document.getElementById(`${prefix}-auto-output-folder`);
  // Keep older hard-coded templates safe during the transition: a provider
  // that advertises this capability should default to the new layout even if
  // its UI has not yet gained the optional switch.
  args.push(!checkbox || checkbox.checked ? '--auto-output-folder' : '--flat-output');
}

function providerSupportsAutoOutputFolder(toolId) {
  const provider = TOOLS[toolId] || {};
  return Array.isArray(provider.fields) && provider.fields.some((field) => (
    field?.name === 'auto_output_folder' && field?.arg === '--auto-output-folder'
  ));
}

function syncAutoOutputFolderControl(toolId) {
  const checkbox = document.getElementById(`${toolId}-auto-output-folder`);
  if (!checkbox) return;
  const supported = providerSupportsAutoOutputFolder(toolId);
  const label = checkbox.closest('label.checkbox-label') || checkbox.closest('.form-group');
  if (label) label.hidden = !supported;
  checkbox.disabled = !supported;
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
    appendAutoOutputFolderArg(args, prefix, forScan);
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
    notifyError(error, { title: 'ima 配置检查失败' });
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
  startProgress(title, '正在保存本机配置...');
  log(`开始：${title}`, 'info');
  try {
    const provider = TOOLS[prefix];
    if (!provider?.script) throw new Error(`ima 平台未提供脚本：${prefix}`);
    const result = await runProviderCommand(provider.script, args, {
      providerId: prefix,
      title,
      action: '保存配置',
      track: false
    });
    if (isStoppedResult(result)) {
      log(`${title}已停止。`, 'warn');
      finishProgress('stopped', `${title}已停止`);
    } else if (result.success) {
      log(`${title}完成`, 'success');
      finishProgress(true, `${title}完成`);
    } else {
      log(`${title}失败：${result.error}`, 'error');
      finishProgress(false, `${title}失败，请查看运行日志`);
    }
  } catch (error) {
    log(`错误：${formatError(error)}`, 'error');
    finishProgress(false, `${title}出错，请查看运行日志`);
  }
}

function initializeImaExportHandlers() {
  loadImaConfigIntoForm('ima-export')
    .then(() => restoreFormDraftForProvider('ima-export'))
    .catch((error) => {
      log(`读取 ima 配置失败：${formatError(error)}`, 'error');
      restoreFormDraftForProvider('ima-export');
    });
  document.getElementById('ima-export-save-config')?.addEventListener('click', () => saveImaConfig('ima-export'));
}

function selectedImaKnowledgeBaseId() {
  const select = document.getElementById('ima-import-kb-select');
  return select?.value?.trim() || '';
}

function updateImaImportKnowledgeBaseState() {
  const select = document.getElementById('ima-import-kb-select');
  const guidance = document.getElementById('ima-import-kb-guidance');
  const inlineReadButton = document.getElementById('ima-import-read-kbs-inline');
  const knowledgeBaseId = selectedImaKnowledgeBaseId();
  const loaded = select?.dataset.knowledgeBasesLoaded === 'true';
  const availableCount = Number(select?.dataset.knowledgeBaseCount || 0);
  const blocked = !knowledgeBaseId || isRunning;
  const reason = '请先读取并选择目标知识库';

  ['ima-import-list-folders', 'ima-import-one', 'ima-import-export'].forEach((id) => {
    const button = document.getElementById(id);
    if (!button) return;
    button.disabled = blocked;
    button.setAttribute('aria-disabled', blocked ? 'true' : 'false');
    button.title = blocked ? reason : '';
  });

  if (inlineReadButton) inlineReadButton.hidden = Boolean(knowledgeBaseId) || isRunning;
  if (!guidance) return;
  const message = guidance.querySelector('p');
  if (!message) return;
  if (knowledgeBaseId) {
    const selectedLabel = select?.selectedOptions?.[0]?.textContent?.trim() || knowledgeBaseId;
    message.textContent = `已选择“${selectedLabel}”。现在可以读取目标文件夹，或执行单文件、批量导入。`;
  } else if (loaded && availableCount === 0) {
    message.textContent = '未读取到可写入的知识库。请检查 ima 凭证和知识库权限后重新读取。';
  } else if (loaded) {
    message.textContent = '请选择一个目标知识库，再读取目标文件夹或开始导入。';
  } else {
    message.textContent = '下一步：读取可写知识库，然后选择本次导入的目标。';
  }
}

async function readImaKnowledgeBases() {
  try {
    const data = await runImaImportCommand(buildImaImportArgs({ listKbs: true }), '读取 ima 可写知识库', '正在读取可导入的知识库列表...');
    if (data) renderImaKnowledgeBaseOptions(data.knowledgeBases || []);
  } catch (error) {
    notifyError(error, { title: '读取 ima 知识库失败' });
  } finally {
    updateImaImportKnowledgeBaseState();
  }
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
  if (sourceFile && options.single) args.push('--source-file', sourceFile);
  if (kbId) args.push('--knowledge-base-id', kbId);
  if (folderId) args.push('--folder-id', folderId);
  if (delay) args.push('--request-delay', delay);
  if (jitter) args.push('--request-jitter', jitter);
  if (maxImport && parseInt(maxImport, 10) > 0) args.push('--max-import', maxImport);
  const includeAssets = document.getElementById('ima-import-include-assets');
  if (includeAssets && includeAssets.checked) args.push('--include-referenced-assets');
  const preserveFolders = document.getElementById('ima-import-preserve-folders');
  if (preserveFolders && !preserveFolders.checked) args.push('--flatten-folders');
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
  updateImaImportKnowledgeBaseState();
  startProgress(title, detail);
  log(`开始：${title}`, 'info');
  try {
    const provider = TOOLS['ima-import'];
    if (!provider?.script) throw new Error('ima 导入未提供脚本');
    const result = await runProviderCommand(provider.script, args, {
      providerId: 'ima-import',
      title,
      action: '导入',
      track: shouldTrackTask(title)
    });
    if (isStoppedResult(result)) {
      log(`${title}已停止，已完成项目会在下次继续时跳过。`, 'warn');
      finishProgress('stopped', `${title}已停止`);
      return null;
    }
    if (result.success) {
      if (result.data) log(JSON.stringify(result.data, null, 2), 'info');
      finishProgressForTaskResult(result, `${title}完成`, {
        provider: 'ima-import',
        mode: '导入'
      });
      logTaskResultCompletion(result, `${title}完成`, {
        provider: 'ima-import',
        mode: '导入'
      });
      return result.data || {};
    }
    log(`${title}失败：${result.error}`, 'error');
    finishProgressForTaskResult(result, `${title}完成`, {
      provider: 'ima-import',
      mode: '导入',
      failedDetail: `${title}失败，请查看运行日志`
    });
  } catch (error) {
    log(`错误：${formatError(error)}`, 'error');
    finishProgress(false, `${title}出错，请查看运行日志`);
  } finally {
    updateImaImportKnowledgeBaseState();
  }
  return null;
}

function renderImaKnowledgeBaseOptions(kbs) {
  const select = document.getElementById('ima-import-kb-select');
  if (!select) return;
  const availableKnowledgeBases = (kbs || []).filter((kb) => String(kb?.id || '').trim());
  const options = availableKnowledgeBases.map((kb) => {
    const id = String(kb.id || '');
    const name = String(kb.name || id || '未命名知识库');
    return `<option value="${escapeHtml(id)}">${escapeHtml(name)}</option>`;
  }).join('');
  select.dataset.knowledgeBasesLoaded = 'true';
  select.dataset.knowledgeBaseCount = String(availableKnowledgeBases.length);
  select.innerHTML = options
    ? `<option value="">请选择目标知识库（${availableKnowledgeBases.length} 个可写）</option>${options}`
    : '<option value="">没有读取到可写入的知识库</option>';
  renderImaFolderOptions([]);
  updateImaImportKnowledgeBaseState();
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
  updateImaImportKnowledgeBaseState();
  loadImaConfigIntoForm('ima-import')
    .then(() => {
      restoreFormDraftForProvider('ima-import');
      updateImaImportKnowledgeBaseState();
    })
    .catch((error) => {
      log(`读取 ima 配置失败：${formatError(error)}`, 'error');
      restoreFormDraftForProvider('ima-import');
      updateImaImportKnowledgeBaseState();
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
  document.getElementById('ima-import-list-kbs')?.addEventListener('click', readImaKnowledgeBases);
  document.getElementById('ima-import-read-kbs-inline')?.addEventListener('click', readImaKnowledgeBases);
  document.getElementById('ima-import-kb-select')?.addEventListener('change', () => {
    renderImaFolderOptions([]);
    updateImaImportKnowledgeBaseState();
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
      notifyError(error, { title: '读取 ima 目标文件夹失败' });
    }
  });
  document.getElementById('ima-import-plan')?.addEventListener('click', async () => {
    try {
      await runImaImportCommand(buildImaImportArgs({ plan: true }), '扫描 ima 导入目录', '正在扫描本地可导入文件...');
    } catch (error) {
      notifyError(error, { title: '扫描 ima 导入目录失败' });
    }
  });
  document.getElementById('ima-import-one')?.addEventListener('click', async () => {
    try {
      const args = buildImaImportArgs({ single: true });
      if (!(await confirmImaImportWrite({ single: true }))) return;
      await runImaImportCommand(args, 'ima 单文件导入测试', '正在上传第一个文件...');
    } catch (error) {
      notifyError(error, { title: 'ima 单文件导入失败' });
    }
  });
  document.getElementById('ima-import-export')?.addEventListener('click', async () => {
    try {
      const args = buildImaImportArgs();
      if (!(await confirmImaImportWrite())) return;
      await runImaImportCommand(args, 'ima 批量导入', '正在批量上传文件...');
    } catch (error) {
      notifyError(error, { title: 'ima 批量导入失败' });
    }
  });
  document.getElementById('ima-import-stop')?.addEventListener('click', handleStop);
  document.getElementById('ima-import-open-dir')?.addEventListener('click', async () => {
    const dir = document.getElementById('ima-import-source')?.value.trim();
    if (dir) {
      await window.electronAPI.openPath(dir);
    } else {
      notifyUser('请先选择本地文件目录。', 'warn');
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

async function confirmImportWrite({ platform, operation, target, source, sourceFile = '', behavior = '' }) {
  const details = [
    `平台：${platform}`,
    `操作：${operation}`,
    `来源：${source || '未选择'}`,
    sourceFile ? `单篇/单文件：${sourceFile}` : '',
    `目标：${target || '未选择'}`,
    behavior
  ].filter(Boolean);
  return confirmUserAction(
    `请核对本次写入信息：\n\n${details.join('\n')}\n\n` +
    '确认后将开始向目标平台写入内容。',
    {
      title: '确认写入目标平台',
      confirmLabel: '开始写入'
    }
  );
}

async function confirmYuqueImportWrite({ single = false } = {}) {
  const targetUrl = document.getElementById('yuque-import-url')?.value.trim() || '当前填写的目标知识库';
  const sourceDir = document.getElementById('yuque-import-output')?.value.trim() || '';
  const updateExisting = document.getElementById('yuque-import-update-existing')?.checked !== false;
  const scope = single ? '导入一篇 Markdown 进行测试' : '批量导入本地 Markdown 文件';
  const existingBehavior = updateExisting
    ? '同名文档存在时会更新其内容。'
    : '同名文档会跳过，不会覆盖。';
  return confirmImportWrite({
    platform: '语雀',
    operation: scope,
    target: targetUrl,
    source: sourceDir,
    behavior: existingBehavior
  });
}

async function confirmImaImportWrite({ single = false } = {}) {
  const kbSelect = document.getElementById('ima-import-kb-select');
  const folderSelect = document.getElementById('ima-import-folder-id');
  const knowledgeBase = kbSelect?.selectedOptions?.[0]?.textContent?.trim() || selectedImaKnowledgeBaseId();
  const folder = folderSelect?.selectedOptions?.[0]?.textContent?.trim() || '知识库根目录';
  const sourceDir = document.getElementById('ima-import-source')?.value.trim() || '';
  const sourceFile = document.getElementById('ima-import-source-file')?.value.trim() || '';
  const skipExisting = document.getElementById('ima-import-skip-existing')?.checked !== false;
  return confirmImportWrite({
    platform: 'ima 知识库',
    operation: single ? '上传一个测试文件' : '批量上传本地文件',
    target: `${knowledgeBase || '未选择知识库'} / ${folder}`,
    source: sourceDir,
    sourceFile: single ? (sourceFile || '目录内第一个支持文件') : '',
    behavior: skipExisting ? '目标已有同名文件时会跳过。' : '目标已有同名文件时允许覆盖。'
  });
}

async function confirmYinxiangImportWrite({ single = false } = {}) {
  const sourceDir = document.getElementById('yinxiang-import-source')?.value.trim() || '';
  const sourceFile = document.getElementById('yinxiang-import-source-file')?.value.trim() || '';
  const notebook = document.getElementById('yinxiang-import-notebook')?.value.trim() || '默认笔记本';
  const stack = document.getElementById('yinxiang-import-stack')?.value.trim();
  const preserveFolders = document.getElementById('yinxiang-import-preserve-folders')?.checked === true;
  return confirmImportWrite({
    platform: '印象笔记',
    operation: single ? '创建一篇测试笔记' : '批量创建笔记',
    target: stack ? `${stack} / ${notebook}` : notebook,
    source: sourceDir,
    sourceFile: single ? (sourceFile || '目录内第一篇 Markdown') : '',
    behavior: preserveFolders ? '本地目录会映射为笔记本结构。' : '所有内容会写入所示默认目标笔记本。'
  });
}

async function confirmFeishuImportWrite({ single = false } = {}) {
  const targetUrl = document.getElementById('feishu-import-url')?.value.trim() || '';
  const sourceDir = document.getElementById('feishu-import-source')?.value.trim() || '';
  const sourceFile = document.getElementById('feishu-import-source-file')?.value.trim() || '';
  const moveToWiki = document.getElementById('feishu-import-move-to-wiki')?.checked === true;
  return confirmImportWrite({
    platform: '飞书',
    operation: single ? '创建一篇测试文档' : '批量创建文档',
    target: targetUrl,
    source: sourceDir,
    sourceFile: single ? (sourceFile || '目录内第一篇 Markdown') : '',
    behavior: moveToWiki ? '创建后会移动到目标 Wiki。' : '文档不会自动移动到目标 Wiki。'
  });
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
  startProgress(title, detail);
  log(`开始：${title}`, 'info');
  try {
    const provider = TOOLS['yuque-import'];
    if (!provider?.script) throw new Error('语雀导入未提供脚本');
    const result = await runProviderCommand(provider.script, args, {
      providerId: 'yuque-import',
      title,
      action: '导入',
      track: shouldTrackTask(title)
    });
    if (isStoppedResult(result)) {
      log(`${title}已停止，已完成项目会在下次继续时跳过。`, 'warn');
      finishProgress('stopped', `${title}已停止`);
      return null;
    } else if (result.success) {
      if (result.data) log(JSON.stringify(result.data, null, 2), 'info');
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
      finishProgressForTaskResult(result, `${title}完成`, {
        provider: 'yuque-import',
        mode: '导入'
      });
      logTaskResultCompletion(result, `${title}完成`, {
        provider: 'yuque-import',
        mode: '导入'
      });
    } else {
      log(`${title}失败：${result.error}`, 'error');
      finishProgressForTaskResult(result, `${title}完成`, {
        provider: 'yuque-import',
        mode: '导入',
        failedDetail: `${title}失败，请查看运行日志`
      });
    }
  } catch (error) {
    log(`错误：${formatError(error)}`, 'error');
    finishProgress(false, `${title}出错，请查看运行日志`);
  }
}

async function loadYuqueImportConfigIntoForm() {
  const userData = appPaths?.userData || appPaths?.dataRoot;
  if (!userData) return;
  const pluginConfigPath = `${userData}/plugin-data/yuque/.yuque_import_config.json`;
  const legacyConfigPath = `${userData}/.yuque_import_config.json`;
  const config = (await readJsonFileIfExists(pluginConfigPath))
    || await readJsonFileIfExists(legacyConfigPath);
  if (!config || typeof config !== 'object') return;
  if (currentTool !== 'yuque-import') return;
  const targetUrl = document.getElementById('yuque-import-url');
  const sourceDir = document.getElementById('yuque-import-output');
  if (targetUrl && !targetUrl.value.trim() && config.targetBookUrl) targetUrl.value = String(config.targetBookUrl);
  if (sourceDir && !sourceDir.value.trim() && config.sourceDir) sourceDir.value = String(config.sourceDir);
}

function initializeYuqueImportHandlers() {
  loadYuqueImportConfigIntoForm().catch((error) => {
    log(`读取语雀导入配置失败：${formatError(error)}`, 'warn');
  });
  document.getElementById('yuque-import-save-config')?.addEventListener('click', async () => {
    try {
      await runYuqueImportCommand(buildYuqueImportArgs({ saveConfig: true }), '保存语雀导入配置', '正在保存本机配置...');
    } catch (error) {
      notifyError(error, { title: '保存语雀导入配置失败' });
    }
  });

  document.getElementById('yuque-import-plan')?.addEventListener('click', async () => {
    try {
      await runYuqueImportCommand(buildYuqueImportArgs({ plan: true }), '生成语雀导入计划', '正在扫描本地 Markdown 并验证目标知识库...');
    } catch (error) {
      notifyError(error, { title: '生成语雀导入计划失败' });
    }
  });

  document.getElementById('yuque-import-one')?.addEventListener('click', async () => {
    try {
      const args = buildYuqueImportArgs({ single: true });
      if (!(await confirmYuqueImportWrite({ single: true }))) return;
      await runYuqueImportCommand(args, '语雀单篇导入测试', '正在导入第一篇 Markdown...');
    } catch (error) {
      notifyError(error, { title: '语雀单篇导入失败' });
    }
  });

  document.getElementById('yuque-import-retry-failed')?.addEventListener('click', async () => {
    try {
      if (await confirmUserAction('将只重试上次导入报告中的失败文档。确认继续吗？', {
        title: '确认重试失败项',
        confirmLabel: '重试失败项'
      })) {
        await runYuqueImportCommand(buildYuqueImportArgs({ retryFailures: true }), '语雀重试失败文档', '正在读取上次报告并重试失败项...');
      }
    } catch (error) {
      notifyError(error, { title: '重试语雀失败项失败' });
    }
  });

  document.getElementById('yuque-import-open-report')?.addEventListener('click', async () => {
    try {
      await window.electronAPI.openPath(latestYuqueImportReportPath());
    } catch (error) {
      notifyError(error, { title: '打开语雀导入报告失败' });
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
    notifyUser('请填写印象笔记账号和密码。已有凭证时可以直接扫描目录或导入。', 'warn');
    return;
  }

  const args = ['--init-auth', '--username', username, '--password-stdin'];
  startProgress('登录并同步印象笔记凭证', '正在初始化本地同步库并同步笔记...');
  log('开始：登录并同步印象笔记凭证', 'info');
  try {
    const exportProvider = TOOLS.yinxiang;
    if (!exportProvider?.script) throw new Error('印象笔记导出未提供凭证初始化脚本');
    const result = await runProviderCommand(exportProvider.script, args, {
      providerId: 'yinxiang-import',
      title: '登录并同步印象笔记凭证',
      action: '登录',
      track: false
    }, {
      stdinText: `${password}\n`
    });
    if (isStoppedResult(result)) {
      log('登录同步已停止。', 'warn');
      finishProgress('stopped', '登录同步已停止');
    } else if (result.success) {
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
  }
}

async function runYinxiangImportCommand(args, title, detail = '正在处理印象笔记导入任务...') {
  startProgress(title, detail);
  log(`开始：${title}`, 'info');
  try {
    const provider = TOOLS['yinxiang-import'];
    if (!provider?.script) throw new Error('印象笔记导入未提供脚本');
    const result = await runProviderCommand(provider.script, args, {
      providerId: 'yinxiang-import',
      title,
      action: '导入',
      track: shouldTrackTask(title)
    });
    if (isStoppedResult(result)) {
      log(`${title}已停止，已完成项目会在下次继续时跳过。`, 'warn');
      finishProgress('stopped', `${title}已停止`);
      return null;
    }
    if (result.success) {
      if (result.data) log(JSON.stringify(result.data, null, 2), 'info');
      finishProgressForTaskResult(result, `${title}完成`, {
        provider: 'yinxiang-import',
        mode: '导入'
      });
      logTaskResultCompletion(result, `${title}完成`, {
        provider: 'yinxiang-import',
        mode: '导入'
      });
      return result.data || {};
    }
    log(`${title}失败：${result.error}`, 'error');
    finishProgressForTaskResult(result, `${title}完成`, {
      provider: 'yinxiang-import',
      mode: '导入',
      failedDetail: `${title}失败，请查看运行日志`
    });
  } catch (error) {
    log(`错误：${formatError(error)}`, 'error');
    finishProgress(false, `${title}出错，请查看运行日志`);
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
      notifyError(error, { title: '扫描印象笔记导入目录失败' });
    }
  });

  document.getElementById('yinxiang-import-one')?.addEventListener('click', async () => {
    try {
      const args = buildYinxiangImportArgs({ single: true });
      if (!(await confirmYinxiangImportWrite({ single: true }))) return;
      await runYinxiangImportCommand(args, '印象笔记单篇导入测试', '正在导入第一篇 Markdown...');
    } catch (error) {
      notifyError(error, { title: '印象笔记单篇导入失败' });
    }
  });

  document.getElementById('yinxiang-import-export')?.addEventListener('click', async () => {
    try {
      const args = buildYinxiangImportArgs();
      if (!(await confirmYinxiangImportWrite())) return;
      await runYinxiangImportCommand(args, '印象笔记批量导入', '正在批量导入 Markdown...');
    } catch (error) {
      notifyError(error, { title: '印象笔记批量导入失败' });
    }
  });

  document.getElementById('yinxiang-import-stop')?.addEventListener('click', handleStop);
  document.getElementById('yinxiang-import-open-dir')?.addEventListener('click', async () => {
    const dir = document.getElementById('yinxiang-import-source')?.value.trim();
    if (dir) {
      await window.electronAPI.openPath(dir);
    } else {
      notifyUser('请先选择 Markdown 目录。', 'warn');
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

async function confirmLargeZsxqGroupExport(toolId) {
  if (toolId !== 'zsxq-group') return true;
  const limit = zsxqGroupLimitValue();
  if (limit <= 1000) return true;
  return confirmUserAction(
    `本次计划导出 ${limit} 条知识星球帖子。\n\n` +
    '连续长时间导出可能触发平台风控，严重时可能影响账号使用甚至被封号。\n' +
    '建议分批导出，并尽量不要让单次任务超过 24 小时。\n\n' +
    '确认继续导出吗？',
    {
      title: '高风险批量导出确认',
      confirmLabel: '继续导出',
      danger: true
    }
  );
}

async function confirmZsxqRecursiveExport(toolId) {
  if (toolId !== 'zsxq-group') return true;
  const followRelatedLinks = document.getElementById(`${toolId}-follow-related-links`);
  if (!followRelatedLinks?.checked) return true;
  const maxDepth = document.getElementById(`${toolId}-max-depth`)?.value;
  const followLinkScope = document.getElementById(`${toolId}-follow-link-scope`)?.value;
  const depth = Number.parseInt(maxDepth || '1', 10);
  if (followLinkScope !== 'all' && depth <= 1) return true;
  return confirmUserAction(
    '关联帖子递归会显著增加请求并更容易触发知识星球风控。确认继续吗？',
    {
      title: '确认递归抓取关联帖子',
      confirmLabel: '继续递归导出',
      danger: true
    }
  );
}

async function confirmZsxqExportPreflight(toolId, options = {}) {
  if (options.includeLarge !== false && !(await confirmLargeZsxqGroupExport(toolId))) return false;
  if (!(await confirmZsxqRecursiveExport(toolId))) return false;
  return true;
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
  appendAutoOutputFolderArg(args, prefix, forScan);

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
    const followLinkScope = document.getElementById(`${prefix}-follow-link-scope`)?.value;
    const followRelatedLinks = document.getElementById(`${prefix}-follow-related-links`);
    const shouldFollowRelatedLinks = toolId !== 'zsxq-group' || Boolean(followRelatedLinks?.checked);
    if (toolId === 'zsxq-group' && !shouldFollowRelatedLinks) {
      // Always pass explicit safe values.  Old form drafts may still contain
      // `all` and a deep recursion value from releases before this checkbox.
      args.push('--max-depth', '0', '--follow-link-scope', 'none');
    } else {
      if (maxDepth) args.push('--max-depth', maxDepth);
      if (followLinkScope) args.push('--follow-link-scope', followLinkScope);
      if (toolId === 'zsxq-group') {
        args.push('--follow-group-links');
      }
    }

    const groupScope = document.getElementById(`${prefix}-group-scope`)?.value;
    if (groupScope) args.push('--group-scope', groupScope);

    const limitInput = document.getElementById(`${prefix}-limit`)?.value;
    if (!forScan && toolId === 'zsxq-group') {
      const limit = zsxqGroupLimitValue();
      if (!Number.isFinite(limit) || limit < 1) {
        throw new Error('知识星球 Group 单次导出数量至少为 1 条。');
      }
      args.push('--limit', String(limit));
      for (const [field, flag] of [
        ['long-sleep-after', '--long-sleep-after'],
        ['long-sleep-every', '--long-sleep-every'],
        ['long-sleep-min', '--long-sleep-min'],
        ['long-sleep-max', '--long-sleep-max']
      ]) {
        const value = document.getElementById(`${prefix}-${field}`)?.value;
        if (value !== undefined && value !== '') args.push(flag, value);
      }
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
    const includeSource = document.getElementById('yuque-include-source');
    if (!forScan && includeSource && !includeSource.checked) {
      args.push('--no-source');
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
    notifyUser('请先点击“读取目录”。', 'warn');
    return;
  }
  state.selected = new Set(selected ? selectableTocIds(state.nodes) : []);
  renderToc(toolId);
}

function invertTocSelection(toolId) {
  const state = tocStates[toolId];
  if (!state?.loaded) {
    notifyUser('请先点击“读取目录”。', 'warn');
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
        <button class="toc-item toc-depth-${depth}${hasSelectableItems ? '' : ' toc-item-empty'}" type="button" data-node-id="${escapeHtml(node.nodeId)}" data-depth="${depth}"${selectionAttributes}>
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
  // The renderer CSP blocks style attributes in generated HTML. Set the shared
  // CSS variable through the CSSOM after the trusted renderer has created nodes.
  list.querySelectorAll('.toc-item[data-depth]').forEach((item) => {
    const depth = Number.parseInt(item.dataset.depth, 10);
    const indent = Number.isFinite(depth) && depth > 0 ? depth * 40 : 0;
    item.style.setProperty('--toc-indent', `${indent}px`);
  });
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
    notifyError(error, { title: '读取目录参数检查失败' });
    return;
  }
  if (!(await confirmZsxqExportPreflight(toolId, { includeLarge: false }))) return;
  if (!(await confirmProviderExecution(config))) return;

  startProgress(`读取目录：${config.title}`, '正在连接远端服务，准备读取目录结构...', { phase: 'directory' });
  log(`开始读取目录：${config.title}`, 'info');

  try {
    const result = await runProviderCommand(config.script, args, {
      providerId: toolId,
      title: `读取目录：${config.title}`,
      action: '读取目录',
      track: false
    });
    if (isStoppedResult(result)) {
      log('读取目录已停止。', 'warn');
      finishProgress('stopped', '读取目录已停止');
      return;
    }
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
    notifyError(error, { title: `${actionName}参数检查失败` });
    return;
  }
  if (!(await confirmZsxqExportPreflight(toolId))) return;
  if (toolId === 'yuque-import' && !(await confirmYuqueImportWrite())) return;
  if (!(await confirmProviderExecution(config))) return;

  startProgress(`${actionName}：${config.title}`, `正在准备${actionName}任务...`);
  log(`开始${actionName}：${config.title}`, 'info');
  const state = tocStates[toolId];
  if (state?.loaded) {
    log(`本次按目录选择导出：已选择 ${state.selected.size} 篇。`, 'info');
    updateProgress(0, state.selected.size, `已选择 ${state.selected.size} 篇，正在读取远端内容...`);
  }
  if (actionName === '导出' && providerSupportsMarkdownPreview(config)) {
    window.WandaoMarkdownDock?.prepareLiveExport({
      outputPath: markdownPreviewOutputPath(config, args),
      title: config.title,
      originTool: toolId
    });
  }

  try {
    const result = await runProviderCommand(config.script, args, {
      providerId: toolId,
      title: `${actionName}：${config.title}`,
      action: actionName,
      track: true
    });
    if (isStoppedResult(result)) {
      log(`${actionName}已停止，已完成项目会在下次继续时跳过。`, 'warn');
      finishProgress('stopped', `${actionName}已停止`);
    } else if (result.success) {
      const outcome = taskResultStatus(result, { provider: toolId, mode: actionName });
      appendExportSuccessSponsorLogs(outcome, actionName, taskReportForResult(result, { provider: toolId, mode: actionName }));
      appendExportResourceRecoveryLog(outcome, actionName, taskReportForResult(result, { provider: toolId, mode: actionName }));
      if (result.data) {
        log(JSON.stringify(result.data, null, 2), 'info');
      }
      finishProgressForTaskResult(result, `${actionName}完成`, {
        provider: toolId,
        mode: actionName
      });
      logTaskResultCompletion(result, `${actionName}完成`, {
        provider: toolId,
        mode: actionName
      });
    } else {
      log(`${actionName}失败：${result.error}`, 'error');
      finishProgressForTaskResult(result, `${actionName}完成`, {
        provider: toolId,
        mode: actionName,
        failedDetail: `${actionName}失败，请查看运行日志`
      });
    }
  } catch (error) {
    log(`错误：${formatError(error)}`, 'error');
    finishProgress(false, `${actionName}出错，请查看运行日志`);
  } finally {
    if (actionName === '导出' && providerSupportsMarkdownPreview(config)) {
      window.WandaoMarkdownDock?.stopLiveExport();
    }
  }
}

// Handle stop
async function handleStop() {
  const result = await window.electronAPI.stopPythonProcess();
  if (result.success) {
    const task = activeHistoryTask;
    if (task) {
      if (task.pendingSave) {
        await task.pendingSave.catch(() => {});
        delete task.pendingSave;
      }
      task.stopRequested = true;
      task.stopRequestedAt = new Date().toISOString();
      if (activeHistoryTask?.id === task.id && task.status === 'running') {
        task.status = 'stopping';
        task.error = '';
        await saveTaskHistory();
        renderTaskHistory();
        renderTaskStatusOrb();
      }
    }
    startProgress('正在停止任务', '已发送停止请求，等待当前进程退出...');
  }
  log(result.success ? '已发送停止请求' : result.error, result.success ? 'info' : 'error');
}

function applyPythonProcessState(state = {}) {
  const previous = mainPythonProcessState;
  mainPythonProcessState = {
    running: Boolean(state.running),
    stopping: Boolean(state.stopping),
    providerId: String(state.providerId || ''),
    taskId: String(state.taskId || ''),
    startedAt: String(state.startedAt || ''),
    lastStatus: String(state.lastStatus || '')
  };
  if (mainPythonProcessState.running) {
    if (!activeCommandOwner) {
      recoveredCommandOwner = Symbol('recovered-python-command');
      activeCommandOwner = recoveredCommandOwner;
      setProviderRunning(mainPythonProcessState.providerId || currentTool, true);
      startProgress(
        mainPythonProcessState.stopping ? '正在停止已恢复的任务' : '任务仍在后台运行',
        mainPythonProcessState.stopping
          ? '已发送停止请求，等待后台进程退出...'
          : '界面曾重新加载，已恢复导航锁和全局停止按钮。'
      );
      log('检测到主进程中仍在运行的任务，已恢复运行状态。', 'warn');
    }
    return;
  }
  if (!recoveredCommandOwner) return;
  const finishedTaskId = mainPythonProcessState.taskId || previous.taskId;
  const recoveredTask = taskHistory.find((task) => task.id === finishedTaskId);
    if (recoveredTask && ['running', 'stopping'].includes(recoveredTask.status)) {
      recoveredTask.status = mainPythonProcessState.lastStatus === 'stopped' ? 'stopped' : 'interrupted';
    recoveredTask.finishedAt = new Date().toISOString();
    recoveredTask.error = mainPythonProcessState.lastStatus === 'stopped'
      ? ''
      : '任务在界面重载期间结束，最终结果未能回传；可以根据输出或检查点继续。';
    saveTaskHistory().catch((error) => log(`保存恢复任务状态失败：${formatError(error)}`, 'error'));
    renderTaskHistory();
    renderTaskStatusOrb();
  }
  if (activeCommandOwner === recoveredCommandOwner) activeCommandOwner = null;
  recoveredCommandOwner = null;
  setProviderRunning(previous.providerId || currentTool, false);
  if (mainPythonProcessState.lastStatus === 'stopped') {
    finishProgress('stopped', '后台任务已停止');
  } else {
    finishProgress('attention', '后台任务已结束，请检查输出或在任务中心继续');
  }
}

function initializePythonProcessStateSync() {
  window.electronAPI.onPythonProcessState?.((state) => applyPythonProcessState(state));
  pythonProcessStateReady = window.electronAPI.getPythonProcessState
    ? window.electronAPI.getPythonProcessState()
      .then((state) => applyPythonProcessState(state))
      .catch((error) => appendDetailedLog('python-process-state', 'error', formatError(error)))
    : Promise.resolve();
  return pythonProcessStateReady;
}

function initializePluginDownloadProgress() {
  window.electronAPI.onPluginDownloadProgress?.((progress) => {
    const pluginId = String(progress?.pluginId || '');
    if (!pluginId || !pluginOperationState.has(pluginId)) return;
    pluginOperationState.set(pluginId, {
      phase: progress?.phase === 'downloading' ? 'downloading' : 'preparing',
      receivedBytes: Number(progress?.receivedBytes) || 0,
      totalBytes: Number(progress?.totalBytes) || 0
    });
    if (currentTool === 'plugin-center') refreshPluginProgressUi(pluginId);
  });
}

function onboardingIsDismissed() {
  try {
    return localStorage.getItem(ONBOARDING_DISMISSED_STORAGE_KEY) === 'true';
  } catch {
    return false;
  }
}

function closeOnboarding({ openTutorial = false } = {}) {
  const backdrop = document.getElementById('onboarding-backdrop');
  const neverShowAgain = document.getElementById('onboarding-dismiss-forever');
  if (neverShowAgain?.checked) {
    try {
      localStorage.setItem(ONBOARDING_DISMISSED_STORAGE_KEY, 'true');
    } catch {
      // The app remains usable when browser storage is unavailable.
    }
  }
  if (backdrop) backdrop.hidden = true;
  if (openTutorial) switchTool('notice-center');
}

function initializeOnboarding() {
  const backdrop = document.getElementById('onboarding-backdrop');
  if (!backdrop || onboardingIsDismissed()) return;
  backdrop.hidden = false;
  window.setTimeout(() => {
    document.getElementById('btn-onboarding-tutorial')?.focus();
  }, 0);
}

function isPrimaryWorkbenchView(targetTool) {
  return PRIMARY_NAV_ITEMS.some((item) => item.id === targetTool);
}

function isAllowedWhileRunningControl(control) {
  if (!control?.matches) return false;
  if (control.matches('[id$="-stop"], [id$="-login-done"], [data-task-orb-action], ' +
    '[data-history-action="copy"], [data-history-action="copy-failures"], ' +
    '[data-history-action="export-failure-log"], [data-history-action="open-output"], ' +
    '[data-history-action="open-report"], [data-history-action="locate-wiz-note"], ' +
    '[data-history-action="locate-source-page"], [data-history-action="load-more"], ' +
    '[data-history-action="expand-failures"]')) {
    return true;
  }
  if (control.matches('[data-tool]')) return isPrimaryWorkbenchView(control.dataset.tool);
  if (control.matches('[data-switch-view]')) return isPrimaryWorkbenchView(control.dataset.switchView);
  if (control.matches('[data-notice-id], [data-notice-action], [data-notice-open], [data-open-url], ' +
    '[data-plugin-search], [data-plugin-search-clear], [data-plugin-refresh], [data-plugin-update-all]')) {
    return true;
  }
  if (control.matches('[data-plugin-action]')) {
    return !pluginOperationBlocked(control.dataset.pluginId);
  }
  return false;
}

function applyRunningControlState() {
  const lockableControls = document.querySelectorAll(
    '#content-area button, #content-area input, #content-area select, #content-area textarea, ' +
    '[data-switch-view], [data-platform-key], [data-open-provider], ' +
    '[data-history-action="resume"], #btn-history-resume-last, #btn-history-refresh'
  );
  lockableControls.forEach((control) => {
    if (isAllowedWhileRunningControl(control)) return;
    if (isRunning) {
      if (!control.disabled) {
        control.disabled = true;
        control.dataset.disabledByRunning = 'true';
        control.setAttribute('aria-disabled', 'true');
        if (!control.title) {
          control.dataset.runningTitleAdded = 'true';
          control.title = '任务运行中，请等待任务结束或先停止任务';
        }
      }
    } else if (control.dataset.disabledByRunning === 'true') {
      control.disabled = false;
      delete control.dataset.disabledByRunning;
      control.removeAttribute('aria-disabled');
      if (control.dataset.runningTitleAdded === 'true') {
        control.removeAttribute('title');
        delete control.dataset.runningTitleAdded;
      }
    }
  });

  document.querySelectorAll('[id$="-stop"]').forEach((button) => {
    button.disabled = !isRunning;
    button.setAttribute('aria-disabled', String(!isRunning));
  });
  const globalStopButton = document.getElementById('btn-global-stop');
  if (globalStopButton) {
    globalStopButton.disabled = !isRunning;
    globalStopButton.setAttribute('aria-disabled', String(!isRunning));
  }
}

// Set running state
function setRunning(running, toolId) {
  const wasRunning = isRunning;
  isRunning = running;
  applyRunningControlState();
  renderProviderNavigation();
  if (wasRunning !== running) {
    if (currentTool === 'plugin-center') renderPluginCenterPage();
    if (currentTool === 'platform-center') renderPlatformCenterPage();
  }
}

function feishuImportConfigPath() {
  if (appPaths?.userData) {
    return `${appPaths.userData}/plugin-data/feishu/feishu_import_config.json`;
  }
  if (appPaths?.projectRoot) {
    return `${appPaths.projectRoot}/.feishu_import_config.json`;
  }
  return '';
}

function feishuImportConfigFallbackPaths() {
  return [
    appPaths?.userData ? `${appPaths.userData}/feishu_import_config.json` : '',
    appPaths?.userData ? `${appPaths.userData}/.feishu_import_config.json` : '',
    appPaths?.userData ? `${appPaths.userData}/plugin-data/feishu/.feishu_import_config.json` : '',
    appPaths?.projectRoot ? `${appPaths.projectRoot}/feishu_import_config.json` : '',
    appPaths?.projectRoot ? `${appPaths.projectRoot}/.feishu_import_config.json` : ''
  ];
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
    notifyUser('请先填写飞书 App ID，再打开 API 权限申请页。', 'warn');
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
    notifyUser('请先填写飞书 App ID，再打开版本发布页。', 'warn');
    return false;
  }
  const result = await window.electronAPI.openExternal(url);
  log(result.success ? '已打开飞书版本管理与发布页。权限改动需要发布应用新版本后才会生效。' : `打开版本发布页失败：${result.error}`, result.success ? 'info' : 'error');
  return Boolean(result.success);
}

async function openFeishuTargetWikiPage() {
  const wikiUrl = document.getElementById('feishu-import-url')?.value.trim() || '';
  if (!wikiUrl) {
    notifyUser('请先填写目标飞书 Wiki URL。', 'warn');
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
  if (!(await confirmUserAction('这会尝试修改目标 Wiki 的文档应用权限。请确认目标 Wiki 与当前飞书应用无误后继续。', {
    title: '确认授权目标 Wiki',
    confirmLabel: '继续授权',
    danger: true
  }))) return null;
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
  if (!result.success) throw new Error(`读取 JSON 文件失败（${filePath}）：${result.error || '未知错误'}`);
  try {
    return JSON.parse(result.content);
  } catch (error) {
    throw new Error(`JSON 文件格式损坏（${filePath}）：${error.message || error}`);
  }
}

async function readJsonConfigWithMigration(canonicalPath, legacyPaths, label) {
  const canonical = await readJsonFileIfExists(canonicalPath);
  if (canonical && typeof canonical === 'object') return canonical;
  const candidates = Array.from(new Set((legacyPaths || []).filter((item) => item && item !== canonicalPath)));
  for (const legacyPath of candidates) {
    const legacy = await readJsonFileIfExists(legacyPath);
    if (!legacy || typeof legacy !== 'object') continue;
    const result = await window.electronAPI.writeFile(canonicalPath, JSON.stringify(legacy, null, 2));
    if (result?.success) {
      log(`已将旧版${label}迁移到统一配置目录：${canonicalPath}`, 'info');
    } else {
      log(`迁移${label}失败，将临时读取旧配置：${result?.error || canonicalPath}`, 'warn');
    }
    return legacy;
  }
  return null;
}

async function loadFeishuImportConfigIntoForm() {
  const primary = feishuImportConfigPath();
  const config = await readJsonConfigWithMigration(
    primary,
    feishuImportConfigFallbackPaths(),
    '飞书导入 API 配置'
  );
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
    notifyUser('请先填写飞书 App ID 和 App Secret。', 'warn');
    return;
  }
  const configPath = feishuImportConfigPath();
  if (!configPath) {
    notifyUser('无法获取本机配置目录。', 'error');
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
    notifyUser('已保存到本机配置文件，下次打开会自动读取。', 'success');
  } else {
    log(`保存配置失败：${result.error}`, 'error');
    notifyError(new Error(result.error || '保存配置失败'), { title: '保存飞书配置失败' });
  }
}

async function applyProbedFeishuTarget(data) {
  const spaceId = String(data?.spaceId || '').trim();
  const parentWikiToken = String(data?.targetWikiToken || '').trim();
  if (!spaceId || !parentWikiToken) {
    const message = '探测结果不完整：未同时返回 Space ID 和目标 Wiki Token，未替换现有目标配置。';
    log(message, 'error');
    notifyUser(message, 'error', { title: '应用飞书探测结果失败' });
    return { updated: false, saved: false };
  }

  const spaceInput = document.getElementById('feishu-import-space-id');
  const parentInput = document.getElementById('feishu-import-parent-token');
  if (!spaceInput || !parentInput) {
    const message = '飞书导入目标字段不可用，无法应用本次探测结果。';
    log(message, 'error');
    notifyUser(message, 'error', { title: '应用飞书探测结果失败' });
    return { updated: false, saved: false };
  }

  spaceInput.value = spaceId;
  parentInput.value = parentWikiToken;

  const appId = document.getElementById('feishu-import-app-id')?.value.trim()
    || feishuImportConfig.app_id
    || '';
  const appSecret = document.getElementById('feishu-import-app-secret')?.value.trim()
    || feishuImportConfig.app_secret
    || '';
  const nextConfig = {
    ...feishuImportConfig,
    ...(appId ? { app_id: appId } : {}),
    ...(appSecret ? { app_secret: appSecret } : {}),
    space_id: spaceId,
    parent_wiki_token: parentWikiToken,
    obj_type: feishuImportConfig.obj_type || 'docx'
  };
  const configPath = feishuImportConfigPath();
  if (!configPath) {
    const message = '已将目标 Wiki 刷新到当前界面，但未能保存到本机配置：无法获取配置目录。';
    log(message, 'warn');
    notifyUser(message, 'warn', { title: '飞书目标已更新但未保存', duration: 0 });
    return { updated: true, saved: false, spaceId, parentWikiToken };
  }

  const writeResult = await window.electronAPI.writeFile(configPath, JSON.stringify(nextConfig, null, 2));
  if (!writeResult?.success) {
    const message = `已将目标 Wiki 刷新到当前界面，但未能保存到本机配置：${writeResult?.error || '未知错误'}`;
    log(message, 'warn');
    notifyUser(message, 'warn', { title: '飞书目标已更新但未保存', duration: 0 });
    return { updated: true, saved: false, spaceId, parentWikiToken };
  }

  feishuImportConfig = nextConfig;
  log('已将目标 Wiki 更新为最新探测结果并保存到本机配置。', 'success');
  return { updated: true, saved: true, spaceId, parentWikiToken };
}

// Load Feishu Import Tool (reuse existing import code)
function loadFeishuImportTool() {
  const contentArea = document.getElementById('content-area');
  contentArea.innerHTML = `
    <div class="tool-panel">
      <section class="form-section">
        <div class="form-group">
          <label for="feishu-import-url">目标飞书 Wiki URL <span class="required">*</span></label>
          <input type="url" id="feishu-import-url" data-history-kind="url" data-history-key="wiki_url" data-history-label="目标飞书 Wiki URL" placeholder="https://<tenant>.feishu.cn/wiki/<token>">
        </div>
        <div class="form-group">
          <label for="feishu-import-source">本地 Markdown 目录 <span class="required">*</span></label>
          <div class="input-with-button">
            <input type="text" id="feishu-import-source" data-history-kind="path" data-history-key="source_dir" data-history-label="本地 Markdown 目录" placeholder="选择包含 Markdown 文件的目录">
            <button class="btn-secondary" id="feishu-import-browse-source">浏览</button>
          </div>
        </div>
        <div class="form-group">
          <label for="feishu-import-source-file">单篇测试文件（可选）</label>
          <div class="input-with-button">
            <input type="text" id="feishu-import-source-file" data-history-kind="path" data-history-key="source_file" data-history-label="单篇测试文件" placeholder="留空则使用目录内第一篇">
            <button class="btn-secondary" id="feishu-import-browse-file">浏览</button>
          </div>
        </div>
        <div class="form-row">
          <div class="form-group flex-1">
            <label for="feishu-import-app-id">飞书 App ID <span class="required">*</span></label>
            <input type="text" id="feishu-import-app-id" data-draft="false" placeholder="从飞书开放平台获取">
          </div>
          <div class="form-group flex-1">
            <label for="feishu-import-app-secret">飞书 App Secret <span class="required">*</span></label>
            <input type="password" id="feishu-import-app-secret" data-draft="false" placeholder="保密信息">
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
                <input type="text" id="feishu-import-space-id" data-draft="false" placeholder="留空自动探测">
              </div>
              <div class="form-group flex-1">
                <label for="feishu-import-parent-token">父级 Wiki token（可自动探测）</label>
                <input type="text" id="feishu-import-parent-token" data-draft="false" placeholder="留空使用 URL 中的 token">
              </div>
            </div>
            <label class="checkbox-label">
              <input type="checkbox" id="feishu-import-move-to-wiki" checked>
              <span>导入后移动到目标 Wiki</span>
            </label>
            <label class="checkbox-label">
              <input type="checkbox" id="feishu-import-use-filename-as-title">
              <span>使用 Markdown 文件名作为飞书标题（保留 01- 等序号）</span>
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
  if (document.getElementById('feishu-import-use-filename-as-title').checked) args.push('--use-filename-as-title');
  if (document.getElementById('feishu-import-skip-rename').checked) args.push('--skip-rename');
  if (!document.getElementById('feishu-import-repair-images').checked) args.push('--skip-image-repair');
  if (document.getElementById('feishu-import-require-image-repair').checked) args.push('--require-image-repair');
  return args;
}

function feishuActionAttentionMessage(data) {
  if (!data || typeof data !== 'object') return '';
  if (data.loginRequired === true) return '尚未获取登录凭证，请先完成飞书网页版登录。';
  if (Array.isArray(data.missingScopes) && data.missingScopes.length) {
    return `仍缺少 ${data.missingScopes.length} 项开放平台权限，请在飞书后台开通并发布新版本。`;
  }
  if (data.hasBot === false) return '飞书应用身份尚未就绪，请检查机器人/应用能力并发布版本。';
  if (String(data.status || '').toLowerCase() === 'manual-required') {
    return '还需在目标 Wiki 页面手动添加当前应用为文档应用。';
  }
  return '';
}

async function runFeishuImportCommand(args, taskName) {
  startProgress(taskName, '任务启动中，正在等待进度信息...');
  log(`开始：${taskName}`, 'info');
  try {
    const provider = TOOLS['feishu-import'];
    if (!provider?.script) throw new Error('飞书导入未提供脚本');
    const result = await runProviderCommand(provider.script, args, {
      providerId: 'feishu-import',
      title: taskName,
      action: '导入',
      track: shouldTrackTask(taskName)
    });
    if (isStoppedResult(result)) {
      log(`${taskName}已停止，已完成项目会在下次继续时跳过。`, 'warn');
      finishProgress('stopped', `${taskName}已停止`);
      return null;
    }
    if (result.success) {
      const attentionMessage = feishuActionAttentionMessage(result.data);
      if (attentionMessage) {
        log(`${taskName}还需操作：${attentionMessage}`, 'warn');
        finishProgress('attention', attentionMessage);
        return result.data || {};
      }
      log(JSON.stringify(result.data || {}, null, 2), 'info');
      finishProgressForTaskResult(result, `${taskName}完成`, {
        provider: 'feishu-import',
        mode: '导入'
      });
      logTaskResultCompletion(result, `${taskName}完成`, {
        provider: 'feishu-import',
        mode: '导入'
      });
      return result.data || {};
    }
    log(`失败：${result.error}`, 'error');
    finishProgressForTaskResult(result, `${taskName}完成`, {
      provider: 'feishu-import',
      mode: '导入',
      failedDetail: `${taskName}失败，请查看运行日志`
    });
    await maybeOpenFeishuPermissionPage(result.error);
  } catch (error) {
    const message = formatError(error);
    log(`错误：${message}`, 'error');
    finishProgress(false, `${taskName}出错，请查看运行日志`);
    await maybeOpenFeishuPermissionPage(message);
  }
  return null;
}

function requireFeishuWikiUrl() {
  const wikiUrl = document.getElementById('feishu-import-url')?.value.trim();
  if (!wikiUrl) {
    notifyUser('请先填写目标飞书 Wiki URL。', 'warn');
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
  loadFeishuImportConfigIntoForm()
    .then(() => restoreFormDraftForProvider('feishu-import'))
    .catch((error) => {
      log(`读取飞书导入 API 配置失败：${error.message || error}`, 'error');
      restoreFormDraftForProvider('feishu-import');
    });
  document.getElementById('feishu-import-open-console').addEventListener('click', async () => {
    await window.electronAPI.openExternal(FEISHU_DEVELOPER_CONSOLE_URL);
    notifyUser('已打开飞书开放平台。请创建企业自建应用，并在“凭证与基础信息”复制 App ID 和 App Secret。', 'info', {
      title: '飞书开放平台已打开',
      duration: 0
    });
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
      await applyProbedFeishuTarget(data);
    }
  });
  document.getElementById('feishu-import-plan').addEventListener('click', async () => {
    if (!requireFeishuWikiUrl()) return;
    await runFeishuImportCommand([...buildFeishuImportArgs(), '--plan'], '生成导入计划');
  });
  document.getElementById('feishu-import-one').addEventListener('click', async () => {
    if (!requireFeishuWikiUrl()) return;
    if (!document.getElementById('feishu-import-source')?.value.trim()) {
      notifyUser('请先选择本地 Markdown 目录。', 'warn');
      return;
    }
    const args = [...buildFeishuImportArgs(), '--api-import-one', '--yes'];
    if (!(await confirmFeishuImportWrite({ single: true }))) return;
    await runFeishuImportCommand(args, '单篇导入测试');
  });
  document.getElementById('feishu-import-all').addEventListener('click', async () => {
    if (!requireFeishuWikiUrl()) return;
    if (!document.getElementById('feishu-import-source')?.value.trim()) {
      notifyUser('请先选择本地 Markdown 目录。', 'warn');
      return;
    }
    const args = [...buildFeishuImportArgs(), '--api-import-all', '--yes'];
    if (!(await confirmFeishuImportWrite())) return;
    await runFeishuImportCommand(args, '批量导入');
  });
  document.getElementById('feishu-import-stop').addEventListener('click', handleStop);
  document.getElementById('feishu-import-open-dir').addEventListener('click', async () => {
    const dir = document.getElementById('feishu-import-source').value.trim();
    if (dir) await window.electronAPI.openPath(dir);
  });
}

// Initialize the shell immediately; slower provider discovery continues in the background.
document.addEventListener('DOMContentLoaded', () => {
  initializeRendererDiagnostics();
  applyTheme(loadTheme());
  loadSidebarState();
  initializeFormDraftPersistence();
  initializePythonProcessStateSync();
  initializePluginDownloadProgress();
  window.WandaoMarkdownDock?.init();
  renderProviderNavigation();
  document.addEventListener('click', (event) => {
    if (!isRunning) return;
    const control = event.target?.closest?.(
      '#content-area button, [data-tool], [data-switch-view], [data-platform-key], ' +
      '[data-open-provider], [data-history-action="resume"], ' +
      '#btn-history-resume-last, #btn-history-refresh'
    );
    if (!control || isAllowedWhileRunningControl(control)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
  }, true);

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
    if (event.target.closest('[data-sidebar-toggle]')) {
      toggleSidebar();
      return;
    }
    const item = event.target.closest('.nav-item');
    if (!item) return;
    switchTool(item.dataset.tool);
  });
  document.getElementById('provider-sidebar')?.addEventListener('pointerdown', startSidebarResize);
  document.getElementById('provider-sidebar')?.addEventListener('pointermove', moveSidebarResize);
  document.getElementById('provider-sidebar')?.addEventListener('pointerup', finishSidebarResize);
  document.getElementById('provider-sidebar')?.addEventListener('pointercancel', finishSidebarResize);
  document.getElementById('provider-sidebar')?.addEventListener('keydown', handleSidebarResizeKeydown);

  // Setup footer buttons
  document.getElementById('btn-clear-log').addEventListener('click', clearLog);
  document.getElementById('btn-global-stop')?.addEventListener('click', handleStop);
  document.getElementById('btn-open-live-markdown')?.addEventListener('click', () => {
    const liveContext = window.WandaoMarkdownDock?.openLiveExportReader();
    if (!liveContext?.outputPath) return;
    if (!switchTool('markdown-reader')) return;
    window.WandaoMarkdownDock?.loadFolder(liveContext.outputPath, {
      remember: false,
      live: true
    }).catch((error) => {
      log(`打开正在导出的 Markdown 目录失败：${formatError(error)}`, 'error');
    });
  });
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
      notifyUser('没有可继续的失败或中断任务。', 'info');
      return;
    }
    resumeTask(task);
  });
  document.getElementById('task-history-search')?.addEventListener('input', (event) => {
    taskHistoryFilters.query = event.target.value;
    resetTaskHistoryVisibleLimit();
    renderTaskHistory();
  });
  document.getElementById('task-history-status')?.addEventListener('change', (event) => {
    taskHistoryFilters.status = event.target.value;
    resetTaskHistoryVisibleLimit();
    renderTaskHistory();
  });
  document.getElementById('task-history-provider')?.addEventListener('change', (event) => {
    taskHistoryFilters.providerId = event.target.value;
    resetTaskHistoryVisibleLimit();
    renderTaskHistory();
  });
  document.getElementById('btn-history-clear-filters')?.addEventListener('click', clearTaskHistoryFilters);
  document.getElementById('task-history-list')?.addEventListener('click', (event) => {
    const externalLink = event.target.closest('a[data-external-link]');
    if (externalLink) {
      event.preventDefault();
      window.electronAPI.openExternal(externalLink.href)
        .then((result) => {
          if (!result?.success) {
            log(`打开外部链接失败：${result?.error || externalLink.href}`, 'error');
          }
        })
        .catch((error) => log(`打开外部链接失败：${formatError(error)}`, 'error'));
      return;
    }
    const button = event.target.closest('[data-history-action]');
    if (button?.dataset.historyAction === 'clear-filters') {
      clearTaskHistoryFilters();
      return;
    }
    if (button?.dataset.historyAction === 'load-more') {
      taskHistoryVisibleLimit += TASK_HISTORY_RENDER_LIMIT;
      renderTaskHistory();
      document.querySelector('[data-history-action="load-more"]')?.scrollIntoView({
        block: 'nearest',
        behavior: 'smooth'
      });
      return;
    }
    const item = event.target.closest('[data-task-id]');
    if (!button || !item) return;
    const task = taskHistory.find((entry) => entry.id === item.dataset.taskId);
    if (!task) return;
    if (button.dataset.historyAction === 'expand-failures') {
      expandTaskFailureDetails(button, task);
      return;
    }
    if (button.dataset.historyAction === 'copy-failures') {
      handleTaskAction(task, 'copy-failures')
        .catch((error) => log(`执行任务操作失败：${formatError(error)}`, 'error'));
      return;
    }
    handleTaskAction(task, button.dataset.historyAction, {
      documentId: button.dataset.documentId,
      knowledgeBaseId: button.dataset.knowledgeBaseId,
      documentUrl: button.dataset.documentUrl
    })
      .catch((error) => log(`执行任务操作失败：${formatError(error)}`, 'error'));
  });
  document.getElementById('task-status-orb')?.addEventListener('click', (event) => {
    if (Date.now() < taskStatusOrbSuppressClickUntil) {
      event.preventDefault();
      event.stopPropagation();
      taskStatusOrbSuppressClickUntil = 0;
      return;
    }
    const action = event.target.closest('[data-task-orb-action]')?.dataset.taskOrbAction;
    const task = activeTaskStatusOrbTask();
    if (!action) return;
    if (action === 'return') openTaskOrigin(task);
    if (action === 'task-center') switchTool('task-center');
    if (action === 'retry') resumeTask(task);
    if (action === 'dismiss') dismissTaskStatusOrb();
  });
  document.getElementById('btn-onboarding-dismiss')?.addEventListener('click', () => {
    closeOnboarding();
  });
  document.getElementById('btn-onboarding-tutorial')?.addEventListener('click', () => {
    closeOnboarding({ openTutorial: true });
  });
  document.getElementById('btn-theme-toggle')?.addEventListener('click', toggleTheme);
  document.getElementById('btn-check-update')?.addEventListener('click', () => checkForUpdates(false));
  document.getElementById('btn-star-project')?.addEventListener('click', () => {
    window.electronAPI.openExternal(GITHUB_REPO_URL);
  });
  document.getElementById('btn-install-update')?.addEventListener('click', installUpdate);
  document.getElementById('btn-open-release')?.addEventListener('click', () => {
    window.electronAPI.openExternal(latestReleaseUrl);
  });
  document.getElementById('btn-dismiss-update')?.addEventListener('click', hideUpdateBanner);

  document.getElementById('btn-about').addEventListener('click', () => {
    window.electronAPI.showAbout();
  });

  document.getElementById('btn-settings').addEventListener('click', toggleLogViewMode);
  document.getElementById('btn-load-earlier-log')?.addEventListener('click', loadEarlierLogEntries);
  renderLogPanel();
  setLogCollapsed(true);

  loadAppPaths();
  initializeOnboarding();

  if (window.electronAPI.onAppInfo) {
    window.electronAPI.onAppInfo((message) => {
      log(message, 'success');
    });
  }
  if (window.electronAPI.onUpdateCheckRequested) {
    window.electronAPI.onUpdateCheckRequested(() => checkForUpdates(false));
  }
  if (window.electronAPI.onUpdateProgress) {
    window.electronAPI.onUpdateProgress((payload) => {
      const detail = document.getElementById('update-detail');
      if (!detail || payload?.phase !== 'downloading') return;
      const downloaded = Number(payload.downloadedBytes || 0);
      const total = Number(payload.totalBytes || 0);
      if (total > 0) {
        detail.textContent = `正在下载更新… ${Math.min(100, Math.round((downloaded / total) * 100))}%`;
      } else {
        detail.textContent = `正在下载更新… ${(downloaded / 1024 / 1024).toFixed(1)} MB`;
      }
    });
  }
});
