const LEGACY_URL_FIELD_NAME = /(?:^|[_-])(?:url|link)(?:$|[_-])/i;

function asFieldList(fields) {
  return Array.isArray(fields) ? fields.filter((field) => field && typeof field === 'object') : [];
}

function isCommandOption(value) {
  return typeof value === 'string' && value.startsWith('--');
}

function firstFieldArg(fields, predicate) {
  const matches = asFieldList(fields).filter((field) => predicate(field) && isCommandOption(field.arg));
  return matches.length === 1 ? matches[0].arg : '';
}

function legacyUrlParam(fields) {
  return firstFieldArg(fields, (field) => {
    const type = String(field.type || '').toLowerCase();
    if (type && type !== 'text' && type !== 'url') return false;
    return (
      LEGACY_URL_FIELD_NAME.test(String(field.name || ''))
      || /(?:^|-)url(?:$|-)/i.test(String(field.arg || ''))
    );
  });
}

function legacyOutputParam(fields) {
  return firstFieldArg(fields, (field) => (
    String(field.name || '').toLowerCase() === 'output'
    || String(field.name || '').toLowerCase() === 'output_dir'
    || String(field.arg || '').toLowerCase() === '--output'
  ));
}

function legacyNoUrl(fields, explicitNoUrl) {
  if (typeof explicitNoUrl === 'boolean') return explicitNoUrl;
  return !legacyUrlParam(fields);
}

function resolveLegacyTemplateConfig(raw = {}) {
  const fields = asFieldList(raw.fields);
  const declaredUrlParam = isCommandOption(raw.urlParam) ? raw.urlParam : '';
  const declaredOutputParam = isCommandOption(raw.outputParam) ? raw.outputParam : '';
  const urlParam = declaredUrlParam || legacyUrlParam(fields);

  return {
    urlParam,
    outputParam: declaredOutputParam || legacyOutputParam(fields),
    noUrl: legacyNoUrl(fields, raw.noUrl)
  };
}

module.exports = {
  resolveLegacyTemplateConfig
};
