"""
passes.py — dependency-light port of the harness cleanup post-passes.

Faithful re-implementation of the detection-research harness's cymbal
re-classifier and kick phantom-remover post-passes.

⛔ THE REFERENCE FILES ARE NOT IN THIS REPO AND NEVER WERE. Earlier revisions
cited `tools/detection_harness/cymbal_postpass.py` / `kick_postpass.py` as if a
reader could open them — that path does not exist here, in the public checkout,
or anywhere in this repo's git history (audited 2026-08-09). The originals lived
in the detection-research environment where the cleanup pass was developed
(v4.5.0 era) and were never imported. So "faithful" cannot be re-verified against
a source by a maintainer of THIS repo; what binds today is (a) the bit-exactness
measured at port time, recorded below, and (b) the `_breaker/` invariants that
pin this sidecar's live behaviour. Treat this docstring as provenance history,
not as a checkable contract.

The ONLY behavioral change vs the harness is the model backend: instead of a
joblib-pickled sklearn Pipeline this uses a ``numpy_rf.NumpyRF`` loaded from the
exported ``.npz`` (which is bit-exact with the sklearn model — max prob diff 0.0,
0 decision flips on the full cached corpus). The gate logic, feature extractor,
lane sets, sort/normalize behavior, and "preserve onset count / only remove from
kick" invariants are reproduced EXACTLY so the sidecar matches the harness.

Imports: numpy + the package's own numpy_rf / features (librosa-based). No
sklearn / joblib / onnx.

CLASS-NAME RESOLUTION
---------------------
NumpyRF stores only the INTEGER class labels (``classes_`` e.g. [0,1,2]); the
harness joblib carried the parallel NAME list ``d["classes"]`` (e.g.
["hihat","crash","ride"]). Each exported ``.npz`` has a sibling ``<npz>.json``
with a ``"classes"`` field holding that name list — we read it from there so the
column-index -> lane-name mapping is identical to the harness.
"""
from __future__ import annotations

import os
import json
import warnings

import numpy as np

from . import features as cf
from . import bleed as cb
from .numpy_rf import NumpyRF

# ---- cymbal post-pass constants (mirror cymbal_postpass.py) -----------------
CYM_LANES = ("hihat", "crash", "ride")
# The harness defaults reclassify() to all-None (ungated audited result) but the
# faithfulness gate / production path use the asymmetric QUALITY gate that the
# exported model recommends.
#
# ⛔ `gate_to_ride` WAS 0.3, AND THAT VALUE COULD NEVER FIRE (measured 2026-08-06).
# The gate refuses a move only when the winning class's probability is BELOW it,
# and with three classes whose posteriors sum to 1 the winner is always >= 1/3
# = 0.3333. So 0.3 was unreachable: it refused nothing, and 0.0 and 0.3 produced
# byte-identical charts. Across 3,901 candidates sitting on charted rides,
# corpus-wide, the count of gate-blocked ride assignments was ZERO -- in the packs
# where the re-classifier emits no rides and in the packs where it works alike.
#
# The value is now None -- "no gate on moves into ride" -- which is what 0.3
# already meant. Output is unchanged; the knob no longer advertises a control it
# does not have. The old value survived because it read as a deliberate leniency
# setting, and every experiment that "tuned" it was measuring nothing.
#
# ⚠ A refusal gate CANNOT express leniency. Its floor is "refuse nothing", which
# is where this already sat. Wanting MORE rides than the argmax yields needs a
# different mechanism, not a smaller number here -- see `ride_floor` in assign().
#: ``ride_floor`` ENABLED at 0.425 on 2026-08-07. This CHANGES CHARTS — it accepts
#: ride where P(ride) >= 0.425 even when another class is argmax, which a refusal
#: gate structurally cannot do.
#:
#: Chosen per-pack, not on the corpus average. The aggregate favoured 0.400 but
#: two of 35 charts breached a severity cap there — Forsaken paid ten wrong-lane
#: notes for ZERO recovered rides. Nobody plays the corpus average.
#:
#: 0.425 is the **LOWEST** value at which every pack clears both caps. (The first
#: version of this comment said "highest", which is backwards and would mislead
#: the next person to tune it: cap breaches shrink monotonically as the floor
#: rises, so 0.450 and 0.500 clear them too. Higher is not safer, it is deader —
#: 0.500 is byte-identical to OFF on all 35 packs. The selection rule is "as much
#: recall as the caps allow", so the binding edge is the bottom.)
#:
#: ⚠ Wrong-lane is not free even though it is milder than a phantom in silence:
#: Paradiddle is VR and each instrument is a distinct object in 3D space, so a
#: wrong lane moves the player's arm. Both classes are capped, separately.
#:
#: ⚠ THE SEVERITY AXIS THIS WAS FIRST DECIDED ON WAS MISLABELLED. `phantom_no_drum`
#: was computed as "not on a charted crash and not on a charted hi-hat" and never
#: checked kick/snare/toms, so a cymbal call landing on a charted KICK was billed
#: at the worst rate. Re-measured 2026-08-08 with the classes split: only ~30% of
#: that column is genuinely silent. On the corrected axis the exchange rate roughly
#: doubles (+181 references per +9 silent phantoms, 20:1) and 0.400 vs 0.425 is a
#: near tie in aggregate — so the per-pack tail is not a tiebreaker here, it is the
#: whole argument. 0.425 still wins it: 35/35 clear, versus 33/35 at 0.400.
#:
#: Data: ride_floor_severity_2026-08-06.json (as decided),
#: ride_floor_severity_classed_2026-08-08.json + _decision_package_silent_2026-08-08
#: (corrected axis; every published field reproduced exactly, 0 mismatches).
RECOMMENDED_ASYM_QUALITY = {"gate_to_ride": None, "gate_swap": 0.7, "ride_floor": 0.425}

