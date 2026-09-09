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
    # Timing-drift analysis against the drum stem (v4.12.0). Imported lazily,
    # inside a try/except, so a missing copy does NOT crash the app -- the
    # check just reports "unknown" forever. That is exactly why it belongs
    # here: without the entry the feature ships as code the updater never
    # delivers, and it fails silently rather than loudly.
    "parakit_onset_align.py",
    # Song Tester tab (v4.13.0): the tab moved out of the app into two sidecars,
    # the same way the Spectral tab did. Without these two lines 4.13.0 would
    # have shipped a tab the updater never delivers -- the 4.12.0 failure shape
    # again, and the drift check cannot see a file the manifest does not list.
    "parakit_song_tester_tab.py", "parakit_song_tester_widgets.py",
]

# App-content dirs walked recursively -- ALL files inside (minus the excludes
# below). screenshots/ and tools/ are intentionally ABSENT (owner: screenshots not
# needed; tools = dev-only). Add a new dir here the moment a release ships one.
CONTENT_DIRS = [
    "icons",   # Fluent chrome icons (F-FLUENT-ICON-ADOPTION); app degrades to text without them
    # The two sprite folders the progress bars read (_NEON_ASSET_DIR and
    # _SNARE_BAR_ASSET_DIR). Neither shipped before 2026-09-04. Measured on the
    # 4.13.1 tree as published: the snare bar constructed with ZERO canvas items and
    # stayed empty, silently, for the life of every public install -- start() and the
    # animation tick raise nothing, so eleven bars across nine tabs showed a blank strip where the
    # animation belongs and nothing ever said so. Only the 4.13.0 Song Tester, whose
    # bar is determinate and painted at build, reached the paths that raise, and it
    # failed loudly enough to find this. (An earlier version of this comment claimed
    # the neon bar 'fell back to the root dot_*.png files'. It does not: it raises
    # FileNotFoundError in its constructor. That never reached a user because nothing
    # in the shipping app constructs NeonDotProgressBar at all.)
    # Only these two, not the whole assets/ tree (the rest is design source).
    "assets/progressbar",
    "assets/ui/progress_bars",
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
    # Folder Batch starter presets the app reads next to itself (ParaKit v4.0.py,
    # _batch_folder_default_template_dir) and names in its own help text. Found
    # missing from every public release through 4.13.3 on 2026-09-05: nothing
    # compared what the code reads against this list. The stager copies the three
    # files by name; this entry puts them in the manifest so the updater delivers
    # them. Guard: _breaker/templates_packaging_regress.py.
    "templates",
]

EXCLUDE_DIRNAMES = {"__pycache__", ".git"}
EXCLUDE_SUFFIX = (".prev", ".dl.tmp", ".tmp", ".pyc", ".bak", ".orig")
EXCLUDE_SUBSTR = ("_backup", ".backup", "40feat_backup")

# ── DOCUMENTATION vs RUNTIME ────────────────────────────────────────────────
# Entries flagged "doc": true are prose whose bytes cannot change what the app
# DOES, so a hash mismatch on one is cosmetic and must not hold the update
# hostage. The updater skips those and installs the rest; everything unflagged
# stays strictly fatal. The flag is emitted HERE, by the tool that already knows
# what it is shipping, because the app used to answer this with its own basename
# list and that list covered 8 of the 16 prose entries -- docs/TROUBLESHOOTING.md,
# docs/BUILDING.md, docs/ROADMAP.md and the three research reports would still
# have aborted every user's update.
#
# ⚠ "cannot change what the app DOES" is the honest bar, and it is deliberately
# weaker than "the app never opens it". CHANGELOG.txt IS read: it is the single
# source of truth behind the Help tab's What's New. Skipping a drifted changelog
# means a user can land on the new version and read the OLD release notes. That
# is a real cost and it is accepted on purpose -- the alternative is the bug this
# whole mechanism exists to kill, where the same drift blocks the update
# entirely and they get neither the notes nor the app. Stale notes beat no
# update. Do not "fix" this by making CHANGELOG.txt fatal again without
# re-reading that trade.
#
# `.md` needs no enumeration: nothing in the app or its sidecars opens a .md.
# Every .md occurrence in the tree is a comment or a docstring reference
# (verified 2026-08-24), so the extension alone is a sound rule and a NEW .md
# added anywhere is covered automatically -- which is the whole point, since a
# hand-maintained list is what drifted.
#
# `.txt` DOES need enumeration and is an ALLOWLIST on purpose. `requirements.txt`
# is the install input and must keep a fatal check; a ".txt = prose" suffix rule
# would silently disarm it. Allowlisting means an unlisted file stays FATAL, so
# being wrong breaks an update loudly instead of opening a silent integrity hole
# -- the safe direction to be wrong in.
#
# `icons/_MANIFEST.txt` is deliberately NOT listed. It qualifies as prose (it is
# icon-license provenance, written by tools/gen_fluent_icons.py and read by
# nothing), but it is machine-generated and nobody hand-edits it on the website,
# which is the situation this flag exists for. Considered and excluded, not missed.
DOC_TXT_BASENAMES = {"readme.txt", "changelog.txt"}

