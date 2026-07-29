use std::{
    collections::{BTreeMap, HashMap},
    env, fs,
    path::{Path, PathBuf},
    sync::Arc,
    time::Duration,
};

use base64::{engine::general_purpose::STANDARD as BASE64, Engine};
use chrono::Utc;
use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, State};
use tauri_plugin_clipboard_manager::ClipboardExt;
use tauri_plugin_dialog::{DialogExt, FilePath, MessageDialogKind};
use tauri_plugin_opener::OpenerExt;

use crate::{
    app_state::{is_inside, normalize_absolute, AppState, CachedRegistry},
    plugins::{compare_versions, verify_bundled_plugin, PluginManager},
    providers::{
        discover_provider_manifests, is_allowed_remote_guide_image_url,
        migrate_legacy_plugin_state, read_guide_image_data_url, remote_guide_asset_spec,
        resolve_script, GuideAssetSpec, ResolvedPluginContext, MAX_GUIDE_ASSET_BYTES,
    },
    security::{
        protect_bytes, read_json, sha256_hex, unprotect_bytes_for_user_data, write_private_atomic,
    },
    tasks::{
        DiagnosticLevel, PluginExecutionContext, TaskEventSink, TaskExecutionContext,
        TaskRunRequest, TaskRuntime, TaskRuntimeEvent,
    },
};

const SETTINGS_SCHEMA_VERSION: u64 = 1;
const BROWSER_DOWNLOAD_URL: &str = "https://www.google.com/chrome/";
const STABLE_REGISTRY_URL: &str =
    "https://github.com/tllovesxs/wandao/releases/download/plugins-latest/registry.json";
const EXPERIMENTAL_REGISTRY_URL: &str =
    "https://github.com/tllovesxs/wandao/releases/download/plugins-experimental/registry.json";
const LATEST_RELEASE_API: &str = "https://api.github.com/repos/tllovesxs/wandao/releases/latest";
const RELEASES_URL: &str = "https://github.com/tllovesxs/wandao/releases";
const MAX_REMOTE_TEXT_BYTES: usize = 1024 * 1024;
const MAX_REGISTRY_BYTES: usize = 4 * 1024 * 1024;
const MAX_PLUGIN_DOWNLOAD_BYTES: usize = 128 * 1024 * 1024;

#[tauri::command]
pub async fn select_directory(
    app: AppHandle,
    options: Option<Value>,
) -> Result<Option<String>, String> {
    let options = options.unwrap_or_else(|| json!({}));
    let mut dialog = app
        .dialog()
        .file()
        .set_title(value_string(&options, &["title"]).unwrap_or_else(|| "选择目录".into()));
    if let Some(default_path) = value_string(&options, &["default_path", "defaultPath"]) {
        if !default_path.is_empty() {
            dialog = dialog.set_directory(PathBuf::from(default_path));
        }
    }
    Ok(dialog.blocking_pick_folder().and_then(file_path_string))
}

#[tauri::command]
pub async fn select_file(app: AppHandle, options: Option<Value>) -> Result<Option<String>, String> {
    let options = options.unwrap_or_else(|| json!({}));
    let mut dialog = app
        .dialog()
        .file()
        .set_title(value_string(&options, &["title"]).unwrap_or_else(|| "选择文件".into()));
    if let Some(default_path) = value_string(&options, &["default_path", "defaultPath"]) {
        if !default_path.is_empty() {
            dialog = dialog.set_directory(PathBuf::from(default_path));
        }
    }
    if let Some(filters) = options.get("filters").and_then(Value::as_array) {
        for filter in filters {
            let name = filter.get("name").and_then(Value::as_str).unwrap_or("文件");
            let extensions: Vec<String> = filter
                .get("extensions")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
                .map(str::to_string)
                .collect();
            let refs: Vec<&str> = extensions.iter().map(String::as_str).collect();
            if !refs.is_empty() {
                dialog = dialog.add_filter(name, &refs);
            }
        }
    }
    Ok(dialog.blocking_pick_file().and_then(file_path_string))
}

#[tauri::command]
pub async fn select_browser_file(app: AppHandle) -> Result<Value, String> {
    let dialog = app.dialog().file().set_title("选择浏览器");
    #[cfg(target_os = "windows")]
    let dialog = dialog
        .add_filter("浏览器可执行文件", &["exe"])
        .add_filter("所有文件", &["*"]);
    let Some(file) = dialog.blocking_pick_file().and_then(file_path_string) else {
        return Ok(json!({"success": false, "canceled": true}));
    };
    let normalized = normalize_browser_executable(&file);
    if normalized.is_none() {
        return Ok(json!({
            "success": false,
            "error": "请选择 Chrome、Edge 或 Chromium 的可执行文件。"
        }));
    }
    Ok(json!({
        "success": true,
        "path": normalized.unwrap().to_string_lossy()
    }))
}

#[tauri::command]
pub async fn save_file(app: AppHandle, options: Option<Value>) -> Result<Option<String>, String> {
    let options = options.unwrap_or_else(|| json!({}));
    let mut dialog = app
        .dialog()
        .file()
        .set_title(value_string(&options, &["title"]).unwrap_or_else(|| "保存文件".into()));
    if let Some(default_path) = value_string(&options, &["default_path", "defaultPath"]) {
        if !default_path.is_empty() {
            let (directory, file_name) = save_default_path_parts(PathBuf::from(default_path));
            if let Some(directory) = directory {
                dialog = dialog.set_directory(directory);
            }
            if let Some(file_name) = file_name {
                dialog = dialog.set_file_name(file_name);
            }
        }
    }
    if let Some(filters) = options.get("filters").and_then(Value::as_array) {
        for filter in filters {
            let name = filter.get("name").and_then(Value::as_str).unwrap_or("文件");
            let extensions: Vec<String> = filter
                .get("extensions")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
                .map(str::to_string)
                .collect();
            let refs: Vec<&str> = extensions.iter().map(String::as_str).collect();
            if !refs.is_empty() {
                dialog = dialog.add_filter(name, &refs);
            }
        }
    }
    Ok(dialog.blocking_save_file().and_then(file_path_string))
}

