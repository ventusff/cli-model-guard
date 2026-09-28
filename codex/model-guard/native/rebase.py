#!/usr/bin/env python3
"""Move the native package to another official Codex release.

`prepare` fetches the release tag and applies the source patches with a
three-way merge, one commit per patch. A patch that still conflicts is left in
the tree with conflict markers for a person to resolve and commit.

`finish` takes that tree, regenerates the lockfile and the protocol schemas,
writes the six patches back, pins the release, bumps the plugin version and
rewrites the version strings in the documentation.

`record` copies the built package's digests into `release.json`.

The three steps are the whole of a release except the build (`build.py`), the
tests and the publication, which the release workflow runs around them.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent
REPO = PLUGIN.parents[1]
UPSTREAM = "https://github.com/openai/codex.git"
SOURCE_PATCHES = ("0001-routing-metadata.patch", "0002-native-status-line.patch",
                  "0003-native-status-line-tests.patch", "0006-update-hand-off.patch")
SCHEMAS_PATCH = "0004-protocol-schemas.patch"
PACKAGING_PATCH = "0005-release-packaging.patch"
PATCH_ORDER = ("0001-routing-metadata.patch", "0002-native-status-line.patch",
               "0003-native-status-line-tests.patch", SCHEMAS_PATCH, PACKAGING_PATCH,
               "0006-update-hand-off.patch")
HELPERS = ("bin/codex-code-mode-host", "codex-resources/bwrap", "codex-resources/zsh/bin/zsh",
           "codex-path/rg", "codex-resources/voice/manifest.json")
# Files that state the tracked Codex version, its commit or the Rust image in prose.
VERSIONED_DOCS = ("README.md", "README.zh-CN.md", "AGENTS.md", "codex/model-guard/native/README.md",
                  "codex/model-guard/skills/codex-model-guard/SKILL.md", ".github/workflows/codex-release.yml")
VERSION_FILES = {
    ".claude-plugin/plugin.json": ('"version": "{old}"', '"version": "{new}"'),
    "scripts/lib.sh": ('MG_VERSION="{old}"', 'MG_VERSION="{new}"'),
    "codex/model-guard/.codex-plugin/plugin.json": ('"version": "{old}"', '"version": "{new}"'),
    "codex/model-guard/model_guard/__init__.py": ('__version__ = "{old}"', '__version__ = "{new}"'),
    "codex/model-guard/pyproject.toml": ('version = "{old}"', 'version = "{new}"'),
}


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(args, cwd=None, env=None, check=True, capture=False):
    return subprocess.run([str(arg) for arg in args], cwd=cwd, env=env, check=check,
                          text=True, capture_output=capture)


def git(source, *args, capture=False, check=True):
    return run(["git", "-c", "user.name=model-guard", "-c", "user.email=model-guard@localhost",
                "-C", source, *args], capture=capture, check=check)


def release():
    return json.loads((HERE / "release.json").read_text())


def plugin_version():
    for line in (PLUGIN / "model_guard/__init__.py").read_text().splitlines():
        if line.startswith("__version__"):
            return line.split("=", 1)[1].strip().strip("\"'")
    raise RuntimeError("model_guard/__init__.py names no version")


def bump_minor(version):
    major, minor, _ = version.split(".")
    return f"{major}.{int(minor) + 1}.0"


def prepare(args):
    pinned = release()
    work = args.work_dir.expanduser().absolute()
    source = work / "source"
    source.mkdir(parents=True, exist_ok=False)
    git(source, "init", "--quiet")
    git(source, "fetch", "--quiet", "--depth=1", UPSTREAM, f"refs/tags/{args.tag}:refs/tags/{args.tag}")
    commit = git(source, "rev-parse", "FETCH_HEAD^{commit}", capture=True).stdout.strip()
    git(source, "checkout", "--quiet", "--detach", commit)
    # The pinned tag supplies the base blobs a three-way merge needs.
    git(source, "fetch", "--quiet", "--depth=1", UPSTREAM, f"refs/tags/{pinned['upstream_ref']}:refs/tags/{pinned['upstream_ref']}")
    for name in SOURCE_PATCHES:
        applied = git(source, "apply", "--3way", "--whitespace=nowarn", HERE / name, capture=True, check=False)
        conflicts = [line.split()[-1] for line in git(source, "status", "--porcelain", capture=True).stdout.splitlines()
                     if line.startswith(("UU", "AA", "DU", "UD"))]
        if applied.returncode != 0 and not conflicts:
            sys.stderr.write(applied.stderr)
            raise RuntimeError(f"{name} could not be applied to {args.tag}")
        if conflicts:
            print(f"{name} conflicts on {args.tag}; resolve and commit it as \"{name}\" in {source}, then run finish:")
            for path in conflicts:
                print(f"  {path}")
            print(f"CONFLICTS {name} " + " ".join(conflicts))
            return 2
        git(source, "add", "-A")
        git(source, "commit", "--quiet", "-m", name)
        print(f"{name}: applied")
    print(source)
    return 0


def stage_commits(source):
    """Map patch names to the commits that carry them, in chain order."""
    log = git(source, "log", "--reverse", "--format=%H %s", capture=True).stdout.splitlines()
    commits = {}
    for line in log:
        sha, _, subject = line.partition(" ")
        if subject in PATCH_ORDER:
            commits[subject] = sha
    return log[0].split()[0], commits


def regenerate_packaging(source, cargo_env):
    """Apply the packaging patch minus the lockfile, then let Cargo rewrite the lockfile."""
    git(source, "apply", "--3way", "--whitespace=nowarn", "--exclude=codex-rs/Cargo.lock", HERE / PACKAGING_PATCH)
    main = source / "codex-rs/cli/src/main.rs"
    text = main.read_text()
    wanted = 'version = concat!(env!("CARGO_PKG_VERSION"), "+model-guard.", env!("MODEL_GUARD_VERSION")),'
    text = re.sub(r'version = concat!\(env!\("CARGO_PKG_VERSION"\), "\+model-guard\.[^)]*\),', wanted, text)
    if wanted not in text:
        raise RuntimeError("The packaging patch did not reach the CLI version string")
    main.write_text(text)
    run(["cargo", "update", "--workspace"], cwd=source / "codex-rs", env=cargo_env)
    git(source, "add", "-A")
    git(source, "commit", "--quiet", "-m", PACKAGING_PATCH)


def regenerate_schemas(source, cargo_env):
    for experimental in ("0", "1"):
        env = dict(cargo_env, CODEX_APP_SERVER_SCHEMA_ROOT=str(source / "codex-rs/app-server-protocol/schema"),
                   CODEX_APP_SERVER_SCHEMA_EXPERIMENTAL=experimental)
        run(["cargo", "test", "--locked", "-p", "codex-app-server-protocol", "--lib",
             "write_schema_fixtures_from_env", "--", "--ignored"], cwd=source / "codex-rs", env=env)
    if git(source, "status", "--porcelain", capture=True).stdout.strip():
        git(source, "add", "-A")
        git(source, "commit", "--quiet", "-m", SCHEMAS_PATCH)
    else:
        git(source, "commit", "--quiet", "--allow-empty", "-m", SCHEMAS_PATCH)


def write_patches(source):
    _, commits = stage_commits(source)
    missing = [name for name in PATCH_ORDER if name not in commits]
    if missing:
        raise RuntimeError("The source tree lacks commits for: " + ", ".join(missing))
    for name, sha in commits.items():
        (HERE / name).write_text(git(source, "diff", "--binary", f"{sha}~1", sha, capture=True).stdout)


def replace_in(path, pairs):
    file = REPO / path
    text = file.read_text()
    for old, new in pairs:
        text = text.replace(old, new)
    file.write_text(text)


def finish(args):
    source = args.source.expanduser().absolute()
    official = args.official_package.expanduser().absolute()
    previous = release()
    upstream_commit, _ = stage_commits(source)
    toolchain = tomllib.loads((source / "codex-rs/rust-toolchain.toml").read_text())["toolchain"]["channel"]
    tag = git(source, "describe", "--tags", "--exact-match", upstream_commit, capture=True, check=False).stdout.strip() or args.tag
    if not tag:
        raise RuntimeError("Pass --tag: the upstream commit carries no tag in this clone")
    codex_version = tag.removeprefix("rust-v")
    cargo_env = dict(os.environ, CARGO_TARGET_DIR=str(args.target_dir or source / "codex-rs/target"),
                     RUSTUP_TOOLCHAIN=toolchain)
    regenerate_packaging(source, cargo_env)
    regenerate_schemas(source, cargo_env)
    write_patches(source)

    old_version = plugin_version()
    new_version = args.version or bump_minor(old_version)
    current = dict(previous)
    current.update({
        "plugin_version": new_version, "codex_version": codex_version, "upstream_ref": tag,
        "upstream_commit": upstream_commit, "rust_version": toolchain,
        "patches": [{"file": name, "sha256": digest(HERE / name)} for name in PATCH_ORDER],
        "helper_sha256": {relative: digest(official / relative) for relative in HELPERS},
        "sha256": "", "binary_sha256": "",
        "url": f"https://github.com/ventusff/cli-model-guard/releases/download/v{new_version}/model-guard-codex-linux-x86_64.tar.gz",
    })
    (HERE / "release.json").write_text(json.dumps(current, indent=2) + "\n")

    for path, (old, new) in VERSION_FILES.items():
        replace_in(path, [(old.format(old=old_version), new.format(new=new_version))])
    prose = [(previous["codex_version"], codex_version), (previous["upstream_commit"], upstream_commit),
             (f"rust:{previous['rust_version']}-bookworm", f"rust:{toolchain}-bookworm"),
             (f"Rust {previous['rust_version']}", f"Rust {toolchain}")]
    for path in VERSIONED_DOCS:
        if (REPO / path).is_file():
            replace_in(path, prose)
    changelog = REPO / "CHANGELOG.md"
    entry = (f"## {new_version} — {args.date}\n\n"
             f"- **Codex: native build moved to official Codex {codex_version}** (`{upstream_commit}`): "
             f"the source patches are rebased and the package ships that release's helpers.\n\n")
    changelog.write_text(changelog.read_text().replace("# Changelog\n\n", "# Changelog\n\n" + entry, 1))
    print(f"{old_version} -> {new_version} on Codex {codex_version} ({upstream_commit}), Rust {toolchain}")
    return 0


def patches(args):
    """Write the six patches from the tree's commits and refresh their digests."""
    source = args.source.expanduser().absolute()
    write_patches(source)
    current = release()
    current["patches"] = [{"file": name, "sha256": digest(HERE / name)} for name in PATCH_ORDER]
    current["sha256"], current["binary_sha256"] = "", ""
    (HERE / "release.json").write_text(json.dumps(current, indent=2) + "\n")
    print("\n".join(f"{entry['file']} {entry['sha256'][:12]}" for entry in current["patches"]))
    return 0


