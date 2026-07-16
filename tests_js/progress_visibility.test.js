const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');

const appJs = fs.readFileSync('wandao_electron/renderer/app.js', 'utf8');

test('leaving a completed provider page hides its finished progress card without changing the progress UI', () => {
  assert.match(appJs, /function hideProgress\(\)[\s\S]*els\.section\.hidden = true;[\s\S]*progressVisible = false;/);
  const switchSource = appJs.slice(appJs.indexOf('function switchTool'), appJs.indexOf('// Load tool template'));
  assert.match(switchSource, /if \(targetTool !== currentTool && !isRunning\) hideProgress\(\);/);
  assert.match(switchSource, /if \(isRunning && targetTool !== currentTool\)[\s\S]*return false;/);
});
