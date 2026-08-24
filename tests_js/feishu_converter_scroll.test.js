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

test('collectFeishuDocBlocks treats height growth during settle as progress', async () => {
  const api = loadScrollApi();
  const { document, window } = parseHTML(`<!doctype html><html><body>
    <div class="page-scroller" data-overflow-y="auto" style="height:200px; overflow:auto">
      <div class="root-render-unit-container"><div class="render-unit-wrapper"></div></div>
    </div>
  </body></html>`);
  const scroller = document.querySelector('.page-scroller');
  const wrapper = document.querySelector('.render-unit-wrapper');
  Object.defineProperty(scroller, 'clientHeight', { configurable: true, get: () => 200 });
  let scrollHeight = 400;
  let scrollTop = 0;
  // Growth is delivered AFTER the settle sleep: the scroll setter only records
  // the position; a separate "settle pass" grows the doc. A collector that
  // samples height after sleep would miss this and stabilize too early.
  Object.defineProperty(scroller, 'scrollHeight', { configurable: true, get: () => scrollHeight });
  Object.defineProperty(scroller, 'scrollTop', {
    configurable: true,
    get: () => scrollTop,
    set: (value) => { scrollTop = value; },
  });
  const mount = (id, text) => {
    if ([...wrapper.children].some((el) => el.textContent === text)) return;
    const block = document.createElement('div');
    block.setAttribute('data-block-type', 'paragraph');
    block.setAttribute('data-block-id', id);
    block.textContent = text;
    wrapper.appendChild(block);
  };
  mount('block-1', 'block-1');
  mount('block-2', 'block-2');
  let settleRounds = 0;
  // The injected sleep is the "settle": on rounds 2 and 4 the document grows.
  const sleep = async () => {
    settleRounds += 1;
    if (settleRounds === 2 && scrollHeight === 400) {
      scrollHeight = 900;
      mount('block-3', 'block-3');
    }
    if (settleRounds === 4 && scrollHeight === 900) {
      scrollHeight = 1400;
      mount('block-4', 'block-4');
    }
  };
  const currentBlocks = () => [...wrapper.children].filter((el) => el.getAttribute('data-block-type'));
  const renderBlock = (el) => el.textContent || '';
  const result = await api.collectFeishuDocBlocks({
    root: document.querySelector('.root-render-unit-container'),
    scroller,
    document,
    sleep,
    currentBlocks,
    renderBlock,
    maxIterations: 40,
  });
  assert.deepEqual(result.rendered, ['block-1', 'block-2', 'block-3', 'block-4']);
  assert.equal(result.finalScrollHeight, 1400);
});

test('collectFeishuDocBlocks upgrades a partially mounted block in place', async () => {
  const api = loadScrollApi();
  const { document, window } = parseHTML(`<!doctype html><html><body>
    <div class="page-scroller" data-overflow-y="auto" style="height:200px; overflow:auto">
      <div class="root-render-unit-container"><div class="render-unit-wrapper"></div></div>
    </div>
  </body></html>`);
  const scroller = document.querySelector('.page-scroller');
  const wrapper = document.querySelector('.render-unit-wrapper');
  Object.defineProperty(scroller, 'clientHeight', { configurable: true, get: () => 200 });
  let scrollHeight = 400;
  let scrollTop = 0;
  Object.defineProperty(scroller, 'scrollHeight', { configurable: true, get: () => scrollHeight });
  Object.defineProperty(scroller, 'scrollTop', {
    configurable: true,
    get: () => scrollTop,
    set: (value) => { scrollTop = value; },
  });
  const block1 = document.createElement('div');
  block1.setAttribute('data-block-type', 'paragraph');
  block1.setAttribute('data-block-id', 'block-1');
  block1.textContent = 'partial text'; // mounted before full .ace-line text arrives
  wrapper.appendChild(block1);
  let settleRounds = 0;
  const sleep = async () => {
    settleRounds += 1;
    if (settleRounds === 1) block1.textContent = 'partial text plus the rest of the sentence https://example.com/x';
  };
  const currentBlocks = () => [...wrapper.children].filter((el) => el.getAttribute('data-block-type'));
  const renderBlock = (el) => el.textContent || '';
  const result = await api.collectFeishuDocBlocks({
    root: document.querySelector('.root-render-unit-container'),
    scroller,
    document,
    sleep,
    currentBlocks,
    renderBlock,
    maxIterations: 20,
  });
  assert.deepEqual(result.rendered, ['partial text plus the rest of the sentence https://example.com/x']);
});

