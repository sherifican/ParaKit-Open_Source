"""
cleanup.py — top-level cleanup entry for the dependency-light sidecar.

clean_a2m_midi(midi_path, audio_path, do_cymbal=True, do_kick=True,
               model_dir=None, ...) :
  1. load the audio (features.load_audio — librosa),
  2. parse the MIDI to {lane: onsets} + per-note records (midi_io.parse_midi),
  3. run the enabled post-passes on the onset dict:
       - cymbal: passes.reclassify (asymmetric gate {to_ride:0.3, swap:0.7}),
       - kick:   passes.remove_phantoms (phantom P>=0.9),
     using NumpyRF models loaded from <model_dir>/parakit_{cymbal,kick}_cleanup.npz,
  4. rewrite midi_path IN PLACE preserving every surviving note's velocity+timing
     (cymbal relabels change pitch only; kick removals delete the note).

This reproduces the VALIDATED harness post-pass exactly (the faithfulness gate
in run_faithfulness_gate.py proves identical cleaned {lane: onsets}).

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
                   cymbal_gate=None, kick_gate=KICK_RECOMMENDED_GATE):
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
                  defaults to the package dir. The faithfulness gate passes the
                  harness ``results/`` dir.
    cymbal_gate : override the cymbal gate. None => the model's recommended
                  asymmetric QUALITY gate {gate_to_ride:0.3, gate_swap:0.7}
                  (matches the validated harness path).
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
        gate_kwargs = _cymbal_gate_kwargs(cymbal_gate)
        cleaned = passes.reclassify(y, sr, cleaned, npz_path=npz, **gate_kwargs)
        # normalize back to plain lists for downstream apply/compare
        cleaned = {lane: list(v) for lane, v in cleaned.items()}
        # Ride-detection toggle is authoritative: if ride is OFF, the user wants
        # NO rides, but the re-classifier can still promote onsets into ride
        # (gate_to_ride=0.3 is lenient). Fold those back into crash so the
        # cleanup never reintroduces rides — mirrors the detector's ride-OFF fold.
        if not allow_ride and cleaned.get("ride"):
            cleaned["crash"] = sorted(
                float(t) for t in (list(cleaned.get("crash", [])) + list(cleaned["ride"])))
            cleaned["ride"] = []

    if do_kick:
        npz = os.path.join(mdir, KICK_NPZ)
        cleaned = passes.remove_phantoms(y, sr, cleaned, npz_path=npz, gate=kick_gate)
        cleaned = {lane: list(v) for lane, v in cleaned.items()}

    # Rewrite the MIDI preserving velocities/timing.
    new_notes = midi_io.apply_cleanup(notes, cleaned, do_cymbal=do_cymbal, do_kick=do_kick)
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
        "cymbal_relabeled": cym_relabeled,
    }


def _cymbal_gate_kwargs(cymbal_gate):
    """Map the cymbal_gate arg -> reclassify kwargs.

    None  -> recommended asymmetric QUALITY {gate_to_ride:0.3, gate_swap:0.7};
    float -> symmetric gate on every move;
    dict  -> {gate_to_ride, gate_swap} passed through."""
    if cymbal_gate is None:
        return dict(gate_to_ride=RECOMMENDED_ASYM_QUALITY["gate_to_ride"],
                    gate_swap=RECOMMENDED_ASYM_QUALITY["gate_swap"])
    if isinstance(cymbal_gate, dict):
        return dict(gate_to_ride=cymbal_gate.get("gate_to_ride"),
                    gate_swap=cymbal_gate.get("gate_swap"),
                    gate=cymbal_gate.get("gate"))
    return dict(gate=float(cymbal_gate))


def _first_tempo(mid):
    """First set_tempo in the file, else the default — symmetric with parse_midi."""
    for track in mid.tracks:
        t = 0
        for msg in track:
            if msg.type == "set_tempo":
                return msg.tempo
    return midi_io.DEFAULT_TEMPO
