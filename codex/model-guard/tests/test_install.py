import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from model_guard import __version__
from model_guard.install import install, remove, replace_block, shell_block, link, legacy_launcher


class InstallTests(unittest.TestCase):
    def test_native_activation_survives_existing_shell_and_removal_preserves_user_edits(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            root, source = home / "guard", home / "source"
            real_root = home / "actual-guard"
            real_root.mkdir(); root.symlink_to(real_root, target_is_directory=True)
            (source / "native").mkdir(parents=True)
            official = home / "official-codex"
            official.write_text('#!/bin/sh\nif [ "$1" = --version ]; then echo "codex-cli 0.153.4"; else printf "stock:%s\\n" "$@"; fi\n')
            official.chmod(0o755)
            entry = home / "bin/codex"
            entry.parent.mkdir(); entry.symlink_to(official)
            package = home / "package/bin"
            package.mkdir(parents=True)
            native = package / "codex"
            native.write_text(f'#!/bin/sh\nif [ "$1" = --version ]; then echo "codex-cli 0.153.4+model-guard.{__version__}"; else printf "native:%s\\n" "$@"; fi\n')
            native.chmod(0o755)
            archive = home / "native.tar.gz"
            with tarfile.open(archive, "w:gz") as bundle:
                bundle.add(native, arcname="bin/codex")
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            (source / "native/release.json").write_text(json.dumps({"sha256": digest, "codex_version": "0.153.4", "upstream_commit": "fixture"}))
            rc = home / "config.fish"
            rc.write_text(replace_block("# before\n", shell_block(root, fish=True)) + "# user's later edit\n")
            (root / "install.json").write_text(json.dumps({"shell_files": [str(rc)]}))
            (root / "bin").mkdir()
            legacy = root / "bin/model-guard-codex"
            legacy.write_text(legacy_launcher(root, "model-guard-codex"))
            legacy.chmod(0o755)
            legacy_codex = root / "bin/codex"
            legacy_codex.write_text(legacy_launcher(root, "codex"))
            legacy_codex.chmod(0o755)
            real_run = subprocess.run
            actual_check_output = subprocess.check_output
            with patch.dict(os.environ, {"MODEL_GUARD_CODEX_HOME": str(root)}), patch("model_guard.install.venv.create"), patch("model_guard.sessions.running", return_value=[]), patch("model_guard.sessions.executables", return_value=[]), patch("model_guard.daemon.adopt", return_value=None) as adopt, patch("model_guard.daemon.selected_version", return_value=None), patch("model_guard.install.subprocess.run", side_effect=lambda args, **kw: None if "pip" in args else real_run(args, **kw)):
                for _ in range(2):
                    state_before = (root / "install.json").read_text()
                    entry_before = os.readlink(entry)
                    rc_before = rc.read_text()
                    launcher_before = legacy_codex.read_text() if legacy_codex.exists() else None
                    def fail_activation(path, target):
                        if path == entry:
                            raise OSError("simulated final activation failure")
                        return link(path, target)
                    with patch("model_guard.install.link", side_effect=fail_activation), self.assertRaises(OSError):
                        install(source, "zh", archive, entry)
                    self.assertEqual(json.loads((root / "install.json").read_text()), json.loads(state_before))
                    self.assertEqual(os.readlink(entry), entry_before)
                    self.assertEqual(rc.read_text(), rc_before)
                    self.assertEqual(legacy_codex.read_text() if legacy_codex.exists() else None, launcher_before)
                    if launcher_before is not None:
                        manual_target = home / "must-not-be-created"
                        def concurrent_edit(path, target):
                            if path == entry:
                                legacy_codex.symlink_to(manual_target)
                                (root / "install.json").write_text('{"manual_edit": true}')
                                raise OSError("simulated concurrent manual edit")
                            return link(path, target)
                        with patch("model_guard.install.link", side_effect=concurrent_edit), self.assertRaises(OSError):
                            install(source, "zh", archive, entry)
                        self.assertTrue(legacy_codex.is_symlink())
                        self.assertFalse(manual_target.exists())
                        self.assertEqual(json.loads((root / "install.json").read_text()), {"manual_edit": True})
                        legacy_codex.unlink()
                        legacy_codex.write_text(launcher_before)
                        legacy_codex.chmod(0o755)
                        (root / "install.json").write_text(state_before)
                    install(source, "zh", archive, entry)
                    self.assertEqual(os.readlink(entry), json.loads((root / "install.json").read_text())["native_binary"])
                    installed = json.loads((root / "install.json").read_text())
                    adopt.assert_called_with(Path(installed["native_binary"]), f"0.153.4+model-guard.{__version__}", installed["binary_sha256"])
                self.assertEqual(actual_check_output([str(entry), "resume", "--last"], text=True), "native:resume\nnative:--last\n")
                self.assertEqual(rc.read_text(), "# before\n# user's later edit\n")
                remove()
            self.assertEqual(os.readlink(entry), str(official))
            self.assertEqual(actual_check_output([str(entry), "resume", "--last"], text=True), "stock:resume\nstock:--last\n")

    def test_incomplete_legacy_markers_are_not_silently_removed(self):
        with self.assertRaises(RuntimeError):
            replace_block("# >>> model-guard-codex >>>\n", "")
