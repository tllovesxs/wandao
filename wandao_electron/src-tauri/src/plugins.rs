use std::{
    collections::{BTreeSet, HashMap},
    fs,
    path::{Component, Path, PathBuf},
    sync::{Arc, Mutex, MutexGuard},
};

use base64::{engine::general_purpose::STANDARD as BASE64, Engine};
use chrono::Utc;
use semver::Version;
use serde_json::{json, Map, Value};

use crate::{
    app_state::{is_inside, platform_id, AppPaths},
    security::{
        canonical_json, read_json, safe_relative_path, sha256_hex, verify_envelope_signature,
        write_json_atomic,
    },
};

const STATE_SCHEMA_VERSION: u64 = 1;
const PLUGIN_FORMAT_VERSION: u64 = 1;
const REGISTRY_FORMAT_VERSION: u64 = 1;
const MAX_PLUGIN_FILES: usize = 2_000;
const MAX_PLUGIN_BYTES: usize = 256 * 1024 * 1024;
const MAX_PLUGIN_PACKAGE_BYTES: u64 = 128 * 1024 * 1024;
const INSTALL_RECEIPT_NAME: &str = ".wandao-install.json";
const ALLOWED_PERMISSIONS: &[&str] = &[
    "browser-automation",
    "credentials",
    "filesystem:read",
    "filesystem:write",
    "network",
    "process",
];

include!(concat!(env!("OUT_DIR"), "/bundled_plugin_hashes.rs"));

#[derive(Debug)]
struct PluginTreeSnapshot {
    canonical_root: PathBuf,
    files: BTreeSet<String>,
    directories: BTreeSet<String>,
}

fn metadata_is_link_or_reparse(metadata: &fs::Metadata) -> bool {
    if metadata.file_type().is_symlink() {
        return true;
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;

        const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x400;
        if metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
            return true;
        }
    }
    false
}

fn path_is_plain_directory(path: &Path) -> bool {
    fs::symlink_metadata(path)
        .is_ok_and(|metadata| metadata.is_dir() && !metadata_is_link_or_reparse(&metadata))
}

fn validate_tree_node(path: &Path, metadata: &fs::Metadata, label: &str) -> Result<(), String> {
    if metadata_is_link_or_reparse(metadata) {
        return Err(format!(
            "{label}不得包含符号链接、目录联接或重解析点：{}",
            path.display()
        ));
    }
    if !metadata.is_dir() && !metadata.is_file() {
        return Err(format!("{label}不得包含特殊文件节点：{}", path.display()));
    }
    Ok(())
}

fn tree_relative_path(root: &Path, path: &Path, label: &str) -> Result<String, String> {
    let relative = path
        .strip_prefix(root)
        .map_err(|_| format!("{label}路径越界：{}", path.display()))?;
    let mut segments = Vec::new();
    for component in relative.components() {
        let Component::Normal(segment) = component else {
            return Err(format!("{label}路径无效：{}", path.display()));
        };
        segments.push(
            segment
                .to_str()
                .ok_or_else(|| format!("{label}路径不是 UTF-8：{}", path.display()))?,
        );
    }
    Ok(segments.join("/"))
}

fn scan_plugin_tree(
    trusted_base: &Path,
    root: &Path,
    label: &str,
) -> Result<PluginTreeSnapshot, String> {
    let relative_root = root
        .strip_prefix(trusted_base)
        .map_err(|_| format!("{label}根目录越界：{}", root.display()))?;
    let canonical_base = fs::canonicalize(trusted_base)
        .map_err(|error| format!("无法访问{label}基础目录：{error}"))?;
    if !canonical_base.is_dir() {
        return Err(format!(
            "{label}基础路径不是目录：{}",
            trusted_base.display()
        ));
    }

    // Check each managed component so a junction in the plugin-id directory
    // cannot hide behind an otherwise ordinary version directory.
    let mut current = trusted_base.to_path_buf();
    for component in relative_root.components() {
        let Component::Normal(segment) = component else {
            return Err(format!("{label}根目录无效：{}", root.display()));
        };
        current.push(segment);
        let metadata = fs::symlink_metadata(&current)
            .map_err(|error| format!("无法检查{label}路径 {}：{error}", current.display()))?;
        validate_tree_node(&current, &metadata, label)?;
        if !metadata.is_dir() {
            return Err(format!("{label}根路径不是目录：{}", current.display()));
        }
        let canonical = fs::canonicalize(&current)
            .map_err(|error| format!("无法解析{label}路径 {}：{error}", current.display()))?;
        if !is_inside(&canonical_base, &canonical) {
            return Err(format!("{label}根目录越界：{}", current.display()));
        }
    }

    let canonical_root = fs::canonicalize(root)
        .map_err(|error| format!("无法访问{label}根目录 {}：{error}", root.display()))?;
    if !is_inside(&canonical_base, &canonical_root) || !canonical_root.is_dir() {
        return Err(format!("{label}根目录越界：{}", root.display()));
    }

    let mut files = BTreeSet::new();
    let mut directories = BTreeSet::new();
    let mut pending = vec![root.to_path_buf()];
    while let Some(directory) = pending.pop() {
        let mut entries: Vec<_> = fs::read_dir(&directory)
            .map_err(|error| format!("无法读取{label}目录 {}：{error}", directory.display()))?
            .collect::<Result<_, _>>()
            .map_err(|error| format!("无法读取{label}目录 {}：{error}", directory.display()))?;
        entries.sort_by_key(|entry| entry.file_name());
        for entry in entries {
            let path = entry.path();
            let metadata = fs::symlink_metadata(&path)
                .map_err(|error| format!("无法检查{label}路径 {}：{error}", path.display()))?;
            validate_tree_node(&path, &metadata, label)?;
            let canonical = fs::canonicalize(&path)
                .map_err(|error| format!("无法解析{label}路径 {}：{error}", path.display()))?;
            if !is_inside(&canonical_root, &canonical) {
                return Err(format!("{label}路径越界：{}", path.display()));
            }
            let relative = tree_relative_path(root, &path, label)?;
            if metadata.is_dir() {
                directories.insert(relative);
                pending.push(path);
            } else {
                files.insert(relative);
            }
        }
    }
    Ok(PluginTreeSnapshot {
        canonical_root,
        files,
        directories,
    })
}

fn expected_tree_directories(files: &BTreeSet<String>) -> BTreeSet<String> {
    let mut directories = BTreeSet::new();
    for file in files {
        let segments: Vec<&str> = file.split('/').collect();
        for length in 1..segments.len() {
            directories.insert(segments[..length].join("/"));
        }
    }
    directories
}

fn verify_exact_plugin_tree(
    snapshot: &PluginTreeSnapshot,
    expected_files: &BTreeSet<String>,
    label: &str,
) -> Result<(), String> {
    if let Some(unexpected) = snapshot.files.difference(expected_files).next() {
        return Err(format!("{label}包含未签名的额外文件：{unexpected}"));
    }
    if let Some(missing) = expected_files.difference(&snapshot.files).next() {
        return Err(format!("{label}文件缺失：{missing}"));
    }
    let expected_directories = expected_tree_directories(expected_files);
    if let Some(unexpected) = snapshot
        .directories
        .difference(&expected_directories)
        .next()
    {
        return Err(format!("{label}包含额外目录：{unexpected}"));
    }
    if let Some(missing) = expected_directories
        .difference(&snapshot.directories)
        .next()
    {
        return Err(format!("{label}目录缺失：{missing}"));
    }
    Ok(())
}

fn is_python_bytecode_file(relative: &str) -> bool {
    let file_name = relative
        .rsplit('/')
        .next()
        .unwrap_or_default()
        .to_ascii_lowercase();
    file_name.ends_with(".pyc") || file_name.ends_with(".pyo")
}

fn is_python_cache_path(relative: &str) -> bool {
    is_python_bytecode_file(relative)
        || relative
            .split('/')
            .any(|segment| segment.eq_ignore_ascii_case("__pycache__"))
}

fn canonical_regular_file(root: &Path, path: &Path, label: &str) -> Result<PathBuf, String> {
    let metadata = fs::symlink_metadata(path)
        .map_err(|error| format!("无法检查{label} {}：{error}", path.display()))?;
    validate_tree_node(path, &metadata, label)?;
    if !metadata.is_file() {
        return Err(format!("{label}不是普通文件：{}", path.display()));
    }
    let canonical = fs::canonicalize(path)
        .map_err(|error| format!("无法解析{label} {}：{error}", path.display()))?;
    if !is_inside(root, &canonical) || !canonical.is_file() {
        return Err(format!("{label}路径越界：{}", path.display()));
    }
    Ok(canonical)
}

fn clean_installed_python_cache(
    trusted_base: &Path,
    root: &Path,
    label: &str,
) -> Result<PluginTreeSnapshot, String> {
    let snapshot = scan_plugin_tree(trusted_base, root, label)?;
    for relative in snapshot
        .files
        .iter()
        .filter(|relative| is_python_bytecode_file(relative))
    {
        let safe = safe_relative_path(relative, "Python 缓存文件")?;
        let path = root.join(safe);
        if !is_inside(root, &path) {
            return Err(format!("Python 缓存文件路径越界：{relative}"));
        }
        canonical_regular_file(&snapshot.canonical_root, &path, "Python 缓存文件")?;
        fs::remove_file(&path)
            .map_err(|error| format!("无法清理 Python 缓存文件 {}：{error}", path.display()))?;
    }

    let mut cache_directories: Vec<&String> = snapshot
        .directories
        .iter()
        .filter(|relative| {
            relative
                .rsplit('/')
                .next()
                .is_some_and(|name| name.eq_ignore_ascii_case("__pycache__"))
        })
        .collect();
    cache_directories.sort_by_key(|relative| std::cmp::Reverse(relative.matches('/').count()));
    for relative in cache_directories {
        let safe = safe_relative_path(relative, "Python 缓存目录")?;
        let path = root.join(safe);
        if !is_inside(root, &path) {
            return Err(format!("Python 缓存目录路径越界：{relative}"));
        }
        let metadata = fs::symlink_metadata(&path)
            .map_err(|error| format!("无法检查 Python 缓存目录 {}：{error}", path.display()))?;
        validate_tree_node(&path, &metadata, "Python 缓存目录")?;
        if !metadata.is_dir() {
            return Err(format!("Python 缓存路径不是目录：{}", path.display()));
        }
        let canonical = fs::canonicalize(&path)
            .map_err(|error| format!("无法解析 Python 缓存目录 {}：{error}", path.display()))?;
        if !is_inside(&snapshot.canonical_root, &canonical) {
            return Err(format!("Python 缓存目录路径越界：{relative}"));
        }
        match fs::remove_dir(&path) {
            Ok(()) => {}
            Err(error) if error.kind() == std::io::ErrorKind::DirectoryNotEmpty => {}
            Err(error) => {
                return Err(format!(
                    "无法清理 Python 缓存目录 {}：{error}",
                    path.display()
                ));
            }
        }
    }
    scan_plugin_tree(trusted_base, root, label)
}

#[derive(Debug, Clone)]
pub struct PluginProviderEntry {
    pub plugin_id: String,
    pub plugin_version: String,
    pub plugin_root: PathBuf,
    pub manifest_path: PathBuf,
    pub permissions: Vec<String>,
    pub ui_entry: String,
    pub verified: bool,
    pub bundled: bool,
}

