import io
import json
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from plugins.wiz.backend import export_wiz


class _FakeCdp:
    def navigate(self, _url: str) -> None:
        return None

    def close(self) -> None:
        return None


class WizLoginStdoutTests(unittest.TestCase):
    def test_login_prompt_does_not_pollute_result_json_stdout(self) -> None:
        stdout = io.StringIO()
        args = SimpleNamespace(close_started_chrome=False)
        result = {"authFile": "test-auth.json", "docCount": 2, "folderCount": 1}

        with (
            patch.object(export_wiz, "connect_wiz_browser", return_value=(_FakeCdp(), None)),
            patch.object(export_wiz, "save_auth_state", return_value=result),
            patch("sys.stdin", io.StringIO("\n")),
            patch.dict("os.environ", {"WANDAO_STRUCTURED_LOGS": "1", "WANDAO_PROVIDER_ID": "wiz"}),
            redirect_stdout(stdout),
        ):
            returned = export_wiz.run_login(args)
            print(json.dumps(returned, ensure_ascii=False, indent=2))

        output = stdout.getvalue()
        self.assertNotIn("Press Enter after WizNote is logged in and visible...", output)
        result_lines = [
            line
            for line in output.splitlines()
            if not line.startswith("@@WANDAO_LOG@@")
        ]
        self.assertEqual(json.loads("\n".join(result_lines)), result)


if __name__ == "__main__":
    unittest.main()