#: A gate at or below this cannot refuse anything, because the largest of N
#: probabilities summing to 1 is always at least 1/N. Configuring a value in that
#: range is always a mistake: it looks like a setting and behaves like None.
def unreachable_gate_floor(classes):
    """Highest gate value that is guaranteed never to fire for `classes`."""
    return 1.0 / max(len(classes), 1)


def check_gate_reachable(value, classes, name="gate"):
    """Return a complaint string if `value` is a gate that can never fire, else "".

    Deliberately returns rather than raises: this is called on the production path
    and a stale config must not break a user's conversion. The caller decides
    whether to log or assert. `None` is not a mistake -- it means "no gate" and
    says so."""
    if value is None:
        return ""
    floor = unreachable_gate_floor(classes)
    if value <= floor:
        return ("%s=%.3f can never refuse anything: with %d classes the winning "
                "probability is always >= %.4f. Use None to mean 'no gate'."
                % (name, value, len(classes), floor))
    return ""


#: ⛔ THE DUAL, AND IT POINTS THE OTHER WAY. Everything above describes a REFUSAL
#: gate, which dies at LOW values: it can only take moves away, so below 1/N it
#: refuses nothing. `ride_floor` is a PROMOTION floor and its dead zone is at the
#: TOP. The promotion branch only runs when ride is NOT argmax, and a non-argmax
#: class can never hold more than half the mass (if P(ride) > 1/2 then no other
#: class can match it, so ride would BE the argmax). So a floor at or above 0.5
#: can essentially never fire — independent of the class count.
#:
#: This is not theory. The 35-pack sweep printed `ride_floor=0.500` as byte-identical
#: to the baseline on every pack and every column, and that row sat in the results
#: table unremarked. Meanwhile INV97 was scanning the shipped config for values
#: <= 1/3 and would have flagged `ride_floor=0.30` — which is its most AGGRESSIVE
#: live setting — as "can never fire". The guard written to catch a knob that looks
#: like a setting and behaves like nothing was, for the replacement knob, wrong at
#: both ends. Found by an independent audit 2026-08-08; pinned by INV97.
PROMOTION_FLOOR_DEAD_AT = 0.5


