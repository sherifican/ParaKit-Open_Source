"""
parakit_onset_align.py — measure a chart's timing against the ACTUAL AUDIO.

WHY THIS EXISTS
---------------
ParaKit has three ways to ask "is this chart's timing wrong?", in increasing
order of strength:

  1. Coverage (last note vs audio duration). Weakest. It cannot tell a long
     outro from a defect, so it now only speaks when a chart covers under
     ~55% of the audio, and it does not claim to measure drift.
  2. The .rlrr affine fit. Strong, but needs a paired .rlrr chart file, which
     most MIDIs do not have (3 of 103 in the reference corpus).
  3. THIS. Aligns the chart against the song itself. It needs only audio, so
     it reaches every chart with a drum stem (73 of 103 in the same corpus),
     and it measures against the thing that actually matters.

METHOD — deliberately correspondence-free
-----------------------------------------
A chart is an INTERPRETATION of a performance, not a transcription of it:
ghost notes get dropped, flams get simplified, hits get added. So this never
tries to match note k to onset k. Instead:

  * `env`  — an onset-strength envelope of the drum stem.
  * `imp`  — an impulse train from the chart, one impulse per distinct strike.
  * For each candidate time-scale `a`, warp the impulses, cross-correlate
    against `env`, and keep the best lag and its score.

Missing and extra notes then cost a little correlation instead of breaking a
correspondence, and the SHAPE of the score surface becomes the confidence
measure. That shape is the whole safety story, so it is gated hard.

WHAT IT REPORTS, AND WHAT IT REFUSES TO
---------------------------------------
It reports a RATE difference only. A constant lag is never a defect: measured
on the reference corpus, five of thirty songs sit 0.8-2.7 s off their stems at
a perfect scale, and a probe against lag=0 confirmed those offsets are real
rather than a periodicity artifact. Charts are routinely authored against
differently-trimmed audio, and the app has its own offset control. Warning on
a lag would have false-alarmed on one song in six.

It also refuses far more often than it answers. `status` is "unknown" unless
every gate passes, and "unknown" is never shown to a user as a finding.

⛔ THE LIMIT THAT SHAPES THIS WHOLE MODULE — IT CANNOT CHECK THE PAIRING
------------------------------------------------------------------------
The statistics measure whether a chart and an audio file share a time-scale.
They CANNOT tell you the audio is the right song. Measured, not argued: every
chart in a 30-song corpus was run against all 29 other songs' stems, 870
deliberately wrong pairings.

  * 60 of 870 (6.9%) passed the confidence gate.
  * 44 of 870 (5.1%) went further and would have raised a false
    "your chart is drifted" warning.

Two rescue statistics were tried and BOTH failed:
  * absolute correlation score — worst true pair 0.4046, best wrong pair
    0.4684. A wrong pairing outscored a right one.
  * per-window rate agreement — worst true pair 0.0015, best wrong pair
    0.0000. Two songs near the same BPM agree about the rate in every window,
    because both are metronomic. This is exactly why it fails: consistency is
    evidence of a steady pulse, not of a shared origin.

So the pairing must be corroborated from OUTSIDE the statistics. `analyse()`
takes `pairing_is_corroborated` and refuses unless the caller can vouch for
it — the user chose the file, or it was matched to the chart by name. Within a
correct pairing the measurement is excellent; it simply cannot establish that
premise for itself, and it does not pretend to.

MEASUREMENTS BEHIND THE CONSTANTS (reference corpus, drums-only stems)
---------------------------------------------------------------------
  * 30/30 correctly-paired charts returned a = 1.0000, and none would warn.
  * Peak prominence: matched 4.68x-17.68x (median 9.97x). Mismatched: median
    1.58x but a long tail to 14.32x -- hence the limit above. An early
    10-pair sample suggested clean separation at 4x; the full 870 showed that
    was a sampling artifact.
  * Injected +-0.4% drift landed on the correct grid node. The grid step is
    0.0005 = 0.05%, so 0.05% is the RESOLUTION. A +-0.05% half-width is one
    grid interval, i.e. the sharpest peaks are under-resolved -- do not read
    the recovery residual as estimator precision.
  * A 1% tempo RAMP (no single true scale) collapsed prominence to 2.4-4.4x
    and widened the peak to +-0.28-0.45%, so the gates refuse it.

CAVEAT: one user's library, click-aligned game charts, known-good only. Thirty
songs with zero failures bounds the false-positive rate near 10% at 95%
confidence by the rule of three, and songs from one collection are correlated.
A live take averaging 120.5 BPM against a 120 BPM chart is a legitimately
correct file that would read as a 0.4% rate error; no such file was in the
corpus. Treat every threshold here as a calibrated pilot, not a guarantee.
"""

from __future__ import annotations

