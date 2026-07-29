//! Provider task process runtime.
//!
//! This module intentionally has no dependency on Tauri. The command layer is
//! responsible for resolving and validating the executable/script paths,
//! extracting secret arguments, and translating [`TaskRuntimeEvent`] values to
//! window events. This keeps process lifecycle behavior testable and prevents
//! the runtime from becoming another script-routing authority.

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use std::collections::{BTreeMap, HashSet};
use std::fs;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, ExitStatus, Stdio};
use std::sync::{Arc, Condvar, Mutex, MutexGuard};
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

pub const STRUCTURED_LOG_PREFIX: &str = "@@WANDAO_LOG@@";
pub const TASK_RESULT_KIND: &str = "wandao.result";
pub const TASK_RESULT_SCHEMA_VERSION: u64 = 1;
pub const DEFAULT_OUTPUT_LIMIT_BYTES: usize = 32 * 1024 * 1024;
pub const DEFAULT_STOP_GRACE: Duration = Duration::from_secs(8);
const MAX_STRUCTURED_LOG_LINE_BYTES: usize = 1024 * 1024;

const PLUGIN_ENV_ALLOWLIST: &[&str] = &[
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "TMPDIR",
    "HOME",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMDATA",
    "LANG",
    "LC_ALL",
    "TZ",
];

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PluginExecutionContext {
    pub plugin_id: String,
    pub plugin_version: String,
    pub plugin_root: PathBuf,
    pub plugin_data_dir: PathBuf,
    #[serde(default)]
    pub permissions: Vec<String>,
}