def unreachable_promotion_floor(classes=None):
    """Lowest promotion-floor value at which promotion is dead for practical purposes.

    Takes `classes` only for symmetry with `unreachable_gate_floor`; the bound is
    1/2 for ANY class count, because it comes from "not the argmax", not from N.

    ⚠ ABOVE 0.5 it is a proof; AT exactly 0.5 it is not. A perfect two-way tie
    (P(ride)=P(other)=0.5) leaves ride non-argmax only because argmax breaks ties
    toward the lower index -- and then P(ride) >= 0.5 does fire. Averaged forest
    probabilities are rationals, so that tie is constructible rather than
    measure-zero. It is empirically absent: the 0.500 rung is byte-identical to
    baseline on all 35 corpus packs, every column. Flagging 0.5 is therefore the
    SAFE error -- it warns about a value that is dead in every observed case."""
    return PROMOTION_FLOOR_DEAD_AT


def check_promotion_floor_reachable(value, classes, name="ride_floor"):
    """Complaint string if `value` is a promotion floor that can never fire, else "".

    Returns rather than raises, for the same reason as `check_gate_reachable`: this
    runs on the production path and a stale config must not break a conversion."""
    if value is None:
        return ""
    ceil = unreachable_promotion_floor(classes)
    if value >= ceil:
        return ("%s=%.3f promotes nothing in practice: the branch only runs when "
                "ride is NOT the argmax, and a non-winning class cannot exceed %.2f "
                "(bar an exact tie). "
                "Use None to mean 'no promotion'." % (name, value, ceil))
    return ""

# ---- kick post-pass constants (mirror kick_postpass.py) ---------------------
KICK_PHANTOM_LABEL = 0
# 2026-07-31: HELD AT 0.9 after a corpus sweep, deliberately.
# A 40-pack sweep (41,771 detected kicks, split by input arm) recommended 0.950, and it IS
# strictly better than the pre-fix FULL-MIX baseline (288 phantoms removed / 4 real lost vs
# 25 / 8). But the recommendation was scored against the wrong arm for this app: on a
# DRUMS-STEM input the cleanup was ALREADY aligned, so today's behaviour there is the drums
# arm at 0.900 -- 680 removed / 28 lost. Moving to 0.950 would have cut phantom removal for
# stem users by 58% (680 -> 288), i.e. a REGRESSION on the main workflow, bought with a
# gain on a path that (a) is not the common case and (b) already pre-splits a full mix
# before detection anyway.
#
# Held also because this project's own error hierarchy says a phantom kick at a no-drum time
# is the WORST error class -- worse than a missing kick -- so at BOTH of the ratios below the
# extra cleaning is the better chart:
#   * gate trade, drums arm, 0.900 vs 0.950 -- 392 more phantoms removed for 24 more real
#     kicks, ~16:1. THIS is the trade this constant controls.
#   * a FIXED 0.900 scored on two DIFFERENT inputs, full-mix arm 25/8 vs drums arm 680/28.
#     An earlier revision of this comment quoted these as the gate trade, which was wrong
#     (right conclusion, wrong arithmetic attached), and a later one called them "what
#     4.9.9 ships". BOTH descriptions are now void: 4.9.9 was the change that fed this
#     pass the separator composite, and it was REVERTED on 2026-08-01 after a 40-pack
#     measurement of the real production condition showed it removes 178 FEWER phantoms
#     and scores 4.24 pp WORSE on cymbals than doing nothing. Nothing in this file's
#     behaviour changed; what changed is that the audio it reads is the user's own file,
#     as it has always been in shipped builds.
#
# ⚠ The gate is ALSO input-dependent, which this single constant cannot express: matched
# probes put the composite's equivalent point near 0.875 (stem) and 0.830 (full mix). Those
# are 10-pack figures, unshipped, and they only matter if the composite is ever fed here
# again. Note they would not fix the cymbal half at all -- the cymbal pass does not read
# this constant.
KICK_RECOMMENDED_GATE = 0.9   # unchanged across 4.9.8/4.9.9/4.9.10; see above before touching

