#!/usr/bin/env python3
"""Fail a release on clippy diagnostics the patches introduce.

`clippy_gate.py BASELINE PATCHED` compares two `cargo clippy
--message-format=json` streams of the same packages: BASELINE from the
official source with only the regenerated lockfile, PATCHED from the patched
source. A diagnostic the patched source has more often than the official one
fails the gate; one both share is upstream's own and is listed, not fatal.
The official source must have built, or there is nothing to compare with.
`clippy_gate.py --verify BASELINE` only checks that.

A diagnostic is identified by its level, lint, message, file and the code it
highlights, so the patches moving upstream's lines does not change it. Files
generated under the target directory are named relative to their `out`
directory, which differs between the two builds.
"""
from collections import Counter
import json
import re
import sys

SUMMARY = re.compile(r"^(\d+ warnings? emitted|aborting due to .*)$")
GENERATED = re.compile(r"^.*/build/[^/]+/out/")


def highlighted(span):
    return "".join(line["text"][line["highlight_start"] - 1:line["highlight_end"] - 1]
                   for line in span.get("text", [])).strip()


def scan(path):
    """The stream's diagnostics as a multiset, and whether the build succeeded."""
    found = Counter()
    rendered = {}
    succeeded = None
    with open(path, encoding="utf-8", errors="replace") as stream:
        for line in stream:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("reason") == "build-finished":
                succeeded = bool(entry.get("success"))
            if entry.get("reason") != "compiler-message":
                continue
            message = entry["message"]
            level = message.get("level")
            text = message.get("message", "")
            code = (message.get("code") or {}).get("code", "")
            if level not in ("warning", "error") or (not code and SUMMARY.match(text)):
                continue
            primary = next((span for span in message.get("spans", []) if span.get("is_primary")), None)
            key = (level, code, text,
                   GENERATED.sub("OUT_DIR/", primary["file_name"]) if primary else "",
                   highlighted(primary) if primary else "")
            found[key] += 1
            rendered.setdefault(key, message.get("rendered") or f"{level}: {text}\n")
    return found, rendered, succeeded


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--verify":
        if scan(sys.argv[2])[2] is not True:
            sys.exit("clippy gate: clippy did not succeed on the official source")
        return
    if len(sys.argv) != 3:
        sys.exit(f"usage: {sys.argv[0]} BASELINE PATCHED | --verify BASELINE")
    baseline, _, baseline_succeeded = scan(sys.argv[1])
    patched, rendered, patched_succeeded = scan(sys.argv[2])
    if baseline_succeeded is not True:
        sys.exit("clippy gate: clippy did not succeed on the official source; there is nothing to compare with")
    introduced = patched - baseline
    shared = patched & baseline
    for key in shared:
        print(f"clippy gate: upstream's own {key[1] or key[2]} in {key[3]} (not fatal)")
    for key in introduced:
        print(rendered[key], end="" if rendered[key].endswith("\n") else "\n")
    if introduced:
        sys.exit(f"clippy gate: the patches introduce {sum(introduced.values())} clippy diagnostic(s)")
    if patched_succeeded is not True:
        sys.exit("clippy gate: clippy did not succeed on the patched source; see the cargo output above")
    print(f"clippy gate: no diagnostics introduced ({sum(shared.values())} upstream)")


if __name__ == "__main__":
    main()
