import importlib.util
import io
import json
import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SMOKE_SCRIPT = REPO_ROOT / "scripts" / "package_smoke.py"
BUILD_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "build-desktop.yml"
TAURI_BUILD_SCRIPT = REPO_ROOT / "wandao_electron" / "src-tauri" / "build.rs"

spec = importlib.util.spec_from_file_location("package_smoke", PACKAGE_SMOKE_SCRIPT)
package_smoke = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(package_smoke)


class FakeProcess:
    def __init__(self, *, return_code: int | None = None, ignore_terminate: bool = False) -> None:
        self.pid = 4242
        self.returncode = return_code
        self.ignore_terminate = ignore_terminate
        self.terminate_called = False
        self.kill_called = False
        self.stdout = io.StringIO("stdout")
        self.stderr = io.StringIO("stderr")

    def poll(self) -> int | None:
        return self.returncode

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        del timeout
        return self.stdout.getvalue(), self.stderr.getvalue()

    def terminate(self) -> None:
        self.terminate_called = True
        if not self.ignore_terminate:
            self.returncode = -15

    def kill(self) -> None:
        self.kill_called = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            raise subprocess.TimeoutExpired("wandao", timeout)
        return self.returncode


def create_resource_root(root: Path) -> Path:
    (root / "plugins").mkdir(parents=True)
    (root / "python-runtime").mkdir()
    return root


def create_macos_app(root: Path, executable_name: str = "wandao") -> tuple[Path, Path]:
    app = root / "Wandao.app"
    resources = create_resource_root(app / "Contents" / "Resources")
    executable_root = app / "Contents" / "MacOS"
    executable_root.mkdir()
    executable = executable_root / executable_name
    executable.touch()
    with (app / "Contents" / "Info.plist").open("wb") as stream:
        plistlib.dump({"CFBundleExecutable": executable_name}, stream)
    return app, resources


def create_packaged_runtime(resources: Path, target: str = "win-x64") -> dict[str, object]:
    runtime = resources / "python-runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    interpreter = runtime / ("python.exe" if target == "win-x64" else "bin/python3")
    interpreter.parent.mkdir(parents=True, exist_ok=True)
    interpreter.touch()
    expected = package_smoke.RUNTIME_TARGETS[target]
    installed_packages = ["evernote-backup==1.13.1"]
    metadata: dict[str, object] = {
        "schemaVersion": package_smoke.RUNTIME_METADATA_SCHEMA_VERSION,
        "target": target,
        "asset": expected["asset"],
        "archiveSha256": expected["archiveSha256"],
        "requirementsSha256": package_smoke.file_sha256(REPO_ROOT / "requirements.txt"),
        "requirementsLockSha256": package_smoke.file_sha256(REPO_ROOT / "requirements.lock"),
        "pythonImplementation": "CPython",
        "pythonVersion": expected["pythonVersion"],
        "architecture": expected["architecture"],
        "installedPackages": installed_packages,
        "preparedBy": "wandao_electron/scripts/prepare_python_runtime.py",
    }
    (runtime / "WANDAO_RUNTIME.json").write_text(json.dumps(metadata), encoding="utf-8")
    return metadata


def runtime_fingerprint(metadata: dict[str, object]) -> dict[str, object]:
    return {
        "implementation": "CPython",
        "version": metadata["pythonVersion"],
        "machine": "AMD64",
        "bits": 64,
        "platform": "win32",
        "installedPackages": metadata["installedPackages"],
    }


