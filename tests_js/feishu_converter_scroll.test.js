const assert = require('node:assert/strict');
const fs = require('node:fs');
const { createRequire } = require('node:module');
const path = require('node:path');
const test = require('node:test');

const repoRoot = path.resolve(__dirname, '..');
const exporterPath = path.join(repoRoot, 'plugins', 'feishu', 'backend', 'export_feishu.py');
const desktopRequire = createRequire(path.join(repoRoot, 'wandao_electron', 'package.json'));
const { parseHTML } = desktopRequire('linkedom');

function loadScrollApi() {
  const source = fs.readFileSync(exporterPath, 'utf8');
  const match = source.match(/FEISHU_CONVERTER_JS = r?"""([\s\S]*?)"""/);
  assert.ok(match, 'FEISHU_CONVERTER_JS was not found');
  const start = match[1].indexOf('/* feishu-scroll-api:start */');
  const end = match[1].indexOf('/* feishu-scroll-api:end */');
  assert.ok(start >= 0 && end > start, 'feishu scroll api markers were not found');
  const helperSource = match[1].slice(start, end + '/* feishu-scroll-api:end */'.length);
  const { document, window } = parseHTML('<!doctype html><html><body></body></html>');
  global.document = document;
  global.window = window;
  global.getComputedStyle = (el) => {
    const overflowY = el.getAttribute('data-overflow-y') || 'visible';
    return { overflowY };
  };
  delete globalThis.__feishuConverterScrollApi;
  const runner = Function(
    'document',
    'window',
    'getComputedStyle',
    'clean',
    `"use strict";\n${helperSource}\nreturn globalThis.__feishuConverterScrollApi;`
  );
  const api = runner(document, window, getComputedStyle, (value) => String(value || '').trim());
  assert.ok(api, 'scroll api was not exported');
  return api;
}

function makeLazyDoc() {
  const { document, window } = parseHTML(`<!doctype html><html><body>
    <div class="shell" data-overflow-y="visible" style="height:200px">
      <div class="page-scroller" data-overflow-y="auto" style="height:200px; overflow:auto">
        <div class="root-render-unit-container">
          <div class="render-unit-wrapper"></div>
        </div>
      </div>
    </div>
  </body></html>`);
  const scroller = document.querySelector('.page-scroller');
  const wrapper = document.querySelector('.render-unit-wrapper');
  // Fake geometry because linkedom does not implement layout.
  Object.defineProperty(scroller, 'clientHeight', { configurable: true, get: () => 200 });
  let scrollHeight = 400;
  let scrollTop = 0;
  let bottomContacts = 0;
  Object.defineProperty(scroller, 'scrollHeight', { configurable: true, get: () => scrollHeight });
  Object.defineProperty(scroller, 'scrollTop', {
    configurable: true,
    get: () => scrollTop,
    set: (value) => {
      scrollTop = value;
      // Defer lazy growth until the iteration after first bottom contact so the
      // old double-count termination exits before blocks 3/4 appear.
      if (scrollTop >= scrollHeight - 200 && wrapper.childElementCount < 4) {
        bottomContacts += 1;
        if (bottomContacts < 2) return;
        scrollHeight = 900;
        for (const [type, text] of [
          ['paragraph', 'block-3'],
          ['paragraph', 'block-4'],
        ]) {
          if ([...wrapper.children].some((el) => el.textContent === text)) continue;
          const block = document.createElement('div');
          block.setAttribute('data-block-type', type);
          block.setAttribute('data-block-id', text);
          block.textContent = text;
          wrapper.appendChild(block);
        }
      }
    },
  });
  // Seed the first viewport blocks.
  for (const [id, text] of [
    ['block-1', 'block-1'],
    ['block-2', 'block-2'],
  ]) {
    const block = document.createElement('div');
    block.setAttribute('data-block-type', 'paragraph');
    block.setAttribute('data-block-id', id);
    block.textContent = text;
    wrapper.appendChild(block);
  }
  Object.defineProperty(window, 'innerHeight', { configurable: true, get: () => 200 });
  Object.defineProperty(document.documentElement, 'clientHeight', { configurable: true, get: () => 200 });
  Object.defineProperty(document.documentElement, 'scrollHeight', { configurable: true, get: () => 200 });
  return { document, window, scroller, root: document.querySelector('.root-render-unit-container'), wrapper };
}

test('resolveFeishuDocScroller prefers the nested page scroller over window', () => {
  const api = loadScrollApi();
  const { document, root, scroller } = makeLazyDoc();
  const resolved = api.resolveFeishuDocScroller(root, document);
  assert.equal(resolved, scroller);
});

test('collectFeishuDocBlocks keeps scrolling after lazy scrollHeight growth', async () => {
  const api = loadScrollApi();
  const { document, root, scroller, wrapper } = makeLazyDoc();
  const sleep = async () => {};
  const currentBlocks = () => [...wrapper.children].filter((el) => el.getAttribute('data-block-type'));
  const renderBlock = (el) => el.textContent || '';
  const result = await api.collectFeishuDocBlocks({
    root,
    scroller,
    document,
    sleep,
    currentBlocks,
    renderBlock,
    maxIterations: 120,
  });
  assert.deepEqual(result.rendered, ['block-1', 'block-2', 'block-3', 'block-4']);
  assert.equal(result.blockCount, 4);
});
