# parakit_spectral_engine.py -- Spectral Comparison analysis engine (v4 sidecar).
#
# PROVENANCE: port of the v5 rebuild's spectral engine (Spectral v0.12.93,
# consensus-gated); the byte-comparable reference copy in THIS repo is
# spectral_ttk_port/_reference_v5/spectral_engine.py. Brought to v4 for the
# Spectral Comparison tab (v4.7.27, 2026-07-20). Pure logic -- no UI toolkit
# imports; module-level imports are stdlib-only, with numpy + librosa (STFT)
# + mido lazy-loaded inside the functions that need them.
# Post-port audit fixes (3-auditor panel, 2026-07-20): rlrr bpmEvents in
# chart_bpm; JSON lane validation + BOM tolerance; no snare-coercion on the
# MIDI write path; true-duration dur; generator-safe find_issues. The bleed-robust thresholds (NEAR/SILENCE/ONSET) are the
# design-doc port-verbatim values -- do NOT retune casually; they were validated in
# the v5 consensus gate and trace to the original prototype (DESIGN_per_lane_spectral).
# NOT a protected function; consumes audio/chart FILES, never detection internals.
"""spectral_engine -- Qt-free spectral comparison: did the detector chart it right?

Faithful port of the web prototype's algorithm (NO v4 .py source exists -- this is
a v5-design tab). Source of truth:
  prototypes/spectral-compare-web/parakit-spectral-perlane.html
  -- CFG.bands/lanes (~L210/224), computeSpectrogram + reduce (~L267), onsetsFromEnv
     (~L310), findIssues (~L435), the MIDI/RLRR chart maps (~L491).

Pipeline (one STFT -> per-band/lane energy + flux + onsets -> MISS/PHANTOM flags):
  - compute_spectral(samples, sr): STFT (Hann, n_fft=2048, hop=512, center=False so
    frame f starts at f*hop, matching the prototype's frameT) -> per-BAND (4) and
    per-LANE (8) energy/flux/max envelopes.
  - band_onsets(spec, key, thresh): local-peak-pick on the band flux envelope.
  - find_issues(spec, notes): the bleed-robust MISS/PHANTOM flagger.
  - load_chart_notes(path): .mid / .json / .rlrr -> [(time_s, lane, vel)].

NOT detector-gated: the prototype rolls its OWN onset detection (own STFT +
peak-pick), so this needs NO protected v4 detect_onsets. Every threshold is
preserved byte-for-byte; see SPECTRAL_BUILD_PLAN.md.
"""
from __future__ import annotations

import json
import os

N_FFT = 2048
HOP = 512

# 8 lanes in MIDI-editor order (CFG.lanes): (idx, name, color, band, lf_lo, lf_hi).
# `band` = the physical band whose energy backs flag logic; `lf` = the per-lane raw
# frequency slice shown in the Per-Lane view.
LANES = (
    (0, "Hi-Hat", "#00e5ff", "hat",   8000, 16000),
    (1, "Crash",  "#ff8c00", "hat",   3000, 8000),
    (2, "Snare",  "#e63946", "snare", 170, 360),
    (3, "Tom 1",  "#1a3a8f", "tom",   160, 300),
    (4, "Tom 2",  "#2e8b57", "tom",   110, 180),
    (5, "Tom 3",  "#7b2d8b", "tom",   70, 120),
    (6, "Ride",   "#ffd700", "hat",   2500, 5500),
    (7, "Kick",   "#ff69b4", "kick",  34, 110),
)

# 4 physical bands (CFG.bands): (key, label, representative lane, freq_lo, freq_hi).
BANDS = (
    ("hat",   "Hats / Cymbals", 0, 3800, 16000),
    ("snare", "Snare",          2, 170, 360),
    ("tom",   "Toms",           3, 90, 300),
    ("kick",  "Kick",           7, 34, 110),
)

# band marker colours (CFG.bands[].color) -- used when the Comparison view's
# "Chart colors" toggle is OFF (notes coloured by band instead of by lane).
BAND_COLORS = {"hat": "#00e5ff", "snare": "#FF69B4", "tom": "#9F67FF", "kick": "#ffb347"}

# lane idx -> band key (findIssues laneBand map: crash/ride->hat, tom 2/3->tom).
LANE_TO_BAND = {0: "hat", 1: "hat", 2: "snare", 3: "tom",
                4: "tom", 5: "tom", 6: "hat", 7: "kick"}

# MIDI note -> lane (CORE_MIDI_IN, first-wins) + .rlrr class -> lane.
_MIDI_IN = {
    0: (42, 22, 23, 44, 46, 26, 21),  # Hi-Hat + Open Hi-Hat
    1: (49, 52, 55, 57),              # Crash
    2: (38, 40, 37),                  # Snare
    3: (48, 50),                      # Tom 1
    4: (45, 47),                      # Tom 2
    5: (43, 58, 41),                  # Tom 3
    6: (51, 53, 59),                  # Ride
    7: (36, 35),                      # Kick
}
MIDI_NOTE_TO_LANE = {}
for _lane, _notes in _MIDI_IN.items():
    for _n in _notes:
        MIDI_NOTE_TO_LANE.setdefault(_n, _lane)  # first-wins, as in the prototype

CLASS_TO_LANE = {
    "BP_HiHat_C": 0, "BP_Crash15_C": 1, "BP_Crash17_C": 1, "BP_Snare_C": 2,
    "BP_Tom1_C": 3, "BP_Tom2_C": 4, "BP_FloorTom_C": 5, "BP_Ride17_C": 6,
    "BP_Ride20_C": 6, "BP_Kick_C": 7,
}

# lane idx -> the MIDI note written on export (prototype CORE_LANE_MIDI). Every
# value maps back to the same lane through MIDI_NOTE_TO_LANE, so a written chart
# round-trips through load_chart_notes unchanged.
LANE_MIDI_OUT = (42, 49, 38, 48, 45, 41, 51, 36)