class PackagedRuntimeVerificationTests(unittest.TestCase):
    def test_python_environment_isolated_and_forbids_bytecode_writes(self) -> None:
        polluted = {
            "PYTHONHOME": "host-home",
            "PYTHONPATH": "host-path",
            "PYTHONSTARTUP": "host-startup.py",
            "PYTHONUSERBASE": "host-user-base",
        }
        with mock.patch.dict(package_smoke.os.environ, polluted, clear=True):
            environment = package_smoke.packaged_python_env()

        for name in polluted:
            self.assertNotIn(name, environment)
        self.assertEqual(environment["PYTHONDONTWRITEBYTECODE"], "1")
        self.assertEqual(environment["PYTHONNOUSERSITE"], "1")
        self.assertEqual(environment["PYTHONUTF8"], "1")

    def test_valid_runtime_metadata_and_live_fingerprint_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            resources = Path(temporary)
            metadata = create_packaged_runtime(resources)
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=json.dumps(runtime_fingerprint(metadata)),
                stderr="",
            )
            with mock.patch.object(package_smoke.subprocess, "run", return_value=completed) as run:
                package_smoke.verify_packaged_runtime(
                    resources,
                    platform="win32",
                    machine="AMD64",
                )

            command = run.call_args.args[0]
            self.assertEqual(command[1:3], ["-I", "-B"])
            self.assertEqual(command[3], "-c")
            self.assertIn(
                "import evernote, evernote_backup, json, platform, sqlite3, struct, sys, tkinter",
                command[4],
            )
            environment = run.call_args.kwargs["env"]
            self.assertEqual(environment["PYTHONDONTWRITEBYTECODE"], "1")
            self.assertNotIn("PYTHONPATH", environment)

    def test_runtime_metadata_rejects_supply_chain_and_architecture_mismatches(self) -> None:
        mismatches: dict[str, object] = {
            "archiveSha256": "0" * 64,
            "requirementsSha256": "f" * 64,
            "requirementsLockSha256": "e" * 64,
            "architecture": "aarch64",
        }
        for field, value in mismatches.items():
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                resources = Path(temporary)
                metadata = create_packaged_runtime(resources)
                metadata[field] = value
                (resources / "python-runtime" / "WANDAO_RUNTIME.json").write_text(
                    json.dumps(metadata),
                    encoding="utf-8",
                )
                with mock.patch.object(package_smoke.subprocess, "run") as run:
                    with self.assertRaisesRegex(RuntimeError, "元数据与候选平台不一致"):
                        package_smoke.verify_packaged_runtime(
                            resources,
                            platform="win32",
                            machine="AMD64",
                        )
                run.assert_not_called()

    def test_runtime_fingerprint_fails_when_a_required_import_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            resources = Path(temporary)
            create_packaged_runtime(resources)
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=1,
                stdout="",
                stderr="ModuleNotFoundError: No module named 'tkinter'",
            )
            with mock.patch.object(package_smoke.subprocess, "run", return_value=completed):
                with self.assertRaisesRegex(RuntimeError, "runtime 指纹检查.*ModuleNotFoundError"):
                    package_smoke.verify_packaged_runtime(
                        resources,
                        platform="win32",
                        machine="AMD64",
                    )

    def test_runtime_rejects_generated_cache_and_build_tooling(self) -> None:
        violations = (
            (Path("Lib") / "__pycache__" / "module.pyc", "生成的 Python 缓存"),
            (Path("Lib") / "site-packages" / "pip" / "__init__.py", "包管理工具"),
        )
        for relative, message in violations:
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as temporary:
                resources = Path(temporary)
                create_packaged_runtime(resources)
                violation = resources / "python-runtime" / relative
                violation.parent.mkdir(parents=True, exist_ok=True)
                violation.touch()
                with self.assertRaisesRegex(RuntimeError, message):
                    package_smoke.verify_packaged_runtime(
                        resources,
                        platform="win32",
                        machine="AMD64",
                    )

    def test_runtime_rejects_a_broken_or_escaping_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            resources = Path(temporary)
            create_packaged_runtime(resources)
            link = resources / "python-runtime" / "unsafe-link"
            link.touch()
            original_is_symlink = Path.is_symlink
            original_resolve = Path.resolve

            def fake_is_symlink(path: Path) -> bool:
                return path == link or original_is_symlink(path)

            def fake_resolve(path: Path, strict: bool = False) -> Path:
                if path == link:
                    raise OSError("simulated broken link")
                return original_resolve(path, strict=strict)

            with (
                mock.patch.object(Path, "is_symlink", fake_is_symlink),
                mock.patch.object(Path, "resolve", fake_resolve),
            ):
                with self.assertRaisesRegex(RuntimeError, "损坏或越界的符号链接"):
                    package_smoke.verify_packaged_runtime(
                        resources,
                        platform="win32",
                        machine="AMD64",
                    )