# ---- cross-stem bleed kick pass (F12), two-tier -----------------------------
# A kick is a bleed-phantom candidate iff a non-drum stem dominates the drums
# stem at its onset (ratio = max(vocals,bass,other)/drums). ORTHOGONAL to the
# decay/timbre kick RF above (validated 2026-07-20: corr +0.08 among phantoms;
# stacked coverage ~doubles). F12 alone cannot reach high precision (real kicks
# often coincide with bass), so we split it in two, honoring recall>precision:
#   * ratio >= BLEED_REMOVE_GATE  -> AUTO-REMOVE (very conservative; only the
#     extreme-ratio phantoms, where real-kick loss is minimal).
#   * BLEED_REVIEW_GATE <= ratio < BLEED_REMOVE_GATE -> FLAG-FOR-REVIEW (never
#     removed; surfaced to the user, zero real-kick cost).
# Gates tuned on the ParaDB harness (13 sync-clean songs, 2026-07-20). Either
# gate = None disables that tier. remove_gate=20 adds only +0.44pp real-kick loss
# beyond the shipped decay RF (6.5:1 phantom:real, vs the RF's own ~2:1) while
# lifting phantom removal 25%->33%; the review band [3,20) surfaces another ~16%
# of phantoms for the user to remove by hand at zero automatic cost.
BLEED_REMOVE_GATE = 20.0
BLEED_REVIEW_GATE = 3.0


# ---- model loading (NumpyRF + class-name list from the .npz.json sidecar) ----
_CACHE = {}


def _load_class_names(npz_path):
    """Read the parallel class-NAME list from the exported ``<npz>.json`` sidecar.

    Mirrors the harness joblib's ``d["classes"]``. Falls back to None when no
    sidecar exists (callers that don't need names — e.g. kick — never read it)."""
    sidecar = npz_path + ".json"
    if os.path.exists(sidecar):
        with open(sidecar, "r", encoding="utf-8") as fh:
            meta = json.load(fh)
        names = meta.get("classes")
        if names is not None:
            return list(names)
    return None


def _load_phantom_label(npz_path, default=0):
    """Read ``phantom_label`` from the ``<npz>.json`` sidecar (mirrors the harness
    joblib ``d.get("phantom_label", 0)``). Absent / null -> default."""
    sidecar = npz_path + ".json"
    if os.path.exists(sidecar):
        with open(sidecar, "r", encoding="utf-8") as fh:
            meta = json.load(fh)
        pl = meta.get("phantom_label")
        if pl is not None:
            return int(pl)
    return default


def load_model(npz_path):
    """Return (NumpyRF model, class_name_list). Cached per path.

    ``class_name_list`` is indexed by INTEGER class label (model.classes_), so
    ``class_name_list[int(label)]`` gives the lane name — exactly how the harness
    ``cymbal_postpass.assign`` uses ``classes[int(mc[j])]``."""
    npz_path = os.path.abspath(npz_path)
    if npz_path not in _CACHE:
        model = NumpyRF.load(npz_path)
        names = _load_class_names(npz_path)
        _CACHE[npz_path] = (model, names)
    return _CACHE[npz_path]


# ===========================================================================
# CYMBAL RE-CLASSIFIER  (port of cymbal_postpass.reclassify / assign)
# ===========================================================================
def _required_prob(orig, pred, gate, gate_to_ride, gate_swap):
    """Min top-class probability to ALLOW the move orig->pred. 0.0 = not a move,
    or no gate configured for this transition.

    Verbatim logic from cymbal_postpass._required_prob: same-lane is never gated;
    a symmetric ``gate`` applies to all moves; otherwise a move INTO ride uses
    ``gate_to_ride`` (lenient) and a hihat<->crash swap uses ``gate_swap``
    (strict)."""
    if pred == orig:
        return 0.0
    if gate is not None:
        return gate
    if pred == "ride":
        return gate_to_ride if gate_to_ride is not None else 0.0
    return gate_swap if gate_swap is not None else 0.0


