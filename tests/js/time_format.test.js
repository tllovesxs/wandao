const assert = require('node:assert/strict');
const test = require('node:test');
const { formatLocalDateTime, isTimestamp } = require('../../wandao_electron/renderer/time_format');

test('formats UTC timestamps in the selected user time zone', () => {
  assert.equal(
    formatLocalDateTime('2026-07-11T16:31:19.852Z', { timeZone: 'Asia/Shanghai' }),
    '2026-07-12 00:31:19（Asia/Shanghai，UTC+08:00）'
  );
});

test('uses the offset active during daylight saving time', () => {
  assert.equal(
    formatLocalDateTime('2026-07-11T16:31:19.852Z', { timeZone: 'America/New_York' }),
    '2026-07-11 12:31:19（America/New_York，UTC-04:00）'
  );
});

test('distinguishes timestamps from date-only publication dates', () => {
  assert.equal(isTimestamp('2026-07-11T16:31:19.852Z'), true);
  assert.equal(isTimestamp('2026-07-11'), false);
});
