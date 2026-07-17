from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = REPO_ROOT / "plugins" / "xiliu" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import export_xiliu
import import_flowus
from wandao_core.browser import ExportStopped, check_stopped


class FakeClient:
    def __init__(self, documents: dict[str, dict] | None = None) -> None:
        self.documents = documents or {}

    def get_doc(self, doc_id: str) -> dict:
        value = self.documents.get(doc_id)
        if isinstance(value, Exception):
            raise value
        if value is None:
            return {
                "code": 200,
                "data": {"blocks": {doc_id: {
                    "type": 0,
                    "title": doc_id,
                    "data": {"segments": [{"type": 0, "text": doc_id, "enhancer": {}}]},
                    "subNodes": [],
                }}},
            }
        return value


def target_list() -> list[dict]:
    return [{"id": "root-page", "name": "Root", "spaceId": "space-1", "spaceName": "Space", "role": "editor"}]


def import_args(source: Path, checkpoint: Path, *extra: str):
    return import_flowus.parse_args([
        "--source-dir", str(source), "--parent-id", "root-page", "--space-id", "space-1",
        "--checkpoint-file", str(checkpoint), "--checkpoint-task-id", "xiliu-import-test", *extra,
    ])


def checkpoint_rows(path: Path, table: str) -> list[dict]:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]
    finally:
        connection.close()


