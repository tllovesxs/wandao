import tempfile
import unittest
from pathlib import Path

from plugins.aliyun_thoughts.backend.export_aliyun_thoughts import Node, select_document_nodes
from plugins.feishu.backend.export_feishu import select_exportable_docs
from plugins.yuque.backend.export_yuque import (
    build_doc_paths,
    normalize_resources,
    resource_failure_counts,
    require_selected_docs,
    select_export_docs,
    write_index,
)


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
        with self.assertRaisesRegex(Exception, "\u6ca1\u6709\u5339\u914d\u5230\u4efb\u4f55\u53ef\u5bfc\u51fa\u6587\u6863"):
            require_selected_docs([], {"tree-node-id"})

    def test_yuque_accepts_legacy_uuid_for_document_with_numeric_doc_id(self) -> None:
        toc = [
            {"type": "TITLE", "uuid": "folder", "doc_id": "", "title": "Folder", "parent_uuid": None, "level": 0},
            {
                "type": "DOC",
                "uuid": "legacy-tree-id",
                "doc_id": 277273010,
                "title": "Document",
                "parent_uuid": "folder",
                "level": 1,
            },
        ]

        docs = select_export_docs(toc, {"legacy-tree-id"})

        self.assertEqual([item["uuid"] for item in docs], ["legacy-tree-id"])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            doc_paths, _ = build_doc_paths(toc, output, {"legacy-tree-id"})
            self.assertEqual(set(doc_paths), {"277273010"})
            write_index(output, {"name": "Book"}, toc, doc_paths, {"legacy-tree-id"})
            index_path = next(output.glob("00-*.md"))
            self.assertIn("[Document]", index_path.read_text(encoding="utf-8"))

    def test_yuque_empty_book_allows_explicit_selection_to_finish_with_zero_docs(self) -> None:
        self.assertEqual(require_selected_docs([], {"stale-id"}, has_exportable_docs=False), [])

    def test_yuque_normalizes_duplicate_table_and_paragraph_resources_once(self) -> None:
        resources = normalize_resources(
            [
                {"url": "https://cdn.example.test/table.png", "kind": "image", "title": "table image"},
                {"url": "https://cdn.example.test/table.png", "kind": "image", "title": "paragraph image"},
            ]
        )

        self.assertEqual(resources, [{"url": "https://cdn.example.test/table.png", "kind": "image", "title": "table image"}])

    def test_yuque_resource_failure_counts_keep_images_and_attachments_separate(self) -> None:
        counts = resource_failure_counts(
            [
                {
                    "document": "A",
                    "failures": [
                        {"kind": "image", "url": "https://cdn.example.test/image.png", "error": "HTTP 500"},
                        {"kind": "attachment", "url": "https://files.example.test/guide.pdf", "error": "HTTP 403"},
                    ],
                }
            ]
        )

        self.assertEqual(
            counts,
            {
                "imageFailureCount": 1,
                "attachmentFailureCount": 1,
                "resourceFailureCount": 2,
            },
        )

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
