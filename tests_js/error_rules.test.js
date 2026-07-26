const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

const repoRoot = path.resolve(__dirname, '..');
const appSource = fs.readFileSync(path.join(repoRoot, 'wandao_electron', 'renderer', 'app.js'), 'utf8');

function sourceBetween(start, end) {
  const startIndex = appSource.indexOf(start);
  assert.notEqual(startIndex, -1, `missing source marker: ${start}`);
  const endIndex = appSource.indexOf(end, startIndex);
  assert.notEqual(endIndex, -1, `missing source marker: ${end}`);
  return appSource.slice(startIndex, endIndex);
}

// app.js 是渲染进程脚本、不是 CommonJS 模块。沿用 tests_js/guide_markdown.test.js 的做法：
// 按内容切出错误分类那几段源码，放进 vm 里执行后取出函数，不为了可测性改动 app.js 的结构。
const errorSource = [
  sourceBetween('const ERROR_RULES = [', '\nfunction applyTheme(theme) {'),
  sourceBetween('function normalizeLogMessage(message) {', '\nfunction trimLogStore('),
  sourceBetween('function compactLogSummary(message, maxLength = 220) {', '\nfunction looksLikeStructuredDump('),
  sourceBetween('function classifyError(message) {', '\nfunction log(message, type = '),
  'globalThis.__errorRulesApi = { ERROR_RULES, classifyError, formatUserError };'
].join('\n');

const context = {};
vm.runInNewContext(errorSource, context);
const { ERROR_RULES, classifyError, formatUserError } = context.__errorRulesApi;

const categoryOf = (message) => classifyError(message).category;
const FALLBACK = '未知错误';

test('B1 网络连接被拒绝/重置/中断的错误落到“网络连接失败”', () => {
  const samples = [
    "requests.exceptions.ConnectionError: HTTPSConnectionPool(host='open.feishu.cn', port=443): Max retries exceeded with url: /open-apis/drive/v1/files (Caused by NewConnectionError('Failed to establish a new connection: [Errno 111] Connection refused'))",
    'Error: connect ECONNREFUSED 220.181.38.148:443',
    'Error: read ECONNRESET',
    'urllib3.exceptions.ProtocolError: (\'Connection aborted.\', RemoteDisconnected(\'Remote end closed connection without response\'))',
    'OSError: [Errno 113] EHOSTUNREACH',
    'ConnectionResetError: [WinError 10054] 远程主机强迫关闭了一个现有的连接。'
  ];
  for (const sample of samples) {
    assert.equal(categoryOf(sample), '网络连接失败', sample.slice(0, 60));
  }
});

test('B1 超时类错误（含 Playwright Timeout 30000ms exceeded）落到“网络超时”', () => {
  const samples = [
    'Error: connect ETIMEDOUT 104.16.0.1:443',
    'requests.exceptions.ReadTimeout: HTTPSConnectionPool(host=\'www.yuque.com\', port=443): Read timed out. (read timeout=30)',
    'requests.exceptions.ConnectTimeout: Connection to api.example.com timed out',
    'TimeoutError: page.waitForSelector: Timeout 30000ms exceeded.\nCall log: waiting for selector "#main"',
    'locator.click: Timeout 15000ms exceeded.',
    '接口请求超时，请稍后重试'
  ];
  for (const sample of samples) {
    assert.equal(categoryOf(sample), '网络超时', sample.slice(0, 60));
  }
});

test('B1 域名解析失败落到“DNS 解析失败”', () => {
  const samples = [
    'Error: getaddrinfo ENOTFOUND www.yuque.com',
    'socket.gaierror: [Errno -3] EAI_AGAIN Temporary failure in name resolution',
    'socket.gaierror: [Errno -2] Name or service not known',
    'urllib3.exceptions.NameResolutionError: Failed to resolve \'open.feishu.cn\'',
    '域名解析失败，请检查网络'
  ];
  for (const sample of samples) {
    assert.equal(categoryOf(sample), 'DNS 解析失败', sample.slice(0, 60));
  }
});

test('B1 证书与代理问题落到“HTTPS 证书或代理问题”', () => {
  const samples = [
    'ssl.SSLCertVerificationError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate (_ssl.c:1000)',
    'requests.exceptions.SSLError: HTTPSConnectionPool(host=\'example.com\', port=443)',
    'Error: unable to verify the first certificate / UNABLE_TO_VERIFY_LEAF_SIGNATURE',
    'requests.exceptions.ProxyError: Cannot connect to proxy.',
    'net::ERR_PROXY_CONNECTION_FAILED',
    'HTTP 407 Proxy Authentication Required'
  ];
  for (const sample of samples) {
    assert.equal(categoryOf(sample), 'HTTPS 证书或代理问题', sample.slice(0, 60));
  }
});

test('B1 网络类错误不再落到兜底“未知错误”', () => {
  const samples = [
    'ECONNREFUSED',
    'ETIMEDOUT',
    'ENOTFOUND',
    'getaddrinfo failed',
    'SSLError',
    'ProxyError',
    'Timeout 30000ms exceeded'
  ];
  for (const sample of samples) {
    assert.notEqual(categoryOf(sample), FALLBACK, sample);
  }
});

