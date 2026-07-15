const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');

const appJs = fs.readFileSync('wandao_electron/renderer/app.js', 'utf8');
const styles = fs.readFileSync('wandao_electron/renderer/styles.css', 'utf8');

test('manifest fields expose native constraints and an adjacent accessible error target', () => {
  const renderer = appJs.slice(appJs.indexOf('function manifestFieldErrorId'), appJs.indexOf('function renderManifestProviderForm'));

  assert.match(renderer, /function manifestFieldInputAttributes\(provider, field, options = \{\}\)/);
  assert.match(renderer, /'required', 'aria-required="true"'/);
  assert.match(renderer, /field\.max !== undefined/);
  assert.match(renderer, /class="form-field-error"[^>]*role="alert" hidden/);
  assert.match(renderer, /aria-describedby="\$\{manifestFieldErrorId\(provider, field\)\}"/);
});

test('manifest action validation marks, announces, focuses, and clears field errors', () => {
  const validation = appJs.slice(appJs.indexOf('function manifestFieldValidationMessage'), appJs.indexOf('function manifestActionKey'));
  const builder = appJs.slice(appJs.indexOf('function buildManifestActionArgs'), appJs.indexOf('function applyActionUpdates'));
  const handlers = appJs.slice(appJs.indexOf('function initializeManifestProviderHandlers'), appJs.indexOf('async function renderCustomPluginProvider'));

  assert.match(validation, /field\.type !== 'number' \|\| value === ''/);
  assert.match(validation, /不能小于 \$\{field\.min\}/);
  assert.match(validation, /不能大于 \$\{field\.max\}/);
  assert.match(validation, /input\.setAttribute\('aria-invalid', 'true'\)/);
  assert.match(validation, /input\.closest\('details\.advanced-section'\)\?\.setAttribute\('open', ''\)/);
  assert.match(validation, /input\.focus\(\)/);
  assert.match(builder, /throw manifestFieldValidationError\(field, validationMessage\)/);
  assert.match(builder, /clearManifestFieldError\(provider, field\)/);
  assert.match(handlers, /input\?\.addEventListener\('input', \(\) => clearManifestFieldErrorIfValid\(provider, field\)\)/);
  assert.match(handlers, /input\?\.addEventListener\('change', \(\) => clearManifestFieldErrorIfValid\(provider, field\)\)/);
  assert.match(handlers, /if \(error\?\.manifestField\) \{[\s\S]*showManifestFieldError\(provider, error\.manifestField, formatError\(error\)\)/);
  const buildIndex = handlers.indexOf('args = buildManifestActionArgs(provider, action, fields);');
  const actionConfirmIndex = handlers.indexOf("if (action.confirm && !confirm(action.confirm)) return;", buildIndex);
  const providerConfirmIndex = handlers.indexOf('if (!confirmProviderExecution(provider, action)) return;', buildIndex);
  assert.ok(buildIndex >= 0 && actionConfirmIndex > buildIndex);
  assert.ok(providerConfirmIndex > actionConfirmIndex);
  assert.match(styles, /\.manifest-tool-panel \.form-group\.has-error/);
  assert.match(styles, /\.form-field-error/);
});
