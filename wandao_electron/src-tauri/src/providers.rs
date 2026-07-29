use std::{
    collections::{HashMap, HashSet},
    fs,
    path::{Path, PathBuf},
};

use base64::{engine::general_purpose::STANDARD as BASE64, Engine};
use serde_json::{json, Map, Value};

use crate::{
    app_state::{is_inside, normalize_absolute, platform_id, AppPaths},
    plugins::{
        compare_versions, string_array, verify_bundled_plugin, verify_bundled_plugin_file,
        PluginDiscovery, PluginManager, PluginProviderEntry,
    },
    security::{read_json, safe_relative_path},
};

const PROVIDER_TYPES: &[&str] = &["automation", "guide", "hybrid"];
const PROVIDER_GROUPS: &[&str] = &["export", "import", "guide"];
const PROVIDER_TRUST: &[&str] = &["official", "community", "local", "experimental", "guide"];
const PROVIDER_STATUSES: &[&str] = &["stable", "beta", "experimental"];
const FIELD_TYPES: &[&str] = &[
    "text",
    "password",
    "number",
    "textarea",
    "directory",
    "file",
    "checkbox",
    "select",
    "notice",
];
const ACTION_KINDS: &[&str] = &[
    "login", "scan", "export", "import", "plan", "check", "custom",
];
pub const MAX_GUIDE_ASSET_BYTES: usize = 3 * 1024 * 1024;
const FEISHU_GUIDE_RAW_PATH_PREFIX: &str = concat!(
    "/tllovesxs/wandao/",
    "82c027b054d9ece8449af30d79600814eb823e46/",
    "plugins/feishu/providers/feishu-import/images/"
);
const FEISHU_GUIDE_BLOB_PATH_PREFIX: &str = concat!(
    "/tllovesxs/wandao/blob/",
    "82c027b054d9ece8449af30d79600814eb823e46/",
    "plugins/feishu/providers/feishu-import/images/"
);
const FEISHU_GUIDE_GITHUB_RAW_PATH_PREFIX: &str = concat!(
    "/tllovesxs/wandao/raw/",
    "82c027b054d9ece8449af30d79600814eb823e46/",
    "plugins/feishu/providers/feishu-import/images/"
);

#[derive(Debug, Default)]
pub struct ProviderDiscovery {
    pub providers: Vec<Value>,
    pub errors: Vec<String>,
    pub guide_roots: HashMap<String, PathBuf>,
}

#[derive(Debug, Clone)]
pub struct ResolvedPluginContext {
    pub plugin_id: String,
    pub plugin_version: String,
    pub plugin_root: PathBuf,
    pub plugin_data_dir: PathBuf,
    pub permissions: Vec<String>,
}

#[derive(Debug, Clone)]
pub struct ResolvedScript {
    pub path: PathBuf,
    pub plugin: Option<ResolvedPluginContext>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GuideAssetSpec {
    pub mime: String,
    pub bytes: usize,
    pub sha256: String,
}

fn valid_feishu_guide_image_name(value: &str) -> bool {
    let Some(stem) = value.strip_suffix(".png") else {
        return false;
    };
    let Some(number) = stem.parse::<u8>().ok() else {
        return false;
    };
    (1..=20).contains(&number) && stem == number.to_string()
}

pub fn is_allowed_remote_guide_image_url(provider_id: &str, url: &url::Url) -> bool {
    if provider_id != "feishu-import"
        || url.scheme() != "https"
        || !url.username().is_empty()
        || url.password().is_some()
        || url.port().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return false;
    }
    let file_name = match url.host_str() {
        Some("raw.githubusercontent.com") => url.path().strip_prefix(FEISHU_GUIDE_RAW_PATH_PREFIX),
        Some("github.com") => url
            .path()
            .strip_prefix(FEISHU_GUIDE_BLOB_PATH_PREFIX)
            .or_else(|| url.path().strip_prefix(FEISHU_GUIDE_GITHUB_RAW_PATH_PREFIX)),
        _ => None,
    };
    file_name.is_some_and(valid_feishu_guide_image_name)
}

fn parse_guide_asset_spec(value: &Value) -> Result<GuideAssetSpec, String> {
    let value = value
        .as_object()
        .ok_or_else(|| "guideAssets 条目必须是对象".to_string())?;
    if value.len() != 3
        || !value.contains_key("mime")
        || !value.contains_key("bytes")
        || !value.contains_key("sha256")
    {
        return Err("guideAssets 条目必须精确声明 mime/bytes/sha256".to_string());
    }
    let mime = value
        .get("mime")
        .and_then(Value::as_str)
        .filter(|mime| *mime == "image/png")
        .ok_or_else(|| "guideAssets.mime 只允许 image/png".to_string())?;
    let bytes = value
        .get("bytes")
        .and_then(Value::as_u64)
        .filter(|bytes| *bytes > 0 && *bytes <= MAX_GUIDE_ASSET_BYTES as u64)
        .ok_or_else(|| "guideAssets.bytes 超出限制".to_string())? as usize;
    let sha256 = value
        .get("sha256")
        .and_then(Value::as_str)
        .filter(|sha256| {
            sha256.len() == 64
                && sha256
                    .bytes()
                    .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        })
        .ok_or_else(|| "guideAssets.sha256 必须是小写 SHA-256".to_string())?;
    Ok(GuideAssetSpec {
        mime: mime.to_string(),
        bytes,
        sha256: sha256.to_string(),
    })
}

pub fn remote_guide_asset_spec(
    provider_root: &Path,
    reference: &str,
) -> Result<Option<GuideAssetSpec>, String> {
    let provider = read_json(&provider_root.join("provider.json"))?;
    let Some(assets) = provider.get("guideAssets") else {
        return Ok(None);
    };
    let assets = assets
        .as_object()
        .ok_or_else(|| "guideAssets 必须是对象".to_string())?;
    assets
        .get(reference)
        .map(parse_guide_asset_spec)
        .transpose()
}

