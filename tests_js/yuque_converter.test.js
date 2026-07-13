const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const test = require('node:test');

const repoRoot = path.resolve(__dirname, '..');
const converterPath = path.join(repoRoot, 'plugins', 'yuque', 'backend', 'export_yuque.py');
const electronPath = path.join(repoRoot, 'wandao_electron', 'node_modules', 'electron', 'dist', 'electron.exe');

function runConverter(content, title = 'Converter regression') {
  const runnerDir = fs.mkdtempSync(path.join(os.tmpdir(), 'wandao-yuque-converter-'));
  const runnerPath = path.join(runnerDir, 'runner.js');
  const resultPath = path.join(runnerDir, 'result.json');
  const runner = String.raw`
const fs = require('node:fs');
const { app, BrowserWindow } = require('electron');
const [converterPath, encodedInput, resultPath] = process.argv.slice(-3);
function finish(payload) {
  fs.writeFileSync(resultPath, JSON.stringify(payload), 'utf8');
  app.exit(payload.error ? 1 : 0);
}
const source = fs.readFileSync(converterPath, 'utf8');
const match = source.match(/YUQUE_CONVERTER_JS = r?"""([\s\S]*?)"""/);
if (!match) {
  finish({ error: 'YUQUE_CONVERTER_JS was not found' });
} else {
  const input = JSON.parse(Buffer.from(encodedInput, 'base64').toString('utf8'));
  app.commandLine.appendSwitch('disable-gpu');
  app.whenReady().then(async () => {
    let win;
    try {
      win = new BrowserWindow({ show: false, webPreferences: { contextIsolation: true, nodeIntegration: false } });
      await win.loadURL('data:text/html,<html><body></body></html>');
      const expression = '(' + match[1] + ')(' + JSON.stringify(input.content) + ', ' + JSON.stringify(input.title) + ')';
      finish({ result: await win.webContents.executeJavaScript(expression, true) });
    } catch (error) {
      finish({ error: error && error.stack ? error.stack : String(error) });
    } finally {
      if (win && !win.isDestroyed()) win.destroy();
    }
  });
}
`;
  fs.writeFileSync(runnerPath, runner, 'utf8');
  try {
    const input = Buffer.from(JSON.stringify({ content, title }), 'utf8').toString('base64');
    const result = spawnSync(electronPath, ['--no-sandbox', runnerPath, converterPath, input, resultPath], {
      cwd: repoRoot,
      encoding: 'utf8',
      timeout: 30000,
      windowsHide: true
    });
    const payload = fs.existsSync(resultPath) ? JSON.parse(fs.readFileSync(resultPath, 'utf8')) : null;
    assert.equal(result.status, 0, `Electron converter runner failed:\n${payload?.error || result.stderr || result.stdout}`);
    assert.ok(payload && payload.result, `Electron converter runner returned no result: ${result.stderr || result.stdout}`);
    return payload.result;
  } finally {
    fs.rmSync(runnerDir, { recursive: true, force: true });
  }
}

test('Yuque converter collects table images and attachments through its live Chromium DOM path', () => {
  const imageUrl = 'https://cdn.example.test/table.png';
  const attachmentUrl = 'https://files.example.test/guide.pdf';
  const cardValue = `data:${encodeURIComponent(JSON.stringify({ src: imageUrl, title: 'table image' }))}`;
  const content = [
    '<p><img src="https://cdn.example.test/paragraph.png" alt="paragraph image"></p>',
    '<table><tr>',
    `<td><p><card name="image" value="${cardValue}"></card></p></td>`,
    `<td><a href="${attachmentUrl}">guide.pdf</a></td>`,
    '<td>A | B</td>',
    '</tr></table>',
    `<p><card name="image" value="${cardValue}"></card></p>`
  ].join('');

  const result = runConverter(content);
  const tableImageResources = result.resources.filter((item) => item.url === imageUrl && item.kind === 'image');

  assert.match(result.markdown, new RegExp(`!\\[table image\\]\\(${imageUrl.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\)`));
  assert.equal(tableImageResources.length, 2);
  assert.ok(result.resources.some((item) => item.url === attachmentUrl && item.kind === 'attachment'));
  assert.ok(result.resources.some((item) => item.url === 'https://cdn.example.test/paragraph.png' && item.kind === 'image'));
  assert.match(result.markdown, /A \\| B/);
});

