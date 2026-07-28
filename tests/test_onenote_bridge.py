"""Regression tests for the OneNote C# bridge plumbing.

The bridge itself needs Windows + the OneNote COM API, so these tests only
cover the parts that are verifiable off-Windows: the generated C# source text
and the stdout/stderr pipe handling in :func:`run_bridge`.
"""

import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

from plugins.onenote.backend import export_onenote as onenote


# Comfortably larger than the typical 64KB OS pipe buffer.
STDERR_PAYLOAD_BYTES = 200 * 1024


class CsharpBridgeSourceTest(unittest.TestCase):
    def test_sets_console_output_encoding(self):
        """Without this the bridge writes stdout in the OEM code page (GBK on
        Chinese Windows) while Python decodes the pipe as UTF-8, which mangles
        page titles and COM error messages."""
        self.assertIn("Console.OutputEncoding", onenote.CSHARP_BRIDGE_SOURCE)

    def test_console_encoding_is_bom_free_utf8(self):
        self.assertIn(
            "Console.OutputEncoding = new UTF8Encoding(false);",
            onenote.CSHARP_BRIDGE_SOURCE,
        )

    def test_console_encoding_is_set_before_any_console_write(self):
        source = onenote.CSHARP_BRIDGE_SOURCE
        main_body = source[source.index("public static int Main("):]
        encoding_at = main_body.index("Console.OutputEncoding")
        first_write_at = min(
            main_body.index("Console.Error.WriteLine"),
            main_body.index("Console.WriteLine"),
        )
        self.assertLess(encoding_at, first_write_at)

    def test_utf8encoding_type_is_imported(self):
        self.assertIn("using System.Text;", onenote.CSHARP_BRIDGE_SOURCE)


def _child_script(stderr_bytes: int, exit_code: int) -> str:
    """A stand-in for the C# bridge: writes stdout, then floods stderr."""
    return (
        "import sys\n"
        "sys.stdout.write('publish\\t1\\tfirst.mht\\n')\n"
        "sys.stdout.flush()\n"
        f"sys.stderr.write('E' * {stderr_bytes})\n"
        "sys.stderr.flush()\n"
        "sys.stdout.write('publish\\t2\\tsecond.mht\\n')\n"
        "sys.stdout.flush()\n"
        f"sys.exit({exit_code})\n"
    )


class RunBridgeStderrPipeTest(unittest.TestCase):
    """`run_bridge(stream=True)` used to read stdout to EOF and only then read
    stderr.  A bridge that writes more than the pipe buffer to stderr blocks on
    that write, never closes stdout, and both processes hang forever."""

    def _run_bridge_with_watchdog(self, script, timeout=30.0):
        spawned = []
        real_popen = subprocess.Popen

        def tracking_popen(*args, **kwargs):
            proc = real_popen(*args, **kwargs)
            spawned.append(proc)
            return proc

        result = {}

        def target():
            try:
                result["value"] = onenote.run_bridge(["-c", script], stream=True)
            except BaseException as exc:  # noqa: BLE001 - re-raised on the main thread
                result["error"] = exc

        with mock.patch.object(onenote, "ensure_bridge", return_value=Path(sys.executable)), \
                mock.patch.object(onenote, "emit"), \
                mock.patch.object(subprocess, "Popen", tracking_popen):
            worker = threading.Thread(target=target, daemon=True)
            worker.start()
            worker.join(timeout)
            if worker.is_alive():
                for proc in spawned:
                    proc.kill()
                worker.join(timeout)
                self.fail(
                    f"run_bridge deadlocked with {STDERR_PAYLOAD_BYTES} bytes on stderr"
                )
        # run_bridge leaves the pipe objects to the garbage collector; close
        # them here so the test does not emit ResourceWarning.
        for proc in spawned:
            for pipe in (proc.stdout, proc.stderr):
                if pipe is not None:
                    pipe.close()
        return result

    def test_large_stderr_does_not_deadlock(self):
        result = self._run_bridge_with_watchdog(_child_script(STDERR_PAYLOAD_BYTES, 0))
        self.assertNotIn("error", result, msg=repr(result.get("error")))
        self.assertEqual(
            result["value"].splitlines(),
            ["publish\t1\tfirst.mht", "publish\t2\tsecond.mht"],
        )

    def test_large_stderr_is_reported_on_failure(self):
        result = self._run_bridge_with_watchdog(_child_script(STDERR_PAYLOAD_BYTES, 3))
        error = result.get("error")
        self.assertIsInstance(error, onenote.ExportError)
        self.assertEqual(str(error).count("E"), STDERR_PAYLOAD_BYTES)


if __name__ == "__main__":
    unittest.main()
