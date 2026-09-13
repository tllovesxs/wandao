(function installMarkdownDock(root) {
  'use strict';

  const STORAGE_KEY = 'wandao-markdown-dock-v1';
  const RECENT_KEY = 'wandao-markdown-reader-recent-v1';
  const RECENT_FOLDER_KEY = 'wandao-markdown-reader-folders-v1';
  const DISMISSED_EXPORT_KEY = 'wandao-markdown-reader-dismissed-exports-v1';
  const DEFAULT_WIDTH = 520;
  const MIN_WIDTH = 360;
  const MAX_WIDTH = 800;
  const DEFAULT_TREE_WIDTH = 168;
  const MIN_TREE_WIDTH = 156;
  const MAX_TREE_WIDTH = 380;
  const TREE_COLLAPSE_SNAP_WIDTH = 120;
  const COLLAPSED_TREE_WIDTH = 0;
  const MAX_RECENT_FILES = 8;

  const state = {
    open: false,
    tab: 'reader',
    width: DEFAULT_WIDTH,
    treeWidth: DEFAULT_TREE_WIDTH,
    treeCollapsed: false,
    exportDirectories: [],
    liveExport: {
      mode: 'idle',
      outputPath: '',
      title: '',
      originTool: '',
      statusText: ''
    },
    reader: {
      status: 'idle',
      screen: 'start',
      path: '',
      title: '',
      content: '',
      error: '',
      headings: [],
      editing: false,
      saveStatus: 'idle',
      live: false,
      scanProgress: { scanned: 0, matched: 0, done: false },
      directory: '',
      tree: [],
      treeTruncated: false
    },
    preview: {
      status: 'idle',
      title: '',
      content: '',
      error: '',
      outputPath: '',
      currentPath: '',
      fileCount: 0,
      bytes: 0
    }
  };

  let dock = null;
  let readerHost = null;
  let initialized = false;
  let resizeSession = null;
  let previewTimer = null;
  let previewRefreshBusy = false;
  let readerResizeSession = null;
  const editorImageObservers = new WeakMap();
  const editorImageHydrationTimers = new WeakMap();
  let restoreLastFolderAttempted = false;
  let restoreLastFolderToken = null;
  let readerLoadToken = 0;
  let markdownTreeProgressUnsubscribe = null;
  let lastTreeProgressPaint = 0;
  let locationValidationScheduled = false;
  let liveReaderTimer = null;
  let liveReaderRefreshBusy = false;
  let liveReaderTreeSignature = '';
  let liveReaderCurrentMeta = '';
  const locationValidation = new Map();
  const readerTreeViewState = {
    scrollTop: 0,
    scrollLeft: 0,
    openPaths: new Set(),
    hasSnapshot: false
  };

  function storage() {
    try {
      return root.localStorage;
    } catch (_) {
      return null;
    }
  }

  function escapeHtml(value) {
    return String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function readStoredJson(key, fallback) {
    const store = storage();
    if (!store) return fallback;
    try {
      const parsed = JSON.parse(store.getItem(key) || 'null');
      return parsed ?? fallback;
    } catch (_) {
      return fallback;
    }
  }

  function writeStoredJson(key, value) {
    const store = storage();
    if (!store) return;
    try {
      store.setItem(key, JSON.stringify(value));
    } catch (_) {
      // A full or restricted localStorage must not prevent the reader opening.
    }
  }

  function clampWidth(value) {
    const viewportMax = Math.max(MIN_WIDTH, Math.floor(root.innerWidth * 0.62));
    return Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, viewportMax, Number(value) || DEFAULT_WIDTH));
  }

  function clampTreeWidth(value, layout = null) {
    const layoutWidth = Number(layout?.clientWidth) || Math.floor(root.innerWidth * 0.65) || 720;
    const max = Math.max(MIN_TREE_WIDTH, Math.min(MAX_TREE_WIDTH, Math.floor(layoutWidth * 0.45)));
    return Math.max(MIN_TREE_WIDTH, Math.min(max, Number(value) || DEFAULT_TREE_WIDTH));
  }

  function loadState() {
    const saved = readStoredJson(STORAGE_KEY, {});
    state.width = clampWidth(saved.width);
    state.treeWidth = clampTreeWidth(saved.treeWidth);
    state.treeCollapsed = saved.treeCollapsed === true;
    if (saved.tab === 'preview' || saved.tab === 'reader') state.tab = saved.tab;
  }

  function saveState() {
    writeStoredJson(STORAGE_KEY, {
      width: state.width,
      treeWidth: state.treeWidth,
      treeCollapsed: state.treeCollapsed,
      tab: state.tab
    });
  }

  function recentFolders() {
    const saved = readStoredJson(RECENT_FOLDER_KEY, []);
    return Array.isArray(saved)
      ? saved.filter((item) => item && typeof item.path === 'string' && item.path.trim()).slice(0, MAX_RECENT_FILES)
      : [];
  }

  function dismissedExportDirectories() {
    const saved = readStoredJson(DISMISSED_EXPORT_KEY, []);
    return new Set(Array.isArray(saved) ? saved.filter((item) => typeof item === 'string' && item.trim()) : []);
  }

  function rememberFolder(path, title) {
    const next = [
      { path, title: title || fileName(path), openedAt: Date.now() },
      ...recentFolders().filter((item) => item.path !== path)
    ].slice(0, MAX_RECENT_FILES);
    writeStoredJson(RECENT_FOLDER_KEY, next);
  }

  function defaultReaderState(overrides = {}) {
    return {
      status: 'idle',
      screen: 'start',
      path: '',
      title: '',
      content: '',
      error: '',
      headings: [],
      editing: false,
      saveStatus: 'idle',
      live: false,
      scanProgress: { scanned: 0, matched: 0, done: false },
      directory: '',
      tree: [],
      treeTruncated: false,
      ...overrides
    };
  }

  function removeRecentFolder(path) {
    writeStoredJson(RECENT_FOLDER_KEY, recentFolders().filter((item) => item.path !== path));
    render();
  }

  function dismissExportDirectory(path) {
    const paths = dismissedExportDirectories();
    paths.add(path);
    writeStoredJson(DISMISSED_EXPORT_KEY, Array.from(paths).slice(-MAX_RECENT_FILES));
    render();
  }

  function recentFiles() {
    const saved = readStoredJson(RECENT_KEY, []);
    return Array.isArray(saved)
      ? saved.filter((item) => item && typeof item.path === 'string' && item.path.trim()).slice(0, MAX_RECENT_FILES)
      : [];
  }

  function rememberFile(path, title) {
    const next = [
      { path, title: title || path.split(/[\\/]/).pop() || 'Markdown 文档', openedAt: Date.now() },
      ...recentFiles().filter((item) => item.path !== path)
    ].slice(0, MAX_RECENT_FILES);
    writeStoredJson(RECENT_KEY, next);
  }

  function fileName(path) {
    return String(path || '').split(/[\\/]/).pop() || 'Markdown 文档';
  }

  function parentDirectory(path) {
    const value = String(path || '').replace(/[\\/]+$/, '');
    const index = Math.max(value.lastIndexOf('\\'), value.lastIndexOf('/'));
    return index > 0 ? value.slice(0, index) : '';
  }

  function pathInsideDirectory(path, directory) {
    const normalizedPath = String(path || '').replace(/\\/g, '/').replace(/\/+$/, '').toLocaleLowerCase();
    const normalizedDirectory = String(directory || '').replace(/\\/g, '/').replace(/\/+$/, '').toLocaleLowerCase();
    return Boolean(normalizedPath && normalizedDirectory
      && (normalizedPath === normalizedDirectory || normalizedPath.startsWith(`${normalizedDirectory}/`)));
  }

  function renderDocument(source, options = {}) {
    if (!root.WandaoVditor?.placeholder) {
      throw new Error('Vditor 渲染器尚未加载。');
    }
    return {
      html: root.WandaoVditor.placeholder(source, {
        imageAttribute: Object.prototype.hasOwnProperty.call(options, 'imageAttribute')
          ? options.imageAttribute
          : 'data-md-image-src',
        allowImageSource: options.allowImageSource || (() => true),
        resolveImageSource: options.resolveImageSource,
        imageClass: options.imageClass || 'markdown-reader-image',
        externalLinkAttribute: options.externalLinkAttribute || 'data-md-external-link'
      }),
      headings: []
    };
  }

  function renderRecentFiles() {
    const files = recentFiles();
    if (!files.length) return '';
    return `
      <div class="markdown-reader-recent">
        <span class="markdown-reader-section-label">最近打开</span>
        ${files.map((item) => `
          <button class="markdown-reader-recent-item" data-md-recent-path="${escapeHtml(item.path)}" type="button">
            <strong>${escapeHtml(item.title || fileName(item.path))}</strong>
            <small>${escapeHtml(item.path)}</small>
          </button>
        `).join('')}
      </div>
    `;
  }

  function quickLocationItems() {
    const dismissed = dismissedExportDirectories();
    const exports = (state.exportDirectories || [])
      .filter((item) => item && item.path && !dismissed.has(item.path));
    const folders = recentFolders();
    const seen = new Set();
    return [
      ...exports.map((item) => ({
        kind: 'export',
        path: String(item.path),
        title: String(item.title || item.provider || fileName(item.path)),
        detail: [item.provider, item.finishedAt ? new Date(item.finishedAt).toLocaleString('zh-CN') : ''].filter(Boolean).join(' · ')
      })),
      ...folders.map((item) => ({
        kind: 'folder',
        path: String(item.path),
        title: String(item.title || fileName(item.path)),
        detail: '最近打开'
      }))
    ].filter((item) => {
      if (seen.has(item.path)) return false;
      seen.add(item.path);
      return true;
    });
  }

  function renderQuickLocation(item) {
    const check = locationValidation.get(item.path) || 'unknown';
    const status = check === 'available'
      ? (item.kind === 'export' ? '可打开' : '可用')
      : (check === 'unavailable' ? '目录不可用' : '检查中');
    const typeLabel = item.kind === 'export' ? '导出记录' : '最近文件夹';
    return `
      <div class="markdown-quick-location-row${check === 'unavailable' ? ' is-unavailable' : ''}">
        <button class="markdown-quick-location" data-md-quick-folder="${escapeHtml(item.path)}" type="button" ${check === 'unavailable' ? 'aria-disabled="true"' : ''}>
          <span class="markdown-quick-location-main">
            <strong>${escapeHtml(item.title)}</strong>
            <small>${escapeHtml(item.detail || item.path)}</small>
          </span>
          <span class="markdown-quick-location-meta"><em>${escapeHtml(typeLabel)}</em><span>${escapeHtml(status)}</span></span>
        </button>
        <button class="markdown-quick-location-remove" data-md-remove-location="${escapeHtml(item.path)}" data-md-remove-location-kind="${escapeHtml(item.kind)}" type="button" aria-label="移除${escapeHtml(item.title)}" title="从快捷列表移除">×</button>
      </div>
    `;
  }

  function scheduleLocationValidation() {
    if (locationValidationScheduled) return;
    const items = quickLocationItems().filter((item) => !locationValidation.has(item.path));
    if (!items.length) return;
    locationValidationScheduled = true;
    Promise.all(items.map(async (item) => {
      locationValidation.set(item.path, 'loading');
      try {
        const result = typeof root.electronAPI.directoryExists === 'function'
          ? await root.electronAPI.directoryExists(item.path)
          : await root.electronAPI.listMarkdownTree(item.path);
        const available = typeof result?.exists === 'boolean' ? result.exists : Boolean(result?.success);
        locationValidation.set(item.path, available ? 'available' : 'unavailable');
      } catch (_) {
        locationValidation.set(item.path, 'unavailable');
      }
    })).finally(() => {
      locationValidationScheduled = false;
      if (state.reader.status === 'idle') render();
    });
  }

  function renderQuickStart() {
    scheduleLocationValidation();
    const items = quickLocationItems();
    const exports = items.filter((item) => item.kind === 'export');
    const folders = items.filter((item) => item.kind === 'folder');
    const section = (title, values, empty) => `
      <section class="markdown-quick-section">
        <div class="markdown-quick-section-head"><strong>${escapeHtml(title)}</strong><span>${values.length}</span></div>
        ${values.length ? values.map(renderQuickLocation).join('') : `<p class="markdown-quick-empty">${escapeHtml(empty)}</p>`}
      </section>
    `;
    return `
      <div class="markdown-reader-start">
        <div class="markdown-reader-start-head">
          <div>
            <span class="markdown-dock-eyebrow">Markdown 阅读器</span>
            <h3>快速打开文档</h3>
            <p>直接打开最近的导出目录或文件夹，目录结构会自动恢复。</p>
          </div>
          <button class="btn-primary" data-md-open-folder type="button">选择文件夹</button>
        </div>
        <div class="markdown-quick-sections">
          ${section('最近导出目录', exports, '还没有可用的导出记录。')}
          ${section('最近打开的文件夹', folders, '选择文件夹后会记录在这里。')}
        </div>
      </div>
    `;
  }

  function treeModel(entries) {
    const rootNode = { kind: 'directory', name: '', path: '', children: new Map() };
    (entries || []).forEach((entry) => {
      const relative = String(entry.relativePath || '').replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
      if (!relative) return;
      const parts = relative.split('/').filter(Boolean);
      let node = rootNode;
      parts.forEach((part, index) => {
        const isLast = index === parts.length - 1;
        const kind = isLast ? String(entry.kind || 'file') : 'directory';
        const childKey = `${kind}:${part}`;
        if (!node.children.has(childKey)) {
          node.children.set(childKey, {
            kind,
            name: part,
            path: isLast ? String(entry.path || '') : '',
            relativePath: parts.slice(0, index + 1).join('/'),
            children: new Map()
          });
        }
        node = node.children.get(childKey);
      });
    });
    return rootNode;
  }

  function renderTreeNode(node) {
    return Array.from(node.children.values()).sort((left, right) => {
      if (left.kind !== right.kind) return left.kind === 'directory' ? -1 : 1;
      return left.name.localeCompare(right.name, 'zh-Hans-CN');
    }).map((child) => {
      if (child.kind === 'directory') {
        return `
          <details class="markdown-reader-tree-folder" data-md-tree-path="${escapeHtml(child.relativePath || child.name)}" open>
            <summary><span class="markdown-tree-icon" aria-hidden="true">▾</span><strong>${escapeHtml(child.name)}</strong></summary>
            <div class="markdown-reader-tree-children">${renderTreeNode(child)}</div>
          </details>
        `;
      }
      const active = child.path && child.path === state.reader.path;
      return `<button class="markdown-reader-tree-file${active ? ' active' : ''}" data-md-file-path="${escapeHtml(child.path)}" type="button" title="${escapeHtml(child.path)}"><span class="markdown-tree-icon" aria-hidden="true">▤</span><span>${escapeHtml(child.name)}</span></button>`;
    }).join('');
  }

  function renderReaderTree() {
    const reader = state.reader;
    if (!reader.directory) {
      return `
        <div class="markdown-reader-tree-empty">
          <span>未打开文件夹</span>
          <button class="btn-secondary" data-md-open-folder type="button">打开文件夹</button>
        </div>
      `;
    }
    const model = treeModel(reader.tree);
    return `
      <div class="markdown-reader-tree-head">
        <div>
          <span class="markdown-reader-section-label">文件夹</span>
          <strong title="${escapeHtml(reader.directory)}">${escapeHtml(fileName(reader.directory))}</strong>
        </div>
        <div class="markdown-reader-tree-head-actions">
          <button class="btn-text" data-md-open-folder type="button">更换</button>
          <button class="markdown-reader-home-toggle markdown-reader-tree-home-toggle" data-md-reader-home type="button" aria-label="返回 Markdown 阅读器导航页" title="返回 Markdown 阅读器导航页">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 6-6 6 6 6"/></svg>
          </button>
        </div>
      </div>
      <div class="markdown-reader-tree-list">
        ${renderTreeNode(model) || '<div class="markdown-reader-tree-empty">文件夹内没有 Markdown 文件。</div>'}
      </div>
      ${reader.treeTruncated ? '<p class="markdown-reader-tree-warning">文件较多，仅显示前 5000 个目录项。</p>' : ''}
    `;
  }

  function renderReaderDocument(reader) {
    if (reader.status !== 'ready') {
      return '<div class="markdown-reader-document-empty"><h3>选择左侧 Markdown 文件</h3><p>点击文件后在这里查看正文。</p></div>';
    }
    if (reader.editing) {
      return `
        <div class="markdown-reader-document-head markdown-reader-edit-head">
          <div class="markdown-reader-toolbar-actions" aria-label="编辑操作">
            <button class="btn-text" data-md-cancel-edit type="button" title="放弃本次编辑">取消</button>
            <button class="btn-primary" data-md-save-file type="button" title="保存当前 Markdown 文档" ${reader.saveStatus === 'saving' ? 'disabled' : ''}>${reader.saveStatus === 'saving' ? '保存中…' : '保存'}</button>
          </div>
        </div>
        <div class="markdown-reader-editor-wrap">
          <div class="markdown-reader-vditor-editor" data-md-vditor-editor aria-label="Markdown 编辑器"></div>
        </div>
      `;
    }
    if (!reader.content) {
      return '<div class="markdown-reader-document-empty"><h3>这个文档暂时没有内容</h3><p>点击“编辑”可以开始写入 Markdown。</p><button class="btn-secondary" data-md-edit-file type="button">编辑文档</button></div>';
    }
    let rendered;
    try {
      rendered = renderDocument(reader.content);
    } catch (error) {
      return `<div class="markdown-dock-empty"><h3>Markdown 渲染失败</h3><p>${escapeHtml(error?.message || String(error))}</p></div>`;
    }
    return `
      <button class="markdown-reader-edit-toggle" data-md-edit-file type="button" aria-label="编辑本文" title="编辑本文">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m4 16.5-.7 3.7 3.7-.7L18.8 7.7a2.1 2.1 0 0 0-3-3L4 16.5Z"/><path d="m14.5 6.5 3 3"/></svg>
      </button>
      <div class="markdown-reader-scroll" data-md-reader-scroll>
        <article class="guide-content markdown-reader-content" data-md-reader-content>
          ${rendered.html || '<p class="markdown-reader-no-content">这个文档没有可显示的内容。</p>'}
        </article>
      </div>
    `;
  }

  function renderLiveReaderStatus() {
    const live = state.liveExport;
    if (!readerStateIsLive()) return '';
    const running = live.mode === 'running';
    return `
      <div class="markdown-live-status" role="status" aria-live="polite">
        <span class="markdown-live-status-dot${running ? ' is-running' : ''}" aria-hidden="true"></span>
        <strong>${escapeHtml(running ? '正在导出' : '导出已完成')}</strong>
        <span>${escapeHtml(live.statusText || live.title || '导出目录')}</span>
        ${live.originTool ? '<button class="btn-text" data-md-return-task type="button">返回导出任务</button>' : ''}
      </div>
    `;
  }

  function readerStateIsLive() {
    return Boolean(state.reader.live && state.liveExport.outputPath);
  }

  function renderReaderPanel() {
    const reader = state.reader;
    if (reader.screen === 'start') return renderQuickStart();
    if (reader.status === 'loading') {
      const scanned = Number(reader.scanProgress?.scanned || 0);
      const matched = Number(reader.scanProgress?.matched || 0);
      return `
        <div class="markdown-dock-empty markdown-dock-loading markdown-reader-scan-state">
          <span class="markdown-dock-eyebrow">Markdown 阅读器</span>
          <h3>正在读取文件夹</h3>
          <p>正在扫描目录结构，请稍候。</p>
          <div class="markdown-reader-scan-track" role="progressbar" aria-label="正在扫描 Markdown 文件夹" aria-valuetext="已扫描 ${scanned} 项，发现 ${matched} 个 Markdown 文件">
            <div class="markdown-reader-scan-fill"></div>
          </div>
          <span class="markdown-reader-scan-count">已扫描 ${scanned} 项 · 发现 ${matched} 个 Markdown 文件</span>
          <button class="btn-text" data-md-cancel-load type="button">取消并返回</button>
        </div>
      `;
    }
    if (reader.status === 'error') {
      return `
        <div class="markdown-dock-empty">
          <span class="markdown-dock-eyebrow">Markdown 阅读器</span>
          <h3>文档读取失败</h3>
          <p>${escapeHtml(reader.error || '无法读取这个 Markdown 文件。')}</p>
          <button class="btn-secondary" data-md-open-file type="button">重新打开文件</button>
          <button class="btn-text" data-md-open-folder type="button">打开文件夹</button>
        </div>
      `;
    }
    if (reader.status !== 'ready') {
      return renderQuickStart();
    }

    return `
      ${renderLiveReaderStatus()}
      <div class="markdown-reader-panel">
        <div class="markdown-reader-layout${state.treeCollapsed ? ' is-tree-collapsed' : ''}">
          <aside class="markdown-reader-tree" aria-label="Markdown 文件目录">
            ${renderReaderTree()}
          </aside>
          <div class="markdown-reader-resizer" data-md-reader-resizer role="separator" aria-orientation="vertical" tabindex="0" aria-label="拖动调整目录和正文宽度" aria-valuemin="0" aria-valuemax="380" aria-valuenow="${state.treeCollapsed ? COLLAPSED_TREE_WIDTH : state.treeWidth}"></div>
          <button class="markdown-reader-collapse-toggle" data-md-tree-toggle type="button" aria-expanded="${state.treeCollapsed ? 'false' : 'true'}" aria-label="${state.treeCollapsed ? '展开目录' : '收起目录'}" title="${state.treeCollapsed ? '展开目录' : '收起目录'}">
            ${state.treeCollapsed
              ? '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 6 6 6-6 6"/></svg>'
              : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 6-6 6 6 6"/></svg>'}
          </button>
          <section class="markdown-reader-document">
            ${renderReaderDocument(reader)}
          </section>
        </div>
      </div>
    `;
  }

  function renderPreviewPanel() {
    const preview = state.preview;
    if (preview.status === 'ready' && preview.content) {
      let rendered;
      try {
        rendered = renderDocument(preview.content, { imageAttribute: 'data-md-image-src' });
      } catch (error) {
        return `<div class="markdown-dock-empty"><span class="markdown-dock-eyebrow">实时预览</span><h3>预览渲染失败</h3><p>${escapeHtml(error?.message || String(error))}</p></div>`;
      }
      return `
        <div class="markdown-reader-panel">
          <div class="markdown-reader-toolbar">
            <div class="markdown-reader-file"><strong>${escapeHtml(preview.title || '导出中的 Markdown')}</strong><small>实时预览 · ${escapeHtml(preview.currentPath || '正在等待输出文件')}</small></div>
            <span class="markdown-preview-status">${preview.fileCount ? `已发现 ${preview.fileCount} 个文件` : '正在读取输出目录'}</span>
          </div>
          <div class="markdown-reader-scroll" data-md-preview-scroll>
            <article class="guide-content markdown-reader-content">${rendered.html}</article>
          </div>
        </div>
      `;
    }
    return `
      <div class="markdown-dock-empty">
        <span class="markdown-dock-eyebrow">实时预览</span>
        <h3>${preview.status === 'error' ? '预览读取失败' : '导出时查看 Markdown'}</h3>
        <p>${escapeHtml(preview.error || (preview.status === 'waiting' ? '正在等待输出目录和第一个 Markdown 文件。' : '开始一次导出后，正在生成的 Markdown 会显示在这里。'))}</p>
        ${preview.fileCount ? `<span class="markdown-dock-hint">已发现 ${preview.fileCount} 个 Markdown 文件，正在等待文件内容更新。</span>` : ''}
        <span class="markdown-dock-hint">导出任务开始后，这里会跟随输出目录更新当前 Markdown。</span>
      </div>
    `;
  }

  function applyWidth() {
    state.width = clampWidth(state.width);
    root.document.documentElement.style.setProperty('--markdown-dock-width', `${state.width}px`);
    if (!dock) return;
    const resizer = dock.querySelector('[data-md-dock-resizer]');
    if (resizer) resizer.setAttribute('aria-valuenow', String(state.width));
  }

  function destroyVditorEditors(container) {
    const runtime = root.WandaoVditor;
    if (!runtime || !container) return;
    container.querySelectorAll?.('[data-md-vditor-editor]').forEach((element) => {
      stopEditorImageObserver(element);
      runtime.destroyEditor(element);
    });
  }

  function mountVditorEditor(container) {
    const runtime = root.WandaoVditor;
    const target = container?.querySelector?.('[data-md-vditor-editor]');
    if (!runtime || !target || !state.reader.editing) return null;
    const editor = runtime.mountEditor(target, state.reader.content, {
      input: (markdown) => {
        if (state.reader.editing) {
          state.reader.content = restoreEditorImageSources(String(markdown || ''), target);
        }
        scheduleEditorImageHydration(target);
      },
      after: () => {
        observeEditorImages(target);
        scheduleEditorImageHydration(target);
      }
    });
    observeEditorImages(target);
    return editor;
  }

  function mountVditorPreviews(container) {
    const runtime = root.WandaoVditor;
    if (!runtime || !container) return Promise.resolve();
    return runtime.mountQueued(container);
  }

  function editorImages(container) {
    return Array.from(container?.querySelectorAll?.(
      '.vditor-ir img, .vditor-wysiwyg img, .vditor-preview img'
    ) || []).filter((image) => !image.closest('.vditor-toolbar'));
  }

  function restoreEditorImageSources(markdown, container) {
    const originalSources = editorImages(container)
      .map((image) => String(image.dataset.wandaoMarkdownSource || '').trim())
      .filter(Boolean);
    if (!originalSources.length) return markdown;
    let index = 0;
    return String(markdown || '').replace(
      /\]\((data:image\/[^)\s]+)\)/gi,
      (match) => {
        const source = originalSources[index++];
        return source ? `](${source})` : match;
      }
    );
  }

  async function hydrateEditorImages(container) {
    const markdownPath = state.reader.path;
    if (!markdownPath || !container) return;
    const images = editorImages(container);
    await Promise.all(images.map(async (image) => {
      const currentSource = String(
        image.dataset.wandaoMarkdownSource || image.getAttribute('src') || ''
      ).trim();
      if (!currentSource
        || /^https?:\/\//i.test(currentSource)
        || /^data:/i.test(currentSource)
        || image.dataset.wandaoImageLoading === 'true'
        || image.dataset.wandaoImageLoaded === 'true') return;

      image.dataset.wandaoMarkdownSource = currentSource;
      image.dataset.wandaoImageLoading = 'true';
      delete image.dataset.wandaoImageError;
      try {
        const result = await root.electronAPI.readMarkdownAsset(
          markdownPath,
          decodeURIComponent(currentSource)
        );
        if (!result?.success || !result.dataUrl) throw new Error(result?.error || '图片读取失败');
        image.src = result.dataUrl;
        image.dataset.wandaoImageLoaded = 'true';
        delete image.dataset.wandaoImageError;
      } catch (error) {
        const message = error?.message || '图片读取失败';
        image.dataset.wandaoImageError = 'true';
        image.title = `图片读取失败：${message}`;
      } finally {
        delete image.dataset.wandaoImageLoading;
      }
    }));
  }

  function scheduleEditorImageHydration(container) {
    if (!container) return;
    const previous = editorImageHydrationTimers.get(container);
    if (previous) root.clearTimeout(previous);
    const timer = root.setTimeout(() => {
      editorImageHydrationTimers.delete(container);
      hydrateEditorImages(container).catch(() => undefined);
    }, 60);
    editorImageHydrationTimers.set(container, timer);
  }

  function observeEditorImages(container) {
    if (!container) return;
    stopEditorImageObserver(container);
    if (typeof root.MutationObserver === 'function') {
      const observer = new root.MutationObserver(() => scheduleEditorImageHydration(container));
      observer.observe(container, { childList: true, subtree: true });
      editorImageObservers.set(container, observer);
    }
    scheduleEditorImageHydration(container);
  }

  function stopEditorImageObserver(container) {
    const observer = editorImageObservers.get(container);
    if (observer) observer.disconnect();
    editorImageObservers.delete(container);
    const timer = editorImageHydrationTimers.get(container);
    if (timer) root.clearTimeout(timer);
    editorImageHydrationTimers.delete(container);
  }

  function renderReaderHost() {
    if (!readerHost || !readerHost.isConnected) {
      readerHost = null;
      return;
    }
    const body = readerHost.querySelector('[data-md-reader-host]');
    if (!body) return;
    captureReaderTreeViewState(body);
    const oldScroll = body.querySelector('[data-md-reader-scroll]');
    const oldScrollTop = oldScroll?.scrollTop || 0;
    destroyVditorEditors(body);
    body.innerHTML = renderReaderPanel();
    applyTreeWidth(body);
    mountVditorEditor(body);
    mountVditorPreviews(body).then(() => {
      if (state.reader.status === 'ready') hydrateReaderImages(body);
    });
    const nextScroll = body.querySelector('[data-md-reader-scroll]');
    if (nextScroll) nextScroll.scrollTop = oldScrollTop;
    restoreReaderTreeViewState(body);
  }

  function render() {
    if (dock) {
      applyWidth();
      dock.hidden = !state.open;
      root.document.body.classList.toggle('markdown-dock-open', state.open);
      if (state.open) {
        dock.querySelectorAll('[data-md-dock-tab]').forEach((tab) => {
          const active = tab.dataset.mdDockTab === state.tab;
          tab.hidden = state.tab !== tab.dataset.mdDockTab;
          tab.classList.toggle('active', active);
          tab.setAttribute('aria-selected', active ? 'true' : 'false');
          tab.tabIndex = active ? 0 : -1;
        });
        const body = dock.querySelector('#markdown-dock-body');
        if (body) {
          captureReaderTreeViewState(body);
          const oldScroll = body.querySelector('[data-md-reader-scroll], [data-md-preview-scroll]');
          const wasAtBottom = oldScroll
            ? oldScroll.scrollHeight - oldScroll.scrollTop - oldScroll.clientHeight < 32
            : true;
          const oldScrollTop = oldScroll?.scrollTop || 0;
          destroyVditorEditors(body);
          body.innerHTML = state.tab === 'reader' ? renderReaderPanel() : renderPreviewPanel();
          if (state.tab === 'reader') applyTreeWidth(body);
          if (state.tab === 'reader') mountVditorEditor(body);
          mountVditorPreviews(body).then(() => {
            if (state.tab === 'reader' && state.reader.status === 'ready') {
              hydrateReaderImages(body);
            } else if (state.tab === 'preview' && state.preview.status === 'ready') {
              hydrateMarkdownImages(body, state.preview.currentPath);
            }
          });
          const nextScroll = body.querySelector('[data-md-reader-scroll], [data-md-preview-scroll]');
          if (nextScroll) nextScroll.scrollTop = wasAtBottom ? nextScroll.scrollHeight : oldScrollTop;
          restoreReaderTreeViewState(body);
          if (state.tab === 'reader' && state.reader.status === 'ready') hydrateReaderImages(dock);
        }
      }
    }
    renderReaderHost();
  }

  function captureReaderTreeViewState(container) {
    const tree = container?.querySelector?.('.markdown-reader-tree');
    if (!tree) return;
    readerTreeViewState.hasSnapshot = true;
    readerTreeViewState.scrollTop = tree.scrollTop;
    readerTreeViewState.scrollLeft = tree.scrollLeft;
    readerTreeViewState.openPaths = new Set(
      Array.from(tree.querySelectorAll('details[data-md-tree-path][open]'))
        .map((item) => item.dataset.mdTreePath)
        .filter(Boolean)
    );
  }

  function restoreReaderTreeViewState(container) {
    const tree = container?.querySelector?.('.markdown-reader-tree');
    if (!tree || !readerTreeViewState.hasSnapshot) return;
    tree.scrollTop = readerTreeViewState.scrollTop;
    tree.scrollLeft = readerTreeViewState.scrollLeft;
    tree.querySelectorAll('details[data-md-tree-path]').forEach((item) => {
      item.open = readerTreeViewState.openPaths.has(item.dataset.mdTreePath);
    });
  }

  function applyTreeWidth(container) {
    const layouts = container?.querySelectorAll?.('.markdown-reader-layout') || [];
    layouts.forEach((layout) => {
      state.treeWidth = clampTreeWidth(state.treeWidth, layout);
      layout.style.setProperty('--markdown-reader-tree-width', state.treeCollapsed ? '0px' : `${state.treeWidth}px`);
      layout.style.setProperty('--markdown-reader-resizer-width', state.treeCollapsed ? '0px' : '9px');
      layout.style.setProperty('--markdown-reader-toggle-left', state.treeCollapsed ? '0px' : `${Math.max(0, state.treeWidth - 14)}px`);
      const resizer = layout.querySelector('[data-md-reader-resizer]');
      if (resizer) {
        resizer.setAttribute('aria-valuenow', String(state.treeWidth));
        resizer.setAttribute('aria-valuemax', String(clampTreeWidth(420, layout)));
      }
    });
  }

  async function hydrateMarkdownImages(container, markdownPath) {
    if (!markdownPath || !container) return;
    const images = Array.from(container.querySelectorAll('[data-md-image-src]'));
    await Promise.all(images.map(async (image) => {
      const source = String(image.dataset.mdImageSrc || '').trim();
      if (/^data:image\//i.test(source)) {
        image.src = source;
        image.removeAttribute('data-md-image-src');
        return;
      }
      if (!source || /^https?:\/\//i.test(source) || /^data:/i.test(source)) {
        replaceImageFallback(image, source ? '远程图片暂未加载' : '图片没有路径');
        return;
      }
      try {
        const result = await root.electronAPI.readMarkdownAsset(markdownPath, decodeURIComponent(source));
        if (!result?.success || !result.dataUrl) throw new Error(result?.error || '图片读取失败');
        image.src = result.dataUrl;
        image.removeAttribute('data-md-image-src');
      } catch (error) {
        replaceImageFallback(image, error?.message || '图片读取失败');
      }
    }));
  }

  async function hydrateReaderImages(container = dock) {
    return hydrateMarkdownImages(container, state.reader.path);
  }

  function replaceImageFallback(image, message) {
    if (!image?.isConnected) return;
    const fallback = root.document.createElement('div');
    fallback.className = 'markdown-reader-image-fallback';
    fallback.textContent = message || '图片暂时无法加载';
    image.replaceWith(fallback);
  }

  async function loadFile(path, options = {}) {
    if (!path) return;
    const isCurrent = typeof options.isCurrent === 'function' ? options.isCurrent : () => true;
    if (!isCurrent()) return false;
    if (state.reader.editing && !root.confirm('当前 Markdown 仍在编辑中，未保存内容会丢失。确认打开另一篇吗？')) return false;
    destroyVditorEditors(readerHost || dock);
    state.reader.screen = 'document';
    if (!readerHost) {
      state.open = true;
      state.tab = 'reader';
    }
    const previousDirectory = pathInsideDirectory(path, state.reader.directory)
      ? state.reader.directory
      : '';
    const previousTree = previousDirectory ? state.reader.tree || [] : [];
    state.reader = { ...state.reader, status: 'loading', path, title: fileName(path), content: '', error: '', headings: [], editing: false, saveStatus: 'idle' };
    saveState();
    render();
    try {
      const result = await root.electronAPI.readMarkdownFile(path);
      if (!isCurrent()) return false;
      if (!result?.success) throw new Error(result?.error || 'Markdown 文件读取失败');
      state.reader = {
        status: 'ready',
        path: result.path || path,
        title: result.title || fileName(path),
        content: String(result.content || ''),
        error: '',
        headings: [],
        editing: false,
        saveStatus: 'idle',
        directory: previousDirectory || parentDirectory(result.path || path),
        tree: previousTree,
        treeTruncated: Boolean(state.reader.treeTruncated)
      };
      if (!previousDirectory && state.reader.directory) {
        try {
          const tree = await root.electronAPI.listMarkdownTree(state.reader.directory);
          if (!isCurrent()) return false;
          if (tree?.success) {
            state.reader.tree = Array.isArray(tree.entries) ? tree.entries : [];
            state.reader.treeTruncated = Boolean(tree.truncated);
          }
          rememberFolder(state.reader.directory);
        } catch (_) {
          // The article remains readable if its parent directory cannot be scanned.
        }
      }
      rememberFile(state.reader.path, state.reader.title);
    } catch (error) {
      if (!isCurrent()) return false;
      state.reader.status = 'error';
      state.reader.error = error?.message || String(error);
    }
    render();
    return state.reader.status === 'ready';
  }

  async function chooseFile() {
    const path = await root.electronAPI.selectFile({
      title: '打开 Markdown 文档',
      filters: [{ name: 'Markdown 文档', extensions: ['md', 'markdown', 'mdown', 'mkdn'] }]
    });
    if (path) await loadFile(path);
  }

  async function loadFolder(path, options = {}) {
    if (!path) return false;
    state.reader.screen = 'document';
    const { remember = true, autoSelect = true, silent = false, live = false } = options;
    if (state.reader.editing && !root.confirm('当前 Markdown 仍在编辑中，未保存内容会丢失。确认切换文件夹吗？')) return false;
    destroyVditorEditors(readerHost || dock);
    const requestToken = ++readerLoadToken;
    const externalIsCurrent = typeof options.isCurrent === 'function' ? options.isCurrent : () => true;
    const isCurrent = () => requestToken === readerLoadToken && externalIsCurrent();
    if (!readerHost) {
      state.open = true;
      state.tab = 'reader';
    }
    readerTreeViewState.scrollTop = 0;
    readerTreeViewState.scrollLeft = 0;
    readerTreeViewState.openPaths = new Set();
    readerTreeViewState.hasSnapshot = false;
    state.reader = defaultReaderState({
      status: 'loading',
      directory: path,
      live,
      scanProgress: { scanned: 0, matched: 0, done: false }
    });
    render();
    try {
      const result = await root.electronAPI.listMarkdownTree(path);
      if (!isCurrent()) return false;
      if (!result?.success) throw new Error(result?.error || 'Markdown 文件夹读取失败');
      state.reader.directory = result.root || path;
      state.reader.tree = Array.isArray(result.entries) ? result.entries : [];
      state.reader.treeTruncated = Boolean(result.truncated);
      state.reader.status = 'ready';
      if (live) startLiveReaderWatch(state.reader.directory);
      if (remember) rememberFolder(state.reader.directory);
      const firstFile = autoSelect && state.reader.tree.find((item) => item.kind === 'file');
      if (firstFile?.path) {
        await loadFile(firstFile.path, { isCurrent });
        return true;
      }
      render();
      return true;
    } catch (error) {
      if (!isCurrent()) return false;
      if (live) {
        state.reader = defaultReaderState({
          status: 'ready',
          screen: 'document',
          directory: path,
          live: true
        });
        startLiveReaderWatch(path);
        render();
        return true;
      }
      if (silent) {
        state.reader = defaultReaderState();
        render();
        return false;
      }
      state.reader.status = 'error';
      state.reader.error = error?.message || String(error);
      render();
      return false;
    }
  }

  async function chooseFolder() {
    const path = await root.electronAPI.selectDirectory({ title: '打开 Markdown 文件夹' });
    if (path) await loadFolder(path);
  }

  async function restoreLastFolder() {
    if (restoreLastFolderAttempted || state.reader.directory) return;
    restoreLastFolderAttempted = true;
    const latest = recentFolders()[0];
    if (!latest?.path) return;
    const token = {};
    let timedOut = false;
    const loadPromise = loadFolder(latest.path, {
      remember: false,
      silent: true,
      isCurrent: () => restoreLastFolderAttempted && !timedOut && restoreLastFolderToken === token
    });
    restoreLastFolderToken = token;
    const timeoutPromise = new Promise((resolve) => {
      root.setTimeout(() => {
        timedOut = true;
        restoreLastFolderToken = null;
        readerLoadToken += 1;
        resolve(false);
      }, 1200);
    });
    const completed = await Promise.race([loadPromise, timeoutPromise]);
    if (!completed && timedOut && readerHost) {
      state.reader = defaultReaderState();
      render();
    }
    await loadPromise.catch(() => undefined);
    if (restoreLastFolderToken === token) restoreLastFolderToken = null;
  }

  function showReaderLanding() {
    if (state.reader.editing && !root.confirm('当前 Markdown 仍在编辑中，未保存内容会保留在编辑状态。确认返回导航页吗？')) return;
    destroyVditorEditors(readerHost || dock);
    readerLoadToken += 1;
    stopLiveReaderWatch();
    state.reader.screen = 'start';
    state.reader.editing = false;
    state.reader.saveStatus = 'idle';
    render();
  }

  function handleMarkdownTreeProgress(data) {
    if (!data || state.reader.status !== 'loading') return;
    if (!pathInsideDirectory(data.directoryPath, state.reader.directory)
      || !pathInsideDirectory(state.reader.directory, data.directoryPath)) return;
    state.reader.scanProgress = {
      scanned: Math.max(0, Number(data.scanned) || 0),
      matched: Math.max(0, Number(data.matched) || 0),
      done: Boolean(data.done)
    };
    const now = Date.now();
    if (data.done || now - lastTreeProgressPaint >= 100) {
      lastTreeProgressPaint = now;
      render();
    }
  }

  function syncLiveExportButton() {
    const button = root.document.getElementById('btn-open-live-markdown');
    if (!button) return;
    const active = state.liveExport.mode === 'running' && Boolean(state.liveExport.outputPath);
    button.hidden = !active;
    button.disabled = !active;
  }

  function prepareLiveExport(options = {}) {
    stopLiveReaderWatch();
    state.liveExport = {
      mode: 'running',
      outputPath: String(options.outputPath || ''),
      title: String(options.title || '导出任务'),
      originTool: String(options.originTool || ''),
      statusText: '正在准备输出目录…'
    };
    syncLiveExportButton();
    render();
  }

  function updateLiveStatus(done, total, detail = '') {
    if (state.liveExport.mode !== 'running') return;
    state.liveExport.statusText = detail
      || (Number(total) > 0 ? `已处理 ${Number(done) || 0}/${Number(total)}` : '正在生成 Markdown…');
    syncLiveExportButton();
    if (readerStateIsLive()) render();
  }

  function openLiveExportReader() {
    if (!state.liveExport.outputPath) return null;
    state.reader = defaultReaderState({
      status: 'loading',
      screen: 'document',
      directory: state.liveExport.outputPath,
      live: true
    });
    return { ...state.liveExport };
  }

  function stopLiveReaderWatch() {
    if (liveReaderTimer) {
      root.clearInterval(liveReaderTimer);
      liveReaderTimer = null;
    }
    liveReaderRefreshBusy = false;
    liveReaderTreeSignature = '';
    liveReaderCurrentMeta = '';
  }

  function startLiveReaderWatch(directory) {
    stopLiveReaderWatch();
    if (!directory) return;
    refreshLiveReader();
    liveReaderTimer = root.setInterval(refreshLiveReader, 1200);
  }

  async function refreshLiveReader() {
    if (!readerStateIsLive() || liveReaderRefreshBusy) return;
    liveReaderRefreshBusy = true;
    try {
      const result = await root.electronAPI.listMarkdownTree(state.reader.directory);
      if (!result?.success) return;
      const entries = Array.isArray(result.entries) ? result.entries : [];
      const files = entries.filter((entry) => entry?.kind === 'file' && entry.path);
      const treeSignature = entries.map((entry) => `${entry.kind}:${entry.path}:${entry.size || 0}:${entry.modifiedAt || 0}`).join('|');
      const treeChanged = treeSignature !== liveReaderTreeSignature;
      const current = state.reader.path
        ? files.find((entry) => entry.path === state.reader.path)
        : files[0];
      state.reader.tree = entries;
      state.reader.treeTruncated = Boolean(result.truncated);
      let contentChanged = false;
      if (current) {
        const currentMeta = `${current.path}:${current.size || 0}:${current.modifiedAt || 0}`;
        if (currentMeta !== liveReaderCurrentMeta || !state.reader.content) {
          const file = await root.electronAPI.readMarkdownFile(current.path);
          if (file?.success) {
            state.reader.path = file.path || current.path;
            state.reader.title = file.title || fileName(current.path);
            state.reader.content = String(file.content || '');
            state.reader.status = 'ready';
            state.reader.screen = 'document';
            liveReaderCurrentMeta = currentMeta;
            contentChanged = true;
          }
        }
      } else if (!files.length) {
        state.reader.status = 'ready';
        state.reader.screen = 'document';
      }
      liveReaderTreeSignature = treeSignature;
      if (treeChanged || contentChanged) render();
    } catch (_) {
      // The reader remains usable while the export directory is being written.
    } finally {
      liveReaderRefreshBusy = false;
    }
  }

  function stopLiveExport() {
    stopLiveReaderWatch();
    if (state.liveExport.mode === 'running') {
      state.liveExport.mode = 'completed';
      state.liveExport.statusText = '导出任务已结束';
    }
    syncLiveExportButton();
    if (readerStateIsLive()) render();
  }

  async function saveFile(host = dock) {
    const reader = state.reader;
    const target = host?.querySelector('[data-md-vditor-editor]');
    const editor = root.WandaoVditor?.editorFor(target);
    if (!reader.path || !editor || reader.saveStatus === 'saving') return;
    let content;
    try {
      content = restoreEditorImageSources(editor.getValue(), target);
    } catch (_) {
      state.reader.error = '编辑器仍在加载，请稍候再保存。';
      render();
      return;
    }
    state.reader.content = content;
    state.reader.saveStatus = 'saving';
    render();
    try {
      const result = await root.electronAPI.writeMarkdownFile(reader.path, content);
      if (!result?.success) throw new Error(result?.error || 'Markdown 文件保存失败');
      state.reader.content = content;
      state.reader.editing = false;
      state.reader.saveStatus = 'idle';
      root.WandaoVditor?.destroyEditor(target);
      rememberFile(reader.path, reader.title);
    } catch (error) {
      state.reader.saveStatus = 'idle';
      state.reader.error = error?.message || String(error);
    }
    render();
  }

  async function refreshPreview() {
    const preview = state.preview;
    if (!preview.outputPath || previewRefreshBusy) return;
    previewRefreshBusy = true;
    try {
      const tree = await root.electronAPI.listMarkdownTree(preview.outputPath);
      if (!tree?.success) {
        preview.status = 'waiting';
        preview.fileCount = 0;
        if (state.open && state.tab === 'preview') render();
        return;
      }
      const files = (Array.isArray(tree.entries) ? tree.entries : [])
        .filter((entry) => entry && entry.kind === 'file' && entry.path);
      preview.fileCount = files.length;
      if (!files.length) {
        preview.status = 'waiting';
        if (state.open && state.tab === 'preview') render();
        return;
      }
      files.sort((left, right) => Number(right.modifiedAt || 0) - Number(left.modifiedAt || 0)
        || String(right.relativePath || right.path).localeCompare(String(left.relativePath || left.path), 'zh-Hans-CN'));
      const current = files[0];
      const result = await root.electronAPI.readMarkdownFile(current.path);
      if (!result?.success) throw new Error(result?.error || '读取导出中的 Markdown 失败');
      const content = String(result.content || '');
      const changed = preview.currentPath !== current.path || preview.content !== content;
      preview.status = 'ready';
      preview.error = '';
      preview.currentPath = current.path;
      preview.title = result.title || fileName(current.path);
      preview.bytes = Number(result.bytes || content.length);
      preview.content = content;
      if (changed && state.open && state.tab === 'preview') render();
    } catch (error) {
      preview.status = 'error';
      preview.error = error?.message || String(error);
      if (state.open && state.tab === 'preview') render();
    } finally {
      previewRefreshBusy = false;
    }
  }

  function stopPreview() {
    if (previewTimer) {
      root.clearInterval(previewTimer);
      previewTimer = null;
    }
    previewRefreshBusy = false;
  }

  function startPreview(options = {}) {
    stopPreview();
    state.preview = {
      status: 'waiting',
      title: String(options.title || '导出中的 Markdown'),
      content: '',
      error: '',
      outputPath: String(options.outputPath || ''),
      currentPath: '',
      fileCount: 0,
      bytes: 0
    };
    open('preview');
    if (!state.preview.outputPath) return;
    refreshPreview();
    previewTimer = root.setInterval(refreshPreview, 900);
  }

  function handleReaderClick(event, host) {
      if (event.target.closest('[data-md-cancel-load]')) {
        readerLoadToken += 1;
        restoreLastFolderToken = null;
        stopLiveReaderWatch();
        state.reader = defaultReaderState();
        render();
        return;
      }
      if (event.target.closest('[data-md-return-task]')) {
        if (state.liveExport.originTool && typeof root.switchTool === 'function') {
          root.switchTool(state.liveExport.originTool);
        }
        return;
      }
      if (event.target.closest('[data-md-open-file]')) {
        chooseFile().catch((error) => {
          state.reader.status = 'error';
          state.reader.error = error?.message || String(error);
          render();
        });
        return;
      }
      if (event.target.closest('[data-md-open-folder]')) {
        chooseFolder().catch((error) => {
          state.reader.status = 'error';
          state.reader.error = error?.message || String(error);
          render();
        });
        return;
      }
      if (event.target.closest('[data-md-reader-home]')) {
        showReaderLanding();
        return;
      }
      const quickFolder = event.target.closest('[data-md-quick-folder]');
      if (quickFolder) {
        const path = quickFolder.dataset.mdQuickFolder || '';
        if (locationValidation.get(path) === 'unavailable') return;
        loadFolder(path).catch((error) => {
          state.reader.status = 'error';
          state.reader.error = error?.message || String(error);
          render();
        });
        return;
      }
      const removeLocation = event.target.closest('[data-md-remove-location]');
      if (removeLocation) {
        event.preventDefault();
        event.stopPropagation();
        const path = removeLocation.dataset.mdRemoveLocation || '';
        if (removeLocation.dataset.mdRemoveLocationKind === 'export') dismissExportDirectory(path);
        else removeRecentFolder(path);
        locationValidation.delete(path);
        return;
      }
      if (event.target.closest('[data-md-tree-toggle]')) {
        state.treeCollapsed = !state.treeCollapsed;
        saveState();
        render();
        return;
      }
      if (event.target.closest('[data-md-edit-file]')) {
        state.reader.editing = true;
        state.reader.error = '';
        render();
        const focusEditor = (attempt = 0) => {
          const target = host.querySelector('[data-md-vditor-editor]');
          const editor = root.WandaoVditor?.editorFor(target);
          if (!editor) return;
          if (editor.vditor) {
            try { editor.focus(); } catch (_) {}
            return;
          }
          if (attempt < 20) root.setTimeout(() => focusEditor(attempt + 1), 50);
        };
        root.setTimeout(() => focusEditor(), 0);
        return;
      }
      if (event.target.closest('[data-md-cancel-edit]')) {
        destroyVditorEditors(host);
        state.reader.editing = false;
        state.reader.saveStatus = 'idle';
        render();
        return;
      }
      if (event.target.closest('[data-md-save-file]')) {
        saveFile(host).catch((error) => {
          state.reader.saveStatus = 'idle';
          state.reader.error = error?.message || String(error);
          render();
        });
        return;
      }
      const treeFile = event.target.closest('[data-md-file-path]');
      if (treeFile) {
        loadFile(treeFile.dataset.mdFilePath).catch((error) => {
          state.reader.status = 'error';
          state.reader.error = error?.message || String(error);
          render();
        });
        return;
      }
      const recent = event.target.closest('[data-md-recent-path]');
      if (recent) {
        loadFile(recent.dataset.mdRecentPath).catch((error) => {
          state.reader.status = 'error';
          state.reader.error = error?.message || String(error);
          render();
        });
        return;
      }
      const anchor = event.target.closest('[data-md-anchor]');
      if (anchor) {
        event.preventDefault();
        const target = host.querySelector(`#${CSS.escape(anchor.dataset.mdAnchor || '')}`);
        target?.scrollIntoView({ behavior: 'smooth', block: 'start' });
        return;
      }
      const external = event.target.closest('[data-md-external-link]');
      if (external) {
        event.preventDefault();
        root.electronAPI.openExternal(external.href).catch(() => undefined);
      }
  }

  function bindReaderActions(host) {
    if (!host || host.dataset.mdReaderActionsBound === 'true') return;
    host.dataset.mdReaderActionsBound = 'true';
    host.addEventListener('click', (event) => handleReaderClick(event, host));
    host.addEventListener('pointerdown', (event) => {
      const resizer = event.target.closest('[data-md-reader-resizer]');
      if (!resizer) return;
      const layout = resizer.closest('.markdown-reader-layout');
      if (!layout) return;
      readerResizeSession = {
        pointerId: event.pointerId,
        host,
        startX: event.clientX,
        startWidth: state.treeCollapsed ? COLLAPSED_TREE_WIDTH : state.treeWidth,
        layout,
        resizer
      };
      resizer.setPointerCapture?.(event.pointerId);
      resizer.classList.add('is-dragging');
      layout.classList.add('is-resizing');
      event.preventDefault();
    });
    host.addEventListener('pointermove', (event) => {
      if (!readerResizeSession || readerResizeSession.host !== host || readerResizeSession.pointerId !== event.pointerId) return;
      const nextWidth = readerResizeSession.startWidth + event.clientX - readerResizeSession.startX;
      if (nextWidth <= TREE_COLLAPSE_SNAP_WIDTH) {
        state.treeCollapsed = true;
      } else {
        state.treeCollapsed = false;
        state.treeWidth = clampTreeWidth(nextWidth, readerResizeSession.layout);
      }
      applyTreeWidth(host);
      event.preventDefault();
    });
    const finishReaderResize = (event) => {
      if (!readerResizeSession || readerResizeSession.host !== host || readerResizeSession.pointerId !== event.pointerId) return;
      readerResizeSession.resizer.classList.remove('is-dragging');
      readerResizeSession.layout.classList.remove('is-resizing');
      readerResizeSession = null;
      saveState();
      render();
    };
    host.addEventListener('pointerup', finishReaderResize);
    host.addEventListener('pointercancel', finishReaderResize);
    host.addEventListener('keydown', (event) => {
      const resizer = event.target.closest('[data-md-reader-resizer]');
      if (!resizer) return;
      const layout = resizer.closest('.markdown-reader-layout');
      if (!layout) return;
      const step = event.shiftKey ? 48 : 20;
      if (event.key === 'ArrowLeft') {
        if (state.treeCollapsed) return;
        if (state.treeWidth - step <= TREE_COLLAPSE_SNAP_WIDTH) state.treeCollapsed = true;
        else state.treeWidth -= step;
      } else if (event.key === 'ArrowRight') {
        if (state.treeCollapsed) {
          state.treeCollapsed = false;
          state.treeWidth = clampTreeWidth(DEFAULT_TREE_WIDTH, layout);
        } else {
          state.treeWidth += step;
        }
      } else return;
      event.preventDefault();
      state.treeWidth = clampTreeWidth(state.treeWidth, layout);
      applyTreeWidth(host);
      saveState();
    });
  }

  function bindDock() {
    if (!dock || initialized) return;
    initialized = true;
    bindReaderActions(dock);
    dock.addEventListener('click', (event) => {
      const tab = event.target.closest('[data-md-dock-tab]');
      if (tab) {
        state.tab = tab.dataset.mdDockTab === 'preview' ? 'preview' : 'reader';
        saveState();
        render();
        return;
      }
      if (event.target.closest('[data-md-dock-close]')) {
        close();
      }
    });

    const resizer = dock.querySelector('[data-md-dock-resizer]');
    resizer?.addEventListener('pointerdown', (event) => {
      event.preventDefault();
      resizer.setPointerCapture?.(event.pointerId);
      resizeSession = { startX: event.clientX, startWidth: state.width };
      resizer.classList.add('is-dragging');
    });
    resizer?.addEventListener('pointermove', (event) => {
      if (!resizeSession) return;
      state.width = clampWidth(resizeSession.startWidth + resizeSession.startX - event.clientX);
      applyWidth();
    });
    const finishResize = () => {
      if (!resizeSession) return;
      resizeSession = null;
      resizer.classList.remove('is-dragging');
      saveState();
    };
    resizer?.addEventListener('pointerup', finishResize);
    resizer?.addEventListener('pointercancel', finishResize);
    resizer?.addEventListener('keydown', (event) => {
      if (event.key === 'ArrowLeft') {
        event.preventDefault();
        state.width = clampWidth(state.width + 24);
        applyWidth();
        saveState();
      } else if (event.key === 'ArrowRight') {
        event.preventDefault();
        state.width = clampWidth(state.width - 24);
        applyWidth();
        saveState();
      } else if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        close();
      }
    });
    root.addEventListener('resize', () => {
      state.width = clampWidth(state.width);
      applyWidth();
    });
  }

  function renderReaderPage(container) {
    if (!container) return;
    stopPreview();
    state.open = false;
    readerHost = container;
    container.innerHTML = `
      <section class="markdown-reader-page" aria-label="Markdown 阅读器">
        <div data-md-reader-host></div>
      </section>
    `;
    bindReaderActions(container);
    render();
    restoreLastFolder().catch(() => undefined);
  }

  function clearReaderPage(container) {
    if (readerHost === container) readerHost = null;
    render();
  }

  function setExportDirectories(items) {
    state.exportDirectories = Array.isArray(items)
      ? items
        .filter((item) => item && typeof item.path === 'string' && item.path.trim())
        .map((item) => ({
          path: item.path.trim(),
          title: String(item.title || item.provider || fileName(item.path)),
          provider: String(item.provider || ''),
          finishedAt: String(item.finishedAt || ''),
          status: String(item.status || '')
        }))
      : [];
    if (readerHost) render();
  }

  function init() {
    dock = root.document.getElementById('markdown-dock');
    if (!dock) return;
    loadState();
    bindDock();
    markdownTreeProgressUnsubscribe = root.electronAPI.onMarkdownTreeProgress?.(handleMarkdownTreeProgress) || null;
    render();
  }

  function open(tab = state.tab) {
    state.open = true;
    state.tab = tab === 'preview' ? 'preview' : 'reader';
    saveState();
    render();
  }

  function close() {
    state.open = false;
    stopPreview();
    render();
  }

  function setPreview(content, options = {}) {
    state.preview = {
      status: content ? 'ready' : 'idle',
      title: String(options.title || '导出中的 Markdown'),
      content: String(content || ''),
      error: String(options.error || '')
    };
    open('preview');
  }

  root.WandaoMarkdownDock = Object.freeze({
    init,
    open,
    render,
    close,
    renderReaderPage,
    clearReaderPage,
    showReaderLanding,
    setExportDirectories,
    startPreview,
    stopPreview,
    prepareLiveExport,
    updateLiveStatus,
    openLiveExportReader,
    stopLiveExport,
    setPreview,
    loadFile,
    chooseFile,
    chooseFolder,
    state
  });
})(window);
