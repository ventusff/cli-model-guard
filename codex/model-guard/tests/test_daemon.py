import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from model_guard import daemon


class DaemonTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.proc = self.home / "proc"
        self.log = self.home / "codex.log"
        self.codex = self.home / "native/bin/codex"
        self.codex.parent.mkdir(parents=True)
        self.version = "0.162.1+model-guard.9.9.9"
        self.selected = self.home / "selected"
        self.update_fails = self.home / "update-fails"
        self.codex.write_text(
            "#!/bin/sh\n"
            f'echo "$*" >> "{self.log}"\n'
            'if [ "$3" = version ]; then\n'
            f'  if [ -f "{self.selected}" ]; then printf \'{{"status":"stopped","managedCodexVersion":"%s"}}\\n\' "$(cat "{self.selected}")"; '
            'else echo \'{"status":"stopped"}\'; fi\n'
            'elif [ "$3" = update ]; then\n'
            f'  if [ -f "{self.update_fails}" ]; then echo "error: app server is running but is not managed" >&2; exit 1; fi\n'
            "  echo '{\"status\":\"updated\"}'\n"
            "fi\n")
        self.codex.chmod(0o755)
        self.digest = hashlib.sha256(self.codex.read_bytes()).hexdigest()
        self.processes = []

    def server(self, pid, contents, argv=(b"codex", b"app-server", b"--listen", b"unix://")):
        directory = self.proc / str(pid)
        directory.mkdir(parents=True)
        (directory / "cmdline").write_bytes(b"\0".join(argv) + b"\0")
        executable = self.home / f"releases/{pid}/bin/codex"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(contents)
        (directory / "exe").symlink_to(executable)
        self.processes.append((pid, str(executable)))

    def adopt(self):
        with patch("model_guard.sessions.PROC", self.proc), patch("model_guard.sessions._processes", return_value=self.processes):
            return daemon.adopt(self.codex, self.version, self.digest)

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_a_server_running_this_build_needs_nothing(self):
        self.server(41, self.codex.read_bytes())
        self.assertIsNone(self.adopt())
        self.assertEqual(self.calls(), [])

    def test_a_running_server_of_another_build_is_named_and_never_restarted(self):
        self.server(40, b"#!/bin/sh\n", argv=(b"codex", b"resume"))
        self.server(42, b"official build")
        line = self.adopt()
        self.assertIn("pid 42", line)
        self.assertIn(daemon.SWITCH, line)
        self.assertEqual(self.calls(), [])

    def test_a_stopped_server_of_another_build_is_switched(self):
        self.selected.write_text("0.162.1")
        self.assertEqual(self.adopt(), "The Codex background server starts this build from now on.")
        self.assertEqual(self.calls(), ["app-server daemon version", "app-server daemon update --from-cli -y"])

    def test_a_stopped_server_already_on_this_build_or_never_selected_is_left_alone(self):
        self.assertIsNone(self.adopt())
        self.selected.write_text(self.version)
        self.assertIsNone(self.adopt())
        self.assertEqual(self.calls(), ["app-server daemon version"] * 2)

    def test_a_failed_switch_names_the_reason_and_the_command(self):
        self.selected.write_text("0.158.0+model-guard.1.11.1")
        self.update_fails.touch()
        line = self.adopt()
        self.assertIn("0.158.0+model-guard.1.11.1", line)
        self.assertIn("not managed", line)
        self.assertIn(daemon.SWITCH, line)

    def test_describe_tells_doctor_which_build_hosts_threads(self):
        self.server(43, self.codex.read_bytes())
        with patch("model_guard.sessions.PROC", self.proc), patch("model_guard.sessions._processes", return_value=self.processes):
            self.assertEqual(daemon.describe(self.version, self.digest, self.codex), "Background server: this build (pid 43)")


if __name__ == "__main__":
    unittest.main()
