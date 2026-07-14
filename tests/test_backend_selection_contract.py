import argparse
import contextlib
import io
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from plugins.aliyun_thoughts.backend.export_aliyun_thoughts import Node, select_document_nodes
from plugins.feishu.backend.export_feishu import select_exportable_docs
from plugins.yuque.backend import export_yuque
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

    def test_yuque_cli_uses_modest_default_detail_request_pacing(self) -> None:
        args = export_yuque.parse_args(
            [
                "--book-url", "https://www.yuque.com/example/book",
                "--output", "output",
            ]
        )

        self.assertEqual(args.request_delay, 0.55)
        self.assertEqual(args.request_jitter, 0.25)

    def test_yuque_provider_defaults_match_modest_cli_detail_request_pacing(self) -> None:
        manifest_path = Path(__file__).resolve().parents[1] / "plugins" / "yuque" / "providers" / "yuque" / "provider.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        options = {item["name"]: item for item in manifest["fields"]}

        self.assertEqual(options["request_delay"]["default"], 0.55)
        self.assertEqual(options["request_jitter"]["default"], 0.25)

    def test_yuque_doc_api_missing_data_raises_safe_api_error(self) -> None:
        class MissingDataCdp:
            expression = ""

            def evaluate(self, expression: str, timeout: int) -> dict[str, object]:
                assert timeout == 120
                self.expression = expression
                return {
                    "apiError": {
                        "status": 403,
                        "statusText": "Forbidden",
                        "code": "NoPermission",
                        "message": "forbidden",
                        "dataPresent": False,
                    }
                }

        cdp = MissingDataCdp()
        with self.assertRaisesRegex(export_yuque.ExportError, r"HTTP 403.*forbidden"):
            export_yuque.fetch_doc_markdown(
                cdp,
                1,
                {"url": "private-doc", "title": "Private document"},
            )

        self.assertIn("response.ok", cdp.expression)
        self.assertIn("payload?.data", cdp.expression)
        self.assertIn("typeof payload.data === 'object'", cdp.expression)
        self.assertIn("Array.isArray(payload.data)", cdp.expression)

    def test_yuque_cli_startup_stop_returns_130_with_stopped_payload(self) -> None:
        args = argparse.Namespace(
            book_url="https://www.yuque.com/example/book",
            login=False,
            scan_toc=False,
            output=Path("output"),
        )
        stdout = io.StringIO()

        with (
            mock.patch.object(export_yuque, "parse_args", return_value=args),
            mock.patch.object(
                export_yuque,
                "export_book",
                side_effect=export_yuque.ExportStopped("?????????"),
            ),
            mock.patch.object(export_yuque, "emit"),
            contextlib.redirect_stdout(stdout),
        ):
            self.assertEqual(export_yuque.main(["--book-url", args.book_url, "--output", "output"]), 130)

        self.assertTrue(json.loads(stdout.getvalue())["stopped"])

    def test_yuque_cli_stopped_result_returns_130_and_keeps_resource_failure_details(self) -> None:
        args = argparse.Namespace(book_url="https://www.yuque.com/example/book", login=False, scan_toc=False, output=Path("output"))
        resource_failures = [
            {
                "document": "doc.md",
                "failures": [
                    {"kind": "image", "url": "https://cdn.example.test/image.png", "error": "HTTP 404"}
                ],
            }
        ]
        report = {
            "stopped": True,
            "exportedDocs": 200,
            "skippedDocs": 316,
            "imageFailureCount": 1,
            "attachmentFailureCount": 0,
            "resourceFailureCount": 1,
            "resourceFailures": resource_failures,
            "imageFailures": resource_failures,
            "attachmentFailures": [],
            "failures": [],
            "reportFile": "output/00-Yuque-export-report.json",
        }
        stdout = io.StringIO()

        with mock.patch.object(export_yuque, "parse_args", return_value=args), mock.patch.object(export_yuque, "export_book", return_value=report), contextlib.redirect_stdout(stdout):
            self.assertEqual(export_yuque.main(["--book-url", args.book_url, "--output", "output"]), 130)

        payload = json.loads(stdout.getvalue())
        self.assertTrue(payload["stopped"])
        self.assertEqual(payload["resourceFailures"], resource_failures)
        self.assertEqual(payload["imageFailures"], resource_failures)
        self.assertEqual(payload["reportFile"], "output/00-Yuque-export-report.json")

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
