(function initWandaoTocTree(root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.WandaoTocTree = api;
})(typeof window !== 'undefined' ? window : globalThis, () => {
  function valueAtPath(source, pathExpression) {
    if (!pathExpression) return source;
    return String(pathExpression).split('.').filter(Boolean).reduce((value, key) => {
      if (value === null || value === undefined) return undefined;
      if (Array.isArray(value) && /^\d+$/.test(key)) return value[Number(key)];
      return value[key];
    }, source);
  }

  function firstDefinedAtPath(source, primaryKey, fallbackKeys) {
    for (const key of [primaryKey, ...fallbackKeys].filter(Boolean)) {
      const value = valueAtPath(source, key);
      if (value !== undefined && value !== null) return value;
    }
    return undefined;
  }

  function normalizeStandardTocNodes(provider, data) {
    const toc = provider?.toc || {};
    const configuredItems = valueAtPath(data, toc.itemsPath || 'nodes');
    const items = Array.isArray(configuredItems) ? configuredItems : ['nodes', 'ordered', 'toc', 'items']
      .map((path) => valueAtPath(data, path))
      .find(Array.isArray);
    if (!items) return [];
    const idKey = toc.idKey || 'nodeId';
    const exportIdKey = toc.exportIdKey || 'exportId';
    const titleKey = toc.titleKey || 'title';
    const parentKey = toc.parentIdKey || 'parentNodeId';
    const selectableKey = toc.selectableKey || 'selectable';
    const typeKey = toc.typeKey || 'type';
    const selectableTypes = Array.isArray(toc.selectableTypes) ? new Set(toc.selectableTypes.map(String)) : null;
    const parentPrefix = toc.nodePrefix || provider.id;
    return items.map((item, index) => {
      const rawId = String(firstDefinedAtPath(item, idKey, ['nodeId', 'id', 'uuid', 'guid', 'key', 'wiki_token']) ?? `${provider.id}-node-${index}`);
      const rawParent = String(firstDefinedAtPath(item, parentKey, ['parentNodeId', 'parent_id', 'parentId', '_parentId', 'parent_uuid', 'parent_wiki_token', 'parentGuid', 'parentKey']) ?? '');
      const exportId = String(firstDefinedAtPath(item, exportIdKey, ['exportId', 'id', 'uuid', 'guid', 'key', 'wiki_token']) ?? '');
      const title = String(firstDefinedAtPath(item, titleKey, ['title', 'name']) ?? '未命名');
      const typeValue = String(firstDefinedAtPath(item, typeKey, ['type', 'nodeType']) ?? '');
      const explicitSelectable = valueAtPath(item, selectableKey);
      let selectable = Boolean(explicitSelectable);
      if (selectableTypes) selectable = selectableTypes.has(typeValue);
      if (toc.selectableWhenExportId !== false && exportId && explicitSelectable === undefined && !selectableTypes) selectable = true;
      return { nodeId: rawId.includes(':') ? rawId : `${parentPrefix}:${rawId}`, exportId, title, parentNodeId: rawParent ? (rawParent.includes(':') ? rawParent : `${parentPrefix}:${rawParent}`) : '', selectable, raw: item };
    });
  }

  function tocNodeMaps(nodes) {
    const byId = new Map(nodes.map((node) => [node.nodeId, node]));
    const children = new Map();
    nodes.forEach((node) => {
      const parent = node.parentNodeId && byId.has(node.parentNodeId) ? node.parentNodeId : '';
      if (!children.has(parent)) children.set(parent, []);
      children.get(parent).push(node);
    });
    return { byId, children };
  }

  function selectionArgs(provider, exportIds) {
    const selectionArg = provider?.toc?.selectionArg || '--doc-id';
    return (exportIds || [])
      .filter((exportId) => exportId !== null && exportId !== undefined && String(exportId).trim())
      .flatMap((exportId) => [selectionArg, String(exportId)]);
  }

  return { normalizeStandardTocNodes, tocNodeMaps, selectionArgs, valueAtPath };
});
