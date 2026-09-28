"""Install a native Codex package with a reversible executable symlink."""

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import uuid
import venv

from . import __version__, sessions
from .launcher import atomic_json, data_home


START = "# >>> model-guard-codex >>>"
END = "# <<< model-guard-codex <<<"
BLOCK = re.compile(r"(?m)^" + re.escape(START) + r"\n.*?^" + re.escape(END) + r"\n?", re.S)


def replace_block(text, block):
    if text.count(START) != text.count(END) or text.count(START) > 1:
        raise RuntimeError("Incomplete or duplicate Model Guard shell markers; repair that block before installing")
    if START in text:
        return BLOCK.sub(lambda _: block, text)
    if not block:
        return text
    return text + ("\n" if text and not text.endswith("\n") else "") + block


def shell_block(root, *, fish=False):
    directory = str(root / "bin")
    if fish:
        quoted = "'" + directory.replace("\\", "\\\\").replace("'", "\\'") + "'"
        # --path changes only this shell's PATH, never persistent fish_user_paths.
        return START + "\nfish_add_path --path --move -- " + quoted + "\n" + END + "\n"
    return (
        START + "\n"
        + "case \":${PATH}:\" in\n"
        + "  *" + shlex.quote(":" + directory + ":") + "*) ;;\n"
        + "  *) export PATH=" + shlex.quote(directory) + ":\"${PATH}\" ;;\n"
        + "esac\n" + END + "\n"
    )


def atomic_text(path, text, mode=None, *, follow_symlinks=True):
    # Follow an existing dotfile symlink rather than replacing the user's dotfile link.
    path = path.resolve() if follow_symlinks and path.is_symlink() else path
    existing_mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    temporary = path.with_name(path.name + ".model-guard-" + uuid.uuid4().hex)
    try:
        temporary.write_text(text)
        temporary.chmod(mode if mode is not None else existing_mode)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def link(path, target):
    temporary = path.with_name(path.name + ".model-guard-" + uuid.uuid4().hex)
    try:
        temporary.symlink_to(target)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@contextmanager
def installation_lock():
    root = data_home().expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    with (root / "install.lock").open("a") as stream:
        os.chmod(stream.fileno(), 0o600)
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def legacy_launcher(root, command):
    suffix = " run" if command == "codex" else ""
    return ("#!/bin/sh\n# model-guard-codex managed launcher\n"
            + "export MODEL_GUARD_CODEX_HOME=" + shlex.quote(str(root)) + "\n"
            + "exec " + shlex.quote(str(root / "current/bin/model-guard-codex")) + suffix + ' "$@"\n')


def remove_legacy(root, state, changed=None):
    """Remove only the previously generated terminal-wrapper integration."""
    for name in state.get("shell_files", []):
        path = Path(name)
        if not path.exists():
            continue
        before = path.read_text()
        replace_block(before, "")
        match = BLOCK.search(before)
        if match and match[0] != shell_block(root, fish=path.suffix == ".fish"):
            raise RuntimeError(f"Edited legacy shell block requires manual reconciliation: {path}")
    for name in state.get("shell_files", []):
        path = Path(name)
        if path.exists():
            before = path.read_text()
            after = replace_block(before, "")
            if after != before:
                target = path.resolve()
                mode = target.stat().st_mode & 0o777
                atomic_text(path, after)
                if changed is not None:
                    changed.append((target, before, after, mode))
    path = root / "bin/codex"
    if path.is_file() and not path.is_symlink() and path.stat().st_size < 4096:
        if path.read_text() == legacy_launcher(root, "codex"):
            before, mode = path.read_text(), path.stat().st_mode & 0o777
            path.unlink()
            if changed is not None:
                changed.append((path, before, None, mode))