pub fn discover_provider_manifests(paths: &AppPaths, manager: &PluginManager) -> ProviderDiscovery {
    let mut output = ProviderDiscovery::default();
    let installed = manager.provider_entries_with_errors();
    output.errors.extend(
        installed
            .errors
            .iter()
            .map(|error| format!("插件校验失败：{error}")),
    );
    let bundled = bundled_plugin_entries(paths, manager);
    output.errors.extend(
        bundled
            .errors
            .iter()
            .map(|error| format!("内置插件校验失败：{error}")),
    );
    let bundled_versions: HashMap<String, String> = bundled
        .entries
        .iter()
        .map(|entry| (entry.plugin_id.clone(), entry.plugin_version.clone()))
        .collect();
    let preferred_installed = installed.entries.into_iter().filter(|entry| {
        bundled_versions
            .get(&entry.plugin_id)
            .is_none_or(|bundled| compare_versions(&entry.plugin_version, bundled) > 0)
    });
    let mut seen = HashSet::new();
    for (source_kind, entries) in [
        ("plugin", preferred_installed.collect::<Vec<_>>()),
        ("bundled-plugin", bundled.entries),
    ] {
        for entry in entries {
            let provider_root = entry
                .manifest_path
                .parent()
                .map(Path::to_path_buf)
                .unwrap_or_else(|| entry.plugin_root.clone());
            match read_json(&entry.manifest_path).and_then(|raw| {
                normalize_provider_manifest(&raw, &provider_root, source_kind, Some(&entry))
            }) {
                Ok(provider) => {
                    let id = provider["id"].as_str().unwrap_or_default().to_string();
                    if !seen.insert(id.clone()) {
                        output.errors.push(format!(
                            "{}：Provider ID 冲突，已忽略 {id}",
                            entry.manifest_path.display()
                        ));
                        continue;
                    }
                    output.guide_roots.insert(id, provider_root);
                    output.providers.push(provider);
                }
                Err(error) => output
                    .errors
                    .push(format!("{}：{error}", entry.manifest_path.display())),
            }
        }
    }

    let user_provider_root = paths.user_data.join("providers");
    for root in provider_roots(paths) {
        let Ok(entries) = fs::read_dir(&root) else {
            continue;
        };
        let source_kind = if is_inside(&user_provider_root, &root) {
            "user"
        } else {
            "bundled"
        };
        for entry in entries.flatten().filter(|entry| entry.path().is_dir()) {
            let name = entry.file_name().to_string_lossy().to_string();
            if name.starts_with(['_', '.']) {
                continue;
            }
            let provider_root = entry.path();
            let manifest_path = provider_root.join("provider.json");
            if !manifest_path.is_file() {
                continue;
            }
            match read_json(&manifest_path).and_then(|raw| {
                normalize_provider_manifest(&raw, &provider_root, source_kind, None)
            }) {
                Ok(provider) => {
                    let id = provider["id"].as_str().unwrap_or_default().to_string();
                    if !seen.insert(id.clone()) {
                        output.errors.push(format!(
                            "{}：Provider ID 冲突，已忽略 {id}",
                            manifest_path.display()
                        ));
                        continue;
                    }
                    output.guide_roots.insert(id, provider_root);
                    output.providers.push(provider);
                }
                Err(error) => output
                    .errors
                    .push(format!("{}：{error}", manifest_path.display())),
            }
        }
    }
    output
}

pub fn bundled_plugin_entries(paths: &AppPaths, manager: &PluginManager) -> PluginDiscovery {
    let mut output = PluginDiscovery::default();
    let Ok(entries) = fs::read_dir(&paths.bundled_plugins) else {
        return output;
    };
    let installed_plugins = match manager.list_installed() {
        Ok(installed) => installed,
        Err(error) => {
            output.errors.push(error);
            Vec::new()
        }
    };
    let installed: HashMap<String, Value> = installed_plugins
        .into_iter()
        .filter_map(|plugin| {
            let id = plugin
                .get("id")
                .and_then(Value::as_str)
                .map(str::to_string)?;
            Some((id, plugin))
        })
        .collect();
    let mut seen = HashSet::new();
    for entry in entries.flatten().filter(|entry| entry.path().is_dir()) {
        let directory_name = entry.file_name().to_string_lossy().to_string();
        if directory_name.starts_with(['_', '.']) {
            continue;
        }
        let plugin_root = entry.path();
        let manifest_path = plugin_root.join("plugin.json");
        if !manifest_path.is_file() {
            continue;
        }
        let result = (|| {
            let verified = verify_bundled_plugin(&paths.bundled_plugins, &directory_name)?;
            let manifest = verified.manifest;
            let plugin_root = verified.root;
            let plugin_id = manifest["id"].as_str().unwrap_or_default();
            if plugin_id != directory_name {
                return Err(format!("插件目录名必须等于插件 ID：{plugin_id}"));
            }
            if !seen.insert(plugin_id.to_string()) {
                return Ok(());
            }
            if let Some(local) = installed.get(plugin_id) {
                if local.get("enabled").and_then(Value::as_bool) == Some(false) {
                    return Ok(());
                }
                let compatible = local
                    .pointer("/compatibility/compatible")
                    .and_then(Value::as_bool)
                    == Some(true);
                let newer = compare_versions(
                    local
                        .get("currentVersion")
                        .and_then(Value::as_str)
                        .unwrap_or_default(),
                    manifest["version"].as_str().unwrap_or_default(),
                ) > 0;
                if compatible && newer {
                    return Ok(());
                }
            }
            if manifest
                .get("platforms")
                .and_then(Value::as_array)
                .is_some_and(|platforms| {
                    !platforms.is_empty()
                        && !platforms
                            .iter()
                            .any(|platform| platform.as_str() == Some(platform_id()))
                })
            {
                return Ok(());
            }
            for relative in manifest
                .pointer("/entrypoints/providers")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
            {
                let safe = safe_relative_path(relative, "Provider 入口")?;
                let provider_path = plugin_root.join(safe);
                if !is_inside(&plugin_root, &provider_path) || !provider_path.is_file() {
                    return Err(format!("Provider 入口不存在或越界：{relative}"));
                }
                output.entries.push(PluginProviderEntry {
                    plugin_id: plugin_id.to_string(),
                    plugin_version: manifest["version"].as_str().unwrap_or_default().to_string(),
                    plugin_root: plugin_root.clone(),
                    manifest_path: provider_path,
                    permissions: string_array(manifest.get("permissions")),
                    ui_entry: manifest
                        .pointer("/entrypoints/ui")
                        .and_then(Value::as_str)
                        .unwrap_or_default()
                        .to_string(),
                    verified: true,
                    bundled: true,
                });
            }
            Ok(())
        })();
        if let Err(error) = result {
            output
                .errors
                .push(format!("{}：{error}", manifest_path.display()));
        }
    }
    output
}