def assign(pairs, F, model, classes, gate=None, gate_to_ride=None, gate_swap=None,
           ride_floor=None):
    """Core cymbal relabeling step — from cymbal_postpass.assign, plus ``ride_floor``.

    ``pairs`` = [(onset_seconds, original_lane), ...] and ``F`` = the feature
    matrix in the SAME order. Returns {lane: [onsets]} for the three cymbal
    lanes. Onset COUNT is preserved (every pair lands in exactly one lane).

    ``ride_floor`` — ACCEPT ride whenever P(ride) reaches this, even where another
    class scores higher. **Default None = off, and the output is then bit-identical
    to the pre-2026-08-06 behaviour.**

    This exists because the thing the old `gate_to_ride=0.3` was *trying* to say
    could not be said by a refusal gate. Measured 2026-08-06: ride is the argmax
    for only 40.3% of candidates that sit on a charted ride, and for 0 of 130 in
    the packs that emit no rides at all — while the gate refused **nothing**,
    anywhere. So the binding constraint is argmax, and no gate value can loosen
    it, because a gate can only ever take moves away.

    ⚠ Turning this on trades ride recall against phantom rides directly, and the
    sweep says that trade is real, not free. SHIPS ON at 0.425 since 2026-08-07
    (it said "ships OFF" for a day after that); pick a value from
    measurement, not from taste."""
    out = {lane: [] for lane in CYM_LANES}
    if not pairs:
        return out
    for _nm, _v in (("gate", gate), ("gate_to_ride", gate_to_ride),
                    ("gate_swap", gate_swap)):
        _c = check_gate_reachable(_v, classes, _nm)
        if _c:
            warnings.warn(_c, RuntimeWarning, stacklevel=2)
    # ride_floor is a PROMOTION floor, so it gets the dual check, not this one.
    # Running it through check_gate_reachable would flag its most aggressive live
    # values and stay silent on the range where it is genuinely dead.
    _cf = check_promotion_floor_reachable(ride_floor, classes, "ride_floor")
    if _cf:
        warnings.warn(_cf, RuntimeWarning, stacklevel=2)
    gated = (gate is not None or gate_to_ride is not None or gate_swap is not None
             or ride_floor is not None)
    if gated and hasattr(model, "predict_proba"):
        proba = model.predict_proba(F)
        # predict_proba columns are ordered by model.classes_ (the integer labels
        # the model was trained on); map column index -> class-name via classes[].
        mc = list(getattr(model, "classes_", range(proba.shape[1])))
        ride_col = next((j for j in range(len(mc)) if classes[int(mc[j])] == "ride"), None)
        for (t, orig), row in zip(pairs, proba):
            j = int(np.argmax(row)); p = float(row[j])
            lab = classes[int(mc[j])]
            if (ride_floor is not None and ride_col is not None and lab != "ride"
                    and float(row[ride_col]) >= ride_floor):
                # Promote to ride, then let the normal gate logic judge the move on
                # ride's OWN probability rather than the winner's.
                lab, p = "ride", float(row[ride_col])
            if lab != orig and p < _required_prob(orig, lab, gate, gate_to_ride, gate_swap):
                lab = orig  # not confident enough to override the detector — keep its lane
            out[lab].append(t)
    else:
        preds = model.predict(F)
        for (t, _orig), pr in zip(pairs, preds):
            out[classes[int(pr)]].append(t)
    return out


def reclassify(y, sr, est_by_class, model=None, classes=None, npz_path=None,
               gate=None, gate_to_ride=None, gate_swap=None, ride_floor=None):
    """Return a new est dict with cymbal-lane onsets relabeled — port of
    cymbal_postpass.reclassify (joblib swapped for NumpyRF), plus ``ride_floor``.

    The set of cymbal onsets is preserved (no onsets created/destroyed) — only
    their hi-hat/crash/ride labels change. Non-cymbal lanes pass through.

    ``ride_floor`` defaults to None (off), in which case this is bit-identical to
    the pre-2026-08-06 behaviour. See assign()."""
    if model is None:
        model, classes = load_model(npz_path)
    est = {k: (list(v) if not isinstance(v, list) else list(v)) for k, v in est_by_class.items()}
    pairs = []
    for lane in CYM_LANES:
        for t in est.get(lane, []):
            pairs.append((float(t), lane))
        est[lane] = []
    if pairs:
        pairs.sort(key=lambda pr: pr[0])
        F = cf.extract_features(y, sr, [t for t, _ in pairs])
        assigned = assign(pairs, F, model, classes, gate=gate,
                          gate_to_ride=gate_to_ride, gate_swap=gate_swap,
                          ride_floor=ride_floor)
        for lane in CYM_LANES:
            est[lane] = assigned[lane]
    for lane in CYM_LANES:
        est[lane] = np.sort(np.asarray(est[lane], dtype=float))
    return est


