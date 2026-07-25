"""
parakit_synth_voices.py — pure-numpy port of Practice Window v3 WebAudio drum-synth voices.

Reference: Practice Window v3 - Web Edition/parakit-practice-v3.html
  voiceRecipes() / _renderVoices() / metClick  (lines ~1744–1923)

No pygame, no scipy. Algorithm fixed — exact recipe port, not a redesign.
"""

from __future__ import annotations

import math
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

SR_DEFAULT = 44100
LAYERS = (0.35, 0.7, 1.0)  # soft / med / hard brightness L
VOICES = (
    "kick",
    "snare",
    "tom1",
    "tom2",
    "tom3",
    "hihat",
    "hatOpen",
    "ride",
    "crash",
    "crash13",
    "china",
    "tambourine",
    "triangle",
    "neutral",
)

# Voice total durations (seconds) — match voiceRecipes() `.dur` fields.
_VOICE_DUR: Dict[str, float] = {
    "kick": 0.35,
    "snare": 0.32,
    "tom1": 0.4,
    "tom2": 0.45,
    "tom3": 0.55,
    "hihat": 0.12,
    "hatOpen": 0.5,
    "ride": 1.5,
    "crash": 1.4,
    "crash13": 0.9,
    "china": 1.0,
    "tambourine": 0.3,
    "triangle": 2.0,
    "neutral": 0.5,
}

_Q_DEFAULT = 0.7071
_EPS = 1e-12


# ── building blocks ──────────────────────────────────────────────────────────


def _n_samples(dur: float, sr: int) -> int:
    return int(round(dur * sr))


def env(
    peak: float,
    atk: float,
    dec: float,
    t0: float = 0.0,
    total: float = 0.0,
    sr: int = SR_DEFAULT,
) -> np.ndarray:
    """Gain curve over the sample grid for duration `total` seconds.

    At t0: 0.0001
    Exponential ramp to max(0.0002, peak) at t0+atk
    Exponential ramp to 0.0001 at t0+atk+dec
    0 after that.
    """
    n = _n_samples(total, sr) if total > 0 else 0
    if n <= 0:
        return np.zeros(0, dtype=np.float64)
    t = np.arange(n, dtype=np.float64) / float(sr)
    g = np.zeros(n, dtype=np.float64)
    v0 = 0.0001
    v_peak = max(0.0002, float(peak))
    v_end = 0.0001
    t_peak = t0 + atk
    t_end = t0 + atk + dec

    for i in range(n):
        ti = t[i]
        if ti < t0:
            g[i] = 0.0
        elif ti <= t_peak:
            if atk <= 0.0:
                g[i] = v_peak
            else:
                frac = (ti - t0) / atk
                g[i] = v0 * (v_peak / v0) ** frac
        elif ti <= t_end:
            if dec <= 0.0:
                g[i] = v_end
            else:
                frac = (ti - t_peak) / dec
                g[i] = v_peak * (v_end / v_peak) ** frac
        else:
            g[i] = 0.0
    return g


def noise(dur: float, sr: int = SR_DEFAULT) -> np.ndarray:
    """Deterministic LCG white noise in ~[-1, 1). Exact WebAudio port seed/step."""
    n = max(0, int(math.ceil(sr * dur)))
    d = np.empty(n, dtype=np.float64)
    s = 0x2F6E2B1
    for i in range(n):
        s = (s * 1664525 + 1013904223) & 0xFFFFFFFF
        d[i] = s / 2147483648.0 - 1.0
    return d


