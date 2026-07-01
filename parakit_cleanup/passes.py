"""
passes.py — dependency-light port of the harness cleanup post-passes.

Faithful re-implementation of:
  - tools/detection_harness/cymbal_postpass.py  (cymbal re-classifier)
  - tools/detection_harness/kick_postpass.py    (kick phantom-remover)

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

import numpy as np

from . import features as cf
from .numpy_rf import NumpyRF

# ---- cymbal post-pass constants (mirror cymbal_postpass.py) -----------------
CYM_LANES = ("hihat", "crash", "ride")
# The harness defaults reclassify() to all-None (ungated audited result) but the
# faithfulness gate / production path use the asymmetric QUALITY gate that the
# exported model recommends: {gate_to_ride: 0.3, gate_swap: 0.7}.
RECOMMENDED_ASYM_QUALITY = {"gate_to_ride": 0.3, "gate_swap": 0.7}

# ---- kick post-pass constants (mirror kick_postpass.py) ---------------------
KICK_PHANTOM_LABEL = 0
KICK_RECOMMENDED_GATE = 0.9


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


def assign(pairs, F, model, classes, gate=None, gate_to_ride=None, gate_swap=None):
    """Core cymbal relabeling step — verbatim from cymbal_postpass.assign.

    ``pairs`` = [(onset_seconds, original_lane), ...] and ``F`` = the feature
    matrix in the SAME order. Returns {lane: [onsets]} for the three cymbal
    lanes. Onset COUNT is preserved (every pair lands in exactly one lane)."""
    out = {lane: [] for lane in CYM_LANES}
    if not pairs:
        return out
    gated = gate is not None or gate_to_ride is not None or gate_swap is not None
    if gated and hasattr(model, "predict_proba"):
        proba = model.predict_proba(F)
        # predict_proba columns are ordered by model.classes_ (the integer labels
        # the model was trained on); map column index -> class-name via classes[].
        mc = list(getattr(model, "classes_", range(proba.shape[1])))
        for (t, orig), row in zip(pairs, proba):
            j = int(np.argmax(row)); p = float(row[j])
            lab = classes[int(mc[j])]
            if lab != orig and p < _required_prob(orig, lab, gate, gate_to_ride, gate_swap):
                lab = orig  # not confident enough to override the detector — keep its lane
            out[lab].append(t)
    else:
        preds = model.predict(F)
        for (t, _orig), pr in zip(pairs, preds):
            out[classes[int(pr)]].append(t)
    return out


def reclassify(y, sr, est_by_class, model=None, classes=None, npz_path=None,
               gate=None, gate_to_ride=None, gate_swap=None):
    """Return a new est dict with cymbal-lane onsets relabeled — verbatim port of
    cymbal_postpass.reclassify (joblib swapped for NumpyRF).

    The set of cymbal onsets is preserved (no onsets created/destroyed) — only
    their hi-hat/crash/ride labels change. Non-cymbal lanes pass through."""
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
                          gate_to_ride=gate_to_ride, gate_swap=gate_swap)
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
                 gate=KICK_RECOMMENDED_GATE):
    """Core removal step — verbatim from kick_postpass.filter_kicks. ``times`` =
    detected kick onset seconds, ``F`` = features in the SAME order. Returns the
    KEPT onsets (sorted) — a kick is DROPPED iff P(phantom) >= gate. gate=None =>
    keep all. NEVER creates onsets."""
    times = np.asarray(times, dtype=float)
    if times.size == 0 or gate is None:
        return np.sort(times)
    pph = phantom_proba(F, model, phantom_label)
    keep = pph < gate
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
