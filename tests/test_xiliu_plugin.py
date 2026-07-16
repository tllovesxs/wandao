from __future__ import annotations

import importlib
import json
import logging
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


REPO_ROOT = Path(__file__).resolve().parents[1]
XILIU_BACKEND = str(REPO_ROOT / "plugins" / "xiliu" / "backend")


def _load_module(name: str):
    """Import a module from the xiliu backend directory."""
    if XILIU_BACKEND not in sys.path:
        sys.path.insert(0, XILIU_BACKEND)
    if name in sys.modules:
        return sys.modules[name]
    return importlib.import_module(name)


class XiliuExportParseArgsTests(unittest.TestCase):
    """Tests for export_xiliu.parse_args checkpoint/retry-failed support."""

    def test_export_accepts_checkpoint_and_retry_failed(self) -> None:
        module = _load_module("export_xiliu")
        args = module.parse_args(
            [
                "--doc-url", "https://flowus.cn/demo",
                "--output", "out",
                "--checkpoint-file", "out/.wandao/checkpoint.sqlite",
                "--checkpoint-task-id", "task-1",
                "--resume",
                "--retry-failed",
            ]
        )
        self.assertEqual(args.checkpoint_file, "out/.wandao/checkpoint.sqlite")
        self.assertEqual(args.checkpoint_task_id, "task-1")
        self.assertTrue(args.resume)
        self.assertTrue(args.retry_failed)


class XiliuImportParseArgsTests(unittest.TestCase):
    """Tests for import_flowus.parse_args checkpoint/retry-failed support."""

    def test_import_accepts_checkpoint_and_retry_failed(self) -> None:
        module = _load_module("import_flowus")
        args = module.parse_args(
            [
                "--source-dir", "source",
                "--space-id", "space-1",
                "--checkpoint-file", "source/.wandao/checkpoint.sqlite",
                "--checkpoint-task-id", "task-1",
                "--resume",
                "--retry-failed",
            ]
        )
        self.assertEqual(args.checkpoint_file, "source/.wandao/checkpoint.sqlite")
        self.assertEqual(args.checkpoint_task_id, "task-1")
        self.assertTrue(args.resume)
        self.assertTrue(args.retry_failed)

    def test_import_accepts_task_timeout(self) -> None:
        module = _load_module("import_flowus")
        args = module.parse_args(
            [
                "--source-dir", "source",
                "--space-id", "space-1",
                "--task-timeout", "300",
            ]
        )
        self.assertEqual(args.task_timeout, 300)


class PathBoundaryTests(unittest.TestCase):
    """Tests for _resolve_image_src and extract_local_images path traversal rejection."""

    def test_resolve_image_src_rejects_dot_dot_escape(self) -> None:
        module = _load_module("import_flowus")
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp) / "source"
            source_dir.mkdir()
            outside = Path(tmp) / "outside"
            outside.mkdir()
            (outside / "secret.png").write_bytes(b"\x89PNG")

            result = module._resolve_image_src("../../outside/secret.png", source_dir)
            self.assertEqual(result, "../../outside/secret.png")

    def test_resolve_image_src_rejects_absolute_path(self) -> None:
        module = _load_module("import_flowus")
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp) / "source"
            source_dir.mkdir()
            result = module._resolve_image_src("/etc/passwd", source_dir)
            self.assertEqual(result, "/etc/passwd")

    def test_resolve_image_src_allows_valid_relative_path(self) -> None:
        module = _load_module("import_flowus")
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp) / "source"
            source_dir.mkdir()
            img = source_dir / "photo.png"
            img.write_bytes(b"\x89PNG")

            result = module._resolve_image_src("photo.png", source_dir)
            self.assertTrue(result.startswith("data:image/png;base64,"))

    def test_extract_local_images_rejects_dot_dot_escape(self) -> None:
        module = _load_module("import_flowus")
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp) / "source"
            source_dir.mkdir()
            outside = Path(tmp) / "outside"
            outside.mkdir()
            (outside / "secret.png").write_bytes(b"\x89PNG")

            md = "![alt](../../outside/secret.png)"
            images = module.extract_local_images(md, source_dir)
            self.assertEqual(images, [])

    def test_extract_local_images_rejects_absolute_path(self) -> None:
        module = _load_module("import_flowus")
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp) / "source"
            source_dir.mkdir()
            md = "![alt](/etc/passwd)"
            images = module.extract_local_images(md, source_dir)
            self.assertEqual(images, [])


class ZeroMatchSelectionTests(unittest.TestCase):
    """Test that --doc-id with zero matches raises FlowUsError."""

    def test_selected_ids_zero_match_raises(self) -> None:
        module = _load_module("export_xiliu")
        args = module.parse_args(
            ["--doc-url", "https://flowus.cn/demo", "--output", "out", "--doc-id", "nonexistent-id"]
        )
        mock_nodes = [
            module.FlowUsNode(id="real-id", title="Test Doc", is_dir=False),
        ]
        with patch.object(module, "build_toc_tree", return_value=mock_nodes), \
             patch.object(module, "read_auth_payload", return_value={"token": "fake", "cookies": []}):
            with self.assertRaises(module.FlowUsError) as ctx:
                module.export_flowus(args)
            self.assertIn("均未在目录中找到", str(ctx.exception))


class CredentialWriteTests(unittest.TestCase):
    """Test that save_auth_state uses write_private_json."""

    def test_save_auth_state_uses_write_private_json(self) -> None:
        source = (REPO_ROOT / "plugins/xiliu/backend/export_xiliu.py").read_text(encoding="utf-8")
        self.assertIn("write_private_json(auth_file, payload)", source)
        self.assertNotIn('auth_file.write_text(json.dumps(payload', source)


class TaskTimeoutConfigTests(unittest.TestCase):
    """Test that task_timeout is used instead of hardcoded 120."""

    def test_task_timeout_is_configurable(self) -> None:
        source = (REPO_ROOT / "plugins/xiliu/backend/import_flowus.py").read_text(encoding="utf-8")
        self.assertIn("getattr(args, \"task_timeout\"", source)
        self.assertNotIn("poll_task_result(client, task_id, timeout=120", source)


class LogSanitizationTests(unittest.TestCase):
    """Verify import_flowus.py does not leak raw API responses in logs."""

    def test_no_raw_json_dumps_in_emit_calls(self) -> None:
        source = (REPO_ROOT / "plugins/xiliu/backend/import_flowus.py").read_text(encoding="utf-8")
        import re
        # Find all emit() calls that contain json.dumps (raw response dumping)
        pattern = r'emit\(f?["\'].*json\.dumps\(result'
        matches = re.findall(pattern, source)
        self.assertEqual(matches, [], f"Found emit calls still dumping raw JSON: {matches}")

    def test_no_oss_name_in_emit_calls(self) -> None:
        source = (REPO_ROOT / "plugins/xiliu/backend/import_flowus.py").read_text(encoding="utf-8")
        import re
        # Check that emit calls don't leak ossName/oss_name values
        pattern = r'emit\(.*ossName=\{|emit\(.*oss_name\b'
        matches = re.findall(pattern, source)
        self.assertEqual(matches, [], f"Found emit calls leaking ossName: {matches}")


class SymlinkBoundaryTests(unittest.TestCase):
    """Test that scan_markdown_docs rejects symlinks pointing outside source_dir."""

    def test_scan_rejects_symlink_escape(self) -> None:
        module = _load_module("import_flowus")
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp) / "source"
            source_dir.mkdir()
            outside = Path(tmp) / "outside"
            outside.mkdir()
            (outside / "secret.md").write_text("# Secret", encoding="utf-8")

            # Create symlink inside source pointing outside
            link = source_dir / "link.md"
            try:
                link.symlink_to(outside / "secret.md")
            except OSError:
                self.skipTest("Platform does not support symlinks")

            docs = module.scan_markdown_docs(source_dir)
            # Should skip the symlink that escapes source_dir
            self.assertEqual(len(docs), 0)

    def test_scan_accepts_normal_files(self) -> None:
        module = _load_module("import_flowus")
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp) / "source"
            source_dir.mkdir()
            (source_dir / "doc.md").write_text("# Hello", encoding="utf-8")

            docs = module.scan_markdown_docs(source_dir)
            self.assertEqual(len(docs), 1)
            self.assertEqual(docs[0]["title"], "Hello")


class PartialMatchSelectionTests(unittest.TestCase):
    """Test partial --doc-id match exports only matching nodes."""

    def test_partial_match_exports_only_matching(self) -> None:
        module = _load_module("export_xiliu")
        args = module.parse_args(
            ["--doc-url", "https://flowus.cn/demo", "--output", "out",
             "--doc-id", "doc-a", "--doc-id", "stale-id"]
        )
        mock_nodes = [
            module.FlowUsNode(id="doc-a", title="Doc A", is_dir=False),
            module.FlowUsNode(id="doc-b", title="Doc B", is_dir=False),
        ]
        # Mock to succeed on doc-a: need get_doc to return valid blocks
        mock_doc = {"code": 200, "data": {"blocks": {
            "doc-a": {"type": 0, "title": "Doc A", "data": {"segments": [{"type": 0, "text": "Doc A", "enhancer": {}}]}, "subNodes": ["text"]},
            "text": {"type": 1, "data": {"segments": [{"type": 0, "text": "content", "enhancer": {}}]}, "subNodes": []},
        }}}
        with patch.object(module, "build_toc_tree", return_value=mock_nodes), \
             patch.object(module, "read_auth_payload", return_value={"token": "fake", "cookies": []}), \
             patch.object(module, "FlowUsClient") as MockClient:
            instance = MockClient.return_value
            instance.get_doc.return_value = mock_doc
            result = module.export_flowus(args)
            # Should export doc-a, not raise error (partial match is OK)
            self.assertEqual(result["exported"], 1)


