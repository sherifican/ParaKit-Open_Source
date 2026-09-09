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
  7. Check 1 required assets: rooted source inventory, generator whitelist
     coverage, exact-spelling served presence, and unresolved-reference refusal.
     This invokes ship_parity in release mode. Local-import delivery, membership,
     drift, and content validity have separate checks; this stage does not
     certify those properties.
  8. Check 2 candidate identity and delivery set: staged tree (`git write-tree`),
     staged `update_manifest.json` blob, raw index-blob hashes (never gen._sha256),
     isolated `git archive` member identity, LFS-pointer refusal, then
     `check_delivery_set` on that same tree (M⊆G, remainder, duplicates,
     casefold, unsafe paths, required gate tools).
  9. Check 2 frozen-reference drift: candidate index blobs versus dev HEAD
     index blobs (`check_frozen_drift`); generator and gate versus dev HEAD
     even though they are not in M; README expected-different; LFS pointer
     references use the smudged worktree with a named diagnostic; a candidate
     blob that is itself a pointer is refused before its manifested content is
     compared with an available dev counterpart; a listed dev path whose blob
     cannot be read is `reference_unavailable`, never a skip. The running
     gate's own checkout is the reference; a candidate that resolves to it or
     selects its git directory is `self_reference_refused`.
 10. Check 2 served NPZ validity: each served cleanup model blob in the staged
     tree (`SERVED_CLEANUP_NPZ`) is opened with `np.load(allow_pickle=False)`
     from bytes and every key `NumpyRF.load` reads is materialized with the
     loader's casts and slice bounds. LFS pointers, non-zip bytes, missing or
     unreadable members, wrong shapes, and models absent from M are refused;
     the worktree file is never consulted.
 11. Check 2 served ONNX validity: the staged `parakit_drum_model.onnx` blob is
     opened with `onnxruntime.InferenceSession(blob, providers=["CPUExecutionProvider"])`
     and one deterministic conversion scenario is run (exactly one float input
     `[1, <symbolic>, 90, 1]`, one float output `[1, <symbolic>, 5]`, zeros
     `(1, 8, 90, 1)` float32, finite `(1, 8, 5)`). LFS pointers, session
     failures, contract mismatches, run failures, and a model absent from M
     are refused; the worktree file is never consulted. A missing onnxruntime
     is UNMEASURED red, never green.
 12. Same-invocation controls. Default main() is the orchestrator: hash this
     file plus ship_parity.py and check_release_file_drift.py, freeze the
     candidate with one write-tree, run _breaker/release_gate_regress.py as a
     child with --gate-source this file (the child reads the gate set once,
     prints its receipt from that capture, and executes the capture, never
     a later disk read), require rc 0 and matching digest lines, run the
     stages against that frozen tree_id (the Check 2 stages
     forward it; version, memory, mirror, and asset checks read the working
     tree), re-write-tree and require equality, re-hash the gate set and
     require every helper load the stages executed to equal the receipt,
     and only then print GATE PASS. --stages-only runs the
     stages and prints STAGES OK / STAGES FAIL, never GATE PASS. No skip
     flag, no cached receipt, no environment substitute.

