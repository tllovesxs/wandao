import re
import unittest
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def read_text(rel_path: str) -> str:
    return (REPO_ROOT / rel_path).read_text(encoding="utf-8")


class ElectronHealthTests(unittest.TestCase):
    def test_browser_window_keeps_safe_renderer_defaults(self) -> None:
        main_js = read_text("wandao_electron/main.js")
        index_html = read_text("wandao_electron/renderer/index.html")

        self.assertRegex(main_js, r"nodeIntegration\s*:\s*false")
        self.assertRegex(main_js, r"contextIsolation\s*:\s*true")
        self.assertRegex(main_js, r"sandbox\s*:\s*true")
        self.assertRegex(main_js, r"preload\s*:\s*path\.join\(__dirname,\s*['\"]preload\.js['\"]\)")
        self.assertIn("setWindowOpenHandler", main_js)
        self.assertIn("will-navigate", main_js)
        self.assertIn("setPermissionRequestHandler", main_js)
        self.assertIn("Content-Security-Policy", index_html)
        self.assertNotRegex(main_js, r"webSecurity\s*:\s*false")
        self.assertNotRegex(main_js, r"allowRunningInsecureContent\s*:\s*true")

    def test_preload_channels_are_handled_by_main_process(self) -> None:
        preload_js = read_text("wandao_electron/preload.js")
        main_js = read_text("wandao_electron/main.js")

        preload_channels = set(re.findall(r"ipcRenderer\.invoke\(['\"]([^'\"]+)['\"]", preload_js))
        main_channels = set(re.findall(r"ipcMain\.handle\(['\"]([^'\"]+)['\"]", main_js))

        self.assertTrue(preload_channels)
        self.assertFalse(preload_channels - main_channels)
        self.assertIn("run-python-command", preload_channels)
        self.assertIn("get-provider-manifests", preload_channels)
        self.assertIn("protect-task-args", preload_channels)
        self.assertIn("restore-task-args", preload_channels)

    def test_task_history_encrypts_args_and_recovers_interrupted_tasks(self) -> None:
        main_js = read_text("wandao_electron/main.js")
        app_js = read_text("wandao_electron/renderer/app.js")

        self.assertIn("safeStorage.encryptString", main_js)
        self.assertIn("safeStorage.decryptString", main_js)
        self.assertIn("persistable.protectedArgs = protectedResult.payload", app_js)
        self.assertIn("persistable.args = []", app_js)
        self.assertIn("persistable.resultData = maskSensitiveValue", app_js)
        self.assertIn("persistable.logs = maskSensitiveValue", app_js)
        self.assertIn("persistable.error = maskSensitiveText", app_js)
        self.assertIn("task.status === 'running' || task.status === 'stopping'", app_js)
        self.assertIn("task.status = 'interrupted'", app_js)

    def test_python_process_lock_is_released_only_by_the_owned_process(self) -> None:
        main_js = read_text("wandao_electron/main.js")

        self.assertIn("if (pythonProcess === proc)", main_js)
        self.assertIn("function terminateProcessTree", main_js)
        self.assertIn("spawnSync('taskkill'", main_js)
        self.assertIn("process.kill(-proc.pid", main_js)
        self.assertIn("detached: process.platform !== 'win32'", main_js)
        stop_start = main_js.index("ipcMain.handle('stop-python-process'")
        stop_handler = main_js[stop_start : stop_start + 900]
        self.assertNotIn("pythonProcess = null", stop_handler)
        self.assertIn("pythonProcessStopping = true", stop_handler)
        self.assertIn("terminateProcessTree(pythonProcess)", stop_handler)

    def test_process_and_task_logs_are_bounded(self) -> None:
        main_js = read_text("wandao_electron/main.js")
        app_js = read_text("wandao_electron/renderer/app.js")

        self.assertIn("const MAX_PROCESS_OUTPUT_CHARS", main_js)
        self.assertIn("function appendOutputTail", main_js)
        self.assertIn("const MAX_TASK_LOG_ENTRIES", app_js)
        self.assertIn("activeTaskLogEntries.push(entry)", app_js)
        self.assertIn("task.logs = [...activeTaskLogEntries]", app_js)
        self.assertNotIn("task.logs = detailLogEntries.slice", app_js)

    def test_runtime_provider_validation_fails_closed(self) -> None:
        main_js = read_text("wandao_electron/main.js")

        self.assertIn("function validateProviderManifestRuntime", main_js)
        self.assertIn("Provider 目录名必须和 ID 一致", main_js)
        self.assertIn("actions[${index}].script 无效", main_js)
        self.assertNotIn("pluginScriptRef(id, action && action.script, providerRoot) || provider.script", main_js)

    def test_python_runtime_build_is_pinned_and_verified(self) -> None:
        runtime_script = read_text("wandao_electron/scripts/prepare_python_runtime.py")

        self.assertIn('PYTHON_STANDALONE_RELEASE = "20260623"', runtime_script)
        self.assertNotIn("releases/latest", runtime_script)
        self.assertIn('"sha256":', runtime_script)
        self.assertIn("def verify_archive", runtime_script)
        self.assertIn("verify_archive(temporary, expected_sha256)", runtime_script)

    def test_preload_does_not_expose_raw_ipc_or_node_modules(self) -> None:
        preload_js = read_text("wandao_electron/preload.js")
        exposed_object = preload_js.split("contextBridge.exposeInMainWorld", 1)[-1]

        self.assertIn("contextBridge.exposeInMainWorld('electronAPI'", preload_js)
        self.assertNotIn("ipcRenderer,", exposed_object)
        self.assertNotIn("require,", exposed_object)
        self.assertNotIn("process,", exposed_object)

    def test_remote_text_fetch_is_limited_to_project_docs(self) -> None:
        main_js = read_text("wandao_electron/main.js")

        self.assertIn("function isAllowedRemoteTextUrl", main_js)
        self.assertIn("parsed.protocol !== 'https:'", main_js)
        self.assertIn("raw.githubusercontent.com", main_js)
        self.assertIn("/tllovesxs/wandao/", main_js)
        self.assertIn("公告文档超过 1MB", main_js)

    def test_file_and_external_ipc_have_main_process_boundaries(self) -> None:
        main_js = read_text("wandao_electron/main.js")

        self.assertIn("function resolveManagedFilePath", main_js)
        self.assertIn("managedFileRoots", main_js)
        self.assertIn("resolveManagedFilePath(filePath, { allowProjectRoot: true })", main_js)
        self.assertIn("resolveManagedFilePath(filePath)", main_js)
        self.assertIn("function isAllowedExternalUrl", main_js)
        self.assertIn("return parsed.protocol === 'https:'", main_js)
        self.assertIn("if (!isAllowedExternalUrl(url))", main_js)
        self.assertNotIn("root.startsWith(app.getPath('userData'))", main_js)

    def test_settings_have_schema_version_and_normalization(self) -> None:
        main_js = read_text("wandao_electron/main.js")

        self.assertIn("const SETTINGS_SCHEMA_VERSION = 1", main_js)
        self.assertIn("function normalizeAppSettings", main_js)
        self.assertIn("schemaVersion: settings.schemaVersion || SETTINGS_SCHEMA_VERSION", main_js)
        self.assertIn("next.schemaVersion = SETTINGS_SCHEMA_VERSION", main_js)

    def test_log_panel_uses_bounded_batch_rendering(self) -> None:
        app_js = read_text("wandao_electron/renderer/app.js")

        self.assertIn("const LOG_PANEL_RENDER_LIMIT = 400", app_js)
        self.assertIn("function visibleLogEntries", app_js)
        self.assertIn("document.createDocumentFragment()", app_js)
        self.assertIn("logContent.replaceChildren()", app_js)
        self.assertIn("trimRenderedLogEntries(logContent)", app_js)
        self.assertIn("为保持界面流畅", app_js)

    def test_startup_does_not_wait_for_provider_discovery(self) -> None:
        app_js = read_text("wandao_electron/renderer/app.js")
        startup = app_js[app_js.index("document.addEventListener('DOMContentLoaded'") :]

        self.assertLess(startup.index("renderProviderNavigation();"), startup.index("loadProviderManifests().then"))
        self.assertNotIn("await loadProviderManifests()", startup)
        self.assertIn("currentTool === 'home' || currentTool === 'platform-center'", startup)
        self.assertEqual(startup.count("if (currentTool === DEFAULT_VIEW_ID) switchTool(DEFAULT_VIEW_ID);"), 2)

    def test_settings_log_toggle_does_not_rerender_whole_settings_page(self) -> None:
        app_js = read_text("wandao_electron/renderer/app.js")

        marker = "querySelector('[data-settings-action=\"log-mode\"]')?.addEventListener"
        start = app_js.find(marker)
        self.assertGreater(start, -1)
        handler = app_js[start : start + 500]
        self.assertIn("toggleLogViewMode()", handler)
        self.assertIn("data-settings-log-mode-summary", app_js)
        self.assertNotIn("renderSettingsPage()", handler)

    def test_desktop_design_system_keeps_accessible_app_shell(self) -> None:
        index_html = read_text("wandao_electron/renderer/index.html")
        styles = read_text("wandao_electron/renderer/styles.css")
        app_js = read_text("wandao_electron/renderer/app.js")
        design = read_text("wandao_electron/DESIGN.md")

        self.assertIn('--brand: #9fe870', styles)
        self.assertIn('--surface: #e8ebe6', styles)
        self.assertIn('--shell-start: #eaf3f7', styles)
        self.assertIn('--r-lg: 24px', styles)
        self.assertIn('linear-gradient(168deg, var(--shell-start)', styles)
        self.assertIn('@media (prefers-reduced-motion: reduce)', styles)
        self.assertIn('class="skip-link"', index_html)
        self.assertIn('id="main-content" tabindex="-1"', index_html)
        self.assertIn('role="progressbar"', index_html)
        self.assertIn('id="btn-toggle-log"', index_html)
        self.assertIn('<nav class="nav-group" aria-label="工作台">', app_js)
        self.assertIn("function setLogCollapsed", app_js)
        self.assertIn("function normalizeActionHierarchy", app_js)
        self.assertIn("选择平台 -> 执行任务 -> 本地 Markdown", design)

    def test_build_workflow_uses_supported_node_version(self) -> None:
        workflow = read_text(".github/workflows/build-desktop.yml")
        package = json.loads(read_text("wandao_electron/package.json"))

        self.assertGreaterEqual(workflow.count('node-version: "22"'), 3)
        self.assertIn("windows-latest, ubuntu-latest, macos-latest", workflow)
        self.assertIn('python: ["3.10", "3.11"]', workflow)
        self.assertIn("PR Windows Package Smoke", workflow)
        self.assertIn("scripts/package_smoke.py --resources wandao_electron/dist/win-unpacked/resources", workflow)
        package_smoke = read_text("scripts/package_smoke.py")
        self.assertIn("verify_packaged_backend_help", package_smoke)
        self.assertIn('"--provider", provider_id, "--", "--help"', package_smoke)
        self.assertEqual(package["engines"]["node"], ">=22.12.0")
        self.assertEqual(package["build"]["electronDist"], "node_modules/electron/dist")
        self.assertNotIn("signExecutable", package["build"]["win"])
        self.assertTrue(package["build"]["mac"]["hardenedRuntime"])
        self.assertIn('CSC_IDENTITY_AUTO_DISCOVERY: "false"', workflow)
        self.assertNotIn("Require signing credentials for a release tag", workflow)
        self.assertNotIn("CSC_LINK:", workflow)
        self.assertIn("actions/attest-build-provenance", workflow)
        self.assertIn("Generate release SBOM", workflow)

    def test_bootstrap_node_runtime_is_pinned_and_verified(self) -> None:
        powershell = read_text("start-wandao.ps1")
        shell = read_text("start-wandao.sh")

        self.assertIn('$NodeVersion = "v22.12.0"', powershell)
        self.assertIn('NODE_VERSION="v22.12.0"', shell)
        self.assertIn("Get-FileHash", powershell)
        self.assertIn("verify_sha256", shell)
        self.assertIn("2b8f2256382f97ad51e29ff71f702961af466c4616393f767455501e6aece9b8", powershell)
        self.assertIn("22982235e1b71fa8850f82edd09cdae7e3f32df1764a9ec298c72d25ef2c164f", shell)

    def test_running_task_requires_confirmation_before_exit(self) -> None:
        main_js = read_text("wandao_electron/main.js")
        app_js = read_text("wandao_electron/renderer/app.js")
        index_html = read_text("wandao_electron/renderer/index.html")

        self.assertIn("function confirmTaskShutdown", main_js)
        self.assertIn("停止任务并退出", main_js)
        self.assertIn("mainWindow.on('close'", main_js)
        self.assertIn('id="btn-global-stop"', index_html)
        self.assertIn("btn-global-stop", app_js)

    def test_plugin_center_always_shows_bundled_platform_plugins(self) -> None:
        main_js = read_text("wandao_electron/main.js")
        app_js = read_text("wandao_electron/renderer/app.js")

        self.assertIn("function bundledPluginCatalogEntries", main_js)
        self.assertIn("function pluginCatalogWithBundled", main_js)
        self.assertIn("plugins: pluginCatalogWithBundled(combined)", main_js)
        self.assertIn("plugins: pluginCatalogWithBundled()", main_js)
        self.assertIn("随主程序提供", app_js)
        self.assertIn("安装更新", app_js)

    def test_plugin_release_channels_are_visible_for_experimental_plugins(self) -> None:
        main_js = read_text("wandao_electron/main.js")
        app_js = read_text("wandao_electron/renderer/app.js")
        workflow = read_text(".github/workflows/publish-plugins.yml")

        self.assertIn("EXPERIMENTAL_PLUGIN_REGISTRY_URL", main_js)
        self.assertIn("currentPluginRegistry(Boolean(options?.refresh), 'experimental')", main_js)
        self.assertNotIn("includeExperimental", main_js)
        self.assertIn("plugins-experimental", workflow)
        self.assertIn("dist-plugins/stable", workflow)
        self.assertIn("dist-plugins/experimental", workflow)
        self.assertIn("实验性插件已标注", app_js)
        self.assertIn("实验性 · 主动测试", app_js)

    def test_platform_discovery_links_to_searchable_plugin_center(self) -> None:
        app_js = read_text("wandao_electron/renderer/app.js")
        styles = read_text("wandao_electron/renderer/styles.css")

        self.assertIn("去插件中心找更多平台", app_js)
        self.assertIn('data-switch-view="plugin-center"', app_js)
        self.assertIn("function filteredPluginCatalog", app_js)
        self.assertIn("data-plugin-search", app_js)
        self.assertIn("搜索平台、功能、发布者或权限", app_js)
        self.assertIn(".plugin-search-row", styles)

    def test_task_history_has_minimal_failure_diagnostics(self) -> None:
        app_js = read_text("wandao_electron/renderer/app.js")

        self.assertIn("function taskFailureDiagnostics", app_js)
        self.assertIn("data-history-action=\"copy-failures\"", app_js)
        self.assertIn("function copyTaskFailures", app_js)
        self.assertIn("button.dataset.historyAction === 'copy-failures'", app_js)
        self.assertIn("if (task.status === 'running') return false", app_js)
        self.assertIn("该平台暂未声明失败项重试能力", app_js)

    def test_manifest_action_fields_can_be_scoped_per_action(self) -> None:
        app_js = read_text("wandao_electron/renderer/app.js")
        schema = read_text("providers/provider.schema.json")
        guide = read_text("docs/插件开发指南.md")

        self.assertIn("function manifestFieldAppliesToAction", app_js)
        self.assertIn("field.actions || field.includeActions || field.onlyActions", app_js)
        self.assertIn("field.excludeActions || field.skipActions", app_js)
        self.assertIn("isManifestOutputField(field) && !manifestActionUsesOutput(action)", app_js)
        self.assertIn('"includeActions"', schema)
        self.assertIn('"excludeActions"', schema)
        self.assertIn("字段默认会参与所有动作", guide)

    def test_scan_toc_passes_provider_id_to_python_process(self) -> None:
        app_js = read_text("wandao_electron/renderer/app.js")

        self.assertIn("runPythonCommand(config.script, args, {", app_js)
        self.assertIn("providerId: toolId", app_js)

    def test_main_process_rejects_parallel_python_tasks(self) -> None:
        main_js = read_text("wandao_electron/main.js")
        marker = "ipcMain.handle('run-python-command'"
        start = main_js.find(marker)
        self.assertGreater(start, -1)
        handler = main_js[start : start + 500]

        self.assertIn("if (pythonProcess)", handler)
        self.assertIn("已有任务正在运行", handler)

    def test_main_process_compresses_large_doc_id_selection_for_exporters(self) -> None:
        main_js = read_text("wandao_electron/main.js")

        self.assertIn("function compressDocIdArgs", main_js)
        self.assertNotIn("const supported = new Set", main_js)
        self.assertIn("if (value === '--doc-id'", main_js)
        self.assertIn("return [...compactArgs, '--doc-id-file', filePath]", main_js)

    def test_group_toc_progress_is_labeled_as_topic_list_reading(self) -> None:
        structured_logs_js = read_text("wandao_electron/renderer/structured_logs.js")

        self.assertIn("stats.groupPage", structured_logs_js)
        self.assertIn("帖子列表读取：已读取", structured_logs_js)
        self.assertIn("帖子列表读取 ${current}/${total || '?'}", structured_logs_js)
        self.assertIn("已跳过视频帖", structured_logs_js)

    def test_zsxq_group_and_column_are_separate_providers(self) -> None:
        group_provider = json.loads(read_text("plugins/zsxq/providers/zsxq-group/provider.json"))
        column_provider = json.loads(read_text("plugins/zsxq/providers/zsxq-column/provider.json"))
        index_html = read_text("wandao_electron/renderer/index.html")
        app_js = read_text("wandao_electron/renderer/app.js")

        self.assertEqual(group_provider["id"], "zsxq-group")
        self.assertEqual(column_provider["id"], "zsxq-column")
        self.assertFalse(group_provider["capabilities"]["scanToc"])
        self.assertTrue(column_provider["capabilities"]["scanToc"])
        self.assertEqual(group_provider["checkpoint"]["strategy"], "cursor")
        self.assertEqual(column_provider["checkpoint"]["strategy"], "items")
        self.assertTrue(group_provider["checkpoint"]["resourceTracking"])
        self.assertTrue(column_provider["checkpoint"]["resourceTracking"])
        self.assertEqual(group_provider["retryFailures"]["arg"], "--retry-failed")
        self.assertIn('template id="template-zsxq-group"', index_html)
        self.assertIn('template id="template-zsxq-column"', index_html)
        self.assertIn('id="zsxq-group-download-files"', index_html)
        self.assertIn('id="zsxq-column-download-files"', index_html)
        self.assertIn("confirmLargeZsxqGroupExport", app_js)
        self.assertIn("function providerCheckpointFile", app_js)
        self.assertIn("args.push('--checkpoint-file', checkpointFile, '--resume')", app_js)
        self.assertIn("limit <= 1000", app_js)
        self.assertIn("单次任务超过 24 小时", app_js)
        self.assertNotIn("知识星球 Group 单次最多导出 500 条", app_js)
        self.assertIn("validateZsxqUrlForTool", app_js)
        self.assertIn("toolId === 'zsxq-column'", app_js)

    def test_checkpoint_is_declared_for_adapted_export_providers_only(self) -> None:
        provider_paths = {
            "yuque": "plugins/yuque/providers/yuque/provider.json",
            "aliyun": "plugins/aliyun_thoughts/providers/aliyun/provider.json",
            "yinxiang": "plugins/yinxiang/providers/yinxiang/provider.json",
            "youdao": "plugins/youdao/providers/youdao/provider.json",
            "onenote": "plugins/onenote/providers/onenote/provider.json",
            "ima-export": "plugins/ima/providers/ima-export/provider.json",
            "zsxq-group": "plugins/zsxq/providers/zsxq-group/provider.json",
            "zsxq-column": "plugins/zsxq/providers/zsxq-column/provider.json",
            "feishu-export": "plugins/feishu/providers/feishu-export/provider.json",
            "wiz": "plugins/wiz/providers/wiz/provider.json",
        }
        providers = {provider_id: json.loads(read_text(path)) for provider_id, path in provider_paths.items()}
        for provider_id in ["yuque", "aliyun", "yinxiang", "youdao", "onenote", "ima-export"]:
            self.assertEqual(providers[provider_id]["checkpoint"]["strategy"], "items")
            self.assertFalse(providers[provider_id]["checkpoint"]["resourceTracking"])
        self.assertEqual(providers["zsxq-group"]["checkpoint"]["strategy"], "cursor")
        for provider_id in ["zsxq-group", "zsxq-column", "feishu-export", "wiz"]:
            self.assertTrue(providers[provider_id]["checkpoint"]["resourceTracking"])
        ima_import = json.loads(read_text("plugins/ima/providers/ima-import/provider.json"))
        self.assertNotIn("checkpoint", ima_import)

    def test_checkpoint_runtime_is_bundled_for_packaged_app(self) -> None:
        package_json = read_text("wandao_electron/package.json")
        package = json.loads(package_json)
        package_lock = json.loads(read_text("wandao_electron/package-lock.json"))
        pyproject = read_text("pyproject.toml")

        self.assertRegex(package["version"], r"^\d+\.\d+\.\d+$")
        self.assertEqual(package["version"], package_lock["version"])
        self.assertEqual(package["version"], package_lock["packages"][""]["version"])
        python_resource = next(item for item in package["build"]["extraResources"] if item.get("to") == "python")
        self.assertEqual(python_resource["from"], "..")
        self.assertIn("*.py", python_resource["filter"])
        self.assertIn("wandao_core/**/*", python_resource["filter"])
        self.assertIn("requirements.txt", python_resource["filter"])
        self.assertIn(f'version = "{package["version"]}"', pyproject)
        self.assertIn('"wandao_checkpoint"', pyproject)
        self.assertIn('"wandao_cli"', pyproject)

    def test_provider_python_scripts_are_bundled_for_packaged_app(self) -> None:
        package = json.loads(read_text("wandao_electron/package.json"))
        resources = package["build"]["extraResources"]
        plugin_resource = next(item for item in resources if item.get("to") == "plugins")
        python_resource = next(item for item in resources if item.get("to") == "python")
        required_common = {
            "wandao_logging.py",
            "wandao_report.py",
            "wandao_checkpoint.py",
            "wandao_cli.py",
            "wandao_credentials.py",
            "wandao_browser.py",
            "gui_utils.py",
        }
        self.assertEqual(plugin_resource["from"], "../plugins")
        self.assertEqual(python_resource["from"], "..")
        self.assertTrue((REPO_ROOT / "wandao_core" / "__init__.py").is_file())
        self.assertTrue(required_common.issubset({path.name for path in REPO_ROOT.glob("*.py")}))
        for manifest_path in (REPO_ROOT / "plugins").glob("*/plugin.json"):
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for provider_path in manifest["entrypoints"]["providers"]:
                provider_file = manifest_path.parent / provider_path
                provider = json.loads(provider_file.read_text(encoding="utf-8"))
                for action in provider.get("actions", []):
                    script = action.get("script") or provider.get("script")
                    if script:
                        self.assertTrue((provider_file.parent / script).resolve().is_file())

    def test_platform_scripts_only_come_from_plugins_or_file_providers(self) -> None:
        main_js = read_text("wandao_electron/main.js")
        providers_js = read_text("wandao_electron/renderer/providers.js")
        self.assertNotIn("ALLOWED_SCRIPTS", main_js)
        self.assertIn("bundled-plugin:", main_js)
        self.assertIn("平台脚本必须来自 Plugin v1 或文件型 Provider", main_js)
        self.assertNotRegex(providers_js, r"(?:export|import)_[a-z0-9_]+\.py")

    def test_plugin_process_environment_uses_an_allowlist(self) -> None:
        main_js = read_text("wandao_electron/main.js")

        self.assertIn("const PLUGIN_ENV_ALLOWLIST = new Set", main_js)
        self.assertIn("pluginHostEnvironment()", main_js)
        self.assertIn("pluginContext ? pluginHostEnvironment() : process.env", main_js)
        self.assertNotIn("WANDAO_PLUGIN_PRIVATE_KEY)/i.test(key)", main_js)

    def test_plugins_are_signed_sandboxed_and_official_plugins_are_bundled(self) -> None:
        main_js = read_text("wandao_electron/main.js")
        preload_js = read_text("wandao_electron/preload.js")
        app_js = read_text("wandao_electron/renderer/app.js")
        providers_js = read_text("wandao_electron/renderer/providers.js")
        package = json.loads(read_text("wandao_electron/package.json"))

        self.assertIn("new PluginManager", main_js)
        self.assertIn("providerEntriesWithErrors", main_js)
        self.assertIn("get-plugin-catalog", main_js)
        self.assertIn("get-plugin-ui", main_js)
        self.assertIn("getPluginCatalog", preload_js)
        self.assertIn("installPlugin", preload_js)
        self.assertIn('sandbox="allow-scripts"', app_js)
        self.assertNotIn('sandbox="allow-scripts allow-same-origin"', app_js)
        self.assertIn("default-src 'none'", app_js)
        self.assertIn("replaceExternal", providers_js)
        self.assertNotIn("id: 'wiz'", providers_js)
        self.assertNotIn("id: 'feishu-export'", providers_js)
        plugin_resource = next(item for item in package["build"]["extraResources"] if item.get("to") == "plugins")
        self.assertEqual(plugin_resource["from"], "../plugins")
        self.assertIn("bundledPluginEntriesWithErrors", main_js)
        self.assertIn("installedPlugin?.enabled", main_js)


if __name__ == "__main__":
    unittest.main()