class EmptySourceNoSelectionTests(unittest.TestCase):
    """Test that no --doc-id proceeds with all documents."""

    def test_no_selection_processes_all(self) -> None:
        module = _load_module("export_xiliu")
        args = module.parse_args(
            ["--doc-url", "https://flowus.cn/demo", "--output", "out"]
        )
        mock_nodes = [
            module.FlowUsNode(id="doc-1", title="Doc 1", is_dir=False),
            module.FlowUsNode(id="doc-2", title="Doc 2", is_dir=False),
        ]
        mock_doc = {"code": 200, "data": {"blocks": {"root": {"type": 0, "title": "T", "data": {"segments": [{"type": 0, "text": "T", "enhancer": {}}]}, "subNodes": []}}}}
        with patch.object(module, "build_toc_tree", return_value=mock_nodes), \
             patch.object(module, "read_auth_payload", return_value={"token": "fake", "cookies": []}), \
             patch.object(module, "FlowUsClient") as MockClient:
            instance = MockClient.return_value
            instance.get_doc.return_value = mock_doc
            result = module.export_flowus(args)
            self.assertEqual(result["totalNodes"], 2)


class LogSanitizationBehaviorTests(unittest.TestCase):
    """Behavior tests: verify emit() output contains no sensitive data."""

    def test_extract_appid_no_jwt_payload_in_log(self) -> None:
        """Verify extract_appid_from_token does not log JWT payload content."""
        module = _load_module("import_flowus")
        # A minimal JWT: header.payload.signature (payload = {"key":"abc"})
        import base64
        payload_json = '{"key":"abc123","sub":"user@example.com"}'
        payload_b64 = base64.urlsafe_b64encode(payload_json.encode()).decode().rstrip("=")
        token = f"eyJhbGciOiJIUzI1NiJ9.{payload_b64}.sig"

        log_records = []
        class Handler(logging.Handler):
            def emit(self, record):
                log_records.append(record.getMessage())

        handler = Handler()
        logger = logging.getLogger("wandao")
        logger.addHandler(handler)
        try:
            module.extract_appid_from_token(token)
        finally:
            logger.removeHandler(handler)

        for msg in log_records:
            # JWT payload content must not appear in logs
            self.assertNotIn("user@example.com", msg)
            self.assertNotIn("abc123", msg)


class ImageUploadFailureTests(unittest.TestCase):
    """Test that image upload failures are handled gracefully."""

    def test_upload_failure_does_not_crash_export(self) -> None:
        """Verify export continues when image download fails."""
        module = _load_module("export_xiliu")
        args = module.parse_args(
            ["--doc-url", "https://flowus.cn/demo", "--output", "out"]
        )
        mock_nodes = [
            module.FlowUsNode(id="doc-1", title="Doc 1", is_dir=False),
        ]
        # Doc with an image block that will fail to download
        mock_doc = {
            "code": 200,
            "data": {
                "blocks": {
                    "doc-1": {
                        "type": 0, "title": "Doc 1",
                        "data": {"segments": [{"type": 0, "text": "Doc 1", "enhancer": {}}]},
                        "subNodes": ["img-1"],
                    },
                    "img-1": {
                        "type": 14,
                        "data": {"ossName": "oss/fail/img.png", "segments": []},
                        "subNodes": [],
                    },
                }
            },
        }
        with patch.object(module, "build_toc_tree", return_value=mock_nodes), \
             patch.object(module, "read_auth_payload", return_value={"token": "fake", "cookies": []}), \
             patch.object(module, "FlowUsClient") as MockClient:
            instance = MockClient.return_value
            instance.get_doc.return_value = mock_doc
            # Make image download fail
            instance.request.side_effect = module.FlowUsError("download failed")
            result = module.export_flowus(args)
            # Export should succeed despite image failure
            self.assertEqual(result["exported"], 1)
            self.assertEqual(result["resourceFailureCount"], 1)
            self.assertEqual(result["failures"][0]["stage"], "resources")


class CompletionMessageTests(unittest.TestCase):
    """Regression: completion logs use 未通过, avoiding frontend false errors."""

    def test_export_completion_uses_wei_tong_guo(self) -> None:
        module = _load_module("export_xiliu")
        messages = []

        class Client:
            def get_doc(self, _doc_id):
                return {
                    "code": 200,
                    "data": {"blocks": {
                        "doc": {
                            "type": 0,
                            "title": "Doc",
                            "data": {"segments": [{"type": 0, "text": "Doc", "enhancer": {}}]},
                            "subNodes": [],
                        }
                    }},
                }

        with tempfile.TemporaryDirectory() as tmp:
            args = module.parse_args([
                "--doc-url", "https://flowus.cn/doc", "--output", str(Path(tmp) / "out")
            ])
            with patch.object(module, "FlowUsClient", lambda *_: Client()),                  patch.object(module, "build_toc_tree", return_value=[
                     module.FlowUsNode(id="doc", title="Doc", is_dir=False)
                 ]),                  patch.object(module, "emit", side_effect=lambda message, **_kwargs: messages.append(message)):
                result = module.export_flowus(args)

        self.assertEqual(result["outcome"], "completed")
        completion = next(message for message in messages if message.startswith("导出完成"))
        self.assertIn("未通过 0", completion)
        self.assertNotIn("失败", completion)


class TocSelectableTests(unittest.TestCase):
    """Verify toc_json output includes selectable: True for all nodes."""

    def test_toc_nodes_have_selectable(self) -> None:
        module = _load_module("export_xiliu")
        mock_nodes = [
            module.FlowUsNode(id="root", title="Root", is_dir=True, parent_id=""),
            module.FlowUsNode(id="child", title="Child", is_dir=False, parent_id="root"),
        ]
        with patch.object(module, "FlowUsClient") as MockClient, \
             patch.object(module, "read_auth_payload", return_value={"token": "fake", "cookies": []}):
            instance = MockClient.return_value
            # Mock get_doc for build_toc_tree
            instance.get_doc.return_value = {
                "code": 200,
                "data": {"blocks": {
                    "root": {"type": 0, "title": "Root", "subNodes": ["child"], "data": {}},
                    "child": {"type": 0, "title": "Child", "subNodes": [], "data": {}},
                }}
            }
            args = module.parse_args(["--doc-url", "https://flowus.cn/root", "--output", "/tmp/out", "--scan-toc"])
            result = module.toc_json(args)
            for node in result["nodes"]:
                self.assertTrue(node.get("selectable"), f"Node {node['id']} missing selectable=True")


class ImportLogSanitizationTests(unittest.TestCase):
    """Verify import logs don't leak doc_id or sensitive info."""

    def test_no_doc_id_in_emit_messages(self) -> None:
        source = (REPO_ROOT / "plugins/xiliu/backend/import_flowus.py").read_text(encoding="utf-8")
        import re
        # Find emit() calls that format doc_id or space_id into the message
        # Allow event="..." and level="..." kwargs, but not in the message string
        pattern = r'emit\(f["\'][^"\']*\{(?:doc_id|space_id)\}'
        matches = re.findall(pattern, source)
        self.assertEqual(matches, [], f"Found emit calls leaking IDs: {matches}")


class ParseFlowusUrlTests(unittest.TestCase):
    """Test parse_flowus_url extracts document ID correctly."""

    def test_standard_url(self) -> None:
        module = _load_module("export_xiliu")
        self.assertEqual(module.parse_flowus_url("https://flowus.cn/abc123"), "abc123")

    def test_url_with_trailing_slash(self) -> None:
        module = _load_module("export_xiliu")
        self.assertEqual(module.parse_flowus_url("https://flowus.cn/abc123/"), "abc123")

    def test_url_with_query_params(self) -> None:
        module = _load_module("export_xiliu")
        self.assertEqual(module.parse_flowus_url("https://flowus.cn/abc123?v=1"), "abc123")

    def test_url_with_fragment(self) -> None:
        module = _load_module("export_xiliu")
        self.assertEqual(module.parse_flowus_url("https://flowus.cn/abc123#section"), "abc123")

    def test_url_with_nested_path(self) -> None:
        module = _load_module("export_xiliu")
        self.assertEqual(module.parse_flowus_url("https://flowus.cn/space/doc456"), "doc456")

    def test_invalid_url_raises(self) -> None:
        module = _load_module("export_xiliu")
        with self.assertRaises(module.FlowUsError):
            module.parse_flowus_url("not-a-url")

    def test_empty_path_raises(self) -> None:
        module = _load_module("export_xiliu")
        with self.assertRaises(module.FlowUsError):
            module.parse_flowus_url("https://flowus.cn/")


class SafeFilenameTests(unittest.TestCase):
    """Test safe_filename sanitizes forbidden characters."""

    def test_normal_name_unchanged(self) -> None:
        module = _load_module("export_xiliu")
        self.assertEqual(module.safe_filename("hello world"), "hello world")

    def test_forbidden_chars_replaced(self) -> None:
        module = _load_module("export_xiliu")
        result = module.safe_filename('file<>:"/\\|?*name')
        for ch in '<>:"/\\|?*':
            self.assertNotIn(ch, result)

    def test_empty_returns_fallback(self) -> None:
        module = _load_module("export_xiliu")
        self.assertEqual(module.safe_filename(""), "untitled")
        self.assertEqual(module.safe_filename("   "), "untitled")

    def test_custom_fallback(self) -> None:
        module = _load_module("export_xiliu")
        self.assertEqual(module.safe_filename("", fallback="default"), "default")


