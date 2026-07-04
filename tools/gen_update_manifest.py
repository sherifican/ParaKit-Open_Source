#!/usr/bin/env python3
"""Generate update_manifest.json for the in-app updater (v4.5.5.1+).

The in-app "Download update now" / "Update supporting files now" button syncs the
whole runtime file set a release needs -- NOT just the main 'ParaKit v4.0.py' --
so a multi-file update installs correctly without re-cloning. This lists every
supporting file with its sha256 so the updater downloads the ones that are
missing/changed and skips the rest (the big models aren't re-fetched every time).

SCOPE (owner-set): the manifest covers ALL distributed app files EXCEPT --
  - the main 'ParaKit v4.0.py'  (the updater handles it specially, version-validated);
  - screenshots/  (only render the GitHub README -- not needed by the app at runtime);
NOTE: CHANGELOG.txt + README.md + README.txt ARE synced (owner 2026-07-04) --
  CHANGELOG drives the in-app "What's New" and the READMEs carry the "Version in
  this release" line the self-update check reads, so the main update button pulls
  all three (CHANGELOG also still has its standalone Download button as a fallback
  if the user deletes/moves it). All three are listed in ROOT_FILES below.
  - tools/  (dev-only tooling, incl. this generator) and __pycache__/ / *.pyc /
    *.prev / *_backup* build+backup cruft.
It is a WHITELIST of the app-content dirs + named root assets (NOT a walk of the
whole repo) so it stays SAFE to run in either the public repo or the larger dev
working tree (whose root holds many non-app files). *** When a release adds a NEW
top-level content dir or root asset, ADD IT to CONTENT_DIRS / ROOT_FILES below ***
-- otherwise the updater won't sync it (this is the same "keep the release surface
list current" discipline as the README/CHANGELOG surfaces).

IMPORTANT: run this against the repo whose files will be ON GITHUB (the PUBLIC
ParaKit-Open_Source repo), AFTER all release files are in place -- the hashes must
match what the updater downloads.

  py -3.12 tools/gen_update_manifest.py [REPO_ROOT]     # default: current dir
Writes <REPO_ROOT>/update_manifest.json.
"""
import hashlib
import json
import os
import re
import sys

# Named root files the updater keeps current. CHANGELOG.txt + README.md/README.txt
# are here (owner 2026-07-04: CHANGELOG drives the in-app "What's New" + the
# READMEs carry the "Version in this release" line the self-update check reads --
# so the main update button should sync all three). The main 'ParaKit v4.0.py'
# and 'update_manifest.json' are handled specially / can't list themselves.
ROOT_FILES = [
    "requirements.txt", "rlrr_parse.py", "parakit_drum_model.onnx",
    "LICENSE", "Run ParaKit v4.0.bat", "CHANGELOG.txt",
    "README.md", "README.txt",
    "parakit.ico", "parakit_header_logo.png", "parakit_logo_FINAL.png",
    "dot_lit_v2.png", "dot_lit_v3_tight.png", "dot_unlit.png",
]

# App-content dirs walked recursively -- ALL files inside (minus the excludes
# below). screenshots/ and tools/ are intentionally ABSENT (owner: screenshots not
# needed; tools = dev-only). Add a new dir here the moment a release ships one.
CONTENT_DIRS = [
    "parakit_cleanup",
    "parakit_separators",
    "Detection Research Notes - Web Edition",
    "Practice Window v2 - Web Edition",
    "Practice Window v3 - Web Edition",
    "Preview Track v2 - Web Edition",
    "docs",
    "extractor",
    "practice_minigame",
    "practice_v2",
]

EXCLUDE_DIRNAMES = {"__pycache__", ".git"}
EXCLUDE_SUFFIX = (".prev", ".dl.tmp", ".tmp", ".pyc", ".bak", ".orig")
EXCLUDE_SUBSTR = ("_backup", ".backup", "40feat_backup")


def _sha256(path):
    """Hash the bytes the in-app updater will DOWNLOAD from GitHub (the committed
    blob), not the raw working-tree bytes. With core.autocrlf=true a text file is
    CRLF in the Windows working tree but stored + served as LF, so a text file is
    hashed with CRLF->LF normalization (matching git); binaries (any NUL byte) are
    hashed raw. Without this, a CRLF text file's manifest hash mismatches the LF
    download and the updater rejects it (e.g. CHANGELOG.txt)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        head = f.read(8192)
        if b"\x00" in head:                        # binary -> hash raw, streamed
            h.update(head)
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
            return h.hexdigest()
        data = head + f.read()                      # text -> normalize to LF
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


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
    for rel in ROOT_FILES:
        p = os.path.join(root, rel)
        if os.path.isfile(p) and not _skip(rel):
            files.append((rel.replace("\\", "/"), _sha256(p)))

    for d in CONTENT_DIRS:
        base = os.path.join(root, d)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [x for x in dirnames if x not in EXCLUDE_DIRNAMES]
            for fn in filenames:
                if _skip(fn):
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, root).replace("\\", "/")
                files.append((rel, _sha256(full)))

    files.sort()
    manifest = {
        "version": ver,
        "note": ("Runtime files the in-app updater keeps current. The main "
                 "'ParaKit v4.0.py' is handled separately; screenshots/ are "
                 "intentionally excluded. CHANGELOG.txt + README.md + README.txt "
                 "ARE synced (What's New + version line). Regenerate with "
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