#[derive(Debug, Default)]
pub struct PluginDiscovery {
    pub entries: Vec<PluginProviderEntry>,
    pub errors: Vec<String>,
}

#[derive(Debug)]
pub struct VerifiedBundledPlugin {
    pub manifest: Value,
    pub root: PathBuf,
    expected_hashes: HashMap<String, String>,
}

pub fn verify_bundled_plugin(
    bundled_plugins: &Path,
    plugin_id: &str,
) -> Result<VerifiedBundledPlugin, String> {
    validate_plugin_id(plugin_id)?;
    let expected: Vec<(&str, &str)> = BUNDLED_PLUGIN_HASHES
        .iter()
        .filter(|(id, _, _)| *id == plugin_id)
        .map(|(_, relative, hash)| (*relative, *hash))
        .collect();
    if expected.is_empty() {
        return Err(format!("内置插件不在构建完整性清单中：{plugin_id}"));
    }

    let lexical_root = bundled_plugins.join(plugin_id);
    let snapshot = scan_plugin_tree(bundled_plugins, &lexical_root, "内置插件")?;
    let expected_files: BTreeSet<String> = expected
        .iter()
        .map(|(relative, _)| (*relative).to_string())
        .collect();
    verify_exact_plugin_tree(&snapshot, &expected_files, "内置插件")?;
    let root = snapshot.canonical_root;

    let mut manifest_bytes = None;
    let mut expected_hashes = HashMap::new();
    for (relative, expected_hash) in expected {
        let safe = safe_relative_path(relative, "内置插件文件")?;
        let lexical = root.join(safe);
        let absolute = canonical_regular_file(&root, &lexical, "内置插件文件")
            .map_err(|_| format!("内置插件文件缺失或不安全：{plugin_id}/{relative}"))?;
        let content =
            fs::read(&absolute).map_err(|error| format!("读取内置插件文件失败：{error}"))?;
        if sha256_hex(&content) != expected_hash {
            return Err(format!(
                "内置插件文件在构建后被修改：{plugin_id}/{relative}"
            ));
        }
        if relative == "plugin.json" {
            manifest_bytes = Some(content);
        }
        expected_hashes.insert(relative.to_string(), expected_hash.to_string());
    }

    let manifest: Value = serde_json::from_slice(
        manifest_bytes
            .as_deref()
            .ok_or_else(|| format!("内置插件清单缺少 plugin.json：{plugin_id}"))?,
    )
    .map_err(|error| format!("内置插件 plugin.json 无效：{plugin_id}：{error}"))?;
    validate_plugin_manifest(&manifest)?;
    if manifest.get("id").and_then(Value::as_str) != Some(plugin_id) {
        return Err(format!("内置插件目录名必须等于插件 ID：{plugin_id}"));
    }
    Ok(VerifiedBundledPlugin {
        manifest,
        root,
        expected_hashes,
    })
}

pub fn verify_bundled_plugin_file(
    bundled_plugins: &Path,
    plugin_id: &str,
    relative_path: &str,
) -> Result<(PathBuf, VerifiedBundledPlugin), String> {
    let verified = verify_bundled_plugin(bundled_plugins, plugin_id)?;
    let safe = safe_relative_path(relative_path, "内置插件文件")?;
    let expected_hash = verified
        .expected_hashes
        .get(relative_path)
        .ok_or_else(|| format!("文件不在内置插件构建清单中：{plugin_id}/{relative_path}"))?;
    let absolute =
        canonical_regular_file(&verified.root, &verified.root.join(safe), "内置插件文件")?;
    let content = fs::read(&absolute).map_err(|error| format!("读取内置插件文件失败：{error}"))?;
    if sha256_hex(content) != *expected_hash {
        return Err(format!(
            "内置插件文件在构建后被修改：{plugin_id}/{relative_path}"
        ));
    }
    Ok((absolute, verified))
}

#[derive(Debug)]
struct VerifiedPackage {
    manifest: Value,
    files: Vec<(String, Vec<u8>)>,
    signer: Value,
    integrity: String,
}

#[derive(Clone)]
pub struct PluginManager {
    plugins_dir: PathBuf,
    state_file: PathBuf,
    trust_store: Value,
    core_version: String,
    platform: String,
    operation_lock: Arc<Mutex<()>>,
}

impl PluginManager {
    pub fn new(paths: &AppPaths, core_version: impl Into<String>) -> Result<Self, String> {
        let root_dir = paths.user_data.join("plugins");
        let plugins_dir = root_dir.join("installed");
        fs::create_dir_all(&plugins_dir).map_err(|error| format!("无法创建插件目录：{error}"))?;
        let trust_store = read_json(&paths.assets.join("plugin-trust.json"))?;
        let manager = Self {
            plugins_dir,
            state_file: root_dir.join("state.json"),
            trust_store,
            core_version: core_version.into(),
            platform: platform_id().to_string(),
            operation_lock: Arc::new(Mutex::new(())),
        };
        manager.recover_operation_directories()?;
        Ok(manager)
    }

    fn default_state(&self) -> Value {
        json!({
            "schemaVersion": STATE_SCHEMA_VERSION,
            "plugins": {},
            "updatedAt": Utc::now().to_rfc3339()
        })
    }