pub fn normalize_provider_manifest(
    raw: &Value,
    provider_root: &Path,
    source_kind: &str,
    plugin: Option<&PluginProviderEntry>,
) -> Result<Value, String> {
    validate_provider_manifest(raw, provider_root)?;
    let id = raw["id"].as_str().unwrap_or_default();
    let default_script = raw
        .get("script")
        .and_then(Value::as_str)
        .map(|script| plugin_script_reference(id, script, provider_root, plugin))
        .transpose()?
        .unwrap_or_default();
    let mut provider = raw.clone();
    let object = provider
        .as_object_mut()
        .ok_or_else(|| "provider.json 根节点必须是对象".to_string())?;
    object.insert("id".into(), json!(id));
    object.insert("sourceKind".into(), json!(source_kind));
    object.insert(
        "trustLevel".into(),
        raw.get("trustLevel")
            .cloned()
            .unwrap_or(json!(if source_kind == "user" {
                "local"
            } else {
                "community"
            })),
    );
    object.insert(
        "status".into(),
        raw.get("status").cloned().unwrap_or(json!("experimental")),
    );
    object.insert(
        "templateId".into(),
        raw.get("templateId").cloned().unwrap_or(json!("")),
    );
    object.insert(
        "guideMarkdown".into(),
        json!(read_guide_markdown(
            provider_root,
            raw.get("guide")
                .or_else(|| raw.get("guidePath"))
                .and_then(Value::as_str)
                .unwrap_or("README.md")
        )),
    );
    object.insert(
        "pluginId".into(),
        json!(plugin.map(|value| value.plugin_id.as_str()).unwrap_or("")),
    );
    object.insert(
        "pluginVersion".into(),
        json!(plugin
            .map(|value| value.plugin_version.as_str())
            .unwrap_or("")),
    );
    object.insert(
        "pluginPermissions".into(),
        json!(plugin
            .map(|value| value.permissions.clone())
            .unwrap_or_default()),
    );
    object.insert(
        "pluginVerified".into(),
        json!(plugin.is_some_and(|value| value.verified)),
    );

    if let Some(plugin) = plugin {
        if raw.pointer("/ui/mode").and_then(Value::as_str) == Some("custom") {
            let entry = raw
                .pointer("/ui/entry")
                .and_then(Value::as_str)
                .ok_or_else(|| "自定义 UI 缺少入口".to_string())?;
            let ui_path = normalize_absolute(&provider_root.join(entry));
            if !is_inside(&plugin.plugin_root, &ui_path)
                || !ui_path.is_file()
                || !has_extension(&ui_path, "html")
            {
                return Err(format!("自定义 UI 文件不存在或路径越界：{entry}"));
            }
            let relative = pathdiff::diff_paths(&ui_path, &plugin.plugin_root)
                .ok_or_else(|| "无法计算自定义 UI 路径".to_string())?
                .to_string_lossy()
                .replace('\\', "/");
            if plugin.ui_entry != relative {
                return Err("自定义 UI 必须在 plugin.json 的 entrypoints.ui 中显式声明".to_string());
            }
            object.insert("ui".into(), json!({"mode": "custom", "entry": relative}));
        }
    }

    let actions = raw
        .get("actions")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let mut normalized_actions = Vec::with_capacity(actions.len());
    for (index, action) in actions.iter().enumerate() {
        let script = action
            .get("script")
            .or_else(|| raw.get("script"))
            .and_then(Value::as_str)
            .ok_or_else(|| format!("actions[{index}].script 不能为空"))?;
        let script = plugin_script_reference(id, script, provider_root, plugin)?;
        let mut normalized = action.clone();
        normalized
            .as_object_mut()
            .ok_or_else(|| format!("actions[{index}] 必须是对象"))?
            .insert("script".into(), json!(script));
        normalized_actions.push(normalized);
    }
    if raw.get("actions").is_some_and(Value::is_array) {
        object.insert("actions".into(), Value::Array(normalized_actions.clone()));
    }
    let resolved_default = if !default_script.is_empty() {
        default_script
    } else {
        let scripts: HashSet<String> = normalized_actions
            .iter()
            .filter_map(|action| action.get("script").and_then(Value::as_str))
            .map(str::to_string)
            .collect();
        if scripts.len() == 1 {
            scripts.into_iter().next().unwrap_or_default()
        } else {
            String::new()
        }
    };
    object.insert("script".into(), json!(resolved_default));

    let fields = raw
        .get("fields")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let url_param = raw
        .get("urlParam")
        .and_then(Value::as_str)
        .filter(|value| value.starts_with("--"))
        .map(str::to_string)
        .unwrap_or_else(|| legacy_url_param(&fields));
    let output_param = raw
        .get("outputParam")
        .and_then(Value::as_str)
        .filter(|value| value.starts_with("--"))
        .map(str::to_string)
        .unwrap_or_else(|| legacy_output_param(&fields));
    let no_url = raw
        .get("noUrl")
        .and_then(Value::as_bool)
        .unwrap_or(url_param.is_empty());
    object.insert("urlParam".into(), json!(url_param));
    object.insert("outputParam".into(), json!(output_param));
    object.insert("noUrl".into(), json!(no_url));
    Ok(provider)
}

