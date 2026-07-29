#!/usr/bin/env node
const { spawnSync } = require('node:child_process');
const path = require('node:path');

const SEVERITY = {
  info: 0,
  low: 1,
  moderate: 2,
  high: 3,
  critical: 4
};

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
    blocked.push(`${name} (${vulnerability.severity || 'unknown'})`);
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
  evaluateAudit
};
