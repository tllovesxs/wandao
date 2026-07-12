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


class ObsidianExportTests(unittest.TestCase):
    def _sample_vault(self) -> Path:
        return FIXTURES

    # ---- TOC -----------------------------------------------------------------

    def test_scan_toc_returns_all_markdown_files(self) -> None:
        data = _run("--vault", str(self._sample_vault()), "--scan-toc")
        self.assertEqual(data["provider"], "obsidian-export")
        self.assertEqual(data["totalDocs"], 3)
        self.assertGreaterEqual(data["folderCount"], 1)  # "notes" + "attachments"
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

    # ---- Resource resolution -------------------------------------------------

    def test_copies_markdown_relative_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            _run(
                "--vault", str(self._sample_vault()),
                "--output", str(out),
                "--export",
                "--progress-every", "0",
            )
            # getting-started.md references screenshot.png (same dir)
            self.assertTrue((out / "notes" / "screenshot.png").exists())

    def test_copies_wiki_link_embed_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            _run(
                "--vault", str(self._sample_vault()),
                "--output", str(out),
                "--export",
                "--progress-every", "0",
            )
            # advanced.md has ![[notes/screenshot.png]]
            self.assertTrue((out / "notes" / "screenshot.png").exists())

    def test_copies_attachment_from_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            _run(
                "--vault", str(self._sample_vault()),
                "--output", str(out),
                "--export",
                "--progress-every", "0",
            )
            # advanced.md has ![[attachments/vault-logo.png|300]]
            self.assertTrue((out / "notes" / "vault-logo.png").exists())

    def test_copies_attachment_from_subdir_link(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            _run(
                "--vault", str(self._sample_vault()),
                "--output", str(out),
                "--export",
                "--progress-every", "0",
            )
            # getting-started.md references attachments/manual.pdf via rel path
            self.assertTrue((out / "notes" / "manual.pdf").exists())

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


if __name__ == "__main__":
    unittest.main()
