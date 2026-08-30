mod app_menu;
mod app_state;
mod commands;
mod plugins;
mod providers;
mod security;
mod tasks;

use std::{
    sync::atomic::{AtomicBool, Ordering},
    time::Duration,
};

use app_state::{AppPaths, AppState};
use plugins::PluginManager;
use tasks::TaskRuntime;
use tauri::{AppHandle, Manager, RunEvent, WindowEvent};
use tauri_plugin_dialog::{
    DialogExt, MessageDialogButtons, MessageDialogKind, MessageDialogResult,
};

const SHUTDOWN_WAIT_TIMEOUT: Duration = Duration::from_secs(5);
const SHUTDOWN_CONTINUE_LABEL: &str = "继续运行";
const SHUTDOWN_STOP_LABEL: &str = "停止任务并退出";

#[derive(Default)]
struct ShutdownState {
    exit_confirmed: AtomicBool,
}

pub fn run() {
    let builder = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(
            |app, _arguments, _working_directory| {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.unminimize();
                    let _ = window.show();
                    let _ = window.set_focus();
                }
            },
        ))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_clipboard_manager::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .menu(app_menu::build)
        .on_menu_event(app_menu::handle)
        .setup(|app| {
            let paths = AppPaths::discover(app.handle())
                .map_err(|error| std::io::Error::other(format!("初始化应用目录失败：{error}")))?;
            let manager = PluginManager::new(&paths, app.package_info().version.to_string())
                .map_err(|error| std::io::Error::other(format!("初始化插件系统失败：{error}")))?;
            app.manage(AppState::new(paths));
            app.manage(manager);
            app.manage(TaskRuntime::default());
            app.manage(ShutdownState::default());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::select_directory,
            commands::select_file,
            commands::select_browser_file,
            commands::save_file,
            commands::fetch_remote_text,
            commands::fetch_remote_image,
            commands::run_python_command,
            commands::stop_python_process,
            commands::get_python_process_state,
            commands::protect_task_args,
            commands::restore_task_args,
            commands::send_python_input,
            commands::read_file,
            commands::write_file,
            commands::file_exists,
            commands::open_path,
            commands::open_external,
            commands::show_about,
            commands::check_for_updates,
            commands::install_update,
            commands::get_app_settings,
            commands::save_app_settings,
            commands::detect_browsers,
            commands::get_provider_manifests,
            commands::read_provider_guide_image,
            commands::get_plugin_catalog,
            commands::install_plugin,
            commands::install_plugin_file,
            commands::set_plugin_enabled,
            commands::rollback_plugin,
            commands::uninstall_plugin,
            commands::get_plugin_ui,
            commands::copy_text,
            commands::get_app_path,
        ])
        .on_window_event(|window, event| {
            let WindowEvent::CloseRequested { api, .. } = event else {
                return;
            };
            let Some(runtime) = window.app_handle().try_state::<TaskRuntime>() else {
                return;
            };
            if !runtime.state().running {
                #[cfg(target_os = "macos")]
                {
                    api.prevent_close();
                    let _ = window.hide();
                }
                return;
            }
            api.prevent_close();
            if !confirm_running_task_shutdown(window.app_handle()) {
                return;
            }
            match stop_running_task(&runtime) {
                Ok(()) => {
                    #[cfg(target_os = "macos")]
                    let _ = window.hide();
                    #[cfg(not(target_os = "macos"))]
                    let _ = window.destroy();
                }
                Err(error) => show_shutdown_error(window.app_handle(), &error),
            }
        });

    let application = match builder.build(tauri::generate_context!()) {
        Ok(application) => application,
        Err(error) => {
            eprintln!("万能导启动失败：{error}");
            return;
        }
    };
    application.run(|app, event| match event {
        RunEvent::ExitRequested { api, .. } => {
            if app
                .try_state::<ShutdownState>()
                .is_some_and(|state| state.exit_confirmed.load(Ordering::Acquire))
            {
                return;
            }
            let Some(runtime) = app.try_state::<TaskRuntime>() else {
                return;
            };
            if !runtime.state().running {
                return;
            }
            api.prevent_exit();
            if !confirm_running_task_shutdown(app) {
                return;
            }
            match stop_running_task(&runtime) {
                Ok(()) => {
                    if let Some(state) = app.try_state::<ShutdownState>() {
                        state.exit_confirmed.store(true, Ordering::Release);
                    }
                    app.exit(0);
                }
                Err(error) => show_shutdown_error(app, &error),
            }
        }
        #[cfg(target_os = "macos")]
        RunEvent::Reopen { .. } => {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }
        _ => {}
    });
}

fn confirm_running_task_shutdown(app: &AppHandle) -> bool {
    let result = app
        .dialog()
        .message(
            "当前迁移任务尚未完成。\n\n立即退出会停止任务；已完成文件和 checkpoint 会保留，下次可继续。",
        )
        .title("任务仍在运行")
        .kind(MessageDialogKind::Warning)
        .buttons(MessageDialogButtons::OkCancelCustom(
            SHUTDOWN_CONTINUE_LABEL.to_string(),
            SHUTDOWN_STOP_LABEL.to_string(),
        ))
        .blocking_show_with_result();
    shutdown_choice_confirms_stop(&result)
}

fn shutdown_choice_confirms_stop(result: &MessageDialogResult) -> bool {
    matches!(result, MessageDialogResult::Custom(label) if label == SHUTDOWN_STOP_LABEL)
}

fn stop_running_task(runtime: &TaskRuntime) -> Result<(), String> {
    let result = runtime.force_stop();
    if !result.success && runtime.state().running {
        return Err(result
            .error
            .unwrap_or_else(|| "无法停止当前任务，请稍后重试。".to_string()));
    }
    if runtime.wait_until_idle(SHUTDOWN_WAIT_TIMEOUT) {
        Ok(())
    } else {
        Err("任务进程未能在 5 秒内完全退出；为避免后台任务继续写入，已取消退出。请再次尝试停止任务。".to_string())
    }
}

fn show_shutdown_error(app: &AppHandle, error: &str) {
    app.dialog()
        .message(error)
        .title("无法安全退出")
        .kind(MessageDialogKind::Error)
        .buttons(MessageDialogButtons::Ok)
        .blocking_show();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shutdown_requires_the_explicit_destructive_choice() {
        assert!(shutdown_choice_confirms_stop(&MessageDialogResult::Custom(
            SHUTDOWN_STOP_LABEL.to_string()
        )));
        assert!(!shutdown_choice_confirms_stop(
            &MessageDialogResult::Custom(SHUTDOWN_CONTINUE_LABEL.to_string())
        ));
        assert!(!shutdown_choice_confirms_stop(&MessageDialogResult::Cancel));
        assert!(!shutdown_choice_confirms_stop(&MessageDialogResult::Ok));
    }
}
