"""
Onset-centered spectral features for cymbal disambiguation (hi-hat / crash / ride).

Used by the cymbal re-classifier (a no-protected-edit POST-PASS). Extracts one
feature vector per onset from the audio, so a supervised classifier can relabel
the detector's lumped cymbal output. The SAME extractor is used for training (at
ground-truth onsets) and inference (at detected onsets).

Cymbals overlap in broadband HF; the discriminators (per the ADT research,
reports/RESEARCH_adt_cleanup_ml_2026-06-16.md §3) are spectral SHAPE
(centroid / rolloff / bandwidth / flatness), MFCC TIMBRE, and DECAY:
hi-hat = short/bright/fast-decay, crash = broadband/long-decay, ride =
sustained + bell ping. So we window AFTER each onset and capture spectral shape
+ an energy-decay slope + a high-frequency energy ratio.
"""
from __future__ import annotations

import numpy as np
import librosa

SR = 44100
WIN_S = 0.16          # 160 ms after the onset — attack + early decay
N_FFT = 512
HOP = 128
N_MFCC = 13

# 14 scalar features + MFCC mean(13) + MFCC std(13) = 40.
# NOTE (audit 2026-06-17): a 9-feature ride-targeted set (bell-ping tonality /
# multi-band HF / long-window sustain) was TRIED to break the crash<->ride wall and
# REVERTED — it did not help and slightly hurt (OOF ride 0.156->0.132); see
# cymbal_improve_eval.py + the record. The crash<->ride limit is feature-resistant on
# the current 101-track corpus; re-test ride features once the corpus is expanded.
FEATURE_NAMES = (
    "cent_mean", "cent_std", "roll_mean", "roll_std", "bw_mean", "bw_std",
    "flat_mean", "flat_std", "rms_mean", "rms_std", "zcr_mean", "zcr_std",
    "decay_slope", "hf_ratio",
) + tuple(f"mfcc{i}_mean" for i in range(N_MFCC)) + tuple(f"mfcc{i}_std" for i in range(N_MFCC))


def load_audio(path, sr=SR):
    y, _ = librosa.load(path, sr=sr, mono=True)
    return y, sr


def _seg_features(seg, sr):
    if seg.size < N_FFT:
        seg = np.pad(seg, (0, N_FFT - seg.size))
    S = np.abs(librosa.stft(seg, n_fft=N_FFT, hop_length=HOP)) + 1e-9
    cent = librosa.feature.spectral_centroid(S=S, sr=sr)[0]
    roll = librosa.feature.spectral_rolloff(S=S, sr=sr, roll_percent=0.85)[0]
    bw = librosa.feature.spectral_bandwidth(S=S, sr=sr)[0]
    flat = librosa.feature.spectral_flatness(S=S)[0]
    # RMS from the TIME-DOMAIN segment, NOT from the Hann-windowed STFT magnitude:
    # rms(S=...) on a windowed magnitude under-reads true energy (~0.62x, corr ~0.81)
    # and distorts the decay_slope fit — the key crash/ride discriminator (audit 2026-06-17).
    rms = librosa.feature.rms(y=seg, frame_length=N_FFT, hop_length=HOP)[0]
    zcr = librosa.feature.zero_crossing_rate(seg, frame_length=N_FFT, hop_length=HOP)[0]
    mfcc = librosa.feature.mfcc(y=seg, sr=sr, n_mfcc=N_MFCC, n_fft=N_FFT, hop_length=HOP)
    # energy-decay slope: linear fit of log-RMS over frames (negative = decaying)
    slope = float(np.polyfit(np.arange(rms.size), np.log(rms + 1e-9), 1)[0]) if rms.size >= 2 else 0.0
    # high-frequency energy ratio (>= 8 kHz) — hi-hat/crash are brighter than ride
    freqs = librosa.fft_frequencies(sr=sr, n_fft=N_FFT)
    hf = float(S[freqs >= 8000].sum() / (S.sum() + 1e-9))
    scal = [cent.mean(), cent.std(), roll.mean(), roll.std(), bw.mean(), bw.std(),
            flat.mean(), flat.std(), rms.mean(), rms.std(), zcr.mean(), zcr.std(),
            slope, hf]
    return np.array([float(x) for x in scal] + list(mfcc.mean(axis=1)) + list(mfcc.std(axis=1)),
                    dtype=float)