    fn read_state(&self) -> Result<Value, String> {
        let content = match fs::read_to_string(&self.state_file) {
            Ok(content) => content,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                return Ok(self.default_state());
            }
            Err(error) => {
                return Err(format!(
                    "读取插件状态 {} 失败：{error}",
                    self.state_file.display()
                ));
            }
        };
        let state: Value = serde_json::from_str(&content).map_err(|error| {
            format!("插件状态文件 {} 已损坏：{error}", self.state_file.display())
        })?;
        if state.get("schemaVersion").and_then(Value::as_u64) != Some(STATE_SCHEMA_VERSION)
            || !state.get("plugins").is_some_and(Value::is_object)
        {
            return Err(format!(
                "插件状态文件 {} 的结构或版本无效",
                self.state_file.display()
            ));
        }
        Ok(state)
    }

    fn write_state(&self, mut state: Value) -> Result<(), String> {
        let object = state
            .as_object_mut()
            .ok_or_else(|| "插件状态必须是对象".to_string())?;
        object.insert("schemaVersion".into(), json!(STATE_SCHEMA_VERSION));
        object.insert("updatedAt".into(), json!(Utc::now().to_rfc3339()));
        write_json_atomic(&self.state_file, &state)
    }

    fn plugin_root(&self, plugin_id: &str) -> Result<PathBuf, String> {
        validate_plugin_id(plugin_id)?;
        let root = self.plugins_dir.join(plugin_id);
        if !is_inside(&self.plugins_dir, &root) {
            return Err("插件路径越界".to_string());
        }
        Ok(root)
    }

    fn version_root(&self, plugin_id: &str, version: &str) -> Result<PathBuf, String> {
        validate_version(version)?;
        Ok(self.plugin_root(plugin_id)?.join(version))
    }

    fn recover_operation_directories(&self) -> Result<(), String> {
        let Ok(plugin_dirs) = fs::read_dir(&self.plugins_dir) else {
            return Ok(());
        };
        let plugin_dirs: Vec<_> = plugin_dirs.flatten().collect();
        for plugin_dir in plugin_dirs
            .iter()
            .filter(|entry| path_is_plain_directory(&entry.path()))
        {
            let plugin_id = plugin_dir.file_name().to_string_lossy().to_string();
            if validate_plugin_id(&plugin_id).is_err() {
                continue;
            }
            let Ok(entries) = fs::read_dir(plugin_dir.path()) else {
                continue;
            };
            for entry in entries
                .flatten()
                .filter(|entry| path_is_plain_directory(&entry.path()))
            {
                let name = entry.file_name().to_string_lossy().to_string();
                if name.starts_with(".staging-") {
                    let _ = fs::remove_dir_all(entry.path());
                    continue;
                }
                let Some(version) = replace_tombstone_version(&name) else {
                    continue;
                };
                let Ok(target) = self.version_root(&plugin_id, &version) else {
                    continue;
                };
                if target.exists() {
                    if self.verify_installed_version(&plugin_id, &version).is_ok() {
                        fs::remove_dir_all(entry.path()).map_err(|error| {
                            format!(
                                "无法清理已提交插件版本的恢复目录 {}：{error}",
                                entry.path().display()
                            )
                        })?;
                    } else {
                        fs::remove_dir_all(&target).map_err(|error| {
                            format!(
                                "无法移除校验失败的插件版本目录 {}：{error}；旧版本仍保留在 {}",
                                target.display(),
                                entry.path().display()
                            )
                        })?;
                        fs::rename(entry.path(), &target).map_err(|error| {
                            format!(
                                "无法从 {} 恢复插件版本目录 {}：{error}",
                                entry.path().display(),
                                target.display()
                            )
                        })?;
                        self.verify_installed_version(&plugin_id, &version)
                            .map_err(|error| format!("恢复的插件版本校验失败：{error}"))?;
                    }
                } else {
                    fs::rename(entry.path(), &target).map_err(|error| {
                        format!(
                            "无法从 {} 恢复插件版本目录 {}：{error}",
                            entry.path().display(),
                            target.display()
                        )
                    })?;
                    self.verify_installed_version(&plugin_id, &version)
                        .map_err(|error| format!("恢复的插件版本校验失败：{error}"))?;
                }
            }
        }

        // A process may stop after the plugin directory was moved out of the
        // way but before state.json was committed. Only decide whether to
        // restore/delete tombstones when the state itself is trustworthy.
        let Ok(state) = self.read_state() else {
            return Ok(());
        };
        for entry in plugin_dirs
            .into_iter()
            .filter(|entry| path_is_plain_directory(&entry.path()))
        {
            let name = entry.file_name().to_string_lossy().to_string();
            let Some(plugin_id) = uninstall_tombstone_plugin_id(&name) else {
                continue;
            };
            let Ok(root) = self.plugin_root(&plugin_id) else {
                continue;
            };
            let still_installed = state
                .get("plugins")
                .and_then(Value::as_object)
                .is_some_and(|plugins| plugins.contains_key(&plugin_id));
            if still_installed && !root.exists() {
                fs::rename(entry.path(), &root).map_err(|error| {
                    format!(
                        "无法恢复待卸载插件目录 {} 到 {}：{error}",
                        entry.path().display(),
                        root.display()
                    )
                })?;
            } else {
                fs::remove_dir_all(entry.path()).map_err(|error| {
                    format!(
                        "无法清理插件卸载恢复目录 {}：{error}",
                        entry.path().display()
                    )
                })?;
            }
        }
        Ok(())
    }

    pub fn compatibility(&self, manifest: &Value) -> Value {
        if let Some(minimum) = manifest
            .pointer("/core/minVersion")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
        {
            if compare_versions(&self.core_version, minimum) < 0 {
                return json!({
                    "compatible": false,
                    "reason": format!("需要万能导 {minimum} 或更高版本")
                });
            }
        }
        if let Some(platforms) = manifest.get("platforms").and_then(Value::as_array) {
            if !platforms.is_empty()
                && !platforms
                    .iter()
                    .any(|platform| platform.as_str() == Some(&self.platform))
            {
                return json!({
                    "compatible": false,
                    "reason": format!("插件不支持当前系统 {}", self.platform)
                });
            }
        }
        json!({"compatible": true, "reason": ""})
    }

    pub fn verify_registry(&self, registry: &Value) -> Result<(), String> {
        if registry.get("formatVersion").and_then(Value::as_u64) != Some(REGISTRY_FORMAT_VERSION)
            || !registry.get("plugins").is_some_and(Value::is_array)
        {
            return Err("插件注册表格式无效".to_string());
        }
        verify_envelope_signature(registry, &self.trust_store)?;
        let mut seen = BTreeSet::new();
        for plugin in registry
            .get("plugins")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
        {
            let id = plugin.get("id").and_then(Value::as_str).unwrap_or_default();
            validate_plugin_id(id)?;
            if !seen.insert(id.to_string()) {
                return Err(format!("注册表插件 ID 重复：{id}"));
            }
            validate_version(
                plugin
                    .get("version")
                    .and_then(Value::as_str)
                    .unwrap_or_default(),
            )?;
            for field in ["name", "description", "publisher"] {
                if plugin
                    .get(field)
                    .and_then(Value::as_str)
                    .is_none_or(|value| value.trim().is_empty())
                {
                    return Err(format!("注册表插件 {id} 缺少字段：{field}"));
                }
            }
            validate_optional_enum_array(
                plugin.get("permissions"),
                ALLOWED_PERMISSIONS,
                &format!("注册表插件 {id} 声明了不支持的权限"),
            )?;
            validate_optional_enum_array(
                plugin.get("platforms"),
                &["win32", "darwin", "linux"],
                &format!("注册表插件 {id} 的 platforms 包含不支持的系统"),
            )?;
            if let Some(minimum) = plugin
                .get("minCoreVersion")
                .filter(|value| !value.is_null())
            {
                let minimum = minimum
                    .as_str()
                    .ok_or_else(|| format!("注册表插件 {id} 的 minCoreVersion 无效"))?;
                if !minimum.is_empty() {
                    validate_version(minimum)
                        .map_err(|_| format!("注册表插件 {id} 的 minCoreVersion 无效"))?;
                }
            }
            if plugin
                .get("channel")
                .and_then(Value::as_str)
                .is_some_and(|channel| !matches!(channel, "stable" | "experimental"))
            {
                return Err(format!("插件发布等级无效：{id}"));
            }
            let package_url = plugin
                .get("packageUrl")
                .and_then(Value::as_str)
                .unwrap_or_default();
            let parsed =
                url::Url::parse(package_url).map_err(|_| format!("插件下载地址无效：{id}"))?;
            let local_http = parsed.scheme() == "http"
                && matches!(parsed.host_str(), Some("127.0.0.1" | "localhost"));
            if parsed.scheme() != "https"
                && !(local_http && std::env::var_os("WANDAO_PLUGIN_ALLOW_LOCAL_HTTP").is_some())
            {
                return Err(format!("插件下载地址不安全：{id}"));
            }
            let sha = plugin
                .get("sha256")
                .and_then(Value::as_str)
                .unwrap_or_default();
            if sha.len() != 64 || !sha.chars().all(|character| character.is_ascii_hexdigit()) {
                return Err(format!("插件缺少 SHA-256：{id}"));
            }
        }
        Ok(())
    }

    pub fn install_bytes(&self, bytes: &[u8], source: Value) -> Result<Value, String> {
        self.install_bytes_with_state_commit(bytes, source, |state| self.write_state(state))
    }

    fn install_bytes_with_state_commit(
        &self,
        bytes: &[u8],
        source: Value,
        commit_state: impl FnOnce(Value) -> Result<(), String>,
    ) -> Result<Value, String> {
        let _operation = self.lock_operations();
        let envelope: Value = serde_json::from_slice(bytes)
            .map_err(|error| format!("插件包不是有效 JSON：{error}"))?;
        let verified = self.verify_package(&envelope)?;
        if self
            .compatibility(&verified.manifest)
            .get("compatible")
            .and_then(Value::as_bool)
            != Some(true)
        {
            return Err(self
                .compatibility(&verified.manifest)
                .get("reason")
                .and_then(Value::as_str)
                .unwrap_or("插件与当前系统不兼容")
                .to_string());
        }

        if let Some(registry_entry) = source.get("registryEntry") {
            let identity_matches = ["id", "version"]
                .iter()
                .all(|key| registry_entry.get(*key) == verified.manifest.get(*key));
            if !identity_matches {
                return Err("插件包身份与注册表不一致".to_string());
            }
        }

        let plugin_id = verified.manifest["id"].as_str().unwrap_or_default();
        let version = verified.manifest["version"].as_str().unwrap_or_default();
        // Refuse to touch an installation while its authoritative state is
        // unreadable. In particular, a malformed state file must never be
        // replaced with a newly synthesized empty state.
        let mut state = self.read_state()?;
        let plugin_dir = self.plugin_root(plugin_id)?;
        fs::create_dir_all(&plugin_dir).map_err(|error| error.to_string())?;
        let target = self.version_root(plugin_id, version)?;
        let target_is_valid = target.exists()
            && self.installed_package_matches(
                plugin_id,
                version,
                &verified.integrity,
                envelope.get("signature").unwrap_or(&Value::Null),
            );

        let plugins = state
            .get_mut("plugins")
            .and_then(Value::as_object_mut)
            .ok_or_else(|| "插件状态损坏".to_string())?;
        let previous = plugins.get(plugin_id).cloned().unwrap_or_else(|| json!({}));
        let mut previous_versions: Vec<String> = previous
            .get("previousVersions")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .map(str::to_string)
            .collect();
        if let Some(current) = previous.get("currentVersion").and_then(Value::as_str) {
            if current != version {
                previous_versions.push(current.to_string());
            }
        }
        previous_versions.retain(|candidate| {
            self.version_root(plugin_id, candidate)
                .is_ok_and(|path| path.is_dir())
        });
        deduplicate_versions(&mut previous_versions);
        if previous_versions.len() > 3 {
            previous_versions.drain(..previous_versions.len() - 3);
        }
        plugins.insert(
            plugin_id.to_string(),
            json!({
                "id": plugin_id,
                "enabled": true,
                "currentVersion": version,
                "previousVersions": previous_versions,
                "channel": source.pointer("/registryEntry/channel")
                    .and_then(Value::as_str)
                    .or_else(|| previous.get("channel").and_then(Value::as_str))
                    .unwrap_or("local"),
                "installedAt": previous.get("installedAt").and_then(Value::as_str)
                    .map(str::to_string).unwrap_or_else(|| Utc::now().to_rfc3339()),
                "updatedAt": Utc::now().to_rfc3339()
            }),
        );
        let commit_state = || commit_state(state);

        if !target_is_valid {
            let staging = plugin_dir.join(format!(
                ".staging-{version}-{}-{}",
                std::process::id(),
                Utc::now().timestamp_millis()
            ));
            fs::create_dir_all(&staging).map_err(|error| error.to_string())?;
            let install_result = (|| {
                for (relative, content) in &verified.files {
                    let safe = safe_relative_path(relative, "插件文件")?;
                    let output = staging.join(safe);
                    if !is_inside(&staging, &output) {
                        return Err(format!("插件文件路径越界：{relative}"));
                    }
                    if let Some(parent) = output.parent() {
                        fs::create_dir_all(parent).map_err(|error| error.to_string())?;
                    }
                    fs::write(&output, content).map_err(|error| error.to_string())?;
                }
                write_json_atomic(&staging.join("plugin.json"), &verified.manifest)?;
                let file_paths: Vec<&str> = verified
                    .files
                    .iter()
                    .map(|(path, _)| path.as_str())
                    .collect();
                write_json_atomic(
                    &staging.join(INSTALL_RECEIPT_NAME),
                    &json!({
                        "installedAt": Utc::now().to_rfc3339(),
                        "integrity": verified.integrity,
                        "signer": verified.signer,
                        "signature": envelope.get("signature").cloned().unwrap_or(Value::Null),
                        "filePaths": file_paths,
                        "source": source
                    }),
                )?;
                self.verify_installed_root(&staging, plugin_id, version)?;
                self.commit_staged_version(plugin_id, version, &staging, || {
                    self.verify_installed_version(plugin_id, version)?;
                    commit_state()
                })
            })();
            if install_result.is_err() && staging.exists() {
                let _ = fs::remove_dir_all(&staging);
            }
            install_result?;
        } else {
            self.verify_installed_version(plugin_id, version)?;
            commit_state()?;
        }

        self.describe_installed(plugin_id)?
            .ok_or_else(|| "插件安装后状态丢失".to_string())
    }

    pub fn install_file(&self, path: &Path) -> Result<Value, String> {
        if !path.is_file() {
            return Err("插件包文件不存在".to_string());
        }
        let size = fs::metadata(path).map_err(|error| error.to_string())?.len();
        if size > MAX_PLUGIN_PACKAGE_BYTES {
            return Err("插件包超过 128 MiB 大小限制".to_string());
        }
        let bytes = fs::read(path).map_err(|error| error.to_string())?;
        self.install_bytes(
            &bytes,
            json!({"sourceFile": path.to_string_lossy().to_string()}),
        )
    }

    pub fn describe_installed(&self, plugin_id: &str) -> Result<Option<Value>, String> {
        let state = self.read_state()?;
        let Some(plugin_state) = state.pointer(&format!("/plugins/{plugin_id}")).cloned() else {
            return Ok(None);
        };
        let version = plugin_state
            .get("currentVersion")
            .and_then(Value::as_str)
            .ok_or_else(|| format!("插件状态缺少版本：{plugin_id}"))?;
        let manifest_path = self.version_root(plugin_id, version)?.join("plugin.json");
        let Ok(manifest) = read_json(&manifest_path) else {
            return Ok(None);
        };
        let mut output = plugin_state;
        let object = output
            .as_object_mut()
            .ok_or_else(|| "插件状态必须是对象".to_string())?;
        object.insert("manifest".into(), manifest.clone());
        object.insert("compatibility".into(), self.compatibility(&manifest));
        Ok(Some(output))
    }

    pub fn list_installed(&self) -> Result<Vec<Value>, String> {
        let state = self.read_state()?;
        let mut ids: Vec<String> = state
            .get("plugins")
            .and_then(Value::as_object)
            .into_iter()
            .flat_map(|plugins| plugins.keys().cloned())
            .collect();
        ids.sort();
        let mut installed = Vec::new();
        for id in ids {
            if let Some(plugin) = self.describe_installed(&id)? {
                installed.push(plugin);
            }
        }
        Ok(installed)
    }

    pub fn list_with_registry(&self, registry: Option<&Value>) -> Result<Vec<Value>, String> {
        let mut installed: HashMap<String, Value> = self
            .list_installed()?
            .into_iter()
            .filter_map(|plugin| {
                let id = plugin
                    .get("id")
                    .and_then(Value::as_str)
                    .map(str::to_string)?;
                Some((id, plugin))
            })
            .collect();
        let mut output = Vec::new();
        for remote in registry
            .and_then(|value| value.get("plugins"))
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
        {
            let id = remote.get("id").and_then(Value::as_str).unwrap_or_default();
            let local = installed.remove(id);
            let mut entry = remote.clone();
            let object = entry.as_object_mut().expect("registry entries are objects");
            object.insert("installed".into(), json!(local.is_some()));
            object.insert(
                "enabled".into(),
                json!(local
                    .as_ref()
                    .and_then(|value| value.get("enabled"))
                    .and_then(Value::as_bool)
                    .unwrap_or(false)),
            );
            let installed_version = local
                .as_ref()
                .and_then(|value| value.get("currentVersion"))
                .and_then(Value::as_str)
                .unwrap_or_default();
            object.insert("installedVersion".into(), json!(installed_version));
            object.insert(
                "updateAvailable".into(),
                json!(
                    local.is_some()
                        && compare_versions(
                            remote
                                .get("version")
                                .and_then(Value::as_str)
                                .unwrap_or_default(),
                            installed_version
                        ) > 0
                ),
            );
            object.insert(
                "previousVersions".into(),
                local
                    .as_ref()
                    .and_then(|value| value.get("previousVersions"))
                    .cloned()
                    .unwrap_or_else(|| json!([])),
            );
            let compatibility_manifest = json!({
                "core": {"minVersion": remote.get("minCoreVersion").cloned().unwrap_or(Value::Null)},
                "platforms": remote.get("platforms").cloned().unwrap_or(Value::Null)
            });
            object.insert(
                "compatibility".into(),
                self.compatibility(&compatibility_manifest),
            );
            output.push(entry);
        }
        for (_, local) in installed {
            let manifest = local.get("manifest").cloned().unwrap_or_else(|| json!({}));
            output.push(json!({
                "id": local.get("id").cloned().unwrap_or(Value::Null),
                "name": manifest.get("name").cloned().unwrap_or(Value::Null),
                "description": manifest.get("description").cloned().unwrap_or(Value::Null),
                "publisher": manifest.get("publisher").cloned().unwrap_or(Value::Null),
                "version": local.get("currentVersion").cloned().unwrap_or(Value::Null),
                "permissions": manifest.get("permissions").cloned().unwrap_or_else(|| json!([])),
                "installed": true,
                "enabled": local.get("enabled").cloned().unwrap_or(Value::Bool(false)),
                "installedVersion": local.get("currentVersion").cloned().unwrap_or(Value::Null),
                "updateAvailable": false,
                "previousVersions": local.get("previousVersions").cloned().unwrap_or_else(|| json!([])),
                "channel": local.get("channel").cloned().unwrap_or(json!("local")),
                "compatibility": local.get("compatibility").cloned().unwrap_or(json!({"compatible": false, "reason": "插件状态无效"})),
                "unavailableFromRegistry": true
            }));
        }
        output.sort_by(|left, right| {
            display_name(left)
                .to_lowercase()
                .cmp(&display_name(right).to_lowercase())
        });
        Ok(output)
    }

    pub fn set_enabled(&self, plugin_id: &str, enabled: bool) -> Result<Value, String> {
        let _operation = self.lock_operations();
        let mut state = self.read_state()?;
        let plugin = state
            .pointer_mut(&format!("/plugins/{plugin_id}"))
            .and_then(Value::as_object_mut)
            .ok_or_else(|| format!("插件尚未安装：{plugin_id}"))?;
        plugin.insert("enabled".into(), json!(enabled));
        plugin.insert("updatedAt".into(), json!(Utc::now().to_rfc3339()));
        self.write_state(state)?;
        self.describe_installed(plugin_id)?
            .ok_or_else(|| format!("插件尚未安装：{plugin_id}"))
    }

    pub fn rollback(&self, plugin_id: &str) -> Result<Value, String> {
        let _operation = self.lock_operations();
        let mut state = self.read_state()?;
        let plugin = state
            .pointer_mut(&format!("/plugins/{plugin_id}"))
            .and_then(Value::as_object_mut)
            .ok_or_else(|| format!("插件尚未安装：{plugin_id}"))?;
        let current = plugin
            .get("currentVersion")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let mut candidates: Vec<String> = plugin
            .get("previousVersions")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .map(str::to_string)
            .filter(|version| self.verify_installed_version(plugin_id, version).is_ok())
            .collect();
        let target = candidates
            .pop()
            .ok_or_else(|| "没有可回滚的插件版本".to_string())?;
        self.verify_installed_version(plugin_id, &target)?;
        if self.verify_installed_version(plugin_id, &current).is_ok() {
            candidates.retain(|version| version != &current);
            candidates.push(current);
        }
        deduplicate_versions(&mut candidates);
        if candidates.len() > 3 {
            candidates.drain(..candidates.len() - 3);
        }
        plugin.insert("currentVersion".into(), json!(target));
        plugin.insert("previousVersions".into(), json!(candidates));
        plugin.insert("enabled".into(), json!(true));
        plugin.insert("updatedAt".into(), json!(Utc::now().to_rfc3339()));
        self.write_state(state)?;
        self.describe_installed(plugin_id)?
            .ok_or_else(|| format!("插件尚未安装：{plugin_id}"))
    }

    pub fn uninstall(&self, plugin_id: &str) -> Result<bool, String> {
        let _operation = self.lock_operations();
        self.uninstall_with_commit(plugin_id, |state| self.write_state(state))
    }

    fn commit_staged_version(
        &self,
        plugin_id: &str,
        version: &str,
        staging: &Path,
        verify_final: impl FnOnce() -> Result<(), String>,
    ) -> Result<(), String> {
        let target = self.version_root(plugin_id, version)?;
        let replaced = if target.exists() {
            let tombstone = self.replace_tombstone_path(plugin_id, version)?;
            fs::rename(&target, &tombstone)
                .map_err(|error| format!("无法暂存旧插件版本目录 {}：{error}", target.display()))?;
            Some(tombstone)
        } else {
            None
        };

        if let Err(error) = fs::rename(staging, &target) {
            if let Some(tombstone) = replaced.as_ref() {
                if let Err(restore_error) = fs::rename(tombstone, &target) {
                    return Err(format!(
                        "无法提交插件目录 {}：{error}；恢复旧版本失败：{restore_error}",
                        target.display()
                    ));
                }
            }
            return Err(format!("无法提交插件目录 {}：{error}", target.display()));
        }

        if let Err(error) = verify_final() {
            let cleanup_error = fs::remove_dir_all(&target).err();
            let restore_error = replaced.as_ref().and_then(|tombstone| {
                if target.exists() {
                    Some("新版本目录无法移除，旧版本仍保留在恢复目录".to_string())
                } else {
                    fs::rename(tombstone, &target)
                        .err()
                        .map(|restore| format!("恢复旧版本失败：{restore}"))
                }
            });
            let recovery_error = restore_error.or_else(|| {
                target.exists().then(|| {
                    cleanup_error
                        .map(|cleanup| format!("移除校验失败的新版本失败：{cleanup}"))
                        .unwrap_or_else(|| "校验失败的新版本目录仍然存在".to_string())
                })
            });
            return Err(match recovery_error {
                Some(recovery) => format!("插件最终校验失败：{error}；{recovery}"),
                None => format!("插件最终校验失败：{error}"),
            });
        }

        if let Some(tombstone) = replaced {
            let _ = fs::remove_dir_all(tombstone);
        }
        Ok(())
    }

    fn uninstall_with_commit(
        &self,
        plugin_id: &str,
        commit: impl FnOnce(Value) -> Result<(), String>,
    ) -> Result<bool, String> {
        let mut state = self.read_state()?;
        let plugins = state
            .get_mut("plugins")
            .and_then(Value::as_object_mut)
            .ok_or_else(|| "插件状态损坏".to_string())?;
        if !plugins.contains_key(plugin_id) {
            return Ok(false);
        }
        let root = self.plugin_root(plugin_id)?;
        if !is_inside(&self.plugins_dir, &root) {
            return Err("拒绝删除越界插件目录".to_string());
        }
        let tombstone = root
            .exists()
            .then(|| self.uninstall_tombstone_path(plugin_id));
        if let Some(tombstone) = tombstone.as_ref() {
            fs::rename(&root, tombstone)
                .map_err(|error| format!("无法暂存待卸载插件目录 {}：{error}", root.display()))?;
        }
        plugins.remove(plugin_id);
        if let Err(error) = commit(state) {
            if let Some(tombstone) = tombstone.as_ref() {
                if let Err(restore_error) = fs::rename(tombstone, &root) {
                    return Err(format!(
                        "{error}；恢复插件目录 {} 失败：{restore_error}",
                        root.display()
                    ));
                }
            }
            return Err(error);
        }
        if let Some(tombstone) = tombstone {
            let _ = fs::remove_dir_all(tombstone);
        }
        Ok(true)
    }

    fn uninstall_tombstone_path(&self, plugin_id: &str) -> PathBuf {
        self.plugins_dir.join(format!(
            ".uninstall-{}-{}",
            hex::encode(plugin_id.as_bytes()),
            uuid::Uuid::new_v4()
        ))
    }

    fn replace_tombstone_path(&self, plugin_id: &str, version: &str) -> Result<PathBuf, String> {
        validate_version(version)?;
        Ok(self.plugin_root(plugin_id)?.join(format!(
            ".replace-{}-{}",
            hex::encode(version.as_bytes()),
            uuid::Uuid::new_v4()
        )))
    }

    fn lock_operations(&self) -> MutexGuard<'_, ()> {
        self.operation_lock
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    pub fn provider_entries_with_errors(&self) -> PluginDiscovery {
        let mut discovery = PluginDiscovery::default();
        let installed = match self.list_installed() {
            Ok(installed) => installed,
            Err(error) => {
                discovery.errors.push(error);
                return discovery;
            }
        };
        for plugin in installed {
            if plugin.get("enabled").and_then(Value::as_bool) != Some(true)
                || plugin
                    .pointer("/compatibility/compatible")
                    .and_then(Value::as_bool)
                    != Some(true)
            {
                continue;
            }
            let plugin_id = plugin.get("id").and_then(Value::as_str).unwrap_or_default();
            let version = plugin
                .get("currentVersion")
                .and_then(Value::as_str)
                .unwrap_or_default();
            let verified = match self.verify_installed_version(plugin_id, version) {
                Ok(value) => value,
                Err(error) => {
                    discovery
                        .errors
                        .push(format!("{plugin_id}@{version}：{error}"));
                    continue;
                }
            };
            let manifest = verified.manifest;
            let root = verified.root;
            let permissions = string_array(manifest.get("permissions"));
            let ui_entry = manifest
                .pointer("/entrypoints/ui")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string();
            for provider in manifest
                .pointer("/entrypoints/providers")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
            {
                let Ok(relative) = safe_relative_path(provider, "Provider 入口") else {
                    continue;
                };
                let manifest_path = root.join(relative);
                if is_inside(&root, &manifest_path) && manifest_path.is_file() {
                    discovery.entries.push(PluginProviderEntry {
                        plugin_id: plugin_id.to_string(),
                        plugin_version: version.to_string(),
                        plugin_root: root.clone(),
                        manifest_path,
                        permissions: permissions.clone(),
                        ui_entry: ui_entry.clone(),
                        verified: true,
                        bundled: false,
                    });
                }
            }
        }
        discovery
    }

    pub fn resolve_script(
        &self,
        plugin_id: &str,
        relative_path: &str,
    ) -> Result<(PathBuf, PathBuf, Value), String> {
        let plugin = self
            .describe_installed(plugin_id)?
            .ok_or_else(|| format!("插件未启用：{plugin_id}"))?;
        if plugin.get("enabled").and_then(Value::as_bool) != Some(true)
            || plugin
                .pointer("/compatibility/compatible")
                .and_then(Value::as_bool)
                != Some(true)
        {
            return Err(format!("插件未启用：{plugin_id}"));
        }
        let manifest = plugin.get("manifest").cloned().unwrap_or_else(|| json!({}));
        if !string_array(manifest.get("permissions"))
            .iter()
            .any(|permission| permission == "process")
        {
            return Err(format!("插件没有声明运行进程权限：{plugin_id}"));
        }
        let version = plugin["currentVersion"].as_str().unwrap_or_default();
        let target = self.verify_installed_file(plugin_id, version, relative_path)?;
        if !has_extension(&target, "py") {
            return Err(format!("插件脚本不存在或类型不允许：{relative_path}"));
        }
        Ok((target, self.version_root(plugin_id, version)?, plugin))
    }

    pub fn read_ui(&self, plugin_id: &str, relative_path: &str) -> Result<String, String> {
        let plugin = self
            .describe_installed(plugin_id)?
            .ok_or_else(|| format!("插件未启用：{plugin_id}"))?;
        if plugin.get("enabled").and_then(Value::as_bool) != Some(true) {
            return Err(format!("插件未启用：{plugin_id}"));
        }
        let version = plugin["currentVersion"].as_str().unwrap_or_default();
        let target = self.verify_installed_file(plugin_id, version, relative_path)?;
        if !has_extension(&target, "html") {
            return Err("插件自定义 UI 文件无效".to_string());
        }
        let metadata = fs::metadata(&target).map_err(|error| error.to_string())?;
        if metadata.len() > 2 * 1024 * 1024 {
            return Err("插件自定义 UI 超过 2 MB 限制".to_string());
        }
        fs::read_to_string(target).map_err(|error| error.to_string())
    }

    fn verify_package(&self, envelope: &Value) -> Result<VerifiedPackage, String> {
        if envelope.get("formatVersion").and_then(Value::as_u64) != Some(PLUGIN_FORMAT_VERSION) {
            return Err("不支持的插件包格式".to_string());
        }
        let manifest = envelope
            .get("manifest")
            .cloned()
            .ok_or_else(|| "插件包缺少 manifest".to_string())?;
        validate_plugin_manifest(&manifest)?;
        let files = envelope
            .get("files")
            .and_then(Value::as_object)
            .ok_or_else(|| "插件 files 必须是对象".to_string())?;
        let body = json!({
            "formatVersion": PLUGIN_FORMAT_VERSION,
            "manifest": manifest,
            "files": Value::Object(files.clone())
        });
        let expected = sha256_hex(canonical_json(&body)?.as_bytes());
        if envelope
            .pointer("/integrity/algorithm")
            .and_then(Value::as_str)
            != Some("sha256")
            || envelope.pointer("/integrity/value").and_then(Value::as_str) != Some(&expected)
        {
            return Err("插件完整性校验失败".to_string());
        }
        let signer = verify_envelope_signature(envelope, &self.trust_store)?;
        if files.is_empty() || files.len() > MAX_PLUGIN_FILES {
            return Err("插件文件数量不合法".to_string());
        }
        let mut decoded = Vec::new();
        let mut total_bytes = 0_usize;
        let mut normalized_paths = BTreeSet::new();
        let mut sorted_files: Vec<(&String, &Value)> = files.iter().collect();
        sorted_files.sort_by(|(left, _), (right, _)| left.cmp(right));
        for (path, encoded) in sorted_files {
            safe_relative_path(path, "插件文件")?;
            if is_python_cache_path(path) {
                return Err(format!("插件包不得包含 Python 缓存文件或目录：{path}"));
            }
            let normalized = path.to_lowercase();
            if !normalized_paths.insert(normalized) {
                return Err(format!("插件文件路径在当前系统会发生冲突：{path}"));
            }
            let root_name = path
                .split('/')
                .next()
                .unwrap_or_default()
                .to_ascii_lowercase();
            if root_name == "plugin.json" || root_name.starts_with(".wandao-") {
                return Err(format!("插件包不得写入管理文件：{path}"));
            }
            let encoded = encoded
                .as_str()
                .ok_or_else(|| format!("插件文件内容不是 Base64：{path}"))?;
            let content = BASE64
                .decode(encoded)
                .map_err(|_| format!("插件文件内容不是 Base64：{path}"))?;
            total_bytes = total_bytes.saturating_add(content.len());
            if total_bytes > MAX_PLUGIN_BYTES {
                return Err("插件解包后超过 256 MB 限制".to_string());
            }
            decoded.push((path.clone(), content));
        }
        for provider in manifest
            .pointer("/entrypoints/providers")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
        {
            if !decoded.iter().any(|(path, _)| path == provider) {
                return Err(format!("Provider 入口不存在：{provider}"));
            }
        }
        if let Some(ui) = manifest.pointer("/entrypoints/ui").and_then(Value::as_str) {
            if !decoded.iter().any(|(path, _)| path == ui) {
                return Err(format!("自定义 UI 入口不存在：{ui}"));
            }
        }
        Ok(VerifiedPackage {
            manifest,
            files: decoded,
            signer,
            integrity: expected,
        })
    }

    fn verify_installed_version(
        &self,
        plugin_id: &str,
        version: &str,
    ) -> Result<VerifiedInstalled, String> {
        let root = self.version_root(plugin_id, version)?;
        self.verify_installed_root(&root, plugin_id, version)
    }

    fn installed_package_matches(
        &self,
        plugin_id: &str,
        version: &str,
        integrity: &str,
        signature: &Value,
    ) -> bool {
        let Ok(root) = self.version_root(plugin_id, version) else {
            return false;
        };
        if self
            .verify_installed_root(&root, plugin_id, version)
            .is_err()
        {
            return false;
        }
        read_json(&root.join(INSTALL_RECEIPT_NAME)).is_ok_and(|receipt| {
            receipt.get("integrity").and_then(Value::as_str) == Some(integrity)
                && receipt.get("signature") == Some(signature)
        })
    }

    fn verify_installed_root(
        &self,
        root: &Path,
        plugin_id: &str,
        version: &str,
    ) -> Result<VerifiedInstalled, String> {
        let label = format!("已安装插件 {plugin_id}@{version}");
        let snapshot = clean_installed_python_cache(&self.plugins_dir, root, &label)?;
        let canonical_root = &snapshot.canonical_root;
        let manifest = read_json(&canonical_root.join("plugin.json"))?;
        let receipt = read_json(&canonical_root.join(INSTALL_RECEIPT_NAME))?;
        if manifest.get("id").and_then(Value::as_str) != Some(plugin_id)
            || manifest.get("version").and_then(Value::as_str) != Some(version)
            || !receipt.get("signature").is_some_and(Value::is_object)
            || !receipt.get("filePaths").is_some_and(Value::is_array)
        {
            return Err(format!("插件安装记录无效：{plugin_id}@{version}"));
        }
        let mut files = Map::new();
        let mut hashes = HashMap::new();
        let mut expected_files =
            BTreeSet::from(["plugin.json".to_string(), INSTALL_RECEIPT_NAME.to_string()]);
        for relative in receipt
            .get("filePaths")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
        {
            let relative = relative
                .as_str()
                .ok_or_else(|| format!("插件安装记录无效：{plugin_id}@{version}"))?;
            let safe = safe_relative_path(relative, "已安装插件文件")?;
            if !expected_files.insert(relative.to_string()) {
                return Err(format!("插件安装记录包含重复文件：{plugin_id}/{relative}"));
            }
            let absolute = canonical_root.join(&safe);
            let canonical = canonical_regular_file(canonical_root, &absolute, "已安装插件文件")
                .map_err(|_| format!("插件文件缺失或不安全：{plugin_id}/{relative}"))?;
            let content = fs::read(&canonical).map_err(|error| error.to_string())?;
            hashes.insert(relative.to_string(), sha256_hex(&content));
            files.insert(relative.to_string(), json!(BASE64.encode(content)));
        }
        verify_exact_plugin_tree(&snapshot, &expected_files, &label)?;
        let envelope = json!({
            "formatVersion": PLUGIN_FORMAT_VERSION,
            "manifest": manifest,
            "files": files,
            "integrity": {
                "algorithm": "sha256",
                "value": receipt.get("integrity").cloned().unwrap_or(Value::Null)
            },
            "signature": receipt.get("signature").cloned().unwrap_or(Value::Null)
        });
        let verified = self.verify_package(&envelope)?;
        Ok(VerifiedInstalled {
            manifest: verified.manifest,
            root: canonical_root.clone(),
            hashes,
        })
    }

    fn verify_installed_file(
        &self,
        plugin_id: &str,
        version: &str,
        relative_path: &str,
    ) -> Result<PathBuf, String> {
        let verified = self
            .verify_installed_version(plugin_id, version)
            .map_err(|error| {
                format!("插件文件在安装后被修改：{plugin_id}/{relative_path}（{error}）")
            })?;
        let safe = safe_relative_path(relative_path, "已安装插件文件")?;
        let expected = verified
            .hashes
            .get(relative_path)
            .ok_or_else(|| format!("文件不在已签名插件包中：{relative_path}"))?;
        let absolute =
            canonical_regular_file(&verified.root, &verified.root.join(safe), "已安装插件文件")?;
        if sha256_hex(fs::read(&absolute).map_err(|error| error.to_string())?) != *expected {
            return Err(format!(
                "插件文件在安装后被修改：{plugin_id}/{relative_path}"
            ));
        }
        Ok(absolute)
    }
}

