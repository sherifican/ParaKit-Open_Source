"""
cleanup.py — top-level cleanup entry for the dependency-light sidecar.

clean_a2m_midi(midi_path, audio_path, do_cymbal=True, do_kick=True,
               model_dir=None, ...) :
  1. load the audio (features.load_audio — librosa),
  2. parse the MIDI to {lane: onsets} + per-note records (midi_io.parse_midi),
  3. run the enabled post-passes on the onset dict:
       - cymbal: passes.reclassify (gate_to_ride None, gate_swap 0.7,
                 ride_floor 0.425 -- see RECOMMENDED_ASYM_QUALITY, and note
                 ride_floor is FORCED OFF when allow_ride is False),
       - kick:   passes.remove_phantoms (phantom P>=0.9),
     using NumpyRF models loaded from <model_dir>/parakit_{cymbal,kick}_cleanup.npz,
  4. rewrite midi_path IN PLACE preserving every surviving note's velocity+timing
     (cymbal relabels change pitch only; kick removals delete the note).

This reproduced the VALIDATED harness post-pass exactly up to 2026-08-07, when
`ride_floor` was enabled and the default path became deliberately DIFFERENT from
that baseline. (The gate it cited, run_faithfulness_gate.py, is also no longer in
the tree -- do not cite it as a live check.) With allow_ride=False the old
equivalence still holds, because ride_floor is forced off on that path.

Imports: numpy + the package's own features / passes / midi_io. No sklearn /
joblib / onnx.
"""
from __future__ import annotations

import os
import sys

from . import features as cf
from . import passes
from . import midi_io
from .passes import RECOMMENDED_ASYM_QUALITY, KICK_RECOMMENDED_GATE

# package directory — default home of the bundled .npz models.
_PKG_DIR = os.path.dirname(os.path.abspath(__file__))

CYMBAL_NPZ = "parakit_cymbal_cleanup.npz"
KICK_NPZ = "parakit_kick_cleanup.npz"


def _resolve_model_dir(model_dir):
    """Where the .npz live. Explicit ``model_dir`` wins; else search the package
    dir (source-run + bundled-into-package) and a PyInstaller ``_MEIPASS`` fallback
    (frozen build, where the .spec datas land under ``parakit_cleanup/``)."""
    if model_dir:
        return os.path.abspath(model_dir)
    cands = [_PKG_DIR]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        cands += [os.path.join(meipass, "parakit_cleanup"), meipass]
    for c in cands:
        if os.path.isfile(os.path.join(c, CYMBAL_NPZ)):
            return c
    return _PKG_DIR  # fall back to the package dir (raises a clear error if missing)