#[tauri::command]
pub async fn run_python_command(
    app: AppHandle,
    state: State<'_, AppState>,
    manager: State<'_, PluginManager>,
    runtime: State<'_, TaskRuntime>,
    command: String,
    args: Option<Vec<Value>>,
    options: Option<Value>,
) -> Result<Value, String> {
    if runtime.state().running {
        return Ok(json!({
            "success": false,
            "error": "已有任务正在运行，请先停止当前任务或等待完成。"
        }));
    }
    let options = options.unwrap_or_else(|| json!({}));
    let resolved = match resolve_script(&command, &state.paths, &manager) {
        Ok(value) => value,
        Err(error) => return Ok(json!({"success": false, "error": error})),
    };
    let raw_args: Vec<String> = args
        .unwrap_or_default()
        .into_iter()
        .map(|value| match value {
            Value::String(value) => value,
            other => value_to_string(&other),
        })
        .collect();
    let compressed = match compress_doc_id_args(&command, raw_args, &state.paths.user_data) {
        Ok(value) => value,
        Err(error) => return Ok(json!({"success": false, "error": error})),
    };
    let (command_args, secrets) = extract_sensitive_arguments(compressed);
    let browser_path = selected_browser_path(&state.paths);
    let (python_executable, python_runtime) = python_command(&state.paths);
    let task_id = value_string(&options, &["task_id", "taskId"]).unwrap_or_default();
    let run_id = value_string(&options, &["run_id", "runId"]).unwrap_or_default();
    let stop_id = if task_id.is_empty() {
        Utc::now().timestamp_millis().to_string()
    } else {
        sanitize_file_id(&task_id)
    };
    let stop_file = state
        .paths
        .user_data
        .join("runtime")
        .join("stops")
        .join(format!("{stop_id}.stop"));
    let stdin_text = initial_stdin_text(&options);
    let close_stdin_after_initial_input = stdin_text.is_some();

    if let Some(plugin) = &resolved.plugin {
        let _ = fs::create_dir_all(&plugin.plugin_data_dir);
        migrate_legacy_plugin_state(
            &plugin.plugin_id,
            &[
                state.paths.user_data.clone(),
                state.paths.project_root.clone(),
            ],
            &plugin.plugin_data_dir,
        );
    }
    let plugin_context = resolved.plugin.as_ref().map(task_plugin_context);
    let request = TaskRunRequest {
        executable: python_executable,
        script: resolved.path.clone(),
        args: command_args.clone(),
        working_directory: resolved.path.parent().map(Path::to_path_buf),
        context: TaskExecutionContext {
            user_data_dir: state.paths.user_data.clone(),
            provider_id: value_string(&options, &["provider_id", "providerId"]).unwrap_or_default(),
            task_id,
            run_id,
            job_id: value_string(&options, &["job_id", "jobId"]).unwrap_or_default(),
            parent_run_id: value_string(&options, &["parent_run_id", "parentRunId"])
                .unwrap_or_default(),
            started_at: Utc::now().to_rfc3339(),
            browser_path,
            python_runtime,
            python_library_dir: Some(state.paths.project_root.clone()),
            additional_python_paths: Vec::new(),
            plugin: plugin_context,
            secret_environment: secrets,
            extra_environment: BTreeMap::new(),
        },
        stop_file: Some(stop_file),
        stdin_text,
        close_stdin_after_initial_input,
    };
    let sink = task_event_sink(app.clone());
    let task_runtime = runtime.inner().clone();
    let cleanup_args = command_args;
    let user_data = state.paths.user_data.clone();
    let result = tauri::async_runtime::spawn_blocking(move || task_runtime.run(request, sink))
        .await
        .map_err(|error| format!("任务线程失败：{error}"))?;
    cleanup_temporary_doc_id_file(&cleanup_args, &user_data);
    serde_json::to_value(result).map_err(|error| error.to_string())
}

