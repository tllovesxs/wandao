const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const { readGuideImageDataUrl } = require('../wandao_electron/guide_assets');

function withFixture(run) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'wandao-guide-assets-'));
  const providerRoot = path.join(root, 'provider');
  const imageDir = path.join(providerRoot, 'images');
  fs.mkdirSync(imageDir, { recursive: true });
  fs.writeFileSync(path.join(imageDir, '1.png'), Buffer.from([0x89, 0x50, 0x4e, 0x47]));
  fs.writeFileSync(path.join(root, 'outside.png'), Buffer.from([0x89, 0x50, 0x4e, 0x47]));
  try {
    run({ root, providerRoot, imageDir });
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
}

test('reads an allowed provider guide image as a data URL', () => {
  withFixture(({ providerRoot }) => {
    const value = readGuideImageDataUrl(providerRoot, './images/1.png');
    assert.equal(value, 'data:image/png;base64,iVBORw==');
  });
});

test('rejects provider guide image path traversal and absolute paths', () => {
  withFixture(({ providerRoot, root }) => {
    assert.throws(() => readGuideImageDataUrl(providerRoot, '../outside.png'), /图片路径/);
    assert.throws(() => readGuideImageDataUrl(providerRoot, path.join(root, 'outside.png')), /图片路径/);
  });
});

test('rejects unsupported image types and oversized images', () => {
  withFixture(({ providerRoot, imageDir }) => {
    fs.writeFileSync(path.join(imageDir, 'note.svg'), '<svg></svg>');
    fs.writeFileSync(path.join(imageDir, 'large.png'), Buffer.alloc(8));
    assert.throws(() => readGuideImageDataUrl(providerRoot, './images/note.svg'), /图片格式/);
    assert.throws(() => readGuideImageDataUrl(providerRoot, './images/large.png', { maxBytes: 4 }), /图片大小/);
  });
});