# --- tunables, all justified in the module docstring ----------------------
SR = 22050            # analysis rate; drums need no more
HOP = 512             # ~23 ms frames
MAX_LAG_S = 5.0       # widest plausible authoring offset
SCALE_LO, SCALE_HI, SCALE_STEP = 0.980, 1.020, 0.0005

MIN_ONSETS = 50       # fewer than this is not evidence, it is a guess
MIN_DURATION_S = 60.0
MIN_PROMINENCE = 4.0  # matched >=4.66x, mismatched <=2.49x -- the gap
MAX_HALFWIDTH_PCT = 0.15
MIN_COVERAGE = 3      # onsets must appear in early, middle AND late thirds
MIN_NEFF = 30.0       # effective sample size; stops a few crashes dominating
WINDOW_COUNT = 4      # for the per-window consistency check
MAX_WINDOW_SLOPE_SPREAD = 0.004   # windows must agree on the rate

RATE_FLOOR = 0.003            # |a-1| below this is not called a rate problem
MIN_DISPLACEMENT_S = 0.30     # ...and it must also matter across the song


def _impulses_from_notes(times, weights=None):
    """Collapse simultaneous strikes into one weighted impulse each."""
    import numpy as np
    if times is None or len(times) == 0:
        return np.array([]), np.array([])
    times = np.asarray(times, dtype=float)
    weights = (np.ones(len(times), dtype=float) if weights is None
               else np.asarray(weights, dtype=float))
    order = np.argsort(times)
    times, weights = times[order], weights[order]
    keys = np.round(times, 3)                      # 1 ms == the same strike
    uniq, inv = np.unique(keys, return_inverse=True)
    acc = np.zeros(len(uniq))
    np.add.at(acc, inv, weights)
    # SQRT of the summed velocity, not the raw sum. Weighting by velocity at
    # all is right -- an onset-strength envelope really is dominated by kicks
    # and crashes, so unweighted 16ths would be a representation mismatch --
    # but a raw sum puts most of the L2 mass in a handful of stacked downbeats
    # and lets them steer the whole fit. It also inherits whatever the
    # converter happened to write for velocity, which may be flat.
    return uniq, np.sqrt(np.maximum(acc, 0.0))


def _load_envelope(stem_path):
    """Onset-strength envelope, mean-removed and L2-normalised."""
    import numpy as np
    import librosa                       # lazy: matches the app's convention
    y, _sr = librosa.load(stem_path, sr=SR, mono=True)
    if len(y) < SR * 5:
        return None, 0.0
    env = librosa.onset.onset_strength(y=y, sr=SR, hop_length=HOP)
    env = env - env.mean()
    norm = float(np.linalg.norm(env))
    if norm <= 0:
        return None, len(y) / float(SR)
    return env / norm, len(y) / float(SR)


def _best_lag_score(env, times, weights, a, max_lag_frames):
    """Correlate the a-warped impulse train against env; best lag + score."""
    import numpy as np
    from scipy.signal import fftconvolve
    n = len(env)
    idx = (times * a * SR / HOP + 0.5).astype("int64")
    keep = (idx >= 0) & (idx < n)
    if int(keep.sum()) < 20:
        return -1.0, 0.0
    imp = np.zeros(n)
    np.add.at(imp, idx[keep], weights[keep])
    norm = float(np.linalg.norm(imp))
    if norm <= 0:
        return -1.0, 0.0
    imp /= norm
    full = fftconvolve(env, imp[::-1], mode="full")
    centre = len(imp) - 1
    lo = max(0, centre - max_lag_frames)
    hi = min(len(full), centre + max_lag_frames + 1)
    seg = full[lo:hi]
    j = int(seg.argmax())
    return float(seg[j]), float((lo + j - centre) * HOP / SR)


def _scan(env, times, weights, grid):
    import numpy as np
    mlf = int(MAX_LAG_S * SR / HOP)
    scores = np.empty(len(grid))
    lags = np.empty(len(grid))
    for i, a in enumerate(grid):
        scores[i], lags[i] = _best_lag_score(env, times, weights, a, mlf)
    return scores, lags


def _peak_shape(grid, scores):
    """(index, prominence, half-width%) of the winning peak."""
    import numpy as np
    k = int(scores.argmax())
    med = float(np.median(scores))
    prom = float(scores[k] / med) if med > 0 else float("inf")
    half = med + 0.5 * (scores[k] - med)
    lo = k
    while lo > 0 and scores[lo] > half:
        lo -= 1
    hi = k
    while hi < len(scores) - 1 and scores[hi] > half:
        hi += 1
    return k, prom, float((grid[hi] - grid[lo]) * 100.0 / 2.0)


