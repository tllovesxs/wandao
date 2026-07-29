#![cfg(windows)]

use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{self, Command};
use std::thread;

const LEGACY_UNINSTALL_KEY: &str = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\1b0cbfbe-638f-5e34-9149-e794da0edaeb";
const LEGACY_FIXTURE_MARKER_ENV: &str = "WANDAO_LEGACY_FIXTURE_MARKER";

fn exit_with_error(message: &str) -> ! {
    eprintln!("{message}");
    process::exit(1);
}

fn same_windows_path(left: &Path, right: &Path) -> bool {
    left.to_string_lossy()
        .eq_ignore_ascii_case(&right.to_string_lossy())
}

fn current_executable() -> PathBuf {
    env::current_exe()
        .unwrap_or_else(|error| exit_with_error(&format!("cannot resolve fixture: {error}")))
}

fn run_legacy_main() -> ! {
    let executable = current_executable();
    let executable_name = executable
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or_default();
    if !executable_name.eq_ignore_ascii_case("Wandao.exe") {
        exit_with_error("stay-running mode must be launched as Wandao.exe");
    }

    loop {
        thread::park();
    }
}

fn write_invocation_marker(arguments: &[String]) {
    let marker = env::var_os(LEGACY_FIXTURE_MARKER_ENV)
        .map(PathBuf::from)
        .unwrap_or_else(|| exit_with_error("WANDAO_LEGACY_FIXTURE_MARKER is missing"));
    let executable = current_executable();
    let record = format!("{}\n{}\n", executable.display(), arguments.join("\n"));
    fs::write(&marker, record)
        .unwrap_or_else(|error| exit_with_error(&format!("cannot write fixture marker: {error}")));
}

fn main() {
    let arguments = env::args().skip(1).collect::<Vec<_>>();
    if arguments == ["--stay-running"] {
        run_legacy_main();
    }

    // Record execution before validating arguments or location. Rejection
    // tests can therefore prove that an untrusted command was never launched.
    write_invocation_marker(&arguments);
    if arguments != ["/currentuser", "/S"] {
        exit_with_error("unexpected legacy fixture arguments");
    }

    let local_app_data = env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(|| exit_with_error("LOCALAPPDATA is missing"));
    let legacy_root = local_app_data.join("Programs").join("wandao");
    let expected_uninstaller = legacy_root.join("Uninstall Wandao.exe");
    let current_executable = fs::canonicalize(current_executable())
        .unwrap_or_else(|error| exit_with_error(&format!("cannot resolve fixture: {error}")));
    let expected_uninstaller = fs::canonicalize(expected_uninstaller).unwrap_or_else(|error| {
        exit_with_error(&format!("cannot resolve expected fixture: {error}"))
    });
    if !same_windows_path(&current_executable, &expected_uninstaller) {
        exit_with_error("legacy fixture was launched outside its fixed install directory");
    }

    let legacy_main = legacy_root.join("Wandao.exe");
    if !legacy_main.is_file() {
        exit_with_error("legacy fixture main executable is missing");
    }
    fs::remove_file(&legacy_main)
        .unwrap_or_else(|error| exit_with_error(&format!("cannot remove legacy main: {error}")));

    let status = Command::new("reg.exe")
        .args(["delete", LEGACY_UNINSTALL_KEY, "/f"])
        .status()
        .unwrap_or_else(|error| exit_with_error(&format!("cannot run reg.exe: {error}")));
    if !status.success() {
        exit_with_error("cannot remove legacy fixture registry key");
    }
}