class PackagedResourceIntegrityTests(unittest.TestCase):
    def test_feishu_guide_images_are_remote_only_package_resources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            resources = Path(temporary)
            remote_only = resources / package_smoke.REMOTE_ONLY_GUIDE_ASSET_PATH
            remote_only.mkdir(parents=True)
            (remote_only / "1.png").write_bytes(b"not packaged")

            with self.assertRaisesRegex(RuntimeError, "远程教程图片不应进入安装包"):
                package_smoke.verify_packaged_resource_layout(resources)

    def test_packaged_requirements_lock_must_match_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            resources = root / "resources"
            for relative in (
                Path("plugins"),
                Path("providers"),
                Path("wandao_core"),
                Path("wandao_electron/assets"),
            ):
                (source / relative).mkdir(parents=True)
            for relative in (
                Path("plugins"),
                Path("providers"),
                Path("python/wandao_core"),
                Path("assets"),
            ):
                (resources / relative).mkdir(parents=True)
            (source / "requirements.txt").write_text("example==1\n", encoding="utf-8")
            (source / "requirements.lock").write_text("locked\n", encoding="utf-8")
            (resources / "python/requirements.txt").write_text(
                "example==1\n", encoding="utf-8"
            )
            packaged_lock = resources / "python/requirements.lock"
            packaged_lock.write_text("locked\n", encoding="utf-8")

            with mock.patch.object(package_smoke, "REPO_ROOT", source):
                package_smoke.verify_packaged_resource_layout(resources)
                packaged_lock.write_text("tampered\n", encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "requirements.lock"):
                    package_smoke.verify_packaged_resource_layout(resources)

    def test_resource_tree_accepts_exact_copy_and_rejects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            packaged = root / "packaged"
            source.mkdir()
            packaged.mkdir()
            (source / "module.py").write_text("value = 1\n", encoding="utf-8")
            (packaged / "module.py").write_text("value = 1\n", encoding="utf-8")

            package_smoke._verify_resource_tree(source, packaged, label="测试资源")
            (packaged / "module.py").write_text("value = 2\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "changed=.*module.py"):
                package_smoke._verify_resource_tree(source, packaged, label="测试资源")

    def test_resource_tree_rejects_missing_files_and_generated_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            packaged = root / "packaged"
            source.mkdir()
            packaged.mkdir()
            (source / "required.py").touch()
            with self.assertRaisesRegex(RuntimeError, "missing=.*required.py"):
                package_smoke._verify_resource_tree(source, packaged, label="测试资源")

            (packaged / "required.py").touch()
            cache = packaged / "__pycache__" / "required.pyc"
            cache.parent.mkdir()
            cache.touch()
            with self.assertRaisesRegex(RuntimeError, "生成缓存"):
                package_smoke._verify_resource_tree(source, packaged, label="测试资源")

    def test_resource_tree_rejects_extra_empty_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            packaged = root / "packaged"
            source.mkdir()
            packaged.mkdir()
            (source / "module.py").touch()
            (packaged / "module.py").touch()
            (packaged / "unsigned-empty-directory").mkdir()

            with self.assertRaisesRegex(RuntimeError, "extraDirectories=.*unsigned-empty-directory"):
                package_smoke._verify_resource_tree(source, packaged, label="测试资源")

            (packaged / "unsigned-empty-directory").rmdir()
            (source / "unsigned-empty-directory").mkdir()
            with self.assertRaisesRegex(RuntimeError, "源码.*未受文件清单约束的空目录"):
                package_smoke._verify_resource_tree(source, packaged, label="测试资源")

    def test_resource_tree_rejects_mixed_case_python_cache_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            packaged = root / "packaged"
            source.mkdir()
            packaged.mkdir()
            (source / "module.py").touch()
            (packaged / "module.py").touch()
            (packaged / "__PyCache__").mkdir()

            with self.assertRaisesRegex(RuntimeError, "生成缓存"):
                package_smoke._verify_resource_tree(source, packaged, label="测试资源")

    def test_build_script_checks_python_cache_directories_without_case_sensitivity(self) -> None:
        source = TAURI_BUILD_SCRIPT.read_text(encoding="utf-8")

        self.assertIn('eq_ignore_ascii_case("__pycache__")', source)
        self.assertIn("!is_python_cache_directory_name(&entry.file_name())", source)
        self.assertIn('if lowercase_name == "__pycache__"', source)

    def test_resource_tree_rejects_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            packaged = Path(temporary) / "packaged"
            packaged.mkdir()
            link = packaged / "linked.py"
            link.touch()
            original_is_symlink = Path.is_symlink

            def fake_is_symlink(path: Path) -> bool:
                return path == link or original_is_symlink(path)

            with mock.patch.object(Path, "is_symlink", fake_is_symlink):
                with self.assertRaisesRegex(RuntimeError, "包含符号链接"):
                    package_smoke._resource_hashes(packaged, label="测试资源")


class PackagedApplicationResolutionTests(unittest.TestCase):
    def test_resolves_named_windows_executable_without_selecting_uninstaller(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install_root = create_resource_root(Path(temporary))
            executable = install_root / "Wandao.exe"
            executable.touch()
            (install_root / "uninstall.exe").touch()

            resources = package_smoke.resolve_resources_root(install_root)
            resolved = package_smoke.resolve_application_executable(
                install_root,
                resources,
                platform="win32",
            )

            self.assertEqual(resolved, executable.resolve())

    def test_rejects_ambiguous_windows_executables(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install_root = create_resource_root(Path(temporary))
            (install_root / "first.exe").touch()
            (install_root / "second.exe").touch()

            with self.assertRaisesRegex(RuntimeError, "--executable"):
                package_smoke.resolve_application_executable(
                    install_root,
                    install_root,
                    platform="win32",
                )

    def test_resolves_macos_main_binary_from_app_info_plist(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app, resources = create_macos_app(Path(temporary))

            resolved_from_app = package_smoke.resolve_application_executable(
                app,
                resources,
                platform="darwin",
            )
            resolved_from_resources = package_smoke.resolve_application_executable(
                resources,
                resources,
                platform="darwin",
            )

            expected = (app / "Contents" / "MacOS" / "wandao").resolve()
            self.assertEqual(resolved_from_app, expected)
            self.assertEqual(resolved_from_resources, expected)

    def test_rejects_macos_bundle_executable_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app, _resources = create_macos_app(Path(temporary))
            with (app / "Contents" / "Info.plist").open("wb") as stream:
                plistlib.dump({"CFBundleExecutable": "../Resources/wandao"}, stream)

            with self.assertRaisesRegex(RuntimeError, "CFBundleExecutable"):
                package_smoke.resolve_macos_bundle_executable(app)


class PackagedApplicationLaunchTests(unittest.TestCase):
    def test_rejects_non_finite_startup_timeout_before_launch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "Wandao.exe"
            executable.touch()
            with mock.patch.object(package_smoke.subprocess, "Popen") as launch:
                with self.assertRaisesRegex(RuntimeError, "120"):
                    package_smoke.verify_tauri_frontend(
                        executable,
                        startup_timeout=float("nan"),
                        platform="win32",
                    )
            launch.assert_not_called()

    def test_windows_launch_uses_isolated_user_data_and_visible_expected_window(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "Wandao.exe"
            executable.touch()
            process = FakeProcess()
            invocation: dict[str, object] = {}

            def launch(*args: object, **kwargs: object) -> FakeProcess:
                invocation["args"] = args
                invocation["kwargs"] = kwargs
                return process

            with (
                mock.patch.object(package_smoke.subprocess, "Popen", side_effect=launch),
                mock.patch.object(
                    package_smoke,
                    "visible_window_titles_for_pid",
                    return_value=[package_smoke.EXPECTED_WINDOW_TITLE],
                ),
                mock.patch.dict(
                    package_smoke.os.environ,
                    {
                        "PYTHON": "host-python",
                        "WANDAO_PYTHON": "host-wandao-python",
                        "WANDAO_PLUGIN_ALLOW_LOCAL_HTTP": "1",
                        "WANDAO_PLUGIN_REGISTRY_URL": "http://127.0.0.1:9999",
                        "WANDAO_USER_DATA_DIR": str(Path(temporary) / "real-user-data"),
                    },
                ),
            ):
                package_smoke.verify_tauri_frontend(
                    executable,
                    startup_timeout=0.001,
                    platform="win32",
                )

            kwargs = invocation["kwargs"]
            assert isinstance(kwargs, dict)
            environment = kwargs["env"]
            assert isinstance(environment, dict)
            isolated_user_data = Path(environment["WANDAO_USER_DATA_DIR"])
            self.assertNotEqual(isolated_user_data, Path(temporary) / "real-user-data")
            self.assertFalse(isolated_user_data.exists())
            self.assertNotIn("PYTHON", environment)
            self.assertNotIn("WANDAO_PYTHON", environment)
            self.assertNotIn("WANDAO_PLUGIN_ALLOW_LOCAL_HTTP", environment)
            self.assertNotIn("WANDAO_PLUGIN_REGISTRY_URL", environment)
            self.assertEqual(kwargs["cwd"], executable.parent)
            self.assertTrue(process.terminate_called)
            self.assertFalse(process.kill_called)

    def test_startup_crash_fails_and_still_removes_isolated_user_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "wandao"
            executable.touch()
            process = FakeProcess(return_code=23)
            isolated_paths: list[Path] = []

            def launch(*args: object, **kwargs: object) -> FakeProcess:
                del args
                isolated_paths.append(Path(kwargs["env"]["WANDAO_USER_DATA_DIR"]))
                return process

            with mock.patch.object(package_smoke.subprocess, "Popen", side_effect=launch):
                with self.assertRaisesRegex(RuntimeError, "23"):
                    package_smoke.verify_tauri_frontend(
                        executable,
                        startup_timeout=0.001,
                        platform="darwin",
                    )

            self.assertEqual(len(isolated_paths), 1)
            self.assertFalse(isolated_paths[0].exists())

    def test_windows_requires_the_expected_visible_window_title(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "Wandao.exe"
            executable.touch()
            process = FakeProcess()
            with (
                mock.patch.object(package_smoke.subprocess, "Popen", return_value=process),
                mock.patch.object(
                    package_smoke,
                    "visible_window_titles_for_pid",
                    return_value=["Unexpected title"],
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "Unexpected title"):
                    package_smoke.verify_tauri_frontend(
                        executable,
                        startup_timeout=0.001,
                        platform="win32",
                    )

            self.assertTrue(process.terminate_called)

    def test_windows_window_must_still_be_visible_at_end_of_observation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "Wandao.exe"
            executable.touch()
            process = FakeProcess()
            observations = 0

            def window_titles(_pid: int) -> list[str]:
                nonlocal observations
                observations += 1
                if observations == 1:
                    return [package_smoke.EXPECTED_WINDOW_TITLE]
                return []

            with (
                mock.patch.object(package_smoke.subprocess, "Popen", return_value=process),
                mock.patch.object(
                    package_smoke,
                    "visible_window_titles_for_pid",
                    side_effect=window_titles,
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "可见主窗口"):
                    package_smoke.verify_tauri_frontend(
                        executable,
                        startup_timeout=0.001,
                        platform="win32",
                    )

            self.assertGreaterEqual(observations, 2)
            self.assertTrue(process.terminate_called)

    def test_macos_forced_kill_is_a_failed_clean_termination_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "wandao"
            executable.touch()
            process = FakeProcess(ignore_terminate=True)
            with mock.patch.object(package_smoke.subprocess, "Popen", return_value=process):
                with self.assertRaisesRegex(RuntimeError, "干净退出"):
                    package_smoke.verify_tauri_frontend(
                        executable,
                        startup_timeout=0.001,
                        platform="darwin",
                    )

            self.assertTrue(process.terminate_called)
            self.assertTrue(process.kill_called)


class PackagedSmokeContractTests(unittest.TestCase):
    def test_resource_count_gates_remain_complete(self) -> None:
        expected_plugins = {
            path.name
            for path in (REPO_ROOT / "plugins").iterdir()
            if path.is_dir() and (path / "plugin.json").is_file()
        }
        self.assertEqual(len(expected_plugins), 14)
        expected_counts = {
            "win32": (20, 19),
            "darwin": (19, 18),
            "linux": (18, 17),
        }
        for target_platform, (provider_count, executable_count) in expected_counts.items():
            with self.subTest(platform=target_platform):
                self.assertEqual(
                    len(package_smoke.expected_providers(target_platform)),
                    provider_count,
                )
                self.assertEqual(
                    len(package_smoke.executable_provider_ids(target_platform)),
                    executable_count,
                )

    def test_platform_specific_providers_are_excluded_from_smoke_expectations(self) -> None:
        self.assertIn("onenote", package_smoke.expected_providers("win32"))
        self.assertNotIn("onenote", package_smoke.expected_providers("darwin"))
        self.assertNotIn("onenote", package_smoke.expected_providers("linux"))
        self.assertIn("dingtalk-export", package_smoke.expected_providers("darwin"))
        self.assertNotIn("dingtalk-export", package_smoke.expected_providers("linux"))

    def test_every_ci_package_smoke_passes_an_actual_application_path(self) -> None:
        workflow = BUILD_WORKFLOW.read_text(encoding="utf-8")
        invocations = [
            line.strip()
            for line in workflow.splitlines()
            if "python scripts/package_smoke.py" in line
        ]

        self.assertEqual(len(invocations), 3)
        self.assertTrue(all("--resources" in line for line in invocations))
        self.assertTrue(all("--executable" in line for line in invocations))
        self.assertIn("WANDAO_SMOKE_EXECUTABLE", workflow)


if __name__ == "__main__":
    unittest.main()
