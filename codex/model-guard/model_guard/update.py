"""Move an installation to the plugin's current release, official Codex included.

The native package tracks one official Codex version. An update therefore has
two halves: the official standalone package is brought to that version with
OpenAI's own installer, which also makes it the entry again, and the new
plugin source then installs its native package on top. The new source runs its
own installer, so the version checks are the new release's, not this one's.
A launcher another tool keeps at the entry is put back after OpenAI's
installer replaced it.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import uuid

from . import __version__
from .launcher import data_home


SOURCE_URL = "https://github.com/ventusff/cli-model-guard/archive/refs/heads/main.tar.gz"
PLUGIN_PATH = "codex/model-guard"
OFFICIAL_INSTALLER = "https://chatgpt.com/codex/install.sh"


def fetch_source(destination):
    """Download the repository at main and return its Codex plugin directory."""
    archive = destination / "source.tar.gz"
    urllib.request.urlretrieve(SOURCE_URL, archive)
    with tarfile.open(archive, "r:gz") as bundle:
        bundle.extractall(destination, filter="data")
    roots = [child for child in destination.iterdir() if child.is_dir()]
    if len(roots) != 1 or not (roots[0] / PLUGIN_PATH / "native/release.json").is_file():
        raise RuntimeError("The downloaded source does not contain the Codex plugin")
    return roots[0] / PLUGIN_PATH


def source_version(source):
    for line in (source / "model_guard/__init__.py").read_text().splitlines():
        if line.startswith("__version__"):
            return line.split("=", 1)[1].strip().strip("\"'")
    raise RuntimeError("The plugin source names no version")


def codex_home():
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))


def official_version():
    """Version of the official standalone package the installer keeps current, or None."""
    package = codex_home() / "packages/standalone/current/codex-package.json"
    try:
        return json.loads(package.read_text()).get("version")
    except (OSError, ValueError):
        return None


def install_official(version):
    """Install official standalone Codex `version`; it becomes the entry.

    This is the command stock Codex runs for `codex update`, pinned to a
    release. The script is fetched with curl because the host serves it to
    curl and refuses plain library clients.
    """
    subprocess.run(["sh", "-c", 'curl -fsSL "$0" | sh -s -- --release "$1"', OFFICIAL_INSTALLER, version],
                   check=True, env={**os.environ, "CODEX_NON_INTERACTIVE": "1"})


def install_official_keeping(entry, version):
    """Install official Codex `version`, keeping a launcher that occupies `entry`."""
    launcher = None if entry.is_symlink() or not entry.is_file() else (entry.read_bytes(), entry.stat().st_mode & 0o777)
    install_official(version)
    if launcher and (entry.is_symlink() or not entry.is_file() or entry.read_bytes() != launcher[0]):
        temporary = entry.with_name(entry.name + ".model-guard-" + uuid.uuid4().hex)
        try:
            temporary.write_bytes(launcher[0])
            temporary.chmod(launcher[1])
            temporary.replace(entry)
        finally:
            temporary.unlink(missing_ok=True)


def update(source=None, language=None):
    from .install import entry_runs

    state_path = data_home() / "install.json"
    state = json.loads(state_path.read_text()) if state_path.is_file() else {}
    entry = Path(state.get("entry") or Path.home() / ".local/bin/codex")
    with tempfile.TemporaryDirectory(prefix="model-guard-update-") as temporary:
        source = Path(source).resolve() if source else fetch_source(Path(temporary))
        release = json.loads((source / "native/release.json").read_text())
        version = source_version(source)
        active = bool(state.get("native_binary")) and entry_runs(
            entry, state["native_binary"], f"{state.get('codex_version')}+model-guard.{state.get('version')}")
        if version == __version__ and state.get("upstream_commit") == release["upstream_commit"] and active:
            print(f"Model Guard {version} on Codex {release['codex_version']} is already installed and active.")
            return 0
        if official_version() != release["codex_version"]:
            print(f"Installing official Codex {release['codex_version']} first.", flush=True)
            install_official_keeping(entry, release["codex_version"])
        command = [sys.executable, str(source / "scripts/install.py")]
        if language:
            command += ["--language", language]
        # The new installer resolves its own package; an inherited PYTHONPATH
        # would otherwise leak this tree into the environment it creates.
        environment = {name: value for name, value in os.environ.items() if name != "PYTHONPATH"}
        return subprocess.run(command, env=environment).returncode

