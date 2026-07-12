import json
import tempfile
import unittest
from pathlib import Path

from scripts.validate_providers import validate_provider_manifest, validate_repository


REPO_ROOT = Path(__file__).resolve().parents[1]


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class ProviderValidationTests(unittest.TestCase):
    def test_current_repository_provider_metadata_is_valid(self) -> None:
        issues = validate_repository(REPO_ROOT)

        self.assertEqual([issue.format(REPO_ROOT) for issue in issues], [])

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

            self.assertEqual(
                sorted(issue.message for issue in issues),
                ["toc.selectableTypes 必须是字符串或数字数组", "toc.typeKey 必须是字符串"],
            )


if __name__ == "__main__":
    unittest.main()
