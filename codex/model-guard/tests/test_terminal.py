"""Exercise native Codex in its own PTY, using isolated homes and a local API."""
import contextlib
import fcntl
import json
import os
from pathlib import Path
import pty
import re
import select
import shutil
import signal
import struct
import subprocess
import tempfile
import termios
import time
import unittest

from test_integration import OFFLINE, ResponsesFixture


class Terminal:
    def __init__(self, command, env, cwd):
        import pyte
        self.screen = pyte.HistoryScreen(120, 32, history=3000)
        self.stream = pyte.ByteStream(self.screen)
        self.output = bytearray()
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 32, 120, 0, 0))
        self.process = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave, env=env, cwd=cwd, start_new_session=True)
        os.close(slave)

    def text(self):
        return "\n".join(self.screen.display)

    def poll(self):
        if select.select([self.master], [], [], .05)[0]:
            try:
                data = os.read(self.master, 65536)
            except OSError:
                return
            self.output.extend(data)
            self.stream.feed(data)
            for query, answer in ((b"\x1b[6n", f"\x1b[{self.screen.cursor.y + 1};{self.screen.cursor.x + 1}R".encode()), (b"\x1b[c", b"\x1b[?1;2c"), (b"\x1b[>c", b"\x1b[>0;136;0c")):
                if query in data:
                    os.write(self.master, answer)

    def wait(self, predicate, timeout=35):
        deadline = time.monotonic() + timeout
        last_enter = time.monotonic()
        while time.monotonic() < deadline:
            self.poll()
            if predicate():
                return
            if time.monotonic() - last_enter > 3 and any(s in self.text().lower() for s in ("press enter", "trust this", "trust the")):
                os.write(self.master, b"\r")
                last_enter = time.monotonic()
        raise AssertionError(self.text())

    def resize(self, rows, columns):
        self.screen.resize(rows, columns)
        fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
        os.killpg(self.process.pid, signal.SIGWINCH)

    def send(self, data):
        os.write(self.master, data)

    def close(self):
        if self.process.poll() is None:
            self.send(b"\x03")
            for _ in range(5): self.poll()
            self.send(b"\x03")
            deadline = time.monotonic() + 5
            while self.process.poll() is None and time.monotonic() < deadline: self.poll()
            if self.process.poll() is None:
                os.killpg(self.process.pid, signal.SIGTERM)
        self.process.wait(timeout=5)
        os.close(self.master)


def fixture_daemons(base):
    """PIDs of the background app-server daemons Codex started under a fixture home."""
    listing = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True, text=True, check=False).stdout
    pids = []
    for line in listing.splitlines():
        pid, _, args = line.strip().partition(" ")
        if args.startswith(str(base) + "/"):
            pids.append(int(pid))
    return pids


@contextlib.contextmanager
def fixture_dir(prefix):
    """A temporary Codex home whose background daemons end with it.

    Codex keeps a daemon per home alive after its sessions exit; the home is
    deleted with the test, so its daemons would otherwise outlive the run.
    """
    with tempfile.TemporaryDirectory(prefix=prefix) as temp:
        try:
            yield temp
        finally:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                pids = fixture_daemons(temp)
                if not pids:
                    break
                for pid in pids:
                    try:
                        os.kill(pid, sig)
                    except ProcessLookupError:
                        pass
                time.sleep(1)