def prune(root, keep):
    """Delete versioned packages and environments that nothing references.

    `keep` holds the resolved directories of the active installation. Only
    real directories inside the installation root are candidates, and a
    directory some running process executes from (a Codex session, or a
    helper running from one of the Python environments) is kept until that
    process ends, because it keeps reading its own files. The `current` link
    of the earlier launcher layout is removed once nothing needs it.
    """
    root = root.resolve()
    in_use = [executable.resolve() for executable in sessions.executables()]
    removed = []
    for parent in (root / "envs", root / "native"):
        if parent.is_symlink() or not parent.is_dir():
            continue
        for child in sorted(parent.iterdir()):
            if child.is_symlink() or not child.is_dir() or child.stat().st_uid != os.getuid():
                continue
            resolved = child.resolve()
            if not resolved.is_relative_to(root) or resolved in keep:
                continue
            if any(executable.is_relative_to(resolved) for executable in in_use):
                continue
            shutil.rmtree(child)
            removed.append(child)
    current = root / "current"
    if current.is_symlink():
        current.unlink()
    return removed


def install(source, language=None, native_package=None, entry=None):
    with installation_lock():
        return install_locked(source, language, native_package, entry)


def install_locked(source, language, native_package, entry):
    release = json.loads((source / "native/release.json").read_text())
    if (platform.system(), platform.machine()) != ("Linux", "x86_64"):
        raise RuntimeError("This prebuilt package supports Linux x86_64; use the documented source build on other platforms")
    libc, version = platform.libc_ver()
    if libc != "glibc" or tuple(map(int, version.split(".")[:2])) < (2, 39):
        raise RuntimeError("This native package requires glibc 2.39 or newer")
    root = data_home().expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    state_path = root / "install.json"
    previous = json.loads(state_path.read_text()) if state_path.exists() else {}
    entry = Path(entry or previous.get("entry") or Path.home() / ".local/bin/codex").absolute()
    if not entry.is_symlink():
        raise RuntimeError(f"Expected the official standalone Codex symlink at {entry}; refusing to replace an unmanaged file")
    current_target = os.readlink(entry)
    if previous.get("native_binary") and Path(previous["native_binary"]).resolve() == entry.resolve():
        original = previous["original_entry"]
    else:
        output = subprocess.check_output([str(entry), "--version"], text=True).strip()
        if output != "codex-cli " + release["codex_version"]:
            raise RuntimeError("Native package and installed official Codex versions must match")
        original = current_target
    tools_entry = entry.with_name("model-guard-codex")
    managed_tools = root / "bin/model-guard-codex"
    if os.path.lexists(tools_entry) and (not tools_entry.is_symlink() or tools_entry.resolve() != managed_tools.resolve()):
        raise RuntimeError(f"Refusing to replace an unmanaged command: {tools_entry}")
    if os.path.lexists(managed_tools):
        expected = str(Path(previous["environment"]) / "bin/model-guard-codex") if previous.get("environment") else None
        native_managed = managed_tools.is_symlink() and os.readlink(managed_tools) == expected
        legacy_managed = (not managed_tools.is_symlink() and managed_tools.is_file()
                          and managed_tools.stat().st_size < 4096
                          and managed_tools.read_text() == legacy_launcher(data_home().expanduser().absolute(), "model-guard-codex"))
        if not (native_managed or legacy_managed):
            raise RuntimeError(f"Refusing to replace an unmanaged helper: {managed_tools}")
    with tempfile.TemporaryDirectory(prefix="native-install-", dir=root) as temporary:
        temp = Path(temporary)
        archive = Path(native_package) if native_package else temp / "native.tar.gz"
        if native_package is None:
            urllib.request.urlretrieve(release["url"], archive)
        if sha256(archive) != release["sha256"]:
            raise RuntimeError("Native package checksum does not match this plugin release")
        unpacked = temp / "package"
        unpacked.mkdir()
        with tarfile.open(archive, "r:gz") as bundle:
            bundle.extractall(unpacked, filter="data")
        binary = unpacked / "bin/codex"
        output = subprocess.check_output([str(binary), "--version"], text=True).strip()
        if output != "codex-cli " + release["codex_version"] + "+model-guard." + __version__:
            raise RuntimeError("The package is not the expected native Model Guard build")
        identifier = __version__ + "-" + uuid.uuid4().hex[:8]
        environment = root / "envs" / identifier
        venv.create(environment, with_pip=True)
        python = environment / "bin/python"
        subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "--require-hashes", "-r", str(source / "requirements.lock")], check=True)
        subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-deps", str(source)], check=True)
        package = root / "native" / identifier
        package.parent.mkdir(exist_ok=True)
        unpacked.rename(package)
    if not entry.is_symlink() or os.readlink(entry) != current_target:
        raise RuntimeError("Codex entry changed during preparation; refusing to overwrite another update")
    cfg_path = root / "config.json"
    cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {"language": "en", "show_account": True, "auto_update": True}
    if language:
        cfg["language"] = language
    atomic_json(cfg_path, cfg)
    (root / "bin").mkdir(exist_ok=True)
    state = {"version": __version__, "codex_version": release["codex_version"], "entry": str(entry), "original_entry": original,
             "native_binary": str(package / "bin/codex"), "binary_sha256": sha256(package / "bin/codex"),
             "package_sha256": release["sha256"], "upstream_commit": release["upstream_commit"],
             "environment": str(environment)}
    changed = []
    legacy_changed = []
    published = False
    try:
        remove_legacy(data_home().expanduser().absolute(), previous, legacy_changed)
        for path, target in ((managed_tools, environment / "bin/model-guard-codex"), (tools_entry, managed_tools)):
            old = ("link", os.readlink(path)) if path.is_symlink() else ("file", path.read_text(), path.stat().st_mode & 0o777) if path.exists() else None
            link(path, target)
            changed.append((path, str(target), old))
        atomic_json(state_path, state)
        published = True
        link(entry, package / "bin/codex")
    except BaseException:
        for path, target, old in reversed(changed):
            # Preserve a concurrent manual change made outside the installer lock.
            if path.is_symlink() and os.readlink(path) == target:
                if old is None:
                    path.unlink()
                elif old[0] == "link":
                    link(path, old[1])
                else:
                    atomic_text(path, old[1], old[2], follow_symlinks=False)
        for path, before, after, mode in reversed(legacy_changed):
            unchanged = (not os.path.lexists(path)) if after is None else (
                not path.is_symlink() and path.is_file() and path.read_text() == after)
            if unchanged:
                atomic_text(path, before, mode, follow_symlinks=False)
        published_bytes = (json.dumps(state, ensure_ascii=False, indent=2) + "\n").encode()
        if published and not state_path.is_symlink() and state_path.is_file() and state_path.read_bytes() == published_bytes:
            atomic_json(state_path, previous)
        raise
    removed = prune(root, {package.resolve(), environment.resolve()})
    print(f"Installed native Model Guard {__version__}. New codex and cx invocations use it, including resume and fork.")
    if removed:
        print(f"Removed {len(removed)} unused earlier package(s) and environment(s).")
    stale = sessions.not_running(package / "bin/codex")
    if stale:
        print(f"{len(stale)} running Codex session(s) keep their current executable and do not show Model Guard. "
              "Finish or /quit each one, then `codex resume` in its directory:")
        print(sessions.describe(stale))
    return 0