# ===========================================================================
# KICK PHANTOM-REMOVER  (port of kick_postpass.remove_phantoms / filter_kicks)
# ===========================================================================
def phantom_proba(F, model, phantom_label=KICK_PHANTOM_LABEL):
    """P(phantom) for each onset whose features are the rows of F — verbatim from
    kick_postpass.phantom_proba. Maps the phantom integer label -> its
    predict_proba column via model.classes_."""
    if len(F) == 0:
        return np.zeros((0,), dtype=float)
    proba = model.predict_proba(F)
    mc = list(getattr(model, "classes_", range(proba.shape[1])))
    col = mc.index(phantom_label) if phantom_label in mc else 0
    return proba[:, col]


def filter_kicks(times, F, model, phantom_label=KICK_PHANTOM_LABEL,
                 gate=KICK_RECOMMENDED_GATE, scores_out=None):
    """Core removal step — verbatim from kick_postpass.filter_kicks. ``times`` =
    detected kick onset seconds, ``F`` = features in the SAME order. Returns the
    KEPT onsets (sorted) — a kick is DROPPED iff P(phantom) >= gate. gate=None =>
    keep all. NEVER creates onsets.

    ``scores_out`` (2026-07-30, diagnostic retention): pass a list and it is
    filled with one ``(time, p_phantom, dropped)`` tuple per input onset, in
    input order. P(phantom) is the single most diagnostic number in the kick
    path — it was computed here on every run and discarded on the next line, so
    "which kick did we remove, and how sure were we" had no answer anywhere.
    Default None keeps the old behaviour EXACTLY: same return, same type, no
    extra work, nothing allocated. Opt-in only, and the caller owns the list."""
    times = np.asarray(times, dtype=float)
    if times.size == 0 or gate is None:
        if scores_out is not None:
            # gate=None keeps everything; report it as such rather than leaving
            # the caller's list empty and ambiguous.
            scores_out.extend((float(t), None, False) for t in times)
        return np.sort(times)
    pph = phantom_proba(F, model, phantom_label)
    keep = pph < gate
    if scores_out is not None:
        scores_out.extend(
            (float(t), float(p), bool(not k))
            for t, p, k in zip(times, pph, keep))
    return np.sort(times[keep])


def remove_phantoms(y, sr, est_by_class, model=None, phantom_label=None,
                    npz_path=None, gate=KICK_RECOMMENDED_GATE):
    """Return a new est dict with phantom kick onsets removed — verbatim port of
    kick_postpass.remove_phantoms (joblib swapped for NumpyRF).

    ONLY the kick lane is modified; every other lane passes through unchanged and
    no onsets are created. ``gate`` (default 0.9, recall-safe) is the min
    P(phantom) to drop a kick; gate=None disables removal (passthrough)."""
    if model is None:
        model, _names = load_model(npz_path)
        if phantom_label is None:
            phantom_label = _load_phantom_label(npz_path, KICK_PHANTOM_LABEL)
    elif phantom_label is None:
        phantom_label = KICK_PHANTOM_LABEL
    est = {k: (list(v) if not isinstance(v, list) else list(v)) for k, v in est_by_class.items()}
    kicks = np.sort(np.asarray(est.get("kick", []), dtype=float))
    if kicks.size and gate is not None:
        F = cf.extract_features(y, sr, kicks.tolist())
        # v4.5.5: the decay-augmented kick model uses 42 features (40 spectral +
        # dt_prev + strength_ratio). Add the 2 decay features iff the LOADED model
        # expects the wider vector — so this stays correct with either the legacy
        # 40-feature npz or the new 42-feature one. `kicks` is already sorted above,
        # which the decay features require (they're relative to the previous kick).
        try:
            n_model = int(model._mean.shape[0])
        except Exception:
            n_model = F.shape[1]
        if n_model == F.shape[1] + 2:
            F = cf.add_decay_features(kicks, F)
        kicks = filter_kicks(kicks, F, model, phantom_label=phantom_label, gate=gate)
    est["kick"] = kicks
    return est


