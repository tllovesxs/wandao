const test = require('node:test');
const assert = require('node:assert/strict');
const { parseLastJson, parseProcessResult, STRUCTURED_LOG_PREFIX } = require('../wandao_electron/process_result');

// 各插件 main() 的实际结果输出形态（grep "print(json.dumps" plugins/*/backend/*.py）：
//   1. json.dumps(result, ensure_ascii=False, indent=2)               —— 绝大多数插件（yuque/zsxq/feishu/wiz/yinxiang/ima/...）
//   2. json.dumps(result, ensure_ascii=False)                         —— obsidian
//   3. json.dumps(result, ensure_ascii=False, separators=(",", ":"))  —— wps
//   4. json.dumps(result, ensure_ascii=True, indent=2)                —— import_feishu
// pretty-print 等价于 JSON.stringify(value, null, 2)；compact 等价于 JSON.stringify(value)。
const prettyPrint = (value) => JSON.stringify(value, null, 2);
const compactPrint = (value) => JSON.stringify(value);

function buildExportReport(count) {
  const exportedItems = [];
  for (let index = 0; index < count; index += 1) {
    exportedItems.push({
      id: `doc-${index}`,
      title: `第 ${index} 篇文档`,
      path: `output/2026/第 ${index} 篇文档.md`,
      bytes: 1024 + index,
      images: index % 3,
      exportedAt: '2026-07-27T10:00:00+08:00'
    });
  }
  return {
    kind: 'wandao.result',
    schemaVersion: 1,
    provider: 'zsxq',
    mode: 'export',
    output: 'output',
    totalDocs: count,
    successCount: count,
    failureCount: 0,
    elapsedSeconds: 42.5,
    exportedItems
  };
}

// 门禁阈值：回退扫描限定零缩进之前实测 3000 条 2_764ms / 10000 条 23_270ms（O(n^2)），
// 之后 12ms / 33ms。阈值取改动前的 1/10 出头，既远低于改动前耗时，又给改动后留 15 倍以上余量。
const BUDGET_3K_MS = 250;
const BUDGET_10K_MS = 500;

function measure(stdout) {
  const started = process.hrtime.bigint();
  const parsed = parseLastJson(stdout);
  const elapsedMs = Number(process.hrtime.bigint() - started) / 1e6;
  return { parsed, elapsedMs };
}

// ---------------------------------------------------------------------------
// 性能回归：stdout 混进一行普通输出后首次 JSON.parse 失败，会落到回退扫描。
// ---------------------------------------------------------------------------

test('普通日志行 + 3000 条 pretty-print 报告：解析正确且不退化成 O(n^2)', () => {
  const report = buildExportReport(3000);
  const stdout = `正在导出星球「万能导」的 3000 条帖子…\n${prettyPrint(report)}\n`;
  const { parsed, elapsedMs } = measure(stdout);
  console.log(`[perf] 3000 条 pretty-print + 普通日志行：${elapsedMs.toFixed(0)}ms`);
  assert.ok(parsed, '混入普通输出后仍必须解析出结果对象');
  assert.equal(parsed.provider, 'zsxq');
  assert.equal(parsed.totalDocs, 3000);
  assert.equal(parsed.exportedItems.length, 3000);
  assert.ok(
    elapsedMs < BUDGET_3K_MS,
    `parseLastJson 耗时 ${elapsedMs.toFixed(0)}ms，超过 ${BUDGET_3K_MS}ms 预算（回退扫描疑似退化为 O(n^2)）`
  );
});

test('普通日志行 + 10000 条 pretty-print 报告：不再阻塞主进程数十秒', () => {
  const report = buildExportReport(10000);
  const stdout = `扫描到 10000 条笔记，开始导出…\n${prettyPrint(report)}\n`;
  const { parsed, elapsedMs } = measure(stdout);
  console.log(`[perf] 10000 条 pretty-print + 普通日志行：${elapsedMs.toFixed(0)}ms`);
  assert.ok(parsed, '一万条场景仍必须解析出结果对象');
  assert.equal(parsed.exportedItems.length, 10000);
  assert.equal(parsed.exportedItems[9999].id, 'doc-9999');
  assert.ok(
    elapsedMs < BUDGET_10K_MS,
    `parseLastJson 耗时 ${elapsedMs.toFixed(0)}ms，超过 ${BUDGET_10K_MS}ms 预算（一万条曾阻塞主进程 31 秒）`
  );
});

// ---------------------------------------------------------------------------
// 反向断言：回退扫描改严之后，下列真实输出形态必须仍能解析出结果。
// ---------------------------------------------------------------------------

