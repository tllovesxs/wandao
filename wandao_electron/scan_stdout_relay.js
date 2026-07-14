function isTerminalJsonObjectStart(line) {
  const text = String(line || '').replace(/\r?\n$/, '');
  return /^\s*\{(?:\s*$|\s*")/.test(text);
}

function createScanStdoutRelay(send) {
  let pending = '';
  let candidate = '';

  function forwardCompleteLines() {
    while (!candidate) {
      const newlineIndex = pending.indexOf('\n');
      if (newlineIndex < 0) return;
      const line = pending.slice(0, newlineIndex + 1);
      pending = pending.slice(newlineIndex + 1);
      if (isTerminalJsonObjectStart(line)) {
        // A scan result is normally the final JSON object, but an arbitrary
        // provider log can also begin with "{". Keep a candidate until the
        // process closes so a JSON-looking log never hides later diagnostics.
        candidate = line + pending;
        pending = '';
        return;
      }
      send(line);
    }
  }

  return {
    push(chunk) {
      if (candidate) {
        candidate += String(chunk || '');
        return;
      }
      pending += String(chunk || '');
      forwardCompleteLines();
    },
    flush() {
      if (candidate) {
        try {
          const parsed = JSON.parse(candidate.trim());
          if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) return;
        } catch (_) {
          // Not the terminal result: preserve it as an ordinary renderer log.
        }
        send(candidate);
        candidate = '';
        return;
      }
      if (!pending) return;
      send(pending);
      pending = '';
    }
  };
}

module.exports = {
  createScanStdoutRelay
};
