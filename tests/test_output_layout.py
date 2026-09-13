import argparse
import json
import tempfile
import unittest
from pathlib import Path

from wandao_core.output_layout import add_output_layout_args, resolve_output_directory, sanitize_output_folder_name


class OutputLayoutTests(unittest.TestCase):
    def test_flat_mode_returns_root_without_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.assertEqual(resolve_output_directory(root, "语雀知识库", "book-1"), root.resolve())
            self.assertFalse((root / ".wandao" / "output-layout.json").exists())

    def test_sanitizes_windows_names(self) -> None:
        self.assertEqual(sanitize_output_folder_name('A<>:"/\\|?*  '), "A---------")
        self.assertEqual(sanitize_output_folder_name("CON"), "CON-来源")
        self.assertEqual(sanitize_output_folder_name("   "), "导出内容")

    def test_same_id_keeps_directory_when_source_is_renamed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = resolve_output_directory(root, "旧名称", "stable-id", auto_output_folder=True)
            renamed = resolve_output_directory(root, "新名称", "stable-id", auto_output_folder=True)
            self.assertEqual(first, renamed)
            layout = json.loads((root / ".wandao" / "output-layout.json").read_text(encoding="utf-8"))
            self.assertEqual(layout["sources"]["stable-id"]["sourceName"], "新名称")

    def test_same_name_different_ids_do_not_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = resolve_output_directory(root, "同名来源", "id-1", auto_output_folder=True)
            second = resolve_output_directory(root, "同名来源", "id-2", auto_output_folder=True)
            self.assertNotEqual(first, second)
            self.assertEqual(second.name, "同名来源 (2)")

    def test_preserves_existing_flat_export_for_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "01-旧文档.md").write_text("# old", encoding="utf-8")
            result = resolve_output_directory(
                root, "来源", "source-id", auto_output_folder=True, preserve_legacy=True
            )
            self.assertEqual(result, root.resolve())
            layout = json.loads((root / ".wandao" / "output-layout.json").read_text(encoding="utf-8"))
            self.assertEqual(layout["sources"]["source-id"]["directory"], ".")

    def test_different_source_gets_child_after_legacy_source_is_registered(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "01-旧文档.md").write_text("# old", encoding="utf-8")
            legacy = resolve_output_directory(
                root, "旧来源", "old-source", auto_output_folder=True, preserve_legacy=True
            )
            different = resolve_output_directory(
                root, "新来源", "new-source", auto_output_folder=True, preserve_legacy=True
            )
            self.assertEqual(legacy, root.resolve())
            self.assertEqual(different, (root / "新来源").resolve())
            self.assertTrue((root / "新来源").is_dir())

    def test_registered_legacy_source_keeps_using_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "01-旧文档.md").write_text("# old", encoding="utf-8")
            first = resolve_output_directory(
                root, "旧来源", "old-source", auto_output_folder=True, preserve_legacy=True
            )
            repeated = resolve_output_directory(
                root, "旧来源", "old-source", auto_output_folder=True, preserve_legacy=True
            )
            self.assertEqual(first, root.resolve())
            self.assertEqual(repeated, root.resolve())

    def test_new_empty_root_creates_source_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result = resolve_output_directory(root, "来源/测试", "source-id", auto_output_folder=True)
            self.assertEqual(result, (root / "来源-测试").resolve())
            self.assertTrue(result.is_dir())


class OutputLayoutArgumentTests(unittest.TestCase):
    def test_cli_flags_are_mutually_exclusive(self) -> None:
        parser = argparse.ArgumentParser()
        add_output_layout_args(parser)
        self.assertFalse(parser.parse_args([]).auto_output_folder)
        self.assertTrue(parser.parse_args(["--auto-output-folder"]).auto_output_folder)
        self.assertFalse(parser.parse_args(["--flat-output"]).auto_output_folder)


class PluginOutputLayoutIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repository_root = Path(__file__).resolve().parents[1]
        cls.renderer_index = (cls.repository_root / "wandao_electron" / "renderer" / "index.html").read_text(
            encoding="utf-8"
        )

    def export_directory_providers(self) -> list[tuple[Path, dict]]:
        providers = []
        for path in sorted((self.repository_root / "plugins").glob("*/providers/**/provider.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            if not data.get("capabilities", {}).get("export"):
                continue
            has_output = any(
                field.get("type") == "directory"
                and (field.get("arg") == "--output" or field.get("name") in {"output", "output_dir", "output-dir"})
                for field in data.get("fields", [])
            )
            if has_output:
                providers.append((path, data))
        return providers

    def test_every_export_directory_provider_declares_auto_layout(self) -> None:
        providers = self.export_directory_providers()
        self.assertEqual(len(providers), 18)
        for path, provider in providers:
            auto_fields = [field for field in provider.get("fields", []) if field.get("name") == "auto_output_folder"]
            self.assertEqual(len(auto_fields), 1, path)
            self.assertEqual(auto_fields[0].get("arg"), "--auto-output-folder", path)
            self.assertEqual(auto_fields[0].get("falseArg"), "--flat-output", path)

    def test_legacy_template_providers_have_a_layout_control(self) -> None:
        for path, provider in self.export_directory_providers():
            template_id = str(provider.get("templateId") or "")
            if not template_id or f'<template id="{template_id}">' not in self.renderer_index:
                continue
            control_id = f'id="{provider["id"]}-auto-output-folder"'
            self.assertIn(control_id, self.renderer_index, path)

    def test_every_export_backend_uses_the_shared_resolver(self) -> None:
        for provider_path, provider in self.export_directory_providers():
            export_actions = [action for action in provider.get("actions", []) if action.get("kind") == "export"]
            self.assertTrue(export_actions, provider_path)
            script = str(export_actions[0].get("script") or "")
            backend_path = (provider_path.parent / script).resolve()
            self.assertTrue(backend_path.is_file(), backend_path)
            source = backend_path.read_text(encoding="utf-8")
            self.assertIn("add_output_layout_args", source, backend_path)
            self.assertIn("resolve_output_directory", source, backend_path)


if __name__ == "__main__":
    unittest.main()
