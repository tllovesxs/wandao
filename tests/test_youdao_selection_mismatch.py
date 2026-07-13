import unittest

from plugins.youdao.backend.export_youdao import RemoteNode, select_export_documents


class YoudaoSelectionMismatchTests(unittest.TestCase):
    def test_explicit_stale_selection_is_rejected_but_partial_match_exports(self) -> None:
        doc = RemoteNode("doc", "Document", False)
        with self.assertRaises(RuntimeError):
            select_export_documents([doc], ["stale"])
        self.assertEqual([item.id for item in select_export_documents([doc], ["doc", "stale"])], ["doc"])


if __name__ == "__main__":
    unittest.main()