// 反向断言：新增网络规则不能抢走原本已经分对的错误。
test('B1 浏览器调试端口连不上仍然是“浏览器自动化启动失败”，不是网络问题', () => {
  const debugPortFailure = [
    '无法连接浏览器调试端口 9222。',
    '[Errno 111] Connection refused',
    '请到“设置 > 自动化浏览器”重新检测并选择 Chrome、Edge 或 Chromium。',
    '如果浏览器已经打开，请关闭后重试，避免旧进程占用调试端口。'
  ].join('\n');
  assert.equal(categoryOf(debugPortFailure), '浏览器自动化启动失败');
  assert.equal(categoryOf('Error: connect ECONNREFUSED 127.0.0.1:9222'), '浏览器自动化启动失败');
});

test('B1 图片下载超时仍然是“图片或附件下载失败”，登录/限流分类也不受影响', () => {
  assert.equal(
    categoryOf('图片下载失败：https://cdn.nlark.com/yuque/0/2024/png/a.png：Read timed out'),
    '图片或附件下载失败'
  );
  assert.equal(categoryOf('登录凭证已失效，请重新登录'), '未登录或登录失效');
  assert.equal(categoryOf('HTTP 429 Too Many Requests'), '请求过快或平台限流');
  assert.equal(categoryOf('Access denied: HTTP 403'), '没有访问权限');
});

test('B2 平台 404 / 远端内容不存在不再被当成本地路径问题', () => {
  const samples = [
    'GET https://open.feishu.cn/open-apis/wiki/v2/spaces/x 失败：HTTP 404 Not Found',
    '{"code":404,"msg":"not found"}',
    'WPSApiError: status=404',
    '选择的 OneNote 页面不存在或目录已变化，请重新读取目录：a, b',
    '目标帖子不存在、已删除，或接口返回的帖子 ID 不可用。',
    '语雀返回：文档已删除',
    'invalid node_token',
    '无效的知识库链接，请重新复制'
  ];
  for (const sample of samples) {
    assert.equal(categoryOf(sample), '远端内容不存在', sample.slice(0, 60));
    assert.notEqual(categoryOf(sample), '本地文件路径问题', sample.slice(0, 60));
  }
});

test('B2 “远端内容不存在”提示的是链接/权限方向，不再让用户去查输入输出目录', () => {
  const rule = classifyError('HTTP 404 Not Found');
  assert.doesNotMatch(rule.suggestion, /输入目录|输出目录|脚本文件/);
  assert.match(rule.suggestion, /链接|浏览器/);
});

// 反向断言：收紧后原本命中“本地文件路径问题”的真实报错必须继续命中。
test('B2 真正的本地路径错误仍然落到“本地文件路径问题”', () => {
  const samples = [
    "FileNotFoundError: [Errno 2] No such file or directory: 'D:\\\\notes\\\\a.md'",
    "ENOENT: no such file or directory, open 'C:\\\\tmp\\\\a.md'",
    "python: can't open file 'export_yuque.py': [Errno 2] No such file or directory",
    'Markdown 目录不存在：D:\\notes',
    '测试 Markdown 文件不存在：D:\\notes\\a.md',
    'ValueError: Markdown 来源目录不存在或不是目录：D:\\notes',
    '系统找不到指定的文件。',
    '无法找到内置插件：feishu',
    '无法找到插件脚本：export_feishu.py',
    'Vault directory not found: /vault/notes',
    'ValueError: File not found: 20240101',
    'Source file not found',
    "PermissionError: [Errno 13] EACCES: permission denied, open 'out.md'",
    'EISDIR: illegal operation on a directory'
  ];
  for (const sample of samples) {
    assert.equal(categoryOf(sample), '本地文件路径问题', sample.slice(0, 60));
  }
});

// 收紧裸 not found 之后，这些本来就被第一条规则错误吞掉的报错回到各自的规则。
test('B2 裸 not found 不再把浏览器/页面结构问题吞成本地路径问题', () => {
  assert.equal(
    categoryOf('Chrome/Edge executable was not found. Install Chrome/Edge, add it to PATH'),
    '浏览器自动化启动失败'
  );
  assert.equal(categoryOf('element not found: .toc-item'), '页面结构变化');
});

test('B2 “远端内容不存在”排在图片规则之后，图片自身 404 仍归图片规则', () => {
  const rules = ERROR_RULES.map((rule) => rule.category);
  assert.ok(rules.indexOf('远端内容不存在') > rules.indexOf('图片或附件下载失败'));
  assert.ok(rules.indexOf('远端内容不存在') < rules.indexOf('没有访问权限'));
  assert.equal(
    categoryOf('图片下载失败：https://cdn.nlark.com/yuque/0/2024/png/a.png：HTTP 404'),
    '图片或附件下载失败'
  );
});
