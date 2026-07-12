import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from wandao_core.browser import stop_requested


class StopMarkerTests(unittest.TestCase):
    def test_stop_requested_uses_environment_marker(self) -> None:
        with TemporaryDirectory() as tmp:
            marker = Path(tmp) / "task.stop"
            with patch.dict("os.environ", {"WANDAO_STOP_FILE": str(marker)}, clear=False):
                self.assertFalse(stop_requested(None))
                marker.write_text("stop", encoding="utf-8")
                self.assertTrue(stop_requested(None))


if __name__ == "__main__":
    unittest.main()