struct VerifiedInstalled {
    manifest: Value,
    root: PathBuf,
    hashes: HashMap<String, String>,
}

pub fn validate_plugin_manifest(manifest: &Value) -> Result<(), String> {
    let manifest = manifest
        .as_object()
        .ok_or_else(|| "plugin.json 必须是对象".to_string())?;
    if manifest.get("schemaVersion").and_then(Value::as_u64) != Some(1) {
        return Err("只支持 plugin schemaVersion=1".to_string());
    }
    let id = manifest
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default();
    validate_plugin_id(id)?;
    validate_version(
        manifest
            .get("version")
            .and_then(Value::as_str)
            .unwrap_or_default(),
    )?;
    for field in ["name", "description", "publisher"] {
        if manifest
            .get(field)
            .and_then(Value::as_str)
            .is_none_or(|value| value.trim().is_empty())
        {
            return Err(format!("插件缺少字段：{field}"));
        }
    }
    let entrypoints = manifest
        .get("entrypoints")
        .and_then(Value::as_object)
        .ok_or_else(|| "插件缺少 entrypoints".to_string())?;
    let providers = entrypoints
        .get("providers")
        .and_then(Value::as_array)
        .ok_or_else(|| "插件至少需要声明一个 Provider 入口".to_string())?;
    if providers.is_empty() {
        return Err("插件至少需要声明一个 Provider 入口".to_string());
    }
    for provider in providers {
        safe_relative_path(
            provider
                .as_str()
                .ok_or_else(|| "Provider 入口必须是字符串".to_string())?,
            "Provider 入口",
        )?;
    }
    if let Some(ui) = entrypoints.get("ui").filter(|value| !value.is_null()) {
        let ui = ui
            .as_str()
            .ok_or_else(|| "自定义 UI 入口必须是字符串".to_string())?;
        safe_relative_path(ui, "自定义 UI 入口")?;
    }
    validate_optional_enum_array(
        manifest.get("permissions"),
        ALLOWED_PERMISSIONS,
        "插件声明了不支持的权限",
    )?;
    if let Some(version) = manifest
        .get("core")
        .and_then(Value::as_object)
        .and_then(|core| core.get("minVersion"))
        .filter(|value| !value.is_null())
    {
        let version = version
            .as_str()
            .ok_or_else(|| "core.minVersion 必须是语义化版本号".to_string())?;
        validate_version(version).map_err(|_| "core.minVersion 必须是语义化版本号".to_string())?;
    }
    validate_optional_enum_array(
        manifest.get("platforms"),
        &["win32", "darwin", "linux"],
        "platforms 包含不支持的系统",
    )?;
    Ok(())
}

