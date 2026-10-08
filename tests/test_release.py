import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_audit", ROOT / "scripts/check_release.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class ReleaseTests(unittest.TestCase):
    def test_private_artifacts_never_selected(self):
        for name in ("models/real.npz", "models/real.json", "state/status.json", "data/capture.csv",
                     ".env", "vendor/sdk.py", "docs/private-notes.md"):
            self.assertNotIn(name, audit.PUBLIC_FILES)
        self.assertEqual([x for x in audit.PUBLIC_FILES if x.startswith("models/")], ["models/README.md"])

    def test_reviewed_text_is_clean(self):
        self.assertEqual(len(audit.read_public(ROOT)), len(audit.PUBLIC_FILES))

    def test_redacts_paths_and_credentials(self):
        examples = [b"/" + b"Users/" + b"someone/work", b"ghp_" + b"x"*36,
                    b"-----BEGIN " + b"PRIVATE KEY-----", b"secret\0binary"]
        for data in examples:
            with self.assertRaises(ValueError):
                audit.audit_text("example.txt", data)

    def test_export_does_not_copy_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "export"
            files = audit.read_public(ROOT)
            audit.export_public(destination, files)
            self.assertFalse((destination / "state").exists())
            self.assertFalse((destination / ".git").exists())
            self.assertTrue((destination / "ringphone").stat().st_mode & 0o111)
            with self.assertRaises(FileExistsError):
                audit.export_public(destination, files)

    def test_force_added_private_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "private.npz").write_bytes(b"not a real model")
            subprocess.run(["git", "add", "private.npz"], cwd=root, check=True)
            with self.assertRaisesRegex(ValueError, "Unreviewed"):
                audit.check_tracked(root, {})

    def test_symlink_in_public_tree_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit.export_public(root / "export", audit.read_public(ROOT))
            path = root / "export/README.md"
            path.unlink()
            path.symlink_to(ROOT / "README.md")
            with self.assertRaisesRegex(ValueError, "Symlink"):
                audit.read_public(root / "export")