test('resolveFeishuDocScroller skips scrollbar chrome that cannot scroll', () => {
  const api = loadScrollApi();
  const { document, window } = parseHTML(`<!doctype html><html><body>
    <div class="page-scroller" data-overflow-y="auto" style="height:200px; overflow:auto">
      <div class="scrollbar-container" data-overflow-y="auto" style="height:200px">
        <div class="root-render-unit-container">
          <div class="render-unit-wrapper">
            <div data-block-type="paragraph" data-block-id="a">hello</div>
          </div>
        </div>
      </div>
    </div>
  </body></html>`);
  const scroller = document.querySelector('.page-scroller');
  const chrome = document.querySelector('.scrollbar-container');
  const root = document.querySelector('.root-render-unit-container');
  let chromeHeight = 200;
  Object.defineProperty(chrome, 'clientHeight', { configurable: true, get: () => 200 });
  Object.defineProperty(chrome, 'scrollHeight', { configurable: true, get: () => 200 });
  let chromeTop = 0;
  Object.defineProperty(chrome, 'scrollTop', {
    configurable: true,
    get: () => chromeTop,
    set: (value) => { chromeTop = value; },
  });
  Object.defineProperty(scroller, 'clientHeight', { configurable: true, get: () => 200 });
  let scrollerHeight = 600;
  Object.defineProperty(scroller, 'scrollHeight', { configurable: true, get: () => scrollerHeight });
  let scrollerTop = 0;
  Object.defineProperty(scroller, 'scrollTop', {
    configurable: true,
    get: () => scrollerTop,
    set: (value) => { scrollerTop = value; },
  });
  const resolved = api.resolveFeishuDocScroller(root, document);
  assert.equal(resolved, scroller);
});

test('collectFeishuDocBlocks mounts blocks via scrollIntoView nudging', async () => {
  const api = loadScrollApi();
  const { document, window } = parseHTML(`<!doctype html><html><body>
    <div class="root-render-unit-container">
      <div class="render-unit-wrapper"></div>
    </div>
  </body></html>`);
  const wrapper = document.querySelector('.render-unit-wrapper');
  const root = document.querySelector('.root-render-unit-container');
  // The collector no longer depends on a real scroller's scrollTop; a minimal
  // window-scroll fallback is enough as long as scrollIntoView mounts blocks.
  let nextId = 3;
  const mk = (id, text) => {
    const block = document.createElement('div');
    block.setAttribute('data-block-type', 'paragraph');
    block.setAttribute('data-block-id', id);
    block.textContent = text;
    block.scrollIntoView = () => {
      if (wrapper.childElementCount < 4) {
        const idNum = nextId++;
        mk(`block-${idNum}`, `block-${idNum}`);
      }
    };
    wrapper.appendChild(block);
  };
  mk('block-1', 'block-1');
  mk('block-2', 'block-2');
  const sleep = async () => {};
  const currentBlocks = () => [...wrapper.children].filter((el) => el.getAttribute('data-block-type'));
  const renderBlock = (el) => el.textContent || '';
  const result = await api.collectFeishuDocBlocks({
    root,
    scroller: document.documentElement,
    document,
    sleep,
    currentBlocks,
    renderBlock,
    maxIterations: 40,
  });
  assert.deepEqual(result.rendered, ['block-1', 'block-2', 'block-3', 'block-4']);
});

test('collectFeishuDocBlocks stops cleanly at bottom without false incomplete', async () => {
  const api = loadScrollApi();
  const { document, window } = parseHTML(`<!doctype html><html><body>
    <div class="page-scroller" data-overflow-y="auto" style="height:200px; overflow:auto">
      <div class="root-render-unit-container"><div class="render-unit-wrapper"></div></div>
    </div>
  </body></html>`);
  const scroller = document.querySelector('.page-scroller');
  const wrapper = document.querySelector('.render-unit-wrapper');
  Object.defineProperty(scroller, 'clientHeight', { configurable: true, get: () => 200 });
  let scrollHeight = 500;
  let scrollTop = 0;
  Object.defineProperty(scroller, 'scrollHeight', { configurable: true, get: () => scrollHeight });
  Object.defineProperty(scroller, 'scrollTop', {
    configurable: true,
    get: () => scrollTop,
    set: (value) => {
      scrollTop = Math.min(Math.max(value, 0), scrollHeight - 200);
      // Mount all blocks on first reach of bottom; after that, nothing grows.
      if (scrollTop >= scrollHeight - 200 && wrapper.childElementCount < 4) {
        scrollHeight = 1400;
        for (const id of ['block-3', 'block-4']) {
          const block = document.createElement('div');
          block.setAttribute('data-block-type', 'paragraph');
          block.setAttribute('data-block-id', id);
          block.textContent = id;
          wrapper.appendChild(block);
        }
      }
    },
  });
  for (const id of ['block-1', 'block-2']) {
    const block = document.createElement('div');
    block.setAttribute('data-block-type', 'paragraph');
    block.setAttribute('data-block-id', id);
    block.textContent = id;
    wrapper.appendChild(block);
  }
  const sleep = async () => {};
  const currentBlocks = () => [...wrapper.children].filter((el) => el.getAttribute('data-block-type'));
  const renderBlock = (el) => el.textContent || '';
  const result = await api.collectFeishuDocBlocks({
    root: document.querySelector('.root-render-unit-container'),
    scroller,
    document,
    sleep,
    currentBlocks,
    renderBlock,
    maxIterations: 180,
  });
  assert.deepEqual(result.rendered, ['block-1', 'block-2', 'block-3', 'block-4']);
  assert.equal(result.reachedBottom, true);
  assert.equal(result.hitIterationCeiling, false);
  assert.ok(result.scrollIterations < 40, `should stop near bottom, got ${result.scrollIterations}`);
});