fn validate_optional_enum_array(
    value: Option<&Value>,
    allowed: &[&str],
    error: &str,
) -> Result<(), String> {
    let values = match value {
        None | Some(Value::Null) => return Ok(()),
        Some(Value::Array(values)) => values,
        Some(_) => return Err(error.to_string()),
    };
    if values
        .iter()
        .any(|value| value.as_str().is_none_or(|value| !allowed.contains(&value)))
    {
        return Err(error.to_string());
    }
    Ok(())
}

fn has_extension(path: &Path, expected: &str) -> bool {
    path.extension()
        .and_then(|value| value.to_str())
        .is_some_and(|value| value.eq_ignore_ascii_case(expected))
}

pub fn compare_versions(left: &str, right: &str) -> i8 {
    let ordering = match (Version::parse(left), Version::parse(right)) {
        (Ok(left), Ok(right)) => left.cmp(&right),
        _ => left.cmp(right),
    };
    match ordering {
        std::cmp::Ordering::Less => -1,
        std::cmp::Ordering::Greater => 1,
        std::cmp::Ordering::Equal => 0,
    }
}

fn deduplicate_versions(versions: &mut Vec<String>) {
    let mut unique = Vec::with_capacity(versions.len());
    for version in versions.drain(..) {
        if !unique.contains(&version) {
            unique.push(version);
        }
    }
    *versions = unique;
}