def remove():
    with installation_lock():
        return remove_locked()


def remove_locked():
    root = data_home().expanduser().resolve()
    path = root / "install.json"
    state = json.loads(path.read_text())
    remove_legacy(data_home().expanduser().absolute(), state)
    restored = False
    if state.get("native_binary"):
        entry = Path(state["entry"])
        if entry.is_symlink() and entry.resolve() == Path(state["native_binary"]).resolve():
            original = Path(state["original_entry"])
            original = original if original.is_absolute() else entry.parent / original
            if not original.is_file() or not os.access(original, os.X_OK):
                raise RuntimeError("The original Codex executable is no longer available; current entry preserved")
            link(entry, state["original_entry"])
            restored = True
        tools_entry = entry.with_name("model-guard-codex")
        if tools_entry.is_symlink() and tools_entry.resolve() == (root / "bin/model-guard-codex").resolve():
            tools_entry.unlink()
    print(("Restored the official Codex entry." if restored else "Preserved the existing Codex entry.") + " Running sessions, packages, preferences and backups are retained.")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Install native Model Guard for Codex")
    parser.add_argument("--language", choices=("en", "zh"))
    parser.add_argument("--native-package", type=Path)
    parser.add_argument("--entry", type=Path)
    args = parser.parse_args()
    try:
        return install(Path(__file__).resolve().parents[1], args.language, args.native_package, args.entry)
    except (RuntimeError, OSError, ValueError, KeyError, tarfile.TarError, subprocess.CalledProcessError) as exc:
        print(f"Model Guard installation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
