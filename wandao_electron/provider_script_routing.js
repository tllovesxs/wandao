function resolveProviderScript(defaultScript, actions = []) {
  if (typeof defaultScript === 'string' && defaultScript) return defaultScript;

  const scripts = new Set(
    (Array.isArray(actions) ? actions : [])
      .map((action) => (typeof action?.script === 'string' ? action.script : ''))
      .filter(Boolean)
  );

  // Legacy built-in templates execute one backend for several actions. Only
  // expose an action-derived default when every declared action resolves to
  // the same already-validated Plugin v1 script.
  return scripts.size === 1 ? Array.from(scripts)[0] : '';
}

module.exports = { resolveProviderScript };