#[tauri::command]
pub async fn stop_python_process(
    app: AppHandle,
    runtime: State<'_, TaskRuntime>,
) -> Result<Value, String> {
    serde_json::to_value(runtime.request_stop(task_event_sink(app)))
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub async fn get_python_process_state(runtime: State<'_, TaskRuntime>) -> Result<Value, String> {
    serde_json::to_value(runtime.state()).map_err(|error| error.to_string())
}

#[tauri::command]
pub async fn send_python_input(
    runtime: State<'_, TaskRuntime>,
    text: Option<String>,
) -> Result<Value, String> {
    serde_json::to_value(runtime.write_input(text.as_deref().unwrap_or("\n"), false))
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub async fn protect_task_args(args: Option<Vec<Value>>) -> Result<Value, String> {
    let args: Vec<String> = args
        .unwrap_or_default()
        .into_iter()
        .map(|value| match value {
            Value::String(value) => value,
            other => value_to_string(&other),
        })
        .collect();
    let plain = serde_json::to_vec(&args).map_err(|error| error.to_string())?;
    match protect_bytes(&plain) {
        Ok(encrypted) => Ok(json!({"success": true, "payload": BASE64.encode(encrypted)})),
        Err(error) => Ok(json!({
            "success": false,
            "error": format!("当前系统无法使用安全存储，任务参数不会写入历史记录：{error}")
        })),
    }
}

#[tauri::command]
pub async fn restore_task_args(
    state: State<'_, AppState>,
    payload: Option<String>,
) -> Result<Value, String> {
    let payload = payload.unwrap_or_default();
    if payload.trim().is_empty() {
        return Ok(json!({"success": true, "args": []}));
    }
    let result = (|| {
        let encrypted = BASE64
            .decode(payload.trim())
            .map_err(|_| "任务参数不是有效 Base64".to_string())?;
        let plain = unprotect_bytes_for_user_data(&encrypted, &state.paths.user_data)?;
        let args: Vec<Value> = serde_json::from_slice(&plain).map_err(|error| error.to_string())?;
        Ok::<Value, String>(json!({"success": true, "args": args}))
    })();
    Ok(result.unwrap_or_else(|error| {
        json!({
            "success": false,
            "error": format!("任务参数解密失败：{error}"),
            "args": []
        })
    }))
}

#[tauri::command]
pub async fn read_file(state: State<'_, AppState>, file_path: String) -> Result<Value, String> {
    let result = resolve_managed_file_path(&file_path, &state, true)
        .and_then(|path| fs::read_to_string(path).map_err(|error| error.to_string()));
    Ok(match result {
        Ok(content) => json!({"success": true, "content": content}),
        Err(error) => json!({"success": false, "error": error}),
    })
}

#[tauri::command]
pub async fn write_file(
    state: State<'_, AppState>,
    file_path: String,
    content: Option<String>,
) -> Result<Value, String> {
    let result = resolve_managed_file_path(&file_path, &state, false)
        .and_then(|path| write_private_atomic(&path, content.unwrap_or_default().as_bytes()));
    Ok(match result {
        Ok(()) => json!({"success": true}),
        Err(error) => json!({"success": false, "error": error}),
    })
}

#[tauri::command]
pub async fn file_exists(state: State<'_, AppState>, file_path: String) -> Result<bool, String> {
    Ok(resolve_managed_file_path(&file_path, &state, true).is_ok_and(|path| path.exists()))
}

#[tauri::command]
pub async fn open_path(app: AppHandle, target_path: String) -> Result<Value, String> {
    Ok(match app.opener().open_path(target_path, None::<&str>) {
        Ok(()) => json!({"success": true}),
        Err(error) => json!({"success": false, "error": error.to_string()}),
    })
}

#[tauri::command]
pub async fn open_external(app: AppHandle, url: String) -> Result<Value, String> {
    if !is_allowed_external_url(&url) {
        return Ok(json!({"success": false, "error": "只允许打开 HTTPS 链接。"}));
    }
    Ok(match app.opener().open_url(url, None::<&str>) {
        Ok(()) => json!({"success": true}),
        Err(error) => json!({"success": false, "error": error.to_string()}),
    })
}

#[tauri::command]
pub async fn fetch_remote_text(url: String) -> Result<Value, String> {
    if !is_allowed_remote_text_url(&url) {
        return Ok(json!({
            "success": false,
            "error": "只允许读取万能导 GitHub 仓库中的公告和教程文档"
        }));
    }
    let result = fetch_limited(
        &url,
        MAX_REMOTE_TEXT_BYTES,
        "wandao-docs-center",
        RedirectUrlPolicy::RemoteText,
    )
    .await;
    Ok(match result {
        Ok(bytes) => json!({
            "success": true,
            "content": String::from_utf8_lossy(&bytes)
        }),
        Err(error) => json!({"success": false, "error": error}),
    })
}

#[tauri::command]
pub async fn copy_text(app: AppHandle, text: Option<String>) -> Result<Value, String> {
    Ok(match app.clipboard().write_text(text.unwrap_or_default()) {
        Ok(()) => json!({"success": true}),
        Err(error) => json!({"success": false, "error": error.to_string()}),
    })
}

#[tauri::command]
pub async fn show_about(app: AppHandle) -> Result<Value, String> {
    let version = app.package_info().version.to_string();
    let detail = format!(
        "让知识没有壁垒，多平台文档互转\n\n版本：{version}\n作者：tllovesxs\nGitHub：https://github.com/tllovesxs/wandao\n微信：pressure_spring\n\n请只处理自己有权限访问的内容，并遵守目标平台服务条款。"
    );
    app.dialog()
        .message(detail)
        .title("关于 万能导 Wandao")
        .kind(MessageDialogKind::Info)
        .blocking_show();
    Ok(json!({"success": true}))
}

#[tauri::command]
pub async fn check_for_updates(app: AppHandle) -> Result<Value, String> {
    let result = async {
        let bytes = fetch_limited(
            LATEST_RELEASE_API,
            MAX_REMOTE_TEXT_BYTES,
            "wandao-update-checker",
            RedirectUrlPolicy::SecureTransport {
                allow_local_http: false,
            },
        )
        .await?;
        let release: Value =
            serde_json::from_slice(&bytes).map_err(|error| format!("解析更新信息失败：{error}"))?;
        let latest_version = release
            .get("tag_name")
            .and_then(Value::as_str)
            .unwrap_or("0.0.0")
            .trim_start_matches(['v', 'V']);
        let current_version = app.package_info().version.to_string();
        Ok::<Value, String>(json!({
            "currentVersion": current_version,
            "latestVersion": latest_version,
            "latestTag": release.get("tag_name").cloned().unwrap_or(json!(format!("v{latest_version}"))),
            "releaseUrl": release.get("html_url").cloned().unwrap_or(json!(RELEASES_URL)),
            "releaseName": release.get("name").or_else(|| release.get("tag_name")).cloned().unwrap_or(json!(latest_version)),
            "publishedAt": release.get("published_at").cloned().unwrap_or(json!("")),
            "hasUpdate": compare_versions(latest_version, &current_version) > 0
        }))
    }
    .await;
    Ok(match result {
        Ok(data) => json!({"success": true, "data": data}),
        Err(error) => json!({"success": false, "error": error}),
    })
}

#[tauri::command]
pub async fn get_app_settings(state: State<'_, AppState>) -> Result<Value, String> {
    Ok(json!({
        "success": true,
        "settings": public_app_settings(&read_app_settings(&state.paths))
    }))
}

#[tauri::command]
pub async fn save_app_settings(
    state: State<'_, AppState>,
    settings: Option<Value>,
) -> Result<Value, String> {
    let result = save_settings_update(&state.paths, settings.unwrap_or_else(|| json!({})));
    Ok(match result {
        Ok(settings) => json!({
            "success": true,
            "settings": public_app_settings(&settings),
            "browsers": detect_browsers_internal(),
            "downloadUrl": BROWSER_DOWNLOAD_URL
        }),
        Err(error) => json!({"success": false, "error": error}),
    })
}

#[tauri::command]
pub async fn detect_browsers(state: State<'_, AppState>) -> Result<Value, String> {
    Ok(json!({
        "success": true,
        "browsers": detect_browsers_internal(),
        "selectedBrowserPath": selected_browser_path(&state.paths)
            .map(|path| path.to_string_lossy().to_string()).unwrap_or_default(),
        "downloadUrl": BROWSER_DOWNLOAD_URL
    }))
}

#[tauri::command]
pub async fn get_provider_manifests(
    state: State<'_, AppState>,
    manager: State<'_, PluginManager>,
) -> Result<Value, String> {
    let discovery = discover_provider_manifests(&state.paths, &manager);
    if let Ok(mut roots) = state.guide_roots.lock() {
        *roots = discovery.guide_roots;
    }
    Ok(json!({
        "success": true,
        "providers": discovery.providers,
        "errors": discovery.errors
    }))
}

#[tauri::command]
pub async fn read_provider_guide_image(
    state: State<'_, AppState>,
    manager: State<'_, PluginManager>,
    provider_id: String,
    relative_path: String,
) -> Result<Value, String> {
    let root = (|| {
        if provider_id.trim().is_empty() {
            return Err("Provider ID 不能为空".to_string());
        }
        let mut root = state
            .guide_roots
            .lock()
            .ok()
            .and_then(|roots| roots.get(&provider_id).cloned());
        if root.is_none() {
            let discovery = discover_provider_manifests(&state.paths, &manager);
            root = discovery.guide_roots.get(&provider_id).cloned();
            if let Ok(mut roots) = state.guide_roots.lock() {
                *roots = discovery.guide_roots;
            }
        }
        root.ok_or_else(|| "没有找到这个 Provider 的教程目录".to_string())
    })();
    let remote_url = url::Url::parse(relative_path.trim())
        .ok()
        .filter(|url| url.has_host());
    let fallback_url = remote_url
        .as_ref()
        .filter(|url| is_allowed_remote_guide_image_url(&provider_id, url))
        .map(url::Url::to_string);
    let result = match root {
        Ok(root) => {
            if let Some(remote_url) = remote_url {
                match remote_guide_asset_spec(&root, relative_path.trim()) {
                    Ok(Some(spec)) => {
                        fetch_remote_guide_image(&provider_id, remote_url, &spec).await
                    }
                    Ok(None) => Err("教程远程图片缺少完整性声明".to_string()),
                    Err(error) => Err(error),
                }
            } else {
                read_guide_image_data_url(&root, &relative_path)
            }
        }
        Err(error) => Err(error),
    };
    Ok(match result {
        Ok(data_url) => json!({"success": true, "dataUrl": data_url}),
        Err(error) => json!({
            "success": false,
            "error": error,
            "fallbackUrl": fallback_url
        }),
    })
}

#[tauri::command]
pub async fn get_plugin_catalog(
    app: AppHandle,
    state: State<'_, AppState>,
    manager: State<'_, PluginManager>,
    options: Option<Value>,
) -> Result<Value, String> {
    let refresh = options
        .as_ref()
        .and_then(|value| value.get("refresh"))
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let stable = current_registry(&app, &state, &manager, refresh, "stable").await;
    let result = match stable {
        Ok(stable) => {
            let experimental =
                current_registry(&app, &state, &manager, refresh, "experimental").await;
            let experimental_error = experimental.as_ref().err().cloned().unwrap_or_default();
            let mut plugins = stable
                .get("plugins")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default();
            if let Ok(experimental) = experimental {
                plugins.extend(
                    experimental
                        .get("plugins")
                        .and_then(Value::as_array)
                        .cloned()
                        .unwrap_or_default(),
                );
            }
            let combined = json!({
                "plugins": plugins,
                "generatedAt": stable.get("generatedAt").cloned().unwrap_or(json!(""))
            });
            match plugin_catalog_with_bundled(&state.paths, &manager, Some(&combined)) {
                Ok(plugins) => json!({
                    "success": true,
                    "plugins": plugins,
                    "registryUpdatedAt": combined.get("generatedAt").cloned().unwrap_or(json!("")),
                    "experimentalError": experimental_error
                }),
                Err(error) => json!({"success": false, "error": error}),
            }
        }
        Err(registry_error) => match plugin_catalog_with_bundled(&state.paths, &manager, None) {
            Ok(plugins) => json!({
                "success": true,
                "plugins": plugins,
                "registryError": registry_error,
                "offline": true
            }),
            Err(error) => json!({"success": false, "error": error}),
        },
    };
    Ok(result)
}

#[tauri::command]
pub async fn install_plugin(
    app: AppHandle,
    state: State<'_, AppState>,
    manager: State<'_, PluginManager>,
    plugin_id: String,
    channel: Option<String>,
) -> Result<Value, String> {
    let result = async {
        let channel = if channel.as_deref() == Some("experimental") {
            "experimental"
        } else {
            "stable"
        };
        let registry = current_registry(&app, &state, &manager, true, channel).await?;
        let entry = registry
            .get("plugins")
            .and_then(Value::as_array)
            .and_then(|plugins| {
                plugins
                    .iter()
                    .find(|plugin| plugin.get("id").and_then(Value::as_str) == Some(&plugin_id))
            })
            .cloned()
            .ok_or_else(|| format!("插件注册表中没有 {plugin_id}"))?;
        let package_url = entry
            .get("packageUrl")
            .and_then(Value::as_str)
            .ok_or_else(|| "插件下载地址缺失".to_string())?;
        let bytes = download_plugin_with_progress(&app, &plugin_id, package_url).await?;
        let expected = entry
            .get("sha256")
            .and_then(Value::as_str)
            .unwrap_or_default();
        if !sha256_hex(&bytes).eq_ignore_ascii_case(expected) {
            return Err("插件下载文件的 SHA-256 与注册表不一致".to_string());
        }
        manager.install_bytes(&bytes, json!({"registryEntry": entry}))
    }
    .await;
    Ok(match result {
        Ok(plugin) => json!({"success": true, "plugin": plugin}),
        Err(error) => json!({"success": false, "error": error}),
    })
}

#[tauri::command]
pub async fn install_plugin_file(
    app: AppHandle,
    manager: State<'_, PluginManager>,
) -> Result<Value, String> {
    let file = app
        .dialog()
        .file()
        .set_title("安装万能导插件")
        .add_filter("Wandao Plugin", &["wandao-plugin"])
        .blocking_pick_file()
        .and_then(file_path_to_path);
    let Some(file) = file else {
        return Ok(json!({"success": false, "canceled": true}));
    };
    Ok(match manager.install_file(&file) {
        Ok(plugin) => json!({"success": true, "plugin": plugin}),
        Err(error) => json!({"success": false, "error": error}),
    })
}

#[tauri::command]
pub async fn set_plugin_enabled(
    manager: State<'_, PluginManager>,
    plugin_id: String,
    enabled: Option<bool>,
) -> Result<Value, String> {
    Ok(
        match manager.set_enabled(&plugin_id, enabled.unwrap_or(false)) {
            Ok(plugin) => json!({"success": true, "plugin": plugin}),
            Err(error) => json!({"success": false, "error": error}),
        },
    )
}

#[tauri::command]
pub async fn rollback_plugin(
    manager: State<'_, PluginManager>,
    plugin_id: String,
) -> Result<Value, String> {
    Ok(match manager.rollback(&plugin_id) {
        Ok(plugin) => json!({"success": true, "plugin": plugin}),
        Err(error) => json!({"success": false, "error": error}),
    })
}

#[tauri::command]
pub async fn uninstall_plugin(
    manager: State<'_, PluginManager>,
    plugin_id: String,
) -> Result<Value, String> {
    Ok(match manager.uninstall(&plugin_id) {
        Ok(removed) => json!({"success": true, "removed": removed}),
        Err(error) => json!({"success": false, "error": error}),
    })
}

#[tauri::command]
pub async fn get_plugin_ui(
    manager: State<'_, PluginManager>,
    plugin_id: String,
    entry: String,
) -> Result<Value, String> {
    Ok(match manager.read_ui(&plugin_id, &entry) {
        Ok(html) => json!({"success": true, "html": html}),
        Err(error) => json!({"success": false, "error": error}),
    })
}

#[tauri::command]
pub async fn get_app_path(app: AppHandle, state: State<'_, AppState>) -> Result<Value, String> {
    Ok(json!({
        "appPath": state.paths.app_dir.to_string_lossy(),
        "appVersion": app.package_info().version.to_string(),
        "userData": state.paths.user_data.to_string_lossy(),
        "dataRoot": state.paths.user_data.to_string_lossy(),
        "projectRoot": state.paths.project_root.to_string_lossy()
    }))
}

fn task_event_sink(app: AppHandle) -> TaskEventSink {
    Arc::new(move |event| match event {
        TaskRuntimeEvent::State { state } => {
            let _ = app.emit("python-process-state", state);
        }
        TaskRuntimeEvent::Output { stream: _, text } => {
            let _ = app.emit("python-log", text);
        }
        TaskRuntimeEvent::StructuredLog { .. } => {
            // The raw structured line is already part of Output and remains
            // available to the renderer's detailed diagnostic parser.
        }
        TaskRuntimeEvent::Diagnostic { level, message } => {
            let prefix = match level {
                DiagnosticLevel::Info => "",
                DiagnosticLevel::Warn => "警告：",
                DiagnosticLevel::Error => "错误：",
            };
            let _ = app.emit("python-log", format!("{prefix}{message}\n"));
        }
    })
}

fn task_plugin_context(plugin: &ResolvedPluginContext) -> PluginExecutionContext {
    PluginExecutionContext {
        plugin_id: plugin.plugin_id.clone(),
        plugin_version: plugin.plugin_version.clone(),
        plugin_root: plugin.plugin_root.clone(),
        plugin_data_dir: plugin.plugin_data_dir.clone(),
        permissions: plugin.permissions.clone(),
    }
}

fn resolve_managed_file_path(
    value: &str,
    state: &AppState,
    allow_project_root: bool,
) -> Result<PathBuf, String> {
    if value.trim().is_empty() {
        return Err("文件路径不能为空".to_string());
    }
    let expanded = expand_home(value);
    let path = normalize_absolute(Path::new(&expanded));
    let mut roots = vec![state.paths.user_data.clone()];
    if allow_project_root {
        roots.push(state.paths.project_root.clone());
    }
    if !roots.iter().any(|root| is_inside(root, &path)) {
        return Err("只允许访问万能导应用数据目录中的配置文件。".to_string());
    }
    Ok(path)
}

fn expand_home(value: &str) -> String {
    let value = value.trim();
    if value == "~" {
        return dirs::home_dir()
            .unwrap_or_else(|| PathBuf::from(value))
            .to_string_lossy()
            .to_string();
    }
    if let Some(rest) = value
        .strip_prefix("~/")
        .or_else(|| value.strip_prefix("~\\"))
    {
        return dirs::home_dir()
            .unwrap_or_else(|| PathBuf::from("~"))
            .join(rest)
            .to_string_lossy()
            .to_string();
    }
    value.to_string()
}

fn app_settings_path(paths: &crate::app_state::AppPaths) -> PathBuf {
    paths.user_data.join("settings.json")
}

fn read_app_settings(paths: &crate::app_state::AppPaths) -> Value {
    let mut settings = read_json(&app_settings_path(paths)).unwrap_or_else(|_| json!({}));
    if !settings.is_object() {
        settings = json!({});
    }
    if settings
        .get("schemaVersion")
        .and_then(Value::as_u64)
        .is_none_or(|version| version < 1)
    {
        settings["schemaVersion"] = json!(SETTINGS_SCHEMA_VERSION);
    }
    settings
}

fn public_app_settings(settings: &Value) -> Value {
    json!({
        "schemaVersion": settings.get("schemaVersion").and_then(Value::as_u64).unwrap_or(SETTINGS_SCHEMA_VERSION),
        "browserPath": settings.get("browserPath").or_else(|| settings.get("browser_path")).and_then(Value::as_str).unwrap_or(""),
        "updatedAt": settings.get("updatedAt").or_else(|| settings.get("updated_at")).and_then(Value::as_str).unwrap_or("")
    })
}

fn save_settings_update(
    paths: &crate::app_state::AppPaths,
    update: Value,
) -> Result<Value, String> {
    let mut settings = read_app_settings(paths);
    settings["schemaVersion"] = json!(SETTINGS_SCHEMA_VERSION);
    if let Some(raw) = update
        .get("browser_path")
        .or_else(|| update.get("browserPath"))
    {
        let raw = raw.as_str().unwrap_or_default().trim();
        if raw.is_empty() {
            settings
                .as_object_mut()
                .expect("settings object")
                .remove("browserPath");
        } else {
            let browser = normalize_browser_executable(raw).ok_or_else(|| {
                "没有找到这个浏览器文件，请选择 Chrome、Edge 或 Chromium 的可执行文件。".to_string()
            })?;
            settings["browserPath"] = json!(browser.to_string_lossy());
        }
    }
    settings["updatedAt"] = json!(Utc::now().to_rfc3339());
    let content = serde_json::to_vec_pretty(&settings).map_err(|error| error.to_string())?;
    write_private_atomic(&app_settings_path(paths), &content)?;
    Ok(settings)
}

fn selected_browser_path(paths: &crate::app_state::AppPaths) -> Option<PathBuf> {
    let settings = read_app_settings(paths);
    settings
        .get("browserPath")
        .or_else(|| settings.get("browser_path"))
        .and_then(Value::as_str)
        .and_then(normalize_browser_executable)
}

fn normalize_browser_executable(value: &str) -> Option<PathBuf> {
    let raw = value.trim();
    if raw.is_empty() {
        return None;
    }
    if !raw.contains(['/', '\\']) && !Path::new(raw).is_absolute() {
        return find_executable_on_path(raw);
    }
    let path = normalize_absolute(Path::new(&expand_home(raw)));
    #[cfg(target_os = "macos")]
    if path
        .extension()
        .and_then(|value| value.to_str())
        .is_some_and(|value| value.eq_ignore_ascii_case("app"))
    {
        let app_name = path
            .file_stem()
            .and_then(|value| value.to_str())
            .unwrap_or("");
        for name in [
            app_name,
            app_name.trim_end_matches(" Browser"),
            "Google Chrome",
            "Microsoft Edge",
            "Chromium",
            "Brave Browser",
        ] {
            let candidate = path.join("Contents").join("MacOS").join(name);
            if candidate.is_file() {
                return Some(candidate);
            }
        }
        return None;
    }
    path.is_file().then_some(path)
}

fn find_executable_on_path(command: &str) -> Option<PathBuf> {
    let path_value = env::var_os("PATH").unwrap_or_default();
    let paths = env::split_paths(&path_value);
    #[cfg(target_os = "windows")]
    let names: Vec<String> = if Path::new(command).extension().is_none() {
        let mut values = vec![command.to_string()];
        values.extend(
            env::var("PATHEXT")
                .unwrap_or_else(|_| ".EXE;.CMD;.BAT;.COM".into())
                .split(';')
                .filter(|value| !value.is_empty())
                .map(|extension| format!("{command}{extension}")),
        );
        values
    } else {
        vec![command.to_string()]
    };
    #[cfg(not(target_os = "windows"))]
    let names = vec![command.to_string()];
    for directory in paths {
        for name in &names {
            let path = directory.join(name);
            if path.is_file() {
                return Some(normalize_absolute(&path));
            }
        }
    }
    None
}

fn detect_browsers_internal() -> Vec<Value> {
    let mut specs: Vec<(&str, &str, Vec<PathBuf>, Vec<&str>)> = Vec::new();
    #[cfg(target_os = "windows")]
    {
        let program_files = env::var_os("PROGRAMFILES")
            .map_or_else(|| PathBuf::from(r"C:\Program Files"), PathBuf::from);
        let program_files_x86 = env::var_os("PROGRAMFILES(X86)")
            .map_or_else(|| PathBuf::from(r"C:\Program Files (x86)"), PathBuf::from);
        let local = env::var_os("LOCALAPPDATA").map(PathBuf::from);
        let paths = |parts: &[&str]| {
            let mut values = vec![
                parts
                    .iter()
                    .fold(program_files.clone(), |path, item| path.join(item)),
                parts
                    .iter()
                    .fold(program_files_x86.clone(), |path, item| path.join(item)),
            ];
            if let Some(local) = &local {
                values.push(
                    parts
                        .iter()
                        .fold(local.clone(), |path, item| path.join(item)),
                );
            }
            values
        };
        specs.push((
            "chrome",
            "Google Chrome",
            paths(&["Google", "Chrome", "Application", "chrome.exe"]),
            vec!["chrome", "chrome.exe", "google-chrome"],
        ));
        specs.push((
            "edge",
            "Microsoft Edge",
            paths(&["Microsoft", "Edge", "Application", "msedge.exe"]),
            vec!["msedge", "msedge.exe"],
        ));
        specs.push((
            "chromium",
            "Chromium",
            paths(&["Chromium", "Application", "chrome.exe"]),
            vec!["chromium", "chromium.exe"],
        ));
        specs.push((
            "brave",
            "Brave",
            paths(&["BraveSoftware", "Brave-Browser", "Application", "brave.exe"]),
            vec!["brave", "brave.exe"],
        ));
    }
    #[cfg(target_os = "macos")]
    {
        let mut roots = vec![PathBuf::from("/Applications")];
        if let Some(home) = dirs::home_dir() {
            roots.push(home.join("Applications"));
        }
        let app_paths = |bundle: &str, executable: &str| {
            roots
                .iter()
                .map(|root| {
                    root.join(bundle)
                        .join("Contents")
                        .join("MacOS")
                        .join(executable)
                })
                .collect()
        };
        specs.push((
            "chrome",
            "Google Chrome",
            app_paths("Google Chrome.app", "Google Chrome"),
            vec![],
        ));
        specs.push((
            "edge",
            "Microsoft Edge",
            app_paths("Microsoft Edge.app", "Microsoft Edge"),
            vec![],
        ));
        specs.push((
            "chromium",
            "Chromium",
            app_paths("Chromium.app", "Chromium"),
            vec![],
        ));
        specs.push((
            "brave",
            "Brave",
            app_paths("Brave Browser.app", "Brave Browser"),
            vec![],
        ));
    }
    #[cfg(target_os = "linux")]
    {
        specs.push((
            "chrome",
            "Google Chrome",
            vec![
                PathBuf::from("/usr/bin/google-chrome"),
                PathBuf::from("/usr/bin/google-chrome-stable"),
                PathBuf::from("/opt/google/chrome/chrome"),
            ],
            vec!["google-chrome", "google-chrome-stable", "chrome"],
        ));
        specs.push((
            "edge",
            "Microsoft Edge",
            vec![
                PathBuf::from("/usr/bin/microsoft-edge"),
                PathBuf::from("/usr/bin/microsoft-edge-stable"),
            ],
            vec!["microsoft-edge", "microsoft-edge-stable"],
        ));
        specs.push((
            "chromium",
            "Chromium",
            vec![
                PathBuf::from("/usr/bin/chromium"),
                PathBuf::from("/usr/bin/chromium-browser"),
                PathBuf::from("/snap/bin/chromium"),
            ],
            vec!["chromium", "chromium-browser"],
        ));
        specs.push((
            "brave",
            "Brave",
            vec![
                PathBuf::from("/usr/bin/brave-browser"),
                PathBuf::from("/snap/bin/brave"),
            ],
            vec!["brave-browser", "brave"],
        ));
    }
    let mut seen = HashMap::<String, ()>::new();
    let mut output = Vec::new();
    for (id, name, paths, commands) in specs {
        for (candidate, source) in paths.into_iter().map(|path| (path, "默认安装位置")).chain(
            commands
                .into_iter()
                .filter_map(find_executable_on_path)
                .map(|path| (path, "PATH")),
        ) {
            let Some(path) = normalize_browser_executable(&candidate.to_string_lossy()) else {
                continue;
            };
            let key = if cfg!(target_os = "windows") {
                path.to_string_lossy().to_lowercase()
            } else {
                path.to_string_lossy().to_string()
            };
            if seen.insert(key, ()).is_none() {
                output.push(json!({
                    "id": id,
                    "name": name,
                    "path": path.to_string_lossy(),
                    "source": source
                }));
            }
        }
    }
    output
}

fn python_command(paths: &crate::app_state::AppPaths) -> (PathBuf, Option<PathBuf>) {
    if let Some(command) = non_empty_path(env::var_os("WANDAO_PYTHON"))
        .or_else(|| non_empty_path(env::var_os("PYTHON")))
    {
        return (command, None);
    }
    let executable = if cfg!(target_os = "windows") {
        paths.bundled_python_runtime.join("python.exe")
    } else {
        paths.bundled_python_runtime.join("bin").join("python3")
    };
    if executable.is_file() {
        return (executable, Some(paths.bundled_python_runtime.clone()));
    }
    (
        PathBuf::from(if cfg!(target_os = "windows") {
            "python"
        } else {
            "python3"
        }),
        None,
    )
}

fn non_empty_path(value: Option<std::ffi::OsString>) -> Option<PathBuf> {
    value.filter(|value| !value.is_empty()).map(PathBuf::from)
}

fn extract_sensitive_arguments(args: Vec<String>) -> (Vec<String>, BTreeMap<String, String>) {
    let sensitive = [
        ("--app-secret", "FEISHU_APP_SECRET"),
        ("--api-key", "IMA_API_KEY"),
    ];
    let mut output = Vec::new();
    let mut environment = BTreeMap::new();
    let mut index = 0;
    while index < args.len() {
        if let Some((_, name)) = sensitive
            .iter()
            .find(|(argument, _)| *argument == args[index])
        {
            if index + 1 < args.len() {
                environment.insert((*name).to_string(), args[index + 1].clone());
                index += 2;
                continue;
            }
        }
        output.push(args[index].clone());
        index += 1;
    }
    (output, environment)
}

fn compress_doc_id_args(
    script_name: &str,
    args: Vec<String>,
    user_data: &Path,
) -> Result<Vec<String>, String> {
    let mut doc_ids = Vec::new();
    let mut compact = Vec::new();
    let mut index = 0;
    while index < args.len() {
        if args[index] == "--doc-id" && index + 1 < args.len() {
            doc_ids.push(args[index + 1].clone());
            index += 2;
        } else {
            compact.push(args[index].clone());
            index += 1;
        }
    }
    let command_length: usize = args.iter().map(|value| value.len() + 3).sum();
    if doc_ids.is_empty() || (doc_ids.len() < 50 && command_length < 12_000) {
        return Ok(args);
    }
    let tmp = user_data.join("tmp");
    fs::create_dir_all(&tmp).map_err(|error| error.to_string())?;
    let script = script_name.rsplit(':').next().unwrap_or(script_name);
    let prefix = Path::new(script)
        .file_stem()
        .and_then(|value| value.to_str())
        .unwrap_or("provider")
        .trim_start_matches("export_");
    let suffix = uuid::Uuid::new_v4().simple().to_string();
    let file = tmp.join(format!(
        "{prefix}-doc-ids-{}-{}.json",
        Utc::now().timestamp_millis(),
        &suffix[..6]
    ));
    write_private_atomic(
        &file,
        serde_json::to_string_pretty(&json!({"docIds": doc_ids}))
            .map_err(|error| error.to_string())?
            .as_bytes(),
    )?;
    compact.push("--doc-id-file".into());
    compact.push(file.to_string_lossy().to_string());
    Ok(compact)
}

fn cleanup_temporary_doc_id_file(args: &[String], user_data: &Path) {
    let Some(index) = args.iter().position(|value| value == "--doc-id-file") else {
        return;
    };
    let Some(candidate) = args.get(index + 1).map(PathBuf::from) else {
        return;
    };
    let root = user_data.join("tmp");
    let name = candidate
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or_default();
    let valid_name = name.contains("-doc-ids-")
        && name.ends_with(".json")
        && name
            .trim_end_matches(".json")
            .rsplit('-')
            .next()
            .is_some_and(|suffix| {
                !suffix.is_empty()
                    && suffix.chars().all(|character| {
                        character.is_ascii_lowercase() || character.is_ascii_digit()
                    })
            });
    if valid_name && is_inside(&root, &candidate) {
        let _ = fs::remove_file(candidate);
    }
}

fn sanitize_file_id(value: &str) -> String {
    value
        .chars()
        .map(|character| {
            if character.is_ascii_alphanumeric() || matches!(character, '-' | '_') {
                character
            } else {
                '_'
            }
        })
        .collect()
}

fn is_allowed_external_url(value: &str) -> bool {
    url::Url::parse(value).is_ok_and(|url| url.scheme() == "https")
}

fn is_allowed_remote_text_url(value: &str) -> bool {
    url::Url::parse(value).is_ok_and(|url| is_allowed_remote_text_target(&url))
}

fn is_allowed_remote_text_target(url: &url::Url) -> bool {
    if url.scheme() != "https" {
        return false;
    }
    matches!(
        url.host_str(),
        Some("raw.githubusercontent.com" | "github.com")
    ) && url.path().starts_with("/tllovesxs/wandao/")
}

#[derive(Clone, Copy)]
enum RedirectUrlPolicy {
    SecureTransport { allow_local_http: bool },
    RemoteText,
    RemoteGuideImage,
}

impl RedirectUrlPolicy {
    fn allows(self, url: &url::Url) -> bool {
        match self {
            Self::SecureTransport { allow_local_http } => {
                url.scheme() == "https"
                    || (allow_local_http
                        && url.scheme() == "http"
                        && matches!(url.host_str(), Some("127.0.0.1" | "localhost")))
            }
            Self::RemoteText => is_allowed_remote_text_target(url),
            Self::RemoteGuideImage => is_allowed_remote_guide_image_url("feishu-import", url),
        }
    }
}

fn restricted_redirect_policy(
    url_policy: RedirectUrlPolicy,
    unsafe_redirect_error: &'static str,
) -> reqwest::redirect::Policy {
    reqwest::redirect::Policy::custom(move |attempt| {
        if attempt.previous().len() > 5 {
            attempt.error("重定向次数过多")
        } else if url_policy.allows(attempt.url()) {
            attempt.follow()
        } else {
            attempt.error(unsafe_redirect_error)
        }
    })
}

async fn fetch_limited(
    url: &str,
    max_bytes: usize,
    user_agent: &str,
    url_policy: RedirectUrlPolicy,
) -> Result<Vec<u8>, String> {
    let parsed = url::Url::parse(url).map_err(|error| error.to_string())?;
    if !url_policy.allows(&parsed) {
        return Err("远程内容地址不安全".to_string());
    }
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(12))
        .redirect(restricted_redirect_policy(
            url_policy,
            "远程内容重定向到不安全地址",
        ))
        .build()
        .map_err(|error| error.to_string())?;
    let mut response = client
        .get(parsed)
        .header(reqwest::header::USER_AGENT, user_agent)
        .send()
        .await
        .map_err(|error| error.to_string())?;
    if !response.status().is_success() {
        return Err(format!("GitHub 返回 HTTP {}", response.status().as_u16()));
    }
    if response
        .content_length()
        .is_some_and(|length| length > max_bytes as u64)
    {
        return Err("远程内容超过大小限制".to_string());
    }
    let mut output = Vec::new();
    while let Some(chunk) = response.chunk().await.map_err(|error| error.to_string())? {
        if output.len().saturating_add(chunk.len()) > max_bytes {
            return Err("远程内容超过大小限制".to_string());
        }
        output.extend_from_slice(&chunk);
    }
    Ok(output)
}

async fn fetch_remote_guide_image(
    provider_id: &str,
    url: url::Url,
    spec: &GuideAssetSpec,
) -> Result<String, String> {
    if !is_allowed_remote_guide_image_url(provider_id, &url) {
        return Err("教程图片地址不在允许的不可变仓库范围".to_string());
    }
    if spec.mime != "image/png"
        || spec.bytes == 0
        || spec.bytes > MAX_GUIDE_ASSET_BYTES
        || spec.sha256.len() != 64
    {
        return Err("教程图片完整性声明无效".to_string());
    }
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(20))
        .redirect(restricted_redirect_policy(
            RedirectUrlPolicy::RemoteGuideImage,
            "教程图片重定向到允许范围之外",
        ))
        .build()
        .map_err(|error| error.to_string())?;
    let mut response = client
        .get(url)
        .header(reqwest::header::USER_AGENT, "Wandao-Guide-Images")
        .send()
        .await
        .map_err(|error| format!("教程图片下载失败：{error}"))?;
    if !is_allowed_remote_guide_image_url(provider_id, response.url()) {
        return Err("教程图片最终地址不在允许范围".to_string());
    }
    if !response.status().is_success() {
        return Err(format!(
            "教程图片下载失败 HTTP {}",
            response.status().as_u16()
        ));
    }
    let content_type = response
        .headers()
        .get(reqwest::header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.split(';').next())
        .map(str::trim)
        .unwrap_or_default();
    if content_type != spec.mime {
        return Err(format!(
            "教程图片 MIME 不符合预期：期望 {}，实际 {}",
            spec.mime,
            if content_type.is_empty() {
                "缺失"
            } else {
                content_type
            }
        ));
    }
    if response
        .content_length()
        .is_some_and(|length| length != spec.bytes as u64)
    {
        return Err("教程图片 Content-Length 与完整性声明不一致".to_string());
    }
    let mut output = Vec::with_capacity(spec.bytes);
    while let Some(chunk) = response
        .chunk()
        .await
        .map_err(|error| format!("教程图片下载失败：{error}"))?
    {
        if output.len().saturating_add(chunk.len()) > spec.bytes
            || output.len().saturating_add(chunk.len()) > MAX_GUIDE_ASSET_BYTES
        {
            return Err("教程图片超过大小限制".to_string());
        }
        output.extend_from_slice(&chunk);
    }
    if output.len() != spec.bytes {
        return Err("教程图片字节数与完整性声明不一致".to_string());
    }
    if !output.starts_with(b"\x89PNG\r\n\x1a\n") {
        return Err("教程图片不是有效的 PNG 文件".to_string());
    }
    if sha256_hex(&output) != spec.sha256 {
        return Err("教程图片 SHA-256 校验失败".to_string());
    }
    Ok(format!(
        "data:{};base64,{}",
        spec.mime,
        BASE64.encode(output)
    ))
}