pub fn resolve_script(
    script_name: &str,
    paths: &AppPaths,
    manager: &PluginManager,
) -> Result<ResolvedScript, String> {
    if let Some(rest) = script_name.strip_prefix("plugin:") {
        let (plugin_id, relative) = rest
            .split_once(':')
            .ok_or_else(|| format!("不允许执行的插件脚本：{script_name}"))?;
        let (path, plugin_root, plugin) = manager.resolve_script(plugin_id, relative)?;
        let manifest = plugin.get("manifest").cloned().unwrap_or_else(|| json!({}));
        return Ok(ResolvedScript {
            path,
            plugin: Some(ResolvedPluginContext {
                plugin_id: plugin_id.to_string(),
                plugin_version: plugin
                    .get("currentVersion")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string(),
                plugin_root,
                plugin_data_dir: paths.user_data.join("plugin-data").join(plugin_id),
                permissions: string_array(manifest.get("permissions")),
            }),
        });
    }
    if let Some(rest) = script_name.strip_prefix("bundled-plugin:") {
        let (plugin_id, relative) = rest
            .split_once(':')
            .ok_or_else(|| format!("不允许执行的内置插件脚本：{script_name}"))?;
        if !valid_script_scope_id(plugin_id) {
            return Err(format!("内置插件 ID 不合法：{plugin_id}"));
        }
        let (path, verified) =
            verify_bundled_plugin_file(&paths.bundled_plugins, plugin_id, relative)?;
        let manifest = verified.manifest;
        let plugin_root = verified.root;
        if !string_array(manifest.get("permissions"))
            .iter()
            .any(|permission| permission == "process")
        {
            return Err(format!("内置插件没有声明运行进程权限：{plugin_id}"));
        }
        if !has_extension(&path, "py") {
            return Err(format!("内置插件脚本不存在或类型不允许：{relative}"));
        }
        return Ok(ResolvedScript {
            path,
            plugin: Some(ResolvedPluginContext {
                plugin_id: plugin_id.to_string(),
                plugin_version: manifest["version"].as_str().unwrap_or_default().to_string(),
                plugin_root,
                plugin_data_dir: paths.user_data.join("plugin-data").join(plugin_id),
                permissions: string_array(manifest.get("permissions")),
            }),
        });
    }
    if let Some(rest) = script_name.strip_prefix("provider:") {
        let (provider_id, relative) = rest
            .split_once(':')
            .ok_or_else(|| format!("不允许执行的脚本：{script_name}"))?;
        if !valid_script_scope_id(provider_id) {
            return Err(format!("Provider ID 不合法：{provider_id}"));
        }
        let safe = safe_relative_path(relative, "Provider 脚本")?;
        for root in provider_roots(paths) {
            let provider_root = normalize_absolute(&root.join(provider_id));
            if !is_inside(&root, &provider_root) || !provider_root.is_dir() {
                continue;
            }
            let path = normalize_absolute(&provider_root.join(&safe));
            if is_inside(&provider_root, &path) && path.is_file() && has_extension(&path, "py") {
                return Ok(ResolvedScript { path, plugin: None });
            }
        }
        return Err(format!("无法找到插件脚本：{script_name}"));
    }
    Err(format!(
        "平台脚本必须来自 Plugin v1 或文件型 Provider：{script_name}"
    ))
}

pub fn read_guide_image_data_url(
    provider_root: &Path,
    relative_path: &str,
) -> Result<String, String> {
    if relative_path.trim().is_empty()
        || Path::new(relative_path).is_absolute()
        || relative_path.split_once(':').is_some_and(|(scheme, _)| {
            !scheme.is_empty()
                && scheme
                    .chars()
                    .all(|character| character.is_ascii_alphanumeric() || "+.-".contains(character))
        })
    {
        return Err("教程图片路径必须是 Provider 内的相对路径".to_string());
    }
    let target = normalize_absolute(&provider_root.join(relative_path));
    if !is_inside(provider_root, &target) {
        return Err("教程图片路径不能超出 Provider 目录".to_string());
    }
    let mime = match target
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or_default()
        .to_ascii_lowercase()
        .as_str()
    {
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "gif" => "image/gif",
        "webp" => "image/webp",
        _ => return Err("不支持的教程图片格式".to_string()),
    };
    let metadata = fs::metadata(&target).map_err(|_| "教程图片不存在".to_string())?;
    if !metadata.is_file() {
        return Err("教程图片必须是文件".to_string());
    }
    if metadata.len() > 5 * 1024 * 1024 {
        return Err("教程图片大小超过限制".to_string());
    }
    let bytes = fs::read(target).map_err(|error| error.to_string())?;
    Ok(format!("data:{mime};base64,{}", BASE64.encode(bytes)))
}

