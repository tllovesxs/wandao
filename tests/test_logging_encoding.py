import codecs
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import wandao
import wandao_core.logging  # noqa: F401  # imported for its module-level stream reconfigure


REPO_ROOT = Path(__file__).resolve().parents[1]


class LoggingStreamEncodingTests(unittest.TestCase):
    def test_stdout_and_stderr_are_utf8_after_importing_logging(self) -> None:
        for name in ("stdout", "stderr"):
            stream = getattr(sys, name, None)
            encoding = getattr(stream, "encoding", None)
            if encoding is None:
                self.skipTest(f"sys.{name} is replaced by the test runner and has no encoding")
            with self.subTest(stream=name):
                self.assertEqual(codecs.lookup(encoding).name, "utf-8")

    def test_import_reconfigures_a_non_utf8_console(self) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(REPO_ROOT), env.get("PYTHONPATH", "")]))
        env["PYTHONUTF8"] = "0"
        env["PYTHONIOENCODING"] = "gbk"
        code = (
            "import sys\n"
            "before = sys.stdout.encoding\n"
            "import wandao_core.logging\n"
            "sys.stdout.write(before + '|' + sys.stdout.encoding + '|' + sys.stderr.encoding)\n"
        )

        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        before, after_out, after_err = result.stdout.strip().split("|")
        self.assertEqual(codecs.lookup(before).name, "gbk")
        self.assertEqual(codecs.lookup(after_out).name, "utf-8")
        self.assertEqual(codecs.lookup(after_err).name, "utf-8")

    def test_emoji_survives_a_gbk_console(self) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(REPO_ROOT), env.get("PYTHONPATH", "")]))
        env["PYTHONUTF8"] = "0"
        env["PYTHONIOENCODING"] = "gbk"
        code = "from wandao_core.logging import print_text\nprint_text('\\N{ROCKET} 万能导')\n"

        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("\N{ROCKET}", result.stdout)
        self.assertIn("万能导", result.stdout)


class RunProviderEnvEncodingTests(unittest.TestCase):
    def _capture_env(self) -> dict[str, str]:
        captured: dict[str, dict[str, str]] = {}

        def fake_call(cmd, cwd=None, env=None):  # noqa: ANN001
            captured["env"] = dict(env or {})
            return 0

        providers = {"demo": {"script": Path(__file__).resolve()}}
        with patch("wandao.subprocess.call", side_effect=fake_call) as call_mock:
            self.assertEqual(wandao.run_provider("demo", [], providers), 0)
        self.assertEqual(call_mock.call_count, 1)
        return captured["env"]

    def test_run_provider_env_forces_python_utf8_mode(self) -> None:
        env = self._capture_env()

        self.assertEqual(env["PYTHONUTF8"], "1")
        self.assertEqual(codecs.lookup(env["PYTHONIOENCODING"]).name, "utf-8")
        self.assertIn("PYTHONPATH", env)

    def test_run_provider_respects_an_explicit_io_encoding(self) -> None:
        with patch.dict(os.environ, {"PYTHONIOENCODING": "gbk"}, clear=False):
            env = self._capture_env()

        self.assertEqual(env["PYTHONUTF8"], "1")
        self.assertEqual(env["PYTHONIOENCODING"], "gbk")


if __name__ == "__main__":
    unittest.main()