# findIssues thresholds (prototype L443) -- LOAD-BEARING, do not change w/o testing.
NEAR = 0.085      # ±85 ms onset<->note agreement window
SILENCE = 0.12    # PHANTOM: < 12% of band max peak energy = silent under the note
ONSET = 0.32      # MISS: flux threshold for band-onset peak-picking
TICK_THRESH = 0.18  # looser flux threshold for the UI onset ticks


def compute_spectral(samples, sr):
    """STFT a mono float array -> a spec dict with per-band (4) and per-lane (8)
    energy (`*Mag`), normalized onset-flux (`*Env`), and per-band/lane max
    (`*MagMax`), plus nFrames/sr/hop/binHz/dur/frameT (the prototype's structure).
    """
    import librosa
    import numpy as np

    # sr guard (breaker B2-6/EDGE-4 round 1; rebuilt for R2B2-3/R2B2-4 round
    # 2, 2026-07-20): coerce-then-bound instead of isinstance — the round-1
    # isinstance check REJECTED valid numpy scalar rates (np.int64 is not an
    # int) while LETTING THROUGH bool True, subnormal floats (5e-324 underflowed
    # bin_hz back to the original ZeroDivisionError), 1e-9, and inf. Any
    # value coercible to a finite float in the plausible audio range passes;
    # everything else is a clean ValueError at the boundary.
    import math
    try:
        if isinstance(sr, bool):
            raise TypeError("bool is not a sample rate")
        sr = float(sr)
    except (TypeError, ValueError):
        raise ValueError("invalid sample rate from the audio decoder: %r" % (sr,))
    if not math.isfinite(sr) or not (1000.0 <= sr <= 1_000_000.0):
        raise ValueError("implausible sample rate from the audio decoder: "
                         "%r (expected 1000-1000000 Hz)" % (sr,))
    # Sanitize non-finite PCM before the STFT (review #2, 2026-07-21): a float
    # WAV carrying NaN/inf samples otherwise made librosa's valid_audio RAISE
    # (ParameterError) -- or, on other versions, poisoned max_mag so the
    # `silent` gate read False and every downstream comparison returned NaN =
    # a confident-WRONG "0 miss / 0 phantom" verdict with no warning. Zeroing
    # non-finite samples makes such a file read as SILENT (correctly flagged);
    # finite audio is untouched (nan_to_num is identity on finite values).
    y = np.nan_to_num(np.asarray(samples, dtype=np.float32),
                      nan=0.0, posinf=0.0, neginf=0.0)
    if y.ndim > 1:
        # Collapse the CHANNEL axis, whichever layout the decoder used
        # (breaker B2-2, 2026-07-20): channels-first (ch, N) like
        # librosa(mono=False) vs channels-last (N, ch) like soundfile's
        # default. Blindly using axis=0 collapsed 3 s of channels-last stereo
        # to TWO samples -> a silently wrong dur=0.000045 compare. Channels
        # is always the smaller axis; average across it.
        y = y.mean(axis=(1 if y.shape[0] > y.shape[1] else 0)).astype(np.float32)
    n_samples = int(y.size)   # true decoded length, BEFORE any pad (dur fix)
    # center=False -> frame f covers [f*hop, f*hop+n_fft); frameT(f)=f*hop/sr,
    # matching the prototype (no n_fft/2 centering shift -> onsets stay aligned).
    if y.size < N_FFT:
        y = np.pad(y, (0, N_FFT - y.size))
    # SYMMETRIC Hann (denom N-1) to match the prototype's literal window EXACTLY
    # (w[i]=0.5-0.5*cos(2*pi*i/(N-1))); librosa's default "hann" is the PERIODIC
    # fftbins window (denom N), which drifts ~0.0012/sample from the JS.
    win = (0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(N_FFT) / (N_FFT - 1))).astype(np.float32)
    mags = np.abs(librosa.stft(y, n_fft=N_FFT, hop_length=HOP, window=win,
                               center=False)).astype(np.float32)
    half = N_FFT >> 1                      # 1024 (prototype uses bins 0..half-1)
    mags = mags[:half]                     # drop the Nyquist bin to match the JS
    n_frames = mags.shape[1]
    bin_hz = sr / N_FFT
    # JS maxMag = max PER-BIN magnitude / half (hypot(re,im)/half); the 1e-5 silent
    # gate is calibrated to THAT quantity, so divide by half (NOT a per-frame sum).
    max_mag = float(mags.max()) / half if n_frames else 0.0
    # Full dB spectrogram (bins x frames) for the Comparison-view heatmap.
    db = (20.0 * np.log10(mags + 1e-9)).astype(np.float32)
    max_db = float(db.max()) if db.size else -1e9

    def reduce(lo_hz, hi_hz):
        lo = max(1, int(np.floor(lo_hz / bin_hz)))
        hi = min(half - 1, int(np.ceil(hi_hz / bin_hz)))
        mag = mags[lo:hi + 1, :].sum(axis=0)          # summed magnitude per frame
        env = np.maximum(0.0, np.diff(mag, prepend=0.0))  # spectral flux (>=0)
        mx = float(env.max()) if env.size else 0.0
        env = env / mx if mx > 1e-9 else env
        return env.astype(np.float32), mag.astype(np.float32), float(mag.max() if mag.size else 1e-9)

    band_env, band_mag, band_mag_max = {}, {}, {}
    for key, _lbl, _ln, lo, hi in BANDS:
        band_env[key], band_mag[key], band_mag_max[key] = reduce(lo, hi)
    lane_env, lane_mag, lane_mag_max = {}, {}, {}
    for idx, _nm, _c, _bnd, lo, hi in LANES:
        lane_env[idx], lane_mag[idx], lane_mag_max[idx] = reduce(lo, hi)

    return {
        "sr": sr, "hop": HOP, "nFrames": n_frames, "binHz": bin_hz,
        "half": half, "fftSize": N_FFT,
        "maxMag": max_mag, "silent": max_mag < 1e-5,
        "db": db, "maxDb": max_db,                 # full spectrogram (heatmap)
        "bandEnv": band_env, "bandMag": band_mag, "bandMagMax": band_mag_max,
        "laneEnv": lane_env, "laneMag": lane_mag, "laneMagMax": lane_mag_max,
        # True decoded duration -- NOT n_frames*hop/sr, which describes frame
        # starts and under-reports by ~n_fft/sr (~35 ms @ 44.1k; and a padded
        # tiny clip would report ~12 ms). Audit 2026-07-20.
        "dur": n_samples / sr if sr else 0.0,
    }


