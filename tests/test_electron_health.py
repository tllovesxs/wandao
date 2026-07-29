import re
import unittest
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def read_text(rel_path: str) -> str:
    return (REPO_ROOT / rel_path).read_text(encoding="utf-8")


class TauriHealthTests(unittest.TestCase):
    def test_tauri_window_keeps_safe_renderer_defaults(self) -> None:
        tauri_config = json.loads(read_text("wandao_electron/src-tauri/tauri.conf.json"))
        index_html = read_text("wandao_electron/renderer/index.html")
        security = tauri_config["app"]["security"]

        self.assertEqual(tauri_config["$schema"], "https://schema.tauri.app/config/2")
        self.assertTrue(tauri_config["app"]["withGlobalTauri"])
        self.assertTrue(security["freezePrototype"])
        self.assertIn("default-src 'self'", security["csp"])
        self.assertIn("object-src 'none'", security["csp"])
        self.assertIn("base-uri 'none'", security["csp"])
        self.assertIn("form-action 'none'", security["csp"])
        self.assertNotIn("'unsafe-eval'", security["csp"])
        self.assertEqual(security["csp"].count("http://"), 1)
        self.assertIn("http://ipc.localhost", security["csp"])
        self.assertIn("Content-Security-Policy", index_html)
        self.assertIn('<script src="tauri_bridge.js"></script>', index_html)

    def test_bridge_commands_are_registered_by_tauri(self) -> None:
        bridge_js = read_text("wandao_electron/renderer/tauri_bridge.js")
        lib_rs = read_text("wandao_electron/src-tauri/src/lib.rs")

        command_list = bridge_js[
            bridge_js.index("const COMMAND_NAMES") : bridge_js.index("const EVENT_NAMES")
        ]
        bridge_commands = set(re.findall(r"'([a-z][a-z0-9_]*)'", command_list))
        handler_list = lib_rs[
            lib_rs.index("tauri::generate_handler![") : lib_rs.index("])", lib_rs.index("tauri::generate_handler!["))
        ]
        rust_commands = set(re.findall(r"commands::([a-z][a-z0-9_]*)", handler_list))

        self.assertTrue(bridge_commands)
        self.assertFalse(bridge_commands - rust_commands)
        self.assertIn("run_python_command", bridge_commands)
        self.assertIn("get_provider_manifests", bridge_commands)
        self.assertIn("protect_task_args", bridge_commands)
        self.assertIn("restore_task_args", bridge_commands)

    def test_task_history_encrypts_args_and_recovers_interrupted_tasks(self) -> None:
        commands_rs = read_text("wandao_electron/src-tauri/src/commands.rs")
        security_rs = read_text("wandao_electron/src-tauri/src/security.rs")
        app_js = read_text("wandao_electron/renderer/app.js")

        self.assertIn("protect_bytes(&plain)", commands_rs)
        restore_start = commands_rs.index("pub async fn restore_task_args")
        restore_end = commands_rs.index("\n#[tauri::command]", restore_start)
        restore_command = commands_rs[restore_start:restore_end]
        self.assertRegex(
            restore_command,
            r"unprotect_bytes_for_user_data\s*\(\s*&encrypted\s*,\s*&state\.paths\.user_data\s*\)",
        )
        self.assertNotIn("unprotect_bytes(&encrypted)", restore_command)
        self.assertIn("CryptProtectData", security_rs)
        self.assertIn("CryptUnprotectData", security_rs)
        self.assertIn("macos_safe_storage", security_rs)
        self.assertIn('encrypted.starts_with(b"v10")', security_rs)
        self.assertRegex(
            security_rs,
            r"#\s*\[\s*test\s*\]\s*fn\s+unprotects_synthetic_electron_v10_payload\s*\(",
        )
        self.assertIn('user_data.0.join("Local State")', security_rs)
        self.assertIn('value.get("encrypted_key")', security_rs)
        self.assertIn("persistable.protectedArgs = protectedResult.payload", app_js)
        self.assertIn("persistable.args = []", app_js)
        self.assertIn("persistable.resultData = maskSensitiveValue", app_js)
        self.assertIn("persistable.logs = maskSensitiveValue", app_js)
        self.assertIn("persistable.error = maskSensitiveText", app_js)
        self.assertIn("task.status === 'running' || task.status === 'stopping'", app_js)
        self.assertIn("task.status = 'interrupted'", app_js)
        self.assertIn("if (task.argsUnavailable)", app_js)
        self.assertIn("if (taskHistoryLoadPromise) await taskHistoryLoadPromise", app_js)
        self.assertIn("任务历史尚未安全加载", app_js)
        runner_start = app_js.index("async function runTrackedPythonCommand")
        runner_end = app_js.index("async function runProviderCommand", runner_start)
        runner = app_js[runner_start:runner_end]
        self.assertLess(runner.index("await taskHistoryLoadPromise"), runner.index("startHistoryTask("))

    def test_task_runtime_owns_process_target_and_stop_state(self) -> None:
        tasks_rs = read_text("wandao_electron/src-tauri/src/tasks.rs")

        self.assertIn("pub struct TaskRuntime", tasks_rs)
        self.assertIn("pub fn request_stop", tasks_rs)
        self.assertIn("pub fn force_stop", tasks_rs)
        self.assertRegex(tasks_rs, r"struct\s+ProcessTarget\s*\{")
        self.assertRegex(
            tasks_rs,
            r"type\s+ProcessTerminator\s*=\s*Arc<dyn\s+Fn\s*\(\s*&ProcessTarget\s*,\s*bool\s*\)",
        )
        self.assertRegex(
            tasks_rs,
            r"fn\s+terminate_process_tree\s*\(\s*target:\s*&ProcessTarget\s*,\s*force:\s*bool\s*\)",
        )
        self.assertRegex(tasks_rs, r"\(self\.terminator\)\s*\(\s*&target\s*,\s*false\s*\)")
        self.assertRegex(tasks_rs, r"\(self\.terminator\)\s*\(\s*&target\s*,\s*true\s*\)")
        self.assertIn("process_target_for_child(&child)", tasks_rs)
        self.assertIn("JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE", tasks_rs)
        self.assertIn("configure_process_group(&mut command)", tasks_rs)
        self.assertIn("stop_file", tasks_rs)
        self.assertIn("TaskExitCode::Number(130)", tasks_rs)
        self.assertIn("active.token == token", tasks_rs)
        self.assertRegex(
            tasks_rs,
            r"#\s*\[\s*test\s*\]\s*fn\s+force_stop_failure_rolls_back_stopping\s*\(",
        )
        self.assertRegex(
            tasks_rs,
            r"#\s*\[\s*test\s*\]\s*fn\s+windows_job_close_terminates_spawned_descendant\s*\(",
        )

    def test_tauri_runtime_prevents_duplicate_instances_and_handles_stdin_failures(self) -> None:
        lib_rs = read_text("wandao_electron/src-tauri/src/lib.rs")
        tasks_rs = read_text("wandao_electron/src-tauri/src/tasks.rs")

        self.assertIn("tauri_plugin_single_instance::init", lib_rs)
        self.assertIn('app.get_webview_window("main")', lib_rs)
        self.assertIn("pub fn write_input", tasks_rs)
        self.assertIn("write_all(input.as_bytes())", tasks_rs)

    def test_process_and_task_logs_are_bounded(self) -> None:
        tasks_rs = read_text("wandao_electron/src-tauri/src/tasks.rs")
        app_js = read_text("wandao_electron/renderer/app.js")

        self.assertIn("DEFAULT_OUTPUT_LIMIT_BYTES", tasks_rs)
        self.assertIn("struct CappedOutput", tasks_rs)
        self.assertIn("MAX_STRUCTURED_LOG_LINE_BYTES", tasks_rs)
        self.assertIn("const MAX_TASK_LOG_ENTRIES", app_js)
        self.assertIn("activeTaskLogEntries.push(entry)", app_js)
        self.assertIn("task.logs = [...activeTaskLogEntries]", app_js)
        self.assertNotIn("task.logs = detailLogEntries.slice", app_js)

    def test_runtime_provider_validation_fails_closed(self) -> None:
        providers_rs = read_text("wandao_electron/src-tauri/src/providers.rs")

        self.assertIn("fn validate_provider_manifest", providers_rs)
        self.assertIn("Provider 目录名必须和 ID 一致", providers_rs)
        self.assertIn("actions[{index}].script 不能为空", providers_rs)
        self.assertIn("safe_relative_path", providers_rs)
        self.assertNotIn("unwrap_or(provider.script", providers_rs)

    def test_python_runtime_build_is_pinned_and_verified(self) -> None:
        runtime_script = read_text("wandao_electron/scripts/prepare_python_runtime.py")

        self.assertIn('PYTHON_STANDALONE_RELEASE = "20260623"', runtime_script)
        self.assertNotIn("releases/latest", runtime_script)
        self.assertIn('"sha256":', runtime_script)
        self.assertIn("def verify_archive", runtime_script)
        self.assertIn("verify_archive(temporary, expected_sha256)", runtime_script)

    def test_bridge_does_not_expose_raw_tauri_or_node_modules(self) -> None:
        bridge_js = read_text("wandao_electron/renderer/tauri_bridge.js")

        self.assertIn("root.electronAPI = api", bridge_js)
        self.assertIn("Object.freeze({", bridge_js)
        self.assertNotIn("root.__TAURI__ =", bridge_js)
        self.assertNotIn("require(", bridge_js)
        self.assertNotIn("process.", bridge_js)

    def test_remote_text_fetch_is_limited_to_project_docs(self) -> None:
        commands_rs = read_text("wandao_electron/src-tauri/src/commands.rs")

        self.assertIn("fn is_allowed_remote_text_url", commands_rs)
        self.assertIn('url.scheme() != "https"', commands_rs)
        self.assertIn('"raw.githubusercontent.com"', commands_rs)
        self.assertIn('"/tllovesxs/wandao/"', commands_rs)
        self.assertIn("MAX_REMOTE_TEXT_BYTES", commands_rs)

    def test_file_and_external_commands_have_rust_boundaries(self) -> None:
        commands_rs = read_text("wandao_electron/src-tauri/src/commands.rs")

        self.assertIn("fn resolve_managed_file_path", commands_rs)
        self.assertIn("if !roots.iter().any(|root| is_inside(root, &path))", commands_rs)
        self.assertIn("resolve_managed_file_path(&file_path, &state, true)", commands_rs)
        self.assertIn("resolve_managed_file_path(&file_path, &state, false)", commands_rs)
        self.assertIn("fn is_allowed_external_url", commands_rs)
        self.assertIn('url.scheme() == "https"', commands_rs)
        self.assertIn("if !is_allowed_external_url(&url)", commands_rs)

    def test_save_dialog_splits_default_file_path(self) -> None:
        commands_rs = read_text("wandao_electron/src-tauri/src/commands.rs")
        save_start = commands_rs.index("pub async fn save_file")
        save_end = commands_rs.index("\n#[tauri::command]", save_start)
        save_command = commands_rs[save_start:save_end]

        self.assertRegex(
            save_command,
            r"save_default_path_parts\s*\(\s*PathBuf::from\s*\(\s*default_path\s*\)\s*\)",
        )
        self.assertIn("dialog.set_directory(directory)", save_command)
        self.assertIn("dialog.set_file_name(file_name)", save_command)
        self.assertRegex(
            commands_rs,
            r"#\s*\[\s*test\s*\]\s*fn\s+save_default_path_separates_parent_directory_and_file_name\s*\(",
        )

    def test_settings_have_schema_version_and_normalization(self) -> None:
        commands_rs = read_text("wandao_electron/src-tauri/src/commands.rs")

        self.assertIn("const SETTINGS_SCHEMA_VERSION: u64 = 1", commands_rs)
        self.assertIn("fn public_app_settings", commands_rs)
        self.assertIn("fn save_settings_update", commands_rs)
        self.assertIn('settings["schemaVersion"] = json!(SETTINGS_SCHEMA_VERSION)', commands_rs)

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
        self.assertIn("loadAppPaths();", startup)
        self.assertIn("if (opensProvider && appPathsStatus !== 'ready')", app_js)
        self.assertIn("pendingProviderTool = targetTool", app_js)
        self.assertIn("本机数据目录初始化失败", app_js)
        self.assertNotIn(".catch(() => {\n    renderTaskHistory();", startup)

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
        cargo = read_text("wandao_electron/src-tauri/Cargo.toml")
        tauri_config = json.loads(read_text("wandao_electron/src-tauri/tauri.conf.json"))

        self.assertGreaterEqual(workflow.count('node-version: "22"'), 3)
        self.assertIn("windows-latest, ubuntu-latest, macos-latest", workflow)
        self.assertIn('python: ["3.10", "3.11"]', workflow)
        self.assertIn("PR Windows Tauri Package Smoke", workflow)
        self.assertIn("cargo test --all-targets --locked", workflow)
        self.assertIn("cargo clippy --all-targets --locked -- -D warnings", workflow)
        self.assertIn("scripts/package_smoke.py --resources", workflow)
        self.assertIn("src-tauri/target/release/bundle/nsis", workflow)
        package_smoke = read_text("scripts/package_smoke.py")
        self.assertIn("verify_packaged_backend_help", package_smoke)
        self.assertIn("verify_tauri_frontend", package_smoke)
        self.assertNotIn("app.asar", package_smoke)
        self.assertIn('"--provider", provider_id, "--", "--help"', package_smoke)
        self.assertEqual(package["engines"]["node"], ">=22.12.0")
        self.assertEqual(package["devDependencies"]["@tauri-apps/cli"], "2.11.4")
        self.assertNotIn("electron", package.get("dependencies", {}))
        self.assertNotIn("electron", package.get("devDependencies", {}))
        self.assertIn('tauri = { version = "2.', cargo)
        self.assertTrue(tauri_config["bundle"]["active"])
        self.assertEqual(tauri_config["bundle"]["windows"]["nsis"]["compression"], "lzma")
        self.assertIsNone(tauri_config["bundle"]["macOS"]["signingIdentity"])
        self.assertIn("actions/attest-build-provenance", workflow)
        self.assertIn("Generate release SBOM", workflow)
        release_files = workflow.split("files: |", 1)[1]
        self.assertIn("release-artifacts/*.exe", release_files)
        self.assertIn("release-artifacts/*.zip", release_files)
        self.assertIn("release-artifacts/SHA256SUMS", release_files)
        self.assertIn("release-artifacts/wandao.spdx.json", release_files)

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
        lib_rs = read_text("wandao_electron/src-tauri/src/lib.rs")
        tasks_rs = read_text("wandao_electron/src-tauri/src/tasks.rs")
        app_js = read_text("wandao_electron/renderer/app.js")
        index_html = read_text("wandao_electron/renderer/index.html")

        self.assertIn("WindowEvent::CloseRequested", lib_rs)
        self.assertIn("api.prevent_close()", lib_rs)
        self.assertIn("停止任务并退出", lib_rs)
        shutdown_start = lib_rs.index("fn stop_running_task")
        shutdown_end = lib_rs.index("fn show_shutdown_error", shutdown_start)
        shutdown = lib_rs[shutdown_start:shutdown_end]
        self.assertLess(shutdown.index("runtime.force_stop()"), shutdown.index("runtime.wait_until_idle"))
        self.assertIn("runtime.wait_until_idle(SHUTDOWN_WAIT_TIMEOUT)", shutdown)
        self.assertIn("const SHUTDOWN_WAIT_TIMEOUT: Duration = Duration::from_secs(5)", lib_rs)
        self.assertIn("show_shutdown_error", lib_rs)
        self.assertRegex(
            tasks_rs,
            r"#\s*\[\s*test\s*\]\s*fn\s+wait_until_idle_is_bounded_and_wakes_on_completion\s*\(",
        )
        self.assertIn('id="btn-global-stop"', index_html)
        self.assertIn("btn-global-stop", app_js)

    def test_native_menu_is_registered_with_core_actions(self) -> None:
        lib_rs = read_text("wandao_electron/src-tauri/src/lib.rs")
        menu_rs = read_text("wandao_electron/src-tauri/src/app_menu.rs")

        self.assertRegex(lib_rs, r"\.menu\s*\(\s*app_menu::build\s*\)")
        self.assertRegex(lib_rs, r"\.on_menu_event\s*\(\s*app_menu::handle\s*\)")
        menu_ids = dict(
            re.findall(r'const\s+(MENU_[A-Z0-9_]+):\s*&str\s*=\s*"([^"]+)"', menu_rs)
        )
        self.assertEqual(menu_ids.get("MENU_STOP_TASK"), "task.stop")
        self.assertTrue(
            {
                "view.reload",
                "view.fullscreen",
                "help.docs",
                "help.check-updates",
                "help.about",
            }.issubset(menu_ids.values())
        )
        for title in ("文件", "编辑", "视图", "帮助"):
            self.assertRegex(menu_rs, rf'SubmenuBuilder::new\s*\(\s*app,\s*"{title}"\s*\)')
        self.assertRegex(menu_rs, r"runtime\.request_stop\s*\(")
        self.assertIn('app.emit("app-info", message)', menu_rs)
        self.assertRegex(
            menu_rs,
            r"#\s*\[\s*test\s*\]\s*fn\s+custom_menu_ids_are_unique\s*\(",
        )
        self.assertRegex(
            menu_rs,
            r"#\s*\[\s*test\s*\]\s*fn\s+project_links_remain_https\s*\(",
        )

    def test_plugin_center_always_shows_bundled_platform_plugins(self) -> None:
        commands_rs = read_text("wandao_electron/src-tauri/src/commands.rs")
        app_js = read_text("wandao_electron/renderer/app.js")

        self.assertIn("fn bundled_plugin_catalog", commands_rs)
        self.assertIn("fn plugin_catalog_with_bundled", commands_rs)
        self.assertIn("plugin_catalog_with_bundled(&state.paths, &manager", commands_rs)
        self.assertIn("随主程序提供", app_js)
        self.assertIn("安装更新", app_js)

    def test_plugin_release_channels_are_visible_for_experimental_plugins(self) -> None:
        commands_rs = read_text("wandao_electron/src-tauri/src/commands.rs")
        app_js = read_text("wandao_electron/renderer/app.js")
        workflow = read_text(".github/workflows/publish-plugins.yml")

        self.assertIn("WANDAO_EXPERIMENTAL_PLUGIN_REGISTRY_URL", commands_rs)
        self.assertIn('current_registry(&app, &state, &manager, refresh, "experimental")', commands_rs)
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
        self.assertIn("if (task.status === 'running' || task.status === 'stopping') return false", app_js)
        self.assertIn("WandaoTaskReport?.deriveTaskStatus", app_js)
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

    def test_feishu_import_keeps_credential_aware_page_for_installed_plugins(self) -> None:
        app_js = read_text("wandao_electron/renderer/app.js")
        switch_start = app_js.index("function switchTool")
        switch_end = app_js.index("// Initialize tool event handlers", switch_start)
        switch_tool = app_js[switch_start:switch_end]

        self.assertIn("if (currentTool === 'feishu-import') {", switch_tool)
        self.assertIn("loadFeishuImportTool();", switch_tool)
        self.assertNotIn("currentTool === 'feishu-import' && config.sourceKind !== 'plugin'", switch_tool)
        self.assertIn("|| currentTool === 'feishu-import';", switch_tool)
        command_start = app_js.index("async function runFeishuImportCommand")
        command_end = app_js.index("function requireFeishuWikiUrl", command_start)
        command = app_js[command_start:command_end]
        self.assertIn("runProviderCommand(provider.script, args, {", command)
        self.assertIn("providerId: 'feishu-import'", command)

    def test_feishu_import_exposes_filename_title_option_from_its_dedicated_page(self) -> None:
        app_js = read_text("wandao_electron/renderer/app.js")
        builder_start = app_js.index("function buildFeishuImportArgs")
        builder_end = app_js.index("function feishuActionAttentionMessage", builder_start)
        builder = app_js[builder_start:builder_end]

        self.assertIn('id="feishu-import-use-filename-as-title"', app_js)
        self.assertIn("--use-filename-as-title", builder)

    def test_plugin_center_supports_bulk_updates_and_download_progress(self) -> None:
        app_js = read_text("wandao_electron/renderer/app.js")
        commands_rs = read_text("wandao_electron/src-tauri/src/commands.rs")
        bridge_js = read_text("wandao_electron/renderer/tauri_bridge.js")

        self.assertIn("data-plugin-update-all", app_js)
        self.assertIn("runPluginCenterUpdateAll", app_js)
        self.assertIn("plugin-download-progress", app_js)
        self.assertIn("onPluginDownloadProgress", app_js)
        self.assertIn('"plugin-download-progress"', commands_rs)
        self.assertIn("onPluginDownloadProgress", bridge_js)

    def test_plugin_runtime_migrates_known_legacy_state_without_exposing_the_data_root(self) -> None:
        commands_rs = read_text("wandao_electron/src-tauri/src/commands.rs")
        providers_rs = read_text("wandao_electron/src-tauri/src/providers.rs")
        migration_js = read_text("wandao_electron/plugin_state_migration.js")
        package = json.loads(read_text("wandao_electron/package.json"))

        self.assertIn("migrate_legacy_plugin_state", commands_rs)
        self.assertIn("pub fn migrate_legacy_plugin_state", providers_rs)
        self.assertIn(".youdao_auth.json", migration_js)
        self.assertIn(".yuque_auth.json", migration_js)
        self.assertIn(".wiz_auth.json", migration_js)
        self.assertIn("yinxiang/yinxiang_china.db", migration_js)
        self.assertIn(".feishu_import_config.json", migration_js)
        self.assertNotIn("WANDAO_LEGACY_DATA_DIR", commands_rs)
        self.assertIn("state.paths.user_data.clone()", commands_rs)
        self.assertIn("state.paths.project_root.clone()", commands_rs)
        self.assertIn("node --check plugin_state_migration.js", package["scripts"]["check"])

    def test_feishu_and_ima_use_one_plugin_owned_config_path(self) -> None:
        app_js = read_text("wandao_electron/renderer/app.js")
        index_html = read_text("wandao_electron/renderer/index.html")

        self.assertIn("plugin-data/feishu/feishu_import_config.json", app_js)
        self.assertIn("plugin-data/ima/ima_config.json", app_js)
        self.assertIn("readJsonConfigWithMigration(configPath, legacyPaths", app_js)
        self.assertIn("feishuImportConfigFallbackPaths()", app_js)
        self.assertIn("JSON 文件格式损坏", app_js)
        self.assertIn('id="feishu-import-space-id" data-draft="false"', app_js)
        self.assertIn('id="ima-import-kb-select" data-draft="false"', index_html)
        self.assertIn('id="ima-export-kb-id" data-draft="false"', index_html)

    def test_feishu_setup_actions_distinguish_success_from_required_followup(self) -> None:
        app_js = read_text("wandao_electron/renderer/app.js")
        provider = json.loads(read_text("plugins/feishu/providers/feishu-import/provider.json"))
        setup_target = next(action for action in provider["actions"] if action["id"] == "setupTarget")

        self.assertIn("--yes", setup_target["args"])
        self.assertTrue(setup_target.get("confirm"))
        self.assertNotEqual(setup_target["kind"], "check")
        self.assertIn("function feishuActionAttentionMessage", app_js)
        self.assertIn("data.loginRequired === true", app_js)
        self.assertIn("data.hasBot === false", app_js)
        self.assertIn("data.missingScopes.length", app_js)
        self.assertIn("finishProgress('attention', attentionMessage)", app_js)

    def test_manifest_action_history_defaults_follow_action_kind(self) -> None:
        app_js = read_text("wandao_electron/renderer/app.js")
        onenote = json.loads(read_text("plugins/onenote/providers/onenote/provider.json"))

        self.assertIn("function shouldTrackManifestAction", app_js)
        self.assertIn("['import', 'export', 'upload'].includes", app_js)
        self.assertNotIn("systems", onenote["requirements"])
        self.assertEqual(onenote["requirements"]["system"], ["Windows"])
        scan = next(action for action in onenote["actions"] if action["id"] == "scan")
        self.assertFalse(scan["track"])

    def test_yuque_import_restores_saved_non_sensitive_form_values(self) -> None:
        app_js = read_text("wandao_electron/renderer/app.js")
        backend = read_text("plugins/yuque/backend/import_yuque.py")

        self.assertIn("function loadYuqueImportConfigIntoForm", app_js)
        self.assertIn("plugin-data/yuque/.yuque_import_config.json", app_js)
        self.assertIn("await readJsonFileIfExists(pluginConfigPath)", app_js)
        self.assertNotIn("runPythonCommand(provider.script, ['--show-config']", app_js)
        self.assertIn("loadYuqueImportConfigIntoForm().catch", app_js)
        self.assertIn("def saved_form_config", backend)
        self.assertIn('parser.add_argument("--show-config"', backend)

    def test_scan_toc_passes_provider_id_to_python_process(self) -> None:
        app_js = read_text("wandao_electron/renderer/app.js")
        start = app_js.index("async function handleScanToc")
        end = app_js.index("// Handle export", start)
        handler = app_js[start:end]

        self.assertIn("runProviderCommand(config.script, args, {", handler)
        self.assertIn("providerId: toolId", handler)
        self.assertIn("track: false", handler)

    def test_all_provider_commands_share_one_owned_running_lifecycle(self) -> None:
        app_js = read_text("wandao_electron/renderer/app.js")

        self.assertEqual(app_js.count("window.electronAPI.runPythonCommand("), 1)
        self.assertEqual(app_js.count("setRunning("), 2)
        self.assertIn("let activeCommandOwner = null", app_js)
        self.assertIn("if (isRunning || activeCommandOwner)", app_js)
        self.assertIn("if (activeCommandOwner === owner)", app_js)
        self.assertIn("#content-area button, #content-area input", app_js)
        self.assertIn("navigationLocked ? 'disabled aria-disabled", app_js)

    def test_renderer_recovers_a_python_task_that_survives_reload(self) -> None:
        commands_rs = read_text("wandao_electron/src-tauri/src/commands.rs")
        bridge_js = read_text("wandao_electron/renderer/tauri_bridge.js")
        app_js = read_text("wandao_electron/renderer/app.js")

        self.assertIn("pub async fn get_python_process_state", commands_rs)
        self.assertIn('"python-process-state"', commands_rs)
        self.assertIn("'python-process-state'", bridge_js)
        self.assertIn("getPythonProcessState", bridge_js)
        self.assertIn("function initializePythonProcessStateSync", app_js)
        self.assertIn("recoveredCommandOwner = Symbol", app_js)
        self.assertIn("已恢复导航锁和全局停止按钮", app_js)
        self.assertIn("mainPythonProcessState.taskId === task.id", app_js)

    def test_tauri_command_rejects_parallel_python_tasks(self) -> None:
        commands_rs = read_text("wandao_electron/src-tauri/src/commands.rs")
        marker = "pub async fn run_python_command"
        start = commands_rs.find(marker)
        self.assertGreater(start, -1)
        handler = commands_rs[start : start + 800]

        self.assertIn("if runtime.state().running", handler)
        self.assertIn("已有任务正在运行", handler)

    def test_tauri_command_compresses_large_doc_id_selection_for_exporters(self) -> None:
        commands_rs = read_text("wandao_electron/src-tauri/src/commands.rs")

        self.assertIn("fn compress_doc_id_args", commands_rs)
        self.assertNotIn("const supported", commands_rs)
        self.assertIn('args[index] == "--doc-id"', commands_rs)
        self.assertIn('"--doc-id-file"', commands_rs)

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
        package = json.loads(read_text("wandao_electron/package.json"))
        package_lock = json.loads(read_text("wandao_electron/package-lock.json"))
        cargo = read_text("wandao_electron/src-tauri/Cargo.toml")
        tauri_config = json.loads(read_text("wandao_electron/src-tauri/tauri.conf.json"))
        resources = tauri_config["bundle"]["resources"]
        pyproject = read_text("pyproject.toml")

        self.assertRegex(package["version"], r"^\d+\.\d+\.\d+$")
        self.assertEqual(package["version"], package_lock["version"])
        self.assertEqual(package["version"], package_lock["packages"][""]["version"])
        self.assertIn(f'version = "{package["version"]}"', cargo)
        self.assertEqual(tauri_config["version"], package["version"])
        self.assertEqual(resources["../../*.py"], "python/")
        self.assertEqual(resources["../../wandao_core/"], "python/wandao_core/")
        self.assertEqual(resources["../../requirements.txt"], "python/requirements.txt")
        self.assertIn(f'version = "{package["version"]}"', pyproject)
        self.assertIn('"wandao_checkpoint"', pyproject)
        self.assertIn('"wandao_cli"', pyproject)

    def test_provider_python_scripts_are_bundled_for_packaged_app(self) -> None:
        tauri_config = json.loads(read_text("wandao_electron/src-tauri/tauri.conf.json"))
        resources = tauri_config["bundle"]["resources"]
        required_common = {
            "wandao_logging.py",
            "wandao_report.py",
            "wandao_checkpoint.py",
            "wandao_cli.py",
            "wandao_credentials.py",
            "wandao_browser.py",
            "gui_utils.py",
        }
        self.assertEqual(resources["../../plugins/"], "plugins/")
        self.assertEqual(resources["../../providers/"], "providers/")
        self.assertEqual(resources["../runtime/python-runtime/"], "python-runtime/")
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
        providers_rs = read_text("wandao_electron/src-tauri/src/providers.rs")
        providers_js = read_text("wandao_electron/renderer/providers.js")
        self.assertNotIn("ALLOWED_SCRIPTS", providers_rs)
        self.assertIn('"bundled-plugin:"', providers_rs)
        self.assertIn("平台脚本必须来自 Plugin v1 或文件型 Provider", providers_rs)
        self.assertNotRegex(providers_js, r"(?:export|import)_[a-z0-9_]+\.py")

    def test_plugin_process_environment_uses_an_allowlist(self) -> None:
        tasks_rs = read_text("wandao_electron/src-tauri/src/tasks.rs")

        self.assertIn("const PLUGIN_ENV_ALLOWLIST: &[&str]", tasks_rs)
        self.assertIn("fn inherited_environment(plugin_isolated: bool)", tasks_rs)
        self.assertIn("allowlist.contains(key.to_ascii_uppercase().as_str())", tasks_rs)
        self.assertNotIn("WANDAO_PLUGIN_PRIVATE_KEY", tasks_rs)

    def test_bundled_plugin_hashes_cover_discovery_and_execution(self) -> None:
        build_rs = read_text("wandao_electron/src-tauri/build.rs")
        commands_rs = read_text("wandao_electron/src-tauri/src/commands.rs")
        plugins_rs = read_text("wandao_electron/src-tauri/src/plugins.rs")
        providers_rs = read_text("wandao_electron/src-tauri/src/providers.rs")

        ids_start = build_rs.index("const BUNDLED_PLUGIN_IDS")
        ids_end = build_rs.index("];", ids_start)
        declared_ids = set(re.findall(r'"([a-z0-9_]+)"', build_rs[ids_start:ids_end]))
        plugin_root = REPO_ROOT / "plugins"
        repository_ids = {
            path.name
            for path in plugin_root.iterdir()
            if path.is_dir() and (path / "plugin.json").is_file()
        }
        self.assertEqual(declared_ids, repository_ids)
        self.assertEqual(len(declared_ids), 14)
        self.assertIn("generate_bundled_plugin_hashes(&manifest_dir)", build_rs)
        self.assertIn('include!(concat!(env!("OUT_DIR"), "/bundled_plugin_hashes.rs"))', plugins_rs)
        self.assertIn("verify_bundled_plugin(&paths.bundled_plugins, &id)", commands_rs)

        bundled_start = providers_rs.index('script_name.strip_prefix("bundled-plugin:")')
        bundled_end = providers_rs.index("if let Some(rest)", bundled_start + 1)
        bundled_resolution = providers_rs[bundled_start:bundled_end]
        self.assertIn("verify_bundled_plugin_file", bundled_resolution)
        self.assertRegex(
            plugins_rs,
            r"#\s*\[\s*test\s*\]\s*fn\s+bundled_hash_catalog_covers_all_plugins_and_detects_tampering\s*\(",
        )
        self.assertIn('contains("构建后被修改")', plugins_rs)

    def test_plugins_are_signed_sandboxed_and_official_plugins_are_bundled(self) -> None:
        commands_rs = read_text("wandao_electron/src-tauri/src/commands.rs")
        plugins_rs = read_text("wandao_electron/src-tauri/src/plugins.rs")
        security_rs = read_text("wandao_electron/src-tauri/src/security.rs")
        bridge_js = read_text("wandao_electron/renderer/tauri_bridge.js")
        app_js = read_text("wandao_electron/renderer/app.js")
        providers_js = read_text("wandao_electron/renderer/providers.js")
        tauri_config = json.loads(read_text("wandao_electron/src-tauri/tauri.conf.json"))

        self.assertIn("PluginManager", commands_rs)
        self.assertIn("provider_entries_with_errors", plugins_rs)
        self.assertIn("pub async fn get_plugin_catalog", commands_rs)
        self.assertIn("pub async fn get_plugin_ui", commands_rs)
        self.assertIn("getPluginCatalog", bridge_js)
        self.assertIn("installPlugin", bridge_js)
        self.assertIn("verify_envelope_signature", plugins_rs)
        self.assertIn("ed25519_dalek", security_rs)
        self.assertIn("safe_relative_path", plugins_rs)
        self.assertIn("MAX_PLUGIN_FILES", plugins_rs)
        self.assertIn("MAX_PLUGIN_BYTES", plugins_rs)
        self.assertIn('sandbox="allow-scripts"', app_js)
        self.assertNotIn('sandbox="allow-scripts allow-same-origin"', app_js)
        self.assertIn("default-src 'none'", app_js)
        self.assertIn("replaceExternal", providers_js)
        self.assertNotIn("id: 'wiz'", providers_js)
        self.assertNotIn("id: 'feishu-export'", providers_js)
        self.assertEqual(tauri_config["bundle"]["resources"]["../../plugins/"], "plugins/")
        self.assertEqual(tauri_config["bundle"]["resources"]["../assets/"], "assets/")
        self.assertIn("bundled_plugin_catalog", commands_rs)
        self.assertIn("plugin_catalog_with_bundled", commands_rs)
        self.assertIn("manager.list_with_registry", commands_rs)
        self.assertIn("compare_versions", plugins_rs)


if __name__ == "__main__":
    unittest.main()
