import unittest

from plugins.wiz.backend.export_wiz import WizDoc, select_wiz_documents


class WizSelectionMismatchTests(unittest.TestCase):
    def test_explicit_stale_selection_is_rejected_but_partial_match_exports(self) -> None:
        doc = WizDoc("kb", "doc", "Document", "/", "document", ".md", 0, 0, {})
        with self.assertRaises(RuntimeError):
            select_wiz_documents([doc], {"stale"})
        self.assertEqual([item.doc_guid for item in select_wiz_documents([doc], {"doc", "stale"})], ["doc"])


if __name__ == "__main__":
    unittest.main()