Exit 0 = all checks pass, including the same-invocation receipt.
Exit 1 = a mismatch, a dirty memory store, or a missing/failed/mismatched
regression child. Run it after gen_update_manifest.py, before `git push`.
"""
import json
import os
import re
import sys

DEFAULT_ROOT = r"C:/Users/micah/ParaKit-Open_Source"
# The extractor mirror check needs the UPSTREAM, which lives only in the dev tree.
# Overridable via PARAKIT_DEV_ROOT so this is not silently box-specific.
DEFAULT_DEV_ROOT = r"C:/Users/micah/PROJECTS & SIDE HUSSTLES/ParaKit"


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


def check_index_matches_manifest(root, tree_id=None):
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

    RAW BYTES ARE THE VERDICT. The updater downloads the index blob GitHub serves, so
    the manifest hash is compared to the raw sha256 of `git cat-file blob <tree>:<path>`
    (ship_parity.check_identity), never to gen_update_manifest's CRLF-normalising
    `_sha256`. An earlier version of this stage imported that hasher and self-tested
    that it was EOL-insensitive; that was the blindness: a `-text` blob stored with
    CRLF hashed as if it were LF and passed. The LF-normalised comparison survives only
    as a named diagnostic (`raw_hash_mismatch_lf_normalized`) inside check_identity.

    Requires a git checkout and both inputs; absence is a failing precondition.
    """
    import subprocess
    import importlib.util
    from pathlib import Path

    root = Path(root)
    man_path = root / "update_manifest.json"
    gen_path = root / "tools" / "gen_update_manifest.py"
    missing = [p for p in (man_path, gen_path) if not p.is_file()]
    if missing:
        print("  ! index vs manifest      GATE FAIL (missing required input)")
        for p in missing:
            print("      required input absent: %s" % p.relative_to(root).as_posix())
        return 1  # parity preflight: missing inputs

    def git(*a):
        return subprocess.run(("git", "-C", str(root)) + a,
                              capture_output=True)

    if git("rev-parse", "--git-dir").returncode != 0:
        print("  ! index vs manifest      GATE FAIL (not a git checkout)")
        print("      candidate is not a git checkout: %s" % root)
        return 1  # parity preflight: checkout required

    scanner = Path(__file__).resolve().with_name("ship_parity.py")
    try:
        if not scanner.is_file():
            raise FileNotFoundError("MISSING identity helper: tools/ship_parity.py")
        subject = _load_attested("ship_parity_digest", scanner, "_release_ship_identity")
        result = subject.check_identity(root, tree_id=tree_id)
    except Exception as exc:
        print("  ! index vs manifest      GATE FAIL (identity unmeasured)")
        print("      %s: %s" % (type(exc).__name__, exc))
        return 1

    issues = list(result.get("issues") or [])
    setup = list(result.get("setup") or [])
    g_count = None
    m_count = None
    rc_seen = [result.get("rc")]
    try:
        if not result.get("tree_id"):
            raise RuntimeError("identity returned no tree_id")
        staged = subject.cat_file_blob(
            root, "%s:update_manifest.json" % result["tree_id"]
        )
        man = json.loads(staged.decode("utf-8"))
        delivery = subject.check_delivery_set(root, result["tree_id"], man)
        rc_seen.append(delivery.get("rc"))
        issues.extend(delivery.get("issues") or [])
        setup.extend(delivery.get("setup") or [])
        g_count = delivery.get("g_count")
        m_count = delivery.get("m_count")
    except Exception as exc:
        setup.append(
            "delivery set unmeasured: %s: %s" % (type(exc).__name__, exc)
        )

    if not issues and not setup and all(code == 0 for code in rc_seen):
        print(
            "  - index vs manifest      %d entries; tree=%s; raw index blobs match the staged manifest; delivery set G=%d M=%d"
            % (
                result["entry_count"],
                (result.get("tree_id") or "?")[:12],
                g_count if g_count is not None else -1,
                m_count if m_count is not None else -1,
            )
        )
        return 0

    print("  ! index vs manifest      GATE FAIL — staged tree identity, raw hashes, or delivery set failed")
    for item in issues:
        print("      " + json.dumps(item, ensure_ascii=False, sort_keys=True))
    for gap in setup:
        print("      UNMEASURED " + gap)
    if not issues and not setup:
        print("      identity/delivery rc=%r with no issues (not a pass)" % (rc_seen,))
    return 1