def tone(
    typ: str,
    f0: float,
    f1: Optional[float],
    dur: float,
    n: int,
    sr: int = SR_DEFAULT,
) -> np.ndarray:
    """Oscillator, optional exponential frequency sweep f0→max(1,f1) over `dur`.

    phase = cumulative sum of 2π·f(t)/sr
    f(t) = f0*(f1/f0)**(t/dur) during the sweep; constant after, or when f1 is None.
    """
    if n <= 0:
        return np.zeros(0, dtype=np.float64)
    t = np.arange(n, dtype=np.float64) / float(sr)
    f = np.empty(n, dtype=np.float64)
    if f1 is None:
        f.fill(float(f0))
    else:
        f1c = max(1.0, float(f1))
        f0c = float(f0)
        if dur <= 0.0:
            f.fill(f1c)
        else:
            ratio = f1c / f0c if f0c != 0.0 else 1.0
            for i in range(n):
                ti = t[i]
                if ti < dur:
                    f[i] = f0c * (ratio ** (ti / dur))
                else:
                    f[i] = f1c
    phase = np.cumsum(2.0 * math.pi * f / float(sr))
    s = np.sin(phase)
    typ = typ.lower()
    if typ == "sine":
        return s
    if typ == "triangle":
        # 2/π · asin(sin) — clip for numerical safety at ±1
        return (2.0 / math.pi) * np.arcsin(np.clip(s, -1.0, 1.0))
    if typ == "square":
        out = np.sign(s)
        # avoid exact-zero samples (rare) becoming silence notches
        out[out == 0.0] = 1.0
        return out
    raise ValueError(f"unknown oscillator type: {typ!r}")


def _biquad_coeffs(
    kind: str, f: float, q: float, sr: int
) -> Tuple[float, float, float, float, float, float]:
    """RBJ Audio-EQ-Cookbook biquad coefficients (a0 not yet normalized out)."""
    f = float(f)
    q = max(float(q), _EPS)
    # Clamp frequency into a safe open interval (0, Nyquist)
    nyq = 0.5 * float(sr)
    f = min(max(f, 1.0), nyq * 0.999)
    w0 = 2.0 * math.pi * f / float(sr)
    cos_w0 = math.cos(w0)
    sin_w0 = math.sin(w0)
    alpha = sin_w0 / (2.0 * q)

    if kind == "lp":
        b0 = (1.0 - cos_w0) * 0.5
        b1 = 1.0 - cos_w0
        b2 = (1.0 - cos_w0) * 0.5
        a0 = 1.0 + alpha
        a1 = -2.0 * cos_w0
        a2 = 1.0 - alpha
    elif kind == "hp":
        b0 = (1.0 + cos_w0) * 0.5
        b1 = -(1.0 + cos_w0)
        b2 = (1.0 + cos_w0) * 0.5
        a0 = 1.0 + alpha
        a1 = -2.0 * cos_w0
        a2 = 1.0 - alpha
    elif kind == "bp":
        # constant 0 dB peak gain form: H(s) = (s/Q) / (s^2 + s/Q + 1)
        b0 = alpha
        b1 = 0.0
        b2 = -alpha
        a0 = 1.0 + alpha
        a1 = -2.0 * cos_w0
        a2 = 1.0 - alpha
    else:
        raise ValueError(f"unknown biquad kind: {kind!r}")
    return b0, b1, b2, a0, a1, a2


def _biquad_filter(x: np.ndarray, kind: str, f: float, q: float, sr: int) -> np.ndarray:
    """Direct-form-I biquad (lfilter-style)."""
    if x.size == 0:
        return x.astype(np.float64, copy=False)
    b0, b1, b2, a0, a1, a2 = _biquad_coeffs(kind, f, q, sr)
    # normalize by a0
    b0, b1, b2 = b0 / a0, b1 / a0, b2 / a0
    a1, a2 = a1 / a0, a2 / a0

    y = np.empty(x.size, dtype=np.float64)
    x1 = x2 = 0.0
    y1 = y2 = 0.0
    for i in range(x.size):
        xi = float(x[i])
        yi = b0 * xi + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        y[i] = yi
        x2, x1 = x1, xi
        y2, y1 = y1, yi
    return y


def bp(x: np.ndarray, f: float, q: float, sr: int = SR_DEFAULT) -> np.ndarray:
    return _biquad_filter(x, "bp", f, q, sr)


def hp(x: np.ndarray, f: float, sr: int = SR_DEFAULT, q: float = _Q_DEFAULT) -> np.ndarray:
    return _biquad_filter(x, "hp", f, q, sr)