class XiliuImportCheckpointBehaviorTests(unittest.TestCase):
    def test_failed_retry_reuses_nested_folders_and_document_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            nested = source / "A" / "B"
            nested.mkdir(parents=True)
            (nested / "doc.md").write_text("# Doc\ncontent", encoding="utf-8")
            checkpoint = root / "checkpoint.sqlite"
            created: list[tuple[str, str, str]] = []
            uploads = {"count": 0}

            def create_page(_client, _space, parent, title, **kwargs):
                page_id = kwargs["page_id"]
                created.append((parent, title, page_id))
                return page_id

            def upload(_client, _html):
                uploads["count"] += 1
                if uploads["count"] == 1:
                    raise import_flowus.FlowUsError("controlled upload failure")
                return "oss/content.html"

            patches = (
                patch.object(import_flowus, "FlowUsClient", lambda *_: FakeClient()),
                patch.object(import_flowus, "list_import_targets", lambda _client: target_list()),
                patch.object(import_flowus, "create_empty_page", create_page),
                patch.object(import_flowus, "upload_html_content", upload),
                patch.object(import_flowus, "enqueue_import_task", lambda *_, **_kwargs: "task-1"),
                patch.object(import_flowus, "poll_task_result", lambda *_args, **_kwargs: {"status": "success"}),
                patch.object(import_flowus, "update_page_title", lambda *_: None),
            )
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
                first = import_flowus.import_flowus(import_args(source, checkpoint, "--resume"))
                second = import_flowus.import_flowus(import_args(source, checkpoint, "--retry-failed"))

            self.assertEqual(first["outcome"], "partial")
            self.assertEqual(second["outcome"], "completed")
            self.assertEqual([title for _parent, title, _page in created], ["A", "B", "Doc"])
            self.assertEqual(created[1][0], created[0][2])
            self.assertEqual(created[2][0], created[1][2])

            items = {row["item_key"]: row for row in checkpoint_rows(checkpoint, "items")}
            doc = items["xiliu:import:A\B\doc.md"] if "xiliu:import:A\B\doc.md" in items else items["xiliu:import:A/B/doc.md"]
            metadata = json.loads(doc["metadata_json"])
            self.assertEqual(doc["status"], "completed")
            self.assertEqual(doc["target_id"], created[2][2])
            self.assertEqual(metadata["pageId"], created[2][2])
            self.assertEqual(metadata["folders"], ["A", "B"])
            self.assertEqual(metadata["stage"], "completed")
            self.assertEqual(len([key for key in items if key.startswith("xiliu:folder:")]), 2)

    def test_stop_records_state_and_resume_reuses_page_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            (source / "doc.md").write_text("# Doc", encoding="utf-8")
            checkpoint = root / "checkpoint.sqlite"
            stop_event = threading.Event()
            created: list[str] = []
            uploads = {"count": 0}
            enqueues = {"count": 0}

            def create_page(*_args, **kwargs):
                page_id = kwargs["page_id"]
                created.append(page_id)
                return page_id

            def upload(*_args):
                uploads["count"] += 1
                return "oss/content.html"

            def enqueue(*_args, **_kwargs):
                enqueues["count"] += 1
                return "task-1"

            def stop_poll(*_args, **kwargs):
                kwargs["args"].stop_event.set()
                check_stopped(kwargs["args"])

            with patch.object(import_flowus, "FlowUsClient", lambda *_: FakeClient()),                  patch.object(import_flowus, "list_import_targets", lambda _client: target_list()),                  patch.object(import_flowus, "create_empty_page", create_page),                  patch.object(import_flowus, "upload_html_content", upload),                  patch.object(import_flowus, "enqueue_import_task", enqueue),                  patch.object(import_flowus, "poll_task_result", stop_poll):
                args = import_args(source, checkpoint, "--resume")
                args.stop_event = stop_event
                with self.assertRaises(ExportStopped):
                    import_flowus.import_flowus(args)

            self.assertEqual(checkpoint_rows(checkpoint, "tasks")[0]["status"], "stopped")
            stop_event.clear()
            with patch.object(import_flowus, "FlowUsClient", lambda *_: FakeClient()),                  patch.object(import_flowus, "list_import_targets", lambda _client: target_list()),                  patch.object(import_flowus, "create_empty_page", create_page),                  patch.object(import_flowus, "upload_html_content", upload),                  patch.object(import_flowus, "enqueue_import_task", enqueue),                  patch.object(import_flowus, "poll_task_result", lambda *_args, **_kwargs: {"status": "success"}),                  patch.object(import_flowus, "update_page_title", lambda *_: None):
                resumed = import_flowus.import_flowus(import_args(source, checkpoint, "--resume"))
            self.assertEqual(resumed["outcome"], "completed")
            self.assertEqual(len(created), 1)
            resumed_item = checkpoint_rows(checkpoint, "items")[0]
            self.assertEqual(resumed_item["target_id"], created[0])
            self.assertEqual(uploads["count"], 1)
            self.assertEqual(enqueues["count"], 1)

    def test_directory_page_markdown_reuses_its_folder_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            folder = source / "A"
            folder.mkdir(parents=True)
            (folder / "A.md").write_text("# A\ncontent", encoding="utf-8")
            checkpoint = root / "checkpoint.sqlite"
            created: list[tuple[str, str, str]] = []

            def create_page(_client, _space, parent, title, **kwargs):
                page_id = kwargs["page_id"]
                created.append((parent, title, page_id))
                return page_id

            with (
                patch.object(import_flowus, "FlowUsClient", lambda *_: FakeClient()),
                patch.object(import_flowus, "list_import_targets", lambda _client: target_list()),
                patch.object(import_flowus, "create_empty_page", create_page),
                patch.object(import_flowus, "upload_html_content", lambda *_: "oss/content.html"),
                patch.object(import_flowus, "enqueue_import_task", lambda *_, **_kwargs: "task-1"),
                patch.object(import_flowus, "poll_task_result", lambda *_args, **_kwargs: {"status": "success"}),
                patch.object(import_flowus, "update_page_title", lambda *_: None),
            ):
                result = import_flowus.import_flowus(import_args(source, checkpoint, "--resume"))

            self.assertEqual(result["outcome"], "completed")
            self.assertEqual(len(created), 1)
            self.assertEqual(created[0][:2], ("root-page", "A"))
            page_id = created[0][2]
            items = {row["item_key"]: row for row in checkpoint_rows(checkpoint, "items")}
            folder_item = items["xiliu:folder:A"]
            doc_key = next(key for key in items if key.startswith("xiliu:import:A"))
            self.assertEqual(folder_item["target_id"], page_id)
            self.assertEqual(items[doc_key]["target_id"], page_id)

    def test_import_requires_an_explicit_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            (source / "doc.md").write_text("# Doc", encoding="utf-8")
            args = import_flowus.parse_args(["--source-dir", str(source)])
            with patch.object(import_flowus, "FlowUsClient", lambda *_: FakeClient()),                  patch.object(import_flowus, "list_import_targets", lambda _client: target_list()):
                with self.assertRaisesRegex(import_flowus.FlowUsError, "请选择明确"):
                    import_flowus.import_flowus(args)


