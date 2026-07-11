// Runtime Provider registry for Wandao desktop.
// Platform metadata is supplied exclusively by Plugin v1 / Provider v1 manifests.
(function () {
  const providers = new Map();

  function normalizeProvider(provider) {
    if (!provider || !provider.id) throw new Error('Provider must include an id');
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
      defaults: { output: '', ...(provider.defaults || {}) }
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

  window.WandaoProviders = {
    defaultId: '',
    register,
    registerMany,
    replaceExternal(items) {
      providers.clear();
      return registerMany(items);
    },
    get(id) { return providers.get(id); },
    all() { return Array.from(providers.values()); },
    list(group) { return Array.from(providers.values()).filter((provider) => provider.group === group); },
    tools() { return Object.fromEntries(Array.from(providers.entries())); }
  };
})();