async fn current_registry(
    _app: &AppHandle,
    state: &AppState,
    manager: &PluginManager,
    force: bool,
    channel: &str,
) -> Result<Value, String> {
    if !matches!(channel, "stable" | "experimental") {
        return Err(format!("未知插件发布等级：{channel}"));
    }
    if !force {
        if let Ok(cache) = state.registry_cache.lock() {
            if let Some(cached) = cache.get(channel) {
                if cached.cached_at.elapsed() < Duration::from_secs(5 * 60) {
                    return Ok(cached.registry.clone());
                }
            }
        }
    }
    let url = match channel {
        "experimental" => env::var("WANDAO_EXPERIMENTAL_PLUGIN_REGISTRY_URL")
            .unwrap_or_else(|_| EXPERIMENTAL_REGISTRY_URL.into()),
        _ => env::var("WANDAO_PLUGIN_REGISTRY_URL").unwrap_or_else(|_| STABLE_REGISTRY_URL.into()),
    };
    let bytes = fetch_limited(
        &url,
        MAX_REGISTRY_BYTES,
        "Wandao-Plugin-Manager",
        RedirectUrlPolicy::SecureTransport {
            allow_local_http: env::var_os("WANDAO_PLUGIN_ALLOW_LOCAL_HTTP").is_some(),
        },
    )
    .await?;
    let mut registry: Value = serde_json::from_slice(&bytes)
        .map_err(|error| format!("插件注册表不是有效 JSON：{error}"))?;
    manager.verify_registry(&registry)?;
    if let Some(plugins) = registry.get_mut("plugins").and_then(Value::as_array_mut) {
        for plugin in plugins {
            if plugin.get("channel").is_none() {
                plugin["channel"] = json!(channel);
            }
        }
    }
    if let Ok(mut cache) = state.registry_cache.lock() {
        cache.insert(
            channel.to_string(),
            CachedRegistry {
                cached_at: std::time::Instant::now(),
                registry: registry.clone(),
            },
        );
    }
    Ok(registry)
}