def lp(x: np.ndarray, f: float, sr: int = SR_DEFAULT, q: float = _Q_DEFAULT) -> np.ndarray:
    return _biquad_filter(x, "lp", f, q, sr)


# ── component helpers ────────────────────────────────────────────────────────


def _apply_env_to(
    signal: np.ndarray,
    peak: float,
    atk: float,
    dec: float,
    t0: float,
    sr: int,
    voice_n: int,
) -> np.ndarray:
    """Multiply `signal` (starts at absolute time t0) by the env over the full voice grid,
    returning a voice-length buffer with the component placed at sample offset t0."""
    out = np.zeros(voice_n, dtype=np.float64)
    if signal.size == 0 or voice_n <= 0:
        return out
    # Absolute-time env over the full voice duration so t0 scheduling is correct.
    total = voice_n / float(sr)
    g = env(peak, atk, dec, t0=t0, total=total, sr=sr)
    start = int(round(t0 * sr))
    if start < 0:
        # trim left
        signal = signal[-start:]
        start = 0
    end = min(voice_n, start + signal.size)
    n_copy = end - start
    if n_copy <= 0:
        return out
    # Component samples align with absolute times start/sr, (start+1)/sr, ...
    out[start:end] = signal[:n_copy] * g[start:end]
    return out


def _tone_component(
    typ: str,
    f0: float,
    f1: Optional[float],
    sweep_dur: float,
    stop: float,
    peak: float,
    atk: float,
    dec: float,
    sr: int,
    voice_n: int,
    t0: float = 0.0,
    filt: Optional[Callable[[np.ndarray], np.ndarray]] = None,
) -> np.ndarray:
    n = max(0, int(math.ceil(sr * stop)))  # generate at least through stop
    # when t0 > 0, stop is relative length of active region; generate `stop` samples of source
    # For standard recipes stop is absolute end time and t0=0, so n ≈ stop*sr.
    if t0 > 0.0:
        n = max(0, int(math.ceil(sr * stop)))  # active length = stop (duration)
    else:
        n = max(0, int(math.ceil(sr * stop)))
    src = tone(typ, f0, f1, sweep_dur, n, sr=sr)
    if filt is not None:
        src = filt(src)
    return _apply_env_to(src, peak, atk, dec, t0, sr, voice_n)


def _noise_component(
    noise_dur: float,
    peak: float,
    atk: float,
    dec: float,
    sr: int,
    voice_n: int,
    t0: float = 0.0,
    stop: Optional[float] = None,
    filt: Optional[Callable[[np.ndarray], np.ndarray]] = None,
) -> np.ndarray:
    src = noise(noise_dur, sr=sr)
    if stop is not None and t0 == 0.0:
        n_stop = int(math.ceil(sr * stop))
        if src.size > n_stop:
            src = src[:n_stop]
    if filt is not None:
        src = filt(src)
    return _apply_env_to(src, peak, atk, dec, t0, sr, voice_n)


def _finalize(buf: np.ndarray) -> np.ndarray:
    """Crop already applied; normalize only if peak > 1.0. Return float32."""
    if buf.size == 0:
        return buf.astype(np.float32)
    peak = float(np.max(np.abs(buf)))
    if peak > 1.0:
        buf = buf / peak
    return buf.astype(np.float32)


# ── voice recipes ────────────────────────────────────────────────────────────


def _render_kick(L: float, sr: int, voice_n: int) -> np.ndarray:
    buf = np.zeros(voice_n, dtype=np.float64)
    buf += _tone_component(
        "sine", 150.0, 48.0, 0.11, 0.30, 0.95, 0.004, 0.22, sr, voice_n
    )
    buf += _noise_component(
        0.05,
        0.22 * L + 0.05,
        0.001,
        0.028,
        sr,
        voice_n,
        stop=0.05,
        filt=lambda x: hp(x, 1200.0 + 1800.0 * L, sr=sr),
    )
    return buf


