#!/usr/bin/env python3
"""Verify that a packaged Tauri application contains usable Plugin v1 assets."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform as platform_module
import plistlib
import subprocess
import sys
import tempfile
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_WINDOW_TITLE = "万能导 Wandao"
DEFAULT_STARTUP_TIMEOUT_SECONDS = 10.0
PROCESS_TERMINATION_TIMEOUT_SECONDS = 5.0
WINDOW_POLL_INTERVAL_SECONDS = 0.2
WINDOWS_EXECUTABLE_NAMES = ("Wandao.exe", "wandao.exe")
RUNTIME_METADATA_SCHEMA_VERSION = 1
REMOTE_ONLY_GUIDE_ASSET_PATH = Path(
    "plugins/feishu/providers/feishu-import/images"
)
RUNTIME_TARGETS = {
    "win-x64": {
        "asset": "cpython-3.11.15+20260623-x86_64-pc-windows-msvc-install_only_stripped.tar.gz",
        "archiveSha256": "6589ca6d63f520bec4096d62b3ab91da3d0a80b16b594c99a6b677e335814683",
        "pythonVersion": "3.11.15",
        "architecture": "x86_64",
        "platform": "win32",
    },
    "mac-x64": {
        "asset": "cpython-3.11.15+20260623-x86_64-apple-darwin-install_only_stripped.tar.gz",
        "archiveSha256": "4925e5aaa9bc77c85302d350b36c1d9def2002996a6bcfa55c88ba6eb318de29",
        "pythonVersion": "3.11.15",
        "architecture": "x86_64",
        "platform": "darwin",
    },
    "mac-arm64": {
        "asset": "cpython-3.11.15+20260623-aarch64-apple-darwin-install_only_stripped.tar.gz",
        "archiveSha256": "2318799eaf104f8a29bc09a93b0851b05dbbcb4ce9a5f045ddea169c0c7ff3a5",
        "pythonVersion": "3.11.15",
        "architecture": "aarch64",
        "platform": "darwin",
    },
}


def plugin_supports_platform(manifest: dict[str, object], target_platform: str) -> bool:
    platforms = manifest.get("platforms")
    if platforms is None:
        return True
    if not isinstance(platforms, list):
        raise RuntimeError("plugin.json platforms 必须是数组")
    return not platforms or target_platform in platforms


def expected_providers(target_platform: str | None = None) -> set[str]:
    target_platform = target_platform or sys.platform
    provider_ids: set[str] = set()
    for manifest_path in (REPO_ROOT / "plugins").glob("*/plugin.json"):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not plugin_supports_platform(manifest, target_platform):
            continue
        for relative_path in manifest.get("entrypoints", {}).get("providers", []):
            provider = json.loads((manifest_path.parent / relative_path).read_text(encoding="utf-8"))
            provider_ids.add(str(provider["id"]))
    return provider_ids


def executable_provider_ids(target_platform: str | None = None) -> set[str]:
    target_platform = target_platform or sys.platform
    provider_ids: set[str] = set()
    for manifest_path in (REPO_ROOT / "plugins").glob("*/plugin.json"):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not plugin_supports_platform(manifest, target_platform):
            continue
        for relative_path in manifest.get("entrypoints", {}).get("providers", []):
            provider_path = manifest_path.parent / relative_path
            provider = json.loads(provider_path.read_text(encoding="utf-8"))
            if any((action.get("script") or provider.get("script")) for action in provider.get("actions", []) if isinstance(action, dict)):
                provider_ids.add(str(provider["id"]))
    return provider_ids


def packaged_python(resources: Path) -> Path:
    candidates = [
        resources / "python-runtime" / "python.exe",
        resources / "python-runtime" / "bin" / "python3",
        resources / "python-runtime" / "bin" / "python",
    ]
    return next((candidate for candidate in candidates if candidate.is_file()), candidates[0])


def packaged_python_env() -> dict[str, str]:
    env = os.environ.copy()
    for name in (
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "PYTHONUSERBASE",
    ):
        env.pop(name, None)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    return env


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _expected_runtime_target(platform: str, machine: str) -> str:
    family = _platform_family(platform)
    normalized_machine = machine.lower()
    if family == "windows" and normalized_machine in {"amd64", "x86_64"}:
        return "win-x64"
    if family == "macos" and normalized_machine in {"arm64", "aarch64"}:
        return "mac-arm64"
    if family == "macos" and normalized_machine in {"amd64", "x86_64"}:
        return "mac-x64"
    raise RuntimeError(f"不支持的安装包运行时架构：{platform} {machine}")


def _runtime_release_violation(runtime: Path) -> tuple[str, Path] | None:
    if runtime.is_symlink():
        return "runtime 根目录是符号链接", runtime
    if not runtime.is_dir():
        return "runtime 根目录缺失或不是目录", runtime

    canonical_root = runtime.resolve()
    for path in runtime.rglob("*"):
        relative = path.relative_to(runtime)
        name = path.name.lower()
        if name == "__pycache__" or name.endswith((".pyc", ".pyo")):
            return "包含生成的 Python 缓存", path
        in_site_packages = "site-packages" in {part.lower() for part in relative.parts}
        build_tool_path = (
            name == "ensurepip"
            or (
                in_site_packages
                and (
                    name in {"pip", "setuptools", "pkg_resources", "_distutils_hack"}
                    or (
                        (name.startswith("pip-") or name.startswith("setuptools-"))
                        and name.endswith(".dist-info")
                    )
                )
            )
            or name == "distutils-precedence.pth"
            or (
                path.parent.name.lower() in {"scripts", "bin"}
                and (name.startswith("pip") or name.startswith("easy_install"))
            )
        )
        if build_tool_path:
            return "包含仅构建期包管理工具", path
        if path.is_symlink():
            try:
                target = path.resolve(strict=True)
                target.relative_to(canonical_root)
            except (OSError, RuntimeError, ValueError):
                return "包含损坏或越界的符号链接", path
            continue
        if not path.is_dir() and not path.is_file():
            return "包含非常规文件", path
    return None


def verify_packaged_runtime(
    resources: Path,
    *,
    platform: str = sys.platform,
    machine: str | None = None,
) -> None:
    runtime = resources / "python-runtime"
    violation = _runtime_release_violation(runtime)
    if violation is not None:
        reason, path = violation
        relative = Path(".") if path == runtime else path.relative_to(runtime)
        raise RuntimeError(f"runtime {reason}：{relative}")

    metadata_path = runtime / "WANDAO_RUNTIME.json"
    if not metadata_path.is_file():
        raise RuntimeError(f"缺少 runtime 元数据：{metadata_path}")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"runtime 元数据无效：{metadata_path}（{error}）") from error
    if not isinstance(metadata, dict):
        raise RuntimeError(f"runtime 元数据必须是 JSON 对象：{metadata_path}")

    target = _expected_runtime_target(platform, machine or platform_module.machine())
    expected = RUNTIME_TARGETS[target]
    required = {
        "schemaVersion": RUNTIME_METADATA_SCHEMA_VERSION,
        "target": target,
        "asset": expected["asset"],
        "archiveSha256": expected["archiveSha256"],
        "requirementsSha256": file_sha256(REPO_ROOT / "requirements.txt"),
        "requirementsLockSha256": file_sha256(REPO_ROOT / "requirements.lock"),
        "pythonImplementation": "CPython",
        "pythonVersion": expected["pythonVersion"],
        "architecture": expected["architecture"],
        "preparedBy": "wandao_electron/scripts/prepare_python_runtime.py",
    }
    mismatches = [
        f"{field}: expected={value!r} actual={metadata.get(field)!r}"
        for field, value in required.items()
        if metadata.get(field) != value
    ]
    if mismatches:
        raise RuntimeError("runtime 元数据与候选平台不一致：" + "; ".join(mismatches))

    installed_packages = metadata.get("installedPackages")
    if (
        not isinstance(installed_packages, list)
        or not installed_packages
        or not all(isinstance(package, str) and "==" in package for package in installed_packages)
        or installed_packages != sorted(set(installed_packages))
        or not any(package.lower() == "evernote-backup==1.13.1" for package in installed_packages)
    ):
        raise RuntimeError("runtime 元数据的 installedPackages 无效或缺少固定直接依赖")

    python = packaged_python(resources)
    if not python.is_file():
        raise RuntimeError(f"缺少打包后的 Python 运行时：{python}")
    code = (
        "import evernote, evernote_backup, json, platform, sqlite3, struct, sys, tkinter; "
        "from importlib.metadata import distributions; "
        "print(json.dumps({'implementation': platform.python_implementation(), "
        "'version': platform.python_version(), 'machine': platform.machine(), "
        "'bits': struct.calcsize('P') * 8, 'platform': sys.platform, "
        "'installedPackages': sorted(f\"{d.metadata['Name']}=={d.version}\" for d in distributions())}))"
    )
    result = subprocess.run(
        [str(python), "-I", "-B", "-c", code],
        env=packaged_python_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )
    if result.returncode:
        raise RuntimeError(f"无法执行打包后的 runtime 指纹检查（{result.returncode}）：{result.stderr}")
    try:
        fingerprint = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as error:
        raise RuntimeError(f"无法解析打包后的 runtime 指纹：{result.stdout!r}") from error

    accepted_machines = {
        "x86_64": {"amd64", "x86_64"},
        "aarch64": {"aarch64", "arm64"},
    }[str(expected["architecture"])]
    if (
        fingerprint.get("implementation") != "CPython"
        or fingerprint.get("version") != expected["pythonVersion"]
        or fingerprint.get("platform") != expected["platform"]
        or fingerprint.get("bits") != 64
        or str(fingerprint.get("machine") or "").lower() not in accepted_machines
        or fingerprint.get("installedPackages") != metadata.get("installedPackages")
    ):
        raise RuntimeError(f"打包后的 runtime 指纹与元数据不一致：{fingerprint}")


def resolve_resources_root(candidate: Path) -> Path:
    """Accept an installed Windows root, a macOS .app, or Resources itself."""
    candidates = [
        candidate,
        candidate / "resources",
        candidate / "Contents" / "Resources",
    ]
    for path in candidates:
        if (path / "plugins").is_dir() and (path / "python-runtime").is_dir():
            return path
    raise RuntimeError(f"找不到 Tauri 资源目录：{candidate}")


def verify_plugin_trust_store(resources: Path) -> None:
    trust_path = resources / "assets" / "plugin-trust.json"
    if not trust_path.is_file():
        raise RuntimeError(f"缺少插件签名信任根：{trust_path}")
    trust = json.loads(trust_path.read_text(encoding="utf-8"))
    keys = trust.get("keys")
    if trust.get("schemaVersion") != 1 or not isinstance(keys, list) or not keys:
        raise RuntimeError(f"插件签名信任根格式无效：{trust_path}")


def _resource_tree(root: Path, *, label: str) -> tuple[set[str], dict[str, str]]:
    if root.is_symlink():
        raise RuntimeError(f"{label}根目录是符号链接：{root}")
    if not root.is_dir():
        raise RuntimeError(f"缺少{label}目录：{root}")
    directories: set[str] = set()
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise RuntimeError(f"{label}包含符号链接：{relative}")
        if path.is_dir():
            if path.name.lower() == "__pycache__":
                raise RuntimeError(f"{label}包含生成缓存：{relative}")
            directories.add(relative)
            continue
        if not path.is_file():
            raise RuntimeError(f"{label}包含非常规资源：{relative}")
        if path.suffix.lower() in {".pyc", ".pyo"}:
            raise RuntimeError(f"{label}包含生成缓存：{relative}")
        hashes[relative] = file_sha256(path)
    return directories, hashes


def _resource_hashes(root: Path, *, label: str) -> dict[str, str]:
    return _resource_tree(root, label=label)[1]


def _expected_resource_directories(files: set[str]) -> set[str]:
    directories: set[str] = set()
    for relative in files:
        segments = relative.split("/")
        for length in range(1, len(segments)):
            directories.add("/".join(segments[:length]))
    return directories


def _verify_resource_tree(source: Path, packaged: Path, *, label: str) -> None:
    source_directories, expected = _resource_tree(source, label=f"源码{label}")
    actual_directories, actual = _resource_tree(packaged, label=f"包内{label}")
    expected_directories = _expected_resource_directories(set(expected))
    source_extra_directories = sorted(source_directories - expected_directories)
    if source_extra_directories:
        raise RuntimeError(f"源码{label}包含未受文件清单约束的空目录：{source_extra_directories[:20]}")
    if actual != expected or actual_directories != expected_directories:
        missing = sorted(expected.keys() - actual.keys())
        extra = sorted(actual.keys() - expected.keys())
        changed = sorted(path for path in expected.keys() & actual.keys() if expected[path] != actual[path])
        missing_directories = sorted(expected_directories - actual_directories)
        extra_directories = sorted(actual_directories - expected_directories)
        raise RuntimeError(
            f"{label}内容不一致：missing={missing[:20]} extra={extra[:20]} changed={changed[:20]} "
            f"missingDirectories={missing_directories[:20]} extraDirectories={extra_directories[:20]}"
        )


def verify_packaged_resource_layout(resources: Path) -> None:
    if resources.is_symlink():
        raise RuntimeError(f"Tauri Resources 根目录是符号链接：{resources}")
    remote_only_assets = resources / REMOTE_ONLY_GUIDE_ASSET_PATH
    if remote_only_assets.exists() or remote_only_assets.is_symlink():
        raise RuntimeError(f"远程教程图片不应进入安装包：{REMOTE_ONLY_GUIDE_ASSET_PATH.as_posix()}")
    _verify_resource_tree(REPO_ROOT / "plugins", resources / "plugins", label="Plugin v1 资源")
    _verify_resource_tree(REPO_ROOT / "providers", resources / "providers", label="兼容 Provider 资源")
    _verify_resource_tree(
        REPO_ROOT / "wandao_core",
        resources / "python" / "wandao_core",
        label="Python 核心资源",
    )
    _verify_resource_tree(
        REPO_ROOT / "wandao_electron" / "assets",
        resources / "assets",
        label="桌面信任与图标资源",
    )

    packaged_python_root = resources / "python"
    if packaged_python_root.is_symlink() or not packaged_python_root.is_dir():
        raise RuntimeError(f"包内 Python 资源根目录缺失或是符号链接：{packaged_python_root}")
    source_python_modules = {source.name: source for source in REPO_ROOT.glob("*.py")}
    packaged_python_modules = {path.name: path for path in packaged_python_root.glob("*.py")}
    if packaged_python_modules.keys() != source_python_modules.keys():
        raise RuntimeError(
            "包内根 Python 模块集合不一致："
            f"expected={sorted(source_python_modules)} actual={sorted(packaged_python_modules)}"
        )
    for source in sorted(source_python_modules.values()):
        packaged = packaged_python_root / source.name
        if packaged.is_symlink() or not packaged.is_file() or file_sha256(packaged) != file_sha256(source):
            raise RuntimeError(f"包内根 Python 模块缺失或内容不一致：{source.name}")
    packaged_requirements = packaged_python_root / "requirements.txt"
    source_requirements = REPO_ROOT / "requirements.txt"
    if (
        packaged_requirements.is_symlink()
        or not packaged_requirements.is_file()
        or file_sha256(packaged_requirements) != file_sha256(source_requirements)
    ):
        raise RuntimeError("包内 requirements.txt 缺失或内容不一致")
    packaged_requirements_lock = packaged_python_root / "requirements.lock"
    source_requirements_lock = REPO_ROOT / "requirements.lock"
    if (
        packaged_requirements_lock.is_symlink()
        or not packaged_requirements_lock.is_file()
        or file_sha256(packaged_requirements_lock) != file_sha256(source_requirements_lock)
    ):
        raise RuntimeError("包内 requirements.lock 缺失或内容不一致")


def _platform_family(platform: str) -> str:
    if platform.startswith("win"):
        return "windows"
    if platform == "darwin":
        return "macos"
    raise RuntimeError(f"安装应用启动 smoke 仅支持 Windows 和 macOS，当前平台：{platform}")


def _unique_paths(paths: list[Path]) -> list[Path]:
    output: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved not in output:
            output.append(resolved)
    return output


def resolve_windows_executable(candidate: Path, resources: Path) -> Path:
    search_roots = [candidate if candidate.is_dir() else candidate.parent, resources]
    if resources.name.lower() == "resources":
        search_roots.append(resources.parent)

    roots = [path for path in _unique_paths(search_roots) if path.is_dir()]
    for root in roots:
        entries = {path.name.lower(): path for path in root.iterdir() if path.is_file()}
        for name in WINDOWS_EXECUTABLE_NAMES:
            executable = entries.get(name.lower())
            if executable is not None:
                return executable.resolve()

    fallback = [
        path.resolve()
        for root in roots
        for path in root.glob("*.exe")
        if path.is_file() and not path.name.lower().startswith(("unins", "uninstall"))
    ]
    fallback = _unique_paths(fallback)
    if len(fallback) == 1:
        return fallback[0]
    if fallback:
        raise RuntimeError(
            "安装目录中存在多个候选 exe，无法安全确定 Wandao 主程序；请通过 --executable 明确指定："
            + ", ".join(str(path) for path in fallback)
        )
    raise RuntimeError(f"找不到安装后的 Wandao exe：{candidate}")


def resolve_macos_bundle_executable(app_bundle: Path) -> Path:
    app_bundle = app_bundle.resolve()
    if not app_bundle.is_dir() or app_bundle.suffix.lower() != ".app":
        raise RuntimeError(f"不是有效的 macOS .app：{app_bundle}")

    info_path = app_bundle / "Contents" / "Info.plist"
    if not info_path.is_file():
        raise RuntimeError(f"macOS 应用缺少 Info.plist：{info_path}")
    try:
        with info_path.open("rb") as stream:
            bundle_info = plistlib.load(stream)
    except (OSError, plistlib.InvalidFileException) as error:
        raise RuntimeError(f"无法读取 macOS Info.plist：{info_path}（{error}）") from error

    executable_name = bundle_info.get("CFBundleExecutable")
    if (
        not isinstance(executable_name, str)
        or not executable_name
        or Path(executable_name).name != executable_name
    ):
        raise RuntimeError(f"macOS Info.plist 的 CFBundleExecutable 无效：{executable_name!r}")

    executable_root = (app_bundle / "Contents" / "MacOS").resolve()
    executable = (executable_root / executable_name).resolve()
    if executable.parent != executable_root or not executable.is_file():
        raise RuntimeError(f"找不到 macOS 应用主二进制：{executable}")
    return executable


def _find_macos_app(candidate: Path, resources: Path) -> Path:
    for origin in _unique_paths([candidate, resources]):
        for path in (origin, *origin.parents):
            if path.suffix.lower() == ".app" and path.is_dir():
                return path
    raise RuntimeError(f"无法从资源路径确定 macOS .app；请通过 --executable 明确指定：{candidate}")


def resolve_application_executable(
    candidate: Path,
    resources: Path,
    explicit: Path | None = None,
    *,
    platform: str = sys.platform,
) -> Path:
    family = _platform_family(platform)
    if explicit is not None:
        explicit = explicit.resolve()
        if family == "macos" and explicit.is_dir():
            return resolve_macos_bundle_executable(explicit)
        if not explicit.is_file():
            raise RuntimeError(f"指定的应用主程序不存在：{explicit}")
        if family == "windows" and explicit.suffix.lower() != ".exe":
            raise RuntimeError(f"Windows 应用主程序必须是 exe：{explicit}")
        return explicit

    if family == "windows":
        return resolve_windows_executable(candidate, resources)
    return resolve_macos_bundle_executable(_find_macos_app(candidate, resources))


def visible_window_titles_for_pid(pid: int) -> list[str]:
    if os.name != "nt":
        raise RuntimeError("EnumWindows 只能在 Windows 上运行")

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    enum_callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [enum_callback, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int

    titles: list[str] = []

    @enum_callback
    def collect(hwnd: int, _parameter: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        window_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
        if window_pid.value != pid:
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        if user32.GetWindowTextW(hwnd, buffer, len(buffer)):
            title = buffer.value.strip()
            if title:
                titles.append(title)
        return True

    ctypes.set_last_error(0)
    if not user32.EnumWindows(collect, 0):
        error_code = ctypes.get_last_error()
        if error_code:
            raise OSError(error_code, "EnumWindows 失败")
    return titles


def _process_exit_details(process: subprocess.Popen[str]) -> str:
    try:
        stdout, stderr = process.communicate(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    output = "\n".join(part.strip() for part in (stderr, stdout) if part and part.strip())
    if not output:
        return ""
    return f"；启动输出：{output[:2000]}"


def _observe_application_startup(
    process: subprocess.Popen[str],
    *,
    family: str,
    timeout: float,
    expected_window_title: str,
) -> None:
    deadline = time.monotonic() + timeout
    matching_window_visible = False
    observed_titles: set[str] = set()

    while True:
        return_code = process.poll()
        if return_code is not None:
            details = _process_exit_details(process)
            raise RuntimeError(f"安装后的应用在启动观察期内退出（{return_code}）{details}")

        if family == "windows":
            titles = visible_window_titles_for_pid(process.pid)
            observed_titles.update(titles)
            matching_window_visible = expected_window_title in titles

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(WINDOW_POLL_INTERVAL_SECONDS, remaining))

    if family == "windows" and not matching_window_visible:
        seen = ", ".join(sorted(observed_titles)) or "无"
        raise RuntimeError(
            f"安装后的应用未出现标题为 {expected_window_title!r} 的可见主窗口；该 PID 的可见窗口标题：{seen}"
        )


def _terminate_process(process: subprocess.Popen[str]) -> bool:
    terminated_without_kill = process.poll() is not None
    try:
        if not terminated_without_kill:
            process.terminate()
            process.wait(timeout=PROCESS_TERMINATION_TIMEOUT_SECONDS)
            terminated_without_kill = True
    except (OSError, subprocess.TimeoutExpired):
        if process.poll() is None:
            try:
                process.kill()
                process.wait(timeout=PROCESS_TERMINATION_TIMEOUT_SECONDS)
            except (OSError, subprocess.TimeoutExpired):
                pass
    finally:
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    if process.poll() is None:
        raise RuntimeError(f"无法清理 smoke 启动的应用进程：PID {process.pid}")
    return terminated_without_kill


def verify_tauri_frontend(
    executable: Path,
    *,
    startup_timeout: float = DEFAULT_STARTUP_TIMEOUT_SECONDS,
    platform: str = sys.platform,
) -> None:
    if not math.isfinite(startup_timeout) or startup_timeout <= 0 or startup_timeout > 120:
        raise RuntimeError("启动观察时间必须大于 0 且不超过 120 秒")

    family = _platform_family(platform)
    clean_termination = True
    with tempfile.TemporaryDirectory(prefix="wandao-package-smoke-") as temporary:
        user_data = Path(temporary) / "user-data"
        user_data.mkdir()
        environment = os.environ.copy()
        for name in (
            "PYTHON",
            "PYTHONHOME",
            "PYTHONPATH",
            "PYTHONUSERBASE",
            "WANDAO_PYTHON",
            "WANDAO_PLUGIN_ALLOW_LOCAL_HTTP",
            "WANDAO_PLUGIN_REGISTRY_URL",
            "WANDAO_EXPERIMENTAL_PLUGIN_REGISTRY_URL",
        ):
            environment.pop(name, None)
        environment["WANDAO_USER_DATA_DIR"] = str(user_data.resolve())

        process: subprocess.Popen[str] | None = None
        try:
            process = subprocess.Popen(
                [str(executable)],
                cwd=executable.parent,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            _observe_application_startup(
                process,
                family=family,
                timeout=startup_timeout,
                expected_window_title=EXPECTED_WINDOW_TITLE,
            )
        finally:
            if process is not None:
                clean_termination = _terminate_process(process)

    if family == "macos" and not clean_termination:
        raise RuntimeError("macOS 应用未能在 smoke 后通过 terminate 干净退出，已强制清理")


def discovered_provider_ids(resources: Path) -> set[str]:
    launcher = resources / "python" / "wandao.py"
    python = packaged_python(resources)
    if not launcher.is_file():
        raise RuntimeError(f"缺少打包后的统一启动器：{launcher}")
    if not python.is_file():
        raise RuntimeError(f"缺少打包后的 Python 运行时：{python}")
    result = subprocess.run(
        [str(python), str(launcher), "--list"],
        cwd=resources / "python",
        env=packaged_python_env(),
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode:
        raise RuntimeError(f"打包后的启动器执行失败（{result.returncode}）：{result.stderr.decode(errors='replace')}")
    return {
        line.split(b"\t", 1)[0].decode("ascii")
        for line in result.stdout.splitlines()
        if b"\t" in line
    }


def verify_packaged_backend_help(resources: Path, provider_ids: set[str]) -> None:
    launcher = resources / "python" / "wandao.py"
    python = packaged_python(resources)
    for provider_id in sorted(provider_ids):
        result = subprocess.run(
            [str(python), str(launcher), "--provider", provider_id, "--", "--help"],
            cwd=resources / "python",
            env=packaged_python_env(),
            capture_output=True,
            check=False,
            timeout=30,
        )
        if result.returncode:
            stderr = result.stderr.decode(errors="replace")
            stdout = result.stdout.decode(errors="replace")
            raise RuntimeError(f"打包后端无法启动：{provider_id}（{result.returncode}）：{stderr or stdout}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="验证 Tauri 安装产物中的 Plugin v1 资源")
    parser.add_argument("--resources", required=True, type=Path, help="安装目录、.app 或 Resources 目录")
    parser.add_argument("--executable", type=Path, help="可选：安装后的 exe、.app 或 .app 主二进制")
    parser.add_argument(
        "--startup-timeout",
        type=float,
        default=DEFAULT_STARTUP_TIMEOUT_SECONDS,
        help="应用必须持续存活的启动观察秒数（默认 10，最大 120）",
    )
    args = parser.parse_args(argv)
    resource_candidate = args.resources.resolve()
    resources = resolve_resources_root(resource_candidate)
    application_executable = resolve_application_executable(resource_candidate, resources, args.executable)
    verify_plugin_trust_store(resources)
    verify_packaged_resource_layout(resources)
    verify_packaged_runtime(resources)
    expected_plugins = {path.name for path in (REPO_ROOT / "plugins").iterdir() if path.is_dir() and (path / "plugin.json").is_file()}
    packaged_plugins = {path.name for path in (resources / "plugins").iterdir() if path.is_dir() and (path / "plugin.json").is_file()}
    if packaged_plugins != expected_plugins:
        raise RuntimeError(f"打包插件不一致：期望 {sorted(expected_plugins)}，实际 {sorted(packaged_plugins)}")
    expected = expected_providers()
    discovered = discovered_provider_ids(resources)
    if discovered != expected:
        raise RuntimeError(f"打包 Provider 发现不一致：期望 {sorted(expected)}，实际 {sorted(discovered)}")
    executable_backends = executable_provider_ids()
    verify_packaged_backend_help(resources, executable_backends)
    verify_tauri_frontend(
        application_executable,
        startup_timeout=args.startup_timeout,
    )
    print(
        f"Packaged application smoke passed ({len(packaged_plugins)} plugins, "
        f"{len(discovered)} providers, {len(executable_backends)} executable backends)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