async fn download_plugin_with_progress(
    app: &AppHandle,
    plugin_id: &str,
    url: &str,
) -> Result<Vec<u8>, String> {
    let parsed = url::Url::parse(url).map_err(|error| error.to_string())?;
    let url_policy = RedirectUrlPolicy::SecureTransport {
        allow_local_http: env::var_os("WANDAO_PLUGIN_ALLOW_LOCAL_HTTP").is_some(),
    };
    if !url_policy.allows(&parsed) {
        return Err("插件只允许通过 HTTPS 下载".to_string());
    }
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(30))
        .redirect(restricted_redirect_policy(
            url_policy,
            "插件下载重定向到不安全地址",
        ))
        .build()
        .map_err(|error| error.to_string())?;
    let mut response = client
        .get(parsed)
        .header(reqwest::header::USER_AGENT, "Wandao-Plugin-Manager")
        .send()
        .await
        .map_err(|error| error.to_string())?;
    if !response.status().is_success() {
        return Err(format!("插件下载失败 HTTP {}", response.status().as_u16()));
    }
    let total = response.content_length().unwrap_or(0);
    if total > MAX_PLUGIN_DOWNLOAD_BYTES as u64 {
        return Err("插件下载超过大小限制".to_string());
    }
    let _ = app.emit(
        "plugin-download-progress",
        json!({
            "pluginId": plugin_id,
            "phase": "downloading",
            "receivedBytes": 0,
            "totalBytes": total
        }),
    );
    let mut output = Vec::new();
    while let Some(chunk) = response.chunk().await.map_err(|error| error.to_string())? {
        if output.len().saturating_add(chunk.len()) > MAX_PLUGIN_DOWNLOAD_BYTES {
            return Err("插件下载超过大小限制".to_string());
        }
        output.extend_from_slice(&chunk);
        let _ = app.emit(
            "plugin-download-progress",
            json!({
                "pluginId": plugin_id,
                "phase": "downloading",
                "receivedBytes": output.len(),
                "totalBytes": total
            }),
        );
    }
    Ok(output)
}

