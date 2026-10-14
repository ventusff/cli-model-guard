"""Codex's background server, which hosts the threads of the sessions attached to it.

Model Guard decides inside the process that hosts a thread, so a session
attached to a background server of another build runs unguarded; the native
build therefore declines such a server and runs its threads itself. The
installer makes the server this build whenever that interrupts nothing:
Codex's own `app-server daemon update --from-cli` selects the CLI's package
and pins it, and it restarts a running server, which interrupts the work of
every session attached to it. A running server of another build is therefore
left to the user, with that command.
"""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import subprocess

from . import sessions


SWITCH = "codex app-server daemon update --from-cli"


@dataclass(frozen=True)
class Server:
    pid: int
    sha256: str


def running():
    """The running background server, or None: this user's `codex app-server --listen` process."""
    for pid, executable in sessions._processes():
        if Path(executable.removesuffix(" (deleted)")).name != "codex":
            continue
        try:
            argv = (sessions.PROC / str(pid) / "cmdline").read_bytes().split(b"\0")[1:3]
            if argv != [b"app-server", b"--listen"]:
                continue
            with (sessions.PROC / str(pid) / "exe").open("rb") as stream:
                return Server(pid, hashlib.file_digest(stream, "sha256").hexdigest())
        except OSError:
            continue
    return None


def selected_version(codex):
    """Version of the package the server starts from, or None when none is selected yet."""
    try:
        output = subprocess.run([str(codex), "app-server", "daemon", "version"], capture_output=True,
                                text=True, timeout=30).stdout
        return json.loads(output).get("managedCodexVersion")
    except (OSError, subprocess.TimeoutExpired, ValueError, AttributeError):
        return None


def adopt(codex, version, binary_sha256):
    """Make the background server this build where that interrupts no work.

    Returns the line to show the user, or None when there is nothing to say.
    No running server: a selected package of another build is replaced by
    this one, which the next session starts. A running server of another
    build keeps running; the line names it and the command that switches it.
    """
    server = running()
    if server is not None:
        if server.sha256 == binary_sha256:
            return None
        return (f"The Codex background server (pid {server.pid}) runs another build, so new sessions run their "
                f"threads themselves until it is switched: `{SWITCH}` switches it to this build; it restarts the "
                "server and interrupts work in the sessions attached to it.")
    selected = selected_version(codex)
    if selected is None or selected == version:
        return None
    try:
        result = subprocess.run([str(codex), "app-server", "daemon", "update", "--from-cli", "-y"],
                                capture_output=True, text=True, timeout=300)
        reason = None if result.returncode == 0 else (result.stderr.strip().splitlines() or ["no output"])[-1]
    except (OSError, subprocess.TimeoutExpired) as exc:
        reason = str(exc)
    if reason is not None:
        return f"The Codex background server still starts Codex {selected} ({reason}); `{SWITCH}` switches it to this build."
    return "The Codex background server starts this build from now on."


def describe(version, binary_sha256, codex):
    """One line on the background server for `doctor`."""
    server = running()
    if server is not None:
        if server.sha256 == binary_sha256:
            return f"Background server: this build (pid {server.pid})"
        return (f"Background server: another build (pid {server.pid}); sessions run their threads themselves "
                f"until `{SWITCH}` switches it, which interrupts work in the sessions attached to it")
    selected = selected_version(codex)
    if selected is None or selected == version:
        return "Background server: not running; the next session starts this build"
    return f"Background server: not running; it would start Codex {selected}, and `{SWITCH}` selects this build"
