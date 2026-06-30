// Provider registry for Wandao desktop.
// Each platform is registered as a provider so new platforms can be added
// without hard-coding navigation and basic script metadata in app.js.
(function () {
  const providers = new Map();

  function normalizeProvider(provider) {
    if (!provider || !provider.id) {
      throw new Error('Provider must include an id');
    }
    return Object.freeze({
      group: provider.isImport ? 'import' : 'export',
      templateId: `template-${provider.id}`,
      capabilities: {
        login: false,
        scanToc: true,
        export: !provider.isImport,
        import: Boolean(provider.isImport),
        stop: true,
        report: true,
        ...(provider.capabilities || {})
      },
      defaults: {
        output: '',
        ...(provider.defaults || {})
      },
      ...provider
    });
  }

  function register(provider) {
    const normalized = normalizeProvider(provider);
    providers.set(normalized.id, normalized);
    return normalized;
  }

  [
    {
      id: 'zsxq',
      platform: 'zsxq',
      navLabel: '知识星球导出',
      title: '知识星球任意项目/专栏导出',
      description: '将知识星球内容导出为本地 Markdown 文件',
      script: 'export_zsxq.py',
      urlParam: '--entry-url',
      outputParam: '--output',
      defaults: { output: 'exports/zsxq' },
      capabilities: { login: true, scanToc: true }
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
      capabilities: { login: true, scanToc: true }
    },
    {
      id: 'feishu-export',
      platform: 'feishu',
      navLabel: '飞书导出',
      title: '飞书 Wiki 知识库导出',
      description: '将飞书 Wiki 导出为 Markdown',
      script: 'export_feishu.py',
      urlParam: '--wiki-url',
      outputParam: '--output',
      defaults: { output: 'exports/feishu' },
      capabilities: { login: true, scanToc: true }
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
      defaults: { output: 'exports/aliyun-thoughts' },
      capabilities: { login: true, scanToc: true }
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
      capabilities: { login: true, scanToc: true }
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
      capabilities: { login: false, scanToc: true }
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
      capabilities: { login: true, scanToc: false }
    },
    {
      id: 'feishu-import',
      platform: 'feishu',
      navLabel: '飞书 Markdown 导入',
      title: '飞书 Wiki Markdown 导入',
      description: '将本地 Markdown 批量导入到飞书 Wiki',
      script: 'import_feishu.py',
      urlParam: '--wiki-url',
      outputParam: '--source-dir',
      isImport: true,
      defaults: { output: 'exports/feishu' },
      capabilities: { login: true, scanToc: false }
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

  window.WandaoProviders = {
    defaultId: 'zsxq',
    register,
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