class ConvertBlocksTests(unittest.TestCase):
    """Test convert_flowus_blocks_to_markdown block type handling."""

    def _make_response(self, blocks: dict) -> dict:
        return {"code": 200, "data": {"blocks": blocks}}

    def test_normal_text_paragraph(self) -> None:
        module = _load_module("export_xiliu")
        blocks = {
            "root": {"type": 0, "title": "Page", "data": {"segments": []}, "subNodes": ["txt"]},
            "txt": {"type": 1, "data": {"segments": [{"type": 0, "text": "Hello world", "enhancer": {}}]}, "subNodes": []},
        }
        md, _, _ = module.convert_flowus_blocks_to_markdown(self._make_response(blocks), "root")
        self.assertIn("Hello world", md)

    def test_heading_with_level(self) -> None:
        module = _load_module("export_xiliu")
        blocks = {
            "root": {"type": 0, "title": "Page", "data": {"segments": []}, "subNodes": ["h"]},
            "h": {"type": 7, "data": {"level": 2, "segments": [{"type": 0, "text": "Title", "enhancer": {}}]}, "subNodes": []},
        }
        md, _, _ = module.convert_flowus_blocks_to_markdown(self._make_response(blocks), "root")
        self.assertIn("## Title", md)

    def test_bold_title_as_heading(self) -> None:
        module = _load_module("export_xiliu")
        blocks = {
            "root": {"type": 0, "title": "Page", "data": {"segments": []}, "subNodes": ["b"]},
            "b": {"type": 5, "data": {"segments": [{"type": 0, "text": "Bold Title", "enhancer": {}}]}, "subNodes": []},
        }
        md, _, _ = module.convert_flowus_blocks_to_markdown(self._make_response(blocks), "root")
        self.assertIn("## Bold Title", md)

    def test_divider(self) -> None:
        module = _load_module("export_xiliu")
        blocks = {
            "root": {"type": 0, "title": "Page", "data": {"segments": []}, "subNodes": ["d"]},
            "d": {"type": 9, "data": {"segments": []}, "subNodes": []},
        }
        md, _, _ = module.convert_flowus_blocks_to_markdown(self._make_response(blocks), "root")
        self.assertIn("---", md)

    def test_inline_bold_formatting(self) -> None:
        module = _load_module("export_xiliu")
        blocks = {
            "root": {"type": 0, "title": "Page", "data": {"segments": []}, "subNodes": ["txt"]},
            "txt": {"type": 1, "data": {"segments": [{"type": 0, "text": "bold", "enhancer": {"bold": True}}]}, "subNodes": []},
        }
        md, _, _ = module.convert_flowus_blocks_to_markdown(self._make_response(blocks), "root")
        self.assertIn("**bold**", md)

    def test_inline_code_formatting(self) -> None:
        module = _load_module("export_xiliu")
        blocks = {
            "root": {"type": 0, "title": "Page", "data": {"segments": []}, "subNodes": ["txt"]},
            "txt": {"type": 1, "data": {"segments": [{"type": 0, "text": "code", "enhancer": {"code": True}}]}, "subNodes": []},
        }
        md, _, _ = module.convert_flowus_blocks_to_markdown(self._make_response(blocks), "root")
        self.assertIn("`code`", md)

    def test_non_200_response_returns_empty(self) -> None:
        module = _load_module("export_xiliu")
        md, count, failures = module.convert_flowus_blocks_to_markdown({"code": 401, "data": {}})
        self.assertEqual(md, "")
        self.assertEqual(count, 0)

    def test_empty_blocks_returns_empty(self) -> None:
        module = _load_module("export_xiliu")
        md, _, _ = module.convert_flowus_blocks_to_markdown(self._make_response({}))
        self.assertEqual(md, "")

    def test_page_title_as_h1(self) -> None:
        module = _load_module("export_xiliu")
        blocks = {
            "root": {"type": 0, "title": "My Page", "data": {"segments": [{"type": 0, "text": "My Page", "enhancer": {}}]}, "subNodes": []},
        }
        md, _, _ = module.convert_flowus_blocks_to_markdown(self._make_response(blocks), "root")
        self.assertTrue(md.startswith("# My Page"))


class ExtractLocalImagesAcceptanceTests(unittest.TestCase):
    """Test extract_local_images correctly extracts valid local images."""

    def test_valid_relative_image(self) -> None:
        module = _load_module("import_flowus")
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp) / "source"
            source_dir.mkdir()
            (source_dir / "photo.png").write_bytes(b"\x89PNG")
            images = module.extract_local_images("![alt](photo.png)", source_dir)
            self.assertEqual(len(images), 1)
            self.assertEqual(images[0][0], "photo.png")

    def test_remote_url_skipped(self) -> None:
        module = _load_module("import_flowus")
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp) / "source"
            source_dir.mkdir()
            images = module.extract_local_images("![alt](https://example.com/img.png)", source_dir)
            self.assertEqual(len(images), 0)

    def test_nonexistent_file_skipped(self) -> None:
        module = _load_module("import_flowus")
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp) / "source"
            source_dir.mkdir()
            images = module.extract_local_images("![alt](missing.png)", source_dir)
            self.assertEqual(len(images), 0)

    def test_multiple_images(self) -> None:
        module = _load_module("import_flowus")
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp) / "source"
            source_dir.mkdir()
            (source_dir / "a.png").write_bytes(b"\x89PNG")
            (source_dir / "b.jpg").write_bytes(b"\xff\xd8\xff")
            images = module.extract_local_images("![a](a.png) ![b](b.jpg)", source_dir)
            self.assertEqual(len(images), 2)


class TocJsonStructureTests(unittest.TestCase):
    """Test toc_json output has correct structure."""

    def test_toc_output_has_required_fields(self) -> None:
        module = _load_module("export_xiliu")
        mock_nodes = [
            module.FlowUsNode(id="root", title="Root", is_dir=True, parent_id=""),
            module.FlowUsNode(id="child-1", title="Child 1", is_dir=False, parent_id="root"),
            module.FlowUsNode(id="child-2", title="Child 2", is_dir=False, parent_id="root"),
        ]
        with patch.object(module, "FlowUsClient") as MockClient, \
             patch.object(module, "read_auth_payload", return_value={"token": "fake", "cookies": []}):
            instance = MockClient.return_value
            instance.get_doc.return_value = {
                "code": 200,
                "data": {"blocks": {
                    "root": {"type": 0, "title": "Root", "subNodes": ["child-1", "child-2"], "data": {}},
                    "child-1": {"type": 0, "title": "Child 1", "subNodes": [], "data": {}},
                    "child-2": {"type": 0, "title": "Child 2", "subNodes": [], "data": {}},
                }}
            }
            args = module.parse_args(["--doc-url", "https://flowus.cn/root", "--output", "/tmp/out", "--scan-toc"])
            result = module.toc_json(args)

            self.assertEqual(result["nodeCount"], 3)
            self.assertEqual(result["rootId"], "root")
            self.assertEqual(result["provider"], "flowus")
            # Check per-level index
            self.assertEqual(result["nodes"][0]["index"], 1)  # root
            self.assertEqual(result["nodes"][1]["index"], 1)  # child-1 (first child)
            self.assertEqual(result["nodes"][2]["index"], 2)  # child-2 (second child)
            # All nodes should have parentId
            self.assertEqual(result["nodes"][0]["parentId"], "")
            self.assertEqual(result["nodes"][1]["parentId"], "root")
            self.assertEqual(result["nodes"][2]["parentId"], "root")


class ExportNoDirectoryLogTests(unittest.TestCase):
    """Regression: export should not emit '创建目录' during batch export loop."""

    def test_no_create_dir_in_batch_loop(self) -> None:
        source = (REPO_ROOT / "plugins/xiliu/backend/export_xiliu.py").read_text(encoding="utf-8")
        import re
        # Match emit calls with "创建目录:" pattern (batch loop style), not "仅创建目录" (empty page warning)
        matches = re.findall(r'emit\(f"\[.*创建目录:', source)
        self.assertEqual(matches, [], f"Found emit calls with '创建目录:': {matches}")


class ExportSingleCompletionMessageTests(unittest.TestCase):
    """Regression: export should emit exactly one completion message."""

    def test_only_one_completion_emit(self) -> None:
        source = (REPO_ROOT / "plugins/xiliu/backend/export_xiliu.py").read_text(encoding="utf-8")
        import re
        matches = re.findall(r'emit\([^)]*导出完成', source)
        self.assertEqual(len(matches), 1, f"Expected 1 completion emit, found {len(matches)}")


class CosTlsVerificationTests(unittest.TestCase):
    """Verify COS upload uses default TLS verification (no bypass)."""

    def test_no_tls_bypass_in_import_flowus(self) -> None:
        source = (REPO_ROOT / "plugins/xiliu/backend/import_flowus.py").read_text(encoding="utf-8")
        self.assertNotIn("ssl.CERT_NONE", source, "ssl.CERT_NONE should not be used")
        self.assertNotIn("check_hostname = False", source, "check_hostname bypass should not be used")
        self.assertNotIn("check_hostname=False", source, "check_hostname bypass should not be used")
        self.assertNotIn("_create_unverified_context", source, "unverified context should not be used")
        # Verify no verify=False pattern (but allow in comments/strings about rejection)
        import re
        matches = re.findall(r'verify\s*=\s*False', source)
        self.assertEqual(matches, [], f"Found verify=False: {matches}")

    def test_no_tls_bypass_in_export_xiliu(self) -> None:
        source = (REPO_ROOT / "plugins/xiliu/backend/export_xiliu.py").read_text(encoding="utf-8")
        self.assertNotIn("ssl.CERT_NONE", source)
        self.assertNotIn("check_hostname = False", source)
        self.assertNotIn("check_hostname=False", source)
        self.assertNotIn("_create_unverified_context", source)

    def test_cos_upload_uses_urlopen_default_tls(self) -> None:
        """Verify upload_to_cos uses urllib.request.urlopen without custom SSL context."""
        source = (REPO_ROOT / "plugins/xiliu/backend/import_flowus.py").read_text(encoding="utf-8")
        # The upload_to_cos function should use urlopen without passing a custom context
        import re
        # Find the upload_to_cos function body
        func_match = re.search(r'def upload_to_cos\(.*?\n(.*?)(?=\ndef |\Z)', source, re.DOTALL)
        self.assertIsNotNone(func_match, "Could not find upload_to_cos function")
        func_body = func_match.group(1)
        # Should not create custom SSL context
        self.assertNotIn("ssl._create_unverified_context", func_body)
        self.assertNotIn("CERT_NONE", func_body)
        # Should use urlopen (default TLS)
        self.assertIn("urlopen", func_body)


