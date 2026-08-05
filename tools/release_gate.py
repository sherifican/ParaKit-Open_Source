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

  6. no ParaKit memory store has uncommitted work (discovered, not hardcoded;
     skipped cleanly when no store exists)

Exit 0 = all checks pass. Exit 1 = a mismatch or a dirty memory store.
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



def check_memory_stores():
    """Warn-then-FAIL if a ParaKit memory store has uncommitted work.

    Added 2026-07-31 after the auto-memory store was found **35 files behind** — real
    rules, all valid, all already referenced in MEMORY.md, none ever committed. They
    were live and load-bearing and one accidental delete from being gone with no
    history. Nothing was watching that store because it rides no repo's normal flow:
    repo `memory/` gets committed with ParaKit, the auto store only when someone
    remembers. Release is the natural moment to flush it — clearing this is one commit.

    DISCOVERED, never hardcoded: globs `~/.claude/projects/*ParaKit*/memory` plus the
    dev repo's own `memory/`. No username is embedded, so this file stays safe in the
    public repo, and a machine with no such store SKIPS cleanly rather than failing —
    which is why it can live in a tool other people can run.
    """
    import subprocess
    from pathlib import Path

    # NOTE: deliberately NOT wrapped in a broad try/except. A check that swallows its
    # own breakage and returns 0 is a green light wired to nothing — the exact failure
    # INV49 shipped with earlier today. If this crashes, the gate should crash.
    stores = sorted(Path.home().glob("*.claude/projects/*ParaKit*/memory"))
    dev_mem = Path(__file__).resolve().parent.parent / "memory"
    if dev_mem.is_dir():
        stores.append(dev_mem)

    checked, dirty = 0, []
    for st in stores:
        try:
            top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=st,
                                 capture_output=True, text=True)
            if top.returncode:
                continue                      # not version-controlled; not our business
            # `-- .` scopes the status to THIS directory. Without it, a store that is a
            # subdirectory of a bigger repo (the dev tree's own memory/) reports the
            # WHOLE repo's status — which read as "726 uncommitted files" on the first
            # run and would have failed every release for reasons nothing to do with
            # memory. The question is "is anything under this store uncommitted", not
            # "is the repo containing it clean".
            r = subprocess.run(["git", "status", "--porcelain", "--", "."], cwd=st,
                               capture_output=True, text=True)
            if r.returncode:
                continue
            checked += 1
            n = len([l for l in r.stdout.splitlines() if l.strip()])
            if n:
                dirty.append((st, n))
        except OSError:
            continue

    if not checked:
        print("  - memory stores            (none found — skipped)")
        return 0
    if dirty:
        for st, n in dirty:
            print(f"  ! memory store DIRTY       {n} uncommitted file(s)  {st}")
        print("GATE FAIL — commit the memory store(s) above before pushing. "
              "An unversioned rule is one delete from gone.")
        return 1
    print(f"  - memory stores            {checked} checked, all clean")
    return 0


