"""Compare EVERY published file against the frozen dev HEAD, before a release goes out.

WHY THIS EXISTS. 4.12.0 shipped a changelog entry describing a fix to
`parakit_cleanup/` and did not ship the two files the fix lived in. Nothing
caught it: the stager copies exactly two surfaces (the app and the changelog)
and says so plainly, the manifest was regenerated from the public checkout so
it was perfectly self-consistent with the WRONG bytes, and both CDN coherence
gates passed for the same reason. Every automated check agreed, because every
one of them asks "is the published set internally consistent?" and none asks
"is the published set the code that was tested?"

WHAT IT ASSERTS (batch 9f). For every entry in the candidate's staged
`update_manifest.json` that has a counterpart at `HEAD:<path>` in the dev
checkout, the two *index blobs* are identical (raw sha256). Candidate bytes
are `git cat-file blob <write-tree>:<path>`, never the worktree. Reference
bytes are `git cat-file blob HEAD:<path>` in dev, never the worktree — except
the LFS case below. Hashing is raw; gen_update_manifest's CRLF->LF heuristic
is not used. `tools/gen_update_manifest.py` and `tools/release_gate.py` are
compared even though they are not in M.

LFS. A dev HEAD blob that is a Git LFS pointer is not the model. The
reference is the dev worktree file (the smudge), named as diagnostic
`lfs_worktree_reference`. If the worktree is missing or still a pointer,
that is RED `lfs_reference_unsmudged`. Pointer bytes are never hashed as
content.

THREE THINGS IT DELIBERATELY DOES NOT FLAG.
  * Entries with no dev HEAD counterpart. Some published assets exist only in
    the public repo; absence from the frozen reference is not drift.
  * `README.md`. The stager edits it in place precisely because public carries
    paragraphs that exist nowhere in dev, so it is EXPECTED to differ and a
    copy would destroy content.
  * Files where PUBLIC IS AHEAD are still RED (`content_drift` with
    `direction=public_newer`) but are reported separately, not as an error to
    fix by copying. `docs/TROUBLESHOOTING.md` was corrected directly in the
    public repo on 2026-08-25 (a stale download source); copying dev over it
    would have silently reverted a real correction. Direction matters, so this
    prints which side is newer by commit date and leaves the decision to a
    person.

  py -3.12 tools/check_release_file_drift.py [DEV_ROOT] [PUBLIC_ROOT]

Exit 0 = no drift to act on. Exit 1 = at least one published file disagrees
with the frozen reference, a required tool is stale, the reference is the
candidate, or the reference cannot be read.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

EXPECT_DIFFERENT = {"README.md"}          # edited in place by the stager, by design
DEFAULT_PUBLIC = r"C:\Users\micah\ParaKit-Open_Source"


def _load_ship_parity():
    scanner = Path(__file__).resolve().with_name("ship_parity.py")
    if not scanner.is_file():
        raise FileNotFoundError("MISSING identity helper: tools/ship_parity.py")
    spec = importlib.util.spec_from_file_location("_drift_ship_parity", str(scanner))
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load tools/ship_parity.py")
    subject = importlib.util.module_from_spec(spec)
    sys.modules["_drift_ship_parity"] = subject
    spec.loader.exec_module(subject)
    return subject


def _same_checkout(a, b):
    left = Path(a).resolve()
    right = Path(b).resolve()
    try:
        return left.samefile(right)
    except OSError:
        return os.path.normcase(str(left)) == os.path.normcase(str(right))


def _is_git(sp, root):
    return sp.git_run(root, "rev-parse", "--git-dir").returncode == 0


def _git_dir(sp, root):
    """The repository a directory selects; two roots sharing it are one checkout."""
    result = sp.git_run(root, "rev-parse", "--absolute-git-dir")
    if result.returncode != 0:
        return None
    return os.path.normcase(result.stdout.decode("utf-8", "replace").strip())


def _last_commit_date(sp, root, rel):
    result = sp.git_run(root, "log", "-1", "--format=%ad", "--date=short", "--", rel)
    text = result.stdout.decode("utf-8", "replace").strip()
    return text or "?"


def _direction(dev_date, cand_date):
    if dev_date > cand_date:
        return "dev_newer"
    if cand_date > dev_date:
        return "public_newer"
    return "same_date"


def _reference_bytes(sp, dev_root, rel):
    """Frozen reference bytes for one path. Never returns pointer bytes as the model.

    Absence is established from the HEAD tree listing, not from a failed read:
    a listed path whose blob cannot be read is `reference_unavailable`, never a
    permitted skip.
    """
    listed = sp.git_run(dev_root, "ls-tree", "-z", "HEAD", "--", rel)
    if listed.returncode != 0:
        return "listing_failed", None, "reference_unavailable"
    if not listed.stdout.strip():
        return None, None, "absent"
    try:
        head = sp.cat_file_blob(dev_root, "HEAD:%s" % rel)
    except Exception:
        return "unreadable_blob", None, "reference_unavailable"
    if not sp.is_lfs_pointer(head):
        return "head_blob", head, "ok"
    worktree = Path(dev_root) / rel.replace("/", os.sep)
    if not worktree.is_file():
        return "unsmudged", head, "lfs_unsmudged"
    data = worktree.read_bytes()
    if sp.is_lfs_pointer(data):
        return "unsmudged", head, "lfs_unsmudged"
    return "worktree_smudge", data, "ok"


# Which scanner the last check_frozen_drift call used: "injected" when the
# caller supplied the module (the release gate's attested load), "path" when
# this helper loaded its sibling itself (the stager's CLI).
LAST_SCANNER_SOURCE = None


def check_frozen_drift(dev_root, candidate_root, tree_id=None, sp=None):
    """Compare candidate index blobs to frozen dev HEAD blobs (Check 2 drift).

    `sp` is the scanner module to use; the release gate passes the module it
    loaded from attested bytes so this helper's own load cannot execute bytes
    the regression receipt did not cover. Without it the sibling is loaded
    by path (the stager's CLI).
    """
    global LAST_SCANNER_SOURCE
    LAST_SCANNER_SOURCE = "path" if sp is None else "injected"
    sp = _load_ship_parity() if sp is None else sp
    issues = []
    setup = []
    diagnostics = []
    expected = []
    missing = []
    no_dev = 0
    compared = 0
    ran = False
    dev_root = Path(dev_root).resolve()
    candidate_root = Path(candidate_root).resolve()

    if not _is_git(sp, dev_root):
        issues.append({
            "stage": "check2_drift",
            "code": "reference_unavailable",
            "path": str(dev_root),
            "reason": "dev root is not a git checkout",
        })
        return _drift_result(1, None, 0, 0, expected, missing, issues, setup, diagnostics, True)
    if not _is_git(sp, candidate_root):
        issues.append({
            "stage": "check2_drift",
            "code": "reference_unavailable",
            "path": str(candidate_root),
            "reason": "candidate is not a git checkout",
        })
        return _drift_result(1, None, 0, 0, expected, missing, issues, setup, diagnostics, True)
    dev_git_dir = _git_dir(sp, dev_root)
    cand_git_dir = _git_dir(sp, candidate_root)
    if dev_git_dir is None or cand_git_dir is None:
        issues.append({
            "stage": "check2_drift",
            "code": "reference_unavailable",
            "path": str(dev_root if dev_git_dir is None else candidate_root),
            "reason": "repository identity could not be established (rev-parse --absolute-git-dir failed)",
        })
        return _drift_result(1, None, 0, 0, expected, missing, issues, setup, diagnostics, True)
    if _same_checkout(dev_root, candidate_root) or dev_git_dir == cand_git_dir:
        issues.append({
            "stage": "check2_drift",
            "code": "self_reference_refused",
            "path": str(candidate_root),
            "reason": (
                "candidate and dev root select the same repository (git dir %s);"
                " a checkout is not its own reference" % dev_git_dir
            ),
        })
        return _drift_result(1, None, 0, 0, expected, missing, issues, setup, diagnostics, True)

    if tree_id is None:
        try:
            tree_id = sp.git_write_tree(candidate_root)
        except Exception as exc:
            setup.append("write-tree unmeasured: %s" % exc)
            return _drift_result(2, None, 0, 0, expected, missing, issues, setup, diagnostics, False)

    try:
        staged = sp.cat_file_blob(candidate_root, "%s:update_manifest.json" % tree_id)
        man = json.loads(staged.decode("utf-8"))
    except Exception as exc:
        issues.append({
            "stage": "check2_drift",
            "code": "reference_unavailable",
            "path": "update_manifest.json",
            "reason": str(exc),
        })
        return _drift_result(1, tree_id, 0, 0, expected, missing, issues, setup, diagnostics, True)

    ran = True
    entries = man.get("files") if isinstance(man, dict) else None
    if not isinstance(entries, list):
        issues.append({
            "stage": "check2_drift",
            "code": "reference_unavailable",
            "path": "update_manifest.json",
            "reason": "files is not a list",
        })
        return _drift_result(1, tree_id, 0, 0, expected, missing, issues, setup, diagnostics, True)

    for ent in entries:
        if not isinstance(ent, dict):
            continue
        rel = (ent.get("path") or "").replace("\\", "/")
        if not rel:
            continue
        kind, ref_bytes, status = _reference_bytes(sp, dev_root, rel)
        if status == "absent":
            no_dev += 1
            continue
        if status == "reference_unavailable":
            issues.append({
                "stage": "check2_drift",
                "code": "reference_unavailable",
                "path": rel,
                "reason": (
                    "dev HEAD listing failed for the path;"
                    " an unestablished reference is not a permitted skip"
                    if kind == "listing_failed" else
                    "dev HEAD lists the path but its blob cannot be read;"
                    " an unreadable reference is not a permitted skip"
                ),
            })
            continue
        if status == "lfs_unsmudged":
            issues.append({
                "stage": "check2_drift",
                "code": "lfs_reference_unsmudged",
                "path": rel,
                "reason": (
                    "dev HEAD blob is an LFS pointer and the worktree is missing "
                    "or still a pointer; pointer bytes are not the model"
                ),
            })
            continue
        try:
            cand = sp.cat_file_blob(candidate_root, "%s:%s" % (tree_id, rel))
        except Exception as exc:
            missing.append(rel)
            issues.append({
                "stage": "check2_drift",
                "code": "content_drift",
                "path": rel,
                "reason": "candidate blob missing: %s" % exc,
            })
            continue
        if sp.is_lfs_pointer(cand):
            issues.append({
                "stage": "check2_drift",
                "code": "candidate_lfs_pointer",
                "path": rel,
                "reason": (
                    "candidate index blob is a Git LFS pointer;"
                    " pointer bytes are not compared as content"
                ),
            })
            continue
        if kind == "worktree_smudge":
            diagnostics.append({
                "lfs_worktree_reference": rel,
                "pointer_sha256": sp.sha256_raw(
                    sp.cat_file_blob(dev_root, "HEAD:%s" % rel)
                ),
                "worktree_sha256": sp.sha256_raw(ref_bytes),
                "candidate_sha256": sp.sha256_raw(cand),
            })
        compared += 1
        ref_digest = sp.sha256_raw(ref_bytes)
        cand_digest = sp.sha256_raw(cand)
        if rel in EXPECT_DIFFERENT:
            if ref_digest != cand_digest:
                expected.append(rel)
            continue
        if ref_digest != cand_digest:  # check2 content drift
            dev_date = _last_commit_date(sp, dev_root, rel)
            cand_date = _last_commit_date(sp, candidate_root, rel)
            direction = _direction(dev_date, cand_date)
            issues.append({
                "stage": "check2_drift",
                "code": "content_drift",
                "path": rel,
                "dev_sha256": ref_digest,
                "candidate_sha256": cand_digest,
                "direction": direction,
                "dev_date": dev_date,
                "candidate_date": cand_date,
                "reference": kind,
                "reason": "candidate index blob differs from the frozen dev reference",
            })

    tool_issues, tool_setup = sp.check_tool_reference_drift(
        dev_root, candidate_root, tree_id
    )
    issues.extend(tool_issues)
    setup.extend(tool_setup)
    tools_compared = len(sp.TOOL_REFERENCE_PATHS) - sum(
        1 for item in tool_issues if item.get("code") == "reference_unavailable"
    )

    rc = 1 if issues else 2 if setup else 0
    result = _drift_result(
        rc, tree_id, compared, no_dev, expected, missing, issues, setup, diagnostics, ran
    )
    result["tools_compared"] = tools_compared
    return result


def _drift_result(
    rc, tree_id, compared, no_dev, expected, missing, issues, setup, diagnostics, ran
):
    return {
        "schema": 1,
        "stage": "check2_drift",
        "rc": rc,
        "tree_id": tree_id,
        "compared": compared,
        "no_dev": no_dev,
        "expected_different": list(expected),
        "missing": list(missing),
        "issues": issues,
        "setup": setup,
        "diagnostics": diagnostics,
        "ran": ran,
    }


def _print_cli_report(result):
    issues = result.get("issues") or []
    expected = result.get("expected_different") or []
    missing = result.get("missing") or []
    drift = [i for i in issues if i.get("code") == "content_drift"]
    stale = [
        i for i in issues
        if i.get("code") in ("generator_stale", "gate_stale")
    ]
    print("== published files vs the dev tree ==")
    print("  manifest counterparts compared   %d" % result.get("compared", 0))
    print("  release tools compared           %d" % result.get("tools_compared", 0))
    print("  no dev counterpart (not checked) %d" % result.get("no_dev", 0))
    print("  differ BY DESIGN                 %d  %s"
          % (len(expected), ", ".join(expected) or "-"))
    print("  listed but absent from public    %d" % len(missing))
    print("  DRIFTED                          %d" % len(drift))
    print("  STALE TOOLS                      %d" % len(stale))
    for rel in missing:
        print("  [!!] MISSING in public   %s" % rel)
    for item in drift:
        rel = item.get("path")
        dd = item.get("dev_date") or "?"
        pd = item.get("candidate_date") or "?"
        direction = item.get("direction") or "same_date"
        side = {
            "dev_newer": "dev is newer",
            "public_newer": "public is newer",
            "same_date": "same date",
        }.get(direction, direction)
        print("  [!!] DRIFT  %-44s dev %s / public %s  (%s)" % (rel, dd, pd, side))
        if direction == "public_newer":
            print("       ^ public is AHEAD. Do NOT copy dev over it without reading the diff:"
                  " that reverts a correction made directly in the public repo.")
    for item in stale:
        print("  [!!] STALE  %-44s %s" % (item.get("path"), item.get("code")))
    for item in issues:
        if item.get("code") in (
            "self_reference_refused",
            "reference_unavailable",
            "lfs_reference_unsmudged",
            "candidate_lfs_pointer",
        ):
            print("  [!!] %s  %s  %s" % (
                item.get("code"), item.get("path"), item.get("reason"),
            ))
    for diag in result.get("diagnostics") or []:
        if "lfs_worktree_reference" in diag:
            print("  [..] LFS    %-44s compared worktree smudge (pointer not hashed)"
                  % diag["lfs_worktree_reference"])
    for gap in result.get("setup") or []:
        print("  UNMEASURED %s" % gap)
    bad = len(issues)
    setup = result.get("setup") or []
    ran = bool(result.get("ran"))
    passed = result.get("rc") == 0 and ran and not bad and not setup
    if passed:
        print(
            "\nDRIFT CHECK PASSED - compared %d manifest path(s) and %d release"
            " tool(s) against the frozen dev HEAD; %d expected difference(s);"
            " %d path(s) unchecked (no dev counterpart)"
            % (
                result.get("compared", 0),
                result.get("tools_compared", 0),
                len(expected),
                result.get("no_dev", 0),
            )
        )
        return 0
    if bad:
        print("\nDRIFT CHECK FAILED - %d issue(s) versus the frozen reference" % bad)
    else:
        print(
            "\nDRIFT CHECK UNMEASURED - the comparison did not complete (rc=%s)"
            % result.get("rc")
        )
    return 1


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    here = os.path.dirname(os.path.abspath(__file__))
    dev = argv[0] if argv else os.path.dirname(here)
    pub = argv[1] if len(argv) > 1 else DEFAULT_PUBLIC
    result = check_frozen_drift(dev, pub)
    _print_cli_report(result)
    return 0 if result["rc"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
