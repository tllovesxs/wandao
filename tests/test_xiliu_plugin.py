from __future__ import annotations

import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


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


if __name__ == "__main__":
    unittest.main()
