import unittest

from plugins.feishu.backend.export_feishu import select_exportable_docs


class FeishuSelectionMismatchTests(unittest.TestCase):
    def test_explicit_stale_selection_is_rejected_but_partial_match_exports(self) -> None:
        docs = [{"wiki_token": "doc", "obj_type": 22, "url": "https://example.test/doc"}]
        with self.assertRaises(RuntimeError):
            select_exportable_docs(docs, {"stale"})
        self.assertEqual([item["wiki_token"] for item in select_exportable_docs(docs, {"doc", "stale"})], ["doc"])


if __name__ == "__main__":
    unittest.main()
