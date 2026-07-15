(function (root) {
  function normalizeText(value) {
    return String(value || '')
      .normalize('NFKC')
      .toLocaleLowerCase()
      .replace(/\s+/g, ' ')
      .trim();
  }

  function tocMaps(nodes) {
    const source = Array.isArray(nodes) ? nodes : [];
    const byId = new Map(source.map((node) => [node.nodeId, node]));
    const children = new Map();
    source.forEach((node) => {
      const parentId = node.parentNodeId && byId.has(node.parentNodeId) ? node.parentNodeId : '';
      if (!children.has(parentId)) children.set(parentId, []);
      children.get(parentId).push(node);
    });
    return { byId, children };
  }

  function matchingNodeIds(nodes, query) {
    const normalized = normalizeText(query);
    if (!normalized) return new Set();
    const terms = normalized.split(' ');
    return new Set((Array.isArray(nodes) ? nodes : [])
      .filter((node) => {
        const title = normalizeText(node?.title);
        return terms.every((term) => title.includes(term));
      })
      .map((node) => node.nodeId));
  }

  function ancestorsOf(nodeIds, byId) {
    const ancestors = new Set();
    nodeIds.forEach((nodeId) => {
      let node = byId.get(nodeId);
      const visited = new Set();
      while (node?.parentNodeId && byId.has(node.parentNodeId) && !visited.has(node.parentNodeId)) {
        visited.add(node.parentNodeId);
        ancestors.add(node.parentNodeId);
        node = byId.get(node.parentNodeId);
      }
    });
    return ancestors;
  }

  function visibleTocRows(nodes, options = {}) {
    const { byId, children } = tocMaps(nodes);
    const query = normalizeText(options.query);
    const limit = Math.max(1, Number(options.limit) || 120);
    const expanded = options.expanded instanceof Set ? options.expanded : new Set(options.expanded || []);
    const matches = matchingNodeIds(nodes, query);
    const ancestors = ancestorsOf(matches, byId);
    const searchBranch = new Set([...matches, ...ancestors]);
    const rows = [];
    let hasMore = false;
    let visitedCount = 0;

    const visit = (node, depth) => {
      if (rows.length > limit) return;
      if (query && !searchBranch.has(node.nodeId)) return;
      visitedCount += 1;
      const childNodes = children.get(node.nodeId) || [];
      const visibleChildren = query
        ? childNodes.filter((child) => searchBranch.has(child.nodeId))
        : childNodes;
      const hasChildren = childNodes.length > 0;
      const isExpanded = Boolean(hasChildren && (expanded.has(node.nodeId) || (query && visibleChildren.length > 0)));
      rows.push({
        node,
        depth,
        hasChildren,
        expanded: isExpanded,
        matchesQuery: matches.has(node.nodeId)
      });
      if (rows.length > limit) {
        hasMore = true;
        return;
      }
      if (!isExpanded) return;
      for (const child of visibleChildren) {
        visit(child, depth + 1);
        if (hasMore) return;
      }
    };

    for (const rootNode of children.get('') || []) {
      visit(rootNode, 0);
      if (hasMore) break;
    }

    if (rows.length > limit) rows.pop();
    return {
      rows,
      hasMore,
      matchCount: matches.size,
      visitedCount
    };
  }

  function expandableNodeIds(nodes) {
    const { children } = tocMaps(nodes);
    return [...children.entries()]
      .filter(([nodeId, childNodes]) => nodeId && childNodes.length)
      .map(([nodeId]) => nodeId);
  }

  const api = {
    normalizeText,
    tocMaps,
    matchingNodeIds,
    ancestorsOf,
    visibleTocRows,
    expandableNodeIds
  };

  root.WandaoTocBrowser = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
