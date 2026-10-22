import tempfile
import unittest
from pathlib import Path

from scripts.build_updater_manifest import build_manifest


class UpdaterManifestTests(unittest.TestCase):
    def test_manifest_contains_both_macos_architectures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name, signature in (
                ("Wandao-1.5.3-macOS-arm64.app.tar.gz", "arm-signature"),
                ("Wandao-1.5.3-macOS-x64.app.tar.gz", "x64-signature"),
                ("Wandao-1.5.3-win-x64-setup.exe", "windows-signature"),
            ):
                artifact = directory / name
                artifact.write_bytes(b"artifact")
                Path(f"{artifact}.sig").write_text(signature, encoding="utf-8")

            manifest = build_manifest(directory, "v1.5.3")

        self.assertEqual(manifest["version"], "1.5.3")
        self.assertIn("darwin-aarch64", manifest["platforms"])
        self.assertIn("darwin-x86_64", manifest["platforms"])
        self.assertIn("macOS-arm64", manifest["platforms"]["darwin-aarch64"]["url"])
        self.assertIn("macOS-x64", manifest["platforms"]["darwin-x86_64"]["url"])
        self.assertEqual(manifest["platforms"]["darwin-x86_64"]["signature"], "x64-signature")


if __name__ == "__main__":
    unittest.main()