def _render_snare(L: float, sr: int, voice_n: int) -> np.ndarray:
    buf = np.zeros(voice_n, dtype=np.float64)
    buf += _tone_component(
        "triangle", 196.0, 148.0, 0.13, 0.22, 0.34, 0.002, 0.12, sr, voice_n
    )
    buf += _noise_component(
        0.26,
        0.5 + 0.18 * L,
        0.001,
        0.17,
        sr,
        voice_n,
        stop=0.26,
        filt=lambda x: bp(x, 1500.0 + 2200.0 * L, 0.7, sr=sr),
    )
    return buf


def _render_tom(L: float, sr: int, voice_n: int, base: float) -> np.ndarray:
    buf = np.zeros(voice_n, dtype=np.float64)
    buf += _tone_component(
        "sine", base, base * 0.55, 0.24, 0.40, 0.72, 0.003, 0.26, sr, voice_n
    )
    buf += _tone_component(
        "triangle",
        base * 1.5,
        base * 0.8,
        0.18,
        0.30,
        0.16 + 0.1 * L,
        0.003,
        0.18,
        sr,
        voice_n,
    )
    return buf


def _render_hihat(L: float, sr: int, voice_n: int) -> np.ndarray:
    buf = np.zeros(voice_n, dtype=np.float64)
    buf += _noise_component(
        0.1,
        0.42,
        0.001,
        0.045 + 0.02 * L,
        sr,
        voice_n,
        stop=0.1,
        filt=lambda x: hp(x, 6800.0 + 1800.0 * L, sr=sr),
    )
    buf += _tone_component(
        "square",
        8200.0,
        None,
        0.05,
        0.06,
        0.06 * L,
        0.001,
        0.03,
        sr,
        voice_n,
        filt=lambda x: hp(x, 8000.0, sr=sr),
    )
    return buf


def _render_hat_open(L: float, sr: int, voice_n: int) -> np.ndarray:
    buf = np.zeros(voice_n, dtype=np.float64)
    buf += _noise_component(
        0.45,
        0.34,
        0.001,
        0.32 + 0.1 * L,
        sr,
        voice_n,
        stop=0.45,
        filt=lambda x: hp(x, 6400.0, sr=sr),
    )
    return buf


def _render_ride(L: float, sr: int, voice_n: int) -> np.ndarray:
    buf = np.zeros(voice_n, dtype=np.float64)
    buf += _noise_component(
        1.4,
        0.16,
        0.002,
        0.95,
        sr,
        voice_n,
        stop=1.4,
        filt=lambda x: hp(x, 5800.0, sr=sr),
    )
    # sine ping — no filter in the HTML recipe
    buf += _tone_component(
        "sine", 4180.0, None, 0.5, 1.0, 0.16 + 0.1 * L, 0.001, 0.42, sr, voice_n
    )
    buf += _tone_component(
        "square",
        522.0,
        None,
        0.4,
        1.0,
        0.07,
        0.002,
        0.5,
        sr,
        voice_n,
        filt=lambda x: lp(x, 1300.0, sr=sr),
    )
    return buf


def _render_crash(L: float, sr: int, voice_n: int, spread: float) -> np.ndarray:
    buf = np.zeros(voice_n, dtype=np.float64)
    nd = 1.3 / spread
    buf += _noise_component(
        nd,
        0.42 + 0.12 * L,
        0.001,
        0.85 / spread,
        sr,
        voice_n,
        stop=nd,
        filt=lambda x: hp(x, 4200.0, sr=sr),
    )
    stop_o = 1.2 / spread
    for fr in (1046.0, 1567.0, 2489.0):
        buf += _tone_component(
            "square",
            fr * spread,
            fr * spread * 0.95,
            0.7,
            stop_o,
            0.05,
            0.002,
            0.65 / spread,
            sr,
            voice_n,
            filt=lambda x: hp(x, 2600.0, sr=sr),
        )
    return buf


def _render_china(L: float, sr: int, voice_n: int) -> np.ndarray:
    buf = np.zeros(voice_n, dtype=np.float64)
    buf += _noise_component(
        0.9,
        0.55 + 0.15 * L,
        0.001,
        0.6,
        sr,
        voice_n,
        stop=0.9,
        filt=lambda x: bp(x, 3400.0, 0.35, sr=sr),
    )
    for fr in (831.0, 1247.0, 2773.0):
        buf += _tone_component(
            "square",
            fr * (1.0 + 0.04 * L),
            fr * 0.92,
            0.5,
            0.9,
            0.075,
            0.001,
            0.5,
            sr,
            voice_n,
            filt=lambda x: hp(x, 2200.0, sr=sr),
        )
    return buf


