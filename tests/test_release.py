import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "packager", ROOT / "scripts/package-release.py"
)
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


class ReleasePackaging(unittest.TestCase):
    def test_runtime_and_unknown_root_files_are_excluded(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name in (
                "README.md",
                "secret.txt",
                "data/config.json",
                ".venv/private.py",
                "app/agent.py",
                "tests/example.py",
                "docs/user.json.pending-1-a.json",
            ):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("test")
            paths = {
                p.relative_to(root).as_posix() for p in packager.source_files(root)
            }
            self.assertEqual(paths, {"README.md", "app/agent.py", "tests/example.py"})

    def test_archive_is_reproducible_and_contains_exact_source(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "README.md").write_text("public content")
            output, digest = packager.build(root)
            first = output.read_bytes()
            output, second_digest = packager.build(root)
            self.assertEqual(first, output.read_bytes())
            self.assertEqual(digest, second_digest)
            manifest = json.loads((root / "dist/release-manifest.json").read_text())
            self.assertEqual(set(manifest["files"]), {"README.md"})
            self.assertEqual(manifest["sha256"], digest)

    def test_tampered_archive_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "README.md").write_text("source")
            path = root / "tampered.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("README.md", "changed")
            with self.assertRaisesRegex(ValueError, "content mismatch"):
                packager.verify_archive(path, root)


if __name__ == "__main__":
    unittest.main()