def check_drift_vs_dev(root, dev_root=None, tree_id=None):
    """FAIL if the candidate's frozen tree drifted from the running gate's HEAD."""
    import importlib.util
    from pathlib import Path

    root = Path(root).resolve()
    if dev_root is None:
        dev_root = Path(__file__).resolve().parents[1]
    else:
        dev_root = Path(dev_root).resolve()

    drift_py = Path(__file__).resolve().with_name("check_release_file_drift.py")
    try:
        if not drift_py.is_file():
            raise FileNotFoundError(
                "MISSING drift helper: tools/check_release_file_drift.py"
            )
        subject = _load_attested("drift_digest", drift_py, "_release_file_drift")
        scanner = drift_py.with_name("ship_parity.py")
        if not scanner.is_file():
            raise FileNotFoundError("MISSING identity helper: tools/ship_parity.py")
        drift_sp = _load_attested(
            "ship_parity_digest", scanner, "_release_drift_ship_parity"
        )
        result = subject.check_frozen_drift(
            dev_root, root, tree_id=tree_id,
            sp=drift_sp,  # same-invocation attested drift scanner
        )
        if getattr(subject, "LAST_SCANNER_SOURCE", None) != "injected":
            raise RuntimeError(
                "drift helper ran with a scanner the receipt did not attest"
            )
    except Exception as exc:
        print("  ! drift vs dev            GATE FAIL (drift unmeasured)")
        print("      %s: %s" % (type(exc).__name__, exc))
        return 1

    issues = list(result.get("issues") or [])
    setup = list(result.get("setup") or [])
    ran = bool(result.get("ran"))
    if not ran and not issues and not setup:
        print("  ! drift vs dev            GATE FAIL (drift unmeasured)")
        print("      comparison did not run")
        return 1
    diagnostics = list(result.get("diagnostics") or [])
    if issues or setup:
        print("  ! drift vs dev            GATE FAIL — frozen-reference drift")
        for item in issues:
            print("      " + json.dumps(item, ensure_ascii=False, sort_keys=True))
        for item in diagnostics:
            print("      " + json.dumps(item, ensure_ascii=False, sort_keys=True))
        for gap in setup:
            print("      UNMEASURED " + gap)
        return 1
    print(
        "  - drift vs dev            compared=%d; expected-different=%d; no-dev=%d; tree=%s"
        % (
            result.get("compared", 0),
            len(result.get("expected_different") or []),
            result.get("no_dev", 0),
            (result.get("tree_id") or "?")[:12],
        )
    )
    for item in diagnostics:
        print("      " + json.dumps(item, ensure_ascii=False, sort_keys=True))
    return 0


def check_npz_validity(root, tree_id=None):
    """FAIL if a served cleanup NPZ is a pointer, truncated, or missing loader arrays."""
    import importlib.util
    from pathlib import Path

    root = Path(root)
    scanner = Path(__file__).resolve().with_name("ship_parity.py")
    try:
        if not scanner.is_file():
            raise FileNotFoundError("MISSING identity helper: tools/ship_parity.py")
        subject = _load_attested("ship_parity_digest", scanner, "_release_ship_npz")
        result = subject.check_npz_validity(root, tree_id=tree_id)
    except Exception as exc:
        print("  ! npz validity            GATE FAIL (npz unmeasured)")
        print("      %s: %s" % (type(exc).__name__, exc))
        return 1

    issues = list(result.get("issues") or [])
    setup = list(result.get("setup") or [])
    ran = bool(result.get("ran"))
    if not ran and not issues and not setup:
        print("  ! npz validity            GATE FAIL (npz unmeasured)")
        print("      comparison did not run")
        return 1
    if issues or setup:
        print("  ! npz validity            GATE FAIL — served cleanup NPZ invalid")
        for item in issues:
            print("      " + json.dumps(item, ensure_ascii=False, sort_keys=True))
        for gap in setup:
            print("      UNMEASURED " + gap)
        return 1
    print(
        "  - npz validity            opened=%d; materialized=%d; tree=%s"
        % (
            result.get("opened", 0),
            result.get("materialized", 0),
            (result.get("tree_id") or "?")[:12],
        )
    )
    return 0