fn uninstall_tombstone_plugin_id(name: &str) -> Option<String> {
    let encoded = name.strip_prefix(".uninstall-")?.split_once('-')?.0;
    let plugin_id = String::from_utf8(hex::decode(encoded).ok()?).ok()?;
    validate_plugin_id(&plugin_id).ok()?;
    Some(plugin_id)
}

fn replace_tombstone_version(name: &str) -> Option<String> {
    let encoded = name.strip_prefix(".replace-")?.split_once('-')?.0;
    let version = String::from_utf8(hex::decode(encoded).ok()?).ok()?;
    validate_version(&version).ok()?;
    Some(version)
}

fn validate_plugin_id(value: &str) -> Result<(), String> {
    let valid = (2..=64).contains(&value.len())
        && value
            .chars()
            .next()
            .is_some_and(|character| character.is_ascii_lowercase() || character.is_ascii_digit())
        && value.chars().all(|character| {
            character.is_ascii_lowercase()
                || character.is_ascii_digit()
                || matches!(character, '_' | '-')
        });
    if valid {
        Ok(())
    } else {
        Err(format!("插件 ID 不合法：{value}"))
    }
}

fn validate_version(value: &str) -> Result<(), String> {
    Version::parse(value)
        .map(|_| ())
        .map_err(|_| format!("插件版本不合法：{value}"))
}

pub fn string_array(value: Option<&Value>) -> Vec<String> {
    value
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::to_string)
        .collect()
}

fn display_name(value: &Value) -> String {
    value
        .get("name")
        .or_else(|| value.get("id"))
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string()
}

#[cfg(test)]
mod tests {
    use super::*;
    use ed25519_dalek::{pkcs8::EncodePublicKey, Signer, SigningKey};

    fn test_manager(temporary: &Path, signing_key: &SigningKey) -> PluginManager {
        let plugins_dir = temporary.join("installed");
        fs::create_dir_all(&plugins_dir).unwrap();
        let public_key = signing_key
            .verifying_key()
            .to_public_key_pem(Default::default())
            .unwrap();
        PluginManager {
            plugins_dir,
            state_file: temporary.join("state.json"),
            trust_store: json!({
                "schemaVersion": 1,
                "keys": [{
                    "id": "test-key",
                    "algorithm": "ed25519",
                    "publisher": "Test",
                    "publicKey": public_key
                }]
            }),
            core_version: "1.4.0".into(),
            platform: platform_id().into(),
            operation_lock: Arc::new(Mutex::new(())),
        }
    }

    fn test_manifest(version: &str) -> Value {
        json!({
            "schemaVersion": 1,
            "id": "signed-test",
            "name": "Signed test",
            "description": "Signature compatibility",
            "version": version,
            "publisher": "Test",
            "entrypoints": {"providers": ["providers/demo/provider.json"]},
            "permissions": ["process"]
        })
    }

    fn signed_envelope(signing_key: &SigningKey, manifest: Value, files: Value) -> Value {
        let body = json!({
            "formatVersion": 1,
            "manifest": manifest,
            "files": files
        });
        let envelope = json!({
            "formatVersion": 1,
            "manifest": body["manifest"].clone(),
            "files": body["files"].clone(),
            "integrity": {
                "algorithm": "sha256",
                "value": sha256_hex(canonical_json(&body).unwrap().as_bytes())
            }
        });
        sign_document(signing_key, envelope)
    }

    fn sign_document(signing_key: &SigningKey, mut document: Value) -> Value {
        let signature = signing_key.sign(canonical_json(&document).unwrap().as_bytes());
        document["signature"] = json!({
            "algorithm": "ed25519",
            "keyId": "test-key",
            "value": BASE64.encode(signature.to_bytes())
        });
        document
    }