class ImportImageUploadFailureTests(unittest.TestCase):
    """Test that import handles image upload failures gracefully."""

    def test_import_upload_local_image_timeout_is_configurable(self) -> None:
        """Verify upload_local_image accepts timeout parameter."""
        source = (REPO_ROOT / "plugins/xiliu/backend/import_flowus.py").read_text(encoding="utf-8")
        self.assertIn("def upload_local_image(client: FlowUsClient, space_id: str, image_path: Path, timeout: int = 60)", source)

    def test_upload_to_cos_timeout_is_configurable(self) -> None:
        """Verify upload_to_cos accepts timeout parameter."""
        source = (REPO_ROOT / "plugins/xiliu/backend/import_flowus.py").read_text(encoding="utf-8")
        self.assertIn("def upload_to_cos(upload_info: dict[str, Any], file_path: Path, appid: str = \"\", timeout: int = 60)", source)

    def test_upload_to_cos_passes_timeout_to_urlopen(self) -> None:
        """Verify upload_to_cos passes timeout parameter to urlopen."""
        source = (REPO_ROOT / "plugins/xiliu/backend/import_flowus.py").read_text(encoding="utf-8")
        import re
        func_match = re.search(r'def upload_to_cos\(.*?\n(.*?)(?=\ndef |\Z)', source, re.DOTALL)
        self.assertIsNotNone(func_match)
        func_body = func_match.group(1)
        # Should use the timeout parameter, not hardcoded 60
        self.assertIn("timeout=timeout", func_body)
        # Should NOT have hardcoded timeout=60 in urlopen call
        self.assertNotIn("urlopen(req, timeout=60)", func_body)


class TaskTimeoutProviderTests(unittest.TestCase):
    """Verify task_timeout is exposed in provider.json for UI configuration."""

    def test_provider_has_task_timeout_field(self) -> None:
        import json
        provider_path = REPO_ROOT / "plugins/xiliu/providers/xiliu/provider.json"
        provider = json.loads(provider_path.read_text(encoding="utf-8"))
        fields = {f["name"]: f for f in provider.get("fields", [])}
        self.assertIn("task_timeout", fields, "task_timeout field missing from provider.json")
        field = fields["task_timeout"]
        self.assertEqual(field["arg"], "--task-timeout")
        self.assertEqual(field["default"], 120)
        self.assertIn("import", field["actions"])


class ApiErrorSanitizationBehaviorTests(unittest.TestCase):
    """Behavioral tests: verify API error messages don't leak sensitive data."""

    def test_api_error_msg_extracts_msg_only(self) -> None:
        """_api_error_msg should return msg and code, never the full result."""
        module = _load_module("import_flowus")
        # Simulate a response with sensitive data
        result = {
            "code": 403,
            "msg": "Forbidden",
            "data": {
                "url": "https://cos.example.com/signed?token=SECRET123",
                "credentials": {"accessKeyId": "AKID", "accessKeySecret": "SECRET"},
            },
        }
        error_msg = module._api_error_msg(result)
        self.assertIn("Forbidden", error_msg)
        self.assertIn("403", error_msg)
        self.assertNotIn("SECRET123", error_msg)
        self.assertNotIn("AKID", error_msg)
        self.assertNotIn("cos.example.com", error_msg)

    def test_api_error_msg_no_msg_field(self) -> None:
        """When msg is missing, should return code only, not the full result."""
        module = _load_module("import_flowus")
        result = {
            "code": 500,
            "data": {"signedUrl": "https://secret-url.example.com"},
        }
        error_msg = module._api_error_msg(result)
        self.assertIn("500", error_msg)
        self.assertNotIn("secret-url", error_msg)

    def test_api_error_msg_empty_result(self) -> None:
        """Empty result should not crash."""
        module = _load_module("import_flowus")
        error_msg = module._api_error_msg({})
        self.assertIn("code=?", error_msg)

    def test_api_helpers_never_include_raw_response_in_errors(self) -> None:
        module = _load_module("import_flowus")
        sensitive_response = {
            "code": 403,
            "data": {
                "token": "SECRET_TOKEN",
                "url": "https://signed.example.com/file?signature=SECRET_SIGNATURE",
            },
        }
        calls = [
            (module.fetch_user_info, (MagicMock(get_json=MagicMock(return_value=sensitive_response)),)),
            (module.fetch_user_spaces, (MagicMock(get_json=MagicMock(return_value=sensitive_response)), "user-1")),
            (module.fetch_space_root, (MagicMock(get_json=MagicMock(return_value=sensitive_response)), "space-1")),
            (module.fetch_page_blocks, (MagicMock(get_json=MagicMock(return_value=sensitive_response)), "page-1")),
        ]

        for helper, args in calls:
            with self.subTest(helper=helper.__name__):
                with self.assertRaises(module.FlowUsError) as ctx:
                    helper(*args)
                error = str(ctx.exception)
                self.assertIn("403", error)
                self.assertNotIn("SECRET_TOKEN", error)
                self.assertNotIn("SECRET_SIGNATURE", error)
                self.assertNotIn("signed.example.com", error)


class CheckpointBehaviorTests(unittest.TestCase):
    """Behavioral tests for checkpoint resume/retry-failed filtering."""

    def test_export_retry_failed_keeps_only_failed(self) -> None:
        """Behavioral: export with --retry-failed processes only failed items."""
        module = _load_module("export_xiliu")
        args = module.parse_args(
            ["--doc-url", "https://flowus.cn/demo", "--output", "/tmp/out",
             "--retry-failed",
             "--checkpoint-file", "/tmp/out/.wandao/checkpoint.sqlite",
             "--checkpoint-task-id", "test-task"]
        )
        mock_nodes = [
            module.FlowUsNode(id="ok-doc", title="OK Doc", is_dir=False),
            module.FlowUsNode(id="fail-doc", title="Fail Doc", is_dir=False),
            module.FlowUsNode(id="new-doc", title="New Doc", is_dir=False),
        ]
        mock_doc = {"code": 200, "data": {"blocks": {"root": {"type": 0, "title": "T", "data": {"segments": [{"type": 0, "text": "T", "enhancer": {}}]}, "subNodes": []}}}}

        # Mock checkpoint that reports statuses
        mock_checkpoint = MagicMock()
        mock_checkpoint.item_status.side_effect = lambda key: {
            "xiliu:doc:ok-doc": "completed",
            "xiliu:doc:fail-doc": "failed",
            "xiliu:doc:new-doc": "pending",
        }.get(key, "pending")
        mock_checkpoint.start_task = MagicMock()
        mock_checkpoint.upsert_item = MagicMock()
        mock_checkpoint.start_item = MagicMock()
        mock_checkpoint.complete_item = MagicMock()
        mock_checkpoint.fail_item = MagicMock()
        mock_checkpoint.complete_task = MagicMock()
        mock_checkpoint.close = MagicMock()

        with patch.object(module, "build_toc_tree", return_value=mock_nodes), \
             patch.object(module, "read_auth_payload", return_value={"token": "fake", "cookies": []}), \
             patch.object(module, "FlowUsClient") as MockClient, \
             patch.object(module, "open_checkpoint_from_args", return_value=mock_checkpoint):
            instance = MockClient.return_value
            instance.get_doc.return_value = mock_doc
            result = module.export_flowus(args)
            # Should only process the failed doc
            self.assertEqual(result["exported"], 1)

    def test_export_resume_skips_completed(self) -> None:
        """Behavioral: export with --resume skips completed items via checkpoint filter."""
        module = _load_module("export_xiliu")
        args = module.parse_args(
            ["--doc-url", "https://flowus.cn/demo", "--output", "/tmp/out",
             "--resume",
             "--checkpoint-file", "/tmp/out/.wandao/checkpoint.sqlite",
             "--checkpoint-task-id", "test-task"]
        )
        mock_nodes = [
            module.FlowUsNode(id="done-doc", title="Done Doc", is_dir=False),
            module.FlowUsNode(id="pending-doc", title="Pending Doc", is_dir=False),
        ]
        mock_doc = {"code": 200, "data": {"blocks": {"root": {"type": 0, "title": "T", "data": {"segments": [{"type": 0, "text": "T", "enhancer": {}}]}, "subNodes": []}}}}

        mock_checkpoint = MagicMock()
        mock_checkpoint.item_status.side_effect = lambda key: {
            "xiliu:doc:done-doc": "completed",
            "xiliu:doc:pending-doc": "pending",
        }.get(key, "pending")
        mock_checkpoint.start_task = MagicMock()
        mock_checkpoint.upsert_item = MagicMock()
        mock_checkpoint.start_item = MagicMock()
        mock_checkpoint.complete_item = MagicMock()
        mock_checkpoint.fail_item = MagicMock()
        mock_checkpoint.complete_task = MagicMock()
        mock_checkpoint.close = MagicMock()

        with patch.object(module, "build_toc_tree", return_value=mock_nodes), \
             patch.object(module, "read_auth_payload", return_value={"token": "fake", "cookies": []}), \
             patch.object(module, "FlowUsClient") as MockClient, \
             patch.object(module, "open_checkpoint_from_args", return_value=mock_checkpoint):
            instance = MockClient.return_value
            instance.get_doc.return_value = mock_doc
            result = module.export_flowus(args)
            # Resume filter should keep only non-completed items (pending-doc)
            # pending-doc gets exported, done-doc is filtered out before the loop
            self.assertEqual(result["exported"], 1)
            self.assertEqual(result["totalNodes"], 1)  # filtered to 1 node


