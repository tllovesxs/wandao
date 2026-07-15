import tempfile
import unittest
from pathlib import Path
from unittest import mock

import import_feishu


class FeishuImportConfigPathTests(unittest.TestCase):
    def test_canonical_config_wins_when_both_names_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            canonical = Path(tmp) / "feishu_import_config.json"
            legacy = Path(tmp) / ".feishu_import_config.json"
            canonical.write_text("{}", encoding="utf-8")
            legacy.write_text("{}", encoding="utf-8")

            with mock.patch.object(import_feishu, "DEFAULT_CONFIG_FILE", canonical), mock.patch.object(
                import_feishu, "LEGACY_DEFAULT_CONFIG_FILE", legacy
            ):
                self.assertEqual(import_feishu.default_import_config_path(), canonical.resolve())

    def test_legacy_hidden_config_remains_readable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            canonical = Path(tmp) / "feishu_import_config.json"
            legacy = Path(tmp) / ".feishu_import_config.json"
            legacy.write_text("{}", encoding="utf-8")

            with mock.patch.object(import_feishu, "DEFAULT_CONFIG_FILE", canonical), mock.patch.object(
                import_feishu, "LEGACY_DEFAULT_CONFIG_FILE", legacy
            ):
                self.assertEqual(import_feishu.default_import_config_path(), legacy.resolve())


if __name__ == "__main__":
    unittest.main()
