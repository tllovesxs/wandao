#!/usr/bin/env python3
"""Prepare a portable Python runtime for Wandao release builds.

The downloaded runtime is intentionally kept out of git. Release builders run
this script before electron-builder so ordinary users can launch Wandao without
installing Python manually.
"""

from __future__ import annotations

import argparse
import inspect
import json
import os
import pathlib
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import urllib.error


SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
ELECTRON_DIR = SCRIPT_DIR.parent
PROJECT_DIR = ELECTRON_DIR.parent
DEFAULT_OUTPUT_DIR = ELECTRON_DIR / "runtime" / "python-runtime"
DEFAULT_CACHE_DIR = ELECTRON_DIR / ".runtime-cache"
PYTHON_STANDALONE_API = "https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest"

TARGETS = {
    "win-x64": {
        "asset": r"cpython-3\.11\..*x86_64-pc-windows-msvc-install_only_stripped\.tar\.gz$",
        "exe": pathlib.Path("python.exe"),
    },
    "mac-x64": {
        "asset": r"cpython-3\.11\..*x86_64-apple-darwin-install_only_stripped\.tar\.gz$",
        "exe": pathlib.Path("bin/python3"),
    },
    "mac-arm64": {
        "asset": r"cpython-3\.11\..*aarch64-apple-darwin-install_only_stripped\.tar\.gz$",
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


def github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "wandao-build",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def request_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=github_headers())
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in {403, 429}:
            raise SystemExit(
                "GitHub API 请求被限流，无法获取 Python standalone release。"
                "请在 GitHub Actions 中传入 GITHUB_TOKEN，或设置 WANDAO_PYTHON_RUNTIME_URL 指向运行时压缩包。"
            ) from exc
        raise


def pick_asset(target: str) -> tuple[str, str]:
    override = os.environ.get("WANDAO_PYTHON_RUNTIME_URL")
    if override:
        return pathlib.PurePosixPath(override.split("?")[0]).name, override

    release = request_json(PYTHON_STANDALONE_API)
    pattern = re.compile(TARGETS[target]["asset"])
    for asset in release.get("assets", []):
        name = asset.get("name", "")
        if pattern.match(name):
            return name, asset["browser_download_url"]
    raise SystemExit(f"没有找到 {target} 对应的 Python standalone 资源")


def download(url: str, destination: pathlib.Path) -> None:
    if destination.exists() and destination.stat().st_size > 0:
        print(f"Reuse cached runtime archive: {destination}")
        return
    print(f"Download Python runtime: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "wandao-build"})
    with urllib.request.urlopen(req, timeout=180) as response:
        with destination.open("wb") as out:
            shutil.copyfileobj(response, out)


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
    for folder_name in ("__pycache__", "test", "tests"):
        for folder in output_dir.rglob(folder_name):
            if folder.is_dir():
                shutil.rmtree(folder, ignore_errors=True)
    for pyc in output_dir.rglob("*.pyc"):
        try:
            pyc.unlink()
        except OSError:
            pass


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
    asset_name, url = pick_asset(target)
    archive_path = cache_dir / asset_name
    download(url, archive_path)

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