    fn basic_files() -> Value {
        json!({
            "providers/demo/provider.json": BASE64.encode(br#"{"schemaVersion":1}"#),
            "backend/demo.py": BASE64.encode(b"print('{}')\n")
        })
    }

    fn install_test_plugin(
        manager: &PluginManager,
        signing_key: &SigningKey,
        version: &str,
    ) -> PathBuf {
        let envelope = signed_envelope(signing_key, test_manifest(version), basic_files());
        manager
            .install_bytes(&serde_json::to_vec(&envelope).unwrap(), json!({}))
            .unwrap();
        manager.version_root("signed-test", version).unwrap()
    }

    fn copy_directory(source: &Path, target: &Path) {
        fs::create_dir_all(target).unwrap();
        for entry in fs::read_dir(source).unwrap().flatten() {
            let path = entry.path();
            let destination = target.join(entry.file_name());
            if path.is_dir() {
                if !entry
                    .file_name()
                    .to_string_lossy()
                    .eq_ignore_ascii_case("__pycache__")
                {
                    copy_directory(&path, &destination);
                }
            } else if path
                .extension()
                .and_then(|extension| extension.to_str())
                .is_none_or(|extension| {
                    !extension.eq_ignore_ascii_case("pyc") && !extension.eq_ignore_ascii_case("pyo")
                })
            {
                fs::copy(path, destination).unwrap();
            }
        }
    }

    #[test]
    fn semantic_version_order_matches_plugin_v1() {
        assert_eq!(compare_versions("1.4.0", "1.3.10"), 1);
        assert_eq!(compare_versions("1.4.0-beta.1", "1.4.0"), -1);
        assert_eq!(compare_versions("1.4.0-beta.10", "1.4.0-beta.2"), 1);
        assert_eq!(compare_versions("1.4.0", "1.4.0"), 0);
        assert!(validate_version("1.2.3-").is_err());
        assert!(validate_version("01.2.3").is_err());
    }

    #[test]
    fn manifest_permissions_remain_allowlisted() {
        let manifest = json!({
            "schemaVersion": 1,
            "id": "demo-plugin",
            "name": "Demo",
            "description": "Demo plugin",
            "version": "1.0.0",
            "publisher": "Wandao",
            "entrypoints": {"providers": ["providers/demo/provider.json"]},
            "permissions": ["process", "filesystem:read"]
        });
        assert!(validate_plugin_manifest(&manifest).is_ok());
        let mut invalid = manifest;
        invalid["permissions"] = json!(["arbitrary-code"]);
        assert!(validate_plugin_manifest(&invalid).is_err());
    }

    #[test]
    fn manifest_collection_types_are_not_silently_ignored() {
        let manifest = test_manifest("1.0.0");

        let mut invalid_permissions = manifest.clone();
        invalid_permissions["permissions"] = json!("process");
        assert!(validate_plugin_manifest(&invalid_permissions)
            .unwrap_err()
            .contains("权限"));

        let mut invalid_platforms = manifest.clone();
        invalid_platforms["platforms"] = json!(["win32", 7]);
        assert!(validate_plugin_manifest(&invalid_platforms)
            .unwrap_err()
            .contains("platforms"));

        let mut invalid_ui = manifest.clone();
        invalid_ui["entrypoints"]["ui"] = json!(7);
        assert!(validate_plugin_manifest(&invalid_ui)
            .unwrap_err()
            .contains("UI"));

        let mut invalid_core_version = manifest;
        invalid_core_version["core"] = json!({"minVersion": 140});
        assert!(validate_plugin_manifest(&invalid_core_version)
            .unwrap_err()
            .contains("core.minVersion"));
    }

    #[test]
    fn signed_registry_validates_plugin_contract_fields() {
        let temporary = std::env::temp_dir().join(format!(
            "wandao-registry-contract-test-{}",
            uuid::Uuid::new_v4()
        ));
        let signing_key = SigningKey::from_bytes(&[16_u8; 32]);
        let manager = test_manager(&temporary, &signing_key);
        let registry_with = |field: &str, value: Value| {
            let mut entry = json!({
                "id": "demo-plugin",
                "version": "1.0.0",
                "name": "Demo plugin",
                "description": "Registry contract fixture",
                "publisher": "Test",
                "packageUrl": "https://example.com/demo.wandao-plugin",
                "sha256": "a".repeat(64)
            });
            entry[field] = value;
            sign_document(
                &signing_key,
                json!({"formatVersion": 1, "plugins": [entry]}),
            )
        };

        assert!(manager
            .verify_registry(&registry_with("permissions", json!("process")))
            .unwrap_err()
            .contains("权限"));
        assert!(manager
            .verify_registry(&registry_with("platforms", json!(["win32", 1])))
            .unwrap_err()
            .contains("platforms"));
        assert!(manager
            .verify_registry(&registry_with("minCoreVersion", json!("not-semver")))
            .unwrap_err()
            .contains("minCoreVersion"));

        let _ = fs::remove_dir_all(temporary);
    }

    #[test]
    fn script_and_ui_extensions_match_electron_case_insensitively() {
        assert!(has_extension(Path::new("backend/EXPORT.PY"), "py"));
        assert!(has_extension(Path::new("ui/INDEX.HTML"), "html"));
        assert!(!has_extension(Path::new("backend/export.txt"), "py"));
    }

    #[test]
    fn empty_platform_list_means_all_platforms() {
        let temporary =
            std::env::temp_dir().join(format!("wandao-platform-test-{}", uuid::Uuid::new_v4()));
        let manager = test_manager(&temporary, &SigningKey::from_bytes(&[8_u8; 32]));
        assert_eq!(
            manager.compatibility(&json!({"platforms": []}))["compatible"],
            true
        );
        assert_eq!(
            manager.compatibility(&json!({"platforms": ["definitely-not-current"]}))["compatible"],
            false
        );
        let _ = fs::remove_dir_all(temporary);
    }

    #[test]
    fn malformed_state_is_never_replaced_during_install() {
        let temporary =
            std::env::temp_dir().join(format!("wandao-state-test-{}", uuid::Uuid::new_v4()));
        let signing_key = SigningKey::from_bytes(&[9_u8; 32]);
        let manager = test_manager(&temporary, &signing_key);
        let corrupt = b"{ definitely-not-json";
        fs::write(&manager.state_file, corrupt).unwrap();
        let envelope = signed_envelope(&signing_key, test_manifest("1.0.0"), basic_files());

        let error = manager
            .install_bytes(&serde_json::to_vec(&envelope).unwrap(), json!({}))
            .unwrap_err();
        assert!(error.contains("已损坏"));
        assert_eq!(fs::read(&manager.state_file).unwrap(), corrupt);
        assert!(!manager.plugin_root("signed-test").unwrap().exists());
        let _ = fs::remove_dir_all(temporary);
    }

    #[test]
    fn package_cannot_replace_plugin_management_files() {
        let temporary = std::env::temp_dir().join(format!(
            "wandao-reserved-file-test-{}",
            uuid::Uuid::new_v4()
        ));
        let signing_key = SigningKey::from_bytes(&[10_u8; 32]);
        let manager = test_manager(&temporary, &signing_key);
        for reserved in [
            "plugin.json",
            "PLUGIN.JSON",
            "plugin.json/payload",
            ".wandao-install.json",
            ".wandao-install.json/payload",
            ".WANDAO-future.json",
        ] {
            let mut files = basic_files();
            files
                .as_object_mut()
                .unwrap()
                .insert(reserved.to_string(), json!(BASE64.encode(b"reserved")));
            let envelope = signed_envelope(&signing_key, test_manifest("1.0.0"), files);
            assert!(manager
                .install_bytes(&serde_json::to_vec(&envelope).unwrap(), json!({}))
                .unwrap_err()
                .contains("管理文件"));
        }
        let _ = fs::remove_dir_all(temporary);
    }

    #[test]
    fn package_rejects_python_cache_files_and_directories() {
        let temporary = std::env::temp_dir().join(format!(
            "wandao-package-cache-test-{}",
            uuid::Uuid::new_v4()
        ));
        let signing_key = SigningKey::from_bytes(&[17_u8; 32]);
        let manager = test_manager(&temporary, &signing_key);
        for cache_path in [
            "backend/__pycache__/demo.cpython-311.pyc",
            "backend/__PYCACHE__/payload.txt",
            "backend/demo.PYC",
            "backend/demo.PYO",
        ] {
            let mut files = basic_files();
            files.as_object_mut().unwrap().insert(
                cache_path.to_string(),
                json!(BASE64.encode(b"cache payload")),
            );
            let envelope = signed_envelope(&signing_key, test_manifest("1.0.0"), files);
            let error = manager
                .install_bytes(&serde_json::to_vec(&envelope).unwrap(), json!({}))
                .unwrap_err();
            assert!(error.contains("Python 缓存"), "{cache_path}: {error}");
        }
        let _ = fs::remove_dir_all(temporary);
    }

    #[test]
    fn package_requires_its_declared_ui_entry() {
        let temporary =
            std::env::temp_dir().join(format!("wandao-package-ui-test-{}", uuid::Uuid::new_v4()));
        let signing_key = SigningKey::from_bytes(&[20_u8; 32]);
        let manager = test_manager(&temporary, &signing_key);
        let mut manifest = test_manifest("1.0.0");
        manifest["entrypoints"]["ui"] = json!("ui/index.html");

        let missing = signed_envelope(&signing_key, manifest.clone(), basic_files());
        let error = manager
            .install_bytes(&serde_json::to_vec(&missing).unwrap(), json!({}))
            .unwrap_err();
        assert!(error.contains("自定义 UI 入口不存在"), "{error}");

        let mut files = basic_files();
        files.as_object_mut().unwrap().insert(
            "ui/index.html".to_string(),
            json!(BASE64.encode(b"<!doctype html><title>Plugin</title>")),
        );
        let complete = signed_envelope(&signing_key, manifest, files);
        manager
            .install_bytes(&serde_json::to_vec(&complete).unwrap(), json!({}))
            .unwrap();
        assert!(manager.read_ui("signed-test", "ui/index.html").is_ok());
        let _ = fs::remove_dir_all(temporary);
    }

    #[test]
    fn uninstall_restores_directory_when_state_commit_fails() {
        let temporary =
            std::env::temp_dir().join(format!("wandao-uninstall-test-{}", uuid::Uuid::new_v4()));
        let signing_key = SigningKey::from_bytes(&[11_u8; 32]);
        let manager = test_manager(&temporary, &signing_key);
        let envelope = signed_envelope(&signing_key, test_manifest("1.0.0"), basic_files());
        manager
            .install_bytes(&serde_json::to_vec(&envelope).unwrap(), json!({}))
            .unwrap();
        let root = manager.plugin_root("signed-test").unwrap();

        let error = manager
            .uninstall_with_commit(
                "signed-test",
                |_| Err("injected commit failure".to_string()),
            )
            .unwrap_err();
        assert!(error.contains("injected commit failure"));
        assert!(root.is_dir());
        assert!(manager
            .read_state()
            .unwrap()
            .pointer("/plugins/signed-test")
            .is_some());
        assert!(fs::read_dir(&manager.plugins_dir)
            .unwrap()
            .flatten()
            .all(|entry| {
                !entry
                    .file_name()
                    .to_string_lossy()
                    .starts_with(".uninstall-")
            }));
        let _ = fs::remove_dir_all(temporary);
    }

    #[test]
    fn same_version_install_reuses_only_the_exact_signed_package() {
        let temporary =
            std::env::temp_dir().join(format!("wandao-same-version-test-{}", uuid::Uuid::new_v4()));
        let signing_key = SigningKey::from_bytes(&[12_u8; 32]);
        let manager = test_manager(&temporary, &signing_key);
        let first = signed_envelope(&signing_key, test_manifest("1.0.0"), basic_files());
        manager
            .install_bytes(&serde_json::to_vec(&first).unwrap(), json!({}))
            .unwrap();

        let mut replacement_files = basic_files();
        replacement_files.as_object_mut().unwrap().insert(
            "backend/demo.py".to_string(),
            json!(BASE64.encode(b"print('replacement')\n")),
        );
        let replacement = signed_envelope(&signing_key, test_manifest("1.0.0"), replacement_files);
        manager
            .install_bytes(&serde_json::to_vec(&replacement).unwrap(), json!({}))
            .unwrap();
        let script = manager
            .resolve_script("signed-test", "backend/demo.py")
            .unwrap()
            .0;
        assert_eq!(fs::read(script).unwrap(), b"print('replacement')\n");
        let _ = fs::remove_dir_all(temporary);
    }

    #[test]
    fn same_version_state_commit_failure_restores_original_package_and_state() {
        let temporary = std::env::temp_dir().join(format!(
            "wandao-same-version-commit-test-{}",
            uuid::Uuid::new_v4()
        ));
        let signing_key = SigningKey::from_bytes(&[21_u8; 32]);
        let manager = test_manager(&temporary, &signing_key);
        let original = signed_envelope(&signing_key, test_manifest("1.0.0"), basic_files());
        manager
            .install_bytes(&serde_json::to_vec(&original).unwrap(), json!({}))
            .unwrap();
        let original_state = fs::read(&manager.state_file).unwrap();
        let root = manager.version_root("signed-test", "1.0.0").unwrap();
        let script = root.join("backend/demo.py");
        let original_script = fs::read(&script).unwrap();

        let mut replacement_files = basic_files();
        replacement_files.as_object_mut().unwrap().insert(
            "backend/demo.py".to_string(),
            json!(BASE64.encode(b"print('must roll back')\n")),
        );
        let replacement = signed_envelope(&signing_key, test_manifest("1.0.0"), replacement_files);
        let error = manager
            .install_bytes_with_state_commit(
                &serde_json::to_vec(&replacement).unwrap(),
                json!({}),
                |_| Err("injected state commit failure".to_string()),
            )
            .unwrap_err();

        assert!(error.contains("injected state commit failure"), "{error}");
        assert_eq!(fs::read(&manager.state_file).unwrap(), original_state);
        assert_eq!(fs::read(&script).unwrap(), original_script);
        manager
            .verify_installed_version("signed-test", "1.0.0")
            .unwrap();
        assert!(fs::read_dir(root.parent().unwrap())
            .unwrap()
            .flatten()
            .all(|entry| {
                let name = entry.file_name().to_string_lossy().to_string();
                !name.starts_with(".staging-") && !name.starts_with(".replace-")
            }));
        let _ = fs::remove_dir_all(temporary);
    }

    #[test]
    fn installed_plugin_rejects_unsigned_extra_files_during_discovery_and_execution() {
        let temporary =
            std::env::temp_dir().join(format!("wandao-extra-file-test-{}", uuid::Uuid::new_v4()));
        let signing_key = SigningKey::from_bytes(&[18_u8; 32]);
        let manager = test_manager(&temporary, &signing_key);
        let root = install_test_plugin(&manager, &signing_key, "1.0.0");

        for relative in ["sitecustomize.py", "unsigned-notes.txt"] {
            let extra = root.join(relative);
            fs::write(&extra, b"unsigned\n").unwrap();

            let discovery = manager.provider_entries_with_errors();
            assert!(discovery.entries.is_empty());
            assert!(discovery
                .errors
                .iter()
                .any(|error| error.contains("额外文件") && error.contains(relative)));
            let execution_error = manager
                .resolve_script("signed-test", "backend/demo.py")
                .unwrap_err();
            assert!(
                execution_error.contains("额外文件") && execution_error.contains(relative),
                "{execution_error}"
            );

            fs::remove_file(extra).unwrap();
            manager
                .verify_installed_version("signed-test", "1.0.0")
                .unwrap();
        }
        let _ = fs::remove_dir_all(temporary);
    }

    #[test]
    fn installed_plugin_safely_cleans_only_legacy_python_bytecode() {
        let temporary = std::env::temp_dir().join(format!(
            "wandao-installed-cache-test-{}",
            uuid::Uuid::new_v4()
        ));
        let signing_key = SigningKey::from_bytes(&[19_u8; 32]);
        let manager = test_manager(&temporary, &signing_key);
        let root = install_test_plugin(&manager, &signing_key, "1.0.0");
        let cache_dir = root.join("backend").join("__PyCache__");
        let bytecode = cache_dir.join("demo.cpython-311.PYC");
        let legacy_bytecode = root.join("backend").join("demo.PYO");
        fs::create_dir_all(&cache_dir).unwrap();
        fs::write(&bytecode, b"legacy cache").unwrap();
        fs::write(&legacy_bytecode, b"legacy optimized cache").unwrap();

        manager
            .verify_installed_version("signed-test", "1.0.0")
            .unwrap();
        assert!(!bytecode.exists());
        assert!(!legacy_bytecode.exists());
        assert!(!cache_dir.exists());
        assert!(manager
            .resolve_script("signed-test", "backend/demo.py")
            .is_ok());
        let discovery = manager.provider_entries_with_errors();
        assert!(discovery.errors.is_empty(), "{:?}", discovery.errors);
        assert_eq!(discovery.entries.len(), 1);

        let disguised_cache_dir = root.join("backend").join("__pycache__");
        let unsigned_payload = disguised_cache_dir.join("payload.txt");
        fs::create_dir_all(&disguised_cache_dir).unwrap();
        fs::write(&unsigned_payload, b"not bytecode").unwrap();
        let error = manager
            .verify_installed_version("signed-test", "1.0.0")
            .err()
            .expect("unsigned cache payload must fail verification");
        assert!(error.contains("额外文件"), "{error}");
        assert!(unsigned_payload.is_file());
        let _ = fs::remove_dir_all(temporary);
    }

    #[test]
    fn startup_restores_replace_tombstone_without_reading_corrupt_state() {
        let temporary = std::env::temp_dir().join(format!(
            "wandao-replace-recovery-test-{}",
            uuid::Uuid::new_v4()
        ));
        let signing_key = SigningKey::from_bytes(&[13_u8; 32]);
        let manager = test_manager(&temporary, &signing_key);
        let envelope = signed_envelope(&signing_key, test_manifest("1.0.0"), basic_files());
        manager
            .install_bytes(&serde_json::to_vec(&envelope).unwrap(), json!({}))
            .unwrap();

        let plugin_dir = manager.plugin_root("signed-test").unwrap();
        let target = manager.version_root("signed-test", "1.0.0").unwrap();
        let tombstone = manager
            .replace_tombstone_path("signed-test", "1.0.0")
            .unwrap();
        let staging = plugin_dir.join(".staging-1.0.0-crash-window");
        fs::rename(&target, &tombstone).unwrap();
        fs::create_dir_all(&staging).unwrap();
        fs::write(staging.join("partial"), b"partial").unwrap();
        let corrupt_state = b"{ corrupt plugin state";
        fs::write(&manager.state_file, corrupt_state).unwrap();

        manager.recover_operation_directories().unwrap();

        assert!(target.is_dir());
        assert!(!tombstone.exists());
        assert!(!staging.exists());
        manager
            .verify_installed_version("signed-test", "1.0.0")
            .unwrap();
        assert_eq!(fs::read(&manager.state_file).unwrap(), corrupt_state);
        let _ = fs::remove_dir_all(temporary);
    }

    #[test]
    fn final_validation_failure_restores_replaced_version() {
        let temporary = std::env::temp_dir().join(format!(
            "wandao-replace-validation-test-{}",
            uuid::Uuid::new_v4()
        ));
        let signing_key = SigningKey::from_bytes(&[14_u8; 32]);
        let manager = test_manager(&temporary, &signing_key);
        let envelope = signed_envelope(&signing_key, test_manifest("1.0.0"), basic_files());
        manager
            .install_bytes(&serde_json::to_vec(&envelope).unwrap(), json!({}))
            .unwrap();

        let plugin_dir = manager.plugin_root("signed-test").unwrap();
        let target = manager.version_root("signed-test", "1.0.0").unwrap();
        let staging = plugin_dir.join(".staging-1.0.0-final-validation");
        copy_directory(&target, &staging);
        manager
            .verify_installed_root(&staging, "signed-test", "1.0.0")
            .unwrap();

        let error = manager
            .commit_staged_version("signed-test", "1.0.0", &staging, || {
                Err("injected final verification failure".to_string())
            })
            .unwrap_err();

        assert!(error.contains("injected final verification failure"));
        assert!(target.is_dir());
        manager
            .verify_installed_version("signed-test", "1.0.0")
            .unwrap();
        assert!(fs::read_dir(&plugin_dir)
            .unwrap()
            .flatten()
            .all(|entry| { !entry.file_name().to_string_lossy().starts_with(".replace-") }));
        let _ = fs::remove_dir_all(temporary);
    }

    #[test]
    fn startup_replaces_partial_target_with_verified_replace_tombstone() {
        let temporary = std::env::temp_dir().join(format!(
            "wandao-partial-replace-recovery-test-{}",
            uuid::Uuid::new_v4()
        ));
        let signing_key = SigningKey::from_bytes(&[15_u8; 32]);
        let manager = test_manager(&temporary, &signing_key);
        let envelope = signed_envelope(&signing_key, test_manifest("1.0.0"), basic_files());
        manager
            .install_bytes(&serde_json::to_vec(&envelope).unwrap(), json!({}))
            .unwrap();

        let target = manager.version_root("signed-test", "1.0.0").unwrap();
        let tombstone = manager
            .replace_tombstone_path("signed-test", "1.0.0")
            .unwrap();
        fs::rename(&target, &tombstone).unwrap();
        fs::create_dir_all(&target).unwrap();
        fs::write(target.join("partial"), b"partial").unwrap();

        manager.recover_operation_directories().unwrap();

        assert!(!tombstone.exists());
        assert!(!target.join("partial").exists());
        manager
            .verify_installed_version("signed-test", "1.0.0")
            .unwrap();
        let _ = fs::remove_dir_all(temporary);
    }

    #[cfg(unix)]
    #[test]
    fn startup_recovery_does_not_traverse_symlinked_plugin_directories() {
        use std::os::unix::fs::symlink;

        let temporary = std::env::temp_dir().join(format!(
            "wandao-recovery-link-test-{}",
            uuid::Uuid::new_v4()
        ));
        let signing_key = SigningKey::from_bytes(&[21_u8; 32]);
        let manager = test_manager(&temporary, &signing_key);
        let external = temporary.join("external");
        let external_staging = external.join(".staging-must-survive");
        let marker = external_staging.join("marker.txt");
        fs::create_dir_all(&external_staging).unwrap();
        fs::write(&marker, b"keep").unwrap();
        let linked_plugin = manager.plugins_dir.join("signed-test");
        symlink(&external, &linked_plugin).unwrap();

        manager.recover_operation_directories().unwrap();

        assert_eq!(fs::read(&marker).unwrap(), b"keep");
        fs::remove_file(linked_plugin).unwrap();
        let _ = fs::remove_dir_all(temporary);
    }

    #[test]
    fn bundled_hash_catalog_covers_all_plugins_and_detects_tampering() {
        let ids: BTreeSet<&str> = BUNDLED_PLUGIN_HASHES
            .iter()
            .map(|(plugin_id, _, _)| *plugin_id)
            .collect();
        assert_eq!(ids.len(), 14);
        let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        let repository_plugins = manifest_dir.join("..").join("..").join("plugins");
        let temporary =
            std::env::temp_dir().join(format!("wandao-bundled-test-{}", uuid::Uuid::new_v4()));
        let release_plugins = temporary.join("release-plugins");
        copy_directory(&repository_plugins, &release_plugins);
        for plugin_id in ids {
            verify_bundled_plugin(&release_plugins, plugin_id).unwrap();
        }

        let copied_plugins = temporary.join("plugins");
        copy_directory(
            &release_plugins.join("dingtalk"),
            &copied_plugins.join("dingtalk"),
        );
        verify_bundled_plugin_file(&copied_plugins, "dingtalk", "backend/export_dingtalk.py")
            .unwrap();
        let extra = copied_plugins.join("dingtalk").join("sitecustomize.py");
        fs::write(&extra, b"unsigned\n").unwrap();
        let error = verify_bundled_plugin(&copied_plugins, "dingtalk").unwrap_err();
        assert!(
            error.contains("额外文件") && error.contains("sitecustomize.py"),
            "{error}"
        );
        fs::remove_file(extra).unwrap();
        fs::write(
            copied_plugins
                .join("dingtalk")
                .join("backend")
                .join("export_dingtalk.py"),
            b"tampered\n",
        )
        .unwrap();
        assert!(verify_bundled_plugin_file(
            &copied_plugins,
            "dingtalk",
            "backend/export_dingtalk.py"
        )
        .unwrap_err()
        .contains("构建后被修改"));
        let _ = fs::remove_dir_all(temporary);
    }

    #[test]
    fn signed_plugin_install_and_post_install_tamper_detection_match_v1() {
        let temporary =
            std::env::temp_dir().join(format!("wandao-plugin-test-{}", uuid::Uuid::new_v4()));
        let plugins_dir = temporary.join("installed");
        fs::create_dir_all(&plugins_dir).unwrap();
        let signing_key = SigningKey::from_bytes(&[7_u8; 32]);
        let public_key = signing_key
            .verifying_key()
            .to_public_key_pem(Default::default())
            .unwrap();
        let manager = PluginManager {
            plugins_dir: plugins_dir.clone(),
            state_file: temporary.join("state.json"),
            trust_store: json!({
                "schemaVersion": 1,
                "keys": [{
                    "id": "test-key",
                    "algorithm": "ed25519",
                    "publisher": "Test",
                    "publicKey": public_key
                }]
            }),
            core_version: "1.4.0".into(),
            platform: platform_id().into(),
            operation_lock: Arc::new(Mutex::new(())),
        };
        let manifest = json!({
            "schemaVersion": 1,
            "id": "signed-test",
            "name": "Signed test",
            "description": "Signature compatibility",
            "version": "1.0.0",
            "publisher": "Test",
            "entrypoints": {"providers": ["providers/demo/provider.json"]},
            "permissions": ["process"]
        });
        let files = json!({
            "providers/demo/provider.json": BASE64.encode(br#"{"schemaVersion":1}"#),
            "backend/demo.py": BASE64.encode(b"print('{}')\n")
        });
        let body = json!({
            "formatVersion": 1,
            "manifest": manifest,
            "files": files
        });
        let mut envelope = json!({
            "formatVersion": 1,
            "manifest": body["manifest"].clone(),
            "files": body["files"].clone(),
            "integrity": {
                "algorithm": "sha256",
                "value": sha256_hex(canonical_json(&body).unwrap().as_bytes())
            }
        });
        let signature = signing_key.sign(canonical_json(&envelope).unwrap().as_bytes());
        envelope["signature"] = json!({
            "algorithm": "ed25519",
            "keyId": "test-key",
            "value": BASE64.encode(signature.to_bytes())
        });

        let installed = manager
            .install_bytes(
                serde_json::to_vec(&envelope).unwrap().as_slice(),
                json!({"sourceFile": "test.wandao-plugin"}),
            )
            .unwrap();
        assert_eq!(installed["currentVersion"], "1.0.0");
        let script = manager
            .resolve_script("signed-test", "backend/demo.py")
            .unwrap()
            .0;
        fs::write(&script, b"tampered\n").unwrap();
        assert!(manager
            .resolve_script("signed-test", "backend/demo.py")
            .unwrap_err()
            .contains("安装后被修改"));
        let _ = fs::remove_dir_all(temporary);
    }
}
