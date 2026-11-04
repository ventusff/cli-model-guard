import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from model_guard import __version__, update


def plugin_source(root, version, codex_version, commit):
    (root / "native").mkdir(parents=True)
    (root / "native/release.json").write_text(json.dumps({"codex_version": codex_version, "upstream_commit": commit}))
    (root / "model_guard").mkdir()
    (root / "model_guard/__init__.py").write_text(f'"""Fixture."""\n\n__version__ = "{version}"\n')
    (root / "scripts").mkdir()
    (root / "scripts/install.py").write_text(
        "import json, sys\nfrom pathlib import Path\n"
        "Path(sys.argv[0]).with_name('argv.json').write_text(json.dumps(sys.argv[1:]))\n")
    return root


class UpdateTests(unittest.TestCase):
    def test_update_brings_official_codex_to_the_release_before_installing(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            guard, codex_home = home / "guard", home / "codex"
            guard.mkdir()
            package = codex_home / "packages/standalone/current"
            package.mkdir(parents=True)
            (package / "codex-package.json").write_text(json.dumps({"version": "0.153.4"}))
            native = guard / "native/old/bin/codex"
            native.parent.mkdir(parents=True)
            native.write_text("")
            entry = home / "bin/codex"
            entry.parent.mkdir()
            entry.symlink_to(native)
            (guard / "install.json").write_text(json.dumps({"version": __version__, "entry": str(entry), "native_binary": str(native), "upstream_commit": "old"}))
            source = plugin_source(home / "source", "9.9.9", "0.155.1", "new")
            installed = []

            def install_official(version):
                installed.append(version)
                (package / "codex-package.json").write_text(json.dumps({"version": version}))

            environment = {"MODEL_GUARD_CODEX_HOME": str(guard), "CODEX_HOME": str(codex_home)}
            with patch.dict(os.environ, environment), patch("model_guard.update.install_official", side_effect=install_official), patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(update.update(source, "zh"), 0)
                self.assertEqual(installed, ["0.155.1"])
                self.assertEqual(json.loads((source / "scripts/argv.json").read_text()), ["--language", "zh"])
                # The same release again: official Codex already matches, and the new installer decides the rest.
                (source / "scripts/argv.json").unlink()
                self.assertEqual(update.update(source), 0)
                self.assertEqual(installed, ["0.155.1"])
                self.assertEqual(json.loads((source / "scripts/argv.json").read_text()), [])

    def test_update_puts_back_a_launcher_the_official_installer_replaced(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            guard, codex_home = home / "guard", home / "codex"
            guard.mkdir()
            package = codex_home / "packages/standalone/current"
            package.mkdir(parents=True)
            (package / "codex-package.json").write_text(json.dumps({"version": "0.153.4"}))
            entry = home / "bin/codex"
            entry.parent.mkdir()
            entry.write_text("#!/bin/sh\nexec launcher\n")
            entry.chmod(0o750)
            (guard / "install.json").write_text(json.dumps({"entry": str(entry)}))
            source = plugin_source(home / "source", "9.9.9", "0.155.1", "new")

            def install_official(version):
                entry.replace(entry.with_name("displaced"))
                entry.symlink_to(package / "bin/codex")

            with patch.dict(os.environ, {"MODEL_GUARD_CODEX_HOME": str(guard), "CODEX_HOME": str(codex_home)}), patch("model_guard.update.install_official", side_effect=install_official), patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(update.update(source), 0)
            self.assertFalse(entry.is_symlink())
            self.assertEqual(entry.read_text(), "#!/bin/sh\nexec launcher\n")
            self.assertEqual(entry.stat().st_mode & 0o777, 0o750)
            self.assertTrue((source / "scripts/argv.json").is_file())

    def test_update_is_a_no_op_when_the_active_build_is_the_current_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            guard = home / "guard"
            native = guard / "native/current/bin/codex"
            native.parent.mkdir(parents=True)
            native.write_text("")
            entry = home / "bin/codex"
            entry.parent.mkdir()
            entry.symlink_to(native)
            (guard / "install.json").write_text(json.dumps({"version": __version__, "entry": str(entry), "native_binary": str(native), "upstream_commit": "same"}))
            source = plugin_source(home / "source", __version__, "0.155.1", "same")
            with patch.dict(os.environ, {"MODEL_GUARD_CODEX_HOME": str(guard), "CODEX_HOME": str(home / "codex")}), patch("model_guard.update.install_official") as official, patch("sys.stdout", new_callable=io.StringIO) as out:
                self.assertEqual(update.update(source), 0)
            official.assert_not_called()
            self.assertFalse((source / "scripts/argv.json").exists())
            self.assertIn("already installed and active", out.getvalue())

    def test_fetch_source_finds_the_codex_plugin_inside_the_repository_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            repository = home / "repo-main"
            plugin_source(repository / update.PLUGIN_PATH, "9.9.9", "0.155.1", "new")
            archive = home / "main.tar.gz"
            with tarfile.open(archive, "w:gz") as bundle:
                bundle.add(repository, arcname=repository.name)
            destination = home / "download"
            destination.mkdir()

            def retrieve(url, target):
                self.assertEqual(url, update.SOURCE_URL)
                Path(target).write_bytes(archive.read_bytes())

            with patch("model_guard.update.urllib.request.urlretrieve", side_effect=retrieve):
                source = update.fetch_source(destination)
            self.assertEqual(source, destination / "repo-main" / update.PLUGIN_PATH)
            self.assertEqual(update.source_version(source), "9.9.9")


if __name__ == "__main__":
    unittest.main()