def clean_a2m_midi(midi_path, audio_path, do_cymbal=True, do_kick=True,
                   allow_ride=True,
                   model_dir=None,
                   cymbal_gate=None, kick_gate=KICK_RECOMMENDED_GATE,
                   stems=None, do_bleed=False, bleed_gate=None, bleed_review_gate=None):
    """Clean an Audio->MIDI drum MIDI in place using the audio.

    Parameters
    ----------
    midi_path   : path to the .mid to clean (REWRITTEN IN PLACE).
    audio_path  : path to the source audio (flac/wav/ogg/mp3) the MIDI came from.
    do_cymbal   : run the cymbal re-classifier (relabel hihat/crash/ride).
    do_kick     : run the kick phantom-remover (delete false kicks).
    allow_ride  : honor the app's Ride-detection toggle. True (default) keeps the
                  validated behavior. When False (ride detection OFF), any onset
                  the cymbal pass assigns to the ride lane is folded back into
                  crash — so the cleanup can never reintroduce rides the user
                  turned off. Mirrors the detector's own ride-OFF fold
                  (ParaKit v4.0.py: ``crash += ride; ride = []``). Only affects
                  the cymbal pass; no-op when do_cymbal is False.
    model_dir   : dir holding parakit_{cymbal,kick}_cleanup.npz (+ .json sidecars);
                  defaults to the package dir.
    cymbal_gate : override the cymbal gate. None => RECOMMENDED_ASYM_QUALITY
                  {gate_to_ride: None, gate_swap: 0.7, ride_floor: 0.425}.
                  ⚠ A bare float selects a symmetric gate and carries NO
                  ride_floor, silently disabling the shipped lever.
    kick_gate   : min P(phantom) to drop a kick (default 0.9, recall-safe).

    Returns
    -------
    dict summary: {"cleaned_est": {lane: list}, "n_notes_before", "n_notes_after",
                   "n_kicks_removed", "cymbal_relabeled": {...}}.
    """
    mdir = _resolve_model_dir(model_dir)
    y, sr = cf.load_audio(audio_path)

    est, notes = midi_io.parse_midi(midi_path)
    n_before = len(notes)
    est_in = {lane: list(v) for lane, v in est.items()}

    cleaned = {lane: list(v) for lane, v in est.items()}

    if do_cymbal:
        npz = os.path.join(mdir, CYMBAL_NPZ)
        gate_kwargs = _cymbal_gate_kwargs(cymbal_gate, allow_ride=allow_ride)
        cleaned = passes.reclassify(y, sr, cleaned, npz_path=npz, **gate_kwargs)
        # normalize back to plain lists for downstream apply/compare
        cleaned = {lane: list(v) for lane, v in cleaned.items()}
        cleaned = _fold_rides_to_crash(cleaned, allow_ride)

    if do_kick:
        npz = os.path.join(mdir, KICK_NPZ)
        cleaned = passes.remove_phantoms(y, sr, cleaned, npz_path=npz, gate=kick_gate)
        cleaned = {lane: list(v) for lane, v in cleaned.items()}

    # Cross-stem bleed kick pass (F12) — opt-in, orthogonal to the decay RF above.
    # Needs the 4 demucs stems; caller passes `stems` (a dict of arrays, or a
    # demucs output dir). Default OFF => this block is skipped and the output is
    # byte-identical to the pre-bleed cleanup.
    n_bleed_removed = 0
    bleed_review_flags = []
    if do_bleed and stems is not None:
        if isinstance(stems, (str, os.PathLike)):
            from . import bleed as _cb
            stems = _cb.load_stems(stems, sr)
        _rgate = bleed_gate if bleed_gate is not None else passes.BLEED_REMOVE_GATE
        _vgate = bleed_review_gate if bleed_review_gate is not None else passes.BLEED_REVIEW_GATE
        _kicks_before = len(cleaned.get("kick", []))
        cleaned, bleed_review_flags = passes.bleed_kick_pass(
            stems, sr, cleaned, remove_gate=_rgate, review_gate=_vgate)
        cleaned = {lane: list(v) for lane, v in cleaned.items()}
        n_bleed_removed = max(0, _kicks_before - len(cleaned.get("kick", [])))

    # Rewrite the MIDI preserving velocities/timing. Bleed removals are kick-lane
    # edits, so honor them even when the decay kick pass (do_kick) is off.
    new_notes = midi_io.apply_cleanup(notes, cleaned, do_cymbal=do_cymbal,
                                      do_kick=(do_kick or n_bleed_removed > 0))
    mid = midi_io.mido.MidiFile(midi_path)
    tpb = mid.ticks_per_beat or midi_io.DEFAULT_TPB
    tempo = _first_tempo(mid)
    midi_io.write_midi(midi_path, new_notes, ticks_per_beat=tpb, tempo=tempo)

    n_kicks_removed = max(0, len(est_in.get("kick", [])) - len(cleaned.get("kick", [])))
    cym_relabeled = {lane: len(cleaned.get(lane, [])) - len(est_in.get(lane, []))
                     for lane in midi_io.CYM_LANES} if do_cymbal else {}

    return {
        "cleaned_est": cleaned,
        "n_notes_before": n_before,
        "n_notes_after": len(new_notes),
        "n_kicks_removed": n_kicks_removed,
        "n_bleed_kicks_removed": n_bleed_removed,
        "bleed_review_flags": bleed_review_flags,
        "cymbal_relabeled": cym_relabeled,
    }