pub fn migrate_legacy_plugin_state(plugin_id: &str, legacy_roots: &[PathBuf], data_root: &Path) {
    let files: &[&str] = match plugin_id {
        "aliyun_thoughts" => &[".aliyun_thoughts_auth.json"],
        "feishu" => &[
            ".feishu_auth.json",
            "feishu_import_config.json",
            ".feishu_import_config.json",
        ],
        "ima" => &["ima_config.json"],
        "yinxiang" => &[
            "yinxiang/yinxiang_china.db",
            "yinxiang/yinxiang_china.db-shm",
            "yinxiang/yinxiang_china.db-wal",
        ],
        "youdao" => &[".youdao_auth.json"],
        "yuque" => &[".yuque_auth.json", ".yuque_import_config.json"],
        "wiz" => &[".wiz_auth.json"],
        "zsxq" => &[".zsxq_auth.json"],
        _ => &[],
    };
    for legacy_root in legacy_roots {
        for relative in files {
            let source = normalize_absolute(&legacy_root.join(relative));
            let target = normalize_absolute(&data_root.join(relative));
            if source == *legacy_root
                || target == data_root
                || !is_inside(legacy_root, &source)
                || !is_inside(data_root, &target)
                || !source.is_file()
                || target.exists()
            {
                continue;
            }
            if let Some(parent) = target.parent() {
                let _ = fs::create_dir_all(parent);
            }
            let _ = fs::copy(&source, &target);
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                let _ = fs::set_permissions(&target, fs::Permissions::from_mode(0o600));
            }
        }
    }
}

pub fn provider_roots(paths: &AppPaths) -> Vec<PathBuf> {
    let candidates = [
        paths.bundled_providers.clone(),
        paths.project_root.join("providers"),
        paths.user_data.join("providers"),
    ];
    let mut output = Vec::new();
    for path in candidates {
        let normalized = normalize_absolute(&path);
        if !output.iter().any(|existing: &PathBuf| {
            if cfg!(target_os = "windows") {
                existing
                    .to_string_lossy()
                    .eq_ignore_ascii_case(&normalized.to_string_lossy())
            } else {
                existing == &normalized
            }
        }) {
            output.push(normalized);
        }
    }
    output
}

fn validate_provider_manifest(raw: &Value, provider_root: &Path) -> Result<(), String> {
    let raw = raw
        .as_object()
        .ok_or_else(|| "provider.json 根节点必须是对象".to_string())?;
    if raw.get("schemaVersion").and_then(Value::as_u64) != Some(1) {
        return Err("只支持 schemaVersion=1".to_string());
    }
    let id = raw.get("id").and_then(Value::as_str).unwrap_or_default();
    if !valid_provider_id(id) {
        return Err(format!("Provider ID 不合法：{id}"));
    }
    if provider_root.file_name().and_then(|value| value.to_str()) != Some(id) {
        return Err(format!(
            "Provider 目录名必须和 ID 一致：{} != {id}",
            provider_root
                .file_name()
                .and_then(|value| value.to_str())
                .unwrap_or_default()
        ));
    }
    for key in [
        "name",
        "title",
        "description",
        "type",
        "group",
        "trustLevel",
        "status",
    ] {
        if raw
            .get(key)
            .and_then(Value::as_str)
            .is_none_or(|value| value.trim().is_empty())
        {
            return Err(format!("缺少必填字段：{key}"));
        }
    }
    validate_enum(raw, "type", PROVIDER_TYPES)?;
    validate_enum(raw, "group", PROVIDER_GROUPS)?;
    validate_enum(raw, "trustLevel", PROVIDER_TRUST)?;
    validate_enum(raw, "status", PROVIDER_STATUSES)?;
    let capabilities = raw
        .get("capabilities")
        .and_then(Value::as_object)
        .ok_or_else(|| "capabilities 必须是对象".to_string())?;
    for (key, value) in capabilities {
        if !value.is_boolean() {
            return Err(format!("capabilities.{key} 必须是布尔值"));
        }
    }
    if capabilities.get("retryFailures").and_then(Value::as_bool) == Some(true)
        && raw
            .get("retryFailures")
            .and_then(Value::as_object)
            .and_then(|retry| retry.get("arg"))
            .and_then(Value::as_str)
            .is_none_or(|value| !value.starts_with("--"))
    {
        return Err("capabilities.retryFailures=true 时必须声明 retryFailures.arg".to_string());
    }
    let provider_type = raw.get("type").and_then(Value::as_str).unwrap_or_default();
    if matches!(provider_type, "guide" | "hybrid") {
        let guide = raw
            .get("guide")
            .or_else(|| raw.get("guidePath"))
            .and_then(Value::as_str)
            .unwrap_or_default();
        let guide_path = normalize_absolute(&provider_root.join(guide));
        if guide.is_empty() || !is_inside(provider_root, &guide_path) || !guide_path.is_file() {
            return Err(format!("guide 文件不存在或路径越界：{guide}"));
        }
    }
    if let Some(guide_assets) = raw.get("guideAssets") {
        let guide_assets = guide_assets
            .as_object()
            .filter(|assets| !assets.is_empty())
            .ok_or_else(|| "guideAssets 必须是非空对象".to_string())?;
        for (reference, spec) in guide_assets {
            let remote = url::Url::parse(reference)
                .map_err(|_| format!("guideAssets URL 无效：{reference}"))?;
            if !is_allowed_remote_guide_image_url(id, &remote) {
                return Err(format!(
                    "guideAssets URL 不在允许的不可变仓库范围：{reference}"
                ));
            }
            parse_guide_asset_spec(spec)?;
        }
    }
    let fields = optional_array(raw, "fields", "fields 必须是数组")?;
    let mut field_names = HashSet::new();
    for (index, field) in fields.iter().enumerate() {
        let field = field
            .as_object()
            .ok_or_else(|| format!("fields[{index}] 必须是对象"))?;
        let name = field
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or_default();
        if !valid_provider_name(name) {
            return Err(format!("fields[{index}].name 不合法"));
        }
        if !field_names.insert(name) {
            return Err(format!("字段名重复：{name}"));
        }
        let field_type = field.get("type").and_then(Value::as_str).unwrap_or("text");
        if !FIELD_TYPES.contains(&field_type) {
            return Err(format!("fields[{index}].type 不支持：{field_type}"));
        }
    }
    let actions = optional_array(raw, "actions", "actions 必须是数组")?;
    if provider_type != "guide" && actions.is_empty() {
        return Err("非教程型 Provider 至少需要一个 action".to_string());
    }
    let mut action_ids = HashSet::new();
    for (index, action) in actions.iter().enumerate() {
        let action = action
            .as_object()
            .ok_or_else(|| format!("actions[{index}] 必须是对象"))?;
        let id = action.get("id").and_then(Value::as_str).unwrap_or_default();
        if !valid_provider_name(id) {
            return Err(format!("actions[{index}].id 不合法"));
        }
        if !action_ids.insert(id) {
            return Err(format!("动作 ID 重复：{id}"));
        }
        if action
            .get("label")
            .and_then(Value::as_str)
            .is_none_or(|value| value.trim().is_empty())
        {
            return Err(format!("actions[{index}].label 不能为空"));
        }
        if let Some(kind) = action.get("kind").and_then(Value::as_str) {
            if !ACTION_KINDS.contains(&kind) {
                return Err(format!("actions[{index}].kind 不支持：{kind}"));
            }
        }
        if action.get("args").is_some_and(|args| {
            args.as_array()
                .is_none_or(|args| args.iter().any(|arg| !arg.is_string()))
        }) {
            return Err(format!("actions[{index}].args 必须是字符串数组"));
        }
        if action.get("script").or_else(|| raw.get("script")).is_none() {
            return Err(format!("actions[{index}].script 不能为空"));
        }
    }
    if provider_type != "guide"
        && capabilities.get("scanToc").and_then(Value::as_bool) == Some(true)
        && !actions.iter().any(|action| {
            action.get("kind").and_then(Value::as_str) == Some("scan")
                || action.get("id").and_then(Value::as_str) == Some("scan")
        })
    {
        return Err("capabilities.scanToc=true 时必须提供 scan action".to_string());
    }
    Ok(())
}

