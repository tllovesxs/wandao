/*
 * Explicit, provider-scoped recent values for reusable form inputs.
 *
 * A field is never inferred from its label or id. The host must opt it in with
 * data-history-kind="url|path|text". Credential-like fields and values still
 * fail closed even if a provider declares them incorrectly.
 */
(function attachRecentInputs(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.WandaoRecentInputs = api;
})(typeof window !== 'undefined' ? window : globalThis, () => {
  const STORAGE_KEY = 'wandao-recent-inputs-v1';
  const VERSION = 1;
  const MAX_VALUES = 3;
  const MAX_BUCKETS = 160;
  const MAX_VALUE_LENGTH = 2048;
  const MAX_TEXT_LENGTH = 256;
  const TTL_MS = 180 * 24 * 60 * 60 * 1000;
  const ALLOWED_KINDS = new Set(['url', 'path', 'text']);
  const SENSITIVE_FIELD_PATTERN = /(?:(?:app|client)[-_.\s]*id|api[-_.\s]*key|access[-_.\s]*key|private[-_.\s]*key|import[-_.\s]*mount[-_.\s]*key|(?:knowledge[-_.\s]*base|folder|space)[-_.\s]*id|token|cookie|password|passwd|pwd|secret|signature|authorization|credential|session|bearer|csrf|username|user[-_.\s]*name|email|phone|account|webhook|dsn)/i;
  const SENSITIVE_QUERY_KEY_PATTERN = /(?:^|[-_.])(access[-_.]?token|auth(?:orization)?|code|cookie|credential|jwt|key|password|passwd|pwd|secret|session|sig|signature|ticket|token|x[-_.]?amz[-_.].*)(?:$|[-_.])/i;
  const SENSITIVE_URL_ASSIGNMENT_PATTERN = /(?:^|[?&#;\s])(access[-_.]?token|auth(?:orization)?|code|cookie|credential|jwt|key|password|passwd|pwd|secret|session|sig|signature|ticket|token|x[-_.]?amz[-_.][^=]*)=/i;
  const SENSITIVE_VALUE_PATTERN = /(?:^|\s)(?:authorization|cookie|set-cookie)\s*:|\bbearer\s+[a-z0-9._~+\/-]+=*|-----BEGIN [A-Z ]*PRIVATE KEY-----/i;

  function safeText(value, maxLength = MAX_VALUE_LENGTH) {
    return String(value ?? '').trim().slice(0, maxLength);
  }

  function attribute(field, name) {
    return typeof field?.getAttribute === 'function' ? field.getAttribute(name) : null;
  }

  function fieldMetadata(field) {
    return [
      field?.id,
      field?.name,
      field?.autocomplete,
      field?.placeholder,
      attribute(field, 'aria-label'),
      attribute(field, 'data-history-key')
    ].map((value) => String(value || '')).join(' ');
  }

  function historyKind(field) {
    const kind = safeText(attribute(field, 'data-history-kind'), 20).toLowerCase();
    return ALLOWED_KINDS.has(kind) ? kind : '';
  }

  function fieldKey(field) {
    return safeText(attribute(field, 'data-history-key') || field?.name || field?.id, 180);
  }

  function isSensitiveField(field) {
    if (!field || typeof field !== 'object') return true;
    const type = String(field.type || '').toLowerCase();
    if (type === 'password') return true;
    if (attribute(field, 'data-history') === 'false'
      || attribute(field, 'data-history-sensitive') === 'true'
      || attribute(field, 'data-draft-sensitive') === 'true'
      || attribute(field, 'data-draft') === 'false') return true;
    return SENSITIVE_FIELD_PATTERN.test(fieldMetadata(field));
  }

  function isHistoryField(field) {
    if (!field || field.disabled || field.readOnly || isSensitiveField(field)) return false;
    const tagName = String(field.tagName || 'INPUT').toUpperCase();
    const type = String(field.type || 'text').toLowerCase();
    return tagName === 'INPUT'
      && ['text', 'url', 'search'].includes(type)
      && Boolean(historyKind(field))
      && Boolean(fieldKey(field));
  }

  function decodeRepeatedly(value) {
    let decoded = String(value || '');
    for (let count = 0; count < 2; count += 1) {
      try {
        const next = decodeURIComponent(decoded);
        if (next === decoded) break;
        decoded = next;
      } catch (_) {
        break;
      }
    }
    return decoded;
  }

  function urlIsSafe(value) {
    let parsed;
    try {
      parsed = new URL(value);
    } catch (_) {
      return false;
    }
    if (!['https:', 'http:'].includes(parsed.protocol)) return false;
    if (parsed.username || parsed.password) return false;
    for (const key of parsed.searchParams.keys()) {
      if (SENSITIVE_QUERY_KEY_PATTERN.test(decodeRepeatedly(key))) return false;
    }
    const decodedTail = decodeRepeatedly(`${parsed.search}${parsed.hash}`);
    if (SENSITIVE_URL_ASSIGNMENT_PATTERN.test(decodedTail) || SENSITIVE_VALUE_PATTERN.test(decodedTail)) return false;
    return true;
  }

  function valueIsSafe(field, value) {
    if (!isHistoryField(field)) return false;
    const kind = historyKind(field);
    const maxLength = kind === 'text' ? MAX_TEXT_LENGTH : MAX_VALUE_LENGTH;
    const normalized = safeText(value, maxLength);
    if (!normalized || normalized !== String(value ?? '').trim()) return false;
    if (SENSITIVE_VALUE_PATTERN.test(decodeRepeatedly(normalized))) return false;
    if (kind === 'url' && !urlIsSafe(normalized)) return false;
    const defaultValue = safeText(attribute(field, 'data-history-default-value'), maxLength);
    if (defaultValue && normalized === defaultValue) return false;
    return true;
  }

  function emptyStore() {
    return { version: VERSION, buckets: {} };
  }

  function loadStore(storage) {
    try {
      const parsed = JSON.parse(storage?.getItem?.(STORAGE_KEY) || 'null');
      if (parsed?.version !== VERSION || !parsed.buckets || typeof parsed.buckets !== 'object' || Array.isArray(parsed.buckets)) {
        return emptyStore();
      }
      return { version: VERSION, buckets: parsed.buckets };
    } catch (_) {
      return emptyStore();
    }
  }

  function saveStore(storage, store) {
    if (typeof storage?.setItem !== 'function') return false;
    try {
      storage.setItem(STORAGE_KEY, JSON.stringify(store));
      return true;
    } catch (_) {
      return false;
    }
  }

  function bucketKey(scope, key) {
    return `${safeText(scope, 240)}::${safeText(key, 180)}`;
  }

  function validEntries(bucket, now = Date.now()) {
    const cutoff = Number(now) - TTL_MS;
    const seen = new Set();
    const entries = [];
    for (const entry of Array.isArray(bucket?.entries) ? bucket.entries : []) {
      const value = safeText(entry?.value);
      const savedAt = Number(entry?.savedAt || 0);
      if (!value || !Number.isFinite(savedAt) || savedAt < cutoff || seen.has(value)) continue;
      seen.add(value);
      entries.push({ value, savedAt });
    }
    return entries.sort((left, right) => right.savedAt - left.savedAt).slice(0, MAX_VALUES);
  }

  function listValues(storage, scope, field, now = Date.now()) {
    if (!isHistoryField(field)) return [];
    const bucket = loadStore(storage).buckets[bucketKey(scope, fieldKey(field))];
    return validEntries(bucket, now).map((entry) => entry.value);
  }

  function pruneBuckets(buckets, now = Date.now()) {
    const entries = Object.entries(buckets || {})
      .map(([key, bucket]) => [key, { ...bucket, entries: validEntries(bucket, now) }])
      .filter(([, bucket]) => bucket.entries.length)
      .sort(([, left], [, right]) => Number(right.updatedAt || 0) - Number(left.updatedAt || 0))
      .slice(0, MAX_BUCKETS);
    return Object.fromEntries(entries);
  }

  function recordValue(storage, scope, field, value = field?.value, now = Date.now()) {
    if (!valueIsSafe(field, value)) return { saved: false, reason: 'unsafe-or-empty', values: [] };
    const normalized = safeText(value, historyKind(field) === 'text' ? MAX_TEXT_LENGTH : MAX_VALUE_LENGTH);
    const store = loadStore(storage);
    const key = bucketKey(scope, fieldKey(field));
    const previous = validEntries(store.buckets[key], now).filter((entry) => entry.value !== normalized);
    const entries = [{ value: normalized, savedAt: Number(now) || Date.now() }, ...previous].slice(0, MAX_VALUES);
    store.buckets[key] = {
      scope: safeText(scope, 240),
      fieldKey: fieldKey(field),
      kind: historyKind(field),
      updatedAt: Number(now) || Date.now(),
      entries
    };
    store.buckets = pruneBuckets(store.buckets, now);
    return { saved: saveStore(storage, store), reason: '', values: entries.map((entry) => entry.value) };
  }

  function recordRoot(storage, scope, rootNode, now = Date.now()) {
    const fields = rootNode?.querySelectorAll
      ? Array.from(rootNode.querySelectorAll('input[data-history-kind]'))
      : [];
    let saved = 0;
    let failed = 0;
    for (const field of fields) {
      const result = recordValue(storage, scope, field, field.value, now);
      if (result.saved) saved += 1;
      else if (result.reason !== 'unsafe-or-empty') failed += 1;
    }
    return { saved, failed };
  }

  function clearField(storage, scope, field) {
    if (!fieldKey(field)) return false;
    const store = loadStore(storage);
    delete store.buckets[bucketKey(scope, fieldKey(field))];
    return saveStore(storage, store);
  }

  function removeValue(storage, scope, field, value, now = Date.now()) {
    const store = loadStore(storage);
    const key = bucketKey(scope, fieldKey(field));
    const entries = validEntries(store.buckets[key], now).filter((entry) => entry.value !== String(value));
    if (!entries.length) delete store.buckets[key];
    else store.buckets[key] = { ...store.buckets[key], updatedAt: Number(now) || Date.now(), entries };
    return saveStore(storage, store);
  }

  function clearAll(storage) {
    if (typeof storage?.removeItem !== 'function') return false;
    try {
      storage.removeItem(STORAGE_KEY);
      return true;
    } catch (_) {
      return false;
    }
  }

  function compactValue(value, kind) {
    const text = String(value || '');
    if (kind === 'url') {
      try {
        const parsed = new URL(text);
        const compact = `${parsed.host}${parsed.pathname}${parsed.search}${parsed.hash}`;
        return compact.length > 72 ? `${compact.slice(0, 34)}…${compact.slice(-34)}` : compact;
      } catch (_) {
        // Fall through to the generic compact representation.
      }
    }
    return text.length > 72 ? `${text.slice(0, 34)}…${text.slice(-34)}` : text;
  }

  function enhanceField(storage, scope, field) {
    if (!isHistoryField(field) || attribute(field, 'data-history-enhanced') === 'true') return null;
    const documentRef = field.ownerDocument;
    const container = field.closest?.('.form-group') || field.parentElement;
    if (!documentRef?.createElement || !container) return null;
    field.setAttribute('data-history-enhanced', 'true');
    field.setAttribute('autocomplete', 'off');
    if (historyKind(field) === 'url') field.setAttribute('spellcheck', 'false');

    const panel = documentRef.createElement('div');
    panel.className = 'recent-input-history';
    panel.setAttribute('aria-live', 'polite');
    container.appendChild(panel);

    const render = () => {
      const values = listValues(storage, scope, field);
      panel.replaceChildren();
      panel.hidden = values.length === 0;
      if (!values.length) return;

      const header = documentRef.createElement('div');
      header.className = 'recent-input-history-head';
      const label = documentRef.createElement('span');
      label.textContent = '最近使用（仅保存在本机）';
      header.appendChild(label);
      const clearButton = documentRef.createElement('button');
      clearButton.type = 'button';
      clearButton.className = 'recent-input-clear';
      clearButton.textContent = '清除此字段';
      clearButton.setAttribute('aria-label', `清除${attribute(field, 'data-history-label') || '此字段'}的最近输入`);
      clearButton.addEventListener('click', () => {
        clearField(storage, scope, field);
        render();
        field.focus();
      });
      header.appendChild(clearButton);
      panel.appendChild(header);

      const options = documentRef.createElement('div');
      options.className = 'recent-input-options';
      for (const value of values) {
        const row = documentRef.createElement('div');
        row.className = 'recent-input-row';
        const useButton = documentRef.createElement('button');
        useButton.type = 'button';
        useButton.className = 'recent-input-value';
        useButton.textContent = compactValue(value, historyKind(field));
        useButton.title = value;
        useButton.setAttribute('aria-label', `使用最近输入：${value}`);
        useButton.addEventListener('click', () => {
          field.value = value;
          field.dispatchEvent(new Event('input', { bubbles: true }));
          field.dispatchEvent(new Event('change', { bubbles: true }));
          field.focus();
        });
        row.appendChild(useButton);

        const removeButton = documentRef.createElement('button');
        removeButton.type = 'button';
        removeButton.className = 'recent-input-remove';
        removeButton.textContent = '移除';
        removeButton.setAttribute('aria-label', `移除最近输入：${value}`);
        removeButton.addEventListener('click', () => {
          removeValue(storage, scope, field, value);
          render();
          field.focus();
        });
        row.appendChild(removeButton);
        options.appendChild(row);
      }
      panel.appendChild(options);
    };

    const recordCommittedValue = () => {
      const result = recordValue(storage, scope, field, field.value);
      if (result.saved) render();
    };
    field.addEventListener('change', recordCommittedValue);
    render();
    return { field, panel, render, recordCommittedValue };
  }

  function enhanceRoot(storage, scope, rootNode) {
    const fields = rootNode?.querySelectorAll
      ? Array.from(rootNode.querySelectorAll('input[data-history-kind]'))
      : [];
    return fields.map((field) => enhanceField(storage, scope, field)).filter(Boolean);
  }

  return {
    MAX_VALUES,
    STORAGE_KEY,
    TTL_MS,
    VERSION,
    bucketKey,
    clearAll,
    clearField,
    compactValue,
    enhanceRoot,
    fieldKey,
    historyKind,
    isHistoryField,
    isSensitiveField,
    listValues,
    loadStore,
    recordRoot,
    recordValue,
    removeValue,
    urlIsSafe,
    valueIsSafe
  };
});