def analyse(note_times, note_weights, stem_path, pairing_is_corroborated=False):
    """Measure the chart's time-scale against the drum stem.

    ``pairing_is_corroborated`` MUST be True, and the caller is asserting that
    this audio really is this chart's song — because the user picked it, or
    because it was matched to the chart by name. See the module docstring: on
    870 deliberately-wrong pairings, 5.1% would have produced a false drift
    warning, and no statistic available here separated them. This is the one
    premise the module cannot check for itself, so it refuses to guess.

    Returns a dict whose 'status' is one of:
      'unknown'  — refused, with 'reason'. NEVER surfaced as a finding.
      'aligned'  — the chart matches the audio's rate.
      'rate'     — the chart's clock runs at a different rate than the audio.

    Everything that could make the answer untrustworthy resolves to 'unknown'.
    """
    def unknown(reason, **kw):
        d = {"status": "unknown", "reason": reason}
        d.update(kw)
        return d

    if not pairing_is_corroborated:
        return unknown("the audio has not been confirmed as this chart's song")

    try:
        import numpy as np
    except Exception:
        return unknown("numpy is unavailable")

    times, weights = _impulses_from_notes(note_times, note_weights)
    if len(times) < MIN_ONSETS:
        return unknown("under %d distinct strikes in the chart" % MIN_ONSETS)

    # Effective sample size — a handful of loud crashes must not carry the fit.
    neff = float((weights.sum() ** 2) / max(1e-9, float(np.square(weights).sum())))
    if neff < MIN_NEFF:
        return unknown("the chart's weight is concentrated in too few strikes "
                       "(effective n = %.0f)" % neff)

    try:
        env, duration = _load_envelope(stem_path)
    except Exception as exc:
        return unknown("the drum stem could not be read (%s)" % type(exc).__name__)
    if env is None:
        return unknown("the drum stem is silent or too short")
    if duration < MIN_DURATION_S:
        return unknown("the audio is under %.0fs" % MIN_DURATION_S)

    # Temporal coverage: strikes must occur across the whole song, or a
    # confident-looking scale could rest on one dense section.
    thirds = np.floor(np.clip(times / max(1e-9, times[-1]), 0, 0.999) * 3)
    if len(np.unique(thirds)) < MIN_COVERAGE:
        return unknown("the chart's notes do not span the whole song")

    grid = np.arange(SCALE_LO, SCALE_HI + 1e-9, SCALE_STEP)
    scores, lags = _scan(env, times, weights, grid)
    if not np.isfinite(scores).any() or scores.max() <= 0:
        return unknown("the chart and the audio do not correlate at all")

    k, prom, halfwidth = _peak_shape(grid, scores)
    a, lag = float(grid[k]), float(lags[k])
    out = {"a": a, "lag": lag, "prominence": prom, "halfwidth_pct": halfwidth,
           "n_onsets": int(len(times)), "neff": neff, "duration": duration,
           "displacement": abs(a - 1.0) * float(times[-1] - times[0])}

    # --- the gates. Any failure means silence, not a hedged claim. --------
    if prom < MIN_PROMINENCE:
        return unknown("the chart does not match this audio strongly enough "
                       "(prominence %.2fx)" % prom, **out)
    if halfwidth > MAX_HALFWIDTH_PCT:
        return unknown("the timing could not be pinned down (peak +-%.3f%%)"
                       % halfwidth, **out)

    # Per-window agreement. A song that genuinely speeds up has no single true
    # scale, and a global fit can still look confident when one long section
    # dominates. Fitting each quarter separately exposes that: real drift is
    # one rate everywhere, tempo variation is not.
    spread = _window_spread(env, times, weights, grid)
    out["window_spread"] = spread
    if spread is not None and spread > MAX_WINDOW_SLOPE_SPREAD:
        return unknown("different parts of the song disagree about the rate "
                       "(spread %.3f%%), which is tempo variation rather than "
                       "drift" % (spread * 100.0), **out)

    if abs(a - 1.0) > RATE_FLOOR and out["displacement"] > MIN_DISPLACEMENT_S:
        out["status"] = "rate"
    else:
        out["status"] = "aligned"
    return out


def _window_spread(env, times, weights, grid):
    """Max-minus-min of the per-window best scale, or None if unmeasurable."""
    import numpy as np
    span = float(times[-1] - times[0])
    if span <= 0:
        return None
    edges = np.linspace(times[0], times[-1], WINDOW_COUNT + 1)
    best = []
    for w in range(WINDOW_COUNT):
        sel = (times >= edges[w]) & (times <= edges[w + 1])
        if int(sel.sum()) < 20:
            continue
        sc, _lg = _scan(env, times[sel], weights[sel], grid)
        if sc.max() <= 0:
            continue
        best.append(float(grid[int(sc.argmax())]))
    if len(best) < 2:
        return None
    return float(max(best) - min(best))
