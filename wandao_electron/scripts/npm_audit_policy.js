#!/usr/bin/env node
const { spawnSync } = require('node:child_process');
const path = require('node:path');

const ALLOWED_ADVISORY_URLS = new Set([
  // electron-builder 26.15.7 still reaches minimatch 3/5/9, whose declared
  // brace-expansion ranges cannot accept the patched 5.0.8 major safely.
  // Wandao only invokes these packages while packaging trusted repository
  // paths. Remove this exception when electron-builder updates those chains.
  'https://github.com/advisories/GHSA-mh99-v99m-4gvg'
]);

const SEVERITY = {
  info: 0,
  low: 1,
  moderate: 2,
  high: 3,
  critical: 4
};

function collectAdvisoryUrls(name, vulnerabilities, visiting = new Set()) {
  const vulnerability = vulnerabilities[name];
  if (!vulnerability) return { urls: new Set(), unresolved: true };
  if (visiting.has(name)) return { urls: new Set(), unresolved: false };
  const nextVisiting = new Set(visiting);
  nextVisiting.add(name);
  const causes = Array.isArray(vulnerability.via) ? vulnerability.via : [];
  if (!causes.length) return { urls: new Set(), unresolved: true };
  const urls = new Set();
  let unresolved = false;
  causes.forEach((cause) => {
    if (typeof cause === 'string') {
      const nested = collectAdvisoryUrls(cause, vulnerabilities, nextVisiting);
      nested.urls.forEach((url) => urls.add(url));
      unresolved ||= nested.unresolved;
      return;
    }
    if (cause?.url) urls.add(cause.url);
    else unresolved = true;
  });
  return { urls, unresolved };
}

function isAllowedVulnerability(name, vulnerabilities) {
  const collected = collectAdvisoryUrls(name, vulnerabilities);
  return !collected.unresolved
    && collected.urls.size > 0
    && [...collected.urls].every((url) => ALLOWED_ADVISORY_URLS.has(url));
}

function evaluateAudit(report, minimumSeverity = 'high') {
  if (!report || report.error || !report.vulnerabilities) {
    return {
      passed: false,
      ignored: [],
      blocked: ['npm audit 没有返回可验证的漏洞数据']
    };
  }
  const threshold = SEVERITY[minimumSeverity] ?? SEVERITY.high;
  const ignored = [];
  const blocked = [];
  for (const [name, vulnerability] of Object.entries(report.vulnerabilities)) {
    if ((SEVERITY[vulnerability.severity] ?? 0) < threshold) continue;
    if (isAllowedVulnerability(name, report.vulnerabilities)) ignored.push(name);
    else blocked.push(`${name} (${vulnerability.severity || 'unknown'})`);
  }
  return { passed: blocked.length === 0, ignored, blocked };
}

function runAudit() {
  const npmArgs = [
    'audit',
    '--package-lock-only',
    '--registry=https://registry.npmjs.org',
    '--json'
  ];
  const executable = process.platform === 'win32' ? (process.env.ComSpec || 'cmd.exe') : 'npm';
  const commandArgs = process.platform === 'win32'
    ? ['/d', '/s', '/c', `npm ${npmArgs.join(' ')}`]
    : npmArgs;
  const result = spawnSync(executable, commandArgs, {
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
    cwd: path.resolve(__dirname, '..')
  });
  if (result.error) throw result.error;
  let report;
  try {
    report = JSON.parse(result.stdout || '');
  } catch (error) {
    throw new Error(`无法解析 npm audit 输出：${error.message}`);
  }
  const outcome = evaluateAudit(report);
  if (outcome.ignored.length) {
    console.warn(`已应用构建期临时例外 GHSA-mh99-v99m-4gvg：${outcome.ignored.join(', ')}`);
  }
  if (!outcome.passed) {
    console.error(`发现未获批准的高危或严重依赖漏洞：${outcome.blocked.join(', ')}`);
    process.exitCode = 1;
    return;
  }
  console.log('npm dependency audit policy passed.');
}

if (require.main === module) {
  try {
    runAudit();
  } catch (error) {
    console.error(error.message || String(error));
    process.exitCode = 1;
  }
}

module.exports = {
  ALLOWED_ADVISORY_URLS,
  collectAdvisoryUrls,
  evaluateAudit,
  isAllowedVulnerability
};