def frame_time(spec, f):
    return f * spec["hop"] / spec["sr"]


def onsets_from_env(spec, env, thresh):
    """Local-max peak times (s) on a normalized flux env (prototype onsetsFromEnv):
    env[f] > thresh, env[f] >= env[f-1], env[f] > env[f+1], with a ~40 ms refractory."""
    out = []
    n = spec["nFrames"]
    sr, hop = spec["sr"], spec["hop"]
    min_gap = round(0.04 * sr / hop)
    last = -1e9
    for f in range(1, n - 1):
        e = env[f]
        if e > thresh and e >= env[f - 1] and e > env[f + 1] and (f - last) >= min_gap:
            out.append(frame_time(spec, f))
            last = f
    return out


def band_onsets(spec, key, thresh=TICK_THRESH):
    return onsets_from_env(spec, spec["bandEnv"][key], thresh)


def lane_onsets(spec, idx, thresh=TICK_THRESH):
    return onsets_from_env(spec, spec["laneEnv"][idx], thresh)


def find_issues(spec, notes):
    """Bleed-robust MISS/PHANTOM flags (prototype findIssues). ``notes`` =
    iterable of (time_s, lane, vel). Returns a list of
    ``{type:'phantom'|'miss', time, lane, key}``.

    PHANTOM (×): a charted note whose band peak energy in [t-25ms, t+35ms] is
    < SILENCE of the band max. MISS (+): a band onset (flux >= ONSET) with NO
    note of ANY lane within ±NEAR.
    """
    n_frames = spec["nFrames"]
    sr, hop = spec["sr"], spec["hop"]
    band_mag, band_mag_max = spec["bandMag"], spec["bandMagMax"]

    import math as _m
    try:
        dur = float(spec.get("dur", 0.0) or 0.0)
    except (TypeError, ValueError):
        dur = 0.0
    if not (_m.isfinite(dur) and dur > 0.0):
        # Hand-rolled/legacy spec dict without a usable dur (incl. NaN/inf —
        # breaker R2B2-2: NaN silently disabled the beyond-end guard): fall
        # back to the frames' full AUDIBLE coverage. Round 1 used
        # n_frames*hop/sr (frame STARTS), which under-reported by ~n_fft/sr
        # and falsely phantomed in-audio notes near the end (breaker R2E-1).
        try:
            _fft_raw = spec.get("fftSize")
            if isinstance(_fft_raw, bool):
                raise TypeError("bool is not an fft size")   # R4B3-3
            _fft = int(_fft_raw or N_FFT)
        except (TypeError, ValueError):
            _fft = N_FFT   # garbage fftSize in a hand-rolled spec (R3B2-1)
        if not (32 <= _fft <= 65536):
            _fft = N_FFT   # coerce-then-BOUND, like the sr guard (R4B3-4:
                           # fftSize=1e9 inflated dur so far the beyond-end
                           # phantom guard never fired)
        dur = (max(0, n_frames - 1) * hop + _fft) / sr

    def frame_of(t):
        return max(0, min(n_frames - 1, round(t * sr / hop)))

    def energy_at(key, t):
        # A note whose ENTIRE [t-25ms, t+35ms] window falls outside the audio
        # has no energy under it, period. frame_of()'s clamp used to judge such
        # notes by the FIRST/LAST frame's energy, so a loud-ending (truncated)
        # stem never flagged beyond-the-end notes as PHANTOM (breaker EDGE-2,
        # 2026-07-20). Notes only slightly past either edge still overlap real
        # frames and are judged by them, as before.
        if t - 0.025 > dur or t + 0.035 < 0.0:
            return 0.0
        f0, f1 = frame_of(t - 0.025), frame_of(t + 0.035)
        seg = band_mag[key][f0:f1 + 1]
        m = float(seg.max()) if seg.size else 0.0
        return m / (band_mag_max[key] or 1e-9)

    issues = []
    # Malformed-entry policy matches the loaders' (breaker B2-7/EDGE-3,
    # 2026-07-20): skip, don't abort. Raw TypeError/ValueError/IndexError from
    # a short/non-numeric tuple — or a NaN/Inf time that survived a loader —
    # used to kill the whole compare. Also keeps the generator-safety fix:
    # materialize ONCE (audit 2026-07-20, empirically proven 0-vs-1).
    import math as _math
    clean = []
    for nt in notes:
        try:
            t, lane = float(nt[0]), int(nt[1])
        except (TypeError, ValueError, IndexError):
            continue
        if not _math.isfinite(t):
            continue
        clean.append((t, lane))
    # Perf (v4.9.2): the MISS test scanned ALL note times per band onset — O(N*M),
    # ~0.76s @10k notes blocking the UI thread on every edit/undo/redo. Sort the
    # note times once and bisect for the nearest candidate. ONE GLOBAL sorted
    # array (not per-lane) because the rule is "no note of ANY lane near this
    # onset", so unmapped/parsed integer lanes must still participate. Proven
    # byte-identical to the O(N*M) form on thousands of randomized fixtures
    # across two independent equivalence harnesses (empty, dense, dup, ties at
    # NEAR, negative/large/NaN/inf times, malformed tuples, unmapped lanes).
    from bisect import bisect_left as _bisect_left
    note_times = sorted(nt[0] for nt in clean)
    for t, lane in clean:
        key = LANE_TO_BAND.get(lane)
        if not key:
            continue
        if energy_at(key, t) < SILENCE:
            issues.append({"type": "phantom", "time": t, "lane": lane, "key": key})
    for key, _lbl, lane, _lo, _hi in BANDS:
        for t in band_onsets(spec, key, ONSET):
            i = _bisect_left(note_times, t)
            has_near_note = (
                (i < len(note_times) and abs(note_times[i] - t) <= NEAR)
                or (i > 0 and abs(note_times[i - 1] - t) <= NEAR)
            )
            if not has_near_note:
                issues.append({"type": "miss", "time": round(float(t), 3),
                               "lane": lane, "key": key})
    return issues