def check_index_matches_manifest(root):
    """FAIL if the bytes git would COMMIT are not the bytes the manifest describes.

    Added 2026-08-04, because the 4.9.10 staging passed this gate while staged to ship a
    broken update. `rlrr_parse.py` had been fixed on disk and `update_manifest.json`
    regenerated from it -- and neither was ever `git add`-ed. The index therefore held
    the PRE-fix file in both locations while the manifest advertised the POST-fix hash.
    Commit that and the repo serves old bytes under a new hash: the updater downloads
    them, the post-download hash verify fails, and every updating user is told their
    install is DAMAGED by a release that looked green at every stage.

    It got through because every other check in this file measures the WORKING TREE.
    `collect()` reads files from disk; gen_update_manifest hashes files from disk; the
    e2e rehearsal builds its fixtures from `git write-tree`, which is the index, but it
    runs against whatever was staged at the time. Nothing compared disk to index, so the
    bytes that actually get committed were examined by nothing automated. It was caught
    by hand, on a second look, because a byte count seemed off.

    THE HASHER MATTERS. gen_update_manifest normalises CRLF->LF before hashing
    (tools/gen_update_manifest.py `_sha256`, mirrored in the app) so the manifest matches
    what GitHub serves from an LF blob. A raw byte compare here reports every CRLF
    working-tree file as broken and buries the two that genuinely are -- that exact wrong
    answer was produced twice while diagnosing this. So this imports the generator's own
    `_sha256` rather than reimplementing it, and self-tests that it is EOL-insensitive AND
    still content-sensitive before trusting a single verdict from it.

    SKIPS CLEANLY outside a git checkout, so the tool stays runnable by anyone.
    """
    import subprocess
    import importlib.util
    import tempfile
    from pathlib import Path

    root = Path(root)
    man_path = root / "update_manifest.json"
    gen_path = root / "tools" / "gen_update_manifest.py"
    if not man_path.is_file() or not gen_path.is_file():
        print("  - index vs manifest      SKIPPED (no manifest/generator here)")
        return 0

    def git(*a):
        return subprocess.run(("git", "-C", str(root)) + a,
                              capture_output=True)

    if git("rev-parse", "--git-dir").returncode != 0:
        print("  - index vs manifest      SKIPPED (not a git checkout)")
        return 0

    spec = importlib.util.spec_from_file_location("_pk_gen", gen_path)
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)

    def h(blob):
        fh = tempfile.NamedTemporaryFile(delete=False)
        fh.write(blob)
        fh.close()
        try:
            return gen._sha256(Path(fh.name))
        finally:
            os.unlink(fh.name)

    # A checker that cannot be shown to distinguish the cases is not evidence.
    if not (h(b"a\r\nb\r\n") == h(b"a\nb\n") and h(b"a\nb\n") != h(b"a\nc\n")):
        print("  ! index vs manifest      GATE FAIL — the generator's hasher is not "
              "EOL-insensitive-and-content-sensitive; this check cannot be trusted.")
        return 1

    man = json.loads(_read(str(man_path)))
    entries = man.get("files", [])
    not_staged, mismatched, unstaged_edit = [], [], []

    for ent in entries:
        rel = (ent.get("path") or "").replace("\\", "/")
        want = ent.get("sha256")
        if not rel or not want:
            continue
        r = git("show", ":" + rel)
        if r.returncode != 0:
            not_staged.append(rel)          # in the manifest, absent from the index
            continue
        if h(r.stdout) != want:
            mismatched.append(rel)          # committed bytes != what the manifest claims

    # A tracked shipping file edited but not staged is the same defect one step earlier:
    # the manifest may still match disk while the commit carries something else.
    d = git("diff", "--name-only", "--")
    if d.returncode == 0:
        dirty = {ln for ln in d.stdout.decode("utf-8", "replace").split("\n") if ln}
        shipping = {(e.get("path") or "").replace("\\", "/") for e in entries}
        unstaged_edit = sorted(dirty & shipping)

    problems = len(not_staged) + len(mismatched) + len(unstaged_edit)
    if not problems:
        print("  - index vs manifest      %d entries; committed bytes match the manifest"
              % len(entries))
        return 0

    print("  ! index vs manifest      GATE FAIL — what would be COMMITTED is not what "
          "the manifest describes.")
    for rel in not_staged[:6]:
        print("      not in the index (never `git add`-ed): %s" % rel)
    for rel in mismatched[:6]:
        print("      staged bytes do not match the manifest hash: %s" % rel)
    for rel in unstaged_edit[:6]:
        print("      shipping file modified but not staged: %s" % rel)
    if problems > 18:
        print("      ... %d more" % (problems - 18))
    print("      Fix: `git add` the shipping files, regenerate the manifest if any of "
          "them changed, then re-run this gate and the update rehearsal.")
    return 1


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
    if not ok:
        print("GATE FAIL — surfaces disagree; fix before pushing.")
        return 1
    print(f"  version agreement          all surfaces on {versions.pop()}")
    # Version agreement is necessary, not sufficient. Run every remaining check and
    # report them all rather than short-circuiting on the first pass.
    rc = check_memory_stores()
    rc_idx = check_index_matches_manifest(root)
    # Report BOTH before returning. Short-circuiting on the first failure hides the
    # second, and a release stopped twice in a row for one problem at a time is how a
    # gate gets run once and then worked around.
    if rc or rc_idx:
        return rc or rc_idx
    print("GATE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