class LogSanitizationFullBehaviorTests(unittest.TestCase):
    """Full behavioral tests: verify error messages from API calls don't leak."""

    def test_create_page_error_sanitized(self) -> None:
        """Behavioral: create_empty_page error message is sanitized."""
        module = _load_module("import_flowus")
        with patch.object(module, "FlowUsClient") as MockClient:
            instance = MockClient.return_value
            # Simulate API response with sensitive data
            error_response = json.dumps({
                "code": 403,
                "msg": "permission denied",
                "data": {"token": "SECRET", "url": "https://signed.example.com"},
            }).encode()
            instance.request.return_value = error_response
            with self.assertRaises(module.FlowUsError) as ctx:
                module.create_empty_page(instance, "space-1", "parent-1", "Test")
            error_str = str(ctx.exception)
            self.assertIn("permission denied", error_str)
            self.assertNotIn("SECRET", error_str)
            self.assertNotIn("signed.example.com", error_str)


# ===========================================================================
# 新增：补充缺失的测试覆盖
# ===========================================================================


class ReadAuthPayloadTests(unittest.TestCase):
    """Test read_auth_payload error paths."""

    def test_missing_file_raises(self) -> None:
        module = _load_module("export_xiliu")
        with self.assertRaises(module.FlowUsError) as ctx:
            module.read_auth_payload(Path("/nonexistent/.flowus_auth.json"))
        self.assertIn("不存在", str(ctx.exception))

    def test_missing_token_raises(self) -> None:
        module = _load_module("export_xiliu")
        with tempfile.TemporaryDirectory() as tmp:
            auth_file = Path(tmp) / "auth.json"
            auth_file.write_text('{"cookies": []}', encoding="utf-8")
            with self.assertRaises(module.FlowUsError) as ctx:
                module.read_auth_payload(auth_file)
            self.assertIn("Token", str(ctx.exception))

    def test_valid_payload_returns_dict(self) -> None:
        module = _load_module("export_xiliu")
        with tempfile.TemporaryDirectory() as tmp:
            auth_file = Path(tmp) / "auth.json"
            auth_file.write_text('{"token": "abc", "cookies": []}', encoding="utf-8")
            result = module.read_auth_payload(auth_file)
            self.assertEqual(result["token"], "abc")


class BuildImageUrlTests(unittest.TestCase):
    """Test build_image_url."""

    def test_http_passthrough(self) -> None:
        module = _load_module("export_xiliu")
        self.assertEqual(module.build_image_url("https://cdn.example.com/img.png"), "https://cdn.example.com/img.png")

    def test_oss_name_builds_cdn_url(self) -> None:
        module = _load_module("export_xiliu")
        result = module.build_image_url("oss/abc/img.png")
        self.assertEqual(result, "https://cdn2.flowus.cn/oss/abc/img.png")


class IsFlowusCookieTests(unittest.TestCase):
    """Test is_flowus_cookie domain/name matching."""

    def test_valid_cookie(self) -> None:
        module = _load_module("export_xiliu")
        cookie = {"domain": ".flowus.cn", "name": "next_auth", "value": "tok"}
        self.assertTrue(module.is_flowus_cookie(cookie))

    def test_wrong_domain_rejected(self) -> None:
        module = _load_module("export_xiliu")
        cookie = {"domain": ".example.com", "name": "next_auth", "value": "tok"}
        self.assertFalse(module.is_flowus_cookie(cookie))

    def test_wrong_name_rejected(self) -> None:
        module = _load_module("export_xiliu")
        cookie = {"domain": ".flowus.cn", "name": "session_id", "value": "tok"}
        self.assertFalse(module.is_flowus_cookie(cookie))

    def test_locale_cookie_accepted(self) -> None:
        module = _load_module("export_xiliu")
        cookie = {"domain": "flowus.cn", "name": "locale", "value": "zh-CN"}
        self.assertTrue(module.is_flowus_cookie(cookie))


class ImageSaverTests(unittest.TestCase):
    """Test ImageSaver.save_image behavior."""

    def test_empty_oss_name_returns_empty(self) -> None:
        module = _load_module("export_xiliu")
        with tempfile.TemporaryDirectory() as tmp:
            saver = module.ImageSaver(client=MagicMock(), output_dir=Path(tmp), doc_id="doc1")
            result = saver.save_image("block1", "", "alt")
            self.assertEqual(result, "")

    def test_cached_oss_name_returns_same_path(self) -> None:
        module = _load_module("export_xiliu")
        with tempfile.TemporaryDirectory() as tmp:
            saver = module.ImageSaver(client=MagicMock(), output_dir=Path(tmp), doc_id="doc1")
            saver.saved["oss/img.png"] = "assets/001-img.png"
            result = saver.save_image("block1", "oss/img.png", "alt")
            self.assertEqual(result, "assets/001-img.png")

    def test_download_failure_records_failure(self) -> None:
        module = _load_module("export_xiliu")
        with tempfile.TemporaryDirectory() as tmp:
            mock_client = MagicMock()
            mock_client.request.side_effect = module.FlowUsError("download failed")
            saver = module.ImageSaver(client=mock_client, output_dir=Path(tmp), doc_id="doc1")
            result = saver.save_image("block1", "oss/fail/img.png", "alt")
            self.assertEqual(result, "")
            self.assertEqual(len(saver.failures), 1)
            self.assertEqual(saver.image_count, 1)

    def test_successful_download_saves_file(self) -> None:
        module = _load_module("export_xiliu")
        with tempfile.TemporaryDirectory() as tmp:
            mock_client = MagicMock()
            # get_signed_url returns a URL, then download returns bytes
            mock_client.request.return_value = b"\x89PNG"
            saver = module.ImageSaver(client=mock_client, output_dir=Path(tmp), doc_id="doc1")
            with patch.object(module, "get_signed_url", return_value="https://cdn.example.com/img.png"):
                result = saver.save_image("block1", "oss/img.png", "alt")
            self.assertTrue(result.startswith("assets/"))
            self.assertTrue(result.endswith(".png"))
            self.assertEqual(saver.image_count, 1)
            # File should exist on disk
            self.assertTrue((Path(tmp) / result).exists())


class DownloadImageDataTests(unittest.TestCase):
    """Test download_image_data response handling."""

    def test_empty_response_returns_none(self) -> None:
        module = _load_module("export_xiliu")
        mock_client = MagicMock()
        mock_client.request.return_value = b""
        with patch.object(module, "get_signed_url", return_value="https://cdn.example.com/img"):
            result = module.download_image_data(mock_client, "block1", "oss/img.png")
        self.assertIsNone(result)

    def test_binary_response_returns_bytes(self) -> None:
        module = _load_module("export_xiliu")
        mock_client = MagicMock()
        mock_client.request.return_value = b"\x89PNG\x00\x01\x02"
        with patch.object(module, "get_signed_url", return_value="https://cdn.example.com/img"):
            result = module.download_image_data(mock_client, "block1", "oss/img.png")
        self.assertEqual(result, b"\x89PNG\x00\x01\x02")

    def test_base64_json_response_decoded(self) -> None:
        module = _load_module("export_xiliu")
        import base64
        img_data = b"\x89PNG" * 50  # >100 bytes so base64 str > 100 chars
        b64_str = base64.b64encode(img_data).decode()
        self.assertGreater(len(b64_str), 100)
        mock_client = MagicMock()
        mock_client.request.return_value = json.dumps({"data": b64_str}).encode()
        with patch.object(module, "get_signed_url", return_value="https://cdn.example.com/img"):
            result = module.download_image_data(mock_client, "block1", "oss/img.png")
        self.assertEqual(result, img_data)

    def test_exception_returns_none(self) -> None:
        module = _load_module("export_xiliu")
        mock_client = MagicMock()
        mock_client.request.side_effect = Exception("network error")
        with patch.object(module, "get_signed_url", return_value="https://cdn.example.com/img"):
            result = module.download_image_data(mock_client, "block1", "oss/img.png")
        self.assertIsNone(result)


