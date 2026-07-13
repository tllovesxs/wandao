import unittest

from plugins.ima.backend.ima_knowledge import KnowledgeEntry, selected_entries


class ImaSelectionMismatchTests(unittest.TestCase):
    def test_explicit_stale_selection_is_rejected_but_partial_match_exports(self) -> None:
        entry = KnowledgeEntry("kb", "KB", "doc", "Document", "", [], False)
        with self.assertRaises(RuntimeError):
            selected_entries([entry], ["stale"])
        self.assertEqual([item.export_id for item in selected_entries([entry], [entry.export_id, "stale"])], [entry.export_id])


if __name__ == "__main__":
    unittest.main()
