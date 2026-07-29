import importlib.util
import io
import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
PREPARE_SCRIPT = REPO_ROOT / "wandao_electron" / "scripts" / "prepare_python_runtime.py"
BUILD_SCRIPT = REPO_ROOT / "wandao_electron" / "src-tauri" / "build.rs"
PACKAGE_SMOKE_SCRIPT = REPO_ROOT / "scripts" / "package_smoke.py"
QUALITY_SCRIPT = REPO_ROOT / "scripts" / "quality_check.py"
RUST_TOOLCHAIN_FILE = REPO_ROOT / "rust-toolchain.toml"
BUILD_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "build-desktop.yml"
DESKTOP_README = REPO_ROOT / "wandao_electron" / "README.md"
RELEASE_GUIDE = REPO_ROOT / "docs" / "发布与回滚手册.md"
PR_TEMPLATE = REPO_ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md"

spec = importlib.util.spec_from_file_location("prepare_python_runtime", PREPARE_SCRIPT)
prepare_python_runtime = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(prepare_python_runtime)


class SafeTarExtractionTests(unittest.TestCase):
    def test_rejects_sibling_path_with_destination_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "runtime.tar.gz"
            payload = b"escaped"
            with tarfile.open(archive, "w:gz") as tar:
                member = tarfile.TarInfo("../extract-evil/escaped.txt")
                member.size = len(payload)
                tar.addfile(member, io.BytesIO(payload))

            with self.assertRaises(SystemExit):
                prepare_python_runtime.safe_extract_tar(archive, root / "extract")

            self.assertFalse((root / "extract-evil" / "escaped.txt").exists())

    def test_rejects_links_and_special_files_before_extraction(self) -> None:
        entry_types = {
            "symlink": tarfile.SYMTYPE,
            "hardlink": tarfile.LNKTYPE,
            "character-device": tarfile.CHRTYPE,
            "block-device": tarfile.BLKTYPE,
            "fifo": tarfile.FIFOTYPE,
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for label, entry_type in entry_types.items():
                with self.subTest(entry_type=label):
                    archive = root / f"{label}.tar.gz"
                    with tarfile.open(archive, "w:gz") as tar:
                        member = tarfile.TarInfo(label)
                        member.type = entry_type
                        if entry_type in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                            member.linkname = "target"
                        tar.addfile(member)

                    destination = root / f"extract-{label}"
                    with self.assertRaises(SystemExit):
                        prepare_python_runtime.safe_extract_tar(archive, destination)
                    self.assertFalse((destination / label).exists())

    def test_relative_symlink_target_validation_is_platform_independent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary).resolve()
            link_path = destination / "python" / "bin" / "python3"
            expected = destination / "python" / "bin" / "python3.11"

            actual = prepare_python_runtime._safe_relative_symlink_target(
                destination,
                link_path,
                "python3.11",
            )
            self.assertEqual(actual, expected)

            for unsafe_target in ("", "../python3.11", "/tmp/python3", "C:\\python3.exe"):
                with self.subTest(target=unsafe_target):
                    with self.assertRaises(SystemExit):
                        prepare_python_runtime._safe_relative_symlink_target(
                            destination,
                            link_path,
                            unsafe_target,
                        )

    def test_only_pinned_mac_runtime_can_enable_relative_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "runtime.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                executable = tarfile.TarInfo("python/bin/python3.11")
                executable.size = 0
                tar.addfile(executable, io.BytesIO())
                link = tarfile.TarInfo("python/bin/python3")
                link.type = tarfile.SYMTYPE
                link.linkname = "python3.11"
                tar.addfile(link)

            with (
                mock.patch.object(prepare_python_runtime, "_extract_tar") as extract,
                mock.patch.object(prepare_python_runtime, "file_sha256") as archive_sha256,
            ):
                archive_sha256.return_value = prepare_python_runtime.TARGETS["mac-x64"]["sha256"]
                prepare_python_runtime.extract_runtime_tar(
                    archive,
                    root / "mac",
                    "mac-x64",
                    prepare_python_runtime.TARGETS["mac-x64"]["sha256"],
                )
                self.assertTrue(extract.call_args.kwargs["allow_relative_symlinks"])

                prepare_python_runtime.extract_runtime_tar(
                    archive,
                    root / "override",
                    "mac-x64",
                    "0" * 64,
                )
                self.assertFalse(extract.call_args.kwargs["allow_relative_symlinks"])

                archive_sha256.return_value = "0" * 64
                prepare_python_runtime.extract_runtime_tar(
                    archive,
                    root / "tampered",
                    "mac-x64",
                    prepare_python_runtime.TARGETS["mac-x64"]["sha256"],
                )
                self.assertFalse(extract.call_args.kwargs["allow_relative_symlinks"])

                prepare_python_runtime.extract_runtime_tar(
                    archive,
                    root / "windows",
                    "win-x64",
                    prepare_python_runtime.TARGETS["win-x64"]["sha256"],
                )
                self.assertFalse(extract.call_args.kwargs["allow_relative_symlinks"])


