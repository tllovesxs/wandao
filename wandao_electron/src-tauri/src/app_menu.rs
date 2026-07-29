use std::sync::{
    atomic::{AtomicU32, Ordering},
    Arc,
};

use serde_json::Value;
#[cfg(target_os = "macos")]
use tauri::menu::AboutMetadata;
use tauri::{
    menu::{Menu, MenuBuilder, MenuEvent, MenuItem, SubmenuBuilder},
    AppHandle, Emitter, Manager,
};
use tauri_plugin_clipboard_manager::ClipboardExt;
use tauri_plugin_opener::OpenerExt;

use crate::{
    commands,
    tasks::{DiagnosticLevel, TaskEventSink, TaskRuntime, TaskRuntimeEvent},
};

const MENU_STOP_TASK: &str = "task.stop";
const MENU_EDIT_UNDO: &str = "edit.undo";
const MENU_EDIT_REDO: &str = "edit.redo";
const MENU_RELOAD: &str = "view.reload";
const MENU_DEVTOOLS: &str = "view.devtools";
const MENU_ZOOM_RESET: &str = "view.zoom-reset";
const MENU_ZOOM_IN: &str = "view.zoom-in";
const MENU_ZOOM_OUT: &str = "view.zoom-out";
const MENU_FULLSCREEN: &str = "view.fullscreen";
const MENU_DOCS: &str = "help.docs";
const MENU_GITHUB: &str = "help.github";
const MENU_RELEASES: &str = "help.releases";
const MENU_CHECK_UPDATES: &str = "help.check-updates";
const MENU_ISSUES: &str = "help.issues";
const MENU_COPY_WECHAT: &str = "help.copy-wechat";
const MENU_ABOUT: &str = "help.about";

const PROJECT_NAME: &str = "万能导 Wandao";
const PROJECT_GITHUB: &str = "https://github.com/tllovesxs/wandao";
const PROJECT_DOCS: &str =
    "https://github.com/tllovesxs/wandao/blob/main/docs/%E4%BD%BF%E7%94%A8%E6%95%99%E7%A8%8B.md";
const PROJECT_RELEASES: &str = "https://github.com/tllovesxs/wandao/releases";
const PROJECT_ISSUES: &str = "https://github.com/tllovesxs/wandao/issues";
const PROJECT_WECHAT: &str = "pressure_spring";

const MIN_ZOOM_PERCENT: u32 = 50;
const MAX_ZOOM_PERCENT: u32 = 200;
const ZOOM_STEP_PERCENT: u32 = 10;
static ZOOM_PERCENT: AtomicU32 = AtomicU32::new(100);