def bleed_kick_pass(stems, sr, est_by_class,
                    remove_gate=BLEED_REMOVE_GATE, review_gate=BLEED_REVIEW_GATE,
                    ratios_out=None):
    """Two-tier cross-stem BLEED kick pass (F12) — orthogonal to remove_phantoms.

    For each kick, ``ratio = bleed.bleed_ratios`` (non-drum vs drums energy at
    the onset). Returns ``(est, review_flags)``:
      * kicks with ``ratio >= remove_gate`` are REMOVED from est (kick lane only;
        NO onsets ever created — same invariant as remove_phantoms).
      * kicks with ``review_gate <= ratio < remove_gate`` are returned in
        ``review_flags`` (sorted onset seconds) — NOT removed; the caller surfaces
        them for user review.
    ``remove_gate=None`` disables removal; ``review_gate=None`` disables flagging.
    A missing/None drums stem or empty stems => passthrough (est unchanged, no
    flags). ``stems`` = dict {drums,vocals,bass,other -> mono np.array at ``sr``}.

    ``ratios_out`` (2026-07-30, diagnostic retention): pass a list and it is
    filled with one ``(time, ratio, verdict)`` tuple per kick, in ascending time
    order, where verdict is "removed" / "review" / "kept". The ratio is what
    decided each kick's fate and it was discarded on the next line — so a flagged
    kick reached the user with no severity attached, and a REMOVED one left no
    trace at all. Callers could not sort a review queue, tune a gate against real
    data, or answer "how bleed-y was it". Default None keeps the old behaviour
    EXACTLY (same 2-tuple return, no extra work). Opt-in; caller owns the list."""
    est = {k: (list(v) if not isinstance(v, list) else list(v)) for k, v in est_by_class.items()}
    kicks = np.sort(np.asarray(est.get("kick", []), dtype=float))
    review_flags = []
    if kicks.size and stems and stems.get("drums") is not None and (remove_gate is not None or review_gate is not None):
        ratios = np.asarray(cb.bleed_ratios(stems, sr, kicks.tolist()), dtype=float)
        remove_mask = (ratios >= remove_gate) if remove_gate is not None else np.zeros(len(kicks), bool)
        kept = kicks[~remove_mask]
        if review_gate is not None:
            lo = review_gate
            hi = remove_gate if remove_gate is not None else np.inf
            flag_mask = (ratios >= lo) & (ratios < hi)
            review_flags = sorted(float(t) for t in kicks[flag_mask])
        if ratios_out is not None:
            _rev = (ratios >= review_gate) if review_gate is not None \
                else np.zeros(len(kicks), bool)
            _rev = _rev & ~remove_mask
            ratios_out.extend(
                (float(t), float(r),
                 "removed" if rm else ("review" if rv else "kept"))
                for t, r, rm, rv in zip(kicks, ratios, remove_mask, _rev))
        kicks = np.sort(kept)
    est["kick"] = kicks
    return est, review_flags


def remove_bleed_phantoms(stems, sr, est_by_class, gate_ratio=BLEED_REMOVE_GATE):
    """Removal-only convenience wrapper around ``bleed_kick_pass`` (no review tier).
    Returns the est dict (kicks with ratio >= gate_ratio removed)."""
    est, _flags = bleed_kick_pass(stems, sr, est_by_class,
                                  remove_gate=gate_ratio, review_gate=None)
    return est
