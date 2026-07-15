import json
import tempfile
import unittest
from pathlib import Path

from scripts.validate_providers import (
    provider_manifest_paths,
    validate_provider_manifest,
    validate_repository,
    validate_toc,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class ProviderValidationTests(unittest.TestCase):
    def test_current_repository_provider_metadata_is_valid(self) -> None:
        issues = validate_repository(REPO_ROOT)

        self.assertEqual([issue.format(REPO_ROOT) for issue in issues], [])

    def test_repository_discovery_includes_plugin_providers_and_templates(self) -> None:
        paths = {path.relative_to(REPO_ROOT).as_posix() for path in provider_manifest_paths(REPO_ROOT)}

        self.assertIn("plugins/feishu/providers/feishu-export/provider.json", paths)
        self.assertIn("plugins/yuque/providers/yuque/provider.json", paths)
        self.assertIn("providers/_template_standard/provider.json", paths)

    def test_rejects_provider_script_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider_root = root / "providers" / "bad"
            provider_root.mkdir(parents=True)
            (provider_root / "README.md").write_text("# Bad\n", encoding="utf-8")
            write_json(
                provider_root / "provider.json",
                {
                    "schemaVersion": 1,
                    "id": "bad",
                    "name": "Bad",
                    "title": "Bad Provider",
                    "description": "Bad provider",
                    "type": "hybrid",
                    "group": "export",
                    "trustLevel": "community",
                    "status": "experimental",
                    "guide": "README.md",
                    "capabilities": {"export": True, "guide": True, "scanToc": False},
                    "actions": [{"id": "export", "label": "导出", "script": "../evil.py", "args": []}],
                },
            )

            issues = validate_provider_manifest(provider_root / "provider.json", root)

            self.assertTrue(any("不能跳出 provider 目录" in issue.message for issue in issues))

    def test_rejects_scan_capability_without_scan_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider_root = root / "providers" / "scanless"
            provider_root.mkdir(parents=True)
            (provider_root / "README.md").write_text("# Scanless\n", encoding="utf-8")
            (provider_root / "actions.py").write_text("print('{}')\n", encoding="utf-8")
            write_json(
                provider_root / "provider.json",
                {
                    "schemaVersion": 1,
                    "id": "scanless",
                    "name": "Scanless",
                    "title": "Scanless Provider",
                    "description": "Scanless provider",
                    "type": "hybrid",
                    "group": "export",
                    "trustLevel": "community",
                    "status": "experimental",
                    "guide": "README.md",
                    "capabilities": {"export": True, "guide": True, "scanToc": True},
                    "actions": [{"id": "export", "label": "导出", "script": "actions.py", "args": []}],
                },
            )

            issues = validate_provider_manifest(provider_root / "provider.json", root)

            self.assertTrue(any("scanToc" in issue.message for issue in issues))
            self.assertTrue(any("非空 toc" in issue.message for issue in issues))

    def test_rejects_retry_failures_without_retry_arg(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider_root = root / "providers" / "retryless"
            provider_root.mkdir(parents=True)
            (provider_root / "README.md").write_text("# Retryless\n", encoding="utf-8")
            (provider_root / "actions.py").write_text("print('{}')\n", encoding="utf-8")
            write_json(
                provider_root / "provider.json",
                {
                    "schemaVersion": 1,
                    "id": "retryless",
                    "name": "Retryless",
                    "title": "Retryless Provider",
                    "description": "Retryless provider",
                    "type": "hybrid",
                    "group": "import",
                    "trustLevel": "community",
                    "status": "experimental",
                    "guide": "README.md",
                    "capabilities": {"import": True, "guide": True, "scanToc": False, "retryFailures": True},
                    "actions": [{"id": "import", "label": "导入", "script": "actions.py", "args": []}],
                },
            )

            issues = validate_provider_manifest(provider_root / "provider.json", root)

            self.assertTrue(any("retryFailures" in issue.message for issue in issues))

    def test_accepts_retry_failures_with_retry_arg(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider_root = root / "providers" / "retryable"
            provider_root.mkdir(parents=True)
            (provider_root / "README.md").write_text("# Retryable\n", encoding="utf-8")
            (provider_root / "actions.py").write_text("print('{}')\n", encoding="utf-8")
            write_json(
                provider_root / "provider.json",
                {
                    "schemaVersion": 1,
                    "id": "retryable",
                    "name": "Retryable",
                    "title": "Retryable Provider",
                    "description": "Retryable provider",
                    "type": "hybrid",
                    "group": "import",
                    "trustLevel": "community",
                    "status": "experimental",
                    "guide": "README.md",
                    "capabilities": {"import": True, "guide": True, "scanToc": False, "retryFailures": True},
                    "retryFailures": {"arg": "--retry-failures", "label": "只重试失败项"},
                    "actions": [{"id": "import", "label": "导入", "script": "actions.py", "args": []}],
                },
            )

            issues = validate_provider_manifest(provider_root / "provider.json", root)

            self.assertEqual([issue.message for issue in issues], [])

    def test_rejects_invalid_toc_type_selection_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider_root = root / "providers" / "bad-toc"
            provider_root.mkdir(parents=True)
            (provider_root / "README.md").write_text("# Bad TOC\n", encoding="utf-8")
            (provider_root / "actions.py").write_text("print('{}')\n", encoding="utf-8")
            write_json(
                provider_root / "provider.json",
                {
                    "schemaVersion": 1,
                    "id": "bad-toc",
                    "name": "Bad TOC",
                    "title": "Bad TOC Provider",
                    "description": "Bad toc provider",
                    "type": "hybrid",
                    "group": "export",
                    "trustLevel": "community",
                    "status": "experimental",
                    "guide": "README.md",
                    "capabilities": {"export": True, "guide": True, "scanToc": False},
                    "toc": {"typeKey": False, "selectableTypes": "DOC"},
                    "actions": [{"id": "export", "label": "导出", "script": "actions.py", "args": []}],
                },
            )

            issues = validate_provider_manifest(provider_root / "provider.json", root)

            messages = [issue.message for issue in issues]
            self.assertIn("toc.selectableTypes 必须是字符串或数字数组", messages)
            self.assertIn("toc.typeKey 必须是非空字符串", messages)

    def test_rejects_scan_toc_without_toc_definition(self) -> None:
        issues = validate_toc({"capabilities": {"scanToc": True}}, Path("provider.json"))

        self.assertEqual([issue.message for issue in issues], ["capabilities.scanToc 为 true 时需要声明非空 toc 对象"])

    def test_rejects_incomplete_standard_toc_contract(self) -> None:
        issues = validate_toc(
            {
                "capabilities": {"scanToc": True},
                "toc": {"itemsPath": "nodes", "selectionArg": "doc-id"},
            },
            Path("provider.json"),
        )
        messages = [issue.message for issue in issues]

        self.assertIn("标准 toc 缺少必填非空字符串：idKey", messages)
        self.assertIn("标准 toc 缺少必填非空字符串：exportIdKey", messages)
        self.assertIn("标准 toc 需要 selectableKey，或同时声明 typeKey 和非空 selectableTypes", messages)
        self.assertIn("toc.selectionArg 必须是以 -- 开头的脚本参数", messages)

    def test_toc_adapter_must_be_implemented_by_renderer(self) -> None:
        unsupported = validate_toc(
            {
                "capabilities": {"scanToc": True},
                "toc": {"adapter": "unknown-adapter", "itemsPath": "nodes", "selectionArg": "--doc-id"},
            },
            Path("provider.json"),
        )
        supported = validate_toc(
            {
                "capabilities": {"scanToc": True},
                "toc": {"adapter": "yinxiang-notebooks", "itemsPath": "notebooks", "selectionArg": "--doc-id"},
            },
            Path("provider.json"),
        )

        self.assertTrue(any("toc.adapter 不支持" in issue.message for issue in unsupported))
        self.assertEqual(supported, [])


if __name__ == "__main__":
    unittest.main()