fn plugin_script_reference(
    provider_id: &str,
    script_name: &str,
    provider_root: &Path,
    plugin: Option<&PluginProviderEntry>,
) -> Result<String, String> {
    if let Some(plugin) = plugin {
        if script_name.is_empty()
            || script_name.starts_with("plugin:")
            || script_name.starts_with("bundled-plugin:")
        {
            return Ok(script_name.to_string());
        }
        let resolved = normalize_absolute(&provider_root.join(script_name));
        if !is_inside(&plugin.plugin_root, &resolved)
            || !resolved.is_file()
            || !has_extension(&resolved, "py")
        {
            return Err(format!("Provider 脚本不存在或越界：{script_name}"));
        }
        let relative = pathdiff::diff_paths(&resolved, &plugin.plugin_root)
            .ok_or_else(|| "无法计算插件脚本路径".to_string())?
            .to_string_lossy()
            .replace('\\', "/");
        return Ok(format!(
            "{}:{}:{}",
            if plugin.bundled {
                "bundled-plugin"
            } else {
                "plugin"
            },
            plugin.plugin_id,
            relative
        ));
    }
    if script_name.is_empty() || script_name.starts_with("provider:") {
        return Ok(script_name.to_string());
    }
    let resolved = normalize_absolute(&provider_root.join(script_name));
    if !is_inside(provider_root, &resolved)
        || !resolved.is_file()
        || !has_extension(&resolved, "py")
    {
        return Err(format!("Provider 脚本不存在或越界：{script_name}"));
    }
    Ok(format!(
        "provider:{provider_id}:{}",
        script_name.replace('\\', "/")
    ))
}

fn read_guide_markdown(provider_root: &Path, relative: &str) -> String {
    let target = normalize_absolute(&provider_root.join(relative));
    if !is_inside(provider_root, &target) || !target.is_file() {
        return String::new();
    }
    if fs::metadata(&target).is_ok_and(|metadata| metadata.len() > 512 * 1024) {
        return String::new();
    }
    fs::read_to_string(target).unwrap_or_default()
}

fn legacy_url_param(fields: &[Value]) -> String {
    first_field_arg(fields, |field| {
        let field_type = field
            .get("type")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_ascii_lowercase();
        if !field_type.is_empty() && !matches!(field_type.as_str(), "text" | "url") {
            return false;
        }
        let name = field
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_ascii_lowercase();
        let arg = field
            .get("arg")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_ascii_lowercase();
        name.split(['_', '-'])
            .any(|segment| matches!(segment, "url" | "link"))
            || arg
                .trim_start_matches("--")
                .split('-')
                .any(|segment| segment == "url")
    })
}

fn legacy_output_param(fields: &[Value]) -> String {
    first_field_arg(fields, |field| {
        matches!(
            field
                .get("name")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_ascii_lowercase()
                .as_str(),
            "output" | "output_dir"
        ) || field.get("arg").and_then(Value::as_str) == Some("--output")
    })
}

fn first_field_arg(fields: &[Value], predicate: impl Fn(&Value) -> bool) -> String {
    let matches: Vec<&str> = fields
        .iter()
        .filter(|field| predicate(field))
        .filter_map(|field| field.get("arg").and_then(Value::as_str))
        .filter(|arg| arg.starts_with("--"))
        .collect();
    if matches.len() == 1 {
        matches[0].to_string()
    } else {
        String::new()
    }
}

