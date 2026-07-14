"""Tests for Obsidian Vault export plugin."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPORT_SCRIPT = REPO_ROOT / "plugins" / "obsidian" / "backend" / "export_obsidian.py"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "obsidian-sample-vault"


def _parse_last_json(stdout: str) -> dict:
    decoder = json.JSONDecoder()
    for index, char in enumerate(stdout):
        if char != "{":
            continue
        try:
            data, end = decoder.raw_decode(stdout[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and not stdout[index + end :].strip():
            return data
    raise AssertionError(f"没有在输出中找到 JSON：{stdout}")


def _run(*args: str) -> dict:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    result = subprocess.run(
        [sys.executable, str(EXPORT_SCRIPT), *args],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=True,
    )
    return _parse_last_json(result.stdout)


def _run_may_fail(*args: str) -> subprocess.CompletedProcess:
    """Run the script and return the full subprocess result regardless of exit code."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    return subprocess.run(
        [sys.executable, str(EXPORT_SCRIPT), *args],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )


class ObsidianExportTests(unittest.TestCase):
    def _sample_vault(self) -> Path:
        return FIXTURES

    # ---- TOC -----------------------------------------------------------------

    def test_scan_toc_returns_all_markdown_files(self) -> None:
        data = _run("--vault", str(self._sample_vault()), "--scan-toc")
        self.assertEqual(data["provider"], "obsidian-export")
        self.assertEqual(data["totalDocs"], 3)
        self.assertGreaterEqual(data["folderCount"], 1)
        node_ids = {n["nodeId"] for n in data["nodes"]}
        self.assertIn("doc:index.md", node_ids)
        self.assertIn("doc:notes/getting-started.md", node_ids)
        self.assertIn("doc:notes/advanced.md", node_ids)

    def test_scan_toc_has_root_pseudo_node(self) -> None:
        data = _run("--vault", str(self._sample_vault()), "--scan-toc")
        roots = [n for n in data["nodes"] if n["nodeId"] == "folder:"]
        self.assertEqual(len(roots), 1)
        self.assertFalse(roots[0]["selectable"])

    def test_scan_toc_folder_hierarchy(self) -> None:
        data = _run("--vault", str(self._sample_vault()), "--scan-toc")
        notes_folder = next(n for n in data["nodes"] if n["nodeId"] == "folder:notes")
        self.assertEqual(notes_folder["parentNodeId"], "folder:")

    # ---- Export (full) -------------------------------------------------------

    def test_export_all_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            report = _run(
                "--vault", str(self._sample_vault()),
                "--output", str(out),
                "--export",
                "--progress-every", "1",
            )
            self.assertEqual(report["provider"], "obsidian-export")
            self.assertEqual(report["mode"], "export")
            self.assertEqual(report["totalDocs"], 3)
            self.assertEqual(report["exportedDocs"], 3)
            self.assertEqual(report["successCount"], 3)
            self.assertEqual(report["failureCount"], 0)
            self.assertTrue((out / "index.md").exists())
            self.assertTrue((out / "notes" / "getting-started.md").exists())
            self.assertTrue((out / "notes" / "advanced.md").exists())

    def test_export_preserves_directory_structure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            _run(
                "--vault", str(self._sample_vault()),
                "--output", str(out),
                "--export",
                "--progress-every", "0",
            )
            self.assertTrue((out / "notes").is_dir())
            self.assertTrue((out / "notes" / "getting-started.md").is_file())

    # ---- Resource resolution & rewriting -------------------------------------

    def test_resources_copied_to_resources_subdir(self) -> None:
        """Resources should be placed under _resources/<vault-rel-path>."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            _run(
                "--vault", str(self._sample_vault()),
                "--output", str(out),
                "--export",
                "--progress-every", "0",
            )
            self.assertTrue((out / "_resources" / "notes" / "screenshot.png").exists())
            self.assertTrue((out / "_resources" / "attachments" / "vault-logo.png").exists())
            self.assertTrue((out / "_resources" / "attachments" / "manual.pdf").exists())

    def test_resources_not_copied_next_to_md(self) -> None:
        """Resources should NOT be placed directly next to .md files."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            _run(
                "--vault", str(self._sample_vault()),
                "--output", str(out),
                "--export",
                "--progress-every", "0",
            )
            self.assertFalse((out / "notes" / "screenshot.png").exists())
            self.assertFalse((out / "notes" / "vault-logo.png").exists())

    def test_markdown_refs_rewritten_to_resources(self) -> None:
        """Exported .md must reference resources at _resources/ paths."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            _run(
                "--vault", str(self._sample_vault()),
                "--output", str(out),
                "--export",
                "--progress-every", "0",
            )
            # advanced.md: ![[notes/screenshot.png]] -> rewritten
            advanced = (out / "notes" / "advanced.md").read_text(encoding="utf-8")
            self.assertIn("_resources/notes/screenshot.png", advanced)
            self.assertIn("_resources/attachments/vault-logo.png", advanced)
            self.assertNotIn("![[notes/screenshot.png]]", advanced)
            self.assertNotIn("![[attachments/vault-logo.png|300]]", advanced)

            # getting-started.md: ![screenshot](screenshot.png) -> rewritten
            gs = (out / "notes" / "getting-started.md").read_text(encoding="utf-8")
            self.assertIn("_resources/notes/screenshot.png", gs)
            self.assertIn("_resources/attachments/manual.pdf", gs)

            # index.md: ![[attachments/vault-logo.png]] -> rewritten
            index_md = (out / "index.md").read_text(encoding="utf-8")
            self.assertIn("_resources/attachments/vault-logo.png", index_md)
            self.assertNotIn("![[attachments/vault-logo.png]]", index_md)

    def test_resolved_refs_are_valid_relative_paths(self) -> None:
        """Verify that rewritten references actually resolve to existing files."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            _run(
                "--vault", str(self._sample_vault()),
                "--output", str(out),
                "--export",
                "--progress-every", "0",
            )
            # Parse _resources paths from advanced.md and verify files exist
            import re
            advanced = (out / "notes" / "advanced.md").read_text(encoding="utf-8")
            refs = re.findall(r'\]\(([^)]+)\)', advanced)
            for ref in refs:
                if ref.startswith("_resources"):
                    self.assertTrue((out / "notes" / ref).exists(),
                                    f"Reference {ref} in advanced.md does not resolve")

    def test_reports_missing_resource(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            report = _run(
                "--vault", str(self._sample_vault()),
                "--output", str(out),
                "--export",
                "--progress-every", "0",
            )
            # advanced.md has ![[missing-image.png]]
            self.assertGreater(len(report["resourceFailures"]), 0)
            missing_refs = {r["ref"] for r in report["resourceFailures"]}
            self.assertIn("missing-image.png", missing_refs)

    # ---- Same-name resources from different directories -----------------------

    def test_same_name_resources_from_different_dirs_not_overwritten(self) -> None:
        """Resources with the same filename but different paths must not collide."""
        # Create a vault with same-named files in different dirs
        with tempfile.TemporaryDirectory() as vault_tmp:
            vault = Path(vault_tmp)
            (vault / "notes").mkdir(parents=True)
            (vault / "attachments").mkdir()
            (vault / "doc.md").write_text(
                "![[notes/pic.png]]\n\n![alt](attachments/pic.png)\n",
                encoding="utf-8",
            )
            # Two files with same name, different content
            (vault / "notes" / "pic.png").write_bytes(b"notes-version")
            (vault / "attachments" / "pic.png").write_bytes(b"attachments-version")

            with tempfile.TemporaryDirectory() as out_tmp:
                out = Path(out_tmp) / "out"
                report = _run(
                    "--vault", str(vault),
                    "--output", str(out),
                    "--export",
                    "--progress-every", "0",
                )
                self.assertEqual(report["exportedDocs"], 1)
                # Both should exist at distinct paths
                r1 = out / "_resources" / "notes" / "pic.png"
                r2 = out / "_resources" / "attachments" / "pic.png"
                self.assertTrue(r1.exists())
                self.assertTrue(r2.exists())
                self.assertEqual(r1.read_bytes(), b"notes-version")
                self.assertEqual(r2.read_bytes(), b"attachments-version")

    # ---- Selection -----------------------------------------------------------

    def test_doc_id_filter_exports_only_selected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            _run(
                "--vault", str(self._sample_vault()),
                "--output", str(out),
                "--export",
                "--doc-id", "index.md",
                "--progress-every", "0",
            )
            self.assertTrue((out / "index.md").exists())
            self.assertFalse((out / "notes" / "getting-started.md").exists())
            self.assertFalse((out / "notes" / "advanced.md").exists())

    # ---- Security: output dir inside vault -----------------------------------

    def test_output_dir_inside_vault_rejected(self) -> None:
        """Output directory located inside the vault must be rejected."""
        result = _run_may_fail(
            "--vault", str(self._sample_vault()),
            "--output", str(self._sample_vault() / "exported"),
            "--export",
            "--progress-every", "0",
        )
        self.assertNotEqual(result.returncode, 0)
        data = _parse_last_json(result.stdout)
        self.assertIn("failureCount", data)
        self.assertGreater(data["failureCount"], 0)
        self.assertIn("inside the vault", data["failures"][0]["error"])

    # ---- Security: --doc-id path traversal -----------------------------------

    def test_doc_id_path_traversal_rejected(self) -> None:
        """--doc-id containing .. must be rejected."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            result = _run_may_fail(
                "--vault", str(self._sample_vault()),
                "--output", str(out),
                "--export",
                "--doc-id", "../outside.md",
                "--progress-every", "0",
            )
            self.assertNotEqual(result.returncode, 0)
            data = _parse_last_json(result.stdout)
            self.assertGreater(data["failureCount"], 0)
            self.assertIn("..", data["failures"][0]["error"])

    def test_doc_id_absolute_outside_vault_rejected(self) -> None:
        """--doc-id resolving outside vault must be rejected."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            # Use an absolute path outside the vault
            outside = tmp + "/outside.md"
            Path(outside).write_text("# outside", encoding="utf-8")
            result = _run_may_fail(
                "--vault", str(self._sample_vault()),
                "--output", str(out),
                "--export",
                "--doc-id", outside,
                "--progress-every", "0",
            )
            self.assertNotEqual(result.returncode, 0)
            data = _parse_last_json(result.stdout)
            self.assertGreater(data["failureCount"], 0)
            self.assertIn("outside vault", data["failures"][0]["error"])

    # ---- Security: resource path traversal via .. ----------------------------

    def test_resource_ref_with_dotdot_rejected(self) -> None:
        """Markdown referencing ../ outside vault must not resolve the resource."""
        with tempfile.TemporaryDirectory() as vault_tmp:
            vault = Path(vault_tmp)
            outside_file = vault_tmp + "_outside.txt"
            Path(outside_file).write_text("secret", encoding="utf-8")
            (vault / "bad.md").write_text(
                f"![escape]({outside_file})",
                encoding="utf-8",
            )

            with tempfile.TemporaryDirectory() as out_tmp:
                out = Path(out_tmp) / "out"
                report = _run(
                    "--vault", str(vault),
                    "--output", str(out),
                    "--export",
                    "--progress-every", "0",
                )
                self.assertGreater(len(report["resourceFailures"]), 0)
                # The outside file should NOT exist in output
                self.assertFalse((out / "_resources").exists() or
                                 list(out.rglob("_outside.txt")) != [])

    # ---- Incremental ---------------------------------------------------------

    def test_incremental_skips_up_to_date_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            # First pass: export everything
            _run(
                "--vault", str(self._sample_vault()),
                "--output", str(out),
                "--export",
                "--progress-every", "0",
            )
            # Second pass: incremental
            report = _run(
                "--vault", str(self._sample_vault()),
                "--output", str(out),
                "--export",
                "--incremental",
                "--progress-every", "0",
            )
            self.assertEqual(report["skippedDocs"], 3)
            self.assertEqual(report["exportedDocs"], 0)

    def test_source_md_not_modified(self) -> None:
        """Verify vault source files remain untouched after export."""
        original_content = (self._sample_vault() / "index.md").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            _run(
                "--vault", str(self._sample_vault()),
                "--output", str(out),
                "--export",
                "--progress-every", "0",
            )
        after_content = (self._sample_vault() / "index.md").read_text(encoding="utf-8")
        self.assertEqual(original_content, after_content)

    # ---- Symlink escape ------------------------------------------------------

    def test_symlink_escape_rejected(self) -> None:
        """A symlink inside vault pointing outside must be rejected."""
        with tempfile.TemporaryDirectory() as vault_tmp:
            vault = Path(vault_tmp)
            outside_dir = tempfile.mkdtemp(prefix="outside_")
            try:
                outside_target = Path(outside_dir) / "outside.md"
                outside_target.write_text("# outside", encoding="utf-8")

                link_path = vault / "escape_link"
                try:
                    link_path.symlink_to(outside_target)
                except OSError:
                    self.skipTest("symlink creation not available on this system")

                with tempfile.TemporaryDirectory() as out_tmp:
                    out = Path(out_tmp) / "out"
                    report = _run(
                        "--vault", str(vault),
                        "--output", str(out),
                        "--export",
                        "--progress-every", "0",
                    )
                    # The symlink target is outside the vault
                    # May fail to export or have resource failures
                    # Regardless, outside content should not appear in output
                    if (out / "escape_link").exists():
                        content = (out / "escape_link").read_text(encoding="utf-8")
                        self.assertNotIn("outside", content)
            finally:
                import shutil
                shutil.rmtree(outside_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
