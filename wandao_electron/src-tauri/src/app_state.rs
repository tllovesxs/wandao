use std::{
    collections::HashMap,
    env,
    ffi::OsStr,
    path::{Path, PathBuf},
    sync::Mutex,
    time::Instant,
};

use serde_json::Value;
use tauri::{AppHandle, Manager};

#[derive(Debug, Clone)]
pub struct AppPaths {
    pub app_dir: PathBuf,
    pub user_data: PathBuf,
    pub project_root: PathBuf,
    pub bundled_plugins: PathBuf,
    pub bundled_providers: PathBuf,
    pub bundled_python_runtime: PathBuf,
    pub assets: PathBuf,
}

impl AppPaths {
    pub fn discover(app: &AppHandle) -> Result<Self, String> {
        let app_dir = env::current_exe()
            .ok()
            .and_then(|path| path.parent().map(Path::to_path_buf))
            .or_else(|| env::current_dir().ok())
            .ok_or_else(|| "无法确定应用目录".to_string())?;
        let resource_dir = app
            .path()
            .resource_dir()
            .unwrap_or_else(|_| app_dir.clone());

        // Electron historically used %APPDATA%/wandao on Windows and
        // ~/Library/Application Support/wandao on macOS. Keep that exact root
        // so installed plugins, task history and credentials survive the shell
        // migration.
        let user_data =
            resolve_user_data_dir(env::var_os("WANDAO_USER_DATA_DIR").as_deref(), &app_dir);

        let current_dir = env::current_dir().unwrap_or_else(|_| app_dir.clone());
        let (project_root, bundled_plugins, bundled_providers, bundled_python_runtime) =
            if cfg!(debug_assertions) {
                let project_candidates = unique_paths([
                    current_dir.parent().map(Path::to_path_buf),
                    Some(current_dir.clone()),
                    Some(resource_dir.join("python")),
                    Some(resource_dir.clone()),
                    app_dir.parent().map(Path::to_path_buf),
                ]);
                let project_root = project_candidates
                    .iter()
                    .find(|path| path.join("wandao_logging.py").is_file())
                    .cloned()
                    .unwrap_or_else(|| resource_dir.join("python"));
                let bundled_plugins = first_existing_dir([
                    resource_dir.join("plugins"),
                    project_root.join("plugins"),
                    current_dir.join("plugins"),
                    current_dir.join("..").join("plugins"),
                ])
                .unwrap_or_else(|| resource_dir.join("plugins"));
                let bundled_providers = first_existing_dir([
                    resource_dir.join("providers"),
                    project_root.join("providers"),
                    current_dir.join("providers"),
                    current_dir.join("..").join("providers"),
                ])
                .unwrap_or_else(|| resource_dir.join("providers"));
                let bundled_python_runtime = first_existing_dir([
                    resource_dir.join("python-runtime"),
                    current_dir.join("runtime").join("python-runtime"),
                    app_dir.join("runtime").join("python-runtime"),
                ])
                .unwrap_or_else(|| resource_dir.join("python-runtime"));
                (
                    project_root,
                    bundled_plugins,
                    bundled_providers,
                    bundled_python_runtime,
                )
            } else {
                (
                    resource_dir.join("python"),
                    resource_dir.join("plugins"),
                    resource_dir.join("providers"),
                    resource_dir.join("python-runtime"),
                )
            };
        let packaged_assets = resource_dir.join("assets");
        let assets = if packaged_assets.join("plugin-trust.json").is_file() {
            packaged_assets
        } else if cfg!(debug_assertions) {
            first_existing_dir([
                project_root.join("wandao_electron").join("assets"),
                current_dir.join("wandao_electron").join("assets"),
                current_dir.join("assets"),
            ])
            .unwrap_or(packaged_assets)
        } else {
            packaged_assets
        };

        std::fs::create_dir_all(&user_data)
            .map_err(|error| format!("无法创建应用数据目录：{error}"))?;

        Ok(Self {
            app_dir,
            user_data,
            project_root,
            bundled_plugins,
            bundled_providers,
            bundled_python_runtime,
            assets,
        })
    }
}

fn resolve_user_data_dir(override_dir: Option<&OsStr>, app_dir: &Path) -> PathBuf {
    override_dir
        .filter(|value| !value.is_empty())
        .map(Path::new)
        .map(normalize_absolute)
        .unwrap_or_else(|| {
            dirs::data_dir()
                .unwrap_or_else(|| app_dir.to_path_buf())
                .join("wandao")
        })
}

#[derive(Debug, Clone)]
pub struct CachedRegistry {
    pub cached_at: Instant,
    pub registry: Value,
}

pub struct AppState {
    pub paths: AppPaths,
    pub guide_roots: Mutex<HashMap<String, PathBuf>>,
    pub registry_cache: Mutex<HashMap<String, CachedRegistry>>,
}