def record(args):
    built = json.loads(Path(args.record).read_text())
    current = release()
    if [entry["sha256"] for entry in built["patches"]] != [entry["sha256"] for entry in current["patches"]]:
        raise RuntimeError("The build record was made from other patches than release.json pins")
    current["sha256"], current["binary_sha256"] = built["package_sha256"], built["binary_sha256"]
    (HERE / "release.json").write_text(json.dumps(current, indent=2) + "\n")
    print(f"package {current['sha256']}\nbinary {current['binary_sha256']}")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("prepare", help="Fetch a tag and apply the source patches, one commit each")
    p.add_argument("--tag", required=True, help="Official Codex tag, e.g. rust-v0.158.0")
    p.add_argument("--work-dir", type=Path, required=True, help="New directory; the tree is created in its `source`")
    p.set_defaults(handler=prepare)
    f = commands.add_parser("finish", help="Regenerate lockfile and schemas, write the patches, pin the release, bump the version")
    f.add_argument("--source", type=Path, required=True, help="Tree with one commit per source patch")
    f.add_argument("--official-package", type=Path, required=True, help="Official standalone package of the same tag")
    f.add_argument("--tag", help="Tag of the upstream commit when the clone carries no tag name")
    f.add_argument("--version", help="Plugin version to release; default bumps the minor version")
    f.add_argument("--date", default=__import__("datetime").date.today().isoformat())
    f.add_argument("--target-dir", type=Path, help="Cargo target directory for the regeneration builds")
    f.set_defaults(handler=finish)
    w = commands.add_parser("patches", help="Rewrite the six patches from a finished tree (after resolving by hand) and refresh their digests")
    w.add_argument("--source", type=Path, required=True)
    w.set_defaults(handler=patches)
    r = commands.add_parser("record", help="Copy the built package's digests into release.json")
    r.add_argument("--record", type=Path, required=True, help="build-record.json written by build.py")
    r.set_defaults(handler=record)
    args = parser.parse_args()
    try:
        return args.handler(args)
    except (RuntimeError, subprocess.CalledProcessError, OSError) as exc:
        print(f"rebase: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