def _fold_rides_to_crash(cleaned, allow_ride):
    """Ride detection OFF => the ride lane is emptied into crash.

    The ride toggle is authoritative: if the user turned rides off they want NO
    rides, but the re-classifier can still put onsets in the ride lane on its own
    argmax. Mirrors the detector's own ride-OFF fold (``crash += ride; ride = []``).

    `ride_floor` is forced off upstream for this case (`_cymbal_gate_kwargs`), so
    what reaches here is only argmax-ride, never a PROMOTED hi-hat. Those are two
    separate defences and they cover different holes: the upstream one stops a
    hi-hat becoming a crash, this one stops an argmax-ride surviving into a
    rides-off chart.

    ⛔ EXTRACTED FROM `clean_a2m_midi` SO IT CAN BE TESTED WITHOUT AUDIO. Inline,
    nothing executed it — deleting the block left INV97, INV100 and the whole suite
    green while ride notes shipped into a chart the user asked to have none, which
    is the sibling of the very defect this round fixed. A guard that inspects the
    kwargs helper and greps for a call site does not cover the transform itself.
    Pinned by INV100."""
    if allow_ride or not cleaned.get("ride"):
        return cleaned
    cleaned["crash"] = sorted(
        float(t) for t in (list(cleaned.get("crash", [])) + list(cleaned["ride"])))
    cleaned["ride"] = []
    return cleaned


def _cymbal_gate_kwargs(cymbal_gate, allow_ride=True):
    """Map the cymbal_gate arg -> reclassify kwargs.

    None  -> RECOMMENDED_ASYM_QUALITY (gate_to_ride None, gate_swap 0.7,
             ride_floor 0.425);
    float -> symmetric gate on every move;
    dict  -> {gate_to_ride, gate_swap, ride_floor} passed through.

    ⛔ ``allow_ride=False`` FORCES ``ride_floor`` OFF, and that is a correctness
    fix, not a tidy-up. ``ride_floor`` PROMOTES an onset into the ride lane even
    where another class is argmax (passes.py ``assign``). With ride detection
    turned off, the caller then folds the whole ride lane into CRASH — so a hi-hat
    the model scored at P(ride)>=0.425 came out as a **crash note**, where before
    this feature existed it stayed a hi-hat. Paradiddle is VR: that sends the
    player's arm to a different physical object, which is exactly the wrong-lane
    harm every cap in the tau decision was built to bound — on an arm no sweep
    ever measured, since every measurement behind 0.425 ran with rides allowed.

    Turning the lever off here (rather than teaching the fold to restore the
    original lane) buys a property worth having: with ride detection OFF the
    cleanup is byte-identical to its pre-``ride_floor`` self. A ride-recall lever
    should do nothing when the user has said they want no rides.

    Found by an independent audit 2026-08-08, not by the sweep; pinned by INV100.

    ⚠ This maps keys BY NAME rather than splatting the config, so a key added to
    RECOMMENDED_ASYM_QUALITY and not added here is silently inert — it would look
    like a setting and behave like nothing, which is exactly the defect INV97
    exists to catch. `ride_floor` is named in all three branches for that reason.
    (The docstring also said gate_to_ride was 0.3 long after it became None; it
    was inert at 0.3 anyway, because a three-class argmax is always >= 1/3.)"""
    if cymbal_gate is None:
        kw = dict(gate_to_ride=RECOMMENDED_ASYM_QUALITY["gate_to_ride"],
                  gate_swap=RECOMMENDED_ASYM_QUALITY["gate_swap"],
                  ride_floor=RECOMMENDED_ASYM_QUALITY.get("ride_floor"))
    elif isinstance(cymbal_gate, dict):
        kw = dict(gate_to_ride=cymbal_gate.get("gate_to_ride"),
                  gate_swap=cymbal_gate.get("gate_swap"),
                  ride_floor=cymbal_gate.get("ride_floor"),
                  gate=cymbal_gate.get("gate"))
    else:
        # ⚠ The float branch carries NO ride_floor, so passing a bare float for
        # cymbal_gate silently disables the shipped lever. Nothing in the app does
        # that today (clean_a2m_midi is called without cymbal_gate), but INV100
        # pins it so a future caller cannot turn the feature off by accident.
        kw = dict(gate=float(cymbal_gate))
    if not allow_ride:
        kw["ride_floor"] = None
    return kw


def _first_tempo(mid):
    """First set_tempo in the file, else the default — symmetric with parse_midi."""
    for track in mid.tracks:
        t = 0
        for msg in track:
            if msg.type == "set_tempo":
                return msg.tempo
    return midi_io.DEFAULT_TEMPO