fn bundled_plugin_catalog(
    paths: &crate::app_state::AppPaths,
    manager: &PluginManager,
) -> HashMap<String, Value> {
    let mut output = HashMap::new();
    let Ok(entries) = fs::read_dir(&paths.bundled_plugins) else {
        return output;
    };
    for entry in entries.flatten().filter(|entry| entry.path().is_dir()) {
        let id = entry.file_name().to_string_lossy().to_string();
        if id.starts_with(['_', '.']) || output.contains_key(&id) {
            continue;
        }
        let manifest = match verify_bundled_plugin(&paths.bundled_plugins, &id) {
            Ok(verified) => verified.manifest,
            _ => continue,
        };
        if manifest.get("id").and_then(Value::as_str) != Some(&id) {
            continue;
        }
        if manifest
            .get("platforms")
            .and_then(Value::as_array)
            .is_some_and(|platforms| {
                !platforms.is_empty()
                    && !platforms
                        .iter()
                        .any(|platform| platform.as_str() == Some(crate::app_state::platform_id()))
            })
        {
            continue;
        }
        let mut catalog = manifest.clone();
        let version = catalog.get("version").cloned().unwrap_or_else(|| json!(""));
        let object = catalog.as_object_mut().expect("manifest object");
        object.insert("bundled".into(), json!(true));
        object.insert("channel".into(), json!("stable"));
        object.insert("bundledVersion".into(), version);
        object.insert("installed".into(), json!(false));
        object.insert("enabled".into(), json!(true));
        object.insert("installedVersion".into(), json!(""));
        object.insert("updateAvailable".into(), json!(false));
        object.insert("previousVersions".into(), json!([]));
        object.insert("compatibility".into(), manager.compatibility(&manifest));
        output.insert(id, catalog);
    }
    output
}

