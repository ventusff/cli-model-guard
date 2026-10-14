#!/usr/bin/env python3
"""Fail a release on clippy diagnostics the patches introduce.

Both arguments are `cargo clippy --message-format=json` streams of the same
packages: the first from the official source with only the regenerated
lockfile, the second from the patched source. A warning or error that the
patched source has more often than the official one fails the gate. A
diagnostic both sources share is upstream's own and is listed, not fatal.

A diagnostic is identified by its lint, its file and the text of the line it
points at, so the patches moving upstream's lines does not change it.
"""
from collections import Counter
import json
import sys


def scan(path):
    """The stream's diagnostics as a multiset, and whether the build finished cleanly."""
    found = Counter()
    rendered = {}
    finished = None
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("reason") == "build-finished":
            finished = entry.get("success")
        if entry.get("reason") != "compiler-message":
            continue
        message = entry["message"]
        if message.get("level") not in ("warning", "error"):
            continue
        primary = next((span for span in message.get("spans", []) if span.get("is_primary")), None)
        if primary is None:
            continue
        key = (
            (message.get("code") or {}).get("code") or message.get("message", ""),
            primary["file_name"],
            "".join(part["text"] for part in primary.get("text", [])).strip(),
        )
        found[key] += 1
        rendered.setdefault(key, message.get("rendered") or message.get("message", ""))
    return found, rendered, finished


def main():
    if len(sys.argv) != 3:
        sys.exit(f"usage: {sys.argv[0]} BASELINE.json PATCHED.json")
    baseline, _, baseline_finished = scan(sys.argv[1])
    patched, rendered, patched_finished = scan(sys.argv[2])
    if patched_finished is None:
        sys.exit("clippy gate: the patched build did not finish; see the cargo output above")
    introduced = patched - baseline
    shared = patched & baseline
    for key in shared:
        print(f"clippy gate: upstream's own {key[0]} in {key[1]} (not fatal)")
    for key in introduced:
        print(rendered[key], end="" if rendered[key].endswith("\n") else "\n")
    if introduced:
        sys.exit(f"clippy gate: the patches introduce {sum(introduced.values())} clippy diagnostic(s)")
    if not patched_finished and baseline_finished:
        sys.exit("clippy gate: the patched build failed where the official source builds")
    print(f"clippy gate: no diagnostics introduced ({sum(shared.values())} upstream)")


if __name__ == "__main__":
    main()