def _render_tambourine(L: float, sr: int, voice_n: int) -> np.ndarray:
    buf = np.zeros(voice_n, dtype=np.float64)
    for i in range(3):
        t0 = i * 0.012
        # each grain: noise(0.08) → bp → env at t0; active t0..t0+0.08
        fi = 7400.0 + 900.0 * i

        def _mk_filt(freq: float = fi) -> Callable[[np.ndarray], np.ndarray]:
            return lambda x, ff=freq: bp(x, ff, 1.4, sr=sr)

        buf += _noise_component(
            0.08,
            0.3 + 0.12 * L,
            0.001,
            0.05,
            sr,
            voice_n,
            t0=t0,
            filt=_mk_filt(),
        )
    return buf


def _render_triangle_voice(L: float, sr: int, voice_n: int) -> np.ndarray:
    buf = np.zeros(voice_n, dtype=np.float64)
    for fr, p in ((4530.0, 0.22), (6780.0, 0.12), (9050.0, 0.07)):
        buf += _tone_component(
            "sine",
            fr,
            None,
            1.8,
            1.95,
            p * (0.7 + 0.4 * L),
            0.001,
            1.6,
            sr,
            voice_n,
            filt=lambda x: hp(x, 3200.0, sr=sr),
        )
    return buf


def _render_neutral(L: float, sr: int, voice_n: int) -> np.ndarray:
    buf = np.zeros(voice_n, dtype=np.float64)
    buf += _tone_component(
        "triangle", 660.0, 440.0, 0.3, 0.45, 0.4, 0.002, 0.3, sr, voice_n
    )
    buf += _noise_component(
        0.1,
        0.18,
        0.001,
        0.06,
        sr,
        voice_n,
        stop=0.1,
        filt=lambda x: hp(x, 3000.0 + 2000.0 * L, sr=sr),
    )
    return buf


_RENDERERS: Dict[str, Callable[[float, int, int], np.ndarray]] = {
    "kick": _render_kick,
    "snare": _render_snare,
    "tom1": lambda L, sr, n: _render_tom(L, sr, n, 230.0),
    "tom2": lambda L, sr, n: _render_tom(L, sr, n, 172.0),
    "tom3": lambda L, sr, n: _render_tom(L, sr, n, 118.0),
    "hihat": _render_hihat,
    "hatOpen": _render_hat_open,
    "ride": _render_ride,
    "crash": lambda L, sr, n: _render_crash(L, sr, n, 1.0),
    "crash13": lambda L, sr, n: _render_crash(L, sr, n, 1.32),
    "china": _render_china,
    "tambourine": _render_tambourine,
    "triangle": _render_triangle_voice,
    "neutral": _render_neutral,
}


# ── public API ───────────────────────────────────────────────────────────────


def render_voice(name: str, L: float, sr: int = SR_DEFAULT) -> np.ndarray:
    """Mono float32 in [-1,1], one voice at brightness layer L."""
    if name not in _RENDERERS:
        raise ValueError(f"unknown voice: {name!r}; expected one of {VOICES}")
    dur = _VOICE_DUR[name]
    voice_n = _n_samples(dur, sr)
    buf = _RENDERERS[name](float(L), int(sr), voice_n)
    # ensure exact length (crop/pad)
    if buf.size > voice_n:
        buf = buf[:voice_n]
    elif buf.size < voice_n:
        pad = np.zeros(voice_n, dtype=np.float64)
        pad[: buf.size] = buf
        buf = pad
    return _finalize(buf)


def render_all(sr: int = SR_DEFAULT) -> dict:
    """{voice_name: [layer0, layer1, layer2]} — all mono float32 arrays."""
    out: Dict[str, List[np.ndarray]] = {}
    for name in VOICES:
        out[name] = [render_voice(name, L, sr=sr) for L in LAYERS]
    return out