fn valid_provider_name(value: &str) -> bool {
    (1..=64).contains(&value.len())
        && value
            .chars()
            .next()
            .is_some_and(|character| character.is_ascii_alphabetic())
        && value
            .chars()
            .all(|character| character.is_ascii_alphanumeric() || matches!(character, '_' | '-'))
}

fn valid_provider_id(value: &str) -> bool {
    (2..=64).contains(&value.len())
        && value
            .chars()
            .next()
            .is_some_and(|character| character.is_ascii_alphanumeric())
        && value
            .chars()
            .all(|character| character.is_ascii_alphanumeric() || matches!(character, '_' | '-'))
}

fn optional_array<'a>(
    object: &'a Map<String, Value>,
    key: &str,
    error: &str,
) -> Result<&'a [Value], String> {
    match object.get(key) {
        None | Some(Value::Null) => Ok(&[]),
        Some(Value::Array(values)) => Ok(values),
        Some(_) => Err(error.to_string()),
    }
}

fn has_extension(path: &Path, expected: &str) -> bool {
    path.extension()
        .and_then(|value| value.to_str())
        .is_some_and(|value| value.eq_ignore_ascii_case(expected))
}

fn valid_script_scope_id(value: &str) -> bool {
    (1..=64).contains(&value.len())
        && value
            .chars()
            .all(|character| character.is_ascii_alphanumeric() || matches!(character, '_' | '-'))
}

