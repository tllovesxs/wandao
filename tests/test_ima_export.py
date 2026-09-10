from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plugins.ima.backend import ima_knowledge as module


class FakeImaClient:
    def __init__(self, media_info: dict, note_data: dict | None = None) -> None:
        self.media_info = media_info
        self.note_data = note_data or {}

    def wiki(self, action: str, payload: dict) -> dict:
        if action == "get_media_info":
            return self.media_info
        raise AssertionError(action)

    def note(self, action: str, payload: dict) -> dict:
        if action == "get_doc_content":
            return self.note_data
        raise AssertionError(action)


class FakeImaImportClient:
    def __init__(self) -> None:
        self.note_payload: dict = {}
        self.add_payload: dict = {}

    def wiki(self, action: str, payload: dict) -> dict:
        if action == "check_repeated_names":
            return {"results": [{"name": payload["params"][0]["name"], "is_repeated": False}]}
        if action == "add_knowledge":
            self.add_payload = payload
            return {}
        raise AssertionError(action)

    def note(self, action: str, payload: dict) -> dict:
        if action == "import_doc":
            self.note_payload = payload
            return {"note_id": "note-test"}
        raise AssertionError(action)


class ImaExportTests(unittest.TestCase):
    def test_remote_image_urls_ignore_normal_links(self) -> None:
        text = (
            "[普通链接](https://example.com/page)\n"
            "![Markdown](https://cdn.example.com/a.png?x=1)\n"
            '<img src="https://cdn.example.com/b" alt="HTML">\n'
            "https://cdn.example.com/c.webp\n"
        )
        self.assertEqual(
            module.remote_image_urls(text),
            [
                "https://cdn.example.com/a.png?x=1",
                "https://cdn.example.com/b",
                "https://cdn.example.com/c.webp",
            ],
        )

    def test_note_export_localizes_images(self) -> None:
        client = FakeImaClient(
            {"media_type": 11, "notebook_ext_info": {"notebook_id": "note-1"}},
            {"content": "正文\n\n![图](https://cdn.example.com/a.png)"},
        )
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            module, "download_url", return_value=(b"png", "image/png")
        ):
            path = module.save_note_entry(
                client,
                module.KnowledgeEntry("kb", "知识库", "media", "测试笔记", "", [], False, 11),
                Path(temporary),
            )
            self.assertIn("![图](测试笔记_assets/image-001.png)", path.read_text(encoding="utf-8"))
            self.assertEqual((Path(temporary) / "测试笔记_assets" / "image-001.png").read_bytes(), b"png")

    def test_malformed_image_url_does_not_fail_note_export(self) -> None:
        client = FakeImaClient(
            {"media_type": 11, "notebook_ext_info": {"notebook_id": "note-1"}},
            {"content": "正文\n\n![内网图](https://[10.0.0.1/image.png)"},
        )
        with tempfile.TemporaryDirectory() as temporary:
            status, path, warning = module.save_media_entry(
                client,
                module.KnowledgeEntry("kb", "知识库", "media", "测试笔记", "", [], False, 11),
                Path(temporary),
            )

            self.assertEqual(status, "exported_note")
            self.assertEqual(warning, "1 张图片下载失败（invalid-url）")
            assert path is not None
            self.assertIn("https://[10.0.0.1/image.png", path.read_text(encoding="utf-8"))

    def test_markdown_file_export_localizes_images(self) -> None:
        client = FakeImaClient(
            {
                "media_type": 7,
                "url_info": {"url": "https://ima.example.com/source.md"},
            }
        )
        responses = [
            ("# 标题\n\n![图](https://cdn.example.com/a.png)\n".encode("utf-8"), "text/markdown"),
            (b"png", "image/png"),
        ]
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            module, "download_url", side_effect=responses
        ):
            status, path, warning = module.save_media_entry(
                client,
                module.KnowledgeEntry("kb", "知识库", "media", "原文.md", "", [], False, 7),
                Path(temporary),
            )
            self.assertEqual(status, "exported")
            self.assertEqual(warning, "")
            assert path is not None
            self.assertIn("原文_assets/image-001.png", path.read_text(encoding="utf-8"))
            self.assertTrue((path.parent / "原文_assets" / "image-001.png").is_file())

    def test_ima_import_includes_referenced_assets_by_default(self) -> None:
        args = module.parse_args(["--scan-source"])
        self.assertFalse(args.include_referenced_assets)

    def test_local_markdown_images_are_embedded_for_ima_notes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image_dir = root / "文档.assets"
            image_dir.mkdir()
            image = image_dir / "image.png"
            image.write_bytes(b"png")
            markdown = "![图](文档.assets/image.png)\n"

            embedded, count = module.embed_local_markdown_images(markdown, root / "文档.md", root)

            self.assertEqual(count, 1)
            self.assertIn("data:image/png;base64,cG5n", embedded)

    def test_markdown_import_uses_one_note_with_embedded_images(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image_dir = root / "文档.assets"
            image_dir.mkdir()
            (image_dir / "image.png").write_bytes(b"png")
            markdown = root / "文档.md"
            markdown.write_text("![图](文档.assets/image.png)\n", encoding="utf-8")
            client = FakeImaImportClient()
            args = module.parse_args(
                [
                    "--knowledge-base-id",
                    "kb-test",
                    "--source-dir",
                    str(root),
                    "--yes",
                ]
            )

            result = module.upload_markdown_note(client, args, markdown, root, [])

            self.assertEqual(result, "note-test")
            self.assertEqual(client.add_payload["media_type"], 11)
            self.assertEqual(client.add_payload["note_info"]["content_id"], "note-test")
            self.assertIn("data:image/png;base64,cG5n", client.note_payload["content"])

    def test_export_repairs_flattened_local_image_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            markdown_dir = root / "测试"
            markdown_dir.mkdir()
            markdown = markdown_dir / "文档 带空格.md"
            markdown.write_text("![图](文档 带空格.assets/image.png)\n", encoding="utf-8")
            source_image = markdown_dir / "image.png"
            source_image.write_bytes(b"png")

            repaired, failures = module.repair_exported_local_image_references(root)

            self.assertEqual(repaired, 1)
            self.assertEqual(failures, [])
            self.assertTrue((markdown_dir / "文档 带空格.assets" / "image.png").is_file())
            self.assertFalse(source_image.exists())


if __name__ == "__main__":
    unittest.main()
