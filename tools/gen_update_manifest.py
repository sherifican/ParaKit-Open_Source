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

  py -3.12 tools/gen_update_manifest.py [REPO_ROOT] [--allow-missing]   # default root: current dir
Writes <REPO_ROOT>/update_manifest.json. If any whitelisted ROOT_FILES entry or
CONTENT_DIRS dir (or the VERSION constant) is missing, the tool prints the list
and exits 2 WITHOUT writing; pass --allow-missing to write anyway.
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
    "parakit_repo_banner.svg",
    "dot_lit_v2.png", "dot_lit_v3_tight.png", "dot_unlit.png",
    # Tab sidecars imported by 'ParaKit v4.0.py' at runtime -- must ship next
    # to the main .py or the tab fails to load. The Spectral tab (v4.8.0) and
    # the native Preview + Practice tab replacements (v4.9.x) each ship as
    # root-level sidecars; add any new one here the moment it is imported.
    # Spectral Comparison tab (v4.8.0):
    "parakit_spectral_tab.py", "parakit_spectral_engine.py",
    # Preview tab (native TTK replacement, v4.9.x):
    "parakit_preview_tab.py", "parakit_preview_engine.py",
    "parakit_preview_sprites.py",
    # Practice tab (native TTK replacement, v4.9.x):
    "parakit_practice_tab.py", "parakit_practice_home.py",
    "parakit_practice_engine.py", "parakit_practice_widgets.py",
    "parakit_practice_sprites.py",
    # Shared drum-synth voices (Preview + Practice synth toggle, v4.9.x):
    "parakit_synth_voices.py",
]

# App-content dirs walked recursively -- ALL files inside (minus the excludes
# below). screenshots/ and tools/ are intentionally ABSENT (owner: screenshots not
# needed; tools = dev-only). Add a new dir here the moment a release ships one.
CONTENT_DIRS = [
    "icons",   # Fluent chrome icons (F-FLUENT-ICON-ADOPTION); app degrades to text without them
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


def _gitignored_set(root):
    """Relative paths (forward-slash) git IGNORES under `root`. The CONTENT_DIRS
    walk below did NOT consult .gitignore (breaker 2026-07-23, codex), so a
    git-ignored file — e.g. `parakit_cleanup/test_bleed.py` via `.gitignore`'s
    `test_*.py` — would enter the manifest and be DOWNLOADED to public users.
    Empty set when `root` is not a git work tree / git is unavailable (then only
    the name-based excludes apply)."""
    try:
        import subprocess
        out = subprocess.run(
            ["git", "-C", root, "ls-files", "--others", "--ignored",
             "--exclude-standard", "-z"],
            capture_output=True, timeout=30)
        if out.returncode != 0:
            return set()
        return {p.replace("\\", "/")
                for p in out.stdout.decode("utf-8", "replace").split("\0") if p}
    except Exception:
        return set()


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    root = os.path.abspath(args[0] if args else ".")
    missing = []
    ver = "?"
    try:
        with open(os.path.join(root, "ParaKit v4.0.py"), encoding="utf-8") as f:
            src = f.read()
        m = re.search(r'VERSION\s*=\s*"([\d.]+)"', src)
        if m:
            ver = m.group(1)
    except Exception:
        pass
    if ver == "?":
        missing.append("VERSION constant (ParaKit v4.0.py unreadable or pattern mismatch)")

    # --- release surface: the README version table must cover THIS release --------
    #
    # WHY THIS LIVES HERE. A release touches several user-facing surfaces —
    # CHANGELOG.txt, both "Version in this release" lines, and the README's
    # version-history table. 4.9.15 and 4.9.16 updated the first two and silently
    # skipped the third, so for two consecutive releases the changelog was correct
    # and live while the first thing anyone reads on GitHub still ended at v4.9.14.
    # Nothing caught it; it was found by eye, days later.
    #
    # This generator is the right place to catch it because it is the one step
    # documented to run FROM THE PUBLIC REPO after every release, so it is the only
    # tool that sees the shipped README and the shipped VERSION at the same moment.
    # (The _breaker invariant suite cannot do this job: it runs against the DEV tree,
    # whose README.md is a different, independently-maintained file — currently
    # topping out at a different version entirely — and pointing an invariant at an
    # absolute path outside the app tree reddens the mutation sandbox and blames the
    # app for it. That trap has been hit four times.)
    #
    # Appended to `missing`, so it reuses the existing refuse-to-write behaviour and
    # the existing --allow-missing escape hatch rather than inventing a second one.
    try:
        with open(os.path.join(root, "README.md"), encoding="utf-8") as f:
            _readme = f.read()
        # Rows are `| **vX.Y.Z**<br>date | ... |`, newest first. The bold markers vary
        # (v4.9.9 is ***italic-bold*** because it was staged and never released), so
        # the count of asterisks is not fixed.
        _rows = re.findall(r"^\|\s*\*+v([0-9][0-9.]*)\*+", _readme, re.M)
        if not _rows:
            missing.append(
                "README.md version-history table: no rows matched — the table was "
                "moved, renamed, or reformatted, so this check is no longer looking "
                "at anything")
        elif ver != "?" and _rows[0] != ver:
            missing.append(
                "README.md version table is stale: newest row is v%s but this "
                "release is v%s — add the row before shipping" % (_rows[0], ver))
    except OSError as _e:
        missing.append("README.md unreadable for the version-table check (%s)"
                       % type(_e).__name__)

    ignored = _gitignored_set(root)
    skipped_ignored = []

    files = []
    for rel in ROOT_FILES:
        p = os.path.join(root, rel)
        rel_fs = rel.replace("\\", "/")
        if rel_fs in ignored:
            # A whitelisted root file that git ignores is a config error — surface
            # it loudly rather than silently ship (or silently drop) it.
            missing.append(f"root file is .gitignore'd (will NOT ship): {rel}")
            continue
        if os.path.isfile(p) and not _skip(rel):
            files.append((rel_fs, _sha256(p)))
        else:
            missing.append(f"root file: {rel}")

    for d in CONTENT_DIRS:
        base = os.path.join(root, d)
        if not os.path.isdir(base):
            missing.append(f"content dir: {d}/")
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [x for x in dirnames if x not in EXCLUDE_DIRNAMES]
            for fn in filenames:
                if _skip(fn):
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, root).replace("\\", "/")
                if rel in ignored:
                    skipped_ignored.append(rel)   # .gitignore'd -> never manifest/ship
                    continue
                files.append((rel, _sha256(full)))

    files.sort()
    if skipped_ignored:
        print("   (skipped %d .gitignore'd file(s) that would otherwise ship: %s)"
              % (len(skipped_ignored), ", ".join(sorted(set(skipped_ignored))[:6])))

    allow = "--allow-missing" in sys.argv
    if missing:
        print("!! MANIFEST INCOMPLETE — the following whitelist entries were NOT found:")
        for m in missing:
            print("   -", m)
        if not allow:
            print("!! Refusing to write update_manifest.json. Fix the release tree,")
            print("!! or re-run with --allow-missing if the omission is intentional.")
            return 2
        print("!! --allow-missing given: writing the manifest WITHOUT them.")

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
