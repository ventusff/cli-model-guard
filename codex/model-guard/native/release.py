#!/usr/bin/env python3
"""Release the native package for an official Codex version, on this machine.

One command does the whole release: fetch the tag, merge the patches
(`rebase.py prepare`), regenerate the lockfile and schemas and bump the version
(`rebase.py finish`), build the package in the pinned Rust image, run clippy,
the Rust cases, the Python fixtures and the real PTY sessions, then commit,
tag, push and publish the GitHub release with the archive. Everything heavy
runs in Docker on this machine's cores; GitHub only receives the result.

`--dry-run` stops before the commit and restores the checkout. A patch that no
longer merges stops the run with the conflicting files listed; resolve them in
the work tree, commit each patch under its own name there, and run again with
`--resume`, which skips the fetch and merge.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent
REPO = PLUGIN.parents[1]
OFFICIAL_ASSET = "codex-package-x86_64-unknown-linux-musl.tar.gz"
APT = "apt-get update -qq >/dev/null && apt-get install -y -qq pkg-config libssl-dev libcap-dev python3 >/dev/null"


def run(args, cwd=None, env=None, check=True, capture=False, input=None):
    return subprocess.run([str(arg) for arg in args], cwd=cwd, env=env, check=check, text=True,
                          capture_output=capture, input=input)


def out(args, cwd=None):
    return run(args, cwd=cwd, capture=True).stdout.strip()


def release():
    return json.loads((HERE / "release.json").read_text())


def latest_stable_tag():
    tags = out(["gh", "release", "list", "-R", "openai/codex", "--exclude-pre-releases", "--limit", "30",
                "--json", "tagName", "-q", ".[].tagName"]).splitlines()
    stable = [tag for tag in tags if tag.startswith("rust-v") and tag.count(".") == 2 and "-" not in tag[6:]]
    return sorted(stable, key=lambda tag: [int(part) for part in tag[6:].split(".")])[-1]


def docker(image, mounts, workdir, script, jobs):
    """Run `script` as root in the pinned Rust image, then give the outputs back to this user."""
    command = ["docker", "run", "--rm", "--cpus", str(jobs), "-w", workdir]
    for host, guest in mounts.items():
        command += ["-v", f"{host}:{guest}"]
    owned = " ".join(guest.split(":")[0] for guest in mounts.values() if not guest.endswith(":ro"))
    command += [image, "bash", "-c",
                f"{APT}; git config --global --add safe.directory '*'; set -e\n{script.strip()}\nchown -R {os.getuid()}:{os.getgid()} {owned}"]
    run(command)


def ensure_official(work, tag):
    official = work / "official"
    if not (official / "bin/codex").is_file():
        official.mkdir(parents=True, exist_ok=True)
        run(["gh", "release", "download", tag, "-R", "openai/codex", "-p", OFFICIAL_ASSET, "-D", official, "--clobber"])
        run(["tar", "-xzf", official / OFFICIAL_ASSET, "-C", official])
    return official


def ensure_venv(work):
    venv = work / "venv"
    python = venv / "bin/python"
    if not python.is_file():
        run([sys.executable, "-m", "venv", venv])
        run([venv / "bin/pip", "install", "-q", "--require-hashes", "-r", PLUGIN / "requirements.lock"])
        run([venv / "bin/pip", "install", "-q", "--no-deps", "-e", PLUGIN])
        run([venv / "bin/pip", "install", "-q", "--require-hashes", "-r", PLUGIN / "tests/requirements.lock"])
    return python


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tag", help="Official Codex tag (rust-vX.Y.Z); default is upstream's latest stable release")
    parser.add_argument("--version", help="Plugin version to release; default bumps the minor version")
    parser.add_argument("--work-dir", type=Path, help="Work directory; default ~/.cache/model-guard/release-<codex version>")
    parser.add_argument("--jobs", type=int, default=max(2, os.cpu_count() // 2), help="Cores for the container (default: half)")
    parser.add_argument("--dry-run", action="store_true", help="Build and verify, then restore the checkout instead of publishing")
    parser.add_argument("--resume", action="store_true", help="Skip fetch and merge: the work tree already carries the resolved patches")
    args = parser.parse_args()

    if out(["git", "status", "--porcelain"], cwd=REPO):
        sys.exit("release: the checkout has uncommitted changes; commit or stash them first")
    for tool in ("docker", "gh", "git"):
        if shutil.which(tool) is None:
            sys.exit(f"release: {tool} is required")
    pinned = release()
    tag = args.tag or latest_stable_tag()
    codex_version = tag.removeprefix("rust-v")
    if codex_version == pinned["codex_version"] and pinned["sha256"] and not args.dry_run:
        print(f"Codex {codex_version} is already the released native package ({pinned['plugin_version']}); nothing to do.")
        return 0
    work = (args.work_dir or Path.home() / ".cache/model-guard" / f"release-{codex_version}").expanduser().absolute()
    source = work / "source"
    official = ensure_official(work, tag)

    if not args.resume:
        if source.exists():
            shutil.rmtree(source)
        prepared = run([sys.executable, HERE / "rebase.py", "prepare", "--tag", tag, "--work-dir", work], check=False)
        if prepared.returncode == 2:
            print(f"release: resolve the conflicts in {source}, commit each patch under its own name, then run again with --resume")
            return 2
        if prepared.returncode != 0:
            return prepared.returncode
    rust = out(["python3", "-c", "import sys,tomllib; print(tomllib.loads(open(sys.argv[1]).read())['toolchain']['channel'])",
                source / "codex-rs/rust-toolchain.toml"])
    image = f"rust:{rust}-bookworm"
    upstream = out(["git", "rev-list", "--max-parents=0", "HEAD"], cwd=source)
    print(f"Codex {codex_version} at {upstream}, Rust {rust}, {args.jobs} cores")

    (work / "target").mkdir(exist_ok=True)
    mounts = {str(REPO): "/repo", str(work): "/work", str(official): "/official:ro"}
    version = f" --version {args.version}" if args.version else ""
    build = work / "build"
    if build.exists():
        shutil.rmtree(build)
    docker(image, mounts, "/work/source", f"""
