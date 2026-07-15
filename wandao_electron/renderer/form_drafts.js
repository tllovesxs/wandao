/*
 * Safe, local form drafts for renderer forms.
 *
 * Drafts are intentionally limited to non-sensitive fields. Credentials are
 * stored by the provider-specific credential flows, never in localStorage.
 */
(function attachFormDrafts(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.WandaoFormDrafts = api;
})(typeof window !== 'undefined' ? window : globalThis, () => {
  const STORAGE_KEY = 'wandao-form-drafts-v1';
  const VERSION = 1;
  const MAX_DRAFTS = 40;
  const MAX_FIELDS_PER_DRAFT = 80;
  const MAX_VALUE_LENGTH = 4096;
  // Prefer a conservative false positive over ever retaining credentials.  Do
  // not require word separators here: provider schemas often use camelCase
  // names such as `accessToken` or `clientSecret`.
  const SENSITIVE_FIELD_PATTERN = /(?:(?:app|client)[-_.\s]*id|api[-_.\s]*key|access[-_.\s]*key|private[-_.\s]*key|import[-_.\s]*mount[-_.\s]*key|(?:knowledge[-_.\s]*base|folder|space)[-_.\s]*id|token|cookie|password|passwd|pwd|secret|signature|authorization|credential|session|bearer|csrf|username|user[-_.\s]*name|email|phone|account|webhook|dsn)/i;
  const SENSITIVE_URL_PART_PATTERN = /(?:^|[-_.])(access[-_.]?token|auth(?:orization)?|code|cookie|credential|jwt|key|password|passwd|pwd|secret|session|sig|signature|ticket|token|x[-_.]?amz[-_.].*)(?:$|[-_.])/i;
  const SENSITIVE_URL_ASSIGNMENT_PATTERN = /(?:^|[?&#;\s])(access[-_.]?token|auth(?:orization)?|code|cookie|credential|jwt|key|password|passwd|pwd|secret|session|sig|signature|ticket|token|x[-_.]?amz[-_.][^=]*)=/i;
  const SENSITIVE_VALUE_PATTERN = /(?:^|\s)(?:authorization|cookie|set-cookie)\s*:|\bbearer\s+[a-z0-9._~+\/-]+=*|-----BEGIN [A-Z ]*PRIVATE KEY-----/i;

  function safeText(value, maxLength = MAX_VALUE_LENGTH) {
    return String(value ?? '').slice(0, maxLength);
  }

  function draftKey(providerId, actionId = 'default') {
    return `${safeText(providerId, 120)}::${safeText(actionId || 'default', 120)}`;
  }

  function fieldMetadata(field) {
    if (!field || typeof field !== 'object') return '';
    const attribute = typeof field.getAttribute === 'function'
      ? (name) => field.getAttribute(name) || ''
      : () => '';
    return [
      field.id,
      field.name,
      field.autocomplete,
      field.ariaLabel,
      field.placeholder,
      attribute('aria-label'),
      attribute('data-draft-key')
    ].map((value) => String(value || '')).join(' ');
  }

  function isSensitiveField(field) {
    if (!field || typeof field !== 'object') return true;
    const attribute = typeof field.getAttribute === 'function'
      ? (name) => field.getAttribute(name)
      : () => null;
    if (String(field.type || '').toLowerCase() === 'password') return true;
    if (attribute('data-draft-sensitive') === 'true' || attribute('data-draft') === 'false') return true;
    return SENSITIVE_FIELD_PATTERN.test(fieldMetadata(field));
  }

  function isSensitiveValue(value) {
    const text = String(value ?? '').trim();
    if (!text) return false;
    if (SENSITIVE_VALUE_PATTERN.test(text)) return true;
    if (!/^https?:\/\//i.test(text)) return false;
    try {
      const parsed = new URL(text);
      if (parsed.username || parsed.password) return true;
      for (const key of parsed.searchParams.keys()) {
        let decodedKey = key;
        try { decodedKey = decodeURIComponent(key); } catch (_) { /* Keep the raw key. */ }
        if (SENSITIVE_URL_PART_PATTERN.test(decodedKey)) return true;
      }
      let tail = `${parsed.search}${parsed.hash}`;
      try { tail = decodeURIComponent(tail); } catch (_) { /* Keep the raw tail. */ }
      return SENSITIVE_URL_ASSIGNMENT_PATTERN.test(tail) || SENSITIVE_VALUE_PATTERN.test(tail);
    } catch (_) {
      return false;
    }
  }

  function fieldKey(field) {
    if (!field || typeof field !== 'object') return '';
    const attribute = typeof field.getAttribute === 'function'
      ? field.getAttribute('data-draft-key')
      : '';
    return safeText(attribute || field.name || field.id, 180);
  }

  function isDraftableField(field) {
    if (!field || field.disabled || isSensitiveField(field)) return false;
    const type = String(field.type || '').toLowerCase();
    if (['button', 'submit', 'reset', 'hidden', 'file', 'password', 'radio'].includes(type)) return false;
    return Boolean(fieldKey(field));
  }

  function collectFieldValues(rootNode) {
    const fields = rootNode?.querySelectorAll ? Array.from(rootNode.querySelectorAll('input, textarea, select')) : [];
    const values = {};
    for (const field of fields) {
      if (!isDraftableField(field)) continue;
      const key = fieldKey(field);
      if (Object.prototype.hasOwnProperty.call(values, key)) continue;
      if (Object.keys(values).length >= MAX_FIELDS_PER_DRAFT) break;
      if (String(field.type || '').toLowerCase() !== 'checkbox' && isSensitiveValue(field.value)) continue;
      values[key] = String(field.type || '').toLowerCase() === 'checkbox'
        ? { checked: Boolean(field.checked) }
        : { value: safeText(field.value) };
    }
    return values;
  }

  function selectHasValue(field, value) {
    if (String(field.tagName || '').toUpperCase() !== 'SELECT' || !field.options?.length) return true;
    return Array.from(field.options).some((option) => String(option.value) === String(value));
  }

  function applyFieldValues(rootNode, values) {
    if (!values || typeof values !== 'object' || !rootNode?.querySelectorAll) return 0;
    const fields = Array.from(rootNode.querySelectorAll('input, textarea, select'));
    let restored = 0;
    for (const field of fields) {
      if (!isDraftableField(field)) continue;
      const value = values[fieldKey(field)];
      if (!value || typeof value !== 'object') continue;
      if (String(field.type || '').toLowerCase() === 'checkbox') {
        if (typeof value.checked !== 'boolean') continue;
        field.checked = value.checked;
        restored += 1;
      } else if (Object.prototype.hasOwnProperty.call(value, 'value') && selectHasValue(field, value.value)) {
        field.value = safeText(value.value);
        restored += 1;
      }
    }
    return restored;
  }

  function emptyStore() {
    return { version: VERSION, drafts: {} };
  }

  function loadStore(storage) {
    try {
      const raw = storage?.getItem?.(STORAGE_KEY);
      if (!raw) return emptyStore();
      const parsed = JSON.parse(raw);
      if (parsed?.version !== VERSION || !parsed.drafts || typeof parsed.drafts !== 'object' || Array.isArray(parsed.drafts)) {
        return emptyStore();
      }
      return { version: VERSION, drafts: parsed.drafts };
    } catch (_) {
      return emptyStore();
    }
  }

  function saveStore(storage, store) {
    try {
      storage?.setItem?.(STORAGE_KEY, JSON.stringify(store));
      return true;
    } catch (_) {
      return false;
    }
  }

  function clearAll(storage) {
    try {
      storage?.removeItem?.(STORAGE_KEY);
      return true;
    } catch (_) {
      return false;
    }
  }

  function pruneDrafts(drafts) {
    const entries = Object.entries(drafts || {})
      .filter(([, draft]) => draft && typeof draft === 'object' && draft.values && typeof draft.values === 'object')
      .sort(([, left], [, right]) => Number(right.savedAt || 0) - Number(left.savedAt || 0))
      .slice(0, MAX_DRAFTS);
    return Object.fromEntries(entries);
  }

  function saveDraft(storage, providerId, actionId, rootNode, now = Date.now()) {
    const values = collectFieldValues(rootNode);
    if (!Object.keys(values).length) return { saved: false, fieldCount: 0 };
    const store = loadStore(storage);
    const key = draftKey(providerId, actionId);
    store.drafts[key] = {
      providerId: safeText(providerId, 120),
      actionId: safeText(actionId || 'default', 120),
      savedAt: Number(now) || Date.now(),
      values
    };
    store.drafts = pruneDrafts(store.drafts);
    return { saved: saveStore(storage, store), fieldCount: Object.keys(values).length };
  }

  function restoreDraft(storage, providerId, actionId, rootNode) {
    const draft = loadStore(storage).drafts[draftKey(providerId, actionId)];
    if (!draft) return { restored: 0, actionId: '' };
    return { restored: applyFieldValues(rootNode, draft.values), actionId: draft.actionId || String(actionId || 'default') };
  }

  function restoreLatestDraft(storage, providerId, rootNode) {
    const provider = safeText(providerId, 120);
    const drafts = Object.values(loadStore(storage).drafts)
      .filter((draft) => draft?.providerId === provider)
      .sort((left, right) => Number(right.savedAt || 0) - Number(left.savedAt || 0));
    const draft = drafts[0];
    if (!draft) return { restored: 0, actionId: '' };
    return { restored: applyFieldValues(rootNode, draft.values), actionId: draft.actionId || 'default' };
  }

  return {
    STORAGE_KEY,
    VERSION,
    applyFieldValues,
    clearAll,
    collectFieldValues,
    draftKey,
    isSensitiveField,
    isSensitiveValue,
    restoreDraft,
    restoreLatestDraft,
    saveDraft
  };
});
