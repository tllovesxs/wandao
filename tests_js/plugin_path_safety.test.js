const test = require('node:test');
const assert = require('node:assert/strict');
const { assertSafeRelativePath } = require('../wandao_electron/plugin_format');

// W4：段内冒号会被 NTFS 当作备用数据流（a.py:evil），保留设备名与结尾点/空格
// 在 Windows 上落盘会被静默改名，导致签名清单里的路径与实际文件不一致。

test('段内冒号被拒绝，避免 NTFS 备用数据流', () => {
  assert.throws(() => assertSafeRelativePath('docs/a:b.py'), /非法路径段/);
  assert.throws(() => assertSafeRelativePath('a.py:evil'), /非法路径段/);
  assert.throws(() => assertSafeRelativePath('backend/run.py:$DATA'), /非法路径段/);
});

test('Windows 保留设备名被拒绝', () => {
  assert.throws(() => assertSafeRelativePath('CON.py'), /非法路径段/);
  assert.throws(() => assertSafeRelativePath('a/nul.json'), /非法路径段/);
  assert.throws(() => assertSafeRelativePath('COM1'), /非法路径段/);
  assert.throws(() => assertSafeRelativePath('backend/lpt9.txt'), /非法路径段/);
  assert.throws(() => assertSafeRelativePath('PRN/run.py'), /非法路径段/);
  assert.throws(() => assertSafeRelativePath('aux.tar.gz'), /非法路径段/);
});

test('结尾点或空格的路径段被拒绝', () => {
  assert.throws(() => assertSafeRelativePath('docs/run.py '), /非法路径段/);
  assert.throws(() => assertSafeRelativePath('docs/run.'), /非法路径段/);
  assert.throws(() => assertSafeRelativePath('trailing. /run.py'), /非法路径段/);
  assert.throws(() => assertSafeRelativePath('leading-ok-but-trailing '), /非法路径段/);
});

test('Windows 其余非法字符与控制字符被拒绝', () => {
  for (const bad of ['a<b.py', 'a>b.py', 'a"b.py', 'a|b.py', 'a?b.py', 'a*b.py']) {
    assert.throws(() => assertSafeRelativePath(bad), /非法路径段/, `应拒绝 ${bad}`);
  }
  assert.throws(() => assertSafeRelativePath('a\u0000b.py'), /非法路径段/);
  assert.throws(() => assertSafeRelativePath('a\nb.py'), /非法路径段/);
});

test('原有的反斜杠、绝对路径、.. 拦截保持不变', () => {
  assert.throws(() => assertSafeRelativePath('a\\b.py'), /POSIX 相对路径/);
  assert.throws(() => assertSafeRelativePath('/etc/passwd'), /POSIX 相对路径/);
  assert.throws(() => assertSafeRelativePath('C:/Windows/system32'), /POSIX 相对路径/);
  assert.throws(() => assertSafeRelativePath(''), /POSIX 相对路径/);
  assert.throws(() => assertSafeRelativePath('../escape.py'), /\. 或 \.\./);
  assert.throws(() => assertSafeRelativePath('a//b.py'), /空目录/);
  assert.throws(() => assertSafeRelativePath('./a.py'), /\. 或 \.\./);
});

// 反向断言：加固不能误伤正常路径。
test('正常路径依然通过', () => {
  const ok = [
    'backend/export.py',
    'providers/x/provider.json',
    'providers/demo/provider.json',
    'plugin.json',
    'ui/index.html',
    'backend/run.py',
    'docs/tutorial-2024.md',
    'assets/logo@2x.png',
    'a/b/c/d/e.txt',
    'con-fig.json',
    'console.js',
    'nullable.py',
    'com10.txt',
    'lpt0.txt',
    'CONTRIBUTING.md',
    'auxiliary/prn_helper.py',
    'docs/说明文档.md',
    '插件/后端/导出.py',
    'docs/使用教程 v1.md',
    "a'b.py",
    'a+b(1).py',
    'a,b;c.py',
    'a=b&c.py',
    'a%20b.py',
    'a#b.py',
    'a!b.py',
    'a$b.py',
    'a[1].py',
    'a{1}.py',
    '.gitignore',
    '..hidden/x.py'
  ];
  for (const value of ok) {
    assert.equal(assertSafeRelativePath(value), value, `不应拒绝 ${value}`);
  }
});

test('自定义 label 出现在错误信息里', () => {
  assert.throws(() => assertSafeRelativePath('CON.py', 'Provider 入口'), /Provider 入口/);
});
