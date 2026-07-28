"""F12 cross-stem bleed scorer, validated 2026-07-20 (AUROC 0.77 for phantom kicks; orthogonal to the decay kick remover)."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Mapping

import librosa
import numpy as np

WIN_LO, WIN_HI = -0.005, 0.045   # onset energy window (sec)
EPS = 1e-9

_STEM_KEYS = ("drums", "vocals", "bass", "other")
_NON_DRUM_KEYS = ("vocals", "bass", "other")


def onset_rms(y: np.ndarray | None, sr: float, t: float) -> float:
    """RMS energy of mono stem ``y`` in [t+WIN_LO, t+WIN_HI], plus EPS.

    Missing/None stems return 0.0 (no energy). Empty window returns 0.0.
    Faithful port of run_f12.rms.
    """
    if y is None:
        return 0.0
    i0 = max(0, int((t + WIN_LO) * sr))
    i1 = min(len(y), int((t + WIN_HI) * sr))
    if i1 <= i0:
        return 0.0
    seg = y[i0:i1]
    return float(np.sqrt(np.mean(seg * seg)) + EPS)


def bleed_ratios(
    stems: Mapping[str, np.ndarray | None],
    sr: float,
    kick_times: Iterable[float],
) -> np.ndarray:
    """Cross-stem bleed ratio at each kick onset.

    ratio = max(onset_rms(vocals), onset_rms(bass), onset_rms(other))
            / (onset_rms(drums) + EPS)

    High ratio ⇒ non-drum energy dominates at the onset (likely phantom kick
    from bass/other bleed). Missing/None stems contribute 0 RMS.
    Empty kick_times → empty float array.
    """
    times = list(kick_times)
    if not times:
        return np.asarray([], dtype=float)

    y_drums = stems.get("drums")
    out = np.empty(len(times), dtype=float)
    for i, t in enumerate(times):
        ed = onset_rms(y_drums, sr, t)
        nd = max(
            onset_rms(stems.get(k), sr, t) for k in _NON_DRUM_KEYS
        )
        out[i] = nd / (ed + EPS)
    return out


def load_stems(stem_dir: str | Path, sr: float) -> dict[str, np.ndarray]:
    """Load demucs-style ``{drums,vocals,bass,other}.wav`` from ``stem_dir``.

    Uses ``librosa.load(path, sr=sr, mono=True)``. Missing files are skipped
    (key absent from the returned dict).
    """
    stem_dir = Path(stem_dir)
    out: dict[str, np.ndarray] = {}
    for key in _STEM_KEYS:
        path = stem_dir / f"{key}.wav"
        if not path.is_file():
            continue
        y, _ = librosa.load(str(path), sr=sr, mono=True)
        out[key] = y
    return out
