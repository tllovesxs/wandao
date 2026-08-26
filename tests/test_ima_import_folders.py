from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plugins.ima.backend import ima_knowledge as module


class FakeImaFolderImportClient:
    def __init__(self) -> None:
        self.children: dict[str, list[dict[str, object]]] = {"": []}
        self.created_folders: list[dict[str, object]] = []
        self.add_payloads: list[dict[str, object]] = []
        self.note_payloads: list[dict[str, object]] = []

    def add_existing_folder(self, parent_id: str, title: str, folder_id: str) -> None:
        self.children.setdefault(parent_id, []).append(
            {"media_id": folder_id, "media_type": 0, "title": title, "parent_folder_id": parent_id}
        )
        self.children.setdefault(folder_id, [])

    def wiki(self, action: str, payload: dict[str, object]) -> dict[str, object]:
        if action == "get_knowledge_list":
            folder_id = str(payload.get("folder_id") or "")
            return {"knowledge_list": list(self.children.get(folder_id, [])), "is_end": True}
        if action == "create_folder":
            parent_id = str(payload.get("folder_id") or "")
            folder_id = f"folder_{len(self.created_folders) + 1}"
            folder = {"media_id": folder_id, "media_type": 0, "title": payload["name"], "parent_folder_id": parent_id}
            self.children.setdefault(parent_id, []).append(folder)
            self.children.setdefault(folder_id, [])
            self.created_folders.append(dict(payload))
            return {}
        if action == "check_repeated_names":
            return {"results": [{"name": payload["params"][0]["name"], "is_repeated": False}]}
        if action == "add_knowledge":
            self.add_payloads.append(dict(payload))
            return {}
        raise AssertionError(action)

    def note(self, action: str, payload: dict[str, object]) -> dict[str, object]:
        if action == "import_doc":
            self.note_payloads.append(dict(payload))
            return {"note_id": f"note_{len(self.note_payloads)}"}
        raise AssertionError(action)


class ImaImportFolderTests(unittest.TestCase):
    def test_scan_keeps_linked_markdown_pages_but_skips_referenced_image_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            article_dir = root / "章节"
            assets_dir = article_dir / "assets"
            assets_dir.mkdir(parents=True)
            (root / "00-入口.md").write_text("[章节](章节/正文.md)\n", encoding="utf-8")
            (article_dir / "正文.md").write_text("![图](assets/picture.png)\n", encoding="utf-8")
            (assets_dir / "picture.png").write_bytes(b"png")

            scanned = module.scan_source_files(root)

            self.assertEqual([item["relativePath"] for item in scanned], ["00-入口.md", "章节/正文.md"])

    def test_import_creates_nested_folders_and_reuses_existing_target_child(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(module, "emit"):
            root = Path(temporary)
            (root / "00-入口.md").write_text("[正文](A/正文.md)\n", encoding="utf-8")
            (root / "A" / "深层").mkdir(parents=True)
            (root / "B").mkdir()
            (root / "A" / "正文.md").write_text("# A\n", encoding="utf-8")
            (root / "A" / "深层" / "二级.md").write_text("# Deep\n", encoding="utf-8")
            (root / "B" / "B.md").write_text("# B\n", encoding="utf-8")
            client = FakeImaFolderImportClient()
            client.add_existing_folder("", "A", "folder_existing_a")
            args = module.parse_args(
                [
                    "--knowledge-base-id", "kb-test", "--source-dir", str(root), "--import-all", "--yes", "--progress-every", "0"
                ]
            )

            report = module.import_files(client, args)

            self.assertEqual(report["importedFiles"], 4)
            self.assertEqual(report["failureCount"], 0)
            self.assertEqual(
                client.created_folders,
                [
                    {"knowledge_base_id": "kb-test", "folder_id": "folder_existing_a", "name": "深层"},
                    {"knowledge_base_id": "kb-test", "name": "B"},
                ],
            )
            target_by_title = {str(payload["title"]): str(payload.get("folder_id") or "") for payload in client.add_payloads}
            self.assertEqual(target_by_title["00-入口.md"], "")
            self.assertEqual(target_by_title["正文.md"], "folder_existing_a")
            self.assertEqual(target_by_title["二级.md"], "folder_1")
            self.assertEqual(target_by_title["B.md"], "folder_2")

    def test_batch_import_ignores_single_file_test_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(module, "emit"):
            root = Path(temporary)
            first = root / "first.md"
            selected = root / "selected.md"
            first.write_text("# first\n", encoding="utf-8")
            selected.write_text("# selected\n", encoding="utf-8")
            client = FakeImaFolderImportClient()
            args = module.parse_args(
                [
                    "--knowledge-base-id", "kb-test", "--source-dir", str(root), "--source-file", str(selected),
                    "--import-all", "--yes", "--progress-every", "0"
                ]
            )

            report = module.import_files(client, args)

            self.assertEqual(report["importedFiles"], 2)
            self.assertEqual({str(payload["title"]) for payload in client.add_payloads}, {"first.md", "selected.md"})


if __name__ == "__main__":
    unittest.main()