python3 /repo/codex/model-guard/native/rebase.py finish --source /work/source --official-package /official --tag {tag} --target-dir /work/target{version}
python3 /repo/codex/model-guard/native/build.py --work-dir /work/build --official-package /official --jobs {args.jobs}
cd /work/build/source/codex-rs
export CARGO_TARGET_DIR=/work/build/target INSTA_WORKSPACE_ROOT=/work/build/source/codex-rs RUST_MIN_STACK=16777216
cargo clippy --locked -p codex-tui --tests -j {args.jobs} -- -D warnings
cargo test --locked -p codex-tui -j {args.jobs} --lib -- native_guard model_guard
cargo test --locked -p codex-core -j {args.jobs} --test all -- model_routing
""", args.jobs)

    native = build / "package/bin/codex"
    python = ensure_venv(work)
    env = dict(os.environ, PYTHONPATH=str(PLUGIN), MODEL_GUARD_INTEGRATION="1", MODEL_GUARD_CODEX_BIN=str(native),
               MODEL_GUARD_NATIVE_BIN=str(native), MODEL_GUARD_STOCK_BIN=str(official / "bin/codex"))
    run([python, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=PLUGIN, env=env, input="")
    run([REPO / "tests/run.sh"], cwd=REPO)
    run([sys.executable, HERE / "rebase.py", "record", "--record", build / "build-record.json"])
    current = release()
    archive = build / "model-guard-codex-linux-x86_64.tar.gz"
    print(f"Built model-guard {current['plugin_version']} on Codex {codex_version}: {archive} ({current['sha256']})")

    if args.dry_run:
        run(["git", "checkout", "--", "."], cwd=REPO)
        run(["git", "clean", "-fdq", "--", "codex/model-guard/native"], cwd=REPO)
        print("Dry run: the checkout is restored; nothing was committed or published.")
        return 0
    tag_name = f"v{current['plugin_version']}"
    run(["git", "add", "-A"], cwd=REPO)
    run(["git", "commit", "-q", "-m", f"model-guard {current['plugin_version']} — Codex {codex_version}"], cwd=REPO)
    run(["git", "push", "-q", "origin", "HEAD:main"], cwd=REPO)
    run(["git", "tag", tag_name], cwd=REPO)
    run(["git", "push", "-q", "origin", tag_name], cwd=REPO)
    run(["gh", "release", "create", tag_name, archive, "--title", f"model-guard {current['plugin_version']}", "--notes",
         f"Native Codex package on official Codex {codex_version} (SHA-256 `{current['sha256']}`). "
         "Installed machines move with `model-guard-codex update`; a Model Guard build with auto-update on installs it at its next start."],
        cwd=REPO)
    print(f"Published {tag_name}; installed machines update at their next Codex start or with model-guard-codex update.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as exc:
        sys.exit(f"release: step failed with exit code {exc.returncode}: {' '.join(map(str, exc.cmd))[:200]}")