class RequirementsLockTests(unittest.TestCase):
    def test_repository_lock_contains_the_direct_runtime_pin(self) -> None:
        direct = REPO_ROOT / "requirements.txt"
        lock = REPO_ROOT / "requirements.lock"

        prepare_python_runtime.validate_requirements_lock(direct, lock)

        direct_pins = prepare_python_runtime._exact_pins(direct, lock_file=False)
        locked_pins = prepare_python_runtime._exact_pins(lock, lock_file=True)
        self.assertEqual(direct_pins, {"evernote-backup": "1.13.1"})
        self.assertEqual(locked_pins["evernote-backup"], "1.13.1")
        self.assertEqual(locked_pins["thrift"], "0.21.0")
        self.assertEqual(locked_pins["six"], "1.17.0")

    def test_lock_mismatch_and_unhashed_entries_fail_before_install(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            direct = root / "requirements.txt"
            lock = root / "requirements.lock"
            direct.write_text("example==1\n", encoding="utf-8")
            lock.write_text(
                f"example==2 --hash=sha256:{'0' * 64}\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(SystemExit, "inconsistent"):
                prepare_python_runtime.validate_requirements_lock(direct, lock)

            lock.write_text("example==1\n", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "invalid SHA256 hash"):
                prepare_python_runtime.validate_requirements_lock(direct, lock)

            lock.write_text(
                f"example==1 --hash=sha256:{'0' * 64} --hash=sha256:invalid\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SystemExit, "invalid SHA256 hash"):
                prepare_python_runtime.validate_requirements_lock(direct, lock)

    def test_install_enforces_hashes_wheels_and_no_build_isolation(self) -> None:
        with mock.patch.object(prepare_python_runtime.subprocess, "check_call") as check_call:
            prepare_python_runtime.install_requirements(
                Path("python.exe"),
                Path("requirements.lock"),
            )

        command = check_call.call_args.args[0]
        self.assertIn("--require-hashes", command)
        self.assertIn("--only-binary=:all:", command)
        self.assertIn("--no-binary=thrift", command)
        self.assertIn("--no-build-isolation", command)
        self.assertEqual(command[-2:], ["-r", "requirements.lock"])


class RuntimeOutputSafetyTests(unittest.TestCase):
    def test_rejects_runtime_old_sibling_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            desktop_dir = Path(temporary) / "wandao_electron"
            sibling = desktop_dir / "runtime-old" / "python-runtime"
            sibling.mkdir(parents=True)
            marker = sibling / "keep.txt"
            marker.write_text("keep", encoding="utf-8")

            with mock.patch.object(prepare_python_runtime, "DESKTOP_DIR", desktop_dir):
                with self.assertRaises(SystemExit):
                    prepare_python_runtime.remove_previous_output(sibling)

            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_rejects_runtime_root_but_removes_a_descendant(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            desktop_dir = Path(temporary) / "wandao_electron"
            runtime_root = desktop_dir / "runtime"
            output = runtime_root / "python-runtime"
            output.mkdir(parents=True)

            with mock.patch.object(prepare_python_runtime, "DESKTOP_DIR", desktop_dir):
                with self.assertRaises(SystemExit):
                    prepare_python_runtime.remove_previous_output(runtime_root)
                prepare_python_runtime.remove_previous_output(output)

            self.assertTrue(runtime_root.exists())
            self.assertFalse(output.exists())

    def test_interrupted_replace_backup_is_restored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            desktop_dir = Path(temporary) / "wandao_electron"
            output = desktop_dir / "runtime" / "python-runtime"
            backup = output.with_name(".python-runtime.previous")
            backup.mkdir(parents=True)
            (backup / "known-good.txt").write_text("good", encoding="utf-8")

            with mock.patch.object(prepare_python_runtime, "DESKTOP_DIR", desktop_dir):
                prepare_python_runtime.recover_interrupted_runtime_replace(output)

            self.assertEqual((output / "known-good.txt").read_text(encoding="utf-8"), "good")
            self.assertFalse(backup.exists())

    def test_interrupted_replace_rejects_a_non_directory_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            desktop_dir = Path(temporary) / "wandao_electron"
            output = desktop_dir / "runtime" / "python-runtime"
            backup = output.with_name(".python-runtime.previous")
            backup.parent.mkdir(parents=True)
            backup.write_text("not a runtime", encoding="utf-8")

            with mock.patch.object(prepare_python_runtime, "DESKTOP_DIR", desktop_dir):
                with self.assertRaisesRegex(SystemExit, "无效的运行时备份"):
                    prepare_python_runtime.recover_interrupted_runtime_replace(output)

            self.assertFalse(output.exists())
            self.assertTrue(backup.is_file())

    def test_replace_failure_restores_the_previous_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            desktop_dir = Path(temporary) / "wandao_electron"
            output = desktop_dir / "runtime" / "python-runtime"
            output.mkdir(parents=True)
            (output / "known-good.txt").write_text("good", encoding="utf-8")
            staged = output.parent / ".python-runtime-staging" / "prepared"
            staged.mkdir(parents=True)
            (staged / "candidate.txt").write_text("candidate", encoding="utf-8")
            backup = output.with_name(".python-runtime.previous")
            original_rename = Path.rename

            def fail_candidate_rename(path: Path, target: Path) -> Path:
                if path == staged:
                    raise OSError("simulated candidate rename failure")
                return original_rename(path, target)

            with (
                mock.patch.object(prepare_python_runtime, "DESKTOP_DIR", desktop_dir),
                mock.patch.object(Path, "rename", fail_candidate_rename),
            ):
                with self.assertRaisesRegex(OSError, "candidate rename failure"):
                    prepare_python_runtime.replace_runtime_output(staged, output)

            self.assertEqual((output / "known-good.txt").read_text(encoding="utf-8"), "good")
            self.assertTrue((staged / "candidate.txt").is_file())
            self.assertFalse(backup.exists())

    def test_preparation_failure_preserves_the_previous_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            desktop_dir = root / "wandao_electron"
            output = desktop_dir / "runtime" / "python-runtime"
            output.mkdir(parents=True)
            marker = output / "known-good.txt"
            marker.write_text("good", encoding="utf-8")
            source_runtime = root / "source-runtime"
            source_runtime.mkdir()
            (source_runtime / "python.exe").touch()
            project_dir = root / "project"
            project_dir.mkdir()
            (project_dir / "requirements.txt").write_text("example==1\n", encoding="utf-8")
            (project_dir / "requirements.lock").write_text(
                f"example==1 --hash=sha256:{'0' * 64}\n",
                encoding="utf-8",
            )

            with (
                mock.patch.object(prepare_python_runtime, "DESKTOP_DIR", desktop_dir),
                mock.patch.object(prepare_python_runtime, "PROJECT_DIR", project_dir),
                mock.patch.object(prepare_python_runtime, "download"),
                mock.patch.object(prepare_python_runtime, "extract_runtime_tar"),
                mock.patch.object(
                    prepare_python_runtime,
                    "find_runtime_root",
                    return_value=source_runtime,
                ),
                mock.patch.object(
                    prepare_python_runtime,
                    "install_requirements",
                    side_effect=RuntimeError("dependency install failed"),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "dependency install failed"):
                    prepare_python_runtime.prepare_runtime(
                        "win-x64",
                        output,
                        desktop_dir / ".runtime-cache",
                    )

            self.assertEqual(marker.read_text(encoding="utf-8"), "good")

    def test_runtime_fingerprint_is_collected_after_build_tools_are_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            desktop_dir = root / "wandao_electron"
            output = desktop_dir / "runtime" / "python-runtime"
            source_runtime = root / "source-runtime"
            source_runtime.mkdir()
            (source_runtime / "python.exe").touch()
            project_dir = root / "project"
            project_dir.mkdir()
            (project_dir / "requirements.txt").write_text(
                "evernote-backup==1.13.1\n",
                encoding="utf-8",
            )
            lock_file = project_dir / "requirements.lock"
            lock_file.write_text(
                f"evernote-backup==1.13.1 --hash=sha256:{'0' * 64}\n",
                encoding="utf-8",
            )
            fingerprint = {
                "implementation": "CPython",
                "version": "3.11.15",
                "machine": "AMD64",
                "bits": 64,
                "platform": "win32",
                "architecture": "x86_64",
                "installedPackages": ["evernote-backup==1.13.1"],
            }

            def remove_build_tools(staged_runtime: Path) -> None:
                (staged_runtime / ".build-tools-removed").touch()

            def verify_after_removal(python: Path, target: str) -> dict[str, object]:
                self.assertEqual(target, "win-x64")
                self.assertTrue((python.parent / ".build-tools-removed").is_file())
                return fingerprint

            with (
                mock.patch.object(prepare_python_runtime, "DESKTOP_DIR", desktop_dir),
                mock.patch.object(prepare_python_runtime, "PROJECT_DIR", project_dir),
                mock.patch.object(prepare_python_runtime, "download"),
                mock.patch.object(prepare_python_runtime, "extract_runtime_tar"),
                mock.patch.object(
                    prepare_python_runtime,
                    "find_runtime_root",
                    return_value=source_runtime,
                ),
                mock.patch.object(prepare_python_runtime, "install_requirements"),
                mock.patch.object(prepare_python_runtime, "verify_dependencies"),
                mock.patch.object(prepare_python_runtime, "cleanup_runtime"),
                mock.patch.object(
                    prepare_python_runtime,
                    "remove_build_only_runtime_files",
                    side_effect=remove_build_tools,
                ),
                mock.patch.object(prepare_python_runtime, "verify_runtime_is_release_only"),
                mock.patch.object(
                    prepare_python_runtime,
                    "verify_runtime",
                    side_effect=verify_after_removal,
                ),
                mock.patch.object(prepare_python_runtime, "cleanup_packaged_source_caches"),
            ):
                prepare_python_runtime.prepare_runtime(
                    "win-x64",
                    output,
                    desktop_dir / ".runtime-cache",
                )

            metadata = json.loads((output / "WANDAO_RUNTIME.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["installedPackages"], fingerprint["installedPackages"])
            self.assertEqual(
                metadata["requirementsLockSha256"],
                prepare_python_runtime.file_sha256(lock_file),
            )


class RuntimeVerificationTests(unittest.TestCase):
    def test_runtime_fingerprint_uses_isolated_no_bytecode_execution(self) -> None:
        payload = {
            "implementation": "CPython",
            "version": "3.11.15",
            "machine": "AMD64",
            "bits": 64,
            "platform": "win32",
            "installedPackages": ["evernote-backup==1.13.1"],
        }
        with mock.patch.object(
            prepare_python_runtime.subprocess,
            "check_output",
            return_value=json.dumps(payload),
        ) as check_output:
            result = prepare_python_runtime.verify_runtime(Path("python.exe"), "win-x64")

        command = check_output.call_args.args[0]
        self.assertEqual(command[1:3], ["-I", "-B"])
        self.assertEqual(result["architecture"], "x86_64")

    def test_dependency_consistency_uses_bundled_pip_in_isolated_mode(self) -> None:
        with mock.patch.object(prepare_python_runtime.subprocess, "check_call") as check_call:
            prepare_python_runtime.verify_dependencies(Path("python.exe"))

        self.assertEqual(
            check_call.call_args.args[0],
            ["python.exe", "-I", "-B", "-m", "pip", "check"],
        )

    def test_runtime_override_requires_https_safe_name_and_valid_digest(self) -> None:
        with mock.patch.dict(
            prepare_python_runtime.os.environ,
            {"WANDAO_PYTHON_RUNTIME_URL": "http://example.test/runtime.tar.gz"},
            clear=False,
        ):
            with self.assertRaises(SystemExit):
                prepare_python_runtime.pick_asset("win-x64")

        with mock.patch.dict(
            prepare_python_runtime.os.environ,
            {
                "WANDAO_PYTHON_RUNTIME_URL": "https://example.test/runtime.tar.gz",
                "WANDAO_PYTHON_RUNTIME_SHA256": "not-a-digest",
            },
            clear=False,
        ):
            with self.assertRaises(SystemExit):
                prepare_python_runtime.pick_asset("win-x64")


class PackagedSourceCleanupTests(unittest.TestCase):
    def test_missing_and_empty_source_roots_are_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_dir = Path(temporary)
            prepare_python_runtime.cleanup_packaged_source_caches(project_dir)
            (project_dir / "plugins").mkdir()
            prepare_python_runtime.cleanup_packaged_source_caches(project_dir)
            self.assertTrue((project_dir / "plugins").is_dir())

    def test_removes_only_generated_python_caches_from_packaged_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_dir = Path(temporary)
            for source_name in ("plugins", "providers", "wandao_core"):
                source_root = project_dir / source_name
                cache_dir = source_root / "nested" / "__pycache__"
                cache_dir.mkdir(parents=True)
                (cache_dir / "module.cpython-311.pyc").write_bytes(b"cache")
                (source_root / "orphan.pyc").write_bytes(b"cache")
                (source_root / "module.py").write_text("value = 1\n", encoding="utf-8")

            prepare_python_runtime.cleanup_packaged_source_caches(project_dir)

            for source_name in ("plugins", "providers", "wandao_core"):
                source_root = project_dir / source_name
                self.assertFalse(any(source_root.rglob("__pycache__")))
                self.assertFalse(any(source_root.rglob("*.pyc")))
                self.assertTrue((source_root / "module.py").is_file())

    def test_clean_source_caches_only_does_not_prepare_or_modify_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_dir = Path(temporary)
            runtime = project_dir / "runtime" / "python-runtime"
            runtime.mkdir(parents=True)
            marker = runtime / "keep.txt"
            marker.write_text("keep", encoding="utf-8")
            cache = project_dir / "plugins" / "sample" / "__pycache__"
            cache.mkdir(parents=True)
            (cache / "module.pyc").write_bytes(b"cache")

            with (
                mock.patch.object(prepare_python_runtime, "PROJECT_DIR", project_dir),
                mock.patch.object(prepare_python_runtime, "prepare_runtime") as prepare_runtime,
                mock.patch.object(
                    sys,
                    "argv",
                    [str(PREPARE_SCRIPT), "--clean-source-caches-only"],
                ),
            ):
                self.assertEqual(prepare_python_runtime.main(), 0)

            prepare_runtime.assert_not_called()
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
            self.assertFalse(cache.exists())


class ReleaseBuildGuardContractTests(unittest.TestCase):
    def test_runtime_pins_match_every_release_validation_layer(self) -> None:
        build_source = BUILD_SCRIPT.read_text(encoding="utf-8")
        smoke_source = PACKAGE_SMOKE_SCRIPT.read_text(encoding="utf-8")

        for target, spec in prepare_python_runtime.TARGETS.items():
            with self.subTest(target=target):
                for value in (
                    target,
                    str(spec["asset"]),
                    str(spec["sha256"]),
                    str(spec["python_version"]),
                    str(spec["architecture"]),
                ):
                    self.assertIn(value, build_source)
                    self.assertIn(value, smoke_source)

    def test_release_build_validates_runtime_target_and_source_caches(self) -> None:
        source = BUILD_SCRIPT.read_text(encoding="utf-8")

        self.assertIn('std::env::var("PROFILE")', source)
        self.assertIn('profile == "release"', source)
        self.assertIn('std::env::var("TARGET")', source)
        self.assertIn("&requirements_lock,", source)
        self.assertIn('runtime.join("WANDAO_RUNTIME.json")', source)
        self.assertIn('target: "win-x64"', source)
        self.assertIn('target: "mac-x64"', source)
        self.assertIn('target: "mac-arm64"', source)
        self.assertIn('interpreter: "python.exe"', source)
        self.assertIn('interpreter: "bin/python3"', source)
        self.assertIn('("target", spec.target)', source)
        self.assertIn('("archiveSha256", spec.archive_sha256)', source)
        self.assertIn('runtime_metadata_string(&metadata, "requirementsSha256")', source)
        self.assertIn('runtime_metadata_string(&metadata, "requirementsLockSha256")', source)
        self.assertIn("validate_runtime_tree(runtime)", source)
        self.assertIn("bundled runtime root must be a real directory", source)
        self.assertIn("bundled runtime contains generated Python cache", source)
        self.assertIn("bundled runtime contains build-only package tooling", source)
        self.assertIn('"../../plugins"', source)
        self.assertIn('"../../providers"', source)
        self.assertIn('"../../wandao_core"', source)
        self.assertIn('name == "__pycache__"', source)
        self.assertIn('lowercase_name.ends_with(".pyc")', source)

        runtime_tree = source[
            source.index("fn validate_runtime_tree(") : source.index("fn validate_runtime(")
        ]
        self.assertLess(
            runtime_tree.index("if build_tool_path"),
            runtime_tree.index("if file_type.is_symlink()"),
        )

        release_branch = source.index('if profile == "release"')
        placeholder_branch = source.index("std::fs::create_dir_all(&python_runtime)")
        tauri_build = source.index("tauri_build::build()")
        self.assertLess(release_branch, placeholder_branch)
        self.assertLess(placeholder_branch, tauri_build)

    def test_tauri_packages_lock_and_cleans_source_caches_before_dev(self) -> None:
        config = json.loads(
            (REPO_ROOT / "wandao_electron" / "src-tauri" / "tauri.conf.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(
            config["build"]["beforeDevCommand"],
            "python scripts/prepare_python_runtime.py --clean-source-caches-only",
        )
        self.assertEqual(
            config["bundle"]["resources"]["../../requirements.lock"],
            "python/requirements.lock",
        )

    def test_source_cache_cleanup_runs_after_runtime_validation(self) -> None:
        source = PREPARE_SCRIPT.read_text(encoding="utf-8")
        prepare_runtime = source[source.index("def prepare_runtime(") :]

        runtime_validation = prepare_runtime.index("verify_runtime_is_release_only(staged_runtime)")
        runtime_fingerprint = prepare_runtime.index("fingerprint = verify_runtime(py, target)")
        source_cleanup = prepare_runtime.index("cleanup_packaged_source_caches()")
        metadata_write = prepare_runtime.index("write_build_info(")
        runtime_replace = prepare_runtime.index("replace_runtime_output(staged_runtime, output_dir)")
        self.assertLess(runtime_validation, runtime_fingerprint)
        self.assertLess(runtime_fingerprint, source_cleanup)
        self.assertLess(source_cleanup, metadata_write)
        self.assertLess(metadata_write, runtime_replace)


class TauriReleaseDocumentationContractTests(unittest.TestCase):
    def test_repository_pins_the_rust_toolchain_used_by_tauri_subprocesses(self) -> None:
        toolchain = RUST_TOOLCHAIN_FILE.read_text(encoding="utf-8")

        self.assertIn('channel = "1.88.0"', toolchain)
        self.assertIn('profile = "minimal"', toolchain)
        self.assertIn('components = ["clippy", "rustfmt"]', toolchain)

    def test_desktop_workflow_pins_rust_and_installs_release_targets(self) -> None:
        workflow = BUILD_WORKFLOW.read_text(encoding="utf-8")

        self.assertNotIn("dtolnay/rust-toolchain@stable", workflow)
        self.assertEqual(workflow.count("dtolnay/rust-toolchain@1.88.0"), 3)
        self.assertGreaterEqual(workflow.count("x86_64-pc-windows-msvc"), 2)
        self.assertGreaterEqual(workflow.count("aarch64-apple-darwin"), 3)

    def test_quality_matrix_installs_locked_node_test_dependencies_first(self) -> None:
        workflow = BUILD_WORKFLOW.read_text(encoding="utf-8")
        quality_job = workflow.split("  quality:\n", 1)[1].split("\n  rust-quality:", 1)[0]

        self.assertIn("cache: npm", quality_job)
        self.assertIn(
            "cache-dependency-path: wandao_electron/package-lock.json",
            quality_job,
        )
        install_step = quality_job.split(
            "- name: Install locked Node test dependencies",
            1,
        )[1].split("- name: Run Python and frontend quality checks", 1)[0]
        self.assertIn("working-directory: wandao_electron", install_step)
        self.assertIn("run: npm ci --ignore-scripts", install_step)
        self.assertLess(
            quality_job.index("run: npm ci --ignore-scripts"),
            quality_job.index("run: python scripts/quality_check.py"),
        )

    def test_rust_quality_commands_cover_all_targets_with_locked_dependencies(self) -> None:
        quality_source = QUALITY_SCRIPT.read_text(encoding="utf-8")
        rust_checks = quality_source[
            quality_source.index("def run_rust_checks()") : quality_source.index(
                "def run_diff_check()"
            )
        ]
        workflow = BUILD_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn('rustup = shutil.which("rustup")', rust_checks)
        self.assertIn('cargo = [rustup, "run", RUST_TOOLCHAIN, "cargo"]', rust_checks)
        self.assertIn('[*cargo, "fmt", "--all", "--", "--check"]', rust_checks)
        self.assertIn('[*cargo, "check", "--all-targets", "--locked"]', rust_checks)
        self.assertIn('[*cargo, "test", "--all-targets", "--locked"]', rust_checks)
        self.assertIn(
            '"clippy", "--all-targets", "--locked", "--", "-D", "warnings"',
            rust_checks,
        )
        self.assertIn("cargo check --all-targets --locked", workflow)
        self.assertIn("cargo test --all-targets --locked", workflow)
        self.assertIn("cargo clippy --all-targets --locked -- -D warnings", workflow)

        fmt = rust_checks.index('"fmt"')
        check = rust_checks.index('"check", "--all-targets"')
        test = rust_checks.index('"test", "--all-targets"')
        clippy = rust_checks.index('"clippy", "--all-targets"')
        self.assertLess(fmt, check)
        self.assertLess(check, test)
        self.assertLess(test, clippy)

    def test_current_release_docs_describe_tauri_artifacts_and_guards(self) -> None:
        desktop_readme = DESKTOP_README.read_text(encoding="utf-8")
        release_guide = RELEASE_GUIDE.read_text(encoding="utf-8")
        pr_template = PR_TEMPLATE.read_text(encoding="utf-8")

        self.assertIn("Tauri 2", desktop_readme)
        self.assertIn("Rust 1.88.0", desktop_readme)
        self.assertIn("src-tauri/", desktop_readme)
        self.assertNotIn("Electron", desktop_readme)
        self.assertNotIn("main.js", desktop_readme)
        self.assertNotIn("preload.js", desktop_readme)

        self.assertIn("Tauri 2", release_guide)
        self.assertIn("Rust 1.88.0", release_guide)
        self.assertIn("WANDAO_RUNTIME.json", release_guide)
        self.assertIn("src-tauri/target/release/bundle/nsis", release_guide)
        self.assertIn("aarch64-apple-darwin/release/bundle/macos", release_guide)
        self.assertNotIn("electron-builder", release_guide)
        self.assertNotIn("dist/win-unpacked", release_guide)

        self.assertIn("Rust 1.88.0", pr_template)
        self.assertIn("Tauri 2 / Rust", pr_template)
        self.assertNotIn("Electron JS", pr_template)


if __name__ == "__main__":
    unittest.main()
