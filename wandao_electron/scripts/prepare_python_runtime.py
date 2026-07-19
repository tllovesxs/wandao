#!/usr/bin/env python3
"""Prepare a portable Python runtime for Wandao release builds.

The downloaded runtime is intentionally kept out of git. Release builders run
this script before electron-builder so ordinary users can launch Wandao without
installing Python manually.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
import pathlib
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request


SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
ELECTRON_DIR = SCRIPT_DIR.parent
PROJECT_DIR = ELECTRON_DIR.parent
DEFAULT_OUTPUT_DIR = ELECTRON_DIR / "runtime" / "python-runtime"
DEFAULT_CACHE_DIR = ELECTRON_DIR / ".runtime-cache"
PYTHON_STANDALONE_RELEASE = "20260623"
PYTHON_STANDALONE_DOWNLOAD_BASE = (
    f"https://github.com/astral-sh/python-build-standalone/releases/download/{PYTHON_STANDALONE_RELEASE}"
)

TARGETS = {
    "win-x64": {
        "asset": "cpython-3.11.15+20260623-x86_64-pc-windows-msvc-install_only_stripped.tar.gz",
        "sha256": "6589ca6d63f520bec4096d62b3ab91da3d0a80b16b594c99a6b677e335814683",
        "exe": pathlib.Path("python.exe"),
    },
    "mac-x64": {
        "asset": "cpython-3.11.15+20260623-x86_64-apple-darwin-install_only_stripped.tar.gz",
        "sha256": "4925e5aaa9bc77c85302d350b36c1d9def2002996a6bcfa55c88ba6eb318de29",
        "exe": pathlib.Path("bin/python3"),
    },
    "mac-arm64": {
        "asset": "cpython-3.11.15+20260623-aarch64-apple-darwin-install_only_stripped.tar.gz",
        "sha256": "2318799eaf104f8a29bc09a93b0851b05dbbcb4ce9a5f045ddea169c0c7ff3a5",
        "exe": pathlib.Path("bin/python3"),
    },
}


def host_target() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "windows":
        return "win-x64"
    if system == "darwin":
        if machine in {"arm64", "aarch64"}:
            return "mac-arm64"
        return "mac-x64"
    raise SystemExit(f"当前系统暂不支持自动准备运行时：{platform.system()} {platform.machine()}")


def pick_asset(target: str) -> tuple[str, str, str]:
    override = os.environ.get("WANDAO_PYTHON_RUNTIME_URL")
    if override:
        digest = os.environ.get("WANDAO_PYTHON_RUNTIME_SHA256") or TARGETS[target]["sha256"]
        return pathlib.PurePosixPath(override.split("?")[0]).name, override, digest

    asset_name = str(TARGETS[target]["asset"])
    url_name = asset_name.replace("+", "%2B")
    return asset_name, f"{PYTHON_STANDALONE_DOWNLOAD_BASE}/{url_name}", str(TARGETS[target]["sha256"])


def file_sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_archive(path: pathlib.Path, expected_sha256: str) -> None:
    actual = file_sha256(path)
    if actual.lower() != expected_sha256.lower():
        raise SystemExit(f"Python runtime SHA256 校验失败：{path.name}，expected={expected_sha256} actual={actual}")


def download(url: str, destination: pathlib.Path, expected_sha256: str) -> None:
    if destination.exists() and destination.stat().st_size > 0:
        try:
            verify_archive(destination, expected_sha256)
            print(f"Reuse cached runtime archive: {destination}")
            return
        except SystemExit:
            destination.unlink()
    print(f"Download Python runtime: {url}")
    temporary = destination.with_suffix(destination.suffix + ".part")
    if temporary.exists():
        temporary.unlink()
    req = urllib.request.Request(url, headers={"User-Agent": "wandao-build"})
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            with temporary.open("wb") as out:
                shutil.copyfileobj(response, out)
        verify_archive(temporary, expected_sha256)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def safe_extract_tar(archive: pathlib.Path, destination: pathlib.Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    dest = destination.resolve()
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            member_path = (destination / member.name).resolve()
            if not str(member_path).startswith(str(dest)):
                raise SystemExit(f"压缩包包含不安全路径：{member.name}")
        if "filter" in inspect.signature(tar.extractall).parameters:
            tar.extractall(destination, filter="data")
        else:
            tar.extractall(destination)


def find_runtime_root(extract_dir: pathlib.Path, target: str) -> pathlib.Path:
    relative_exe = TARGETS[target]["exe"]
    candidates = []
    if relative_exe.name == "python.exe":
        candidates = [p.parent for p in extract_dir.rglob("python.exe")]
    else:
        candidates = [p.parent.parent for p in extract_dir.rglob("bin/python3")]

    for candidate in candidates:
        if (candidate / relative_exe).exists():
            return candidate
    raise SystemExit("解压后没有找到可用的 Python 可执行文件")


def remove_previous_output(output_dir: pathlib.Path) -> None:
    if not output_dir.exists():
        return
    resolved = output_dir.resolve()
    allowed_root = (ELECTRON_DIR / "runtime").resolve()
    if not str(resolved).startswith(str(allowed_root)):
        raise SystemExit(f"拒绝删除非运行时目录：{output_dir}")
    shutil.rmtree(output_dir)


def cleanup_runtime(output_dir: pathlib.Path) -> None:
    for folder_name in ("__pycache__", ".pytest_cache", "test", "tests"):
        for folder in output_dir.rglob(folder_name):
            if folder.is_dir():
                shutil.rmtree(folder, ignore_errors=True)
    for pattern in ("*.pyc", "*.pyo"):
        for compiled_file in output_dir.rglob(pattern):
            try:
                compiled_file.unlink()
            except OSError:
                pass

def remove_build_only_runtime_files(output_dir: pathlib.Path) -> None:
    """Remove installation tooling after dependencies have been installed and verified.

    The bundled interpreter never installs packages on an end user's machine.
    Keeping pip, setuptools and ensurepip in a release only increases the
    installer size and exposes unnecessary package-management entry points.
    """
    for relative_dir in ("Lib/ensurepip", "lib/python3.11/ensurepip"):
        shutil.rmtree(output_dir / relative_dir, ignore_errors=True)

    for site_packages in output_dir.rglob("site-packages"):
        if not site_packages.is_dir():
            continue
        for name in ("pip", "setuptools", "pkg_resources"):
            shutil.rmtree(site_packages / name, ignore_errors=True)
        for pattern in ("pip-*.dist-info", "setuptools-*.dist-info"):
            for metadata in site_packages.glob(pattern):
                shutil.rmtree(metadata, ignore_errors=True)

    for scripts_dir in (output_dir / "Scripts", output_dir / "bin"):
        if not scripts_dir.is_dir():
            continue
        for pattern in ("pip*", "easy_install*"):
            for script in scripts_dir.glob(pattern):
                if script.is_file() or script.is_symlink():
                    script.unlink(missing_ok=True)


def verify_runtime_is_release_only(output_dir: pathlib.Path) -> None:
    forbidden = []
    for relative_dir in ("Lib/ensurepip", "lib/python3.11/ensurepip"):
        candidate = output_dir / relative_dir
        if candidate.exists():
            forbidden.append(str(candidate.relative_to(output_dir)))
    for site_packages in output_dir.rglob("site-packages"):
        for name in ("pip", "setuptools", "pkg_resources"):
            if (site_packages / name).exists():
                forbidden.append(str((site_packages / name).relative_to(output_dir)))
        for pattern in ("pip-*.dist-info", "setuptools-*.dist-info"):
            forbidden.extend(str(path.relative_to(output_dir)) for path in site_packages.glob(pattern))
    if forbidden:
        raise SystemExit(f"运行时仍包含仅构建期工具：{', '.join(sorted(forbidden))}")



def python_executable(output_dir: pathlib.Path, target: str) -> pathlib.Path:
    exe = output_dir / TARGETS[target]["exe"]
    if not exe.exists():
        raise SystemExit(f"运行时缺少 Python 可执行文件：{exe}")
    return exe


def install_requirements(python: pathlib.Path, requirements: pathlib.Path) -> None:
    print("Install Python dependencies...")
    subprocess.check_call([
        str(python),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-cache-dir",
        "--no-warn-script-location",
        "-r",
        str(requirements),
    ])


def verify_runtime(python: pathlib.Path) -> None:
    print("Verify Python runtime...")
    code = "import sys, sqlite3, tkinter; import evernote_backup, evernote; print(sys.version)"
    subprocess.check_call([str(python), "-c", code])


def write_build_info(output_dir: pathlib.Path, target: str, asset_name: str) -> None:
    info = {
        "target": target,
        "asset": asset_name,
        "preparedBy": "wandao_electron/scripts/prepare_python_runtime.py",
    }
    (output_dir / "WANDAO_RUNTIME.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def prepare_runtime(target: str, output_dir: pathlib.Path, cache_dir: pathlib.Path) -> None:
    if target == "auto":
        target = host_target()
    if target not in TARGETS:
        raise SystemExit(f"未知 target：{target}，可选：auto, {', '.join(TARGETS)}")

    cache_dir.mkdir(parents=True, exist_ok=True)
    asset_name, url, expected_sha256 = pick_asset(target)
    archive_path = cache_dir / asset_name
    download(url, archive_path, expected_sha256)

    with tempfile.TemporaryDirectory(prefix="wandao-python-runtime-") as tmp:
        extract_dir = pathlib.Path(tmp) / "extract"
        safe_extract_tar(archive_path, extract_dir)
        runtime_root = find_runtime_root(extract_dir, target)
        remove_previous_output(output_dir)
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(runtime_root, output_dir)

    py = python_executable(output_dir, target)
    install_requirements(py, PROJECT_DIR / "requirements.txt")
    cleanup_runtime(output_dir)
    verify_runtime(py)
    remove_build_only_runtime_files(output_dir)
    verify_runtime_is_release_only(output_dir)
    write_build_info(output_dir, target, asset_name)
    print(f"Prepared bundled Python runtime: {output_dir}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare Wandao bundled Python runtime.")
    parser.add_argument("--target", default="auto", choices=["auto", *TARGETS.keys()])
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    args = parser.parse_args()

    prepare_runtime(
        target=args.target,
        output_dir=pathlib.Path(args.output_dir).resolve(),
        cache_dir=pathlib.Path(args.cache_dir).resolve(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
