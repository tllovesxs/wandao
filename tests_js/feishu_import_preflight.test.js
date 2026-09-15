const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');

const appJs = fs.readFileSync('wandao_electron/renderer/app.js', 'utf8');

test('Feishu API write actions are guarded before confirmation and task startup', () => {
  assert.match(appJs, /function feishuActionRequiresApiCredentials\(args\)/);
  assert.match(appJs, /async function ensureFeishuApiCredentials\(\)/);
  assert.match(appJs, /缺少 \$\{missing\.join\('、'\)\}/);
  assert.match(appJs, /feishuActionRequiresApiCredentials\(args\) && !\(await ensureFeishuApiCredentials\(\)\)/);

  const batchHandler = appJs.slice(
    appJs.indexOf("document.getElementById('feishu-import-all')"),
    appJs.indexOf("document.getElementById('feishu-import-stop')")
  );
  assert.ok(batchHandler.indexOf('ensureFeishuApiCredentials') < batchHandler.indexOf('confirmFeishuImportWrite'));
});

test('Feishu batch backend guards credentials and empty source directories', () => {
  const backend = fs.readFileSync('plugins/feishu/backend/import_feishu.py', 'utf8');
  assert.match(backend, /def require_feishu_api_credentials\(args: argparse\.Namespace\)/);
  assert.match(backend, /飞书 API 配置不完整，无法开始导入/);
  assert.match(backend, /所选目录中没有找到 Markdown 文件/);
  const batchStart = backend.indexOf('def import_all_with_openapi(args: argparse.Namespace)');
  const credentialGuard = backend.indexOf('require_feishu_api_credentials(args)', batchStart);
  const networkProbe = backend.indexOf('host, _origin, target_wiki_token', batchStart);
  assert.ok(batchStart >= 0 && credentialGuard >= 0 && credentialGuard < networkProbe);
});
