function isTerminalJsonObjectStart(line) {
  const text = String(line || '').replace(/\r?\n$/, '');
  return /^\s*\{(?:\s*$|\s*")/.test(text);
}

function createScanStdoutRelay(send) {
  let pending = '';
  let resultStarted = false;

  function forwardCompleteLines() {
    while (!resultStarted) {
      const newlineIndex = pending.indexOf('\n');
      if (newlineIndex < 0) return;
      const line = pending.slice(0, newlineIndex + 1);
      pending = pending.slice(newlineIndex + 1);
      if (isTerminalJsonObjectStart(line)) {
        resultStarted = true;
        pending = '';
        return;
      }
      send(line);
    }
  }

  return {
    push(chunk) {
      if (resultStarted) return;
      pending += String(chunk || '');
      forwardCompleteLines();
    },
    flush() {
      if (resultStarted || !pending) return;
      if (isTerminalJsonObjectStart(pending)) {
        resultStarted = true;
        pending = '';
        return;
      }
      send(pending);
      pending = '';
    }
  };
}

module.exports = {
  createScanStdoutRelay
};
