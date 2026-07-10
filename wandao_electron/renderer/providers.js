// Provider registry for Wandao desktop.
// Each platform is registered as a provider so new platforms can be added
// without hard-coding navigation and basic script metadata in app.js.
(function () {
  const providers = new Map();

  function normalizeProvider(provider) {
    if (!provider || !provider.id) {
      throw new Error('Provider must include an id');
    }
    const type = provider.type || (provider.guide ? 'hybrid' : 'automation');
    const isImport = Boolean(provider.isImport || provider.group === 'import');
    const group = provider.group || (type === 'guide' ? 'guide' : (isImport ? 'import' : 'export'));
    const trustLevel = provider.trustLevel || (provider.sourceKind ? 'community' : 'official');
    const defaultScanToc = provider.sourceKind ? false : true;
    return Object.freeze({
      ...provider,
      type,
      group,
      isImport,
      trustLevel,
      requirements: provider.requirements || {},
      toc: provider.toc || {},
      templateId: provider.templateId || `template-${provider.id}`,
      capabilities: {
        login: false,
        scanToc: defaultScanToc,
        export: !isImport && type !== 'guide',
        import: isImport,
        guide: type === 'guide' || type === 'hybrid',
        stop: true,
        report: true,
        retryFailures: false,
        ...(provider.capabilities || {})
      },
      retryFailures: provider.retryFailures || {},
      defaults: {
        output: '',
        ...(provider.defaults || {})
      }
    });
  }

  function register(provider) {
    const normalized = normalizeProvider(provider);
    providers.set(normalized.id, normalized);
    return normalized;
  }

  function registerMany(items) {
    (items || []).forEach(register);
    return Array.from(providers.values());
  }

  [
    {
      id: 'zsxq-group',
      platform: 'zsxq',
      navLabel: '知识星球 Group 导出',
      title: '知识星球 Group 帖子导出',
      description: '按数量导出知识星球 Group 里的最新、精华或星主帖子',
      script: 'export_zsxq.py',
      urlParam: '--entry-url',
      outputParam: '--output',
      defaults: { output: 'exports/zsxq-group' },
      capabilities: { login: true, scanToc: false, retryFailures: true },
      retryFailures: { arg: '--retry-failed', label: '只重试失败项' },
      checkpoint: { supported: true, strategy: 'cursor', resourceTracking: true }
    },
    {
      id: 'zsxq-column',
      platform: 'zsxq',
      navLabel: '知识星球专栏导出',
      title: '知识星球专栏导出',
      description: '读取知识星球专栏目录，按章节导出 Markdown',
      script: 'export_zsxq.py',
      urlParam: '--entry-url',
      outputParam: '--output',
      defaults: { output: 'exports/zsxq-column' },
      capabilities: { login: true, scanToc: true, retryFailures: true },
      retryFailures: { arg: '--retry-failed', label: '只重试失败项' },
      checkpoint: { supported: true, strategy: 'items', resourceTracking: true }
    },
    {
      id: 'yuque',
      platform: 'yuque',
      navLabel: '语雀导出',
      title: '语雀任意知识库导出',
      description: '将语雀知识库导出为 Markdown',
      script: 'export_yuque.py',
      urlParam: '--book-url',
      outputParam: '--output',
      defaults: { output: 'exports/yuque' },
      capabilities: { login: true, scanToc: true, retryFailures: true },
      retryFailures: { arg: '--retry-failed', label: '只重试失败项' },
      checkpoint: { supported: true, strategy: 'items', resourceTracking: false }
    },
    {
      id: 'aliyun',
      platform: 'aliyun-thoughts',
      navLabel: '阿里 Thoughts 导出',
      title: '阿里云 Thoughts 工作区导出',
      description: '将阿里云 Thoughts 导出为 Markdown',
      script: 'export_aliyun_thoughts.py',
      urlParam: '--workspace-url',
      outputParam: '--output',
      defaults: { output: 'exports/aliyun-thoughts', delay: '0.1', jitter: '0' },
      capabilities: { login: true, scanToc: true, retryFailures: true },
      retryFailures: { arg: '--retry-failed', label: '只重试失败项' },
      checkpoint: { supported: true, strategy: 'items', resourceTracking: false }
    },
    {
      id: 'yinxiang',
      platform: 'yinxiang',
      navLabel: '印象笔记导出',
      title: '印象笔记导出',
      description: '将印象笔记笔记本导出为 Markdown',
      script: 'export_yinxiang.py',
      outputParam: '--output',
      noUrl: true,
      defaults: { output: 'exports/yinxiang' },
      capabilities: { login: true, scanToc: true, retryFailures: true },
      retryFailures: { arg: '--retry-failed', label: '只重试失败项' },
      checkpoint: { supported: true, strategy: 'items', resourceTracking: false }
    },
    {
      id: 'youdao',
      platform: 'youdao',
      navLabel: '有道云笔记导出',
      title: '有道云笔记导出',
      description: '将有道云笔记导出为本地 Markdown 文件',
      script: 'export_youdao.py',
      outputParam: '--output',
      noUrl: true,
      defaults: { output: 'exports/youdao' },
      capabilities: { login: true, scanToc: true, retryFailures: true },
      retryFailures: { arg: '--retry-failed', label: '只重试失败项' },
      checkpoint: { supported: true, strategy: 'items', resourceTracking: false }
    },
    {
      id: 'onenote',
      platform: 'onenote',
      navLabel: 'OneNote 导出',
      title: 'OneNote 本地笔记导出',
      description: '将 Windows 桌面版 OneNote 导出为 Markdown，并保留笔记本、分区和页面层级',
      script: 'export_onenote.py',
      outputParam: '--output',
      noUrl: true,
      defaults: { output: 'exports/onenote', delay: '0', jitter: '0' },
      capabilities: { login: false, scanToc: true, retryFailures: true },
      retryFailures: { arg: '--retry-failed', label: '只重试失败项' },
      checkpoint: { supported: true, strategy: 'items', resourceTracking: false }
    },
    {
      id: 'ima-export',
      platform: 'ima',
      navLabel: 'ima 知识库导出',
      title: 'ima 知识库导出',
      description: '将 ima 知识库内容导出到本地',
      script: 'ima_knowledge.py',
      outputParam: '--output',
      noUrl: true,
      defaults: { output: 'exports/ima' },
      capabilities: { login: false, scanToc: true, retryFailures: true },
      retryFailures: { arg: '--retry-failed', label: '只重试失败项' },
      checkpoint: { supported: true, strategy: 'items', resourceTracking: false }
    },
    {
      id: 'yuque-import',
      platform: 'yuque',
      navLabel: '语雀 Markdown 导入',
      title: '语雀 Markdown 导入',
      description: '将本地 Markdown 批量导入到语雀知识库',
      script: 'import_yuque.py',
      urlParam: '--target-book-url',
      outputParam: '--source-dir',
      isImport: true,
      defaults: { output: 'exports/yuque' },
      capabilities: { login: true, scanToc: false, retryFailures: true },
      retryFailures: { arg: '--retry-failures', label: '只重试失败项' }
    },
    {
      id: 'yinxiang-import',
      platform: 'yinxiang',
      navLabel: '印象笔记 Markdown 导入',
      title: '印象笔记 Markdown 导入',
      description: '将本地 Markdown 批量导入到印象笔记',
      script: 'import_yinxiang.py',
      outputParam: '--source-dir',
      isImport: true,
      noUrl: true,
      defaults: { output: 'exports/yinxiang' },
      capabilities: { login: true, scanToc: false }
    },
    {
      id: 'ima-import',
      platform: 'ima',
      navLabel: 'ima 知识库导入',
      title: 'ima 知识库导入',
      description: '将本地文件批量导入 ima 知识库',
      script: 'ima_knowledge.py',
      outputParam: '--source-dir',
      isImport: true,
      noUrl: true,
      defaults: { output: 'exports/ima' },
      capabilities: { login: false, scanToc: false }
    }
  ].forEach(register);
  const coreProviderIds = new Set(providers.keys());

  window.WandaoProviders = {
    defaultId: 'zsxq-group',
    register,
    registerMany,
    replaceExternal(items) {
      Array.from(providers.keys()).forEach((id) => {
        if (!coreProviderIds.has(id)) providers.delete(id);
      });
      return registerMany(items);
    },
    get(id) {
      return providers.get(id);
    },
    all() {
      return Array.from(providers.values());
    },
    list(group) {
      return Array.from(providers.values()).filter((provider) => provider.group === group);
    },
    tools() {
      return Object.fromEntries(Array.from(providers.entries()));
    }
  };
})();