def check_onnx_validity(root, tree_id=None):
    """FAIL if the served drum ONNX is a pointer, truncated, or fails the conversion contract."""
    import importlib.util
    from pathlib import Path

    root = Path(root)
    scanner = Path(__file__).resolve().with_name("ship_parity.py")
    try:
        if not scanner.is_file():
            raise FileNotFoundError("MISSING identity helper: tools/ship_parity.py")
        subject = _load_attested("ship_parity_digest", scanner, "_release_ship_onnx")
        result = subject.check_onnx_validity(root, tree_id=tree_id)
    except Exception as exc:
        print("  ! onnx validity            GATE FAIL (onnx unmeasured)")
        print("      %s: %s" % (type(exc).__name__, exc))
        return 1

    issues = list(result.get("issues") or [])
    setup = list(result.get("setup") or [])
    ran = bool(result.get("ran"))
    opened = int(result.get("opened") or 0)
    scenario = int(result.get("scenario") or 0)
    if not ran and not issues and not setup:
        print("  ! onnx validity            GATE FAIL (onnx unmeasured)")
        print("      comparison did not run")
        return 1
    if issues or setup:
        unmeasured_only = any(
            item.get("code") == "onnx_unmeasured" for item in issues
        ) and not any(
            item.get("code") not in ("onnx_unmeasured",) for item in issues
        )
        if unmeasured_only or (setup and not opened):
            print("  ! onnx validity            GATE FAIL (onnx unmeasured)")
        else:
            print("  ! onnx validity            GATE FAIL — served ONNX invalid")
        for item in issues:
            print("      " + json.dumps(item, ensure_ascii=False, sort_keys=True))
        for gap in setup:
            print("      UNMEASURED " + gap)
        return 1
    if opened < 1 or scenario < 1:
        print("  ! onnx validity            GATE FAIL (onnx unmeasured)")
        print("      served model was not opened and run")
        return 1
    print(
        "  - onnx validity            opened=%d; scenario=%d; tree=%s"
        % (
            opened,
            scenario,
            (result.get("tree_id") or "?")[:12],
        )
    )
    return 0


def check_extractor_mirrors(root, dev_root=None):
    """The SHIPPING parser copies still match the upstream they mirror.

    `extractor/rlrr_parse.py` states its own rule: the five shared functions must
    stay identical to `paradb_extract.py`, direction upstream -> mirror only, and
    "use sync_check.py at repo root" — which exists in NEITHER repo. The rule named
    its enforcement mechanism and the mechanism was never written, so a mirror sat
    divergent on parse_rlrr across roughly four releases.

    ⛔ WHY THIS IS IN THE GATE AND NOT ONLY AN INVARIANT. The upstream lives ONLY in
    the dev tree; both mirrors ship from the public one. An invariant runs in dev and
    is structurally blind to what is published. And the tempting self-contained check
    — comparing the public repo's two copies to EACH OTHER — is satisfiable while
    broken: both copies can agree perfectly and both be stale against an upstream
    that moved. So the comparison has to reach across both trees, and ship time is
    the only moment all three files are in scope.

    Missing upstream is UNMEASURED, reported as such, and does NOT pass.
    """
    import ast
    import hashlib

    SHARED = ("parse_rlrr", "event_to_class", "event_velocity", "event_time",
              "write_ground_truth_mid")
    mirrors = ["rlrr_parse.py", os.path.join("extractor", "rlrr_parse.py")]

    if dev_root is None:
        dev_root = os.environ.get("PARAKIT_DEV_ROOT", DEFAULT_DEV_ROOT)
    upstream = os.path.join(dev_root, "paradb_extract.py")
    if not os.path.isfile(upstream):
        print(f"  ? extractor mirrors        UNMEASURED — upstream not found at "
              f"{upstream}; set PARAKIT_DEV_ROOT or pass it. Not treated as a pass.")
        return 1

    def fnhashes(path):
        src = open(path, encoding="utf-8", errors="replace").read()
        lines = src.splitlines()
        out = {}
        for n in ast.walk(ast.parse(src)):
            if isinstance(n, ast.FunctionDef) and n.name in SHARED:
                body = "\n".join(lines[n.lineno - 1:n.end_lineno])
                out[n.name] = hashlib.sha256(body.encode()).hexdigest()[:12]
        return out

    try:
        up = fnhashes(upstream)
    except Exception as exc:
        print(f"  ? extractor mirrors        UNMEASURED — upstream unparseable: {exc}")
        return 1
    if not up:
        print("  ! extractor mirrors        upstream defines NONE of the five shared "
              "functions — zero comparisons is not a pass")
        return 1

    problems, checked = [], 0
    for rel in mirrors:
        p = os.path.join(root, rel)
        if not os.path.isfile(p):
            problems.append(f"{rel}: missing from the release — UNMEASURED")
            continue
        try:
            mi = fnhashes(p)
        except Exception as exc:
            problems.append(f"{rel}: unparseable ({exc})")
            continue
        for f in up:
            checked += 1
            if f not in mi:
                problems.append(f"{rel}: {f} absent")
            elif mi[f] != up[f]:
                problems.append(f"{rel}: {f} DIFFERS from upstream "
                                f"({up[f]} vs {mi[f]})")

    if problems:
        print("  ! extractor mirrors        " + problems[0])
        for extra in problems[1:4]:
            print(f"                             {extra}")
        return 1
    print(f"  - extractor mirrors      {checked} function comparisons across "
          f"{len(mirrors)} shipping copies match the upstream")
    return 0


