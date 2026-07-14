import unittest
from unittest.mock import patch

from plugins.aliyun_thoughts.backend import export_aliyun_thoughts


class AliyunThoughtsStopExitTests(unittest.TestCase):
    def test_export_report_marked_stopped_returns_stop_exit_code(self) -> None:
        with patch.object(export_aliyun_thoughts, "export_workspace", return_value={"stopped": True}):
            exit_code = export_aliyun_thoughts.main(
                ["--workspace-url", "https://thoughts.aliyun.com/workspace/demo", "--output", "out"]
            )

        self.assertEqual(exit_code, 130)

    def test_completed_export_report_returns_success_exit_code(self) -> None:
        with patch.object(export_aliyun_thoughts, "export_workspace", return_value={"stopped": False}):
            exit_code = export_aliyun_thoughts.main(
                ["--workspace-url", "https://thoughts.aliyun.com/workspace/demo", "--output", "out"]
            )

        self.assertEqual(exit_code, 0)


if __name__ == "__main__":
    unittest.main()