fn plugin_catalog_with_bundled(
    paths: &crate::app_state::AppPaths,
    manager: &PluginManager,
    registry: Option<&Value>,
) -> Result<Vec<Value>, String> {
    let mut bundled = bundled_plugin_catalog(paths, manager);
    let mut output = Vec::new();
    for remote in manager.list_with_registry(registry)? {
        let id = remote
            .get("id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let Some(mut builtin) = bundled.remove(&id) else {
            output.push(remote);
            continue;
        };
        let builtin_version = builtin
            .get("version")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let installed = remote
            .get("installed")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        if let (Some(base), Some(values)) = (builtin.as_object_mut(), remote.as_object()) {
            base.extend(values.clone());
            base.insert("bundled".into(), json!(true));
            base.insert("bundledVersion".into(), json!(builtin_version));
            base.insert(
                "updateAvailable".into(),
                if installed {
                    remote
                        .get("updateAvailable")
                        .cloned()
                        .unwrap_or(json!(false))
                } else {
                    json!(
                        compare_versions(
                            remote
                                .get("version")
                                .and_then(Value::as_str)
                                .unwrap_or_default(),
                            &builtin_version
                        ) > 0
                    )
                },
            );
        }
        output.push(builtin);
    }
    output.extend(bundled.into_values());
    output.sort_by(|left, right| {
        left.get("name")
            .or_else(|| left.get("id"))
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_lowercase()
            .cmp(
                &right
                    .get("name")
                    .or_else(|| right.get("id"))
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_lowercase(),
            )
    });
    Ok(output)
}