def check_ship_parity(root):
    """Check 1: rooted required assets are whitelisted and present."""
    import importlib.util
    from pathlib import Path

    scanner = Path(__file__).resolve().with_name("ship_parity.py")
    try:
        if not scanner.is_file():
            raise FileNotFoundError("MISSING scanner: tools/ship_parity.py")
        subject = _load_attested("ship_parity_digest", scanner, "_release_ship_parity")
        result = subject.check(
            root=root,
            served=root,
            generator=Path(root) / "tools" / "gen_update_manifest.py",
            release=True,
        )
    except Exception as exc:
        print("  ! required assets        UNMEASURED")
        print("      %s: %s" % (type(exc).__name__, exc))
        return 1

    if result["rc"] == 0:
        print("  - required assets        PASS (%d inventory records)"
              % len(result["records"]))
        return 0

    verdict = "GATE FAIL" if result["rc"] == 1 else "UNMEASURED"
    print("  ! required assets        %s" % verdict)
    for item in result["issues"]:
        print("      " + json.dumps(item, ensure_ascii=False, sort_keys=True))
    for gap in result["setup"]:
        print("      UNMEASURED " + gap)
    if not result["issues"]:
        for item in result["review_queue"]:
            print("      REVIEW " + json.dumps(
                item, ensure_ascii=False, sort_keys=True))
    return 1


# Every helper load the gate performs, hashed from the bytes that were
# executed, keyed by the receipt label. The orchestrator requires each
# recorded digest to equal the receipt the regression child attested.
EXECUTED_DIGESTS = {}


def _load_attested(label, path, module_name):
    """Execute a helper from bytes read once and hashed under `label`.

    The code that runs is the code the receipt attests, not whatever the
    path holds at some later moment. `__file__` is kept so the helpers
    still resolve their siblings beside themselves.
    """
    import hashlib
    import types
    from pathlib import Path

    path = Path(path)
    data = path.read_bytes()
    EXECUTED_DIGESTS.setdefault(label, []).append(
        hashlib.sha256(data).hexdigest()
    )
    module = types.ModuleType(module_name)
    module.__file__ = str(path)
    sys.modules[module_name] = module
    exec(compile(data, str(path), "exec", dont_inherit=True), module.__dict__)
    return module


def write_candidate_tree(root):
    """One `git write-tree` on the candidate, GIT_* stripped and the work tree
    pinned (the identity stage's isolation). The orchestrator calls this twice.
    """
    import os
    import subprocess
    from pathlib import Path
    root = Path(root).resolve()
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    r = subprocess.run(
        ("git", "-C", str(root), "--work-tree=" + str(root), "write-tree"),
        capture_output=True,
        text=True,
        env=env,
    )
    if r.returncode != 0:
        raise RuntimeError(
            "git write-tree failed: %s"
            % ((r.stderr or r.stdout or "").strip() or r.returncode)
        )
    return (r.stdout or "").strip()


def digest_set(gate_dir=None):
    """sha256 of the running gate and the two loaders it importlibs."""
    import hashlib
    from pathlib import Path
    gate_dir = Path(gate_dir or Path(__file__).resolve().parent)
    out = {}
    for label, name in (
        ("gate_digest", "release_gate.py"),
        ("ship_parity_digest", "ship_parity.py"),
        ("drift_digest", "check_release_file_drift.py"),
    ):
        path = gate_dir / name
        out[label] = (
            hashlib.sha256(path.read_bytes()).hexdigest()
            if path.is_file()
            else "MISSING"
        )
    return out


