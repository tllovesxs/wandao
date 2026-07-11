import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from wandao_logging import LOG_PREFIX, WandaoLogger, mask_sensitive


class WandaoLoggingTests(unittest.TestCase):
    def test_masks_sensitive_url_query_values(self) -> None:
        text = "https://example.com/a.png?Signature=abc123&token=secret&safe=ok"

        masked = mask_sensitive(text)

        self.assertIn("Signature=***", masked)
        self.assertIn("token=***", masked)
        self.assertIn("safe=ok", masked)
        self.assertNotIn("abc123", masked)
        self.assertNotIn("secret", masked)

    def test_masks_nested_sensitive_fields(self) -> None:
        payload = {
            "message": "Authorization: Bearer abc.def token=hidden",
            "headers": {"Authorization": "Bearer abc.def", "cookie": "sid=123"},
            "items": [{"access_key": "key-123", "url": "https://a.test/?Signature=x"}],
        }

        masked = mask_sensitive(payload)

        self.assertEqual(masked["headers"]["Authorization"], "***")
        self.assertEqual(masked["headers"]["cookie"], "***")
        self.assertEqual(masked["items"][0]["access_key"], "***")
        self.assertIn("***", masked["message"])
        self.assertIn("token=***", masked["message"])
        self.assertIn("Signature=***", masked["items"][0]["url"])
        self.assertNotIn("abc.def", jsonish(masked))
        self.assertNotIn("hidden", jsonish(masked))

    def test_structured_event_carries_stable_task_lineage_and_masks_secrets(self) -> None:
        output = io.StringIO()
        with patch.dict(
            "os.environ",
            {
                "WANDAO_STRUCTURED_LOGS": "1",
                "WANDAO_RUN_ID": "run-123",
                "WANDAO_JOB_ID": "job-456",
                "WANDAO_PARENT_RUN_ID": "run-122",
            },
            clear=False,
        ), redirect_stdout(output):
            WandaoLogger("yuque").info("task.progress", "token=secret-value", api_key="secret-value")

        payload = json.loads(output.getvalue().removeprefix(LOG_PREFIX))
        self.assertEqual(payload["runId"], "run-123")
        self.assertEqual(payload["taskId"], "run-123")
        self.assertEqual(payload["jobId"], "job-456")
        self.assertEqual(payload["parentRunId"], "run-122")
        self.assertEqual(payload["api_key"], "***")
        self.assertNotIn("secret-value", payload["message"])


def jsonish(value) -> str:
    return repr(value)


if __name__ == "__main__":
    unittest.main()