class ConvertBlocksEdgeCaseTests(unittest.TestCase):
    """Additional edge cases for convert_flowus_blocks_to_markdown."""

    def _make_response(self, blocks: dict) -> dict:
        return {"code": 200, "data": {"blocks": blocks}}

    def test_italic_formatting(self) -> None:
        module = _load_module("export_xiliu")
        blocks = {
            "root": {"type": 0, "title": "Page", "data": {"segments": []}, "subNodes": ["txt"]},
            "txt": {"type": 1, "data": {"segments": [{"type": 0, "text": "italic", "enhancer": {"italic": True}}]}, "subNodes": []},
        }
        md, _, _ = module.convert_flowus_blocks_to_markdown(self._make_response(blocks), "root")
        self.assertIn("*italic*", md)

    def test_strikethrough_formatting(self) -> None:
        module = _load_module("export_xiliu")
        blocks = {
            "root": {"type": 0, "title": "Page", "data": {"segments": []}, "subNodes": ["txt"]},
            "txt": {"type": 1, "data": {"segments": [{"type": 0, "text": "deleted", "enhancer": {"strikethrough": True}}]}, "subNodes": []},
        }
        md, _, _ = module.convert_flowus_blocks_to_markdown(self._make_response(blocks), "root")
        self.assertIn("~~deleted~~", md)

    def test_heading_bold_not_doubled(self) -> None:
        """Bold enhancer on heading should not produce **text**."""
        module = _load_module("export_xiliu")
        blocks = {
            "root": {"type": 0, "title": "Page", "data": {"segments": []}, "subNodes": ["h"]},
            "h": {"type": 7, "data": {"level": 2, "segments": [{"type": 0, "text": "Title", "enhancer": {"bold": True}}]}, "subNodes": []},
        }
        md, _, _ = module.convert_flowus_blocks_to_markdown(self._make_response(blocks), "root")
        self.assertIn("## Title", md)
        self.assertNotIn("**Title**", md)

    def test_image_block_with_saver(self) -> None:
        module = _load_module("export_xiliu")
        blocks = {
            "root": {"type": 0, "title": "Page", "data": {"segments": []}, "subNodes": ["img"]},
            "img": {"type": 14, "data": {"ossName": "oss/test.png", "segments": []}, "subNodes": []},
        }
        mock_saver = MagicMock()
        mock_saver.save_image.return_value = "assets/001-test.png"
        mock_saver.image_count = 1
        mock_saver.failures = []
        md, count, _ = module.convert_flowus_blocks_to_markdown(self._make_response(blocks), "root", image_saver=mock_saver)
        self.assertIn("![image](assets/001-test.png)", md)
        self.assertEqual(count, 1)

    def test_image_block_download_failed_placeholder(self) -> None:
        module = _load_module("export_xiliu")
        blocks = {
            "root": {"type": 0, "title": "Page", "data": {"segments": []}, "subNodes": ["img"]},
            "img": {"type": 14, "data": {"ossName": "oss/fail.png", "segments": []}, "subNodes": []},
        }
        mock_saver = MagicMock()
        mock_saver.save_image.return_value = ""
        mock_saver.image_count = 1
        mock_saver.failures = [{"url": "oss/fail.png", "error": "fail"}]
        md, _, _ = module.convert_flowus_blocks_to_markdown(self._make_response(blocks), "root", image_saver=mock_saver)
        self.assertIn("图片下载失败", md)

    def test_image_block_no_saver_uses_cdn_url(self) -> None:
        module = _load_module("export_xiliu")
        blocks = {
            "root": {"type": 0, "title": "Page", "data": {"segments": []}, "subNodes": ["img"]},
            "img": {"type": 14, "data": {"ossName": "oss/test.png", "segments": []}, "subNodes": []},
        }
        md, _, _ = module.convert_flowus_blocks_to_markdown(self._make_response(blocks), "root")
        self.assertIn("https://cdn2.flowus.cn/oss/test.png", md)

    def test_unknown_block_type_with_text(self) -> None:
        module = _load_module("export_xiliu")
        blocks = {
            "root": {"type": 0, "title": "Page", "data": {"segments": []}, "subNodes": ["x"]},
            "x": {"type": 99, "data": {"segments": [{"type": 0, "text": "custom", "enhancer": {}}]}, "subNodes": []},
        }
        md, _, _ = module.convert_flowus_blocks_to_markdown(self._make_response(blocks), "root")
        self.assertIn("custom", md)

    def test_auto_detect_root_by_parent_equals_space(self) -> None:
        module = _load_module("export_xiliu")
        blocks = {
            "page1": {"type": 0, "parentId": "space-1", "spaceId": "space-1", "title": "Root",
                      "data": {"segments": [{"type": 0, "text": "Root", "enhancer": {}}]}, "subNodes": []},
        }
        md, _, _ = module.convert_flowus_blocks_to_markdown({"code": 200, "data": {"blocks": blocks}})
        self.assertTrue(md.startswith("# Root"))


class ExportFlowUsEdgeCaseTests(unittest.TestCase):
    """Additional behavioral tests for export_flowus."""

    def test_no_doc_url_raises(self) -> None:
        module = _load_module("export_xiliu")
        args = module.parse_args(["--output", "out"])
        with patch.object(module, "FlowUsClient"):
            with self.assertRaises(module.FlowUsError) as ctx:
                module.export_flowus(args)
            self.assertIn("--doc-url", str(ctx.exception))

    def test_root_read_failure_is_not_reported_as_zero_success(self) -> None:
        module = _load_module("export_xiliu")

        class Client:
            def get_doc(self, _doc_id):
                raise module.FlowUsError("offline")

        with self.assertRaisesRegex(module.FlowUsError, "读取根目录失败"):
            module.build_toc_tree(Client(), "root")

    def test_incremental_skip_existing_file(self) -> None:
        module = _load_module("export_xiliu")
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "out"
            out_dir.mkdir()
            # Pre-create the file
            (out_dir / "Doc.md").write_text("existing content", encoding="utf-8")
            args = module.parse_args(
                ["--doc-url", "https://flowus.cn/demo", "--output", str(out_dir), "--incremental"]
            )
            mock_nodes = [module.FlowUsNode(id="doc-1", title="Doc", is_dir=False)]
            with patch.object(module, "build_toc_tree", return_value=mock_nodes), \
                 patch.object(module, "read_auth_payload", return_value={"token": "fake", "cookies": []}):
                result = module.export_flowus(args)
            self.assertEqual(result["skipped"], 1)
            self.assertEqual(result["exported"], 0)

    def test_resume_file_deleted_re_exports(self) -> None:
        """When a completed file is deleted, resume detects and re-exports it."""
        module = _load_module("export_xiliu")
        args = module.parse_args(
            ["--doc-url", "https://flowus.cn/demo", "--output", "/tmp/out",
             "--resume", "--checkpoint-file", "/tmp/out/.wandao/checkpoint.sqlite",
             "--checkpoint-task-id", "test-task"]
        )
        mock_nodes = [module.FlowUsNode(id="doc-1", title="Doc", is_dir=False)]
        mock_doc = {"code": 200, "data": {"blocks": {"root": {"type": 0, "title": "T", "data": {"segments": [{"type": 0, "text": "T", "enhancer": {}}]}, "subNodes": []}}}}

        # Smart checkpoint: initially "completed" for resume filter,
        # but after upsert (file deleted reset), return "pending"
        class SmartCheckpoint:
            def __init__(self):
                self._statuses: dict[str, str] = {}
                self._reset: set[str] = set()
                self.start_task = MagicMock()
                self.start_item = MagicMock()
                self.complete_item = MagicMock()
                self.fail_item = MagicMock()
                self.complete_task = MagicMock()
                self.close = MagicMock()

            def upsert_item(self, key: str, **kw: Any) -> None:
                self._reset.add(key)

            def item_status(self, key: str) -> str:
                if key in self._reset:
                    return "pending"
                return "completed"

        checkpoint = SmartCheckpoint()

        with patch.object(module, "build_toc_tree", return_value=mock_nodes), \
             patch.object(module, "read_auth_payload", return_value={"token": "fake", "cookies": []}), \
             patch.object(module, "FlowUsClient") as MockClient, \
             patch.object(module, "open_checkpoint_from_args", return_value=checkpoint):
            instance = MockClient.return_value
            instance.get_doc.return_value = mock_doc
            result = module.export_flowus(args)
            # File doesn't exist, checkpoint reset → should re-export
            self.assertEqual(result["exported"], 1)


class MarkdownToHtmlTests(unittest.TestCase):
    """Test markdown_to_html conversion logic."""

    def test_heading_conversion(self) -> None:
        module = _load_module("import_flowus")
        html_out = module.markdown_to_html("# Title\n## Sub\n### Sub3")
        self.assertIn("<h1>Title</h1>", html_out)
        self.assertIn("<h2>Sub</h2>", html_out)
        self.assertIn("<h3>Sub3</h3>", html_out)

    def test_code_block_conversion(self) -> None:
        module = _load_module("import_flowus")
        md = "```python\nprint('hello')\n```"
        html_out = module.markdown_to_html(md)
        self.assertIn("<pre><code", html_out)
        self.assertIn("print", html_out)

    def test_unclosed_code_block(self) -> None:
        module = _load_module("import_flowus")
        md = "```python\nprint('hello')"
        html_out = module.markdown_to_html(md)
        self.assertIn("<pre><code", html_out)

    def test_horizontal_rule(self) -> None:
        module = _load_module("import_flowus")
        html_out = module.markdown_to_html("---")
        self.assertIn("<hr>", html_out)

    def test_unordered_list(self) -> None:
        module = _load_module("import_flowus")
        html_out = module.markdown_to_html("- item1\n- item2")
        self.assertIn("<li>item1</li>", html_out)
        self.assertIn("<li>item2</li>", html_out)

    def test_ordered_list(self) -> None:
        module = _load_module("import_flowus")
        html_out = module.markdown_to_html("1. first\n2. second")
        self.assertIn("<li>first</li>", html_out)

    def test_paragraph_conversion(self) -> None:
        module = _load_module("import_flowus")
        html_out = module.markdown_to_html("Hello world")
        self.assertIn("<p>Hello world</p>", html_out)

    def test_standalone_image_to_img_tag(self) -> None:
        module = _load_module("import_flowus")
        html_out = module.markdown_to_html("![alt text](https://example.com/img.png)")
        self.assertIn('<img src="https://example.com/img.png"', html_out)
        self.assertIn('alt="alt text"', html_out)

    def test_empty_line_produces_empty_html(self) -> None:
        module = _load_module("import_flowus")
        html_out = module.markdown_to_html("line1\n\nline2")
        # Should have empty string between paragraphs
        self.assertIn("<p>line1</p>", html_out)
        self.assertIn("<p>line2</p>", html_out)

    def test_html_wraps_in_doctype(self) -> None:
        module = _load_module("import_flowus")
        html_out = module.markdown_to_html("test", title="My Title")
        self.assertIn("<!DOCTYPE html>", html_out)
        self.assertIn("<title>My Title</title>", html_out)


