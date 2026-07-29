#!/usr/bin/env python3
"""Prepare a portable Python runtime for Wandao release builds.

The downloaded runtime is intentionally kept out of git. Release builders run
this script before the Tauri build so ordinary users can launch Wandao without
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
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request


SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
DESKTOP_DIR = SCRIPT_DIR.parent
PROJECT_DIR = DESKTOP_DIR.parent
DEFAULT_OUTPUT_DIR = DESKTOP_DIR / "runtime" / "python-runtime"
DEFAULT_CACHE_DIR = DESKTOP_DIR / ".runtime-cache"
PYTHON_STANDALONE_RELEASE = "20260623"
PYTHON_STANDALONE_DOWNLOAD_BASE = (
    f"https://github.com/astral-sh/python-build-standalone/releases/download/{PYTHON_STANDALONE_RELEASE}"
)
RUNTIME_METADATA_SCHEMA_VERSION = 1
DIRECT_REQUIREMENTS_NAME = "requirements.txt"
REQUIREMENTS_LOCK_NAME = "requirements.lock"

TARGETS = {
    "win-x64": {
        "asset": "cpython-3.11.15+20260623-x86_64-pc-windows-msvc-install_only_stripped.tar.gz",
        "sha256": "6589ca6d63f520bec4096d62b3ab91da3d0a80b16b594c99a6b677e335814683",
        "exe": pathlib.Path("python.exe"),
        "python_version": "3.11.15",
        "platform": "win32",
        "architecture": "x86_64",
    },
    "mac-x64": {
        "asset": "cpython-3.11.15+20260623-x86_64-apple-darwin-install_only_stripped.tar.gz",
        "sha256": "4925e5aaa9bc77c85302d350b36c1d9def2002996a6bcfa55c88ba6eb318de29",
        "exe": pathlib.Path("bin/python3"),
        "python_version": "3.11.15",
        "platform": "darwin",
        "architecture": "x86_64",
    },
    "mac-arm64": {
        "asset": "cpython-3.11.15+20260623-aarch64-apple-darwin-install_only_stripped.tar.gz",
        "sha256": "2318799eaf104f8a29bc09a93b0851b05dbbcb4ce9a5f045ddea169c0c7ff3a5",
        "exe": pathlib.Path("bin/python3"),
        "python_version": "3.11.15",
        "platform": "darwin",
        "architecture": "aarch64",
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


def _validated_sha256(value: str, *, label: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise SystemExit(f"{label} 必须是 64 位十六进制 SHA256")
    return normalized


def _override_asset_name(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise SystemExit("WANDAO_PYTHON_RUNTIME_URL 必须是不含凭证的 HTTPS URL")
    name = pathlib.PurePosixPath(parsed.path).name
    if (
        not name
        or name in {".", ".."}
        or "\\" in name
        or pathlib.PureWindowsPath(name).name != name
    ):
        raise SystemExit("WANDAO_PYTHON_RUNTIME_URL 必须包含安全的归档文件名")
    return name


def pick_asset(target: str) -> tuple[str, str, str]:
    override = os.environ.get("WANDAO_PYTHON_RUNTIME_URL")
    if override:
        digest = os.environ.get("WANDAO_PYTHON_RUNTIME_SHA256") or str(TARGETS[target]["sha256"])
        return _override_asset_name(override), override, _validated_sha256(digest, label="运行时归档摘要")

    asset_name = str(TARGETS[target]["asset"])
    url_name = asset_name.replace("+", "%2B")
    digest = _validated_sha256(str(TARGETS[target]["sha256"]), label="固定运行时归档摘要")
    return asset_name, f"{PYTHON_STANDALONE_DOWNLOAD_BASE}/{url_name}", digest


def file_sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalized_distribution_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _exact_pins(path: pathlib.Path, *, lock_file: bool) -> dict[str, str]:
    if not path.is_file():
        raise SystemExit(f"Python dependency file does not exist: {path}")

    logical_lines: list[tuple[int, str]] = []
    continued = ""
    continued_at = 0
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if not continued:
            continued_at = line_number
        if line.endswith("\\"):
            continued += line[:-1].rstrip() + " "
            continue
        logical_lines.append((continued_at, (continued + line).strip()))
        continued = ""
    if continued:
        raise SystemExit(f"Python dependency has an unfinished continuation: {path}:{continued_at}")

    pins: dict[str, str] = {}
    for line_number, line in logical_lines:
        if lock_file:
            hashes = re.findall(r"(?:^|\s)--hash=([^\s]+)", line)
            if not hashes or any(
                re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None for value in hashes
            ):
                raise SystemExit(
                    f"Locked Python dependency has an invalid SHA256 hash: "
                    f"{path}:{line_number}"
                )
            line = re.split(r"\s+--hash=", line, maxsplit=1)[0].strip()
        requirement = line.split(";", 1)[0].strip()
        match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s\\]+)", requirement)
        if match is None:
            raise SystemExit(
                f"Python dependency must be an exact name==version pin: {path}:{line_number}"
            )
        name = _normalized_distribution_name(match.group(1))
        version = match.group(2)
        if name in pins:
            raise SystemExit(f"Python dependency is pinned more than once: {name} ({path})")
        pins[name] = version
    if not pins:
        raise SystemExit(f"Python dependency file has no exact pins: {path}")
    return pins


def validate_requirements_lock(
    requirements: pathlib.Path,
    requirements_lock: pathlib.Path,
) -> None:
    direct_pins = _exact_pins(requirements, lock_file=False)
    locked_pins = _exact_pins(requirements_lock, lock_file=True)
    mismatches = [
        f"{name}=={version} (lock: {locked_pins.get(name) or 'missing'})"
        for name, version in sorted(direct_pins.items())
        if locked_pins.get(name) != version
    ]
    if mismatches:
        raise SystemExit(
            "requirements.txt and requirements.lock are inconsistent: " + ", ".join(mismatches)
        )


def verify_archive(path: pathlib.Path, expected_sha256: str) -> None:
    actual = file_sha256(path)
    if actual.lower() != expected_sha256.lower():
        raise SystemExit(f"Python runtime SHA256 校验失败：{path.name}，expected={expected_sha256} actual={actual}")


def download(url: str, destination: pathlib.Path, expected_sha256: str) -> None:
    expected_sha256 = _validated_sha256(expected_sha256, label="运行时归档摘要")
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
            if urllib.parse.urlsplit(response.geturl()).scheme.lower() != "https":
                raise SystemExit("Python runtime 下载重定向到了非 HTTPS 地址")
            with temporary.open("wb") as out:
                shutil.copyfileobj(response, out)
        verify_archive(temporary, expected_sha256)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def _path_within(path: pathlib.Path, root: pathlib.Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _safe_relative_symlink_target(
    destination: pathlib.Path,
    link_path: pathlib.Path,
    link_name: str,
) -> pathlib.Path:
    if not link_name or "\\" in link_name:
        raise SystemExit(f"压缩包包含不安全的符号链接目标：{link_name!r}")
    posix_target = pathlib.PurePosixPath(link_name)
    windows_target = pathlib.PureWindowsPath(link_name)
    if (
        posix_target.is_absolute()
        or windows_target.is_absolute()
        or windows_target.drive
        or ".." in posix_target.parts
    ):
        raise SystemExit(f"压缩包包含不安全的符号链接目标：{link_name}")

    resolved_target = (link_path.parent / pathlib.Path(*posix_target.parts)).resolve()
    if not _path_within(resolved_target, destination):
        raise SystemExit(f"压缩包包含越界的符号链接目标：{link_name}")
    return resolved_target


def _extract_tar(
    archive: pathlib.Path,
    destination: pathlib.Path,
    *,
    allow_relative_symlinks: bool,
) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    dest = destination.resolve()
    with tarfile.open(archive, "r:gz") as tar:
        regular_members = []
        symlink_members = []
        member_paths: set[pathlib.Path] = set()
        for member in tar.getmembers():
            member_path = (dest / member.name).resolve()
            if not _path_within(member_path, dest):
                raise SystemExit(f"压缩包包含不安全路径：{member.name}")
            if member_path in member_paths:
                raise SystemExit(f"压缩包包含重复路径：{member.name}")
            member_paths.add(member_path)

            if member.issym():
                if not allow_relative_symlinks:
                    raise SystemExit(f"压缩包包含不允许的符号链接：{member.name}")
                _safe_relative_symlink_target(dest, member_path, member.linkname)
                symlink_members.append((member, member_path))
                continue
            if member.islnk():
                raise SystemExit(f"压缩包包含不允许的硬链接：{member.name}")
            if member.ischr() or member.isblk():
                raise SystemExit(f"压缩包包含不允许的设备文件：{member.name}")
            if member.isfifo():
                raise SystemExit(f"压缩包包含不允许的 FIFO：{member.name}")
            if not (member.isfile() or member.isdir()):
                raise SystemExit(f"压缩包包含不支持的条目类型：{member.name}")
            regular_members.append(member)

        symlink_paths = {member_path for _, member_path in symlink_members}
        for member_path in member_paths:
            if any(parent in symlink_paths for parent in member_path.parents):
                relative_path = member_path.relative_to(dest)
                raise SystemExit(f"压缩包条目位于符号链接之下：{relative_path}")

        if "filter" in inspect.signature(tar.extractall).parameters:
            tar.extractall(destination, members=regular_members, filter="data")
        else:
            tar.extractall(destination, members=regular_members)

        for member, link_path in symlink_members:
            link_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                link_path.symlink_to(member.linkname)
                resolved_target = link_path.resolve(strict=True)
            except (OSError, RuntimeError) as error:
                raise SystemExit(f"无法安全创建运行时符号链接 {member.name}：{error}")
            if not _path_within(resolved_target, dest):
                raise SystemExit(f"运行时符号链接解析到目录之外：{member.name}")


def safe_extract_tar(archive: pathlib.Path, destination: pathlib.Path) -> None:
    _extract_tar(archive, destination, allow_relative_symlinks=False)


def extract_runtime_tar(
    archive: pathlib.Path,
    destination: pathlib.Path,
    target: str,
    expected_sha256: str,
) -> None:
    pinned_digest = str(TARGETS[target]["sha256"])
    allow_relative_symlinks = (
        target.startswith("mac-")
        and expected_sha256.lower() == pinned_digest.lower()
        and file_sha256(archive).lower() == pinned_digest.lower()
    )
    _extract_tar(
        archive,
        destination,
        allow_relative_symlinks=allow_relative_symlinks,
    )


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
    resolved = output_dir.resolve()
    allowed_root = (DESKTOP_DIR / "runtime").resolve()
    try:
        relative_output = resolved.relative_to(allowed_root)
    except ValueError:
        raise SystemExit(f"拒绝删除非运行时目录：{output_dir}")
    if not relative_output.parts:
        raise SystemExit(f"拒绝删除非运行时目录：{output_dir}")
    if not output_dir.exists():
        return
    shutil.rmtree(output_dir)


def _remove_runtime_path(path: pathlib.Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path)


def _runtime_backup_path(output_dir: pathlib.Path) -> pathlib.Path:
    return output_dir.with_name(f".{output_dir.name}.previous")


def recover_interrupted_runtime_replace(output_dir: pathlib.Path) -> None:
    """Recover the deterministic backup left by an interrupted directory swap."""
    resolved_output = output_dir.resolve()
    allowed_root = (DESKTOP_DIR / "runtime").resolve()
    try:
        relative_output = resolved_output.relative_to(allowed_root)
    except ValueError:
        raise SystemExit(f"拒绝恢复非运行时目录：{output_dir}")
    if not relative_output.parts:
        raise SystemExit(f"拒绝恢复非运行时目录：{output_dir}")

    backup = _runtime_backup_path(output_dir)
    if not backup.exists() and not backup.is_symlink():
        return
    if output_dir.exists() or output_dir.is_symlink():
        _remove_runtime_path(backup)
        return
    if backup.is_symlink() or not backup.is_dir():
        raise SystemExit(f"拒绝恢复无效的运行时备份：{backup}")
    backup.rename(output_dir)


def replace_runtime_output(staged_runtime: pathlib.Path, output_dir: pathlib.Path) -> None:
    """Commit a verified staged runtime while preserving rollback on rename failure."""
    if staged_runtime.is_symlink() or not staged_runtime.is_dir():
        raise SystemExit(f"拒绝提交无效的暂存运行时：{staged_runtime}")
    recover_interrupted_runtime_replace(output_dir)
    backup = _runtime_backup_path(output_dir)
    if backup.exists() or backup.is_symlink():
        raise SystemExit(f"运行时备份目录未能安全恢复：{backup}")

    had_previous = output_dir.exists() or output_dir.is_symlink()
    if had_previous:
        output_dir.rename(backup)
    try:
        staged_runtime.rename(output_dir)
    except BaseException:
        if had_previous and not output_dir.exists() and backup.exists():
            backup.rename(output_dir)
        raise
    if had_previous:
        _remove_runtime_path(backup)


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


def cleanup_packaged_source_caches(project_dir: pathlib.Path | None = None) -> None:
    """Remove generated bytecode from every Python source root bundled by Tauri."""
    root = PROJECT_DIR if project_dir is None else project_dir
    for source_name in ("plugins", "providers", "wandao_core"):
        source_root = root / source_name
        if not source_root.is_dir():
            continue
        cache_dirs = sorted(
            source_root.rglob("__pycache__"),
            key=lambda path: len(path.parts),
            reverse=True,
        )
        for cache_dir in cache_dirs:
            if cache_dir.is_symlink():
                cache_dir.unlink(missing_ok=True)
            elif cache_dir.is_dir():
                shutil.rmtree(cache_dir)
        for pattern in ("*.pyc", "*.pyo"):
            for compiled_file in source_root.rglob(pattern):
                compiled_file.unlink(missing_ok=True)


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
        for name in ("pip", "setuptools", "pkg_resources", "_distutils_hack"):
            shutil.rmtree(site_packages / name, ignore_errors=True)
        (site_packages / "distutils-precedence.pth").unlink(missing_ok=True)
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
        for name in ("pip", "setuptools", "pkg_resources", "_distutils_hack"):
            if (site_packages / name).exists():
                forbidden.append(str((site_packages / name).relative_to(output_dir)))
        precedence = site_packages / "distutils-precedence.pth"
        if precedence.exists():
            forbidden.append(str(precedence.relative_to(output_dir)))
        for pattern in ("pip-*.dist-info", "setuptools-*.dist-info"):
            forbidden.extend(str(path.relative_to(output_dir)) for path in site_packages.glob(pattern))
    if forbidden:
        raise SystemExit(f"运行时仍包含仅构建期工具：{', '.join(sorted(forbidden))}")



def python_executable(output_dir: pathlib.Path, target: str) -> pathlib.Path:
    exe = output_dir / TARGETS[target]["exe"]
    if not exe.exists():
        raise SystemExit(f"运行时缺少 Python 可执行文件：{exe}")
    return exe


def install_requirements(python: pathlib.Path, requirements_lock: pathlib.Path) -> None:
    print("Install Python dependencies...")
    subprocess.check_call([
        str(python),
        "-I",
        "-B",
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-cache-dir",
        "--no-warn-script-location",
        "--require-hashes",
        "--only-binary=:all:",
        "--no-binary=thrift",
        "--no-build-isolation",
        "-r",
        str(requirements_lock),
    ])


def verify_dependencies(python: pathlib.Path) -> None:
    print("Verify Python dependency consistency...")
    subprocess.check_call([str(python), "-I", "-B", "-m", "pip", "check"])


def verify_runtime(python: pathlib.Path, target: str) -> dict[str, object]:
    print("Verify Python runtime...")
    code = (
        "import json, platform, struct, sys, sqlite3, tkinter; "
        "from importlib.metadata import distributions; "
        "import evernote_backup, evernote; "
        "print(json.dumps({'implementation': platform.python_implementation(), "
        "'version': platform.python_version(), 'machine': platform.machine(), "
        "'bits': struct.calcsize('P') * 8, 'platform': sys.platform, "
        "'installedPackages': sorted(f\"{d.metadata['Name']}=={d.version}\" for d in distributions())}))"
    )
    output = subprocess.check_output(
        [str(python), "-I", "-B", "-c", code],
        text=True,
        encoding="utf-8",
        errors="strict",
    )
    try:
        fingerprint = json.loads(output.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as error:
        raise SystemExit(f"无法解析 Python runtime 指纹：{error}") from error

    expected = TARGETS[target]
    machine = str(fingerprint.get("machine") or "").lower()
    architecture = str(expected["architecture"])
    accepted_machines = {
        "x86_64": {"amd64", "x86_64"},
        "aarch64": {"aarch64", "arm64"},
    }[architecture]
    if (
        fingerprint.get("implementation") != "CPython"
        or fingerprint.get("version") != expected["python_version"]
        or fingerprint.get("platform") != expected["platform"]
        or fingerprint.get("bits") != 64
        or machine not in accepted_machines
    ):
        raise SystemExit(f"Python runtime 指纹与 target {target} 不匹配：{fingerprint}")
    fingerprint["architecture"] = architecture
    return fingerprint


def write_build_info(
    output_dir: pathlib.Path,
    target: str,
    archive_sha256: str,
    requirements_sha256: str,
    requirements_lock_sha256: str,
    fingerprint: dict[str, object],
) -> None:
    info = {
        "schemaVersion": RUNTIME_METADATA_SCHEMA_VERSION,
        "target": target,
        "asset": TARGETS[target]["asset"],
        "archiveSha256": archive_sha256,
        "requirementsSha256": requirements_sha256,
        "requirementsLockSha256": requirements_lock_sha256,
        "pythonImplementation": fingerprint["implementation"],
        "pythonVersion": fingerprint["version"],
        "architecture": fingerprint["architecture"],
        "installedPackages": fingerprint["installedPackages"],
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

    requirements = PROJECT_DIR / DIRECT_REQUIREMENTS_NAME
    requirements_lock = PROJECT_DIR / REQUIREMENTS_LOCK_NAME
    validate_requirements_lock(requirements, requirements_lock)

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    recover_interrupted_runtime_replace(output_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    asset_name, url, expected_sha256 = pick_asset(target)
    archive_path = cache_dir / asset_name
    download(url, archive_path, expected_sha256)

    with tempfile.TemporaryDirectory(
        prefix=f".{output_dir.name}-staging-",
        dir=output_dir.parent,
    ) as staging_directory:
        staging_root = pathlib.Path(staging_directory)
        extract_dir = staging_root / "extract"
        extract_runtime_tar(archive_path, extract_dir, target, expected_sha256)
        runtime_root = find_runtime_root(extract_dir, target)
        staged_runtime = staging_root / "prepared"
        shutil.copytree(runtime_root, staged_runtime)

        py = python_executable(staged_runtime, target)
        install_requirements(py, requirements_lock)
        verify_dependencies(py)
        cleanup_runtime(staged_runtime)
        remove_build_only_runtime_files(staged_runtime)
        verify_runtime_is_release_only(staged_runtime)
        cleanup_runtime(staged_runtime)
        fingerprint = verify_runtime(py, target)
        cleanup_runtime(staged_runtime)
        cleanup_packaged_source_caches()
        write_build_info(
            staged_runtime,
            target,
            expected_sha256,
            file_sha256(requirements),
            file_sha256(requirements_lock),
            fingerprint,
        )
        replace_runtime_output(staged_runtime, output_dir)
    print(f"Prepared bundled Python runtime: {output_dir}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare Wandao bundled Python runtime.")
    parser.add_argument("--target", default="auto", choices=["auto", *TARGETS.keys()])
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument(
        "--clean-source-caches-only",
        action="store_true",
        help="remove bundled Python source caches without preparing a runtime",
    )
    args = parser.parse_args()

    if args.clean_source_caches_only:
        cleanup_packaged_source_caches()
        print("Cleaned bundled Python source caches.")
        return 0

    prepare_runtime(
        target=args.target,
        output_dir=pathlib.Path(args.output_dir).resolve(),
        cache_dir=pathlib.Path(args.cache_dir).resolve(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
