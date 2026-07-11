import argparse
import json
import tempfile
import unittest
from pathlib import Path

from wandao_cli import extend_arg_list_from_file, read_id_file


class WandaoCliTests(unittest.TestCase):
    def test_plugin_root_supports_electron_resources_layout(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "wandao.py").read_text(encoding="utf-8")

        self.assertIn("def find_plugins_root()", source)
        self.assertIn('ROOT.parent / "plugins"', source)
        self.assertIn("PLUGINS_ROOT = find_plugins_root()", source)

    def test_read_id_file_accepts_json_object_and_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            json_path = root / "ids.json"
            lines_path = root / "ids.txt"
            json_path.write_text(json.dumps({"docIds": ["a", "b", ""]}), encoding="utf-8")
            lines_path.write_text("x\n\n y \n", encoding="utf-8")

            self.assertEqual(read_id_file(json_path), ["a", "b"])
            self.assertEqual(read_id_file(lines_path), ["x", "y"])

    def test_extend_arg_list_from_file_appends_to_existing_attr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ids.json"
            path.write_text(json.dumps(["new-1", "new-2"]), encoding="utf-8")
            args = argparse.Namespace(selected_doc_ids=["old"], doc_id_file=str(path))

            extend_arg_list_from_file(args)

            self.assertEqual(args.selected_doc_ids, ["old", "new-1", "new-2"])


if __name__ == "__main__":
    unittest.main()
