"""Release gate — refuse to publish when the release surfaces disagree.

Audit A5 (Sol, 2026-07-09): the update popup / in-app updater trust FOUR
surfaces that are maintained by hand + one generated file. A release where they
disagree mis-advertises the version (users offered the wrong update) or ships a
manifest describing an older file set. This script verifies they all agree.

Checks, in the PUBLIC repo (default C:/Users/micah/ParaKit-Open_Source, or pass
a path as argv[1]):
  1. `VERSION = "X"`            in ParaKit v4.0.py
  2. "Version in this release:" in README.md  (both occurrences)
  3. "Version in this release:" in README.txt
  4. newest version header      in CHANGELOG.txt (first "vX" between dashed
                                 separator lines)
  5. "version"                  in update_manifest.json

Exit 0 = all agree (prints the version). Exit 1 = mismatch (prints a table).
Run it after gen_update_manifest.py, before `git push`.
"""
import json
import os
import re
import sys

DEFAULT_ROOT = r"C:/Users/micah/ParaKit-Open_Source"


def _read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def collect(root):
    """Return {surface_name: version_string_or_error}."""
    out = {}

    try:
        m = re.search(r'^\s*VERSION\s*=\s*"([\d.]+)"',
                      _read(os.path.join(root, "ParaKit v4.0.py")), re.M)
        out["ParaKit v4.0.py VERSION"] = m.group(1) if m else "(not found)"
    except OSError as e:
        out["ParaKit v4.0.py VERSION"] = f"(unreadable: {e})"

    try:
        hits = re.findall(r"Version in this release:\**\s*`?([\d.]+)",
                          _read(os.path.join(root, "README.md")))
        if not hits:
            out["README.md"] = "(not found)"
        else:
            for i, h in enumerate(hits, 1):
                out[f"README.md #{i}"] = h
    except OSError as e:
        out["README.md"] = f"(unreadable: {e})"

    try:
        m = re.search(r"Version in this release:\s*([\d.]+)",
                      _read(os.path.join(root, "README.txt")))
        out["README.txt"] = m.group(1) if m else "(not found)"
    except OSError as e:
        out["README.txt"] = f"(unreadable: {e})"

    try:
        m = re.search(r"^-{20,}\s*\n\s*v([\d.]+)", _read(
            os.path.join(root, "CHANGELOG.txt")), re.M)
        out["CHANGELOG.txt newest"] = m.group(1) if m else "(not found)"
    except OSError as e:
        out["CHANGELOG.txt newest"] = f"(unreadable: {e})"

    try:
        man = json.loads(_read(os.path.join(root, "update_manifest.json")))
        out["update_manifest.json"] = str(man.get("version", "(no key)"))
    except (OSError, ValueError) as e:
        out["update_manifest.json"] = f"(unreadable: {e})"

    return out


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ROOT
    surfaces = collect(root)
    versions = set(surfaces.values())
    width = max(len(k) for k in surfaces)
    ok = len(versions) == 1 and not next(iter(versions)).startswith("(")
    print(f"Release gate — {root}")
    for k, v in surfaces.items():
        mark = " " if ok or v == max(versions, key=list(surfaces.values()).count) else "!"
        print(f"  {mark} {k:<{width}}  {v}")
    if ok:
        print(f"GATE PASS — all surfaces agree on {versions.pop()}")
        return 0
    print("GATE FAIL — surfaces disagree; fix before pushing.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