/// Serializable inputs used to construct the provider's environment.
///
/// Callers should put values extracted from sensitive command arguments in
/// `secret_environment`; they are never included in runtime events. For plugin
/// tasks, only the same small host environment allowlist used by Electron is
/// inherited.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TaskExecutionContext {
    pub user_data_dir: PathBuf,
    #[serde(default)]
    pub provider_id: String,
    #[serde(default)]
    pub task_id: String,
    #[serde(default)]
    pub run_id: String,
    #[serde(default)]
    pub job_id: String,
    #[serde(default)]
    pub parent_run_id: String,
    /// ISO-8601 timestamp supplied by the command layer for UI compatibility.
    #[serde(default)]
    pub started_at: String,
    #[serde(default)]
    pub browser_path: Option<PathBuf>,
    #[serde(default)]
    pub python_runtime: Option<PathBuf>,
    #[serde(default)]
    pub python_library_dir: Option<PathBuf>,
    #[serde(default)]
    pub additional_python_paths: Vec<PathBuf>,
    #[serde(default)]
    pub plugin: Option<PluginExecutionContext>,
    #[serde(default)]
    pub secret_environment: BTreeMap<String, String>,
    #[serde(default)]
    pub extra_environment: BTreeMap<String, String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TaskRunRequest {
    /// Python executable or another explicitly approved provider runtime.
    pub executable: PathBuf,
    /// Absolute, pre-validated provider script path.
    pub script: PathBuf,
    #[serde(default)]
    pub args: Vec<String>,
    #[serde(default)]
    pub working_directory: Option<PathBuf>,
    pub context: TaskExecutionContext,
    #[serde(default)]
    pub stop_file: Option<PathBuf>,
    #[serde(default)]
    pub stdin_text: Option<String>,
    #[serde(default)]
    pub close_stdin_after_initial_input: bool,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct TaskProcessState {
    pub running: bool,
    pub stopping: bool,
    pub provider_id: String,
    pub task_id: String,
    pub started_at: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub last_status: Option<TaskLastStatus>,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub enum TaskLastStatus {
    Finished,
    Stopped,
    Failed,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum TaskOutputStream {
    Stdout,
    Stderr,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(tag = "type", rename_all = "camelCase")]
pub enum TaskRuntimeEvent {
    State {
        state: TaskProcessState,
    },
    Output {
        stream: TaskOutputStream,
        text: String,
    },
    StructuredLog {
        stream: TaskOutputStream,
        payload: Value,
        raw: String,
    },
    Diagnostic {
        level: DiagnosticLevel,
        message: String,
    },
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum DiagnosticLevel {
    Info,
    Warn,
    Error,
}

pub type TaskEventSink = Arc<dyn Fn(TaskRuntimeEvent) + Send + Sync + 'static>;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(untagged)]
pub enum TaskExitCode {
    Number(i32),
    Label(String),
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TaskRunResult {
    pub success: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub code: Option<TaskExitCode>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub data: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub legacy_result: Option<bool>,
}

impl TaskRunResult {
    fn failure(error: impl Into<String>) -> Self {
        Self {
            success: false,
            error: Some(error.into()),
            code: None,
            data: None,
            legacy_result: None,
        }
    }
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TaskControlResult {
    pub success: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    #[serde(default)]
    pub stopping: bool,
    #[serde(default)]
    pub cooperative: bool,
}

struct ActiveProcess {
    token: u64,
    target: ProcessTarget,
    stopping: bool,
    stop_file: PathBuf,
    stdin: Option<Arc<Mutex<ChildStdin>>>,
    provider_id: String,
    task_id: String,
    started_at: String,
}

#[derive(Default)]
struct RuntimeState {
    next_token: u64,
    active: Option<ActiveProcess>,
}

struct RuntimeInner {
    state: Mutex<RuntimeState>,
    idle: Condvar,
}

#[derive(Clone)]
struct ProcessTarget {
    pid: u32,
    #[cfg(windows)]
    job: Option<Arc<WindowsJob>>,
}

type ProcessTerminator = Arc<dyn Fn(&ProcessTarget, bool) -> bool + Send + Sync + 'static>;

#[derive(Clone)]
pub struct TaskRuntime {
    inner: Arc<RuntimeInner>,
    output_limit_bytes: usize,
    stop_grace: Duration,
    terminator: ProcessTerminator,
}

impl Default for TaskRuntime {
    fn default() -> Self {
        Self::new(DEFAULT_OUTPUT_LIMIT_BYTES, DEFAULT_STOP_GRACE)
    }
}

impl TaskRuntime {
    pub fn new(output_limit_bytes: usize, stop_grace: Duration) -> Self {
        Self {
            inner: Arc::new(RuntimeInner {
                state: Mutex::new(RuntimeState::default()),
                idle: Condvar::new(),
            }),
            output_limit_bytes: output_limit_bytes.max(1),
            stop_grace,
            terminator: Arc::new(terminate_process_tree),
        }
    }

    #[cfg(test)]
    fn with_terminator<F>(output_limit_bytes: usize, stop_grace: Duration, terminator: F) -> Self
    where
        F: Fn(&ProcessTarget, bool) -> bool + Send + Sync + 'static,
    {
        let mut runtime = Self::new(output_limit_bytes, stop_grace);
        runtime.terminator = Arc::new(terminator);
        runtime
    }

    pub fn state(&self) -> TaskProcessState {
        state_from_active(self.lock_state().active.as_ref(), None)
    }

    /// Waits for the active process and its output readers to finish.
    ///
    /// This is intended for bounded application-shutdown coordination after
    /// [`Self::force_stop`] has successfully delivered a termination request.
    pub fn wait_until_idle(&self, timeout: Duration) -> bool {
        let state = self.lock_state();
        if state.active.is_none() {
            return true;
        }
        match self
            .inner
            .idle
            .wait_timeout_while(state, timeout, |state| state.active.is_some())
        {
            Ok((state, _)) => state.active.is_none(),
            Err(poisoned) => {
                let (state, _) = poisoned.into_inner();
                state.active.is_none()
            }
        }
    }

    /// Runs one provider task and blocks until its process exits.
    ///
    /// An async Tauri command should call this from `spawn_blocking`. Only one
    /// process may be active for a runtime at a time.
    pub fn run(&self, request: TaskRunRequest, sink: TaskEventSink) -> TaskRunResult {
        let working_directory = request
            .working_directory
            .clone()
            .or_else(|| request.script.parent().map(Path::to_path_buf))
            .unwrap_or_else(|| PathBuf::from("."));
        let stop_file = request.stop_file.clone().unwrap_or_else(|| {
            default_stop_file(&request.context.user_data_dir, &request.context.task_id)
        });

        let mut command = Command::new(&request.executable);
        command
            .arg(&request.script)
            .args(&request.args)
            .current_dir(&working_directory)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .env_clear()
            .envs(build_execution_environment(&request.context, &stop_file));
        configure_process_group(&mut command);

        let (mut child, token) = {
            let mut state = self.lock_state();
            if state.active.is_some() {
                return TaskRunResult::failure("已有任务正在运行，请先停止当前任务或等待完成。");
            }
            if let Err(error) = remove_stale_stop_file(&stop_file) {
                return TaskRunResult::failure(format!(
                    "无法清理上次任务的停止标记 {}：{error}",
                    stop_file.display()
                ));
            }
            let mut child = match command.spawn() {
                Ok(child) => child,
                Err(error) => {
                    return TaskRunResult::failure(error.to_string());
                }
            };
            let target = match process_target_for_child(&child) {
                Ok(target) => target,
                Err(error) => {
                    let _ = child.kill();
                    let _ = child.wait();
                    return TaskRunResult::failure(format!("无法建立任务进程隔离：{error}"));
                }
            };
            state.next_token = state.next_token.wrapping_add(1).max(1);
            let token = state.next_token;
            let active = ActiveProcess {
                token,
                target,
                stopping: false,
                stop_file: stop_file.clone(),
                stdin: child.stdin.take().map(|stdin| Arc::new(Mutex::new(stdin))),
                provider_id: request.context.provider_id.clone(),
                task_id: request.context.task_id.clone(),
                started_at: request.context.started_at.clone(),
            };
            state.active = Some(active);
            (child, token)
        };

        emit(
            &sink,
            TaskRuntimeEvent::State {
                state: self.state(),
            },
        );

        let stdout_tail = Arc::new(Mutex::new(CappedOutput::new(self.output_limit_bytes)));
        let stderr_tail = Arc::new(Mutex::new(CappedOutput::new(self.output_limit_bytes)));
        let scan_toc = request.args.iter().any(|argument| argument == "--scan-toc");
        let stdout_reader = child.stdout.take().map(|stdout| {
            spawn_output_reader(
                stdout,
                TaskOutputStream::Stdout,
                Arc::clone(&stdout_tail),
                Arc::clone(&sink),
                scan_toc,
            )
        });
        let stderr_reader = child.stderr.take().map(|stderr| {
            spawn_output_reader(
                stderr,
                TaskOutputStream::Stderr,
                Arc::clone(&stderr_tail),
                Arc::clone(&sink),
                false,
            )
        });

        if let Some(text) = request.stdin_text.as_deref() {
            let result =
                self.write_input_for_token(token, text, request.close_stdin_after_initial_input);
            if !result.success {
                emit(
                    &sink,
                    TaskRuntimeEvent::Diagnostic {
                        level: DiagnosticLevel::Warn,
                        message: format!(
                            "无法写入任务初始输入：{}",
                            result.error.unwrap_or_else(|| "未知错误".to_string())
                        ),
                    },
                );
            }
        }

        let wait_result = child.wait();
        let was_stopping = {
            let mut state = self.lock_state();
            let active = state.active.as_mut().filter(|active| active.token == token);
            #[cfg(windows)]
            let mut active = active;
            #[cfg(windows)]
            if let Some(active) = active.as_mut() {
                // Closing the kill-on-close job after the root exits prevents
                // surviving descendants from keeping inherited pipes open.
                active.target.job.take();
            }
            active.is_some_and(|active| active.stopping)
        };
        if let Some(reader) = stdout_reader {
            let _ = reader.join();
        }
        if let Some(reader) = stderr_reader {
            let _ = reader.join();
        }

        let stdout = self.output_snapshot(&stdout_tail);
        let stderr = self.output_snapshot(&stderr_tail);
        let cleared = {
            let mut state = self.lock_state();
            let cleared = state
                .active
                .as_ref()
                .is_some_and(|active| active.token == token);
            if cleared {
                state.active = None;
            }
            cleared
        };
        if cleared {
            self.inner.idle.notify_all();
        }
        remove_file_best_effort(&stop_file);

        let (result, last_status) = match wait_result {
            Ok(status) => {
                let result = parse_exit_result(status, was_stopping, &stdout, &stderr);
                let last_status = if was_stopping {
                    TaskLastStatus::Stopped
                } else if result.success {
                    TaskLastStatus::Finished
                } else {
                    TaskLastStatus::Failed
                };
                (result, last_status)
            }
            Err(_error) if was_stopping => (
                stopped_result(parse_last_json(&stdout.text)),
                TaskLastStatus::Stopped,
            ),
            Err(error) => (
                TaskRunResult::failure(error.to_string()),
                TaskLastStatus::Failed,
            ),
        };

        emit(
            &sink,
            TaskRuntimeEvent::State {
                state: TaskProcessState {
                    running: false,
                    stopping: false,
                    provider_id: request.context.provider_id,
                    task_id: request.context.task_id,
                    started_at: request.context.started_at,
                    last_status: Some(last_status),
                },
            },
        );
        result
    }

    pub fn write_input(&self, text: &str, end: bool) -> TaskControlResult {
        let token = match self.lock_state().active.as_ref() {
            Some(active) => active.token,
            None => {
                return TaskControlResult {
                    success: false,
                    error: Some("没有正在等待输入的任务".to_string()),
                    ..TaskControlResult::default()
                };
            }
        };
        self.write_input_for_token(token, text, end)
    }

    pub fn request_stop(&self, sink: TaskEventSink) -> TaskControlResult {
        let (token, target, stop_file) = {
            let mut state = self.lock_state();
            let Some(active) = state.active.as_mut() else {
                return TaskControlResult {
                    success: false,
                    error: Some("没有正在运行的任务".to_string()),
                    ..TaskControlResult::default()
                };
            };
            if active.stopping {
                return TaskControlResult {
                    success: true,
                    stopping: true,
                    ..TaskControlResult::default()
                };
            }
            active.stopping = true;
            (
                active.token,
                active.target.clone(),
                active.stop_file.clone(),
            )
        };
        emit(
            &sink,
            TaskRuntimeEvent::State {
                state: self.state(),
            },
        );

        let marker_result = stop_file
            .parent()
            .map(fs::create_dir_all)
            .transpose()
            .and_then(|_| fs::write(&stop_file, b"stop"));
        if marker_result.is_ok() {
            let runtime = self.clone();
            let grace = self.stop_grace;
            let sink = Arc::clone(&sink);
            let expected_pid = target.pid;
            thread::spawn(move || {
                thread::sleep(grace);
                let target = runtime.lock_state().active.as_ref().and_then(|active| {
                    (active.token == token && active.target.pid == expected_pid && active.stopping)
                        .then(|| active.target.clone())
                });
                let Some(target) = target else {
                    return;
                };
                if !(runtime.terminator)(&target, true) {
                    if let Some(state) = runtime.rollback_stopping(token) {
                        emit(
                            &sink,
                            TaskRuntimeEvent::Diagnostic {
                                level: DiagnosticLevel::Error,
                                message: "任务在停止宽限期内未退出，强制终止失败；可再次尝试停止。"
                                    .to_string(),
                            },
                        );
                        emit(&sink, TaskRuntimeEvent::State { state });
                    }
                }
            });
            return TaskControlResult {
                success: true,
                stopping: true,
                cooperative: true,
                ..TaskControlResult::default()
            };
        }

        if (self.terminator)(&target, false) {
            TaskControlResult {
                success: true,
                stopping: true,
                cooperative: false,
                error: marker_result
                    .err()
                    .map(|error| format!("停止标记写入失败，已改为发送终止信号：{error}")),
            }
        } else {
            let message = marker_result
                .err()
                .map(|error| format!("停止标记写入失败，且无法终止任务进程：{error}"))
                .unwrap_or_else(|| "无法停止当前任务，请稍后重试。".to_string());
            if let Some(state) = self.rollback_stopping(token) {
                emit(
                    &sink,
                    TaskRuntimeEvent::Diagnostic {
                        level: DiagnosticLevel::Error,
                        message: message.clone(),
                    },
                );
                emit(&sink, TaskRuntimeEvent::State { state });
            }
            TaskControlResult {
                success: false,
                error: Some(message),
                ..TaskControlResult::default()
            }
        }
    }

    /// Immediately terminates the active process tree, for application exit.
    pub fn force_stop(&self) -> TaskControlResult {
        let (token, target) = {
            let mut state = self.lock_state();
            let Some(active) = state.active.as_mut() else {
                return TaskControlResult {
                    success: false,
                    error: Some("没有正在运行的任务".to_string()),
                    ..TaskControlResult::default()
                };
            };
            active.stopping = true;
            (active.token, active.target.clone())
        };
        if (self.terminator)(&target, true) {
            TaskControlResult {
                success: true,
                stopping: true,
                ..TaskControlResult::default()
            }
        } else {
            self.rollback_stopping(token);
            TaskControlResult {
                success: false,
                error: Some("无法停止当前任务，请稍后重试。".to_string()),
                ..TaskControlResult::default()
            }
        }
    }

    fn output_snapshot(&self, output: &Arc<Mutex<CappedOutput>>) -> OutputSnapshot {
        output
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .snapshot()
    }

    fn write_input_for_token(&self, token: u64, text: &str, end: bool) -> TaskControlResult {
        let stdin = {
            let mut state = self.lock_state();
            let Some(active) = state.active.as_mut().filter(|active| active.token == token) else {
                return TaskControlResult {
                    success: false,
                    error: Some("没有正在等待输入的任务".to_string()),
                    ..TaskControlResult::default()
                };
            };
            let Some(stdin) = active.stdin.as_ref().cloned() else {
                return TaskControlResult {
                    success: false,
                    error: Some("没有正在等待输入的任务".to_string()),
                    ..TaskControlResult::default()
                };
            };
            if end {
                active.stdin = None;
            }
            stdin
        };
        let input = if text.is_empty() { "\n" } else { text };
        let write_result = {
            let mut handle = stdin
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            handle
                .write_all(input.as_bytes())
                .and_then(|_| handle.flush())
        };
        if let Err(error) = write_result {
            let mut state = self.lock_state();
            if let Some(active) = state.active.as_mut().filter(|active| active.token == token) {
                if active
                    .stdin
                    .as_ref()
                    .is_some_and(|current| Arc::ptr_eq(current, &stdin))
                {
                    active.stdin = None;
                }
            }
            return TaskControlResult {
                success: false,
                error: Some(error.to_string()),
                ..TaskControlResult::default()
            };
        }
        TaskControlResult {
            success: true,
            ..TaskControlResult::default()
        }
    }

    fn lock_state(&self) -> MutexGuard<'_, RuntimeState> {
        self.inner
            .state
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    fn rollback_stopping(&self, token: u64) -> Option<TaskProcessState> {
        let mut state = self.lock_state();
        let active = state
            .active
            .as_mut()
            .filter(|active| active.token == token && active.stopping)?;
        active.stopping = false;
        Some(state_from_active(state.active.as_ref(), None))
    }
}

/// Builds the complete environment passed to `Command::envs`.
///
/// Environment precedence, from lowest to highest, is inherited host values,
/// Wandao defaults, task identifiers, secret values, caller extras, and plugin
/// isolation values. Plugin identity/data paths and Python isolation therefore
/// cannot be overridden by a caller-provided argument.
pub fn build_execution_environment(
    context: &TaskExecutionContext,
    stop_file: &Path,
) -> BTreeMap<String, String> {
    let mut environment = inherited_environment(context.plugin.is_some());
    environment.insert("PYTHONIOENCODING".into(), "utf-8".into());
    environment.insert("PYTHONUNBUFFERED".into(), "1".into());
    environment.insert("PYTHONUTF8".into(), "1".into());
    environment.insert("WANDAO_STRUCTURED_LOGS".into(), "1".into());
    environment.insert(
        "WANDAO_DATA_DIR".into(),
        context.user_data_dir.to_string_lossy().into_owned(),
    );
    environment.insert("WANDAO_TASK_ID".into(), effective_run_id(context));
    environment.insert("WANDAO_RUN_ID".into(), effective_run_id(context));
    environment.insert("WANDAO_JOB_ID".into(), context.job_id.clone());
    environment.insert("WANDAO_PARENT_RUN_ID".into(), context.parent_run_id.clone());
    environment.insert("WANDAO_PROVIDER_ID".into(), context.provider_id.clone());
    environment.insert(
        "WANDAO_STOP_FILE".into(),
        stop_file.to_string_lossy().into_owned(),
    );

    if let Some(browser_path) = context.browser_path.as_ref() {
        environment.insert(
            "WANDAO_BROWSER".into(),
            browser_path.to_string_lossy().into_owned(),
        );
    }
    if let Some(runtime) = context.python_runtime.as_ref() {
        environment.insert(
            "WANDAO_PYTHON_RUNTIME".into(),
            runtime.to_string_lossy().into_owned(),
        );
        prepend_runtime_to_path(&mut environment, runtime);
        environment.insert("PYTHONNOUSERSITE".into(), "1".into());
    }

    let mut python_paths = Vec::new();
    if let Some(plugin) = context.plugin.as_ref() {
        python_paths.push(plugin.plugin_root.clone());
        if let Some(library) = context.python_library_dir.as_ref() {
            python_paths.push(library.clone());
        }
    } else {
        if let Some(library) = context.python_library_dir.as_ref() {
            python_paths.push(library.clone());
        }
        python_paths.extend(context.additional_python_paths.iter().cloned());
        if let Some(existing) = std::env::var_os("PYTHONPATH") {
            if !existing.is_empty() {
                python_paths.extend(std::env::split_paths(&existing));
            }
        }
    }
    let python_path = std::env::join_paths(python_paths)
        .ok()
        .filter(|value| !value.is_empty())
        .map(|value| value.to_string_lossy().into_owned());
    if context.plugin.is_none() {
        if let Some(value) = python_path.as_ref() {
            environment.insert("PYTHONPATH".into(), value.clone());
        }
    }

    environment.extend(context.secret_environment.clone());
    environment.extend(context.extra_environment.clone());

    if let Some(plugin) = context.plugin.as_ref() {
        environment.retain(|key, _| {
            !matches!(
                key.to_ascii_uppercase().as_str(),
                "PYTHONPATH" | "PYTHONDONTWRITEBYTECODE" | "PYTHONNOUSERSITE"
            )
        });
        if let Some(value) = python_path {
            environment.insert("PYTHONPATH".into(), value);
        }
        environment.insert("PYTHONDONTWRITEBYTECODE".into(), "1".into());
        environment.insert("PYTHONNOUSERSITE".into(), "1".into());
        environment.insert("WANDAO_PLUGIN_ID".into(), plugin.plugin_id.clone());
        environment.insert(
            "WANDAO_PLUGIN_VERSION".into(),
            plugin.plugin_version.clone(),
        );
        environment.insert(
            "WANDAO_PLUGIN_ROOT".into(),
            plugin.plugin_root.to_string_lossy().into_owned(),
        );
        environment.insert(
            "WANDAO_PLUGIN_DATA_DIR".into(),
            plugin.plugin_data_dir.to_string_lossy().into_owned(),
        );
        environment.insert(
            "WANDAO_DATA_DIR".into(),
            plugin.plugin_data_dir.to_string_lossy().into_owned(),
        );
        environment.insert(
            "WANDAO_PLUGIN_PERMISSIONS".into(),
            serde_json::to_string(&plugin.permissions).unwrap_or_else(|_| "[]".into()),
        );
    }
    environment
}

fn inherited_environment(plugin_isolated: bool) -> BTreeMap<String, String> {
    if !plugin_isolated {
        return std::env::vars().collect();
    }
    let allowlist = PLUGIN_ENV_ALLOWLIST.iter().copied().collect::<HashSet<_>>();
    std::env::vars()
        .filter(|(key, _)| allowlist.contains(key.to_ascii_uppercase().as_str()))
        .collect()
}

fn effective_run_id(context: &TaskExecutionContext) -> String {
    if context.run_id.is_empty() {
        context.task_id.clone()
    } else {
        context.run_id.clone()
    }
}

fn prepend_runtime_to_path(environment: &mut BTreeMap<String, String>, runtime: &Path) {
    let bin_dir = if cfg!(windows) {
        runtime.to_path_buf()
    } else {
        runtime.join("bin")
    };
    let scripts_dir = if cfg!(windows) {
        runtime.join("Scripts")
    } else {
        runtime.join("bin")
    };
    let mut paths = vec![bin_dir, scripts_dir];
    if let Some(existing) = environment.get("PATH") {
        paths.extend(std::env::split_paths(existing));
    }
    if let Ok(value) = std::env::join_paths(paths) {
        environment.insert("PATH".into(), value.to_string_lossy().into_owned());
    }
}

fn default_stop_file(user_data_dir: &Path, task_id: &str) -> PathBuf {
    let id = if task_id.is_empty() {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis()
            .to_string()
    } else {
        task_id
            .chars()
            .map(|character| {
                if character.is_ascii_alphanumeric() || matches!(character, '-' | '_') {
                    character
                } else {
                    '_'
                }
            })
            .collect()
    };
    user_data_dir
        .join("runtime")
        .join("stops")
        .join(format!("{id}.stop"))
}

fn state_from_active(
    active: Option<&ActiveProcess>,
    last_status: Option<TaskLastStatus>,
) -> TaskProcessState {
    match active {
        Some(active) => TaskProcessState {
            running: true,
            stopping: active.stopping,
            provider_id: active.provider_id.clone(),
            task_id: active.task_id.clone(),
            started_at: active.started_at.clone(),
            last_status,
        },
        None => TaskProcessState {
            last_status,
            ..TaskProcessState::default()
        },
    }
}

fn emit(sink: &TaskEventSink, event: TaskRuntimeEvent) {
    sink(event);
}

fn remove_file_best_effort(path: &Path) {
    match fs::remove_file(path) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(_) => {}
    }
}

fn remove_stale_stop_file(path: &Path) -> std::io::Result<()> {
    match fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}

fn configure_process_group(command: &mut Command) {
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
}

fn process_target_for_child(child: &Child) -> std::io::Result<ProcessTarget> {
    #[cfg(windows)]
    {
        Ok(ProcessTarget {
            pid: child.id(),
            job: Some(Arc::new(WindowsJob::for_child(child)?)),
        })
    }
    #[cfg(not(windows))]
    {
        Ok(ProcessTarget { pid: child.id() })
    }
}

fn terminate_process_tree(target: &ProcessTarget, force: bool) -> bool {
    #[cfg(windows)]
    {
        let _ = force;
        if target.job.as_ref().is_some_and(|job| job.terminate(130)) {
            return true;
        }
        Command::new("taskkill")
            .args(["/pid", &target.pid.to_string(), "/T", "/F"])
            .creation_flags_for_helper()
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .is_ok_and(|status| status.success())
    }
    #[cfg(unix)]
    {
        let signal = if force { "-KILL" } else { "-TERM" };
        Command::new("/bin/kill")
            .args([signal, &format!("-{}", target.pid)])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .is_ok_and(|status| status.success())
    }
    #[cfg(not(any(windows, unix)))]
    {
        let _ = (target, force);
        false
    }
}

#[cfg(windows)]
struct WindowsJob {
    handle: isize,
}

#[cfg(windows)]
impl WindowsJob {
    fn for_child(child: &Child) -> std::io::Result<Self> {
        use std::os::windows::io::AsRawHandle;
        use windows_sys::Win32::System::JobObjects::{
            AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
            SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        };

        let handle = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
        if handle.is_null() {
            return Err(std::io::Error::last_os_error());
        }
        let job = Self {
            handle: handle as isize,
        };
        let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        let configured = unsafe {
            SetInformationJobObject(
                job.raw_handle(),
                JobObjectExtendedLimitInformation,
                (&limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
                std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            )
        };
        if configured == 0 {
            return Err(std::io::Error::last_os_error());
        }
        let assigned =
            unsafe { AssignProcessToJobObject(job.raw_handle(), child.as_raw_handle().cast()) };
        if assigned == 0 {
            return Err(std::io::Error::last_os_error());
        }
        Ok(job)
    }

    fn terminate(&self, exit_code: u32) -> bool {
        use windows_sys::Win32::System::JobObjects::TerminateJobObject;

        unsafe { TerminateJobObject(self.raw_handle(), exit_code) != 0 }
    }

    fn raw_handle(&self) -> windows_sys::Win32::Foundation::HANDLE {
        self.handle as windows_sys::Win32::Foundation::HANDLE
    }
}

#[cfg(windows)]
impl Drop for WindowsJob {
    fn drop(&mut self) {
        use windows_sys::Win32::Foundation::CloseHandle;

        unsafe {
            CloseHandle(self.raw_handle());
        }
    }
}

#[cfg(windows)]
trait WindowsHelperCommand {
    fn creation_flags_for_helper(&mut self) -> &mut Self;
}

#[cfg(windows)]
impl WindowsHelperCommand for Command {
    fn creation_flags_for_helper(&mut self) -> &mut Self {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        self.creation_flags(CREATE_NO_WINDOW)
    }
}

fn spawn_output_reader<R>(
    mut reader: R,
    stream: TaskOutputStream,
    output: Arc<Mutex<CappedOutput>>,
    sink: TaskEventSink,
    suppress_terminal_json: bool,
) -> thread::JoinHandle<()>
where
    R: Read + Send + 'static,
{
    thread::spawn(move || {
        let mut buffer = [0_u8; 16 * 1024];
        let mut raw_text = IncrementalUtf8Decoder::default();
        let mut structured_lines = StructuredLineDecoder::default();
        let mut scan_relay = suppress_terminal_json.then(ScanStdoutRelay::default);
        loop {
            match reader.read(&mut buffer) {
                Ok(0) => {
                    break;
                }
                Ok(count) => {
                    let bytes = &buffer[..count];
                    output
                        .lock()
                        .unwrap_or_else(|poisoned| poisoned.into_inner())
                        .append(bytes);
                    let text = raw_text.push(bytes);
                    if !text.is_empty() {
                        if let Some(relay) = scan_relay.as_mut() {
                            for text in relay.push(&text) {
                                emit(&sink, TaskRuntimeEvent::Output { stream, text });
                            }
                        } else {
                            emit(&sink, TaskRuntimeEvent::Output { stream, text });
                        }
                    }
                    structured_lines.push(bytes, stream, &sink);
                }
                Err(error) if error.kind() == std::io::ErrorKind::Interrupted => {
                    continue;
                }
                Err(error) => {
                    emit(
                        &sink,
                        TaskRuntimeEvent::Diagnostic {
                            level: DiagnosticLevel::Warn,
                            message: format!("任务输出读取失败：{error}"),
                        },
                    );
                    break;
                }
            }
        }
        let text = raw_text.flush();
        if !text.is_empty() {
            if let Some(relay) = scan_relay.as_mut() {
                for text in relay.push(&text) {
                    emit(&sink, TaskRuntimeEvent::Output { stream, text });
                }
            } else {
                emit(&sink, TaskRuntimeEvent::Output { stream, text });
            }
        }
        if let Some(relay) = scan_relay.as_mut() {
            for text in relay.flush() {
                emit(&sink, TaskRuntimeEvent::Output { stream, text });
            }
        }
        structured_lines.flush(stream, &sink);
    })
}

#[derive(Default)]
struct IncrementalUtf8Decoder {
    pending: Vec<u8>,
}

impl IncrementalUtf8Decoder {
    fn push(&mut self, bytes: &[u8]) -> String {
        self.pending.extend_from_slice(bytes);
        let mut output = String::new();
        loop {
            match std::str::from_utf8(&self.pending) {
                Ok(text) => {
                    output.push_str(text);
                    self.pending.clear();
                    break;
                }
                Err(error) => {
                    let valid = error.valid_up_to();
                    if valid > 0 {
                        output.push_str(
                            std::str::from_utf8(&self.pending[..valid])
                                .expect("UTF-8 validator reported a valid prefix"),
                        );
                    }
                    let Some(invalid) = error.error_len() else {
                        if valid > 0 {
                            self.pending.drain(..valid);
                        }
                        break;
                    };
                    output.push('\u{fffd}');
                    self.pending.drain(..valid + invalid);
                }
            }
        }
        output
    }

    fn flush(&mut self) -> String {
        let output = String::from_utf8_lossy(&self.pending).into_owned();
        self.pending.clear();
        output
    }
}

#[derive(Default)]
struct ScanStdoutRelay {
    pending: String,
    candidate: String,
}

impl ScanStdoutRelay {
    fn push(&mut self, text: &str) -> Vec<String> {
        if !self.candidate.is_empty() {
            self.candidate.push_str(text);
            return Vec::new();
        }
        self.pending.push_str(text);
        let mut output = Vec::new();
        while let Some(newline) = self.pending.find('\n') {
            let remainder = self.pending.split_off(newline + 1);
            let line = std::mem::replace(&mut self.pending, remainder);
            if is_terminal_json_object_start(&line) {
                self.candidate.push_str(&line);
                self.candidate.push_str(&self.pending);
                self.pending.clear();
                break;
            }
            output.push(line);
        }
        output
    }

    fn flush(&mut self) -> Vec<String> {
        if !self.candidate.is_empty() {
            let candidate = std::mem::take(&mut self.candidate);
            if serde_json::from_str::<Value>(candidate.trim()).is_ok_and(|value| value.is_object())
            {
                return Vec::new();
            }
            return vec![candidate];
        }
        if self.pending.is_empty() {
            Vec::new()
        } else {
            vec![std::mem::take(&mut self.pending)]
        }
    }
}

fn is_terminal_json_object_start(line: &str) -> bool {
    let trimmed = line.trim_end_matches(['\r', '\n']).trim_start();
    let Some(remainder) = trimmed.strip_prefix('{') else {
        return false;
    };
    remainder.is_empty()
        || remainder.starts_with('"')
        || remainder.chars().next().is_some_and(char::is_whitespace)
}

#[derive(Default)]
struct StructuredLineDecoder {
    pending: Vec<u8>,
    discarding_line: bool,
}

impl StructuredLineDecoder {
    fn push(&mut self, bytes: &[u8], stream: TaskOutputStream, sink: &TaskEventSink) {
        let prefix = STRUCTURED_LOG_PREFIX.as_bytes();
        for byte in bytes {
            if self.discarding_line {
                if *byte == b'\n' {
                    self.discarding_line = false;
                }
                continue;
            }
            self.pending.push(*byte);
            if self.pending.len() <= prefix.len() && !prefix.starts_with(&self.pending) {
                let is_newline = *byte == b'\n';
                self.pending.clear();
                self.discarding_line = !is_newline;
                continue;
            }
            if *byte == b'\n' {
                let line = std::mem::take(&mut self.pending);
                self.process_line(&line, stream, sink);
                continue;
            }
            if self.pending.len() > MAX_STRUCTURED_LOG_LINE_BYTES {
                self.pending.clear();
                self.discarding_line = true;
                emit(
                    sink,
                    TaskRuntimeEvent::Diagnostic {
                        level: DiagnosticLevel::Warn,
                        message: "单条结构化任务日志超过 1 MiB，已停止解析该行。".to_string(),
                    },
                );
            }
        }
    }

    fn flush(&mut self, stream: TaskOutputStream, sink: &TaskEventSink) {
        if self.discarding_line {
            self.pending.clear();
            return;
        }
        if !self.pending.is_empty() {
            let line = std::mem::take(&mut self.pending);
            self.process_line(&line, stream, sink);
        }
    }

    fn process_line(&self, bytes: &[u8], stream: TaskOutputStream, sink: &TaskEventSink) {
        let raw = String::from_utf8_lossy(bytes)
            .trim_end_matches(['\r', '\n'])
            .to_string();
        let Some(payload) = raw.strip_prefix(STRUCTURED_LOG_PREFIX) else {
            return;
        };
        match serde_json::from_str::<Value>(payload) {
            Ok(payload) => emit(
                sink,
                TaskRuntimeEvent::StructuredLog {
                    stream,
                    payload,
                    raw,
                },
            ),
            Err(error) => emit(
                sink,
                TaskRuntimeEvent::Diagnostic {
                    level: DiagnosticLevel::Warn,
                    message: format!("结构化任务日志无法解析：{error}"),
                },
            ),
        }
    }
}

struct CappedOutput {
    bytes: Vec<u8>,
    start: usize,
    omitted: u64,
    limit: usize,
}

impl CappedOutput {
    fn new(limit: usize) -> Self {
        Self {
            bytes: Vec::new(),
            start: 0,
            omitted: 0,
            limit,
        }
    }

    fn append(&mut self, chunk: &[u8]) {
        self.bytes.extend_from_slice(chunk);
        let retained = self.bytes.len().saturating_sub(self.start);
        if retained > self.limit {
            let excess = retained - self.limit;
            self.start += excess;
            self.omitted += excess as u64;
        }
        if self.start >= 1024 * 1024 {
            self.bytes.drain(..self.start);
            self.start = 0;
        }
    }

    fn snapshot(&self) -> OutputSnapshot {
        OutputSnapshot {
            text: String::from_utf8_lossy(&self.bytes[self.start..]).into_owned(),
            omitted: self.omitted,
        }
    }
}

struct OutputSnapshot {
    text: String,
    omitted: u64,
}

fn parse_exit_result(
    status: ExitStatus,
    was_stopping: bool,
    stdout: &OutputSnapshot,
    stderr: &OutputSnapshot,
) -> TaskRunResult {
    if was_stopping {
        return stopped_result(parse_last_json(&stdout.text));
    }
    if status.success() {
        return match parse_process_result(&stdout.text) {
            Ok((data, legacy)) => TaskRunResult {
                success: true,
                error: None,
                code: None,
                data: Some(data),
                legacy_result: Some(legacy),
            },
            Err(error) => TaskRunResult {
                success: false,
                error: Some(error),
                code: Some(TaskExitCode::Label("protocol_error".into())),
                data: None,
                legacy_result: None,
            },
        };
    }

    let code = status.code().unwrap_or(-1);
    let error = if !stderr.text.is_empty() {
        output_with_omission_notice(stderr)
    } else if !stdout.text.is_empty() {
        output_with_omission_notice(stdout)
    } else {
        format!("Python exited with code {code}")
    };
    TaskRunResult {
        success: false,
        error: Some(error),
        code: Some(TaskExitCode::Number(code)),
        data: meaningful_json(parse_last_json(&stdout.text)),
        legacy_result: None,
    }
}

fn stopped_result(parsed: Option<Value>) -> TaskRunResult {
    let mut data = match meaningful_json(parsed) {
        Some(Value::Object(object)) => object,
        _ => Map::new(),
    };
    data.insert("stopped".into(), Value::Bool(true));
    TaskRunResult {
        success: false,
        error: Some("任务已由用户停止。".into()),
        code: Some(TaskExitCode::Number(130)),
        data: Some(Value::Object(data)),
        legacy_result: None,
    }
}

fn output_with_omission_notice(output: &OutputSnapshot) -> String {
    if output.omitted == 0 {
        output.text.clone()
    } else {
        format!(
            "[前部 {} 个字节已省略，以下为输出尾部]\n{}",
            output.omitted, output.text
        )
    }
}

pub fn parse_last_json(stdout: &str) -> Option<Value> {
    let lines = stdout
        .lines()
        .filter(|line| !line.starts_with(STRUCTURED_LOG_PREFIX))
        .collect::<Vec<_>>();
    let combined = lines.join("\n");
    let trimmed = combined.trim();
    if trimmed.is_empty() {
        return None;
    }
    if let Ok(value) = serde_json::from_str(trimmed) {
        return Some(value);
    }
    for index in (0..lines.len()).rev() {
        let line = lines[index];
        if !line.starts_with('{') && !line.starts_with('[') {
            continue;
        }
        let candidate = lines[index..].join("\n");
        if let Ok(value) = serde_json::from_str(candidate.trim()) {
            return Some(value);
        }
    }
    None
}

pub fn parse_process_result(stdout: &str) -> Result<(Value, bool), String> {
    let parsed = parse_last_json(stdout)
        .ok_or_else(|| "任务进程已正常退出，但没有输出合法的 JSON 结果。".to_string())?;
    normalize_process_result(parsed)
}

pub fn normalize_process_result(value: Value) -> Result<(Value, bool), String> {
    let Value::Object(mut object) = value else {
        return Err("任务结果必须是 JSON 对象。".into());
    };
    if object.get("kind").and_then(Value::as_str) == Some(TASK_RESULT_KIND) {
        if object.get("schemaVersion").and_then(Value::as_u64) != Some(TASK_RESULT_SCHEMA_VERSION) {
            let version = object
                .get("schemaVersion")
                .map(Value::to_string)
                .unwrap_or_else(|| "undefined".into());
            return Err(format!("不支持的 TaskResult schemaVersion：{version}"));
        }
        return Ok((Value::Object(object), false));
    }
    object.insert("kind".into(), Value::String("wandao.legacy-result".into()));
    object.insert("schemaVersion".into(), Value::Number((0).into()));
    Ok((Value::Object(object), true))
}

fn meaningful_json(value: Option<Value>) -> Option<Value> {
    value.and_then(|value| match &value {
        Value::Object(object) if object.is_empty() => None,
        Value::Array(items) if items.is_empty() => None,
        Value::Null => None,
        _ => Some(value),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::VecDeque;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::mpsc;
    use std::time::Instant;

    fn temporary_directory(label: &str) -> PathBuf {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        std::env::temp_dir().join(format!(
            "wandao-task-{label}-{}-{unique}",
            std::process::id()
        ))
    }

    fn self_test_request(
        test_name: &str,
        user_data_dir: PathBuf,
        extra_environment: BTreeMap<String, String>,
    ) -> TaskRunRequest {
        TaskRunRequest {
            executable: std::env::current_exe().unwrap(),
            script: PathBuf::from(test_name),
            args: vec!["--exact".into(), "--nocapture".into()],
            working_directory: Some(std::env::current_dir().unwrap()),
            context: TaskExecutionContext {
                user_data_dir,
                provider_id: "test-provider".into(),
                task_id: "test-task".into(),
                run_id: String::new(),
                job_id: String::new(),
                parent_run_id: String::new(),
                started_at: "2026-07-28T00:00:00Z".into(),
                browser_path: None,
                python_runtime: None,
                python_library_dir: None,
                additional_python_paths: Vec::new(),
                plugin: None,
                secret_environment: BTreeMap::new(),
                extra_environment,
            },
            stop_file: None,
            stdin_text: None,
            close_stdin_after_initial_input: false,
        }
    }

    fn fake_process_target(pid: u32) -> ProcessTarget {
        ProcessTarget {
            pid,
            #[cfg(windows)]
            job: None,
        }
    }

    fn install_fake_active(runtime: &TaskRuntime, stop_file: PathBuf) {
        let mut state = runtime.lock_state();
        state.next_token = state.next_token.wrapping_add(1).max(1);
        state.active = Some(ActiveProcess {
            token: state.next_token,
            target: fake_process_target(u32::MAX),
            stopping: false,
            stop_file,
            stdin: None,
            provider_id: "test-provider".into(),
            task_id: "test-task".into(),
            started_at: "2026-07-28T00:00:00Z".into(),
        });
    }

    fn clear_fake_active(runtime: &TaskRuntime) {
        runtime.lock_state().active = None;
        runtime.inner.idle.notify_all();
    }

    fn capturing_sink() -> (TaskEventSink, Arc<Mutex<Vec<TaskRuntimeEvent>>>) {
        let events = Arc::new(Mutex::new(Vec::new()));
        let captured = Arc::clone(&events);
        let sink = Arc::new(move |event| {
            captured
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner())
                .push(event);
        });
        (sink, events)
    }

    #[test]
    fn plugin_environment_uses_only_verified_python_roots_and_disables_bytecode() {
        let temporary = temporary_directory("plugin-environment");
        let plugin_root = temporary.join("plugin");
        let core_library = temporary.join("core");
        let untrusted = temporary.join("host-python-path");
        let mut secret_environment = BTreeMap::new();
        secret_environment.insert("PYTHONDONTWRITEBYTECODE".into(), "0".into());
        let mut extra_environment = BTreeMap::new();
        extra_environment.insert(
            "PYTHONPATH".into(),
            std::env::join_paths([&untrusted])
                .unwrap()
                .to_string_lossy()
                .into_owned(),
        );
        extra_environment.insert("PythonPath".into(), "case-variant-injection".into());
        extra_environment.insert("PYTHONNOUSERSITE".into(), "0".into());
        let context = TaskExecutionContext {
            user_data_dir: temporary.join("user-data"),
            provider_id: "plugin-provider".into(),
            task_id: "plugin-task".into(),
            run_id: String::new(),
            job_id: String::new(),
            parent_run_id: String::new(),
            started_at: "2026-07-28T00:00:00Z".into(),
            browser_path: None,
            python_runtime: None,
            python_library_dir: Some(core_library.clone()),
            additional_python_paths: vec![untrusted.clone()],
            plugin: Some(PluginExecutionContext {
                plugin_id: "signed-test".into(),
                plugin_version: "1.0.0".into(),
                plugin_root: plugin_root.clone(),
                plugin_data_dir: temporary.join("plugin-data"),
                permissions: vec!["process".into()],
            }),
            secret_environment,
            extra_environment,
        };

        let environment = build_execution_environment(&context, &temporary.join("stop"));
        let python_paths: Vec<PathBuf> = std::env::split_paths(
            environment
                .get("PYTHONPATH")
                .expect("isolated plugin PYTHONPATH"),
        )
        .collect();
        assert_eq!(python_paths, vec![plugin_root, core_library]);
        assert!(!python_paths.contains(&untrusted));
        assert_eq!(
            environment
                .get("PYTHONDONTWRITEBYTECODE")
                .map(String::as_str),
            Some("1")
        );
        assert_eq!(
            environment.get("PYTHONNOUSERSITE").map(String::as_str),
            Some("1")
        );
        assert_eq!(
            environment
                .keys()
                .filter(|key| key.eq_ignore_ascii_case("PYTHONPATH"))
                .count(),
            1
        );
    }

    struct ChunkedReader {
        chunks: VecDeque<Vec<u8>>,
    }

    impl Read for ChunkedReader {
        fn read(&mut self, buffer: &mut [u8]) -> std::io::Result<usize> {
            let Some(chunk) = self.chunks.front_mut() else {
                return Ok(0);
            };
            let count = chunk.len().min(buffer.len());
            buffer[..count].copy_from_slice(&chunk[..count]);
            chunk.drain(..count);
            if chunk.is_empty() {
                self.chunks.pop_front();
            }
            Ok(count)
        }
    }

    #[test]
    fn parses_last_json_after_logs_and_ignores_structured_lines() {
        let stdout = concat!(
            "普通日志\n",
            "@@WANDAO_LOG@@{\"event\":\"task.progress\"}\n",
            "{\n  \"success\": true,\n  \"count\": 3\n}\n"
        );
        assert_eq!(
            parse_last_json(stdout),
            Some(serde_json::json!({"success": true, "count": 3}))
        );
    }

    #[test]
    fn normalizes_legacy_result_without_losing_fields() {
        let (value, legacy) =
            normalize_process_result(serde_json::json!({"output": "done"})).unwrap();
        assert!(legacy);
        assert_eq!(value["output"], "done");
        assert_eq!(value["kind"], "wandao.legacy-result");
        assert_eq!(value["schemaVersion"], 0);
    }

    #[test]
    fn rejects_unknown_task_result_schema() {
        let error = normalize_process_result(serde_json::json!({
            "kind": "wandao.result",
            "schemaVersion": 2
        }))
        .unwrap_err();
        assert!(error.contains("schemaVersion"));
    }

    #[test]
    fn capped_output_keeps_tail_and_reports_omission() {
        let mut output = CappedOutput::new(5);
        output.append(b"1234");
        output.append(b"56789");
        let snapshot = output.snapshot();
        assert_eq!(snapshot.text, "56789");
        assert_eq!(snapshot.omitted, 4);
    }

    #[test]
    fn stop_file_name_cannot_escape_runtime_directory() {
        let file = default_stop_file(Path::new("data"), "../../unsafe/task");
        assert_eq!(
            file,
            Path::new("data")
                .join("runtime")
                .join("stops")
                .join("______unsafe_task.stop")
        );
    }

    #[test]
    fn scan_relay_forwards_progress_and_suppresses_terminal_json() {
        let mut relay = ScanStdoutRelay::default();
        assert_eq!(
            relay.push("Loaded credentials\nReading directory\n{\n  \"ordered\": ["),
            vec![
                "Loaded credentials\n".to_string(),
                "Reading directory\n".to_string()
            ]
        );
        assert!(relay.push("]\n}\n").is_empty());
        assert!(relay.flush().is_empty());
    }

    #[test]
    fn scan_relay_preserves_json_looking_log_when_diagnostics_follow() {
        let mut relay = ScanStdoutRelay::default();
        assert!(relay.push("{\"event\":\"scan-progress\"}\n").is_empty());
        assert!(relay.push("Directory scan failed\n").is_empty());
        assert_eq!(
            relay.flush(),
            vec!["{\"event\":\"scan-progress\"}\nDirectory scan failed\n".to_string()]
        );
    }

    #[test]
    fn large_plain_json_does_not_report_structured_log_overflow() {
        let events = Arc::new(Mutex::new(Vec::new()));
        let captured = Arc::clone(&events);
        let sink: TaskEventSink = Arc::new(move |event| {
            captured
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner())
                .push(event);
        });
        let mut decoder = StructuredLineDecoder::default();
        let line = format!("{{\"content\":\"{}\"}}\n", "x".repeat(1024 * 1024 + 100));
        decoder.push(line.as_bytes(), TaskOutputStream::Stdout, &sink);
        decoder.flush(TaskOutputStream::Stdout, &sink);
        assert!(events
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .is_empty());
    }

    #[test]
    fn output_reader_preserves_utf8_split_across_chunks_and_structured_events() {
        let mut structured_start = format!("{STRUCTURED_LOG_PREFIX}{{\"message\":\"").into_bytes();
        structured_start.push(0xe4);
        let reader = ChunkedReader {
            chunks: VecDeque::from([
                b"plain ".to_vec(),
                vec![0xe4],
                vec![0xb8, 0xad, b'\n'],
                structured_start,
                vec![0xb8, 0xad, b'"', b'}', b'\n'],
            ]),
        };
        let output = Arc::new(Mutex::new(CappedOutput::new(4096)));
        let (sink, events) = capturing_sink();
        spawn_output_reader(
            reader,
            TaskOutputStream::Stdout,
            Arc::clone(&output),
            sink,
            false,
        )
        .join()
        .unwrap();

        let events = events
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let raw_output = events
            .iter()
            .filter_map(|event| match event {
                TaskRuntimeEvent::Output { text, .. } => Some(text.as_str()),
                _ => None,
            })
            .collect::<String>();
        assert_eq!(
            raw_output,
            format!("plain 中\n{STRUCTURED_LOG_PREFIX}{{\"message\":\"中\"}}\n")
        );
        assert!(!raw_output.contains('\u{fffd}'));
        assert!(events.iter().any(|event| matches!(
            event,
            TaskRuntimeEvent::StructuredLog { payload, raw, .. }
                if payload["message"] == "中"
                    && raw == &format!("{STRUCTURED_LOG_PREFIX}{{\"message\":\"中\"}}")
        )));
    }

    #[test]
    fn active_task_rejection_does_not_delete_its_stop_marker() {
        let temporary = temporary_directory("active-marker");
        let marker = temporary
            .join("runtime")
            .join("stops")
            .join("test-task.stop");
        fs::create_dir_all(marker.parent().unwrap()).unwrap();
        fs::write(&marker, b"stop").unwrap();
        let runtime = TaskRuntime::default();
        install_fake_active(&runtime, marker.clone());
        let mut request = self_test_request(
            "tasks::tests::active_task_rejection_does_not_delete_its_stop_marker",
            temporary.clone(),
            BTreeMap::new(),
        );
        request.stop_file = Some(marker.clone());

        let result = runtime.run(request, Arc::new(|_| {}));

        assert!(!result.success);
        assert!(result.error.unwrap().contains("已有任务"));
        assert_eq!(fs::read(&marker).unwrap(), b"stop");
        clear_fake_active(&runtime);
        let _ = fs::remove_dir_all(temporary);
    }

    #[test]
    fn stale_stop_marker_removal_error_aborts_before_spawn() {
        let temporary = temporary_directory("marker-error");
        let marker = temporary.join("marker-is-a-directory");
        fs::create_dir_all(&marker).unwrap();
        let runtime = TaskRuntime::default();
        let mut request = self_test_request(
            "tasks::tests::stale_stop_marker_removal_error_aborts_before_spawn",
            temporary.clone(),
            BTreeMap::new(),
        );
        request.stop_file = Some(marker.clone());

        let result = runtime.run(request, Arc::new(|_| {}));

        assert!(!result.success);
        assert!(result.error.unwrap().contains("停止标记"));
        assert!(!runtime.state().running);
        assert!(marker.is_dir());
        let _ = fs::remove_dir_all(temporary);
    }

    #[test]
    fn readers_start_before_large_initial_stdin_is_written() {
        const CHILD_MARKER: &str = "WANDAO_TASK_RUNTIME_LARGE_IO_CHILD";
        const BYTES: usize = 2 * 1024 * 1024;
        if std::env::var_os(CHILD_MARKER).is_some() {
            let mut stdout = std::io::stdout().lock();
            stdout.write_all(&vec![b'x'; BYTES]).unwrap();
            stdout.write_all(b"\n").unwrap();
            stdout.flush().unwrap();
            let mut input = Vec::new();
            std::io::stdin().read_to_end(&mut input).unwrap();
            writeln!(stdout, "{{\"received\":{}}}", input.len()).unwrap();
            stdout.flush().unwrap();
            std::process::exit(0);
        }

        let temporary = temporary_directory("large-io");
        let runtime = TaskRuntime::new(64 * 1024, Duration::from_secs(1));
        let runtime_for_thread = runtime.clone();
        let mut environment = BTreeMap::new();
        environment.insert(CHILD_MARKER.into(), "1".into());
        let mut request = self_test_request(
            "tasks::tests::readers_start_before_large_initial_stdin_is_written",
            temporary.clone(),
            environment,
        );
        request.stdin_text = Some("y".repeat(BYTES));
        request.close_stdin_after_initial_input = true;
        let (sender, receiver) = mpsc::channel();
        thread::spawn(move || {
            sender
                .send(runtime_for_thread.run(request, Arc::new(|_| {})))
                .unwrap();
        });

        let result = match receiver.recv_timeout(Duration::from_secs(15)) {
            Ok(result) => result,
            Err(error) => {
                let _ = runtime.force_stop();
                let _ = runtime.wait_until_idle(Duration::from_secs(5));
                panic!("large bidirectional pipe transfer deadlocked: {error}");
            }
        };
        assert!(result.success, "{:?}", result.error);
        assert_eq!(result.data.unwrap()["received"], BYTES);
        let _ = fs::remove_dir_all(temporary);
    }

    #[test]
    fn failed_marker_and_terminator_roll_back_and_emit_state() {
        let calls = Arc::new(AtomicUsize::new(0));
        let called = Arc::clone(&calls);
        let runtime = TaskRuntime::with_terminator(1024, Duration::from_millis(10), move |_, _| {
            called.fetch_add(1, Ordering::SeqCst);
            false
        });
        let temporary = temporary_directory("stop-failure");
        fs::create_dir_all(&temporary).unwrap();
        let blocked_parent = temporary.join("not-a-directory");
        fs::write(&blocked_parent, b"file").unwrap();
        install_fake_active(&runtime, blocked_parent.join("stop.marker"));
        let (sink, events) = capturing_sink();

        let result = runtime.request_stop(sink);

        assert!(!result.success);
        assert!(!result.stopping);
        assert!(!runtime.state().stopping);
        assert_eq!(calls.load(Ordering::SeqCst), 1);
        let events = events
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        assert!(events.iter().any(|event| matches!(
            event,
            TaskRuntimeEvent::Diagnostic {
                level: DiagnosticLevel::Error,
                ..
            }
        )));
        assert!(matches!(
            events.last(),
            Some(TaskRuntimeEvent::State {
                state: TaskProcessState {
                    stopping: false,
                    ..
                }
            })
        ));
        drop(events);
        clear_fake_active(&runtime);
        let _ = fs::remove_dir_all(temporary);
    }

    #[test]
    fn delayed_force_failure_rolls_back_reports_and_allows_retry() {
        let calls = Arc::new(AtomicUsize::new(0));
        let called = Arc::clone(&calls);
        let runtime = TaskRuntime::with_terminator(1024, Duration::from_millis(20), move |_, _| {
            called.fetch_add(1, Ordering::SeqCst);
            false
        });
        let temporary = temporary_directory("delayed-stop-failure");
        let marker = temporary.join("stop.marker");
        install_fake_active(&runtime, marker);
        let (sink, events) = capturing_sink();
        let first = runtime.request_stop(sink);
        assert!(first.success && first.cooperative && first.stopping);

        let deadline = Instant::now() + Duration::from_secs(2);
        while (calls.load(Ordering::SeqCst) == 0 || runtime.state().stopping)
            && Instant::now() < deadline
        {
            thread::sleep(Duration::from_millis(5));
        }
        assert_eq!(calls.load(Ordering::SeqCst), 1);
        assert!(!runtime.state().stopping);
        assert!(events
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .iter()
            .any(|event| matches!(
                event,
                TaskRuntimeEvent::Diagnostic {
                    level: DiagnosticLevel::Error,
                    message
                } if message.contains("强制终止失败")
            )));

        let second = runtime.request_stop(Arc::new(|_| {}));
        assert!(second.success && second.cooperative && second.stopping);
        clear_fake_active(&runtime);
        let _ = fs::remove_dir_all(temporary);
    }

    #[test]
    fn force_stop_failure_rolls_back_stopping() {
        let runtime = TaskRuntime::with_terminator(1024, Duration::from_secs(1), |_, _| false);
        let temporary = temporary_directory("force-stop-failure");
        install_fake_active(&runtime, temporary.join("stop.marker"));

        let result = runtime.force_stop();

        assert!(!result.success);
        assert!(!result.stopping);
        assert!(!runtime.state().stopping);
        clear_fake_active(&runtime);
    }

    #[test]
    fn wait_until_idle_is_bounded_and_wakes_on_completion() {
        let runtime = TaskRuntime::default();
        install_fake_active(
            &runtime,
            temporary_directory("wait-idle").join("stop.marker"),
        );
        let started = Instant::now();
        assert!(!runtime.wait_until_idle(Duration::from_millis(25)));
        assert!(started.elapsed() < Duration::from_secs(1));

        let runtime_for_thread = runtime.clone();
        thread::spawn(move || {
            thread::sleep(Duration::from_millis(25));
            clear_fake_active(&runtime_for_thread);
        });
        assert!(runtime.wait_until_idle(Duration::from_secs(1)));
        assert!(!runtime.state().running);
    }

    #[cfg(windows)]
    #[test]
    fn windows_job_close_terminates_spawned_descendant() {
        const ROLE: &str = "WANDAO_TASK_RUNTIME_JOB_TEST_ROLE";
        const PID_FILE: &str = "WANDAO_TASK_RUNTIME_JOB_TEST_PID_FILE";
        const TEST_NAME: &str = "tasks::tests::windows_job_close_terminates_spawned_descendant";
        match std::env::var(ROLE).ok().as_deref() {
            Some("descendant") => loop {
                thread::sleep(Duration::from_secs(1));
            },
            Some("root") => {
                let descendant = Command::new(std::env::current_exe().unwrap())
                    .arg(TEST_NAME)
                    .args(["--exact", "--nocapture"])
                    .env(ROLE, "descendant")
                    .stdin(Stdio::null())
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .spawn()
                    .unwrap();
                fs::write(
                    std::env::var(PID_FILE).unwrap(),
                    descendant.id().to_string(),
                )
                .unwrap();
                println!("{{\"descendantPid\":{}}}", descendant.id());
                std::io::stdout().flush().unwrap();
                std::process::exit(0);
            }
            _ => {}
        }

        let temporary = temporary_directory("windows-job");
        fs::create_dir_all(&temporary).unwrap();
        let pid_file = temporary.join("descendant.pid");
        let mut environment = BTreeMap::new();
        environment.insert(ROLE.into(), "root".into());
        environment.insert(PID_FILE.into(), pid_file.to_string_lossy().into_owned());
        let request = self_test_request(TEST_NAME, temporary.clone(), environment);
        let runtime = TaskRuntime::new(64 * 1024, Duration::from_secs(1));

        let result = runtime.run(request, Arc::new(|_| {}));

        assert!(result.success, "{:?}", result.error);
        let descendant_pid = fs::read_to_string(&pid_file)
            .unwrap()
            .trim()
            .parse::<u32>()
            .unwrap();
        let deadline = Instant::now() + Duration::from_secs(3);
        while windows_process_is_running(descendant_pid) && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(20));
        }
        assert!(
            !windows_process_is_running(descendant_pid),
            "descendant process {descendant_pid} survived job close"
        );
        assert!(runtime.wait_until_idle(Duration::ZERO));
        let _ = fs::remove_dir_all(temporary);
    }

    #[cfg(windows)]
    fn windows_process_is_running(pid: u32) -> bool {
        use windows_sys::Win32::Foundation::{CloseHandle, WAIT_TIMEOUT};
        use windows_sys::Win32::System::Threading::{
            OpenProcess, WaitForSingleObject, PROCESS_SYNCHRONIZE,
        };

        let process = unsafe { OpenProcess(PROCESS_SYNCHRONIZE, 0, pid) };
        if process.is_null() {
            return false;
        }
        let status = unsafe { WaitForSingleObject(process, 0) };
        unsafe {
            CloseHandle(process);
        }
        status == WAIT_TIMEOUT
    }

    #[test]
    fn cooperative_stop_marks_result_and_clears_runtime_state() {
        const CHILD_MARKER: &str = "WANDAO_TASK_RUNTIME_STOP_TEST_CHILD";
        if std::env::var_os(CHILD_MARKER).is_some() {
            let stop_file = PathBuf::from(
                std::env::var("WANDAO_STOP_FILE").expect("child stop file environment"),
            );
            let deadline = SystemTime::now() + Duration::from_secs(10);
            while SystemTime::now() < deadline {
                if stop_file.exists() {
                    std::process::exit(0);
                }
                thread::sleep(Duration::from_millis(20));
            }
            std::process::exit(24);
        }

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let user_data_dir = std::env::temp_dir().join(format!("wandao-task-runtime-test-{unique}"));
        let runtime = TaskRuntime::new(1024, Duration::from_secs(2));
        let runtime_for_thread = runtime.clone();
        let (sender, receiver) = mpsc::channel();
        let mut extra_environment = BTreeMap::new();
        extra_environment.insert(CHILD_MARKER.to_string(), "1".to_string());
        let request = TaskRunRequest {
            executable: std::env::current_exe().unwrap(),
            script: PathBuf::from(
                "tasks::tests::cooperative_stop_marks_result_and_clears_runtime_state",
            ),
            args: vec!["--exact".into(), "--nocapture".into()],
            working_directory: Some(std::env::current_dir().unwrap()),
            context: TaskExecutionContext {
                user_data_dir: user_data_dir.clone(),
                provider_id: "test-provider".into(),
                task_id: "test-task".into(),
                run_id: String::new(),
                job_id: String::new(),
                parent_run_id: String::new(),
                started_at: "2026-07-28T00:00:00Z".into(),
                browser_path: None,
                python_runtime: None,
                python_library_dir: None,
                additional_python_paths: Vec::new(),
                plugin: None,
                secret_environment: BTreeMap::new(),
                extra_environment,
            },
            stop_file: None,
            stdin_text: None,
            close_stdin_after_initial_input: false,
        };
        let events = Arc::new(Mutex::new(Vec::new()));
        let captured = Arc::clone(&events);
        let sink: TaskEventSink = Arc::new(move |event| {
            captured
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner())
                .push(event);
        });
        thread::spawn(move || {
            let result = runtime_for_thread.run(request, sink);
            sender.send(result).unwrap();
        });

        let deadline = SystemTime::now() + Duration::from_secs(5);
        while !runtime.state().running && SystemTime::now() < deadline {
            thread::sleep(Duration::from_millis(10));
        }
        assert!(runtime.state().running);
        let stop = runtime.request_stop(Arc::new(|_| {}));
        assert!(stop.success);
        assert!(stop.cooperative);

        let result = receiver.recv_timeout(Duration::from_secs(5)).unwrap();
        assert!(!result.success);
        assert!(matches!(result.code, Some(TaskExitCode::Number(130))));
        assert_eq!(result.data.unwrap()["stopped"], true);
        assert!(!runtime.state().running);
        assert!(events
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .iter()
            .any(|event| matches!(
                event,
                TaskRuntimeEvent::State {
                    state: TaskProcessState {
                        last_status: Some(TaskLastStatus::Stopped),
                        ..
                    }
                }
            )));
        let _ = fs::remove_dir_all(user_data_dir);
    }
}
