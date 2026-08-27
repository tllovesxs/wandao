from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "plugins" / "dingtalk" / "backend" / "import_dingtalk.py"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
SPEC = importlib.util.spec_from_file_location("wandao_dingtalk_import", MODULE_PATH)
assert SPEC and SPEC.loader
dingtalk_import = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = dingtalk_import
SPEC.loader.exec_module(dingtalk_import)


class DingTalkImportTests(unittest.TestCase):
    def create_source_tree(self, root: Path) -> Path:
        source = root / "导入根目录"
        (source / "01-图文").mkdir(parents=True)
        (source / "02-嵌套" / "03-更深").mkdir(parents=True)
        (source / "assets").mkdir()
        (source / "assets" / "图片.png").write_bytes(b"png-test-data")
        (source / "assets" / "未引用.png").write_bytes(b"unused-image")
        (source / "00-说明.md").write_text("# 说明\n", encoding="utf-8")
        (source / "01-图文" / "图文.md").write_text(
            "# 图文\n\n![本地图片](../assets/%E5%9B%BE%E7%89%87.png)\n\n<img src=\"../assets/图片.png\">\n",
            encoding="utf-8",
        )
        (source / "02-嵌套" / "03-更深" / "正文.txt").write_text("纯文本", encoding="utf-8")
        (source / "无法导入.pdf").write_bytes(b"not-a-pdf")
        return source

    def test_parse_target_url_supports_folder_and_node_links(self) -> None:
        self.assertEqual(
            dingtalk_import.parse_target_url("https://docs.dingtalk.com/i/desktop/folders/folder_123?from=copy"),
            "folder_123",
        )
        self.assertEqual(
            dingtalk_import.parse_target_url("https://alidocs.dingtalk.com/i/nodes/node_456"),
            "node_456",
        )
        with self.assertRaises(dingtalk_import.ExportError):
            dingtalk_import.parse_target_url("https://example.com/i/desktop/folders/not-dingtalk")

    def test_rewrite_markdown_images_uses_opaque_placeholders_and_keeps_remote_urls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.create_source_tree(root)
            markdown_path = source / "01-图文" / "图文.md"
            resources: dict[str, object] = {}
            rewritten, local_resources, references, warnings = dingtalk_import.rewrite_markdown_images(
                markdown_path.read_text(encoding="utf-8"),
                document_path=markdown_path,
                source_root=source,
                resource_cache=resources,
            )
            self.assertEqual(references, 2)
            self.assertEqual(len(local_resources), 1)
            self.assertIn("wandao-resource://image-", rewritten)
            self.assertNotIn("../assets/图片.png", rewritten)
            self.assertEqual(warnings, [])

            remote, remote_resources, remote_refs, remote_warnings = dingtalk_import.rewrite_markdown_images(
                "![远程](https://example.com/image.png)\n![缺失](missing.png)\n",
                document_path=markdown_path,
                source_root=source,
                resource_cache={},
            )
            self.assertIn("https://example.com/image.png", remote)
            self.assertEqual(remote_resources, [])
            self.assertEqual(remote_refs, 0)
            self.assertEqual(remote_warnings[0]["error"], "本地图片不存在，已保留原链接")

    def test_build_plan_preserves_document_folders_but_not_assets_only_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = self.create_source_tree(Path(temporary))
            args = dingtalk_import.parse_args([
                "--target-url", "https://docs.dingtalk.com/i/desktop/folders/target_123",
                "--source-dir", str(source),
                "--plan",
            ])
            plan = dingtalk_import.build_import_plan(args)
            self.assertEqual([doc.relative_path.as_posix() for doc in plan.documents], [
                "00-说明.md", "01-图文/图文.md", "02-嵌套/03-更深/正文.txt",
            ])
            self.assertEqual(plan.folders, [("01-图文",), ("02-嵌套",), ("02-嵌套", "03-更深")])
            self.assertNotIn(("assets",), plan.folders)
            self.assertEqual(len(plan.referenced_resources), 1)
            skipped = {item["path"]: item["reason"] for item in plan.skipped_files}
            self.assertIn("无法导入.pdf", skipped)
            self.assertIn("assets/未引用.png", skipped)

    def test_import_one_uses_first_supported_document_when_no_file_is_chosen(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = self.create_source_tree(Path(temporary))
            args = dingtalk_import.parse_args([
                "--target-url", "https://docs.dingtalk.com/i/desktop/folders/target_123",
                "--source-dir", str(source),
                "--import-one",
            ])
            plan = dingtalk_import.build_import_plan(args)
            self.assertEqual([doc.relative_path.as_posix() for doc in plan.documents], ["00-说明.md"])

    def test_helper_uses_content_type_and_does_not_expose_temporary_urls(self) -> None:
        helper = dingtalk_import.DINGTALK_IMPORT_HELPER_JS
        self.assertIn("'Content-Type':item.contentType", helper)
        self.assertIn("conflictHandleStrategy:'return_existing_dentry'", helper)
        self.assertIn("wandao-resource:\\/\\/", helper)
        self.assertIn("resources.set(resourceId", helper)
        self.assertNotIn("return {resourceId, accessUrl", helper)

    def test_ensure_folder_creates_nested_structure_once(self) -> None:
        created: list[tuple[str, str]] = []

        def fake_call(_cdp, method, *args, **_kwargs):
            self.assertEqual(method, "createFolder")
            parent, name = args
            created.append((parent, name))
            return {"dentryUuid": f"id-{name}"}

        folder_map: dict[str, str] = {}
        with patch.object(dingtalk_import, "call_import_helper", side_effect=fake_call):
            dingtalk_import._ensure_folder(
                object(), folder_map=folder_map, checkpoint=None,
                parts=("根目录",), parent_uuid="target",
            )
            dingtalk_import._ensure_folder(
                object(), folder_map=folder_map, checkpoint=None,
                parts=("根目录", "子目录"), parent_uuid="target",
            )
            result = dingtalk_import._ensure_folder(
                object(), folder_map=folder_map, checkpoint=None,
                parts=("根目录", "子目录", "更深层"), parent_uuid="target",
            )
        self.assertEqual(result, "id-更深层")
        self.assertEqual(created, [("target", "根目录"), ("id-根目录", "子目录"), ("id-子目录", "更深层")])
        self.assertEqual(folder_map["根目录/子目录/更深层"], "id-更深层")

    def test_import_documents_creates_tree_uploads_image_once_and_writes_report(self) -> None:
        class FakeCdp:
            def close(self) -> None:
                return None

        with tempfile.TemporaryDirectory() as temporary:
            source = self.create_source_tree(Path(temporary))
            args = dingtalk_import.parse_args([
                "--target-url", "https://docs.dingtalk.com/i/desktop/folders/target_123",
                "--source-dir", str(source),
                "--import-all", "--yes", "--request-delay", "0", "--request-jitter", "0",
            ])
            created_folders: list[tuple[str, str]] = []
            imported: list[dict[str, object]] = []
            finishes: list[str] = []

            def fake_call(_cdp, method, *call_args, **_kwargs):
                if method == "clear":
                    return {"cleared": True}
                if method == "createFolder":
                    parent, name = call_args
                    created_folders.append((str(parent), str(name)))
                    return {"dentryUuid": f"folder-{name}"}
                if method == "beginUpload":
                    return {"resourceId": str(call_args[0])}
                if method == "appendUpload":
                    return {"resourceId": str(call_args[0]), "chunks": 1}
                if method == "finishUpload":
                    finishes.append(str(call_args[0]))
                    return {"resourceId": str(call_args[0]), "size": 12}
                if method == "importDocument":
                    payload = call_args[0]
                    imported.append(payload)
                    return {"dentryUuid": f"doc-{len(imported)}", "status": 0}
                raise AssertionError(method)

            with (
                patch.object(dingtalk_import, "connect_dingtalk_browser", return_value=(FakeCdp(), None)),
                patch.object(dingtalk_import, "resolve_target_folder", return_value={"dentryUuid": "target", "name": "目标", "resolvedFrom": "folder"}),
                patch.object(dingtalk_import, "call_import_helper", side_effect=fake_call),
                patch.object(dingtalk_import, "default_data_dir", return_value=Path(temporary) / "wandao-data"),
            ):
                report = dingtalk_import.import_documents(args)

            self.assertEqual(report["importedDocs"], 3)
            self.assertEqual(report["imageUploads"], 1)
            self.assertEqual(len(imported), 3)
            self.assertTrue(all(item["fileSize"] == 12 for item in imported))
            self.assertIn(("target", source.name), created_folders)
            report_file = Path(report["reportFile"])
            self.assertTrue(report_file.is_file())
            self.assertFalse((source / "00-钉钉导入报告.json").exists())
            persisted = json.loads(report_file.read_text(encoding="utf-8"))
            self.assertEqual(persisted["importedDocs"], 3)
            self.assertGreaterEqual(len(finishes), 4)  # one image plus three documents


if __name__ == "__main__":
    unittest.main()