def load_chart_notes(path):
    """Load a candidate chart (.mid/.midi/.json/.rlrr) -> sorted
    [(time_s, lane, vel)] list (only notes that map to a drum lane)."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".mid", ".midi"):
        notes = _load_midi_notes(path)
    elif ext == ".rlrr":
        notes = _load_rlrr_notes(path)
    elif ext == ".json":
        notes = _load_json_notes(path)
    else:
        raise ValueError(f"Unsupported chart type: {ext} (use .mid / .json / .rlrr)")
    notes.sort(key=lambda x: x[0])
    return notes


def _midi_tempo_map(mid):
    """The GLOBAL tempo map of a mido MidiFile as tick-sorted
    ``[(abs_tick, tempo_us), ...]`` (prototype parseMidi: tempo events are
    collected across ALL tracks -- format-1 keeps them in the conductor
    track -- then sorted; empty -> the 120 BPM default)."""
    tempo_map = []
    for track in mid.tracks:
        abs_tick = 0
        for msg in track:
            abs_tick += msg.time
            if msg.type == "set_tempo":
                tempo_map.append((abs_tick, msg.tempo))
    if not tempo_map:
        tempo_map.append((0, 500000))
    tempo_map.sort(key=lambda e: e[0])
    return tempo_map


def chart_bpm(path):
    """First tempo of the chart as BPM, or None on any error (the caller falls
    back to 120). .mid/.midi: first tempo by tick order across all tracks (the
    prototype's gridBpm = 6e7/tempoMap[0][1]). .rlrr: the first bpmEvents value
    (audit 2026-07-20 — returning None here forced 120-BPM beat grids and
    exported-tempo metadata on every non-120 RLRR chart)."""
    import math as _math_bpm
    ext = os.path.splitext(path)[1].lower()
    if ext in (".mid", ".midi"):
        try:
            import mido
            tempo_map = _midi_tempo_map(mido.MidiFile(path))
            _b = 6e7 / tempo_map[0][1]
            return _b if _math_bpm.isfinite(_b) and 4.0 <= _b <= 100000.0 else None
        except Exception:
            return None
    if ext == ".rlrr":
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            evs = data.get("bpmEvents") or []
            for ev in evs:
                bpm = float(ev.get("bpm", 0) or 0)
                # isfinite rejects the `Infinity` literal json.load accepts
                # (R8B2-1); the plausible-range [1, 1e5] bound rejects a
                # finite-but-extreme bpm that overflows snap_time's
                # round(t/step) (huge, R9-B4-1) or collapses every note to 0
                # (denormal-tiny, R9E-1). 2026-07-20.
                if _math_bpm.isfinite(bpm) and 4.0 <= bpm <= 100000.0:
                    return bpm
        except Exception:
            return None
    return None


def write_chart_midi(notes, bpm, path):
    """Write ``[(time_s, lane, vel)]`` to a format-0 SMF (prototype writeMidi):
    tpb 480, drum channel 9, one constant-tempo meta event, note-off 60 ticks
    (tpb>>3) after each note-on. Times are re-gridded to ticks at the constant
    ``bpm`` (falls back to 120 when missing/invalid), velocities clamped 1-127
    (0/None -> 100), unknown lanes -> snare (38), all as in the prototype JS.
    """
    import mido

    tpb = 480
    bpm = float(bpm) if bpm and bpm > 0 else 120.0
    # Clamp to the set_tempo-encodable range (breaker R10-B2-1, 2026-07-20):
    # tempo = round(6e7/bpm) must fit mido's 24-bit meta (1..16777215), i.e.
    # bpm in [~3.5763, 6e7]. A direct call with a tiny/huge bpm otherwise
    # raises a raw "attribute must be in range" mido error. The tab floors
    # model bpm at 4.0, but this keeps the engine's own write path robust.
    import math as _math_wm
    if not (_math_wm.isfinite(bpm) and 3.5763 <= bpm <= 6e7):
        bpm = 120.0
    evs = []
    for nt in notes:
        t, lane = float(nt[0]), int(nt[1])
        vel = int(nt[2]) if len(nt) > 2 and nt[2] is not None else 100
        vel = min(127, max(1, vel or 100))
        if not (0 <= lane < len(LANE_MIDI_OUT)):
            continue   # never silently coerce an unknown lane to snare on a
                       # WRITE path -- that alters user data (audit 2026-07-20;
                       # diverges from the prototype JS, deliberately)
        note = LANE_MIDI_OUT[lane]
        # A magnitude-extreme FINITE note time (e.g. 1e308) survives load --
        # time is isfinite-guarded there but not magnitude-bounded -- so
        # `t * bpm / 60 * tpb` overflows to inf and round(inf) raises
        # OverflowError (breaker R14-B2-1, 2026-07-20). Skip the un-encodable
        # note, keeping the rest of the export -- the export-tail twin of the
        # R13E-1 load-seam tick skip (the magnitude bound belongs at this sink,
        # not the loader: find_issues phantoms a huge-t note and the renderer
        # culls it, both proven safe, so load stays lossless).
        try:
            tick = max(0, round(t * bpm / 60.0 * tpb))
        except (OverflowError, ValueError):
            continue
        # kind 0 = note-off, 1 = note-on: ties sort off-before-on, matching the
        # prototype's status-byte sort (0x89 < 0x99).
        evs.append((tick, 1, note, vel))
        evs.append((tick + (tpb >> 3), 0, note, 0))
    evs.sort(key=lambda e: (e[0], e[1]))

    mid = mido.MidiFile(type=0, ticks_per_beat=tpb)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=round(6e7 / bpm), time=0))
    last = 0
    for tick, kind, note, vel in evs:
        delta = tick - last
        last = tick
        kind_name = "note_on" if kind else "note_off"
        track.append(mido.Message(kind_name, channel=9, note=note,
                                  velocity=vel, time=delta))
    mid.save(path)


def _load_midi_notes(path):
    """Honors the FULL tempo map (prototype parseMidi/ticksToSecs): pass 1
    collects the global tempo map across all tracks; pass 2 converts each
    note-on's absolute tick by integrating the piecewise-constant tempo. A
    format-1 chart (tempo in the conductor track, notes in another) and
    mid-song tempo changes both land on the right seconds -- the old per-track
    constant-tempo conversion mistimed exactly those charts."""
    import mido
    mid = mido.MidiFile(path)
    tpb = mid.ticks_per_beat
    # tpb must be a POSITIVE PPQ value (breaker R11-B2-1 + R12-B2-1,
    # 2026-07-20): mido loads a header with ticks_per_beat<=0 without
    # complaint, then ticks_to_secs' PPQ formula (`ticks * tempo / tpb / 1e6`)
    # either raised ZeroDivisionError (tpb=0 -> cryptic "division by zero") OR
    # -- for a NEGATIVE SMPTE tpb -- SILENTLY MIS-TIMED the whole chart to
    # negative seconds with no error (R12 corrected the round-11 comment,
    # which wrongly claimed negative tpb "divides fine"). ParaKit's engine is
    # PPQ-only; real drum charts are all positive tpb (1865/1882 on disk are
    # 480). Reject non-positive with a clean actionable message rather than
    # silently corrupt timing or crash -- same precedent as the sr=0 guard.
    if not (isinstance(tpb, int) and tpb > 0):
        raise ValueError("chart has an unsupported ticks_per_beat (%r) -- the "
                         ".mid header is malformed or uses SMPTE timing this "
                         "tool does not read" % (tpb,))
    tempo_map = _midi_tempo_map(mid)

    def ticks_to_secs(ticks):
        secs, prev_tick, prev_tempo = 0.0, 0, 500000
        for ct, tempo in tempo_map:
            if ct >= ticks:
                break
            secs += (ct - prev_tick) * prev_tempo / tpb / 1e6
            prev_tick, prev_tempo = ct, tempo
        return secs + (ticks - prev_tick) * prev_tempo / tpb / 1e6

    import math as _math_mid
    notes = []
    for track in mid.tracks:
        abs_tick = 0
        for msg in track:
            abs_tick += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                lane = MIDI_NOTE_TO_LANE.get(msg.note)
                if lane is not None:
                    # An absurdly large absolute tick (a ~144-byte VLQ delta
                    # mido parses without complaint) makes ticks_to_secs'
                    # int/int quotient exceed float-max -> raw OverflowError
                    # ("integer division result too large for a float"),
                    # worker-caught as a cryptic amber (breaker R13E-1,
                    # 2026-07-20). A note whose time can't be represented as
                    # finite seconds can't be placed -> skip it, same policy
                    # as a non-finite time (EDGE-3), preserving the rest.
                    try:
                        _sec = float(ticks_to_secs(abs_tick))
                    except (OverflowError, ValueError):
                        continue
                    if not _math_mid.isfinite(_sec):
                        continue
                    notes.append((_sec, int(lane), int(msg.velocity)))
    return notes


def _load_rlrr_notes(path):
    import math
    with open(path, encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("rlrr chart: top level must be an object, got %s"
                         % type(data).__name__)
    events = data.get("events", []) or []
    instruments = data.get("instruments", []) or []
    # Container/entry schema checks (breaker B2-5, 2026-07-20): wrong-typed
    # containers raised raw AttributeError/TypeError mid-loop. Container wrong
    # -> clean error; a non-dict ENTRY -> skip, per the malformed-entry policy.
    if not isinstance(events, list):
        raise ValueError("rlrr chart: 'events' must be a list, got %s"
                         % type(events).__name__)
    if not isinstance(instruments, list):
        instruments = []
    notes = []
    for e in events:
        if not isinstance(e, dict):
            continue
        cls = ""
        name = e.get("name")
        if name is not None:
            nv = str(name)
            for c in CLASS_TO_LANE:
                if nv.startswith(c):
                    cls = c
                    break
        else:
            ii = e.get("instrumentIndex")
            if isinstance(ii, int) and 0 <= ii < len(instruments):
                inst = instruments[ii]
                cls = (inst.get("class", "") or "") if isinstance(inst, dict) else ""
        lane = CLASS_TO_LANE.get(cls)
        if lane is None:
            continue
        try:
            t = float(e.get("time"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(t):
            continue   # NaN/Infinity time (breaker EDGE-3)
        vel = e.get("vel")
        # isfinite before int() (breaker R13-B2-1, 2026-07-20): a non-finite
        # vel — int(Infinity) raises OverflowError, int(NaN) raises ValueError,
        # both from json.load-accepted literals — is not a real velocity; fall
        # back to the default, matching the isfinite `time` guard (EDGE-3).
        vel = (int(vel) if isinstance(vel, (int, float)) and math.isfinite(vel)
               else 80)
        notes.append((t, int(lane), vel))
    return notes


def _load_json_notes(path):
    import math
    # utf-8-sig: Windows editors love BOM-prefixing JSON (audit 2026-07-20).
    with open(path, encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("chart JSON: top level must be an object, got %s"
                         % type(data).__name__)
    notes_raw = data.get("notes", [])
    # Container-level schema check (breaker B2-5, 2026-07-20): a str/dict/int
    # here escaped the per-entry filter as a raw AttributeError ("'str' object
    # has no attribute 'get'"). Whole-container wrong type = the chart itself
    # is malformed -> clean error, not a silent 0-note "success".
    if notes_raw is None:
        notes_raw = []
    if not isinstance(notes_raw, list):
        raise ValueError("chart JSON: 'notes' must be a list, got %s"
                         % type(notes_raw).__name__)
    out = []
    for n in notes_raw:
        try:
            if isinstance(n, (list, tuple)):
                t, lane = float(n[0]), int(n[1])
                vel_raw = n[2] if len(n) > 2 else 100
            else:
                t, lane = float(n.get("time")), int(n.get("lane"))
                vel_raw = n.get("vel", 100)
        except (TypeError, ValueError, IndexError, AttributeError,
                OverflowError):
            # OverflowError (breaker R13-B2-1, 2026-07-20): int(Infinity) on a
            # non-finite lane raises OverflowError — an ArithmeticError, NOT a
            # ValueError — so it escaped the original tuple. A note with no
            # placeable time/lane is skipped, per the malformed-entry policy.
            continue
        if not math.isfinite(t):
            continue   # NaN/Infinity time: json.load accepts them but every
                       # downstream round()/compare blows up (breaker EDGE-3)
        # A non-finite VEL only defaults (velocity is cosmetic — clamped on
        # export, ignored by find_issues), keeping the note, consistent with
        # _load_rlrr_notes (breaker R13-B2-1).
        vel = (int(vel_raw) if isinstance(vel_raw, (int, float))
               and math.isfinite(vel_raw) else 100)
        # Enforce load_chart_notes' contract ("only notes that map to a drum
        # lane") here too -- an out-of-range lane would otherwise slide through
        # to export and get silently coerced to snare (audit 2026-07-20).
        if 0 <= lane < len(LANES):
            out.append((t, lane, vel))
    return out


# ---------------------------------------------------------------------------
# --selftest: dual invariants for the 2026-07-20 breaker-round-1 fixes.
# Each test is RED against the pre-fix engine (crash or wrong value on these
# exact inputs) and GREEN now — the mutation-proof teeth for the fold.
# ---------------------------------------------------------------------------
def _selftest():
    import math
    import tempfile
    import numpy as np

    sr = 22050
    fails = []

    def check(name, cond):
        (print("  ok  %s" % name) if cond else
         (fails.append(name), print("  FAIL %s" % name)))

    # Broadband clicks at known times -> energy in every band.
    rng = np.random.default_rng(7)
    sig = np.zeros(sr * 2, dtype=np.float32)
    click_times = (0.5, 1.0, 1.5)
    for ct in click_times:
        i = int(ct * sr)
        sig[i:i + 220] = rng.standard_normal(220).astype(np.float32) * 0.8

    # --- B2-6/EDGE-4 + R2B2-3: invalid/implausible sr fails loud and clear -
    for bad_sr in (0, None, -44100, float("nan"), True, 1e-9, 5e-324,
                   float("inf"), "44100x"):
        try:
            compute_spectral(sig, bad_sr)
            check("sr=%r rejected" % (bad_sr,), False)
        except ValueError:
            check("sr=%r rejected" % (bad_sr,), True)
        except Exception as e:
            print("    (raised %r instead of ValueError)" % (e,))
            check("sr=%r rejected" % (bad_sr,), False)

    # --- R2B2-4: numpy scalar rates from real decoders must be ACCEPTED ----
    for np_sr in (np.int64(sr), np.int32(sr), np.float32(sr)):
        try:
            got = compute_spectral(sig[: sr // 2], np_sr)
            check("numpy sr %s accepted" % type(np_sr).__name__,
                  abs(got["dur"] - 0.5) < 1e-6)
        except Exception as e:
            print("    (raised %r)" % (e,))
            check("numpy sr %s accepted" % type(np_sr).__name__, False)

    # --- B2-2: BOTH stereo layouts collapse the channel axis ---------------
    want_dur = len(sig) / sr
    spec_mono = compute_spectral(sig, sr)
    d_first = compute_spectral(np.stack([sig, sig]), sr)["dur"]        # (2, N)
    d_last = compute_spectral(np.stack([sig, sig]).T, sr)["dur"]       # (N, 2)
    check("mono dur exact", abs(spec_mono["dur"] - want_dur) < 1e-9)
    check("channels-first stereo dur", abs(d_first - want_dur) < 1e-9)
    check("channels-last stereo dur (was 2 samples)",
          abs(d_last - want_dur) < 1e-9)

    # --- EDGE-2: a note wholly beyond the audio IS a phantom ---------------
    notes_ok = [(ct, 2, 100) for ct in click_times]
    base = find_issues(spec_mono, notes_ok)
    check("clicks charted right: 0 phantom",
          not any(i["type"] == "phantom" for i in base))
    beyond = find_issues(spec_mono, notes_ok + [(9999.0, 2, 100)])
    check("note @9999s flagged phantom (was clamped to last frame)",
          any(i["type"] == "phantom" and i["time"] == 9999.0 for i in beyond))
    # Loud-ENDING audio — the exact EDGE-2 repro shape: last frame is loud.
    loud_end = np.concatenate([np.zeros(sr, dtype=np.float32),
                               rng.standard_normal(sr).astype(np.float32) * 0.8])
    spec_loud = compute_spectral(loud_end, sr)
    le = find_issues(spec_loud, [(1.5, 2, 100), (9999.0, 2, 100)])
    check("loud-ending: beyond-end phantom flagged, in-audio note not",
          any(i["type"] == "phantom" and i["time"] == 9999.0 for i in le)
          and not any(i["type"] == "phantom" and i["time"] == 1.5 for i in le))
    # A note just past the edge still overlaps real frames — judged, not
    # auto-phantomed (the guard only fires when the whole window is outside).
    edge = find_issues(spec_loud, [(spec_loud["dur"] + 0.01, 2, 100)])
    check("note 10ms past end judged by real trailing energy",
          not any(i["type"] == "phantom" for i in edge))

    # --- B2-7/EDGE-3: malformed/non-finite note entries skip, not raise ----
    try:
        junk = find_issues(spec_mono, [(0.5,), ("abc", 2), (None, 1),
                                       (float("nan"), 2), (0.5, 2, 100)])
        check("malformed note tuples skipped (was raw TypeError)", True)
        check("nan-time note not judged",
              not any(i["type"] == "phantom" and
                      isinstance(i["time"], float) and math.isnan(i["time"])
                      for i in junk))
    except Exception as e:
        print("    (raised %r)" % (e,))
        check("malformed note tuples skipped (was raw TypeError)", False)

    # --- dur-fallback: a spec dict without dur must not phantom everything -
    spec_nodur = dict(spec_mono)
    spec_nodur.pop("dur")
    nd = find_issues(spec_nodur, notes_ok)
    check("spec without dur: clicks still 0 phantom",
          not any(i["type"] == "phantom" for i in nd))
    # R2B2-2: dur=NaN must not silently DISABLE the beyond-end guard.
    spec_nan = dict(spec_loud)
    spec_nan["dur"] = float("nan")
    nn = find_issues(spec_nan, [(1.5, 2, 100), (9999.0, 2, 100)])
    check("dur=NaN: beyond-end phantom still flagged, in-audio note not",
          any(i["type"] == "phantom" and i["time"] == 9999.0 for i in nn)
          and not any(i["type"] == "phantom" and i["time"] == 1.5 for i in nn))
    # R2E-1: the fallback must cover the frames' AUDIBLE span, not frame
    # starts — a 1-frame loud spec (true span n_fft/sr ~0.093 s) must not
    # phantom an in-audio note at t=0.06.
    one_frame = rng.standard_normal(N_FFT).astype(np.float32) * 0.8
    spec_1f = compute_spectral(one_frame, sr)
    spec_1f.pop("dur")
    f1 = find_issues(spec_1f, [(0.06, 2, 100)])
    check("1-frame no-dur spec: in-audio note at 0.06s not phantomed",
          not any(i["type"] == "phantom" for i in f1))
    # R4B3-3: fftSize=True slipped the coercion as int 1 (bool is truthy),
    # under-reporting the fallback dur -> false phantom on an in-audio note.
    s_bool = dict(spec_1f)
    s_bool["fftSize"] = True
    try:
        fb_bool = find_issues(s_bool, [(0.06, 2, 100)])
        check("fftSize=True rejected (in-audio note not phantomed)",
              not any(i["type"] == "phantom" for i in fb_bool))
    except Exception as e:
        print("    (raised %r)" % (e,))
        check("fftSize=True rejected (in-audio note not phantomed)", False)
    # R4B3-4: a huge fftSize inflated the fallback dur so far the beyond-end
    # phantom guard never fired. On a no-dur loud-ending spec, a note @9999s
    # must STILL be phantomed with fftSize=1e9.
    s_huge = dict(spec_loud)
    s_huge.pop("dur")
    s_huge["fftSize"] = int(1e9)
    hg = find_issues(s_huge, [(1.5, 2, 100), (9999.0, 2, 100)])
    check("fftSize=1e9 bounded (beyond-end note still phantomed)",
          any(i["type"] == "phantom" and i["time"] == 9999.0 for i in hg))

    # R3B2-1: garbage fftSize in a hand-rolled spec must not crash the
    # fallback arithmetic (raw TypeError pre-fix).
    for bad_fft in ("x", None, [], float("nan")):
        s = dict(spec_1f)
        s["fftSize"] = bad_fft
        try:
            fb = find_issues(s, [(0.06, 2, 100)])
            check("fftSize=%r tolerated in dur fallback" % (bad_fft,),
                  not any(i["type"] == "phantom" for i in fb))
        except Exception as e:
            print("    (raised %r)" % (e,))
            check("fftSize=%r tolerated in dur fallback" % (bad_fft,), False)

    # --- B2-5/EDGE-3: loader schema + NaN hardening ------------------------
    def tmpjson(text, suffix):
        f = tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False,
                                        encoding="utf-8")
        f.write(text)
        f.close()
        return f.name

    p = tmpjson('{"notes": "abc"}', ".json")
    try:
        load_chart_notes(p)
        check("json notes-as-str -> clean ValueError", False)
    except ValueError:
        check("json notes-as-str -> clean ValueError", True)
    except Exception as e:
        print("    (raised %r)" % (e,))
        check("json notes-as-str -> clean ValueError", False)
    finally:
        os.unlink(p)

    p = tmpjson('{"notes": [{"time": NaN, "lane": 2}, "x", 42,'
                ' {"time": 0.5, "lane": 2}]}', ".json")
    try:
        got = load_chart_notes(p)
        check("json NaN/garbage entries skipped, good one kept",
              len(got) == 1 and got[0][0] == 0.5)
    except Exception as e:
        print("    (raised %r)" % (e,))
        check("json NaN/garbage entries skipped, good one kept", False)
    finally:
        os.unlink(p)

    p = tmpjson('{"events": "abc"}', ".rlrr")
    try:
        load_chart_notes(p)
        check("rlrr events-as-str -> clean ValueError", False)
    except ValueError:
        check("rlrr events-as-str -> clean ValueError", True)
    except Exception as e:
        print("    (raised %r)" % (e,))
        check("rlrr events-as-str -> clean ValueError", False)
    finally:
        os.unlink(p)

    p = tmpjson('{"events": ["x", 42, {"name": "BP_Snare_C", "time": 0.5}],'
                ' "instruments": ["bogus"]}', ".rlrr")
    try:
        got = load_chart_notes(p)
        check("rlrr non-dict events skipped, good one kept",
              len(got) == 1 and got[0][1] == 2)
    except Exception as e:
        print("    (raised %r)" % (e,))
        check("rlrr non-dict events skipped, good one kept", False)
    finally:
        os.unlink(p)

    # R11-B2-1: a .mid with ticks_per_beat=0 must give a clean ValueError,
    # not a bare ZeroDivisionError (which surfaced as "Compare failed:
    # division by zero"). A valid tpb must still load.
    try:
        import mido
        # tpb=0 AND negative SMPTE tpb (R11-B2-1 + R12-B2-1) must both give a
        # clean ValueError, not a bare ZeroDivisionError (tpb=0) or silently
        # mis-timed negative-seconds notes (negative tpb).
        for _bad_tpb in (0, -6360, -1):
            _mb = mido.MidiFile(ticks_per_beat=_bad_tpb)
            _mb.tracks.append(mido.MidiTrack())
            p = tempfile.NamedTemporaryFile(suffix=".mid", delete=False).name
            _mb.save(p)
            try:
                load_chart_notes(p)
                check("mid tpb=%d -> clean ValueError" % _bad_tpb, False)
            except ValueError:
                check("mid tpb=%d -> clean ValueError" % _bad_tpb, True)
            except Exception as e:
                print("    (raised %r)" % (e,))
                check("mid tpb=%d -> clean ValueError" % _bad_tpb, False)
            finally:
                os.unlink(p)
        _mv = mido.MidiFile(ticks_per_beat=480)
        _t = mido.MidiTrack(); _mv.tracks.append(_t)
        _t.append(mido.MetaMessage("set_tempo", tempo=500000, time=0))
        _t.append(mido.Message("note_on", channel=9, note=38, velocity=100, time=240))
        _t.append(mido.Message("note_off", channel=9, note=38, velocity=0, time=60))
        p = tempfile.NamedTemporaryFile(suffix=".mid", delete=False).name
        _mv.save(p)
        try:
            _n = load_chart_notes(p)
            # 240 ticks / 480 tpb = 0.5 beat; at 120 BPM (500000 us/beat)
            # that is 0.25 s.
            check("mid tpb=480 still loads (1 snare @0.25s)",
                  len(_n) == 1 and _n[0][1] == 2 and abs(_n[0][0] - 0.25) < 1e-6)
        finally:
            os.unlink(p)
    except Exception as e:
        print("    (tpb guard harness raised %r)" % (e,))
        check("mid tpb guard harness ran", False)

    # R13-B2-1: a non-finite vel/lane (json.load accepts the Infinity/NaN
    # literals) must NOT raise an uncaught OverflowError/ValueError out of the
    # loader — skip the entry (vel → default), matching the isfinite time guard.
    for _txt, _sfx, _want in (
            ('{"notes": [{"time": 0.5, "lane": 2, "vel": Infinity},'
             ' {"time": 1.0, "lane": 7, "vel": 90}]}', ".json", 2),
            ('{"notes": [{"time": 0.5, "lane": Infinity, "vel": 90},'
             ' {"time": 1.0, "lane": 7, "vel": 90}]}', ".json", 1),
            ('{"notes": [[0.5, Infinity, 90], [1.0, 7, 90]]}', ".json", 1),
            ('{"events": [{"name": "BP_Snare_C", "time": 0.5, "vel": Infinity},'
             ' {"name": "BP_Kick_C", "time": 1.0, "vel": 90}]}', ".rlrr", 2),
            ('{"events": [{"name": "BP_Snare_C", "time": 0.5, "vel": NaN}]}',
             ".rlrr", 1)):
        p = tmpjson(_txt, _sfx)
        try:
            got = load_chart_notes(p)
            check("non-finite vel/lane skipped not raised (%s, %d notes)"
                  % (_sfx, _want), len(got) == _want)
        except Exception as e:
            print("    (raised %r)" % (e,))
            check("non-finite vel/lane skipped not raised (%s)" % _sfx, False)
        finally:
            os.unlink(p)

    # R13E-1: an oversized absolute tick (mido parses arbitrary-length VLQ)
    # must SKIP that note, not raise "integer division result too large for a
    # float" out of ticks_to_secs; other notes in the same chart survive.
    try:
        import mido
        _mo = mido.MidiFile(ticks_per_beat=1)
        _t = mido.MidiTrack(); _mo.tracks.append(_t)
        _t.append(mido.MetaMessage("set_tempo", tempo=16777215, time=0))
        _t.append(mido.Message("note_on", channel=9, note=38, velocity=100, time=0))
        _t.append(mido.Message("note_off", channel=9, note=38, velocity=0, time=60))
        # a second note-on at an overflowing absolute tick
        _t.append(mido.Message("note_on", channel=9, note=36, velocity=100,
                               time=int(1e303)))
        _t.append(mido.Message("note_off", channel=9, note=36, velocity=0, time=60))
        p = tempfile.NamedTemporaryFile(suffix=".mid", delete=False).name
        _mo.save(p)
        try:
            _n = load_chart_notes(p)
            check("mid oversized tick skipped (good note kept, no raise)",
                  len(_n) == 1 and all(math.isfinite(x[0]) for x in _n))
        except Exception as e:
            print("    (raised %r)" % (e,))
            check("mid oversized tick skipped (good note kept, no raise)", False)
        finally:
            os.unlink(p)
    except Exception as e:
        print("    (oversized-tick harness raised %r)" % (e,))
        check("mid oversized-tick harness ran", False)

    # R14-B2-1: write_chart_midi must skip a magnitude-extreme FINITE note
    # time (round(t*bpm/60*tpb) overflows) rather than raise OverflowError,
    # keeping the other notes -- the export-tail twin of the R13E-1 skip.
    p = tempfile.NamedTemporaryFile(suffix=".mid", delete=False).name
    try:
        write_chart_midi([(0.5, 2, 100), (1e308, 7, 100), (1.0, 7, 90)],
                         120.0, p)
        _n = load_chart_notes(p)
        check("export skips overflowing-time note (others kept, no raise)",
              len(_n) == 2)
    except Exception as e:
        print("    (raised %r)" % (e,))
        check("export skips overflowing-time note (others kept, no raise)",
              False)
    finally:
        os.unlink(p)

    if fails:
        print("ENGINE SELFTEST: %d FAILURE(S): %s" % (len(fails), fails))
        return 1
    print("ENGINE SELFTEST OK")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
