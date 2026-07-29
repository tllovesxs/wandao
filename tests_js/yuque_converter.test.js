const assert = require('node:assert/strict');
const fs = require('node:fs');
const { createRequire } = require('node:module');
const path = require('node:path');
const test = require('node:test');

const repoRoot = path.resolve(__dirname, '..');
const converterPath = path.join(repoRoot, 'plugins', 'yuque', 'backend', 'export_yuque.py');
const desktopRequire = createRequire(path.join(repoRoot, 'wandao_electron', 'package.json'));
const { parseHTML } = desktopRequire('linkedom');

function runConverter(content, title = 'Converter regression') {
  const source = fs.readFileSync(converterPath, 'utf8');
  const match = source.match(/YUQUE_CONVERTER_JS = r?"""([\s\S]*?)"""/);
  assert.ok(match, 'YUQUE_CONVERTER_JS was not found');
  const document = {
    implementation: {
      createHTMLDocument: () => parseHTML('<!doctype html><html><body></body></html>').document
    }
  };
  const converter = Function('document', `"use strict"; return (${match[1]});`)(document);
  return converter(content, title);
}

test('Yuque converter collects table images and attachments through a standards-based DOM', () => {
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