pub fn build(app: &AppHandle) -> tauri::Result<Menu<tauri::Wry>> {
    let stop_task = MenuItem::with_id(
        app,
        MENU_STOP_TASK,
        "停止当前任务",
        true,
        Some("CmdOrCtrl+Shift+S"),
    )?;
    let reload = MenuItem::with_id(app, MENU_RELOAD, "刷新", true, Some("CmdOrCtrl+R"))?;
    let devtools = MenuItem::with_id(
        app,
        MENU_DEVTOOLS,
        "开发者工具",
        cfg!(debug_assertions),
        Some("CmdOrCtrl+Shift+I"),
    )?;
    let zoom_reset =
        MenuItem::with_id(app, MENU_ZOOM_RESET, "实际大小", true, Some("CmdOrCtrl+0"))?;
    let zoom_in = MenuItem::with_id(app, MENU_ZOOM_IN, "放大", true, Some("CmdOrCtrl+="))?;
    let zoom_out = MenuItem::with_id(app, MENU_ZOOM_OUT, "缩小", true, Some("CmdOrCtrl+-"))?;
    let fullscreen = MenuItem::with_id(app, MENU_FULLSCREEN, "全屏", true, Some("F11"))?;

    let mut file = SubmenuBuilder::new(app, "文件")
        .item(&stop_task)
        .separator();
    #[cfg(target_os = "macos")]
    {
        file = file.close_window_with_text("关闭窗口");
    }
    #[cfg(not(target_os = "macos"))]
    {
        file = file.quit_with_text("退出");
    }
    let file = file.build()?;

    #[cfg(target_os = "macos")]
    let edit = SubmenuBuilder::new(app, "编辑")
        .undo_with_text("撤销")
        .redo_with_text("重做")
        .separator()
        .cut_with_text("剪切")
        .copy_with_text("复制")
        .paste_with_text("粘贴")
        .select_all_with_text("全选")
        .build()?;
    #[cfg(not(target_os = "macos"))]
    let edit = {
        let undo = MenuItem::with_id(app, MENU_EDIT_UNDO, "撤销", true, Some("CmdOrCtrl+Z"))?;
        let redo = MenuItem::with_id(app, MENU_EDIT_REDO, "重做", true, Some("CmdOrCtrl+Y"))?;
        SubmenuBuilder::new(app, "编辑")
            .item(&undo)
            .item(&redo)
            .separator()
            .cut_with_text("剪切")
            .copy_with_text("复制")
            .paste_with_text("粘贴")
            .select_all_with_text("全选")
            .build()?
    };

    let view = SubmenuBuilder::new(app, "视图")
        .item(&reload)
        .item(&devtools)
        .separator()
        .item(&zoom_reset)
        .item(&zoom_in)
        .item(&zoom_out)
        .separator()
        .item(&fullscreen)
        .build()?;

    let help = SubmenuBuilder::new(app, "帮助")
        .text(MENU_DOCS, "新手模式 / 使用教程")
        .separator()
        .text(MENU_GITHUB, "项目主页 GitHub")
        .text(MENU_RELEASES, "下载发行版")
        .text(MENU_CHECK_UPDATES, "检查更新")
        .text(MENU_ISSUES, "问题反馈")
        .separator()
        .text(MENU_COPY_WECHAT, "复制微信号")
        .text(MENU_ABOUT, format!("关于 {PROJECT_NAME}"))
        .build()?;

    let mut menu = MenuBuilder::new(app);
    #[cfg(target_os = "macos")]
    {
        let metadata = AboutMetadata {
            name: Some(PROJECT_NAME.to_string()),
            version: Some(app.package_info().version.to_string()),
            copyright: Some("Copyright © tllovesxs".to_string()),
            ..AboutMetadata::default()
        };
        let app_menu = SubmenuBuilder::new(app, PROJECT_NAME)
            .about_with_text(format!("关于 {PROJECT_NAME}"), Some(metadata))
            .separator()
            .services_with_text("服务")
            .separator()
            .hide_with_text(format!("隐藏 {PROJECT_NAME}"))
            .hide_others_with_text("隐藏其他")
            .show_all_with_text("全部显示")
            .separator()
            .quit_with_text(format!("退出 {PROJECT_NAME}"))
            .build()?;
        menu = menu.item(&app_menu);
    }
    menu = menu.item(&file).item(&edit).item(&view);
    #[cfg(target_os = "macos")]
    {
        let window = SubmenuBuilder::new(app, "窗口")
            .minimize_with_text("最小化")
            .maximize_with_text("缩放")
            .separator()
            .bring_all_to_front_with_text("全部置于前台")
            .close_window_with_text("关闭窗口")
            .build()?;
        menu = menu.item(&window);
    }
    menu.item(&help).build()
}

pub fn handle(app: &AppHandle, event: MenuEvent) {
    match event.id().as_ref() {
        MENU_STOP_TASK => stop_task(app),
        MENU_EDIT_UNDO => edit_command(app, "undo"),
        MENU_EDIT_REDO => edit_command(app, "redo"),
        MENU_RELOAD => with_main_window(app, |window| window.eval("window.location.reload()")),
        MENU_DEVTOOLS => toggle_devtools(app),
        MENU_ZOOM_RESET => set_zoom(app, 100),
        MENU_ZOOM_IN => adjust_zoom(app, ZOOM_STEP_PERCENT as i32),
        MENU_ZOOM_OUT => adjust_zoom(app, -(ZOOM_STEP_PERCENT as i32)),
        MENU_FULLSCREEN => toggle_fullscreen(app),
        MENU_DOCS => open_url(app, PROJECT_DOCS),
        MENU_GITHUB => open_url(app, PROJECT_GITHUB),
        MENU_RELEASES => open_url(app, PROJECT_RELEASES),
        MENU_CHECK_UPDATES => check_for_updates(app),
        MENU_ISSUES => open_url(app, PROJECT_ISSUES),
        MENU_COPY_WECHAT => copy_wechat(app),
        MENU_ABOUT => show_about(app),
        _ => {}
    }
}

fn stop_task(app: &AppHandle) {
    let Some(runtime) = app.try_state::<TaskRuntime>() else {
        emit_app_info(app, "任务运行时尚未初始化。".to_string());
        return;
    };
    let result = runtime.request_stop(task_event_sink(app.clone()));
    if result.success {
        emit_app_info(app, "已发送停止请求，正在等待当前任务退出。".to_string());
    } else {
        emit_app_info(
            app,
            result
                .error
                .unwrap_or_else(|| "无法停止当前任务。".to_string()),
        );
    }
}

