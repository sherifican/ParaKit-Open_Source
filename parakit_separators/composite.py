"""Stem → drums-equivalent composite mixer (F-INT-001 v4.4.4).

After a separator produces per-class stem WAVs, _a2m_do_convert needs a
single drums-only audio file to feed into the existing hybrid detection
path. compose() sums the named subset of stems with peak-normalized
clipping protection and writes one wav.

The summed file is structurally equivalent to the original drums.flac the
hybrid detector already expects, so detection logic doesn't change at all
— only the input audio gets pre-cleaned by the separator first.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, Optional


def compose(stem_paths: Dict[str, Path],
            classes_to_mix: Iterable[str],
            output_path: Path,
            target_sr: Optional[int] = None) -> Path:
    """Mix the requested classes into a single composite WAV at output_path.

    Args:
        stem_paths: dict from canonical class name (e.g. 'kick') to the
            WAV path produced by the separator. Extra keys are ignored;
            missing keys (vs classes_to_mix) are skipped silently.
        classes_to_mix: iterable of canonical class names to include in
            the composite. Typically every class except 'ride' (since
            production hybrid handles ride from the original audio and
            re-feeding would double-count).
        output_path: destination WAV file (parent dir created if needed).
        target_sr: sample rate for the composite. None → use the SR of
            the first stem read. Mismatched-SR stems are resampled to
            target_sr via librosa (only loaded if needed; not imported
            unless a resample is required, since librosa is heavy).

    Returns:
        output_path on success.

    Raises:
        ValueError if no stems matched classes_to_mix, OR if unreadable
        stems reduce the composite to a single stem (the skipped classes
        are named in the message — this is how the skip reaches the A2M
        log: _a2m_do_convert's F-INT-001 wrap logs the exception text and
        falls back to default detection).
        OSError on read/write failure (caller decides whether to fall
        back to default hybrid path).
    """
    import numpy as np
    import soundfile as sf

    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Filter to the requested classes that actually have stems on disk
    selected: list[tuple[str, Path]] = [
        (cls, Path(stem_paths[cls]).resolve())
        for cls in classes_to_mix
        if cls in stem_paths and Path(stem_paths[cls]).is_file()
    ]
    if not selected:
        raise ValueError(
            f"compose(): no stems found for any class in {list(classes_to_mix)} "
            f"(stem_paths keys: {sorted(stem_paths.keys())}).")

    # Read the first stem to seed buffer shape + sample rate
    first_cls, first_path = selected[0]
    first_audio, first_sr = sf.read(str(first_path), dtype="float32",
                                     always_2d=False)
    if target_sr is None:
        target_sr = int(first_sr)
    if first_sr != target_sr:
        first_audio = _resample_if_needed(first_audio, first_sr, target_sr)
    summed = first_audio.astype("float32", copy=True)

    # Sum the rest. Mismatched lengths get truncated to the shortest stem
    # — they SHOULD all be the same length (separator output for one input)
    # but defensive truncation prevents a runtime crash if not.
    skipped: list[str] = []
    for cls, path in selected[1:]:
        try:
            audio, sr = sf.read(str(path), dtype="float32", always_2d=False)
        except Exception as exc:
            # Skip an unreadable stem rather than failing the whole compose —
            # but RECORD it; a silent skip degrades detection invisibly.
            skipped.append(f"{cls} ({exc.__class__.__name__})")
            continue
        if sr != target_sr:
            audio = _resample_if_needed(audio, sr, target_sr)
        # Normalize shape — both buffers should be 1-D for mono or N×2 for
        # stereo. If shapes mismatch (e.g. mono vs stereo stems from a
        # misbehaving separator), downcast everything to mono.
        if summed.ndim != audio.ndim:
            if summed.ndim == 2:
                summed = summed.mean(axis=1)
            if audio.ndim == 2:
                audio = audio.mean(axis=1)
        # Truncate to shortest
        n = min(len(summed), len(audio))
        summed = summed[:n] + audio[:n]

    if skipped:
        # Best-effort console trace only (dev runs / frozen exe swallows it).
        # VERIFIED 2026-07-06: _a2m_do_convert's F-INT-001 separator wrap does
        # NOT capture stdout — everything reaches the A2M log via explicit
        # self._a2m_log(...) calls or the except-block's exception text. So
        # the load-bearing reporting channel is the raise below, not this.
        print(f"[parakit_separators.compose] WARNING: unreadable stems "
              f"skipped: {', '.join(skipped)}", flush=True)
        if len(skipped) >= len(selected) - 1:
            # The composite has been reduced to a single stem — almost
            # certainly wrong. Raise so the caller's except logs the skipped
            # classes to the A2M log and falls back to default detection on
            # the original audio (the safe path).
            raise ValueError(
                f"compose(): composite reduced to a single stem "
                f"('{first_cls}') — unreadable stems skipped: "
                f"{', '.join(skipped)}.")

    # Peak-normalize protection: if the sum exceeds [-1, 1], scale down so
    # the max abs sample is 0.99. Detection is invariant to overall gain
    # (uses onset envelopes + spectral features, not absolute level), so
    # this doesn't hurt detection accuracy and prevents clipped output.
    peak = float(abs(summed).max()) if summed.size else 0.0
    if peak > 0.99:
        summed = summed * (0.99 / peak)

    sf.write(str(output_path), summed, int(target_sr), subtype="FLOAT")
    return output_path


def _resample_if_needed(audio, src_sr: int, dst_sr: int):
    """Resample only if SRs actually differ. librosa is imported lazily so
    callers that only pass same-SR stems don't pay the import cost."""
    if src_sr == dst_sr:
        return audio
    import librosa
    import numpy as np
    if audio.ndim == 1:
        return librosa.resample(audio.astype(np.float32),
                                orig_sr=src_sr, target_sr=dst_sr)
    # Stereo: resample each channel
    chans = [librosa.resample(audio[:, c].astype(np.float32),
                              orig_sr=src_sr, target_sr=dst_sr)
             for c in range(audio.shape[1])]
    import numpy as np
    return np.stack(chans, axis=1)
