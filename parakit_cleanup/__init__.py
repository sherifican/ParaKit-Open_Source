"""
parakit_cleanup — dependency-light (numpy + librosa + mido) Audio->MIDI cleanup
sidecar for ParaKit.

Reproduces the VALIDATED detection-harness post-pass EXACTLY with NO sklearn /
joblib / onnx at runtime:
  - cymbal re-classifier: relabel hihat/crash/ride lanes (count-preserving),
  - kick phantom-remover: delete false-positive kicks (kick lane only).

The trained models ship as dependency-free .npz (numpy_rf.NumpyRF, bit-exact to
the sklearn originals). Feature extraction (features.py) and the gate logic
(passes.py) are faithful ports of the harness; the faithfulness gate proves the
sidecar yields identical cleaned {lane: onsets} to the harness path.

Public API:
    clean_a2m_midi(midi_path, audio_path, do_cymbal=True, do_kick=True,
                   model_dir=None, ...)
"""
from __future__ import annotations

from .cleanup import clean_a2m_midi

__all__ = ["clean_a2m_midi"]
__version__ = "1.0.0-b1"