def parse_digest_lines(text):
    found = {}
    for line in (text or "").splitlines():
        for key in ("gate_digest", "ship_parity_digest", "drift_digest"):
            prefix = key + "="
            if line.startswith(prefix):
                found[key] = line[len(prefix):].strip()
    return found


def _print_digest_set(expected):
    for key in ("gate_digest", "ship_parity_digest", "drift_digest"):
        print("%s=%s" % (key, expected.get(key, "MISSING")))


def _print_elapsed(t0):
    import time
    print("elapsed_s=%.3f" % (time.perf_counter() - t0))


def parse_gate_argv(argv):
    argv = list(argv)
    stages_only = False
    tree_id = None
    positional = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--stages-only":
            stages_only = True
        elif a == "--tree-id":
            i += 1
            if i >= len(argv):
                raise SystemExit("release_gate.py: --tree-id needs a value")
            tree_id = argv[i]
        elif a.startswith("-"):
            raise SystemExit(
                "release_gate.py: unknown option %s "
                "(no skip-regression / cached-receipt flag exists)"
                % a
            )
        else:
            positional.append(a)
        i += 1
    root = positional[0] if positional else DEFAULT_ROOT
    return root, stages_only, tree_id


def check_version_agreement(root):
    surfaces = collect(root)
    versions = set(surfaces.values())
    width = max(len(k) for k in surfaces) if surfaces else 0
    ok = len(versions) == 1 and not next(iter(versions)).startswith("(")
    print("Release gate — %s" % root)
    for k, v in surfaces.items():
        mark = (
            " "
            if ok or v == max(versions, key=list(surfaces.values()).count)
            else "!"
        )
        print("  %s %-*s  %s" % (mark, width, k, v))
    if not ok:
        print("GATE FAIL — surfaces disagree; fix before pushing.")
        return 1
    print("  version agreement          all surfaces on %s" % versions.pop())
    return 0


def run_stages(root, *, tree_id=None, stage_fns=None):
    """Run every gate stage. Print STAGES OK / STAGES FAIL. Never GATE PASS."""
    rc_ver = check_version_agreement(root)
    if rc_ver:
        print("executed_stages=version")
        print("STAGES FAIL")
        return 1
    if tree_id is None:
        try:
            tree_id = write_candidate_tree(root)
        except Exception:
            tree_id = None
    print("frozen_tree=%s" % (tree_id or "?"))
    if stage_fns is not None:
        names = []
        rcs = []
        for name, fn in stage_fns:
            names.append(name)
            rcs.append(int(fn(root, tree_id)))
        print("executed_stages=%s" % ",".join(["version"] + names))
        if any(rcs):
            print("STAGES FAIL")
            return 1
        print("STAGES OK")
        return 0
    # Production path. Named calls so INV166/168/169/170 propagation needles
    # keep binding exactly once. Version agreement already returned above.
    rc = check_memory_stores()
    rc_idx = check_index_matches_manifest(root, tree_id=tree_id)
    rc_drift = check_drift_vs_dev(root, tree_id=tree_id)
    rc_npz = check_npz_validity(root, tree_id=tree_id)
    rc_onnx = check_onnx_validity(root, tree_id=tree_id)
    rc_mir = check_extractor_mirrors(root)
    rc_assets = check_ship_parity(root)
    print("executed_stages=version,memory,index,drift,npz,onnx,mirrors,assets")
    # Every stage above runs before any stage failure is returned.
    if rc_idx:
        print("STAGES FAIL")
        return 1  # check2 identity gate failure propagation
    if rc_drift:
        print("STAGES FAIL")
        return 1  # check2 drift gate failure propagation
    if rc_npz:
        print("STAGES FAIL")
        return 1  # check2 npz validity gate failure propagation
    if rc_onnx:
        print("STAGES FAIL")
        return 1  # check2 onnx validity gate failure propagation
    if rc_assets:
        print("STAGES FAIL")
        return 1  # check1 gate failure propagation
    if rc or rc_mir:
        print("STAGES FAIL")
        return rc or rc_mir
    print("STAGES OK")
    return 0