test('干净 stdout：pretty-print 结果独占输出', () => {
  const stdout = `${prettyPrint({ provider: 'yuque', successCount: 3 })}\n`;
  const parsed = parseLastJson(stdout);
  assert.equal(parsed.provider, 'yuque');
  assert.equal(parsed.successCount, 3);
});

test('干净 stdout：compact 单行结果（wps separators=(",",":")）', () => {
  const stdout = `${compactPrint({ provider: 'wps', successCount: 7 })}\n`;
  const parsed = parseLastJson(stdout);
  assert.equal(parsed.provider, 'wps');
  assert.equal(parsed.successCount, 7);
});

test('普通日志行 + compact 单行结果（obsidian/wps 形态）', () => {
  const stdout = `progress 12/12 exported=12 failures=0\n${compactPrint({ provider: 'obsidian', successCount: 12 })}\n`;
  const parsed = parseLastJson(stdout);
  assert.equal(parsed.provider, 'obsidian');
  assert.equal(parsed.successCount, 12);
});

test('普通日志行 + 中文 pretty-print 结果（ensure_ascii=False）', () => {
  const stdout = `已登录印象笔记账号。\n${prettyPrint({ provider: 'yinxiang', 备注: '导出完成', failures: [] })}\n`;
  const parsed = parseLastJson(stdout);
  assert.equal(parsed.provider, 'yinxiang');
  assert.equal(parsed['备注'], '导出完成');
});

test('普通日志行 + 顶层数组结果（pretty-print）', () => {
  const stdout = `扫描目录树…\n${prettyPrint([{ id: 'a', children: [] }, { id: 'b', children: [] }])}\n`;
  const parsed = parseLastJson(stdout);
  assert.ok(Array.isArray(parsed));
  assert.equal(parsed.length, 2);
  assert.equal(parsed[1].id, 'b');
});

test('普通日志行 + 顶层数组结果（compact 单行）', () => {
  const stdout = `扫描目录树…\n${compactPrint([{ id: 'a' }, { id: 'b' }])}\n`;
  const parsed = parseLastJson(stdout);
  assert.ok(Array.isArray(parsed));
  assert.equal(parsed.length, 2);
});

test('结构化日志行穿插时先被过滤，结果照常解析', () => {
  const stdout = [
    `${STRUCTURED_LOG_PREFIX}{"event":"log.message","message":"开始导出"}`,
    '正在处理…',
    `${STRUCTURED_LOG_PREFIX}{"event":"progress","current":1,"total":2}`,
    prettyPrint({ provider: 'feishu', successCount: 2 }),
    ''
  ].join('\n');
  const parsed = parseLastJson(stdout);
  assert.equal(parsed.provider, 'feishu');
  assert.equal(parsed.successCount, 2);
});

test('CRLF 行尾 + 普通日志行不影响解析', () => {
  const stdout = `开始导出…\r\n${prettyPrint({ provider: 'wiz', successCount: 1 }).replace(/\n/g, '\r\n')}\r\n`;
  const parsed = parseLastJson(stdout);
  assert.equal(parsed.provider, 'wiz');
  assert.equal(parsed.successCount, 1);
});

test('结果后带多余空行仍可解析', () => {
  const stdout = `导出中…\n${prettyPrint({ provider: 'ima', successCount: 5 })}\n\n\n`;
  const parsed = parseLastJson(stdout);
  assert.equal(parsed.provider, 'ima');
  assert.equal(parsed.successCount, 5);
});

test('日志里先 dump 过一份 pretty JSON 时，取最后一个结果', () => {
  const stdout = [
    '本次任务摘要：',
    prettyPrint({ summary: true, provider: 'aliyun_thoughts', successCount: 0 }),
    prettyPrint({ summary: false, provider: 'aliyun_thoughts', successCount: 9 }),
    ''
  ].join('\n');
  const parsed = parseLastJson(stdout);
  assert.equal(parsed.summary, false);
  assert.equal(parsed.successCount, 9);
});

test('TaskResult v1 经 parseProcessResult 正常归一化', () => {
  const stdout = `准备导出…\n${prettyPrint({ kind: 'wandao.result', schemaVersion: 1, totalDocs: 4 })}\n`;
  const result = parseProcessResult(stdout);
  assert.equal(result.ok, true);
  assert.equal(result.legacy, false);
  assert.equal(result.data.totalDocs, 4);
});

test('完全没有 JSON 时返回 null', () => {
  assert.equal(parseLastJson('任务已完成，但没有结果输出。\n第二行普通日志\n'), null);
});

test('空 stdout / 仅结构化日志返回 null', () => {
  assert.equal(parseLastJson(''), null);
  assert.equal(parseLastJson('   \n\n'), null);
  assert.equal(parseLastJson(`${STRUCTURED_LOG_PREFIX}{"event":"log.message"}\n`), null);
});
