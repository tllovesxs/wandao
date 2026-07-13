import unittest

from plugins.aliyun_thoughts.backend.export_aliyun_thoughts import Node, select_document_nodes


class AliyunSelectionMismatchTests(unittest.TestCase):
    def test_explicit_stale_selection_is_rejected_but_partial_match_exports(self) -> None:
        nodes = [Node(id="doc", title="Doc", type="document", parent_id=None, pos=1, raw={})]
        with self.assertRaises(RuntimeError):
            select_document_nodes(nodes, {"stale"})
        self.assertEqual([item.id for item in select_document_nodes(nodes, {"doc", "stale"})], ["doc"])


if __name__ == "__main__":
    unittest.main()