fn file_path_string(path: FilePath) -> Option<String> {
    file_path_to_path(path).map(|path| path.to_string_lossy().to_string())
}

fn file_path_to_path(path: FilePath) -> Option<PathBuf> {
    path.into_path().ok()
}

fn save_default_path_parts(path: PathBuf) -> (Option<PathBuf>, Option<String>) {
    if path.is_dir() || path.file_name().is_none() {
        return (Some(path), None);
    }
    let directory = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
        .map(Path::to_path_buf);
    let file_name = path
        .file_name()
        .map(|file_name| file_name.to_string_lossy().into_owned());
    (directory, file_name)
}

fn value_string(value: &Value, keys: &[&str]) -> Option<String> {
    keys.iter()
        .find_map(|key| value.get(*key).and_then(Value::as_str))
        .map(str::to_string)
}

fn initial_stdin_text(options: &Value) -> Option<String> {
    value_string(options, &["stdin_text", "stdinText"]).filter(|text| !text.is_empty())
}

fn value_to_string(value: &Value) -> String {
    match value {
        Value::Null => String::new(),
        Value::Bool(value) => value.to_string(),
        Value::Number(value) => value.to_string(),
        Value::String(value) => value.clone(),
        other => serde_json::to_string(other).unwrap_or_default(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sensitive_arguments_never_reach_the_process_command_line() {
        let (args, secrets) = extract_sensitive_arguments(vec![
            "--app-id".into(),
            "cli_x".into(),
            "--app-secret".into(),
            "secret".into(),
            "--api-key".into(),
            "key".into(),
        ]);
        assert_eq!(args, vec!["--app-id", "cli_x"]);
        assert_eq!(secrets.get("FEISHU_APP_SECRET").unwrap(), "secret");
        assert_eq!(secrets.get("IMA_API_KEY").unwrap(), "key");
    }

    #[test]
    fn remote_text_is_restricted_to_the_wandao_repository() {
        assert!(is_allowed_remote_text_url(
            "https://raw.githubusercontent.com/tllovesxs/wandao/main/docs/announcement.md"
        ));
        assert!(!is_allowed_remote_text_url(
            "https://raw.githubusercontent.com/other/repo/main/payload.md"
        ));
        assert!(!is_allowed_remote_text_url(
            "http://github.com/tllovesxs/wandao/main/README.md"
        ));
    }

    #[test]
    fn save_default_path_separates_parent_directory_and_file_name() {
        let (directory, file_name) = save_default_path_parts(PathBuf::from("/tmp/report.json"));
        assert_eq!(directory, Some(PathBuf::from("/tmp")));
        assert_eq!(file_name.as_deref(), Some("report.json"));

        let (directory, file_name) = save_default_path_parts(PathBuf::from("report.json"));
        assert_eq!(directory, None);
        assert_eq!(file_name.as_deref(), Some("report.json"));
    }

    #[test]
    fn empty_initial_stdin_keeps_the_interactive_pipe_open() {
        assert_eq!(initial_stdin_text(&json!({})), None);
        assert_eq!(initial_stdin_text(&json!({"stdinText": null})), None);
        assert_eq!(initial_stdin_text(&json!({"stdinText": ""})), None);
        assert_eq!(
            initial_stdin_text(&json!({"stdinText": "password\n"})).as_deref(),
            Some("password\n")
        );
    }

    #[test]
    fn redirects_revalidate_transport_and_remote_text_scope() {
        let https_only = RedirectUrlPolicy::SecureTransport {
            allow_local_http: false,
        };
        assert!(https_only.allows(&url::Url::parse("https://example.test/plugin").unwrap()));
        assert!(!https_only.allows(&url::Url::parse("http://example.test/plugin").unwrap()));
        assert!(!https_only.allows(&url::Url::parse("http://127.0.0.1/plugin").unwrap()));

        let local_development = RedirectUrlPolicy::SecureTransport {
            allow_local_http: true,
        };
        assert!(local_development.allows(&url::Url::parse("http://localhost/plugin").unwrap()));
        assert!(local_development.allows(&url::Url::parse("http://127.0.0.1/plugin").unwrap()));
        assert!(!local_development.allows(&url::Url::parse("http://127.0.0.2/plugin").unwrap()));

        assert!(RedirectUrlPolicy::RemoteText.allows(
            &url::Url::parse("https://raw.githubusercontent.com/tllovesxs/wandao/main/README.md")
                .unwrap()
        ));
        assert!(!RedirectUrlPolicy::RemoteText
            .allows(&url::Url::parse("https://example.test/tllovesxs/wandao/README.md").unwrap()));
        assert!(!RedirectUrlPolicy::RemoteText.allows(
            &url::Url::parse("http://raw.githubusercontent.com/tllovesxs/wandao/main/README.md")
                .unwrap()
        ));

        assert!(RedirectUrlPolicy::RemoteGuideImage.allows(
            &url::Url::parse(
                "https://raw.githubusercontent.com/tllovesxs/wandao/\
                 82c027b054d9ece8449af30d79600814eb823e46/\
                 plugins/feishu/providers/feishu-import/images/20.png"
            )
            .unwrap()
        ));
        assert!(!RedirectUrlPolicy::RemoteGuideImage.allows(
            &url::Url::parse(
                "https://raw.githubusercontent.com/tllovesxs/wandao/main/\
                 plugins/feishu/providers/feishu-import/images/20.png"
            )
            .unwrap()
        ));
    }

    #[test]
    fn empty_python_override_does_not_become_an_executable_path() {
        assert_eq!(non_empty_path(None), None);
        assert_eq!(non_empty_path(Some("".into())), None);
        assert_eq!(
            non_empty_path(Some("python-custom".into())),
            Some(PathBuf::from("python-custom"))
        );
    }
}
