#!/usr/bin/env python3
"""Build the pinned native Codex extension and its canonical standalone package."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

HERE = Path(__file__).resolve().parent
HELPERS = {
    "bin/codex-code-mode-host": "--code-mode-host-bin",
    "codex-resources/bwrap": "--bwrap-bin",
    "codex-resources/zsh/bin/zsh": "--zsh-bin",
    "codex-path/rg": "--rg-bin",
}
# The voice runtime ships as a directory whose manifest lists every file's
# digest; the manifest itself is pinned and each listed runtime file is checked.
VOICE = "codex-resources/voice"
VOICE_MANIFEST = VOICE + "/manifest.json"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(args, **kwargs):
    subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def verify_voice(official, release):
    manifest = official / VOICE_MANIFEST
    if digest(manifest) != release["helper_sha256"][VOICE_MANIFEST]:
        raise RuntimeError("Official voice runtime manifest differs from the pinned release")
    for relative, expected in json.loads(manifest.read_text())["sha256"].items():
        if relative.startswith(VOICE + "/") and digest(official / relative) != expected:
            raise RuntimeError(f"Official voice runtime file differs from its manifest: {relative}")


def prepare(work, release):
    source = work / "source"
    source.mkdir()
    run(["git", "init", "--quiet", source])
    run(["git", "-C", source, "fetch", "--depth=1", release["upstream_repository"], release["upstream_ref"]])
    commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "FETCH_HEAD^{commit}"], text=True).strip()
    if commit != release["upstream_commit"]:
        raise RuntimeError("The upstream tag does not match the reviewed source commit")
    run(["git", "-C", source, "checkout", "--quiet", "--detach", commit])
    for patch in release["patches"]:
        path = HERE / patch["file"]
        if digest(path) != patch["sha256"]:
            raise RuntimeError(f"Patch checksum mismatch: {path.name}")
        # Fixed-width terminal snapshots intentionally retain blank cells.
        run(["git", "-C", source, "apply", "--whitespace=nowarn", "--check", path])
        run(["git", "-C", source, "apply", "--whitespace=nowarn", path])
    return source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True, help="New, empty build directory")
    parser.add_argument("--official-package", type=Path, help="Official standalone Codex package root")
    parser.add_argument("--prepare-only", action="store_true", help="Verify and apply source patches without compiling")
    parser.add_argument("--jobs", type=int, default=6)
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    if not args.prepare_only and not args.official_package:
        parser.error("--official-package is required for a complete build")
    release = json.loads((HERE / "release.json").read_text())
    work = args.work_dir.expanduser().absolute()
    work.mkdir(parents=True, exist_ok=False)
    source = prepare(work, release)
    if args.prepare_only:
        print(source)
        return
    official = args.official_package.expanduser().resolve()
    for relative in HELPERS:
        if digest(official / relative) != release["helper_sha256"][relative]:
            raise RuntimeError(f"Official helper differs from the pinned release: {relative}")
    verify_voice(official, release)
    env = dict(os.environ, CARGO_BUILD_JOBS=str(args.jobs), CARGO_TARGET_DIR=str(work / "target"),
               CARGO_PROFILE_RELEASE_DEBUG="0", MODEL_GUARD_VERSION=release["plugin_version"])
    run(["cargo", "+" + release["rust_version"], "build", "--locked", "--release", "-p", "codex-cli", "--bin", "codex"], cwd=source / "codex-rs", env=env)
    binary = work / "target/release/codex"
    expected = "codex-cli " + release["codex_version"] + "+model-guard." + release["plugin_version"]
    if subprocess.check_output([str(binary), "--version"], text=True).strip() != expected:
        raise RuntimeError("Built executable has an unexpected version")
    package = work / "package"
    command = [sys.executable, source / "scripts/build_codex_package.py", "--target", release["target"],
               "--package-version", expected.removeprefix("codex-cli "), "--package-dir", package, "--entrypoint-bin", binary]
    for relative, option in HELPERS.items():
        command += [option, official / relative]
    run(command, env=dict(env, CODEX_REPO_ROOT=str(source)))
    for name in ("LICENSE", "NOTICE"):
        shutil.copy2(source / name, package / name)
    shutil.copytree(official / VOICE, package / VOICE, symlinks=True)
    (package / "codex").symlink_to("bin/codex")
    archive = work / "model-guard-codex-linux-x86_64.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        for child in sorted(package.iterdir()):
            bundle.add(child, arcname=child.name)
    record = {"upstream_commit": release["upstream_commit"], "patches": release["patches"],
              "rust_version": release["rust_version"], "package_sha256": digest(archive), "binary_sha256": digest(package / "bin/codex")}
    (work / "build-record.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"Built {archive}\nSHA-256: {record['package_sha256']}")


if __name__ == "__main__":
    main()