fn task_event_sink(app: AppHandle) -> TaskEventSink {
    Arc::new(move |event| match event {
        TaskRuntimeEvent::State { state } => {
            let _ = app.emit("python-process-state", state);
        }
        TaskRuntimeEvent::Output { text, .. } => {
            let _ = app.emit("python-log", text);
        }
        TaskRuntimeEvent::StructuredLog { .. } => {}
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

fn with_main_window(
    app: &AppHandle,
    action: impl FnOnce(&tauri::WebviewWindow) -> tauri::Result<()>,
) {
    let Some(window) = app.get_webview_window("main") else {
        emit_app_info(app, "主窗口当前不可用。".to_string());
        return;
    };
    if let Err(error) = action(&window) {
        emit_app_info(app, format!("窗口操作失败：{error}"));
    }
}

fn toggle_devtools(app: &AppHandle) {
    #[cfg(debug_assertions)]
    if let Some(window) = app.get_webview_window("main") {
        if window.is_devtools_open() {
            window.close_devtools();
        } else {
            window.open_devtools();
        }
    }
    #[cfg(not(debug_assertions))]
    let _ = app;
}

fn edit_command(app: &AppHandle, command: &'static str) {
    with_main_window(app, |window| {
        window.eval(format!("document.execCommand({command:?})"))
    });
}

fn adjust_zoom(app: &AppHandle, delta: i32) {
    let current = ZOOM_PERCENT.load(Ordering::Relaxed) as i32;
    set_zoom(
        app,
        (current + delta).clamp(MIN_ZOOM_PERCENT as i32, MAX_ZOOM_PERCENT as i32) as u32,
    );
}

fn set_zoom(app: &AppHandle, percent: u32) {
    let percent = percent.clamp(MIN_ZOOM_PERCENT, MAX_ZOOM_PERCENT);
    with_main_window(app, |window| window.set_zoom(percent as f64 / 100.0));
    ZOOM_PERCENT.store(percent, Ordering::Relaxed);
}

fn toggle_fullscreen(app: &AppHandle) {
    with_main_window(app, |window| {
        let fullscreen = window.is_fullscreen()?;
        window.set_fullscreen(!fullscreen)
    });
}

fn open_url(app: &AppHandle, url: &'static str) {
    if let Err(error) = app.opener().open_url(url, None::<&str>) {
        emit_app_info(app, format!("无法打开链接：{error}"));
    }
}

fn check_for_updates(app: &AppHandle) {
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        let message = match commands::check_for_updates(app.clone()).await {
            Ok(result) if result.get("success").and_then(Value::as_bool) == Some(true) => {
                let data = &result["data"];
                if data.get("hasUpdate").and_then(Value::as_bool) == Some(true) {
                    format!(
                        "发现新版本：v{}",
                        data.get("latestVersion")
                            .and_then(Value::as_str)
                            .unwrap_or("未知")
                    )
                } else {
                    format!(
                        "当前已是最新版本：v{}",
                        data.get("currentVersion")
                            .and_then(Value::as_str)
                            .unwrap_or("未知")
                    )
                }
            }
            Ok(result) => format!(
                "检查更新失败：{}",
                result
                    .get("error")
                    .and_then(Value::as_str)
                    .unwrap_or("未知错误")
            ),
            Err(error) => format!("检查更新失败：{error}"),
        };
        emit_app_info(&app, message);
    });
}

fn copy_wechat(app: &AppHandle) {
    let message = match app.clipboard().write_text(PROJECT_WECHAT) {
        Ok(()) => format!("已复制微信号：{PROJECT_WECHAT}"),
        Err(error) => format!("复制微信号失败：{error}"),
    };
    emit_app_info(app, message);
}

fn show_about(app: &AppHandle) {
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        if let Err(error) = commands::show_about(app.clone()).await {
            emit_app_info(&app, format!("无法显示关于信息：{error}"));
        }
    });
}

fn emit_app_info(app: &AppHandle, message: String) {
    let _ = app.emit("app-info", message);
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashSet;

    #[test]
    fn custom_menu_ids_are_unique() {
        let ids = [
            MENU_STOP_TASK,
            MENU_EDIT_UNDO,
            MENU_EDIT_REDO,
            MENU_RELOAD,
            MENU_DEVTOOLS,
            MENU_ZOOM_RESET,
            MENU_ZOOM_IN,
            MENU_ZOOM_OUT,
            MENU_FULLSCREEN,
            MENU_DOCS,
            MENU_GITHUB,
            MENU_RELEASES,
            MENU_CHECK_UPDATES,
            MENU_ISSUES,
            MENU_COPY_WECHAT,
            MENU_ABOUT,
        ];
        assert_eq!(ids.iter().copied().collect::<HashSet<_>>().len(), ids.len());
    }

    #[test]
    fn project_links_remain_https() {
        for url in [
            PROJECT_GITHUB,
            PROJECT_DOCS,
            PROJECT_RELEASES,
            PROJECT_ISSUES,
        ] {
            assert!(url.starts_with("https://"));
        }
    }
}
