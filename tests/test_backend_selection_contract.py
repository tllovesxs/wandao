import unittest

from plugins.aliyun_thoughts.backend.export_aliyun_thoughts import Node, select_document_nodes
from plugins.feishu.backend.export_feishu import select_exportable_docs
from plugins.yuque.backend.export_yuque import require_selected_docs, select_export_docs


class BackendSelectionContractTests(unittest.TestCase):
    def test_yuque_filters_doc_id_without_selecting_title_nodes(self) -> None:
        docs = select_export_docs(
            [
                {"type": "TITLE", "uuid": "folder", "doc_id": ""},
                {"type": "DOC", "uuid": "tree-doc", "doc_id": 277273010},
                {"type": "DOC", "uuid": "other-doc", "doc_id": 277273002},
            ],
            {"277273010"},
        )

        self.assertEqual([doc["doc_id"] for doc in docs], [277273010])

    def test_yuque_rejects_nonempty_selection_that_matches_no_document(self) -> None:
        with self.assertRaisesRegex(Exception, "没有匹配到任何可导出文档"):
            require_selected_docs([], {"tree-node-id"})

    def test_aliyun_filters_document_node_ids(self) -> None:
        docs = select_document_nodes(
            [
                Node(id="folder", title="Folder", type="folder", parent_id=None, pos=0, raw={}),
                Node(id="doc", title="Doc", type="document", parent_id="folder", pos=1, raw={}),
            ],
            {"doc"},
        )

        self.assertEqual([doc.id for doc in docs], ["doc"])

    def test_feishu_filters_only_exportable_document_tokens(self) -> None:
        docs = select_exportable_docs(
            [
                {"wiki_token": "folder", "obj_type": 0, "url": ""},
                {"wiki_token": "sheet", "obj_type": 2, "url": "https://example.test/sheet"},
                {"wiki_token": "doc", "obj_type": 22, "url": "https://example.test/doc"},
            ],
            {"doc"},
        )

        self.assertEqual([doc["wiki_token"] for doc in docs], ["doc"])


if __name__ == "__main__":
    unittest.main()