def met_click(accent: bool, sr: int = SR_DEFAULT) -> np.ndarray:
    """Square 1760Hz (accent) / 1175Hz; gain starts 0.5/0.3, exp-decays to
    0.001 over 0.055s; total length 0.08s."""
    n = _n_samples(0.08, sr)
    freq = 1760.0 if accent else 1175.0
    peak = 0.5 if accent else 0.3
    # constant-frequency square
    src = tone("square", freq, None, 0.08, n, sr=sr)
    t = np.arange(n, dtype=np.float64) / float(sr)
    g = np.empty(n, dtype=np.float64)
    t_dec = 0.055
    v_end = 0.001
    for i in range(n):
        ti = t[i]
        if ti <= t_dec:
            if t_dec <= 0.0:
                g[i] = v_end
            else:
                g[i] = peak * (v_end / peak) ** (ti / t_dec)
        else:
            # hold terminal gain until stop (WebAudio leaves last automation value)
            g[i] = v_end
    return (src * g).astype(np.float32)


def to_stereo_i16(mono: np.ndarray) -> np.ndarray:
    """float mono -> (N,2) int16, clipped, both channels equal."""
    x = np.asarray(mono, dtype=np.float64).reshape(-1)
    x = np.clip(x, -1.0, 1.0)
    i16 = np.round(x * 32767.0).astype(np.int16)
    return np.column_stack((i16, i16))


# ── selftest ─────────────────────────────────────────────────────────────────


def _selftest() -> None:
    sr = SR_DEFAULT
    rows: List[Tuple[str, int, float]] = []
    for name in VOICES:
        dur = _VOICE_DUR[name]
        expect_n = _n_samples(dur, sr)
        for L in LAYERS:
            a = render_voice(name, L, sr=sr)
            assert a.dtype == np.float32, f"{name} L={L}: dtype {a.dtype}"
            assert a.ndim == 1, f"{name} L={L}: not mono"
            assert a.size == expect_n, (
                f"{name} L={L}: len {a.size} != round(dur*sr)={expect_n}"
            )
            peak = float(np.max(np.abs(a))) if a.size else 0.0
            assert peak <= 1.0 + 1e-6, f"{name} L={L}: peak {peak} > 1"
            rms = float(np.sqrt(np.mean(a.astype(np.float64) ** 2))) if a.size else 0.0
            assert rms > 1e-4, f"{name} L={L}: silent rms={rms}"
        # table row uses middle layer
        mid = render_voice(name, LAYERS[1], sr=sr)
        rms_mid = float(np.sqrt(np.mean(mid.astype(np.float64) ** 2)))
        rows.append((name, mid.size, rms_mid))

    click = met_click(True, sr=sr)
    expect_click = _n_samples(0.08, sr)
    assert click.dtype == np.float32
    assert click.size == expect_click, f"met_click len {click.size} != {expect_click}"
    # peaks near the start (first 10% of samples holds the max)
    abs_c = np.abs(click.astype(np.float64))
    peak_i = int(np.argmax(abs_c))
    assert peak_i < max(1, expect_click // 10), (
        f"met_click peak index {peak_i} not near start"
    )
    assert float(abs_c[peak_i]) > 0.1, "met_click nearly silent at peak"

    # light API smoke
    allv = render_all(sr=sr)
    assert set(allv.keys()) == set(VOICES)
    for name in VOICES:
        assert len(allv[name]) == 3
    st = to_stereo_i16(click)
    assert st.shape == (click.size, 2) and st.dtype == np.int16

    print("SELFTEST OK")
    print(f"{'voice':<14} {'len':>8} {'rms':>12}")
    print("-" * 36)
    for name, length, rms in rows:
        print(f"{name:<14} {length:8d} {rms:12.6f}")
    print(f"{'met_click':<14} {click.size:8d} "
          f"{float(np.sqrt(np.mean(click.astype(np.float64)**2))):12.6f}")


if __name__ == "__main__":
    _selftest()