class InlineToHtmlTests(unittest.TestCase):
    """Test _inline_to_html formatting."""

    def test_bold(self) -> None:
        module = _load_module("import_flowus")
        self.assertEqual(module._inline_to_html("**bold**"), "<strong>bold</strong>")

    def test_italic(self) -> None:
        module = _load_module("import_flowus")
        self.assertEqual(module._inline_to_html("*italic*"), "<em>italic</em>")

    def test_code(self) -> None:
        module = _load_module("import_flowus")
        self.assertEqual(module._inline_to_html("`code`"), "<code>code</code>")

    def test_strikethrough(self) -> None:
        module = _load_module("import_flowus")
        self.assertEqual(module._inline_to_html("~~del~~"), "<del>del</del>")

    def test_link(self) -> None:
        module = _load_module("import_flowus")
        result = module._inline_to_html("[text](https://example.com)")
        self.assertIn('<a href="https://example.com">text</a>', result)

    def test_inline_image(self) -> None:
        module = _load_module("import_flowus")
        result = module._inline_to_html("![alt](https://example.com/img.png)")
        self.assertIn('<img src="https://example.com/img.png"', result)


class GetBlockRoleTests(unittest.TestCase):
    """Test _get_block_role permission logic."""

    def test_user_permission_editor(self) -> None:
        module = _load_module("import_flowus")
        permissions = [{"type": "user", "userId": "u1", "role": "editor"}]
        self.assertEqual(module._get_block_role(permissions, "u1", []), "editor")

    def test_user_permission_writer(self) -> None:
        module = _load_module("import_flowus")
        permissions = [{"type": "user", "userId": "u1", "role": "writer"}]
        self.assertEqual(module._get_block_role(permissions, "u1", []), "writer")

    def test_user_permission_reader(self) -> None:
        module = _load_module("import_flowus")
        permissions = [{"type": "user", "userId": "u1", "role": "reader"}]
        self.assertEqual(module._get_block_role(permissions, "u1", []), "reader")

    def test_group_permission(self) -> None:
        module = _load_module("import_flowus")
        permissions = [{"type": "group", "groupId": "g1", "role": "writer"}]
        groups = [{"id": "g1", "userIds": ["u1"]}]
        self.assertEqual(module._get_block_role(permissions, "u1", groups), "writer")

    def test_space_permission(self) -> None:
        module = _load_module("import_flowus")
        permissions = [{"type": "space", "role": "editor"}]
        self.assertEqual(module._get_block_role(permissions, "u1", []), "editor")

    def test_highest_role_wins(self) -> None:
        module = _load_module("import_flowus")
        permissions = [
            {"type": "user", "userId": "u1", "role": "reader"},
            {"type": "space", "role": "editor"},
        ]
        self.assertEqual(module._get_block_role(permissions, "u1", []), "editor")

    def test_no_matching_permission(self) -> None:
        module = _load_module("import_flowus")
        permissions = [{"type": "user", "userId": "other", "role": "editor"}]
        self.assertEqual(module._get_block_role(permissions, "u1", []), "none")

    def test_empty_permissions(self) -> None:
        module = _load_module("import_flowus")
        self.assertEqual(module._get_block_role([], "u1", []), "none")


class ParseMarkdownImagePositionsTests(unittest.TestCase):
    """Test parse_markdown_image_positions."""

    def test_text_and_image_elements(self) -> None:
        module = _load_module("import_flowus")
        md = "Hello\n![alt](img.png)\nWorld"
        elements = module.parse_markdown_image_positions(md)
        types = [e["type"] for e in elements]
        self.assertEqual(types, ["text", "image", "text"])

    def test_empty_lines_are_skip(self) -> None:
        module = _load_module("import_flowus")
        md = "line1\n\nline2"
        elements = module.parse_markdown_image_positions(md)
        types = [e["type"] for e in elements]
        self.assertEqual(types, ["text", "skip", "text"])

    def test_code_block_content_skipped(self) -> None:
        module = _load_module("import_flowus")
        md = "```\n![not-image](fake.png)\n```"
        elements = module.parse_markdown_image_positions(md)
        # The ``` markers are processed, content inside is skipped
        image_elems = [e for e in elements if e["type"] == "image"]
        self.assertEqual(len(image_elems), 0)

    def test_image_alt_and_path_extracted(self) -> None:
        module = _load_module("import_flowus")
        md = "![my photo](photos/test.jpg)"
        elements = module.parse_markdown_image_positions(md)
        img = [e for e in elements if e["type"] == "image"][0]
        self.assertEqual(img["alt"], "my photo")
        self.assertEqual(img["path"], "photos/test.jpg")


class PollTaskResultTests(unittest.TestCase):
    """Test poll_task_result timeout and error handling."""

    def test_success_returns_result(self) -> None:
        module = _load_module("import_flowus")
        mock_client = MagicMock()
        success_response = json.dumps({
            "code": 200,
            "data": {"results": {"task-1": {"status": "success", "result": {"status": "success"}}}}
        }).encode()
        mock_client.request.return_value = success_response
        result = module.poll_task_result(mock_client, "task-1", timeout=5, interval=0.01)
        self.assertEqual(result["status"], "success")

    def test_failed_status_raises(self) -> None:
        module = _load_module("import_flowus")
        mock_client = MagicMock()
        failed_response = json.dumps({
            "code": 200,
            "data": {"results": {"task-1": {"status": "failed", "result": {"msg": "bad input"}}}}
        }).encode()
        mock_client.request.return_value = failed_response
        with self.assertRaises(module.FlowUsError) as ctx:
            module.poll_task_result(mock_client, "task-1", timeout=5, interval=0.01)
        self.assertIn("bad input", str(ctx.exception))

    def test_error_status_raises(self) -> None:
        module = _load_module("import_flowus")
        mock_client = MagicMock()
        error_response = json.dumps({
            "code": 200,
            "data": {"results": {"task-1": {"status": "error", "result": {"msg": "server error"}}}}
        }).encode()
        mock_client.request.return_value = error_response
        with self.assertRaises(module.FlowUsError):
            module.poll_task_result(mock_client, "task-1", timeout=5, interval=0.01)

    def test_success_but_inner_failed_raises(self) -> None:
        module = _load_module("import_flowus")
        mock_client = MagicMock()
        response = json.dumps({
            "code": 200,
            "data": {"results": {"task-1": {"status": "success", "result": {"status": "failed", "msg": "import error"}}}}
        }).encode()
        mock_client.request.return_value = response
        with self.assertRaises(module.FlowUsError) as ctx:
            module.poll_task_result(mock_client, "task-1", timeout=5, interval=0.01)
        self.assertIn("import error", str(ctx.exception))

    def test_timeout_raises(self) -> None:
        module = _load_module("import_flowus")
        mock_client = MagicMock()
        # Return empty results (task not ready yet)
        pending_response = json.dumps({
            "code": 200,
            "data": {"results": {}}
        }).encode()
        mock_client.request.return_value = pending_response
        with self.assertRaises(module.FlowUsError) as ctx:
            module.poll_task_result(mock_client, "task-1", timeout=1, interval=0.1)
        self.assertIn("超时", str(ctx.exception))

    def test_api_error_raises(self) -> None:
        module = _load_module("import_flowus")
        mock_client = MagicMock()
        error_response = json.dumps({"code": 500, "msg": "server error"}).encode()
        mock_client.request.return_value = error_response
        with self.assertRaises(module.FlowUsError) as ctx:
            module.poll_task_result(mock_client, "task-1", timeout=5, interval=0.01)
        self.assertIn("查询任务状态失败", str(ctx.exception))


class MarkdownToLocalImageTests(unittest.TestCase):
    """Test _resolve_image_src with local files."""

    def test_local_file_to_data_url(self) -> None:
        module = _load_module("import_flowus")
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp) / "source"
            source_dir.mkdir()
            img = source_dir / "test.png"
            img.write_bytes(b"\x89PNG")
            result = module._resolve_image_src("test.png", source_dir)
            self.assertTrue(result.startswith("data:image/png;base64,"))

    def test_remote_url_passthrough(self) -> None:
        module = _load_module("import_flowus")
        result = module._resolve_image_src("https://example.com/img.png", Path("/tmp"))
        self.assertEqual(result, "https://example.com/img.png")

    def test_data_url_passthrough(self) -> None:
        module = _load_module("import_flowus")
        result = module._resolve_image_src("data:image/png;base64,abc", Path("/tmp"))
        self.assertEqual(result, "data:image/png;base64,abc")

    def test_nonexistent_file_returns_raw(self) -> None:
        module = _load_module("import_flowus")
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp) / "source"
            source_dir.mkdir()
            result = module._resolve_image_src("missing.png", source_dir)
            self.assertEqual(result, "missing.png")

    def test_no_source_dir_returns_raw(self) -> None:
        module = _load_module("import_flowus")
        result = module._resolve_image_src("test.png", None)
        self.assertEqual(result, "test.png")


