#!/usr/bin/env python3
"""Generate update_manifest.json for the in-app updater (v4.5.5.1+).

The in-app "Download update now" button syncs the whole RUNTIME file set a
release needs (not just the main .py) -- the old updater pulled only
'ParaKit v4.0.py', which broke any multi-file update. This lists every
dependency file with its sha256 so the updater downloads the ones that are
missing/changed and skips the rest (so the big models aren't re-fetched every
time).

The main 'ParaKit v4.0.py' is NOT in the manifest -- the updater handles it
specially (version-validated). Static assets (icons, the *-Web Edition HTML,
docs, screenshots) are excluded: they don't gate the app at runtime and are
large / rarely-changing.

IMPORTANT: run this against the repo whose files will be ON GITHUB (the PUBLIC
ParaKit-Open_Source repo), AFTER all release files are in place -- the hashes
must match what the updater downloads.

  py -3.12 tools/gen_update_manifest.py [REPO_ROOT]     # default: current dir
Writes <REPO_ROOT>/update_manifest.json.
"""
import hashlib
import json
import os
import re
import sys

# Loose runtime files (relative to repo root) the updater keeps current.
LOOSE_FILES = ["CHANGELOG.txt", "requirements.txt", "rlrr_parse.py",
               "parakit_drum_model.onnx"]

# (dir, allowed-extensions) walked recursively. parakit_separators = code only
# (its model weights are downloaded on demand, not shipped in the repo).
DIRS = [
    ("parakit_cleanup", (".py", ".npz", ".json")),
    ("parakit_separators", (".py",)),
]

EXCLUDE_DIRNAMES = {"__pycache__"}
EXCLUDE_SUFFIX = (".prev", ".dl.tmp", ".pyc")
EXCLUDE_SUBSTR = ("_backup", ".backup", "40feat_backup")


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _skip(name):
    low = name.lower()
    return low.endswith(EXCLUDE_SUFFIX) or any(s in low for s in EXCLUDE_SUBSTR)


def main():
    root = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
    ver = "?"
    try:
        src = open(os.path.join(root, "ParaKit v4.0.py"), encoding="utf-8").read()
        m = re.search(r'VERSION\s*=\s*"([\d.]+)"', src)
        if m:
            ver = m.group(1)
    except Exception:
        pass

    files = []
    for rel in LOOSE_FILES:
        p = os.path.join(root, rel)
        if os.path.isfile(p) and not _skip(rel):
            files.append((rel.replace("\\", "/"), _sha256(p)))

    for d, exts in DIRS:
        base = os.path.join(root, d)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [x for x in dirnames if x not in EXCLUDE_DIRNAMES]
            for fn in filenames:
                if not fn.lower().endswith(exts) or _skip(fn):
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, root).replace("\\", "/")
                files.append((rel, _sha256(full)))

    files.sort()
    manifest = {
        "version": ver,
        "note": ("Runtime files the in-app updater keeps current (main "
                 "'ParaKit v4.0.py' handled separately). Regenerate with "
                 "tools/gen_update_manifest.py from the PUBLIC repo after any release."),
        "files": [{"path": rel, "sha256": h} for rel, h in files],
    }
    out = os.path.join(root, "update_manifest.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    print(f"wrote {out}: v{ver}, {len(files)} files")
    for rel, _h in files:
        print("  ", rel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
