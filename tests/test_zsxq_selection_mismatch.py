import argparse
import unittest

from plugins.zsxq.backend.export_zsxq import select_toc_items


class ZsxqSelectionMismatchTests(unittest.TestCase):
    def test_explicit_stale_selection_is_rejected_but_partial_match_exports(self) -> None:
        toc = {"groups": [{"topics": [{"key": "toc:1:0", "title": "Article"}]}]}
        stale = argparse.Namespace(selected_toc_keys=["stale"], toc_group_pattern=None, toc_title_pattern=None, link_pattern=None, limit=0)
        partial = argparse.Namespace(selected_toc_keys=["toc:1:0", "stale"], toc_group_pattern=None, toc_title_pattern=None, link_pattern=None, limit=0)
        with self.assertRaises(RuntimeError):
            select_toc_items(toc, stale)
        self.assertEqual([item["key"] for item in select_toc_items(toc, partial)], ["toc:1:0"])


if __name__ == "__main__":
    unittest.main()
