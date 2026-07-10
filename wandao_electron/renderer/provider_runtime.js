(function (root) {
  function providerTypeLabel(provider = {}) {
    const typeMap = {
      automation: '自动化',
      guide: '教程',
      hybrid: '混合'
    };
    const statusMap = {
      stable: '稳定',
      beta: '测试',
      experimental: '实验'
    };
    const trustMap = {
      official: '官方',
      community: '社区',
      local: '本地',
      experimental: '实验',
      guide: '教程'
    };
    return [
      trustMap[provider.trustLevel] || provider.trustLevel,
      typeMap[provider.type] || provider.type,
      statusMap[provider.status] || provider.status
    ].filter(Boolean).join(' · ');
  }

  function providerTrustClass(provider = {}) {
    const trust = provider.trustLevel || 'community';
    if (trust === 'official') return 'official';
    if (trust === 'local') return 'local';
    if (trust === 'experimental' || provider.status === 'experimental') return 'experimental';
    return 'community';
  }

  function hasExecutableScript(provider, action = null) {
    if (!provider) return false;
    if (action) return Boolean(action.script || provider.script);
    if (provider.script) return true;
    return (provider.actions || []).some((item) => item?.script || provider.script);
  }

  function shouldConfirmExecution(provider, action = null) {
    if (!hasExecutableScript(provider, action)) return false;
    if (provider.sourceKind === 'user') return true;
    return (provider.trustLevel || 'community') !== 'official';
  }

  function sourceText(provider = {}) {
    if (provider.sourceKind === 'plugin') return '插件中心（签名已验证）';
    if (provider.sourceKind === 'user') return '本地用户目录';
    if (provider.sourceKind === 'bundled') return '应用内置 Provider';
    return 'Provider 配置';
  }

  function executionWarningTitle(provider = {}) {
    return provider.trustLevel === 'local' || provider.sourceKind === 'user'
      ? '本地 Provider 执行提醒'
      : '社区 Provider 执行提醒';
  }

  function executionConfirmMessage(provider = {}) {
    return `${provider.title || provider.name || provider.id || 'Provider'} 将在本机执行脚本。\n\n` +
      '请确认这个 Provider 来源可信，并且你了解它会访问本地文件或目标平台。是否继续？';
  }

  const api = {
    providerTypeLabel,
    providerTrustClass,
    hasExecutableScript,
    shouldConfirmExecution,
    sourceText,
    executionWarningTitle,
    executionConfirmMessage
  };

  root.WandaoProviderRuntime = api;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
})(typeof window !== 'undefined' ? window : globalThis);