impl AppState {
    pub fn new(paths: AppPaths) -> Self {
        Self {
            paths,
            guide_roots: Mutex::new(HashMap::new()),
            registry_cache: Mutex::new(HashMap::new()),
        }
    }
}

pub fn platform_id() -> &'static str {
    #[cfg(target_os = "windows")]
    {
        "win32"
    }
    #[cfg(target_os = "macos")]
    {
        "darwin"
    }
    #[cfg(target_os = "linux")]
    {
        "linux"
    }
}

pub fn is_inside(root: &Path, candidate: &Path) -> bool {
    let root = resolve_for_boundary(root);
    let candidate = resolve_for_boundary(candidate);
    if cfg!(target_os = "windows") {
        let root = root.to_string_lossy().to_lowercase();
        let candidate = candidate.to_string_lossy().to_lowercase();
        candidate == root
            || candidate
                .strip_prefix(&root)
                .is_some_and(|suffix| suffix.starts_with(['\\', '/']))
    } else {
        candidate == root || candidate.starts_with(root)
    }
}

fn resolve_for_boundary(path: &Path) -> PathBuf {
    let normalized = normalize_absolute(path);
    let mut existing = normalized.as_path();
    let mut suffix = Vec::new();
    while !existing.exists() {
        let Some(name) = existing.file_name() else {
            break;
        };
        suffix.push(name.to_os_string());
        let Some(parent) = existing.parent() else {
            break;
        };
        existing = parent;
    }
    let mut resolved = fs_canonicalize(existing).unwrap_or_else(|| existing.to_path_buf());
    for component in suffix.iter().rev() {
        resolved.push(component);
    }
    clean_path(&resolved)
}

fn fs_canonicalize(path: &Path) -> Option<PathBuf> {
    let resolved = std::fs::canonicalize(path).ok()?;
    #[cfg(target_os = "windows")]
    {
        let text = resolved.to_string_lossy();
        if let Some(without_prefix) = text.strip_prefix(r"\\?\") {
            return Some(PathBuf::from(without_prefix));
        }
    }
    Some(resolved)
}

pub fn normalize_absolute(path: &Path) -> PathBuf {
    if path.is_absolute() {
        clean_path(path)
    } else {
        clean_path(
            &env::current_dir()
                .unwrap_or_else(|_| PathBuf::from("."))
                .join(path),
        )
    }
}

fn clean_path(path: &Path) -> PathBuf {
    use std::path::Component;

    let mut output = PathBuf::new();
    for component in path.components() {
        match component {
            Component::CurDir => {}
            Component::ParentDir => {
                output.pop();
            }
            other => output.push(other.as_os_str()),
        }
    }
    output
}

fn unique_paths<const N: usize>(items: [Option<PathBuf>; N]) -> Vec<PathBuf> {
    let mut output = Vec::new();
    for path in items.into_iter().flatten() {
        let path = normalize_absolute(&path);
        if !output.iter().any(|item: &PathBuf| {
            if cfg!(target_os = "windows") {
                item.to_string_lossy()
                    .eq_ignore_ascii_case(&path.to_string_lossy())
            } else {
                item == &path
            }
        }) {
            output.push(path);
        }
    }
    output
}

fn first_existing_dir<const N: usize>(items: [PathBuf; N]) -> Option<PathBuf> {
    items.into_iter().find(|path| path.is_dir())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn inside_path_rejects_sibling_prefixes() {
        let root = PathBuf::from(if cfg!(target_os = "windows") {
            r"C:\Users\tester\AppData\Roaming\wandao"
        } else {
            "/Users/tester/Library/Application Support/wandao"
        });
        assert!(is_inside(&root, &root.join("runtime/tasks.json")));
        assert!(!is_inside(
            &root,
            &root.with_file_name("wandao-malicious").join("state.json")
        ));
    }

    #[test]
    fn relative_user_data_override_matches_electron_path_resolve_semantics() {
        let current_dir = env::current_dir().expect("current directory should be available");
        let app_dir = current_dir.join("unused-app-dir");
        let resolved = resolve_user_data_dir(
            Some(OsStr::new("relative-user-data/../wandao-profile")),
            &app_dir,
        );

        assert!(resolved.is_absolute());
        assert_eq!(resolved, clean_path(&current_dir.join("wandao-profile")));
    }

    #[test]
    fn empty_user_data_override_uses_the_electron_default() {
        let app_dir = env::current_dir()
            .expect("current directory should be available")
            .join("fallback-app-dir");
        let resolved = resolve_user_data_dir(Some(OsStr::new("")), &app_dir);
        let expected = dirs::data_dir()
            .unwrap_or_else(|| app_dir.clone())
            .join("wandao");

        assert_eq!(resolved, expected);
    }
}