fn validate_enum(object: &Map<String, Value>, key: &str, allowed: &[&str]) -> Result<(), String> {
    let value = object.get(key).and_then(Value::as_str).unwrap_or_default();
    if allowed.contains(&value) {
        Ok(())
    } else {
        Err(format!("{key} 不支持：{value}"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn copy_release_resources(source: &Path, target: &Path) {
        fs::create_dir_all(target).unwrap();
        for entry in fs::read_dir(source)
            .unwrap()
            .collect::<Result<Vec<_>, _>>()
            .unwrap()
        {
            let path = entry.path();
            let destination = target.join(entry.file_name());
            let name = entry.file_name().to_string_lossy().to_string();
            if path.is_dir() {
                if !name.eq_ignore_ascii_case("__pycache__") {
                    copy_release_resources(&path, &destination);
                }
            } else {
                let lowercase = name.to_ascii_lowercase();
                if !lowercase.ends_with(".pyc") && !lowercase.ends_with(".pyo") {
                    fs::copy(path, destination).unwrap();
                }
            }
        }
    }

    fn minimal_provider(id: &str, provider_type: &str) -> Value {
        json!({
            "schemaVersion": 1,
            "id": id,
            "name": "Test provider",
            "title": "Test provider",
            "description": "Provider contract fixture",
            "type": provider_type,
            "group": if provider_type == "guide" { "guide" } else { "export" },
            "trustLevel": "local",
            "status": "stable",
            "capabilities": {}
        })
    }

    #[test]
    fn provider_v1_optional_arrays_and_numeric_ids_remain_compatible() {
        let temporary =
            std::env::temp_dir().join(format!("wandao-provider-contract-{}", uuid::Uuid::new_v4()));
        let root = temporary.join("2guide");
        fs::create_dir_all(&root).unwrap();
        fs::write(root.join("README.md"), "# Guide\n").unwrap();
        let mut manifest = minimal_provider("2guide", "guide");
        manifest["guide"] = json!("README.md");

        let provider = normalize_provider_manifest(&manifest, &root, "user", None).unwrap();
        assert_eq!(provider["id"], "2guide");
        assert!(provider.get("fields").is_none());
        assert!(provider.get("actions").is_none());

        let _ = fs::remove_dir_all(temporary);
    }

    #[test]
    fn remote_guide_images_require_the_pinned_feishu_repository_path() {
        let raw = url::Url::parse(
            "https://raw.githubusercontent.com/tllovesxs/wandao/\
             82c027b054d9ece8449af30d79600814eb823e46/\
             plugins/feishu/providers/feishu-import/images/1.png",
        )
        .unwrap();
        assert!(is_allowed_remote_guide_image_url("feishu-import", &raw));
        assert!(!is_allowed_remote_guide_image_url("other-provider", &raw));
        assert!(!is_allowed_remote_guide_image_url(
            "feishu-import",
            &url::Url::parse(raw.as_str().replace("/1.png", "/21.png").as_str()).unwrap()
        ));
        assert!(!is_allowed_remote_guide_image_url(
            "feishu-import",
            &url::Url::parse(raw.as_str().replace("https://", "http://").as_str()).unwrap()
        ));
        assert!(!is_allowed_remote_guide_image_url(
            "feishu-import",
            &url::Url::parse(
                raw.as_str()
                    .replace("82c027b054d9ece8449af30d79600814eb823e46", "main")
                    .as_str()
            )
            .unwrap()
        ));
    }

    #[test]
    fn remote_guide_asset_metadata_is_read_from_the_verified_provider_manifest() {
        let temporary =
            std::env::temp_dir().join(format!("wandao-guide-asset-{}", uuid::Uuid::new_v4()));
        fs::create_dir_all(&temporary).unwrap();
        let reference = "https://raw.githubusercontent.com/tllovesxs/wandao/\
                         82c027b054d9ece8449af30d79600814eb823e46/\
                         plugins/feishu/providers/feishu-import/images/1.png";
        fs::write(
            temporary.join("provider.json"),
            serde_json::to_vec(&json!({
                "guideAssets": {
                    reference: {
                        "mime": "image/png",
                        "bytes": 1981436,
                        "sha256": "16a3d8a2aa18b108ff1c3bd76eae114bd9cda639d447f5de0af91d10dc6d1ae2"
                    }
                }
            }))
            .unwrap(),
        )
        .unwrap();

        let spec = remote_guide_asset_spec(&temporary, reference)
            .unwrap()
            .unwrap();
        assert_eq!(spec.mime, "image/png");
        assert_eq!(spec.bytes, 1981436);
        assert_eq!(
            spec.sha256,
            "16a3d8a2aa18b108ff1c3bd76eae114bd9cda639d447f5de0af91d10dc6d1ae2"
        );
        assert!(
            remote_guide_asset_spec(&temporary, "https://example.test/image.png")
                .unwrap()
                .is_none()
        );
        let _ = fs::remove_dir_all(temporary);
    }

    #[test]
    fn provider_v1_rejects_present_non_array_collections() {
        let root = PathBuf::from("demo-provider");
        let mut manifest = minimal_provider("demo-provider", "automation");
        manifest["fields"] = json!({});
        assert!(validate_provider_manifest(&manifest, &root)
            .unwrap_err()
            .contains("fields 必须是数组"));

        manifest.as_object_mut().unwrap().remove("fields");
        manifest["actions"] = json!("invalid");
        assert!(validate_provider_manifest(&manifest, &root)
            .unwrap_err()
            .contains("actions 必须是数组"));
    }

    #[test]
    fn provider_scripts_keep_case_insensitive_extension_compatibility() {
        let temporary = std::env::temp_dir().join(format!(
            "wandao-provider-extension-{}",
            uuid::Uuid::new_v4()
        ));
        let root = temporary.join("demo-provider");
        fs::create_dir_all(&root).unwrap();
        fs::write(root.join("EXPORT.PY"), "print('{}')\n").unwrap();
        let mut manifest = minimal_provider("demo-provider", "automation");
        manifest["actions"] = json!([{
            "id": "export",
            "label": "Export",
            "script": "EXPORT.PY"
        }]);

        let provider = normalize_provider_manifest(&manifest, &root, "user", None).unwrap();
        assert_eq!(
            provider
                .pointer("/actions/0/script")
                .and_then(Value::as_str),
            Some("provider:demo-provider:EXPORT.PY")
        );

        let _ = fs::remove_dir_all(temporary);
    }

    #[test]
    fn legacy_fields_project_url_and_output_parameters() {
        let fields = vec![
            json!({"name": "wiki_url", "type": "text", "arg": "--wiki-url"}),
            json!({"name": "output", "type": "directory", "arg": "--output"}),
        ];
        assert_eq!(legacy_url_param(&fields), "--wiki-url");
        assert_eq!(legacy_output_param(&fields), "--output");
    }

    #[test]
    fn ambiguous_urls_do_not_guess_a_legacy_parameter() {
        let fields = vec![
            json!({"name": "source_url", "arg": "--source-url"}),
            json!({"name": "target_url", "arg": "--target-url"}),
        ];
        assert_eq!(legacy_url_param(&fields), "");
    }

    #[test]
    fn repository_discovers_every_bundled_plugin_v1_provider() {
        let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        let repository = normalize_absolute(&manifest_dir.join("..").join(".."));
        let temporary = std::env::temp_dir().join(format!(
            "wandao-tauri-provider-test-{}",
            uuid::Uuid::new_v4()
        ));
        let bundled_plugins = temporary.join("bundled-plugins");
        copy_release_resources(&repository.join("plugins"), &bundled_plugins);
        let paths = AppPaths {
            app_dir: manifest_dir.clone(),
            user_data: temporary.clone(),
            project_root: repository.clone(),
            bundled_plugins,
            bundled_providers: repository.join("providers"),
            bundled_python_runtime: manifest_dir.join("runtime").join("python-runtime"),
            assets: repository.join("wandao_electron").join("assets"),
        };
        let manager = PluginManager::new(&paths, "1.4.0").unwrap();
        let discovery = discover_provider_manifests(&paths, &manager);

        assert!(
            discovery.errors.is_empty(),
            "provider discovery errors: {:?}",
            discovery.errors
        );
        let expected_provider_count = match platform_id() {
            "win32" => 20,
            "darwin" => 19,
            "linux" => 18,
            platform => panic!("unsupported test platform: {platform}"),
        };
        assert_eq!(discovery.providers.len(), expected_provider_count);
        assert_eq!(
            discovery
                .providers
                .iter()
                .filter(|provider| provider["sourceKind"] == "bundled-plugin")
                .count(),
            expected_provider_count
        );
        let executable: Vec<&Value> = discovery
            .providers
            .iter()
            .filter(|provider| {
                provider["actions"]
                    .as_array()
                    .is_some_and(|actions| !actions.is_empty())
            })
            .collect();
        assert_eq!(executable.len(), expected_provider_count - 1);
        assert!(executable.iter().all(|provider| provider["script"]
            .as_str()
            .is_some_and(|script| script.starts_with("bundled-plugin:"))));
        assert_eq!(
            discovery
                .providers
                .iter()
                .any(|provider| provider["id"] == "onenote"),
            platform_id() == "win32"
        );
        assert_eq!(
            discovery
                .providers
                .iter()
                .any(|provider| provider["id"] == "dingtalk-export"),
            platform_id() != "linux"
        );
        let notion = discovery
            .providers
            .iter()
            .find(|provider| provider["id"] == "notion")
            .unwrap();
        assert_eq!(notion["script"], "");

        assert!(resolve_script("provider:..:export_yuque.py", &paths, &manager).is_err());
        assert!(resolve_script("bundled-plugin:..:export_yuque.py", &paths, &manager).is_err());
        assert!(
            resolve_script("provider:demo/../../outside:backend.py", &paths, &manager).is_err()
        );
        let _ = fs::remove_dir_all(temporary);
    }
}