class XiliuExportReliabilityTests(unittest.TestCase):
    def test_title_only_pages_do_not_create_markdown_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "out"
            nodes = [
                export_xiliu.FlowUsNode("root", "Root", True),
                export_xiliu.FlowUsNode("empty", "Empty", False, parent_id="root"),
            ]
            documents = {
                "root": {
                    "code": 200,
                    "data": {"blocks": {
                        "root": {
                            "type": 0,
                            "title": "Root",
                            "data": {"segments": [{"type": 0, "text": "Root", "enhancer": {}}]},
                            "subNodes": ["empty"],
                        },
                        "empty": {
                            "type": 0,
                            "title": "Empty",
                            "data": {"segments": [{"type": 0, "text": "Empty", "enhancer": {}}]},
                            "subNodes": [],
                        },
                    }},
                },
                "empty": {
                    "code": 200,
                    "data": {"blocks": {
                        "empty": {
                            "type": 0,
                            "title": "Empty",
                            "data": {"segments": [{"type": 0, "text": "Empty", "enhancer": {}}]},
                            "subNodes": [],
                        }
                    }},
                },
            }
            args = export_xiliu.parse_args([
                "--doc-url", "https://flowus.cn/root", "--output", str(output)
            ])
            with (
                patch.object(export_xiliu, "FlowUsClient", lambda *_: FakeClient(documents)),
                patch.object(export_xiliu, "build_toc_tree", lambda *_args, **_kwargs: nodes),
            ):
                result = export_xiliu.export_flowus(args)

            self.assertEqual(result["exported"], 0)
            self.assertTrue((output / "Root").is_dir())
            self.assertFalse((output / "Root" / "Root.md").exists())
            self.assertFalse((output / "Root" / "Empty.md").exists())

    def test_deep_selection_keeps_ancestors_and_collision_paths_are_stable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            nodes = [
                export_xiliu.FlowUsNode("root", "Root", True),
                export_xiliu.FlowUsNode("folder", "Folder", True, parent_id="root"),
                export_xiliu.FlowUsNode("deep", "Deep", False, parent_id="folder"),
            ]
            args = export_xiliu.parse_args([
                "--doc-url", "https://flowus.cn/root", "--output", str(output), "--doc-id", "deep"
            ])
            with patch.object(export_xiliu, "FlowUsClient", lambda *_: FakeClient()),                  patch.object(export_xiliu, "build_toc_tree", lambda *_args, **_kwargs: nodes):
                result = export_xiliu.export_flowus(args)
            self.assertEqual(result["outcome"], "completed")
            self.assertTrue((output / "Root" / "Folder" / "Deep.md").exists())

            collision_output = output / "collision"
            collision_nodes = [
                export_xiliu.FlowUsNode("root", "Root", True),
                export_xiliu.FlowUsNode("doc-one", "A/B", False, parent_id="root"),
                export_xiliu.FlowUsNode("doc-two", "A:B", False, parent_id="root"),
            ]
            args = export_xiliu.parse_args(["--doc-url", "https://flowus.cn/root", "--output", str(collision_output)])
            with patch.object(export_xiliu, "FlowUsClient", lambda *_: FakeClient()),                  patch.object(export_xiliu, "build_toc_tree", lambda *_args, **_kwargs: collision_nodes):
                export_xiliu.export_flowus(args)
            files = sorted(path.name for path in (collision_output / "Root").glob("A-B*.md"))
            collision_components = export_xiliu._stable_output_components(collision_nodes)
            self.assertEqual(
                files,
                sorted([
                    f"{collision_components['doc-one']}.md",
                    f"{collision_components['doc-two']}.md",
                ]),
            )

    def test_collision_paths_do_not_overwrite_ids_with_the_same_12_character_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            first_id = "123456789012-alpha"
            second_id = "123456789012-beta"
            nodes = [
                export_xiliu.FlowUsNode("root", "Root", True),
                export_xiliu.FlowUsNode(first_id, "A/B", False, parent_id="root"),
                export_xiliu.FlowUsNode(second_id, "A:B", False, parent_id="root"),
            ]
            args = export_xiliu.parse_args([
                "--doc-url", "https://flowus.cn/root", "--output", str(output),
            ])
            with (
                patch.object(export_xiliu, "FlowUsClient", lambda *_: FakeClient()),
                patch.object(export_xiliu, "build_toc_tree", lambda *_args, **_kwargs: nodes),
            ):
                result = export_xiliu.export_flowus(args)

            files = sorted((output / "Root").glob("A-B*.md"))
            self.assertEqual(result["exported"], 3)
            self.assertEqual(len(files), 2)
            self.assertEqual(len({path.name.casefold() for path in files}), 2)
            contents = {path.read_text(encoding="utf-8").strip() for path in files}
            self.assertEqual(contents, {f"# {first_id}", f"# {second_id}"})
            self.assertEqual(
                export_xiliu._stable_output_components(nodes),
                export_xiliu._stable_output_components(list(reversed(nodes))),
            )

    def test_image_download_checks_stop_before_each_request(self) -> None:
        calls: list[str] = []

        class ImageClient:
            args = SimpleNamespace()

            def request(self, method, *_args, **_kwargs):
                calls.append(method)
                return json.dumps({"code": 200, "data": [{"url": "https://example.test/image.png"}]}).encode()

        client = ImageClient()
        with patch.object(export_xiliu, "check_stopped", side_effect=ExportStopped("controlled stop")):
            with self.assertRaises(ExportStopped):
                export_xiliu.download_image_data(client, "block", "oss/image.png")
        self.assertEqual(calls, [])

        calls.clear()
        with patch.object(
            export_xiliu,
            "check_stopped",
            side_effect=[None, None, ExportStopped("controlled stop before image GET")],
        ):
            with self.assertRaises(ExportStopped):
                export_xiliu.download_image_data(client, "block", "oss/image.png")
        self.assertEqual(calls, ["POST"])

    def test_throttle_and_retry_backoff_use_wait_with_stop(self) -> None:
        args = SimpleNamespace(request_delay=1.25, request_jitter=0, retry=2)
        client = export_xiliu.FlowUsClient.__new__(export_xiliu.FlowUsClient)
        client.args = args
        client.request_count = 0
        client.token = "token"
        client.cookies = []

        with (
            patch.object(export_xiliu, "wait_with_stop") as wait,
            patch.object(
                export_xiliu.urllib.request,
                "urlopen",
                side_effect=export_xiliu.urllib.error.URLError("controlled network failure"),
            ),
        ):
            with self.assertRaises(export_xiliu.FlowUsError):
                client.request("GET", "https://example.test/data")

        self.assertEqual(wait.call_args_list[0].args, (args, 1.25))
        self.assertEqual(wait.call_args_list[1].args, (args, 0.8))
        self.assertEqual(wait.call_count, 2)

    def test_root_failure_is_fatal_and_child_failure_is_structured(self) -> None:
        with self.assertRaisesRegex(export_xiliu.FlowUsError, "读取根目录失败"):
            export_xiliu.build_toc_tree(FakeClient({"root": export_xiliu.FlowUsError("offline")}), "root")

        missing_root = {"code": 200, "data": {"blocks": {"other": {"type": 0, "title": "Other"}}}}
        with self.assertRaisesRegex(export_xiliu.FlowUsError, "缺少目标页面 block"):
            export_xiliu.build_toc_tree(FakeClient({"root": missing_root}), "root")

        root_response = {
            "code": 200,
            "data": {"blocks": {
                "root": {"type": 0, "title": "Root", "subNodes": ["child"]},
                "child": {"type": 0, "title": "Child", "subNodes": []},
            }},
        }
        failures: list[dict] = []
        nodes = export_xiliu.build_toc_tree(
            FakeClient({"root": root_response, "child": export_xiliu.FlowUsError("child offline")}),
            "root",
            failures=failures,
        )
        self.assertEqual([node.id for node in nodes], ["root"])
        self.assertTrue(nodes[0].is_dir)
        self.assertEqual(failures[0]["type"], "subtree")
        self.assertEqual(failures[0]["documentId"], "child")

    def test_export_stop_records_state_and_resume_completes_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint = root / "checkpoint.sqlite"
            output = root / "out"
            node = export_xiliu.FlowUsNode("doc", "Doc", False)
            stop_event = threading.Event()
            args = export_xiliu.parse_args([
                "--doc-url", "https://flowus.cn/doc", "--output", str(output),
                "--checkpoint-file", str(checkpoint),
                "--checkpoint-task-id", "xiliu-export-stop-test", "--resume",
            ])
            args.stop_event = stop_event

            def stop_convert(*_args):
                stop_event.set()
                check_stopped(args)

            with (
                patch.object(export_xiliu, "FlowUsClient", lambda *_: FakeClient()),
                patch.object(export_xiliu, "build_toc_tree", lambda *_args, **_kwargs: [node]),
                patch.object(export_xiliu, "convert_flowus_blocks_to_markdown", stop_convert),
            ):
                with self.assertRaises(ExportStopped):
                    export_xiliu.export_flowus(args)

            self.assertEqual(checkpoint_rows(checkpoint, "tasks")[0]["status"], "stopped")
            self.assertEqual(checkpoint_rows(checkpoint, "items")[0]["status"], "failed")

            stop_event.clear()
            resumed_args = export_xiliu.parse_args([
                "--doc-url", "https://flowus.cn/doc", "--output", str(output),
                "--checkpoint-file", str(checkpoint),
                "--checkpoint-task-id", "xiliu-export-stop-test", "--resume",
            ])
            with (
                patch.object(export_xiliu, "FlowUsClient", lambda *_: FakeClient()),
                patch.object(export_xiliu, "build_toc_tree", lambda *_args, **_kwargs: [node]),
                patch.object(
                    export_xiliu,
                    "convert_flowus_blocks_to_markdown",
                    lambda *_args: ("# Doc\n\ncontent", 0, []),
                ),
            ):
                resumed = export_xiliu.export_flowus(resumed_args)

            self.assertEqual(resumed["outcome"], "completed")
            self.assertTrue((output / "Doc.md").is_file())
            self.assertEqual(checkpoint_rows(checkpoint, "tasks")[0]["status"], "completed")
            self.assertEqual(checkpoint_rows(checkpoint, "items")[0]["status"], "completed")

    def test_failed_subtree_is_checkpointed_and_retryable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint = root / "checkpoint.sqlite"
            output = root / "out"
            root_response = {
                "code": 200,
                "data": {"blocks": {
                    "root": {"type": 0, "title": "Root", "subNodes": ["child"], "data": {"segments": []}},
                    "child": {"type": 0, "title": "Child", "subNodes": [], "data": {"segments": []}},
                }},
            }
            child_response = {
                "code": 200,
                "data": {"blocks": {
                    "child": {
                        "type": 0,
                        "title": "Child",
                        "subNodes": ["text"],
                        "data": {"segments": [{"type": 0, "text": "Child", "enhancer": {}}]},
                    },
                    "text": {
                        "type": 1,
                        "data": {"segments": [{"type": 0, "text": "content", "enhancer": {}}]},
                        "subNodes": [],
                    },
                }},
            }
            documents = {"root": root_response, "child": export_xiliu.FlowUsError("offline")}
            client = FakeClient(documents)

            def run(extra):
                args = export_xiliu.parse_args([
                    "--doc-url", "https://flowus.cn/root", "--output", str(output),
                    "--checkpoint-file", str(checkpoint),
                    "--checkpoint-task-id", "xiliu-subtree-test", *extra,
                ])
                with patch.object(export_xiliu, "FlowUsClient", lambda *_: client):
                    return export_xiliu.export_flowus(args)

            first = run(["--resume"])
            self.assertEqual(first["outcome"], "partial")
            failed = {row["item_key"]: row for row in checkpoint_rows(checkpoint, "items")}
            self.assertEqual(failed["xiliu:doc:child"]["status"], "failed")

            documents["child"] = child_response
            second = run(["--retry-failed"])
            self.assertEqual(second["outcome"], "completed")
            self.assertTrue((output / "Root" / "Child.md").is_file())
            completed = {row["item_key"]: row for row in checkpoint_rows(checkpoint, "items")}
            self.assertEqual(completed["xiliu:doc:child"]["status"], "completed")

    def test_incremental_retry_failed_restores_image_and_completes_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint = root / "checkpoint.sqlite"
            output = root / "out"
            node = export_xiliu.FlowUsNode("doc", "Doc", False)
            documents = {
                "doc": {
                    "code": 200,
                    "data": {"blocks": {
                        "doc": {
                            "type": 0,
                            "title": "Doc",
                            "data": {"segments": [{"type": 0, "text": "Doc", "enhancer": {}}]},
                            "subNodes": ["image"],
                        },
                        "image": {
                            "type": 14,
                            "data": {"ossName": "oss/image.png", "segments": []},
                            "subNodes": [],
                        },
                    }},
                },
            }
            downloads = {"count": 0}

            def download(*_args):
                downloads["count"] += 1
                return None if downloads["count"] == 1 else b"restored-image"

            def run(extra: list[str]):
                args = export_xiliu.parse_args([
                    "--doc-url", "https://flowus.cn/doc", "--output", str(output),
                    "--checkpoint-file", str(checkpoint), "--checkpoint-task-id", "xiliu-export-test", *extra,
                ])
                with (
                    patch.object(export_xiliu, "FlowUsClient", lambda *_: FakeClient(documents)),
                    patch.object(export_xiliu, "build_toc_tree", lambda *_args, **_kwargs: [node]),
                    patch.object(export_xiliu, "download_image_data", download),
                ):
                    return export_xiliu.export_flowus(args)

            first = run(["--resume"])
            placeholder = (output / "Doc.md").read_text(encoding="utf-8")
            second = run(["--incremental", "--retry-failed"])
            restored = (output / "Doc.md").read_text(encoding="utf-8")

            self.assertEqual(first["outcome"], "partial")
            self.assertEqual(first["resourceFailureCount"], 1)
            self.assertIn("图片下载失败: oss/image.png", placeholder)
            self.assertEqual(second["outcome"], "completed")
            self.assertEqual(second["resourceFailureCount"], 0)
            self.assertEqual(downloads["count"], 2)
            self.assertIn("assets/001-image.png", restored)
            self.assertNotIn("图片下载失败", restored)
            self.assertEqual((output / "assets" / "001-image.png").read_bytes(), b"restored-image")
            self.assertEqual(checkpoint_rows(checkpoint, "items")[0]["status"], "completed")
            self.assertEqual(second["kind"], "wandao.result")


if __name__ == "__main__":
    unittest.main()
