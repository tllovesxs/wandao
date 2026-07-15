(function (root) {
  function firstNonEmpty(...values) {
    for (const value of values) {
      if (value !== undefined && value !== null && String(value).trim() !== '') return String(value).trim();
    }
    return '';
  }

  function compact(value, limit = 700) {
    const text = typeof value === 'string' ? value : JSON.stringify(value ?? '');
    return String(text || '').replace(/\s+/g, ' ').trim().slice(0, limit);
  }

  function numberValue(...values) {
    for (const value of values) {
      const number = Number(value);
      if (Number.isFinite(number) && number > 0) return number;
    }
    return 0;
  }

  function maxCount(...values) {
    return values.reduce((maximum, value) => {
      const number = Number(value);
      return Number.isFinite(number) && number > maximum ? number : maximum;
    }, 0);
  }

  function asArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function formatTaskTime(value) {
    if (!value) return '-';
    const formatter = root.WandaoTime?.formatLocalDateTime;
    if (typeof formatter === 'function') return formatter(value);
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
  }

  function describeFailureItem(item, parent = '') {
    if (!item || typeof item !== 'object') return compact(item);
    const subject = firstNonEmpty(
      item.relativePath,
      item.document,
      item.title,
      item.path,
      item.id,
      item.docId,
      item.nodeId,
      item.url,
      item.target,
      item.file,
      item.resource
    );
    const reason = firstNonEmpty(item.error, item.reason, item.message, item.status, item.code);
    const prefix = [parent, subject].filter(Boolean).join(' / ');
    if (prefix && reason) return `${prefix}：${reason}`;
    return prefix || reason || compact(item);
  }

  function collectFailureItems(data, limit = 100) {
    const items = [];
    const visit = (value, source = '') => {
      if (!value || items.length >= limit) return;
      if (Array.isArray(value)) {
        value.forEach((item) => visit(item, source));
        return;
      }
      if (typeof value !== 'object') return;
      const looksLikeFailure = value.error || value.reason || value.message || value.relativePath || value.url || value.title;
      if (looksLikeFailure) items.push({ source, ...value });
      Object.entries(value).forEach(([key, child]) => {
        if (/fail|error/i.test(key)) visit(child, key);
      });
    };
    if (data && typeof data === 'object') {
      ['failures', 'resourceFailures', 'imageFailures', 'attachmentFailures', 'errors'].forEach((key) => {
        visit(data[key], key);
      });
    }
    return items;
  }

  function countResourceFailureEntries(value, kind = '') {
    let count = 0;
    const visit = (item) => {
      if (!item) return;
      if (Array.isArray(item)) {
        item.forEach(visit);
        return;
      }
      if (typeof item !== 'object') return;
      if (Array.isArray(item.failures)) {
        item.failures.forEach(visit);
        return;
      }
      if (item.url || item.error || item.reason || item.message) {
        if (!kind || item.kind === kind) count += 1;
      }
    };
    visit(value);
    return count;
  }

  function normalizeTaskReport(data, options = {}) {
    const source = data && typeof data === 'object' ? data : {};
    const failureItems = collectFailureItems(source);
    const resourceFailures = asArray(source.resourceFailures);
    const imageFailures = asArray(source.imageFailures);
    const attachmentFailures = asArray(source.attachmentFailures);
    const resourceFailureEntries = countResourceFailureEntries(resourceFailures);
    const imageFailureEntries = countResourceFailureEntries(resourceFailures, 'image');
    const attachmentFailureEntries = countResourceFailureEntries(resourceFailures, 'attachment');
    const imageFailed = maxCount(
      source.imageFailureCount,
      imageFailureEntries,
      countResourceFailureEntries(imageFailures),
      imageFailures.length
    );
    const attachmentFailed = maxCount(
      source.attachmentFailureCount,
      attachmentFailureEntries,
      countResourceFailureEntries(attachmentFailures),
      attachmentFailures.length
    );
    const stats = {
      total: numberValue(source.totalDocs, source.total, source.selectedDocs, source.docCount, source.fileCount, source.totalFiles),
      success: numberValue(source.successCount, source.successfulDocs, source.successfulFiles),
      exported: numberValue(source.exportedDocs, source.exported, source.exportCount),
      imported: numberValue(source.importedDocs, source.imported, source.importCount),
      created: numberValue(source.createdDocs, source.created, source.createdCount),
      updated: numberValue(source.updatedDocs, source.updated, source.updatedCount),
      skipped: numberValue(source.skippedDocs, source.skipped, source.skippedCount),
      failed: numberValue(source.failureCount, source.failedDocs, source.failed, source.errorCount),
      imageSuccess: numberValue(source.imageSuccess, source.imageUploads, source.imageUploadsCount),
      imageFailed,
      attachmentSuccess: numberValue(source.attachmentSuccess, source.attachmentUploads),
      attachmentFailed,
      resourceFailed: maxCount(source.resourceFailureCount, resourceFailureEntries, imageFailed + attachmentFailed)
    };
    if (!stats.success) stats.success = stats.exported || stats.imported || stats.created + stats.updated;
    if (!stats.failed && asArray(source.failures).length) stats.failed = asArray(source.failures).length;
    if (!stats.failed && options.errorText) stats.failed = 1;
    return {
      schemaVersion: 1,
      provider: firstNonEmpty(source.provider, source.platform, options.provider),
      mode: firstNonEmpty(source.mode, options.mode),
      output: firstNonEmpty(source.output, source.outputDir),
      reportFile: firstNonEmpty(source.reportFile),
      stopped: Boolean(source.stopped),
      stats,
      failures: failureItems,
      raw: source
    };
  }

  function summarizeStats(stats = {}, errorText = '') {
    const parts = [];
    if (stats.total) parts.push(`总数 ${stats.total}`);
    if (stats.exported) parts.push(`导出 ${stats.exported}`);
    if (stats.imported) parts.push(`导入 ${stats.imported}`);
    if (stats.created) parts.push(`创建 ${stats.created}`);
    if (stats.updated) parts.push(`更新 ${stats.updated}`);
    if (stats.skipped) parts.push(`跳过 ${stats.skipped}`);
    if (stats.imageSuccess) parts.push(`图片 ${stats.imageSuccess}`);
    if (stats.attachmentSuccess) parts.push(`附件 ${stats.attachmentSuccess}`);
    if (stats.imageFailed) parts.push(`\u56fe\u7247\u5931\u8d25 ${stats.imageFailed}`);
    if (stats.attachmentFailed) parts.push(`\u9644\u4ef6\u5931\u8d25 ${stats.attachmentFailed}`);
    if (stats.resourceFailed) parts.push(`\u8d44\u6e90\u5931\u8d25 ${stats.resourceFailed}`);
    if (stats.failed) parts.push(`失败 ${stats.failed}`);
    if (!parts.length && errorText) parts.push(compact(errorText, 120));
    return parts.join('，') || '暂无统计信息';
  }

  function collectFailureDiagnostics(data, limit = 80) {
    const lines = [];
    const report = normalizeTaskReport(data);
    const pushLine = (label, text) => {
      const content = compact(text, 700);
      if (!content || lines.length >= limit) return;
      lines.push(`${label}：${content}`);
    };
    report.failures.forEach((item, index) => {
      if (lines.length >= limit) return;
      const current = describeFailureItem(item);
      if (current) pushLine(`失败项 #${index + 1}`, current);
      if (Array.isArray(item.failures)) {
        const parent = firstNonEmpty(item.document, item.relativePath, item.title, item.path);
        item.failures.forEach((child, childIndex) => {
          if (lines.length >= limit) return;
          pushLine(`失败项 #${index + 1}.${childIndex + 1}`, describeFailureItem(child, parent));
        });
      }
    });
    if (report.stats.failed > 0 && !lines.length) {
      pushLine('失败统计', `failureCount=${report.stats.failed}，脚本没有返回逐项失败原因，请查看 Python 原始日志。`);
    }
    if (report.stats.imageFailed > 0 && !lines.some((line) => line.includes('图片'))) {
      pushLine('图片失败统计', `imageFailureCount=${report.stats.imageFailed}，脚本没有返回逐项图片失败原因。`);
    }
    if (report.stats.attachmentFailed > 0 && !lines.some((line) => line.includes('\u9644\u4ef6'))) {
      pushLine('\u9644\u4ef6\u5931\u8d25\u7edf\u8ba1', `attachmentFailureCount=${report.stats.attachmentFailed}\uff0c\u811a\u672c\u6ca1\u6709\u8fd4\u56de\u9010\u9879\u9644\u4ef6\u5931\u8d25\u539f\u56e0\u3002`);
    }
    if (lines.length >= limit) {
      lines.push(`还有更多失败项未展示，请打开报告文件查看完整内容：${report.reportFile || report.output || ''}`.trim());
    }
    return lines;
  }

  function statusText(status) {
    const map = {
      running: '\u8fdb\u884c\u4e2d',
      stopping: '\u6b63\u5728\u505c\u6b62',
      interrupted: '\u5df2\u4e2d\u65ad',
      completed: '\u5df2\u5b8c\u6210',
      partial: '\u90e8\u5206\u5b8c\u6210',
      failed: '\u5931\u8d25',
      stopped: '\u5df2\u505c\u6b62'
    };
    return map[status] || status || '\u672a\u77e5';
  }

  function hasResourceWarnings(stats = {}) {
    return maxCount(stats.resourceFailed, stats.imageFailed, stats.attachmentFailed) > 0;
  }

  function taskStatusText(task) {
    return statusText(deriveTaskStatus(task));
  }

  function formatDuration(ms) {
    const seconds = Math.max(0, Math.round((Number(ms) || 0) / 1000));
    if (seconds < 60) return `${seconds} 秒`;
    const minutes = Math.floor(seconds / 60);
    const rest = seconds % 60;
    return `${minutes} 分 ${rest} 秒`;
  }

  function maskArgs(args) {
    const sensitiveKeys = new Set([
      '--password',
      '--password-stdin',
      '--app-secret',
      '--api-key',
      '--client-secret',
      '--token',
      '--cookie'
    ]);
    const masked = [];
    for (let index = 0; index < (args || []).length; index += 1) {
      const value = String(args[index]);
      masked.push(value);
      if (sensitiveKeys.has(value) && index + 1 < args.length) {
        masked.push('***');
        index += 1;
      }
    }
    return masked;
  }

  function createMarkdownTaskReport(task, options = {}) {
    const provider = options.provider || {};
    const maskSensitiveText = options.maskSensitiveText || ((text) => text);
    const normalizedReport = task.report || normalizeTaskReport(task.resultData, {
      errorText: task.error,
      provider: task.providerId,
      mode: task.action
    });
    const failureItems = normalizedReport?.failures || task.stats?.failureItems || [];
    const structuredEvents = (task.logs || [])
      .filter((entry) => entry.event || entry.data)
      .map((entry) => ({
        time: entry.time,
        source: entry.source,
        type: entry.type,
        event: entry.event,
        message: entry.message,
        data: entry.data
      }));
    return maskSensitiveText([
      '# 万能导任务报告',
      '',
      `任务 ID：${task.id}`,
      `平台：${task.providerTitle || provider.title || task.providerId || '-'}`,
      `任务：${task.title || '-'}`,
      `状态：${taskStatusText(task)}`,
      `开始时间：${formatTaskTime(task.startedAt)}`,
      `结束时间：${formatTaskTime(task.finishedAt)}`,
      task.elapsedMs ? `耗时：${formatDuration(task.elapsedMs)}` : '',
      `脚本：${task.script || '-'}`,
      `参数：${JSON.stringify(maskArgs(task.args || []))}`,
      '',
      '## 统计',
      summarizeStats(normalizedReport?.stats || task.stats || {}, task.error),
      normalizedReport?.reportFile ? `报告文件：${normalizedReport.reportFile}` : '',
      normalizedReport?.output ? `输出目录：${normalizedReport.output}` : '',
      '',
      '## 错误',
      task.error || '无',
      '',
      '## 失败项',
      failureItems.length ? JSON.stringify(failureItems, null, 2) : '无',
      '',
      '## 结构化事件',
      structuredEvents.length ? JSON.stringify(structuredEvents, null, 2) : '无',
      '',
      '## 结果数据',
      task.resultData ? JSON.stringify(task.resultData, null, 2) : '无',
      '',
      '## 本任务详细日志',
      task.logs?.length ? task.logs.map((entry) => {
        const event = entry.event ? ` [${entry.event}]` : '';
        return `[${formatTaskTime(entry.time)}] [${entry.source}] [${entry.type}]${event} ${entry.message}`;
      }).join('\n') : '无'
    ].filter((line) => line !== '').join('\n'));
  }

  function taskArtifactPaths(task) {
    const report = task.report || normalizeTaskReport(task.resultData, {
      errorText: task.error,
      provider: task.providerId,
      mode: task.action
    });
    return {
      output: firstNonEmpty(report?.output, task.resultData?.output, task.resultData?.outputDir),
      reportFile: firstNonEmpty(report?.reportFile, task.resultData?.reportFile)
    };
  }

  function taskFailurePreview(task, limit = 3) {
    const source = task.report?.raw || task.resultData || task.report || {};
    const lines = collectFailureDiagnostics(source, Math.max(1, limit));
    if (lines.length) return lines.slice(0, limit);
    if (task.error) return [compact(task.error, 260)];
    return [];
  }

  function taskDocumentFailureCount(task) {
    const stats = task?.report?.stats || task?.stats || {};
    const raw = task?.report?.raw || task?.resultData || {};
    const explicitDocumentFailureKeys = ['failureCount', 'failedDocs', 'failed', 'errorCount'];
    const explicitKey = explicitDocumentFailureKeys.find((key) => Object.prototype.hasOwnProperty.call(raw, key));
    const hasResourceFailureLists = ['resourceFailures', 'imageFailures', 'attachmentFailures']
      .some((key) => Array.isArray(raw[key]) && raw[key].length > 0);
    const declaredFailures = explicitKey ? Number(raw[explicitKey] || 0) : 0;
    const listedFailures = Array.isArray(raw.failures) ? raw.failures.length : 0;
    const statisticFailures = hasResourceFailureLists ? 0 : Number(stats.failed || 0);
    return Math.max(
      Number.isFinite(declaredFailures) ? declaredFailures : 0,
      Number.isFinite(listedFailures) ? listedFailures : 0,
      Number.isFinite(statisticFailures) ? statisticFailures : 0
    );
  }

  function taskResourceFailureCount(task) {
    const stats = task?.report?.stats || task?.stats || {};
    const resourceFailures = Number(stats.resourceFailed || 0);
    const typedResourceFailures = Number(stats.imageFailed || 0) + Number(stats.attachmentFailed || 0);
    return Math.max(
      Number.isFinite(resourceFailures) ? resourceFailures : 0,
      Number.isFinite(typedResourceFailures) ? typedResourceFailures : 0
    );
  }

  function taskFailureCount(task) {
    return taskDocumentFailureCount(task) + taskResourceFailureCount(task);
  }

  function deriveTaskStatus(task = {}, options = {}) {
    const source = task && typeof task === 'object' ? task : {};
    const result = options.result || source.result || null;
    const explicitStatus = String(options.status || source.status || '').toLowerCase();
    const report = source.report?.stats
      ? source.report
      : normalizeTaskReport(source.resultData || source.report || source, {
        errorText: options.errorText || source.error,
        provider: source.providerId,
        mode: source.action
      });
    const stopped = explicitStatus === 'stopped'
      || result?.code === 130
      || result?.data?.stopped === true
      || report?.stopped === true;

    if (stopped) return 'stopped';
    if (explicitStatus === 'running' || explicitStatus === 'stopping' || explicitStatus === 'interrupted') {
      return explicitStatus;
    }
    const failureCount = taskFailureCount({ ...source, report });
    const stats = report?.stats || {};
    const completedCount = Math.max(
      Number(stats.success || 0),
      Number(stats.exported || 0),
      Number(stats.imported || 0),
      Number(stats.created || 0) + Number(stats.updated || 0),
      Math.max(0, Number(stats.total || 0) - Number(stats.failed || 0))
    );
    if (options.thrownError || explicitStatus === 'failed' || (result && result.success === false)) {
      if (failureCount > 0 && completedCount > 0) return 'partial';
      return 'failed';
    }
    if (failureCount > 0) return 'partial';
    if (explicitStatus === 'partial') return 'partial';
    if (explicitStatus === 'completed' || result?.success === true) return 'completed';
    return explicitStatus || 'failed';
  }

  const api = {
    normalizeTaskReport,
    summarizeStats,
    collectFailureDiagnostics,
    collectFailureItems,
    describeFailureItem,
    statusText,
    taskStatusText,
    formatDuration,
    maskArgs,
    createMarkdownTaskReport,
    taskArtifactPaths,
    taskFailurePreview,
    taskFailureCount,
    taskDocumentFailureCount,
    taskResourceFailureCount,
    deriveTaskStatus
  };

  root.WandaoTaskReport = api;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
})(typeof window !== 'undefined' ? window : globalThis);