class ImportFlowUsBehaviorTests(unittest.TestCase):
    """Behavioral tests for import_flowus."""

    def test_no_source_dir_raises(self) -> None:
        module = _load_module("import_flowus")
        export_module = _load_module("export_xiliu")
        args = module.parse_args(["--source-dir", "/nonexistent", "--space-id", "s1"])
        with patch.object(export_module, "read_auth_payload", return_value={"token": "fake", "cookies": []}), \
             patch.object(export_module, "FlowUsClient"):
            with self.assertRaises(module.FlowUsError) as ctx:
                module.import_flowus(args)
            self.assertIn("不存在", str(ctx.exception))

    def test_no_targets_raises(self) -> None:
        module = _load_module("import_flowus")
        export_module = _load_module("export_xiliu")
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp) / "source"
            source_dir.mkdir()
            (source_dir / "doc.md").write_text("# Hello", encoding="utf-8")
            args = module.parse_args(["--source-dir", str(source_dir), "--space-id", "s1", "--parent-id", "p1"])
            with patch.object(export_module, "read_auth_payload", return_value={"token": "fake", "cookies": []}), \
                 patch.object(export_module, "FlowUsClient"), \
                 patch.object(module, "list_import_targets", return_value=[]):
                with self.assertRaises(module.FlowUsError) as ctx:
                    module.import_flowus(args)
                self.assertIn("没有找到可写入", str(ctx.exception))

    def test_successful_import(self) -> None:
        module = _load_module("import_flowus")
        export_module = _load_module("export_xiliu")
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp) / "source"
            source_dir.mkdir()
            (source_dir / "doc.md").write_text("# Hello World", encoding="utf-8")
            args = module.parse_args(["--source-dir", str(source_dir), "--space-id", "s1", "--parent-id", "p1"])
            with patch.object(export_module, "read_auth_payload", return_value={"token": "fake", "cookies": []}), \
                 patch.object(export_module, "FlowUsClient") as MockClient, \
                 patch.object(module, "list_import_targets", return_value=[{"id": "p1", "name": "Root", "spaceId": "s1", "role": "editor"}]), \
                 patch.object(module, "create_empty_page", return_value="page-1"), \
                 patch.object(module, "upload_html_content", return_value="oss/test.html"), \
                 patch.object(module, "enqueue_import_task", return_value="task-1"), \
                 patch.object(module, "poll_task_result", return_value={"status": "success"}), \
                 patch.object(module, "update_page_title"):
                result = module.import_flowus(args)
                self.assertEqual(result["imported"], 1)
                self.assertEqual(result["failed"], 0)

    def test_import_failure_increments_failed(self) -> None:
        module = _load_module("import_flowus")
        export_module = _load_module("export_xiliu")
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp) / "source"
            source_dir.mkdir()
            (source_dir / "doc.md").write_text("# Hello", encoding="utf-8")
            args = module.parse_args(["--source-dir", str(source_dir), "--space-id", "s1", "--parent-id", "p1"])
            with patch.object(export_module, "read_auth_payload", return_value={"token": "fake", "cookies": []}), \
                 patch.object(export_module, "FlowUsClient"), \
                 patch.object(module, "list_import_targets", return_value=[{"id": "p1", "name": "Root", "spaceId": "s1", "role": "editor"}]), \
                 patch.object(module, "create_empty_page", side_effect=module.FlowUsError("API error")):
                result = module.import_flowus(args)
                self.assertEqual(result["imported"], 0)
                self.assertEqual(result["failed"], 1)


class UpdatePageTitleTests(unittest.TestCase):
    """Test update_page_title error handling."""

    def test_non_200_raises(self) -> None:
        module = _load_module("import_flowus")
        mock_client = MagicMock()
        mock_client.request.return_value = json.dumps({"code": 403, "msg": "forbidden"}).encode()
        with self.assertRaises(module.FlowUsError) as ctx:
            module.update_page_title(mock_client, "space1", "page1", "New Title")
        self.assertIn("更新页面标题失败", str(ctx.exception))


class UploadHtmlContentTests(unittest.TestCase):
    """Test upload_html_content error handling."""

    def test_non_200_raises(self) -> None:
        module = _load_module("import_flowus")
        mock_client = MagicMock()
        mock_client.request.return_value = json.dumps({"code": 500, "msg": "server error"}).encode()
        with self.assertRaises(module.FlowUsError):
            module.upload_html_content(mock_client, "<p>test</p>")

    def test_missing_ossname_raises(self) -> None:
        module = _load_module("import_flowus")
        mock_client = MagicMock()
        mock_client.request.return_value = json.dumps({"code": 200, "data": {}}).encode()
        with self.assertRaises(module.FlowUsError) as ctx:
            module.upload_html_content(mock_client, "<p>test</p>")
        self.assertIn("ossName", str(ctx.exception))


class EnqueueImportTaskTests(unittest.TestCase):
    """Test enqueue_import_task error handling."""

    def test_non_200_raises(self) -> None:
        module = _load_module("import_flowus")
        mock_client = MagicMock()
        mock_client.request.return_value = json.dumps({"code": 400, "msg": "bad request"}).encode()
        with self.assertRaises(module.FlowUsError):
            module.enqueue_import_task(mock_client, "block1", "space1", "oss/test.html")

    def test_missing_taskid_raises(self) -> None:
        module = _load_module("import_flowus")
        mock_client = MagicMock()
        mock_client.request.return_value = json.dumps({"code": 200, "data": {}}).encode()
        with self.assertRaises(module.FlowUsError) as ctx:
            module.enqueue_import_task(mock_client, "block1", "space1", "oss/test.html")
        self.assertIn("taskId", str(ctx.exception))


class FetchPageBlocksTests(unittest.TestCase):
    """Test fetch_page_blocks error handling."""

    def test_non_200_raises(self) -> None:
        module = _load_module("import_flowus")
        mock_client = MagicMock()
        mock_client.get_json.return_value = {"code": 404, "msg": "not found"}
        with self.assertRaises(module.FlowUsError) as ctx:
            module.fetch_page_blocks(mock_client, "page1")
        self.assertIn("blocks", str(ctx.exception))


class CreateImageBlockTests(unittest.TestCase):
    """Test create_image_block error handling."""

    def test_non_200_raises(self) -> None:
        module = _load_module("import_flowus")
        mock_client = MagicMock()
        mock_client.request.return_value = json.dumps({"code": 403, "msg": "forbidden"}).encode()
        with self.assertRaises(module.FlowUsError) as ctx:
            module.create_image_block(mock_client, "space1", "page1", "block1", "oss/img.png")
        self.assertIn("创建图片块失败", str(ctx.exception))


class UpdateBlockOssnameTests(unittest.TestCase):
    """Test update_block_ossname error handling."""

    def test_non_200_raises(self) -> None:
        module = _load_module("import_flowus")
        mock_client = MagicMock()
        mock_client.request.return_value = json.dumps({"code": 500, "msg": "error"}).encode()
        with self.assertRaises(module.FlowUsError) as ctx:
            module.update_block_ossname(mock_client, "space1", "block1", "https://cdn.example.com/img.png")
        self.assertIn("更新图片块", str(ctx.exception))


class CreateSignedUrlsTests(unittest.TestCase):
    """Test create_signed_urls retry logic."""

    def test_success_returns_url(self) -> None:
        module = _load_module("import_flowus")
        mock_client = MagicMock()
        mock_client.request.return_value = json.dumps({
            "code": 200, "data": [{"url": "https://cdn.example.com/signed"}]
        }).encode()
        result = module.create_signed_urls(mock_client, "block1", "oss/img.png")
        self.assertEqual(result, "https://cdn.example.com/signed")

    def test_retry_on_null_then_success(self) -> None:
        module = _load_module("import_flowus")
        mock_client = MagicMock()
        null_resp = json.dumps({"code": 200, "data": [None]}).encode()
        ok_resp = json.dumps({"code": 200, "data": [{"url": "https://cdn.example.com/signed"}]}).encode()
        mock_client.request.side_effect = [null_resp, ok_resp]
        with patch("time.sleep"):
            result = module.create_signed_urls(mock_client, "block1", "oss/img.png", max_retries=3)
        self.assertEqual(result, "https://cdn.example.com/signed")

    def test_all_retries_fail_raises(self) -> None:
        module = _load_module("import_flowus")
        mock_client = MagicMock()
        null_resp = json.dumps({"code": 200, "data": [None]}).encode()
        mock_client.request.return_value = null_resp
        with patch("time.sleep"):
            with self.assertRaises(module.FlowUsError) as ctx:
                module.create_signed_urls(mock_client, "block1", "oss/img.png", max_retries=2)
        self.assertIn("多次重试", str(ctx.exception))

    def test_non_200_raises_immediately(self) -> None:
        module = _load_module("import_flowus")
        mock_client = MagicMock()
        mock_client.request.return_value = json.dumps({"code": 403, "msg": "forbidden"}).encode()
        with self.assertRaises(module.FlowUsError):
            module.create_signed_urls(mock_client, "block1", "oss/img.png")


class GetUploadInfoTests(unittest.TestCase):
    """Test get_upload_info error handling."""

    def test_non_200_raises(self) -> None:
        module = _load_module("import_flowus")
        mock_client = MagicMock()
        mock_client.request.return_value = json.dumps({"code": 403, "msg": "forbidden"}).encode()
        with self.assertRaises(module.FlowUsError) as ctx:
            module.get_upload_info(mock_client, "space1", "test.png")
        self.assertIn("获取上传凭证失败", str(ctx.exception))


class InsertImageBlocksAfterImportTests(unittest.TestCase):
    """Test insert_image_blocks_after_import."""

    def test_empty_mapping_returns_zero(self) -> None:
        module = _load_module("import_flowus")
        mock_client = MagicMock()
        result = module.insert_image_blocks_after_import(mock_client, "s1", "p1", "content", {})
        self.assertEqual(result, 0)

    def test_no_blocks_returns_zero(self) -> None:
        module = _load_module("import_flowus")
        mock_client = MagicMock()
        with patch.object(module, "fetch_page_blocks", return_value={}):
            result = module.insert_image_blocks_after_import(
                mock_client, "s1", "p1", "content", {"img.png": ("oss/img.png", "")}
            )
        self.assertEqual(result, 0)

    def test_creates_image_block_for_standalone_image(self) -> None:
        module = _load_module("import_flowus")
        mock_client = MagicMock()
        page_blocks = {
            "p1": {"subNodes": ["txt1"]},
            "txt1": {"type": 1},
        }
        with patch.object(module, "fetch_page_blocks", return_value=page_blocks), \
             patch.object(module, "create_image_block") as mock_create, \
             patch.object(module, "create_signed_urls", return_value="https://cdn.example.com/signed"), \
             patch.object(module, "update_block_ossname"):
            md = "![alt](img.png)"
            mapping = {"img.png": ("oss/img.png", "")}
            result = module.insert_image_blocks_after_import(mock_client, "s1", "p1", md, mapping)
            self.assertEqual(result, 1)
            mock_create.assert_called_once()


if __name__ == "__main__":
    unittest.main()