def fixture_home(base, api, alternate_screen="never"):
    """An isolated Codex home on the fixture provider.

    `alternate_screen` selects the terminal layout: Codex's default is the
    alternate screen, whose bottom area is drawn by another path than the
    inline viewport's, so the guard's footer is checked under both.
    """
    home = base / "codex"
    home.mkdir()
    (home / "config.toml").write_text(
        'model = "gpt-6-astra"\nmodel_reasoning_effort = "max"\nmodel_provider = "fixture"\n'
        f'check_for_update_on_startup = false\n[tui]\nresume_cwd = "session"\nalternate_screen = "{alternate_screen}"\n'
        '[plugins."model-guard@personal"]\nenabled = true\n'
        '[model_providers.fixture]\nname = "Fixture"\nwire_api = "responses"\n'
        f'base_url = "http://127.0.0.1:{api.server.server_port}/v1"\n'
        'requires_openai_auth = false\nsupports_websockets = false\n'
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CODEX_", "OPENAI_", "MODEL_GUARD_", "TMUX", "KITTY_"))}
    env.update(CODEX_HOME=str(home), MODEL_GUARD_CODEX_HOME=str(base / "guard"), TERM="xterm-256color", COLORTERM="truecolor", **OFFLINE)
    return env


@unittest.skipUnless(os.environ.get("MODEL_GUARD_NATIVE_BIN"), "requires the native build and pyte")
class NativeTerminalTests(unittest.TestCase):
    def test_native_new_resume_fork_and_paste(self):
        binary = os.environ["MODEL_GUARD_NATIVE_BIN"]
        with fixture_dir("mg-native-pty-") as temp, ResponsesFixture(None, [516]) as api:
            base = Path(temp)
            env = fixture_home(base, api)
            seed = subprocess.run([binary, "exec", "--skip-git-repo-check", "--json", "--sandbox", "read-only", "Reply OK."], env=env, cwd=base, capture_output=True, text=True, timeout=90, check=True)
            thread = next(json.loads(line)["thread_id"] for line in seed.stdout.splitlines() if json.loads(line).get("type") == "thread.started")
            commands = [[binary, "--sandbox", "read-only", "--ask-for-approval", "never", *mode, "Reply OK."] for mode in ([], ["resume", thread], ["fork", thread])]
            if shutil.which("fish"):
                entry = base / "bin/codex"
                entry.parent.mkdir()
                entry.symlink_to(binary)
                env["PATH"] = str(entry.parent) + os.pathsep + env["PATH"]
                commands.append([shutil.which("fish"), "--no-config", "-c", "function cx; codex --dangerously-bypass-approvals-and-sandbox $argv; end; cx $argv", "--", "resume", thread, "Reply OK."])
            for command in commands:
                with self.subTest(command=command):
                    before = len(api.requests)
                    terminal = Terminal(command, env, base)
                    try:
                        terminal.wait(lambda: len(api.requests) > before and "selected gpt-6-astra" in terminal.text())
                        screen = terminal.text()
                        self.assertNotIn("UNVERIFIED", screen)
                        self.assertNotIn("516", screen)
                        self.assertEqual(sum("selected gpt-6-astra" in row for row in screen.splitlines()), 1)
                        self.assertEqual(api.requests[-1]["model"], "gpt-6-astra")
                        self.assertEqual(api.requests[-1]["reasoning"]["effort"], "max")
                        terminal.send(b"\x1b[200~Native pasted prompt.\x1b[201~")
                        terminal.wait(lambda: "Native pasted prompt." in terminal.text())
                        before = len(api.requests)
                        terminal.send(b"\r")
                        terminal.wait(lambda: len(api.requests) > before and "selected gpt-6-astra" in terminal.text())
                        terminal.send(b"/status")
                        terminal.wait(lambda: "/status" in terminal.text())
                        for _ in range(5):
                            terminal.poll()
                        terminal.send(b"\r")
                        terminal.wait(lambda: "Model Guard" in terminal.text() and "measured responses" in terminal.text())
                        terminal.resize(24, 80)
                        terminal.wait(lambda: "selected gpt-6-astra" in terminal.text())
                        terminal.send(b"Resize preserved input.")
                        terminal.wait(lambda: "Resize preserved input." in terminal.text())
                        self.assertEqual(terminal.screen.columns, 80)
                    finally:
                        terminal.close()

    def test_disclosed_route_difference_stops_the_turn(self):
        binary = os.environ["MODEL_GUARD_NATIVE_BIN"]
        with fixture_dir("mg-native-route-") as temp, ResponsesFixture("gpt-4o") as api:
            base = Path(temp)
            terminal = Terminal([binary, "--sandbox", "read-only", "--ask-for-approval", "never", "Reply OK."], fixture_home(base, api, alternate_screen="auto"), base)
            try:
                terminal.wait(lambda: "STOPPED · ROUTE DIFF gpt-6-astra" in terminal.text() and "Model Guard interrupted this turn" in terminal.text())
                # Codex titles the thread from a hidden gpt-5.6-luna thread; only the
                # visible thread's own requests count here.
                visible = lambda: [request for request in api.requests if request["model"] == "gpt-6-astra"]
                # The stop holds every further turn until a model is selected.
                terminal.send(b"Second prompt.")
                terminal.wait(lambda: "Second prompt." in terminal.text())
                terminal.send(b"\r")
                terminal.wait(lambda: "Model Guard held this input" in terminal.text())
                self.assertEqual(len(visible()), 1)
                self.assertIn("Second prompt.", terminal.text())
                # Compaction is enqueued around the composer and meets the same hold.
                terminal.send(b"\x15/compact")
                terminal.wait(lambda: "/compact" in terminal.text())
                for _ in range(5):
                    terminal.poll()
                terminal.send(b"\r")
                terminal.wait(lambda: terminal.text().count("Model Guard held this input") >= 2)
                self.assertEqual(len(visible()), 1)
            finally:
                terminal.close()

    def test_body_label_difference_stops_the_turn_and_is_explained_in_status(self):
        binary = os.environ["MODEL_GUARD_NATIVE_BIN"]
        with fixture_dir("mg-native-label-") as temp, ResponsesFixture(None, label="gpt-4o") as api:
            base = Path(temp)
            terminal = Terminal([binary, "--sandbox", "read-only", "--ask-for-approval", "never", "Reply OK."], fixture_home(base, api), base)
            try:
                terminal.wait(lambda: "STOPPED · labeled gpt-4o · requested gpt-6-astra" in terminal.text())
                self.assertNotIn("ROUTE DIFF", terminal.text())
                terminal.send(b"/status")
                terminal.wait(lambda: "/status" in terminal.text())
                for _ in range(5):
                    terminal.poll()
                terminal.send(b"\r")
                terminal.wait(lambda: "Response body label: gpt-4o" in terminal.text() and "Selected model: gpt-6-astra" in terminal.text())
            finally:
                terminal.close()

    def test_thread_restored_on_the_reserve_model_stops_before_its_first_turn(self):
        binary = os.environ["MODEL_GUARD_NATIVE_BIN"]
        with fixture_dir("mg-native-reserve-") as temp, ResponsesFixture(None, label="gpt-reserve") as api:
            base = Path(temp)
            env = fixture_home(base, api)
            # The thread was last used on Codex's reserve model; the configured model is still gpt-6-astra.
            seed = subprocess.run([binary, "-c", "model='gpt-reserve'", "exec", "--skip-git-repo-check", "--json", "--sandbox", "read-only", "Reply OK."], env=env, cwd=base, capture_output=True, text=True, timeout=90, check=True)
            thread = next(json.loads(line)["thread_id"] for line in seed.stdout.splitlines() if json.loads(line).get("type") == "thread.started")
            self.assertEqual(api.requests[-1]["model"], "gpt-reserve")
            terminal = Terminal([binary, "--sandbox", "read-only", "--ask-for-approval", "never", "resume", thread, "Reply again."], env, base)
            try:
                terminal.wait(lambda: "STOPPED · request gpt-reserve · selected gpt-6-astra" in terminal.text() and "Model Guard held this input" in terminal.text())
                for _ in range(10):
                    terminal.poll()
                self.assertEqual([request["model"] for request in api.requests], ["gpt-reserve"])
                self.assertIn("Reply again.", terminal.text())
            finally:
                terminal.close()


    @unittest.skipUnless(os.environ.get("MODEL_GUARD_STOCK_BIN"), "requires the official comparison binary")
    def test_native_preserves_stock_scrollback_and_terminal_modes(self):
        modes = []
        for binary in (os.environ["MODEL_GUARD_STOCK_BIN"], os.environ["MODEL_GUARD_NATIVE_BIN"]):
            with self.subTest(binary=binary), fixture_dir("mg-scroll-") as temp, ResponsesFixture(None, output_text="\n".join(f"History line {index:03d}" for index in range(1, 121))) as api:
                base = Path(temp)
                terminal = Terminal([binary, "--sandbox", "read-only", "--ask-for-approval", "never", "Print the history."], fixture_home(base, api), base)
                try:
                    terminal.wait(lambda: "History line 120" in terminal.text())
                    for _ in range(5):
                        terminal.poll()
                    self.assertGreater(len(terminal.screen.history.top), 50)
                    history = "\n".join("".join(cell.data for _, cell in sorted(row.items())) for row in terminal.screen.history.top)
                    self.assertIn("History line 001", history)
                    self.assertIn("History line 060", history)
                    # Terminal wheel/scrollback behavior depends on these modes.
                    # Compare stock Codex, including alternate-screen and mouse modes.
                    modes.append(set(re.findall(rb"\x1b\[\?([0-9;]+)[hl]", terminal.output)))
                    terminal.screen.prev_page()
                    self.assertIn("History line", terminal.text())
                    terminal.screen.next_page()
                    terminal.send(b"Fresh draft")
                    terminal.wait(lambda: "Fresh draft" in terminal.text())
                    self.assertNotIn("Code Mode is unavailable", terminal.output.decode(errors="replace"))
                finally:
                    terminal.close()
        self.assertEqual(modes[0], modes[1])
