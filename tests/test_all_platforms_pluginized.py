import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGINS_ROOT = REPO_ROOT / "plugins"


EXPECTED_PLUGINS = {
    "aliyun_thoughts": {"aliyun"},
    "dingtalk": {"dingtalk-export"},
    "feishu": {"feishu-export", "feishu-import"},
    "ima": {"ima-export", "ima-import"},
    "notion": {"notion"},
    "obsidian": {"obsidian-export"},
    "onenote": {"onenote"},
    "wiz": {"wiz"},
    "wps": {"wps-export"},
    "xiliu": {"xiliu", "xiliu-import"},
    "yinxiang": {"yinxiang", "yinxiang-import"},
    "youdao": {"youdao"},
    "yuque": {"yuque", "yuque-import"},
    "zsxq": {"zsxq-group", "zsxq-column"},
}


class AllPlatformsPluginizedTests(unittest.TestCase):
    def plugin_manifests(self) -> dict[str, tuple[Path, dict]]:
        result = {}
        for path in PLUGINS_ROOT.glob("*/plugin.json"):
            manifest = json.loads(path.read_text(encoding="utf-8"))
            result[manifest["id"]] = (path, manifest)
        return result

    def test_every_supported_platform_is_a_plugin(self) -> None:
        manifests = self.plugin_manifests()
        self.assertEqual(set(manifests), set(EXPECTED_PLUGINS))
        for plugin_id, expected_provider_ids in EXPECTED_PLUGINS.items():
            manifest_path, manifest = manifests[plugin_id]
            provider_ids = set()
            for relative in manifest["entrypoints"]["providers"]:
                provider_path = manifest_path.parent / relative
                provider = json.loads(provider_path.read_text(encoding="utf-8"))
                provider_ids.add(provider["id"])
            self.assertEqual(provider_ids, expected_provider_ids, plugin_id)

    def test_plugin_action_scripts_have_a_working_help_entrypoint(self) -> None:
        scripts = set()
        for manifest_path, manifest in self.plugin_manifests().values():
            for relative in manifest["entrypoints"]["providers"]:
                provider_path = manifest_path.parent / relative
                provider = json.loads(provider_path.read_text(encoding="utf-8"))
                for action in provider.get("actions", []):
                    script = action.get("script") or provider.get("script")
                    if script:
                        scripts.add((provider_path.parent / script).resolve())

        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(REPO_ROOT), env.get("PYTHONPATH", "")]))
        # Help text is part of the CLI contract and contains Chinese text. Force
        # a portable encoding instead of inheriting a Windows runner code page.
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        for script in sorted(scripts):
            with self.subTest(script=script.relative_to(REPO_ROOT)):
                result = subprocess.run(
                    [sys.executable, str(script), "--help"],
                    cwd=REPO_ROOT,
                    env=env,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    capture_output=True,
                    timeout=20,
                )
                self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_desktop_core_contains_no_platform_registry_or_script_allowlist(self) -> None:
        providers_js = (REPO_ROOT / "wandao_electron/renderer/providers.js").read_text(encoding="utf-8")
        main_js = (REPO_ROOT / "wandao_electron/main.js").read_text(encoding="utf-8")
        self.assertNotIn("ALLOWED_SCRIPTS", main_js)
        self.assertNotIn("id: 'zsxq-group'", providers_js)
        self.assertNotIn("id: 'yuque'", providers_js)
        self.assertIn("bundledPluginEntriesWithErrors", main_js)

    def test_root_platform_modules_are_compatibility_only(self) -> None:
        entrypoints = [
            "export_aliyun_thoughts.py",
            "export_feishu.py",
            "export_onenote.py",
            "export_wiz.py",
            "export_yinxiang.py",
            "export_youdao.py",
            "export_yuque.py",
            "export_zsxq.py",
            "ima_knowledge.py",
            "import_feishu.py",
            "import_yinxiang.py",
            "import_yuque.py",
        ]
        for filename in entrypoints:
            with self.subTest(filename=filename):
                source = (REPO_ROOT / filename).read_text(encoding="utf-8")
                self.assertIn("Compatibility", source)
                self.assertLessEqual(len(source.splitlines()), 12)

    def test_plugin_backends_use_shared_core_not_legacy_facades(self) -> None:
        legacy_imports = (
            "from wandao_browser import",
            "from wandao_checkpoint import",
            "from wandao_credentials import",
            "from wandao_logging import",
            "from wandao_report import",
        )
        core = REPO_ROOT / "wandao_core"
        self.assertTrue((core / "__init__.py").is_file())
        for module in ("browser.py", "checkpoint.py", "credentials.py", "logging.py", "report.py"):
            self.assertTrue((core / module).is_file(), module)
        for backend in PLUGINS_ROOT.glob("*/backend/*.py"):
            source = backend.read_text(encoding="utf-8")
            with self.subTest(backend=backend.relative_to(REPO_ROOT)):
                self.assertFalse(any(token in source for token in legacy_imports))


if __name__ == "__main__":
    unittest.main()