def _default_child_argv():
    from pathlib import Path
    regress = (
        Path(__file__).resolve().parents[1]
        / "_breaker"
        / "release_gate_regress.py"
    )
    return [
        sys.executable,
        str(regress),
        "--gate-source",
        str(Path(__file__).resolve()),
    ]


def run_orchestrator(root, *, child_argv=None, stage_fns=None,
                     write_tree_fn=None, digest_dir=None):
    """Same-invocation controls. The only path that may print GATE PASS."""
    import subprocess
    import time
    from pathlib import Path

    t0 = time.perf_counter()
    reasons = []
    EXECUTED_DIGESTS.clear()
    expected = digest_set(digest_dir)
    _print_digest_set(expected)
    write_tree_fn = write_tree_fn or write_candidate_tree
    frozen = None
    try:
        frozen = write_tree_fn(root)
        print("candidate_tree=%s" % frozen)
    except Exception as exc:
        reasons.append("candidate_unmeasured")
        print("candidate_tree=?")
        print("      %s: %s" % (type(exc).__name__, exc))

    if child_argv is None:
        child_argv = _default_child_argv()
    proc = None
    try:
        if not child_argv:
            raise ValueError("empty child argv")
        proc = subprocess.run(
            list(child_argv), capture_output=True, text=True
        )  # same-invocation regression child
    except (FileNotFoundError, OSError, ValueError):
        proc = None
    child_out = "" if proc is None else (proc.stdout or "") + (proc.stderr or "")
    if child_out:
        print(child_out, end="" if child_out.endswith("\n") else "\n")
    got = parse_digest_lines(child_out)
    # A child that could not start, or that printed no receipt at all (an
    # interpreter that could not open the harness, say), has not attested
    # its execution; nothing it may have run counts.
    if proc is None or not got:  # same-invocation regression child required
        reasons.append("regression_omitted")
    if proc is not None and proc.returncode != 0:  # same-invocation regression rc
        reasons.append("regression_failed")
    if got and got != expected:  # same-invocation gate-digest comparison
        reasons.append("regression_wrong_digest")

    rc_stages = run_stages(root, tree_id=frozen, stage_fns=stage_fns)
    if rc_stages != 0:
        reasons.append("stages_failed")

    end_tree = None
    try:
        end_tree = write_tree_fn(root)
    except Exception as exc:
        reasons.append("candidate_unmeasured")
        print("      end write-tree: %s: %s" % (type(exc).__name__, exc))
    if end_tree != frozen:  # same-invocation candidate identity
        reasons.append("candidate_changed")

    # The receipt attests the bytes the child exercised. The stages loaded
    # the helpers again afterwards: the gate set on disk must still hash to
    # the receipt, and every helper load the stages executed must have
    # executed exactly those bytes.
    try:
        final = digest_set(digest_dir)
    except Exception as exc:
        final = {}
        reasons.append("gate_unmeasured")
        print("      final digest: %s: %s" % (type(exc).__name__, exc))
    if final != expected:  # same-invocation gate bytes stable
        reasons.append("gate_changed")
        for key in sorted(final):
            if final[key] != expected.get(key):
                print("      final %s=%s" % (key, final[key]))
    executed_mismatch = [
        (label, digest)
        for label, digests in EXECUTED_DIGESTS.items()
        for digest in digests
        if digest != expected.get(label)
    ]
    if executed_mismatch:  # same-invocation helper execution attested
        reasons.append("helper_changed")
        for label, digest in executed_mismatch:
            print("      executed %s=%s" % (label, digest))

    if reasons:  # same-invocation approval requires receipt
        for code in reasons:
            print("GATE FAIL (%s)" % code)
        _print_elapsed(t0)
        return 1
    print("GATE PASS")
    print("candidate_tree=%s" % frozen)
    _print_digest_set(final)
    print("regression_receipt=ok")
    _print_elapsed(t0)
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    root, stages_only, tree_id = parse_gate_argv(argv)
    if stages_only:
        return run_stages(root, tree_id=tree_id)
    return run_orchestrator(root)


if __name__ == "__main__":
    sys.exit(main())