def extract_features(y, sr, onset_times, win_s=WIN_S):
    """Return (n_onsets, n_features) for the given onset times (seconds)."""
    win = int(win_s * sr)
    n = len(y)
    out = []
    for t in onset_times:
        i0 = int(float(t) * sr)
        seg = y[max(0, i0): min(n, i0 + win)]
        out.append(_seg_features(seg, sr))
    return np.asarray(out, dtype=float) if out else np.zeros((0, len(FEATURE_NAMES)))


# --- v4.5.5: decay-relationship features for the KICK phantom-remover ---------
# On top of the 40 spectral features above, the decay-augmented kick model adds 2
# RELATIONAL features vs the immediately-previous detected kick: dt_prev (time gap)
# and strength_ratio (this kick's rms_mean / the previous kick's rms_mean). A
# phantom double-trigger sits CLOSE to the prior kick AND WEAKER than it — the
# "decay of the previous kick" signal the owner hypothesized. Confirmed 3-way
# (deepseek/qwen/codex) at held-out kick-lane +0.006. This helper is SHARED by
# training and the sidecar inference so the 42-feature vector is byte-identical at
# both — the bit-exactness contract for the exported numpy model.
DECAY_FEATURE_NAMES = ("dt_prev", "strength_ratio")
RMS_MEAN_IDX = FEATURE_NAMES.index("rms_mean")   # == 8


def add_decay_features(kick_times_sorted, F):
    """Append (dt_prev, strength_ratio) to the (n, 40) matrix F -> (n, 42).

    ``kick_times_sorted`` MUST be ascending (both features are relative to the
    IMMEDIATELY-PREVIOUS kick) and in the SAME row order as F. The first kick has
    no predecessor -> its two decay values are 0.0. strength_ratio reuses F's
    rms_mean column (RMS_MEAN_IDX), so it needs no extra audio pass. Formula pinned
    to match every reconcile leg exactly:
        dt_prev[i]        = t[i] - t[i-1]                     (0.0 at i=0)
        strength_ratio[i] = rms[i] / max(rms[i-1], 1e-9)     (0.0 at i=0)
    """
    t = np.asarray(kick_times_sorted, dtype=float)
    F = np.asarray(F, dtype=float)
    n = F.shape[0]
    dt_prev = np.zeros(n, dtype=float)
    strength_ratio = np.zeros(n, dtype=float)
    if n >= 2:
        rms = F[:, RMS_MEAN_IDX]
        dt_prev[1:] = t[1:] - t[:-1]
        strength_ratio[1:] = rms[1:] / np.maximum(rms[:-1], 1e-9)
    return np.hstack([F, dt_prev[:, None], strength_ratio[:, None]])


if __name__ == "__main__":
    # smoke demo: separation of hi-hat vs crash vs ride features on a real track
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import loaders
    track = r"C:\Users\micah\PROJECTS & SIDE HUSSTLES\ParaKit\samples_for_det_imp\drums only audio\paradb_corpus\all_i_want_-_a_day_to_remember\drums.flac"
    gtmid = r"C:\Users\micah\PROJECTS & SIDE HUSSTLES\ParaKit\samples_for_det_imp\curated\paradb_corpus\all_i_want_-_a_day_to_remember\ground_truth.mid"
    y, sr = load_audio(track)
    gt = loaders.load_midi(gtmid)
    ci = {n: FEATURE_NAMES.index(n) for n in ("cent_mean", "decay_slope", "hf_ratio")}
    print(f"{'class':<8} {'n':>4} | cent_mean   decay_slope   hf_ratio")
    for c in ("hihat", "crash", "ride"):
        ons = gt.get(c, [])[:40]
        if len(ons) == 0:
            print(f"{c:<8} {0:>4} | (none)"); continue
        F = extract_features(y, sr, ons)
        print(f"{c:<8} {len(ons):>4} | {F[:,ci['cent_mean']].mean():>9.0f}   "
              f"{F[:,ci['decay_slope']].mean():>10.3f}   {F[:,ci['hf_ratio']].mean():>7.3f}")