# Documentation that is neither .md nor .txt, named by FULL relative path because
# the extension says nothing useful about these two. Reviewed 2026-08-24:
#   LICENSE               -- legal prose, no suffix at all. Year bumps are an
#                            annual near-certainty and are exactly the kind of
#                            one-line edit made on the website without a regen.
#   docs/SYSTEM_CHART.html -- a rendered documentation page.
# The other five shipped .html files are deliberately ABSENT: the Web Edition
# pages are applications, not documents, and an ".html = prose" rule would strip
# the integrity check from running code. That is why this is a path list and not
# a suffix.
DOC_EXACT_PATHS = {"license", "docs/system_chart.html"}


def _is_doc(rel):
    """True if this shipping file is documentation. See the block above."""
    q = rel.replace("\\", "/").lower()
    base = q.rsplit("/", 1)[-1]
    return (base.endswith(".md") or base in DOC_TXT_BASENAMES
            or q in DOC_EXACT_PATHS)


def _entry(rel, h):
    """Build one manifest entry.

    Factored out of main() SO THAT A GUARD CAN CALL IT. Nothing used to test that
    the flag is actually EMITTED: both acceptance probes exercised `_is_doc` and
    then built their own entries, so deleting the emission here would have left
    every invariant, every needle and both probes green while every shipped entry
    went unflagged and the app silently reverted to aborting on a prose edit.

    The value must be the BOOLEAN True. The app tests `is not True`, so emitting
    the string "true" or the integer 1 disables the feature just as completely as
    emitting nothing, and reads correct in the JSON."""
    ent = {"path": rel, "sha256": h}
    if _is_doc(rel):
        ent["doc"] = True
    return ent


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


def _ignore_query_note(reason):
    """Every failure of the ignore query is said out loud: the name-based excludes
    still apply, but nobody should mistake a failed query for 'nothing ignored'."""
    if True:  # generator discloses ignore-query failure
        print("   (git ignore query unavailable, %s: only the name-based excludes "
              "apply)" % reason)


def _gitignored_set(root):
    """Relative paths (forward-slash) git IGNORES under `root`. The CONTENT_DIRS
    walk below did NOT consult .gitignore (breaker 2026-07-23, codex), so a
    git-ignored file — e.g. `parakit_cleanup/test_bleed.py` via `.gitignore`'s
    `test_*.py` — would enter the manifest and be DOWNLOADED to public users.
    Empty set when `root` is not a git work tree, git cannot be launched, or the
    query times out (then only the name-based excludes apply; every one of those
    prints a note naming the reason, so the fallback is visible).
    The query strips inherited GIT_* selectors, suppresses global/system config
    (a global core.excludesFile must not decide what ships), and pins
    --work-tree to `root` so a repository-local core.worktree cannot redirect
    it: membership is defined by this checkout's own ignore rules."""
    try:
        import subprocess
        if True:  # generator pins and isolates the ignore query
            env = {k: v for k, v in os.environ.items()
                   if not k.upper().startswith("GIT_")}
            env["GIT_CONFIG_NOSYSTEM"] = "1"
            env["GIT_CONFIG_GLOBAL"] = os.devnull
            env["GIT_CONFIG_SYSTEM"] = os.devnull
            cmd = ["git", "-C", root, "--work-tree=" + os.path.abspath(root),
                   "ls-files", "--others", "--ignored", "--exclude-standard", "-z"]
        else:
            env = None
            cmd = ["git", "-C", root, "ls-files", "--others", "--ignored",
                   "--exclude-standard", "-z"]
        out = subprocess.run(cmd, capture_output=True, timeout=30, env=env)
        if out.returncode != 0:
            _ignore_query_note("rc %d" % out.returncode)
            return set()
        return {p.replace("\\", "/")
                for p in out.stdout.decode("utf-8", "replace").split("\0") if p}
    except Exception as exc:
        _ignore_query_note("%s: %s" % (type(exc).__name__, exc))
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
                 "ARE synced (What's New + version line). Entries marked "
                 "\"doc\": true are prose -- a hash mismatch on one is skipped by "
                 "the updater instead of aborting the whole update; everything "
                 "else stays fatal. Regenerate with "
                 "tools/gen_update_manifest.py from the PUBLIC repo after any release."),
        "files": [_entry(rel, h) for rel, h in files],
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
