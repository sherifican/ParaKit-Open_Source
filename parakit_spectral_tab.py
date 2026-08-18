"""parakit_spectral_tab.py -- Spectral Comparison tab, v4-embeddable module.

A single-file, stdlib+tKinter redesign of the v5 PySide6 "Spectral Comparison"
tab (`spectral.py`, Spectral v0.12.93), slotted into ParaKit v4 as a new tab.

WHAT THIS TAB DOES (same as v5):
  You charted a song -- the detector turned drum audio into notes. Load a
  REFERENCE audio + a CANDIDATE chart, press Compare, and the tab overlays the
  chart notes on the audio's spectral content two ways:
    - Per-Lane view: one row per drum lane -- band-energy ribbon + onset ticks
      + chart-note diamonds + MISS/PHANTOM flags.
    - Spectrogram view: a magma spectrogram heatmap + drum-band guides + a
      per-lane note-row strip with flags.
  A green "+" = MISS (audio hit, no charted note); an orange "x" = PHANTOM
  (charted note, no audio). Energy alone is not a miss -- only the flags are
  real problems.

EMBEDDING CONTRACT (ParaKit v4):
  ``SpectralTab(parent, hooks=None)`` builds into any parent frame.
  ``hooks`` is either ``None`` (standalone review host) or a dict supplied by
  the v4 side with these optional keys:
    - ``decode_audio(path) -> (samples_float32_mono, sr)``
    - ``get_cfg(key, default)`` / ``set_cfg(key, value)``
    - ``mixer_play(path, start_s)`` / ``mixer_stop()`` / ``mixer_pos()``
  Missing or raising hooks degrade to the standalone behaviour for that seam
  with a one-line status note; they never crash the tab.

STYLES:
  All ttk styles used by the tab live in the ``Spec.*`` namespace so the tab
  is a self-contained panel that does not clobber v4's app-wide theming.
  ``apply_theme_global(root)`` (standalone ``__main__``) configures both the
  bare legacy names and the ``Spec.*`` names; ``apply_theme_embedded(widget)``
  (called inside ``SpectralTab`` when ``hooks`` is present) configures only the
  ``Spec.*`` names.

Structure:
    apply_theme_global / apply_theme_embedded  -- ttk style setup.
    Tooltip / Chip / Segmented / OutlineButton / PlaceholderEntry -- widgets.
    MockSpectralModel / _SpectralModel         -- UI-facing model objects.
    LaneViewCanvas                             -- Per-Lane view (tk.Canvas).
    GramView                                   -- Spectrogram view.
    SpectralTab(ttk.Frame)                     -- the embeddable tab.
    __main__                                   -- standalone review host.
"""
from __future__ import annotations

import bisect
import functools
import math
import os
import queue
import random
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# ---------------------------------------------------------------------------
# Palette -- ParaKit v4 dark-purple identity. Kept as module constants so the
# v4 theming pass can swap them in one place.
# ---------------------------------------------------------------------------
PURPLE = "#7c3aed"        # primary
PURPLE_LT = "#b388ff"     # light purple (text/links/accents)
PURPLE_EDGE = "#8b5cf6"   # purple border/edge
PURPLE_DEEP = "#2a1235"   # deep purple shadow
MAGENTA = "#bd02c1"       # logo magenta
CYAN = "#00d4d4"          # app accent -- highlights important text/settings
PANEL = "#15152a"         # panel background
ROW_ALT = "#181830"       # alternating row
BG = "#0d0d1a"            # deep background
DARKER = "#0c0712"        # darker still
INPUT_BG = "#1d1a2e"      # input field bg
INPUT_FG = "#e8e0ff"      # input field fg
TEXT = "#e0e0e0"          # body text
MUTED = "#7e7e96"         # muted/secondary text
GREEN = "#00c853"         # success green
AMBER = "#e09a3a"         # warning amber

# Canvas-specific colours (match the v5 views so the two apps read alike).
CANVAS_BG = "#070710"
MISS_GREEN = "#39ff14"
# One white constant used to serve five unrelated jobs (phantom flags, note
# outlines, note flash, and BOTH playheads), so the playhead was drawn in the
# same colour as a PHANTOM flag -- a collision between the two things this tab
# exists to tell apart. Split into distinct roles 2026-07-19; keep them distinct.
PHANTOM_ORANGE = "#ff9100"   # PHANTOM flag: ring, glyph, dotted connector
NOTE_OUTLINE = "#ffffff"     # charted-note diamond outline + flash fill
PLAYHEAD_COL = "#00ff88"     # transport playhead -- v4's playhead green
TIME_YELLOW = "#ffd54a"      # live transport readout
CHIP_OFF_EDGE = "#3a3a55"
SEPARATOR = "#2b2b45"
OUTLINE_DARK = "#0b0b14"      # dark halo behind outlined lane-colour text

F_H1 = ("Segoe UI", 12, "bold")
F_BASE = ("Segoe UI", 9)
F_BOLD = ("Segoe UI", 9, "bold")
F_SMALL = ("Segoe UI", 8)
F_MONO = ("Consolas", 8)
F_MONO_B = ("Consolas", 10, "bold")
F_MONO_SMALL_B = ("Consolas", 8, "bold")   # gutter MIDI-note number

TICK_COL = "#554477"          # faint slider tick marks (matches host app)


def _spec_tick_strip(parent, length, bg=PANEL, tick_px=10):
    """Pack a faint tick-mark canvas below a slider -- mirrors the host app's
    _tick_strip so the Spectral sliders show the same ticks the other option
    sliders do. Call right after the Scale is packed inside a small wrapper
    Frame that holds only the scale + this canvas."""
    c = tk.Canvas(parent, width=length, height=6, bg=bg,
                  highlightthickness=0, bd=0)
    c.pack(side=tk.TOP, anchor="w")
    n = max(2, length // tick_px)
    for i in range(n + 1):
        x = int(round(i * (length - 1) / n))
        c.create_line(x, 0, x, 4, fill=TICK_COL, width=1)
    return c


# ---------------------------------------------------------------------------
# Lane / band data -- VERBATIM semantics from v5 spectral_engine.py LANES /
# BANDS (which mirror v4's MIDI_EDITOR_LANES order + palette).
# (idx, name, colour, band key, per-lane raw slice label, shared-band label)
# ---------------------------------------------------------------------------
LANES = (
    (0, "Hi-Hat", "#00e5ff", "hat",   "8k-16k Hz (top sizzle)", "Cymbal band (shared)"),
    (1, "Crash",  "#ff8c00", "hat",   "3k-8k Hz (body / wash)", "Cymbal band (shared)"),
    (2, "Snare",  "#e63946", "snare", "170-360 Hz",             "Snare band (own)"),
    (3, "Tom 1",  "#1a3a8f", "tom",   "160-300 Hz (high)",      "Tom band (shared)"),
    (4, "Tom 2",  "#2e8b57", "tom",   "110-180 Hz (mid)",       "Tom band (shared)"),
    (5, "Tom 3",  "#7b2d8b", "tom",   "70-120 Hz (floor)",      "Tom band (shared)"),
    (6, "Ride",   "#ffd700", "hat",   "2.5k-5.5k Hz (ping)",    "Cymbal band (shared)"),
    (7, "Kick",   "#ff69b4", "kick",  "34-110 Hz",              "Kick band (own)"),
)
# 4 physical bands: (key, label, freq_lo, freq_hi) -- v5 spectral_engine.BANDS.
BANDS = (
    # The cymbal band collapses ALL cymbals (Hi-Hat + Crash + Ride) into one
    # frequency band, so it is labelled "Cymbals" (the individual Hi-Hat / Crash
    # / Ride lanes are still shown separately in the note-strip rows below).
    ("hat",   "Cymbals", 3800, 16000),
    ("snare", "Snare",   170,  360),
    ("tom",   "Toms",    90,   300),
    ("kick",  "Kick",    34,   110),
)
# Note colours when the Spectrogram "Chart colors" toggle is OFF (v5
# BAND_COLORS): notes coloured by drum band instead of by lane.
BAND_COLORS = {"hat": "#00e5ff", "snare": "#ff69b4",
               "tom": "#9f67ff", "kick": "#ffb347"}
LANE_TO_BAND = {0: "hat", 1: "hat", 2: "snare", 3: "tom",
                4: "tom", 5: "tom", 6: "hat", 7: "kick"}
# GM drum note the engine WRITES per lane (mirrors engine.LANE_MIDI_OUT); shown
# in the gutter so users know each lane's MIDI number. Hi-Hat 42, Crash 49,
# Snare 38, Tom1 48, Tom2 45, Tom3 41, Ride 51, Kick 36.
LANE_MIDI_OUT = (42, 49, 38, 48, 45, 41, 51, 36)
# Note-strip row order, frequency-sorted (v5 ROLL_ROWS).
ROLL_ROWS = (0, 1, 6, 3, 4, 5, 2, 7)

# Geometry constants (px).
RULER_H = 20          # time ruler strip at the top of each canvas
# Fill-height layout: both canvases DERIVE their row heights from the live
# widget height on <Configure> (debounced ~120 ms). LANE_ROW_H / GRAM_H below
# are only the INITIAL values used before the first Configure event lands.
LANE_ROW_H = 46       # initial Per-Lane row height (pre-first-resize)
LANE_ROW_H_MIN = 34   # floor: below this flags/diamonds stop being hittable
LANE_ROW_H_MAX = 110  # ceiling: past this rows read as empty slabs
GRAM_H = 250          # initial spectrogram heatmap height (pre-first-resize)
GRAM_H_MIN = 180      # floor: keeps the drum band (30-500 Hz) readable on a
                      # log axis -- below this kick/snare/tom share ~40 px
GRAM_H_MAX = 720      # ceiling: ~2.4 px per master row (GRAM_ROWS=300)
STRIP_ROW_H = 26      # per-lane note-row height under the heatmap (FIXED)
GUTTER_W = 140        # Per-Lane label gutter width
AXIS_W = 58           # Spectrogram freq-axis gutter width

ZOOM_DEFAULT = 200.0  # px per second (v5 SPEC_ZOOM_DEFAULT)
ZOOM_MIN = 30.0
ZOOM_MAX = 1200.0


def px_span(dur, pps):
    """Pixel width of `dur` seconds at `pps` px/s, always a finite float.

    Every `int(dur * pps)` site must go through this (breaker R6B2-3 +
    R7B2-1, 2026-07-20): the product overflows to inf for dur >= ~9e305 —
    a FINITE dur that passes every upstream guard — and `min()` is not a NaN
    filter, so `int()` raised OverflowError/ValueError. The positively-phrased
    range test rejects NaN and inf alike; 1e9 px is ~10x any usable canvas.
    """
    span = dur * pps
    if not (0.0 < span < float("inf")):
        return 0.0
    return min(span, 1e9)


def _sort_cache(items, keyfn):
    """(sorted items, parallel sorted key list) for O(log n) window slicing.
    v4.9.0 playhead-lag fix: the per-frame overlay used to LINEAR-scan every
    note + issue (O(N)) each of ~33 frames/s, so a dense chart spent ~20 ms/frame
    scanning off-screen notes — enough to overrun the 30 ms tick budget and make
    the playhead visibly stutter/lag. With a sorted key list, redraw_overlay
    bisects to just the on-screen slice (see _iter_window)."""
    s = sorted(items or [], key=keyfn)
    return s, [keyfn(x) for x in s]


def _iter_window(sorted_items, sorted_keys, t0, t1):
    """Yield only the items whose key is within [t0, t1] (both sorted)."""
    lo = bisect.bisect_left(sorted_keys, t0)
    hi = bisect.bisect_right(sorted_keys, t1)
    return sorted_items[lo:hi]


def _active_lane_set(model):
    """The set of lane indices carrying >=1 note or issue. Precomputed once at
    set_model time (F3, 2026-07-22) so _lane_active is an O(1) membership test
    instead of re-scanning every note + issue PER LANE (8 x O(N)) on each of the
    ~33 redraws/s — a dense chart spent that scan purely to decide lane tint."""
    s = set()
    for n in getattr(model, "notes", None) or []:
        try:
            s.add(int(n[1]))
        except (TypeError, ValueError, IndexError):
            pass
    for i in getattr(model, "issues", None) or []:
        try:
            s.add(int(i["lane"]))
        except (TypeError, ValueError, KeyError):
            pass
    return s


# Grid-line tiers (bar / beat / subdivision) — MIDI-editor look.
_GRID_BAR = "#4a3f6e"
_GRID_BEAT = "#3a3160"
_GRID_SUB = "#241d3a"
# label -> subdivisions per beat (1/4 = the beat itself)
GRID_DIVS = (("1/4", 1), ("1/8", 2), ("1/16", 4), ("1/32", 8))

# Lane-edit delete tolerance is "8 px worth of time" (8/pps). At Fit zoom pps can
# be ~0.01, making 8/pps ~800 s -> a click ANYWHERE deletes the nearest note in
# the lane (silent data loss, review #5, 2026-07-21). Cap it so a delete needs a
# genuinely nearby note; at extreme zoom-out the user must zoom in to delete.
_LANE_DEL_TOL_MAX_S = 0.5


def _paint_note(c, x, yc, r, fill, shape, tag="overlay"):
    """Draw one chart-note marker (v4.9.0 note-shape toggle): a lane-coloured
    DIAMOND (default) or a classic thin vertical BAR like the MIDI editor, each
    with a black halo for contrast."""
    if shape == "bar":
        bw = max(1.5, r * 0.5)
        bh = r * 1.4
        c.create_rectangle(x - bw - 1, yc - bh - 1, x + bw + 1, yc + bh + 1,
                           fill="", outline="#000000", width=1, tags=tag)
        c.create_rectangle(x - bw, yc - bh, x + bw, yc + bh,
                           fill=fill, outline=NOTE_OUTLINE, width=1, tags=tag)
    else:
        pts = (x, yc - r, x + r, yc, x, yc + r, x - r, yc)
        halo = (x, yc - r - 1.5, x + r + 1.5, yc,
                x, yc + r + 1.5, x - r - 1.5, yc)
        c.create_polygon(halo, fill="", outline="#000000", width=2, tags=tag)
        c.create_polygon(pts, fill=fill, outline=NOTE_OUTLINE, width=1, tags=tag)


def _paint_time_grid(view, x0, x1, top, bottom):
    """Draw MIDI-editor-style bar/beat/subdivision timing lines across x0..x1
    (v4.9.0). Shared by both views; reads view._model.bpm, view._pps,
    view.opt['grid_div'], and view._t_of / _x_of. Static layer (tag 'static')."""
    m = getattr(view, "_model", None)
    if m is None:
        return
    bpm = _safe_bpm(m.bpm)
    beat = 60.0 / bpm if bpm > 0 else 0.0
    if beat <= 0:
        return
    div = max(1, int(view.opt.get("grid_div", 4)))
    sub = beat / div
    sub_px = sub * view._pps
    # Thin the subdivision as it gets dense so we never draw sub-pixel spam
    # (mirrors the MIDI editor / Preview grid).
    while sub_px < 5.0 and div > 1:
        div //= 2
        sub = beat / div
        sub_px = sub * view._pps
    if sub_px < 1.0:
        return
    t_lo, t_hi = view._t_of(x0), view._t_of(x1)
    if sub <= 0 or (t_hi - t_lo) / sub > 6000:
        return
    c = view.canvas
    per_bar = div * 4
    si = max(0, int(math.floor(t_lo / sub)))
    while True:
        t = si * sub
        if t > t_hi:
            break
        x = view._x_of(t)
        if x >= x0 - 1:
            on_beat = (si % div) == 0
            on_bar = (si % per_bar) == 0
            col = _GRID_BAR if on_bar else (_GRID_BEAT if on_beat else _GRID_SUB)
            c.create_line(x + 0.5, top, x + 0.5, bottom, fill=col,
                          width=1, tags="static")
        si += 1
# Fit bypasses the slider floor (breaker R3E-2): hard floor so an absurd
# duration still yields a finite, positive px/s.
ZOOM_FIT_MIN = 0.01

# Colormap stops (v5 spectral_gram_view._CMAP_STOPS).
CMAP_STOPS = {
    "magma": (
        (0.00, (7, 4, 18)), (0.18, (40, 18, 72)), (0.42, (124, 58, 237)),
        (0.62, (189, 2, 193)), (0.80, (255, 107, 157)), (0.93, (255, 214, 160)),
        (1.00, (255, 255, 255)),
    ),
    "cyan": (
        (0.00, (5, 8, 18)), (0.25, (10, 52, 80)), (0.50, (0, 150, 170)),
        (0.72, (0, 229, 255)), (0.90, (170, 245, 255)), (1.00, (255, 255, 255)),
    ),
    "gray": (
        (0.00, (8, 8, 16)), (1.00, (245, 245, 255)),
    ),
}


# ---------------------------------------------------------------------------
# Mock / engine adapter data constants.
# ---------------------------------------------------------------------------
ENV_FPS = 50          # mock energy-envelope resolution (frames per second)
# Spectrogram master-grid width (time columns). This is the resolution the
# heatmap is decimated to; zoom re-windows this grid, so at high zoom a small
# value stretches each master column across many display pixels -> the blocky
# "blended divides" look. Raised 1200 -> 6000 (v4.9.2) so ~5x more time detail
# survives before the stretch shows. The hard ceiling is still the STFT hop
# (~86 frames/s), so beyond that some stretch is inherent; 6000 covers songs up
# to ~70s at full STFT detail and is far crisper than 1200 for longer ones.
# Cost is one-time (built once per Compare in the worker) + ~1.8 MB; the display
# render loop iterates DISPLAY pixels, not master columns, so scroll/zoom cost
# is unchanged.
GRAM_COLS = 6000      # spectrogram master width (time columns)
GRAM_ROWS = 300       # spectrogram master height (log-freq rows)
_SCROLL_RENDER_MS = 28   # v4.9.2 lag fix: throttle the re-window/redraw on scroll
GRAM_FMIN = 30.0
GRAM_FMAX = 16000.0
NEAR = 0.085          # +-85 ms onset<->note agreement (v5 spectral_engine.NEAR)


def hex_rgb(color: str):
    """'#rrggbb' -> (r, g, b) ints."""
    color = color.lstrip("#")
    return (int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16))


def rgb_hex(r: int, g: int, b: int) -> str:
    return "#%02x%02x%02x" % (max(0, min(255, int(r))),
                              max(0, min(255, int(g))),
                              max(0, min(255, int(b))))


def readable_on_dark(color: str) -> str:
    """A lane colour bright enough to read as TEXT on the dark panel. Dark lane
    hues (Tom 1 navy, Tom 3 purple) are lifted toward white while keeping their
    hue; already-bright hues pass through. So the gutter text matches the lane
    and stays legible."""
    r, g, b = hex_rgb(color)
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    # Threshold 100: only the genuinely-too-dark hues (Tom 1 navy ~58,
    # Tom 3 purple ~79) lift; Snare red (~110) stays RED, Tom 2 green stays green.
    if lum < 100:
        return blend(color, "#ffffff", 0.42)
    return color


def blend(c1: str, c2: str, t: float) -> str:
    """Linear blend c1 -> c2, t in [0,1]."""
    a, b = hex_rgb(c1), hex_rgb(c2)
    return rgb_hex(a[0] + (b[0] - a[0]) * t,
                   a[1] + (b[1] - a[1]) * t,
                   a[2] + (b[2] - a[2]) * t)


def cmap_lut(key: str, brightness: float = 1.0):
    """256-entry colormap lookup as a list of 3-byte RGB values."""
    stops = CMAP_STOPS.get(key) or CMAP_STOPS["magma"]
    lut = []
    for i in range(256):
        v = max(0.0, min(1.0, (i / 255.0) * brightness))
        placed = stops[-1][1]
        for j in range(len(stops) - 1):
            p0, c0 = stops[j]
            p1, c1 = stops[j + 1]
            if v <= p1:
                t = (v - p0) / (p1 - p0) if p1 > p0 else 0.0
                placed = tuple(int(c0[k] + (c1[k] - c0[k]) * t) for k in range(3))
                break
        lut.append(bytes(placed))
    return lut


# Floor >= the MIDI exporter's encodable minimum (breaker R10-B2-1): a
# set_tempo meta caps at 16777215 us/beat = 6e7/16777215 = 3.5763 BPM, so a
# bpm below that is accepted everywhere else but fails Export/Overwrite MIDI
# with a raw mido "0..16777215" message. 4.0 clears it with margin and still
# sits far below the ~23 BPM slowest real ParaDB chart.
MIN_PLAUSIBLE_BPM = 4.0
MAX_PLAUSIBLE_BPM = 100000.0   # ~100x the fastest real chart; see _safe_bpm


def _safe_bpm(bpm) -> float:
    """A finite, positive, PLAUSIBLE BPM — 120 otherwise (breaker R8B2-1 +
    R9-B4-1/R9E-1, 2026-07-20). `chart_bpm` filters <=0 and NaN but PASSED inf,
    and the old `float(bpm) if bpm else 120.0` sanitizer filtered only 0/None/''
    (inf and NaN are truthy). A non-finite bpm made snap_time's `(60/bpm)/4`
    collapse to 0.0 and note-edit divide by zero (R8B2-1). Round 9: finiteness
    alone was not enough — the step `(60/bpm)/4` and `t/step` must stay finite,
    and a FINITE-but-EXTREME bpm breaks both ends: ~1e308 makes `step` ~1e-307
    so `round(t/step)` overflows to +inf -> OverflowError on edit (R9-B4-1),
    while ~5e-324 makes `60/bpm` overflow so every note snaps to 0.0 (R9E-1
    silent-wrong). A plausible-MUSICAL-RANGE bound closes both — real charts
    sit in ~[40, 1000] BPM, so [1, 1e5] rejects only corrupt/hostile values.
    Same boundary-guard idiom the campaign applied to `dur` (R2B2-2)."""
    try:
        b = float(bpm)
    except (TypeError, ValueError):
        return 120.0
    return b if (math.isfinite(b)
                 and MIN_PLAUSIBLE_BPM <= b <= MAX_PLAUSIBLE_BPM) else 120.0


def fmt_time(t: float) -> str:
    """Seconds -> m:ss.d for the transport readout."""
    t = max(0.0, float(t))
    # +inf slips max(0.0, …): inf//60 is nan and int(nan) raises (breaker
    # R8B4-1, 2026-07-20). This is the 4th _model.dur consumer — the px_span
    # fold (R7B2-1) guarded the three RENDER sites but not the time label,
    # so a +inf-dur model froze the playhead loop and hard-raised _on_stop.
    # Same finite-guard idiom as _build_image; covers both readout slots.
    if not math.isfinite(t):
        return "0:00.0"
    return "%d:%04.1f" % (int(t // 60), t % 60.0)


# ---------------------------------------------------------------------------
# Theme -- ALL ttk styling lives here. apply_theme_global is the standalone
# host entry point and configures both legacy bare names and the Spec.* names
# that SpectralTab widgets actually use. apply_theme_embedded is called when
# the tab is constructed with hooks and configures only the Spec.* namespace,
# leaving the parent app's styles untouched.
# ---------------------------------------------------------------------------
def apply_theme_global(root: tk.Misc) -> ttk.Style:
    style = ttk.Style(root)
    # 'clam' is the only stock theme whose colours are fully configurable on
    # Windows (vista/xpnative ignore background overrides).
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    _configure_bare_styles(style)
    _configure_spec_styles(style)
    root.configure(bg=BG)
    root.option_add("*TCombobox*Listbox.background", INPUT_BG)
    root.option_add("*TCombobox*Listbox.foreground", INPUT_FG)
    root.option_add("*TCombobox*Listbox.selectBackground", PURPLE)
    return style


def apply_theme_embedded(widget: tk.Widget) -> ttk.Style:
    """Set up only the Spec.* styles for an embedded tab. No theme_use, no
    option_add -- the parent application owns those."""
    style = ttk.Style(widget)
    _configure_spec_styles(style)
    return style


def _configure_bare_styles(style: ttk.Style):
    """Legacy style names -- standalone host keeps these for any other
    consumers in the same process."""
    style.configure(".", background=BG, foreground=TEXT, font=F_BASE,
                    bordercolor=SEPARATOR, darkcolor=BG, lightcolor=BG,
                    troughcolor=BG, fieldbackground=INPUT_BG,
                    selectbackground=PURPLE, selectforeground=INPUT_FG)
    style.configure("TFrame", background=BG)
    style.configure("Panel.TFrame", background=PANEL)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Panel.TLabel", background=PANEL, foreground=TEXT)
    style.configure("Muted.TLabel", background=BG, foreground=MUTED)
    style.configure("PanelMuted.TLabel", background=PANEL, foreground=MUTED)
    style.configure("Header.TLabel", background=PANEL, foreground=PURPLE_LT,
                    font=F_H1)
    style.configure("Cyan.TLabel", background=PANEL, foreground=CYAN)
    style.configure("TLabelframe", background=PANEL, foreground=PURPLE_LT,
                    bordercolor=PURPLE_EDGE)
    style.configure("TLabelframe.Label", background=PANEL,
                    foreground=PURPLE_LT, font=F_BOLD)
    style.configure("TEntry", fieldbackground=INPUT_BG, foreground=INPUT_FG,
                    insertcolor=INPUT_FG, bordercolor=PURPLE_EDGE,
                    lightcolor=INPUT_BG, darkcolor=INPUT_BG, padding=3)
    style.configure("Placeholder.TEntry", fieldbackground=INPUT_BG,
                    foreground=MUTED, insertcolor=INPUT_FG,
                    bordercolor=PURPLE_EDGE, lightcolor=INPUT_BG,
                    darkcolor=INPUT_BG, padding=3)
    style.configure("TCombobox", fieldbackground=INPUT_BG, foreground=INPUT_FG,
                    background=INPUT_BG, arrowcolor=PURPLE_LT,
                    bordercolor=PURPLE_EDGE, padding=2)
    style.map("TCombobox",
              fieldbackground=[("readonly", INPUT_BG)],
              foreground=[("readonly", INPUT_FG)],
              selectbackground=[("readonly", INPUT_BG)],
              selectforeground=[("readonly", INPUT_FG)])
    style.configure("Horizontal.TScale", background=PURPLE_LT,
                    troughcolor=PURPLE_DEEP, bordercolor=PANEL,
                    lightcolor=PURPLE_LT, darkcolor=PURPLE)
    style.configure("Horizontal.TScrollbar", background="#26263e",
                    troughcolor=BG, arrowcolor=MUTED, bordercolor="#26263e",
                    lightcolor="#26263e", darkcolor="#26263e")
    style.map("Horizontal.TScrollbar",
              background=[("active", "#34345a")],
              bordercolor=[("active", "#34345a")],
              lightcolor=[("active", "#34345a")],
              darkcolor=[("active", "#34345a")])
    style.configure("Primary.TButton", background=PURPLE, foreground="#ffffff",
                    bordercolor=PURPLE_EDGE, focuscolor=PURPLE,
                    lightcolor=PURPLE, darkcolor=PURPLE, padding=(14, 5),
                    font=F_BOLD)
    style.map("Primary.TButton",
              background=[("active", "#8b5cf6"), ("disabled", "#241a38")],
              foreground=[("disabled", MUTED)])
    style.configure("TButton", background=ROW_ALT, foreground=TEXT,
                    bordercolor=SEPARATOR, lightcolor=ROW_ALT,
                    darkcolor=ROW_ALT, padding=(8, 3))
    style.map("TButton", background=[("active", "#232344")])


def _configure_spec_styles(style: ttk.Style):
    """Spec.* namespace -- the ONLY styles used by SpectralTab widgets. These
    are configured identically to the bare names so the tab looks the same
    whether the host used apply_theme_global or apply_theme_embedded."""
    style.configure("Spec.", background=BG, foreground=TEXT, font=F_BASE,
                    bordercolor=SEPARATOR, darkcolor=BG, lightcolor=BG,
                    troughcolor=BG, fieldbackground=INPUT_BG,
                    selectbackground=PURPLE, selectforeground=INPUT_FG)
    style.configure("Spec.TFrame", background=BG)
    style.configure("Spec.Panel.TFrame", background=PANEL)
    style.configure("Spec.TLabel", background=BG, foreground=TEXT)
    style.configure("Spec.Panel.TLabel", background=PANEL, foreground=TEXT)
    style.configure("Spec.Muted.TLabel", background=BG, foreground=MUTED)
    style.configure("Spec.PanelMuted.TLabel", background=PANEL, foreground=MUTED)
    style.configure("Spec.Header.TLabel", background=PANEL, foreground=PURPLE_LT,
                    font=F_H1)
    style.configure("Spec.Cyan.TLabel", background=PANEL, foreground=CYAN)
    style.configure("Spec.TLabelframe", background=PANEL, foreground=PURPLE_LT,
                    bordercolor=PURPLE_EDGE)
    style.configure("Spec.TLabelframe.Label", background=PANEL,
                    foreground=PURPLE_LT, font=F_BOLD)
    style.configure("Spec.TEntry", fieldbackground=INPUT_BG, foreground=INPUT_FG,
                    insertcolor=INPUT_FG, bordercolor=PURPLE_EDGE,
                    lightcolor=INPUT_BG, darkcolor=INPUT_BG, padding=3)
    style.configure("Spec.Placeholder.TEntry", fieldbackground=INPUT_BG,
                    foreground=MUTED, insertcolor=INPUT_FG,
                    bordercolor=PURPLE_EDGE, lightcolor=INPUT_BG,
                    darkcolor=INPUT_BG, padding=3)
    style.configure("Spec.TCombobox", fieldbackground=INPUT_BG,
                    foreground=INPUT_FG, background=INPUT_BG,
                    arrowcolor=PURPLE_LT, bordercolor=PURPLE_EDGE, padding=2)
    style.map("Spec.TCombobox",
              fieldbackground=[("readonly", INPUT_BG)],
              foreground=[("readonly", INPUT_FG)],
              selectbackground=[("readonly", INPUT_BG)],
              selectforeground=[("readonly", INPUT_FG)])
    style.configure("Spec.Horizontal.TScale", background=PURPLE_LT,
                    troughcolor=PURPLE_DEEP, bordercolor=PANEL,
                    lightcolor=PURPLE_LT, darkcolor=PURPLE)
    style.configure("Spec.Horizontal.TScrollbar", background="#26263e",
                    troughcolor=BG, arrowcolor=MUTED, bordercolor="#26263e",
                    lightcolor="#26263e", darkcolor="#26263e")
    style.map("Spec.Horizontal.TScrollbar",
              background=[("active", "#34345a")],
              bordercolor=[("active", "#34345a")],
              lightcolor=[("active", "#34345a")],
              darkcolor=[("active", "#34345a")])
    style.configure("Spec.Primary.TButton", background=PURPLE,
                    foreground="#ffffff", bordercolor=PURPLE_EDGE,
                    focuscolor=PURPLE, lightcolor=PURPLE, darkcolor=PURPLE,
                    padding=(14, 5), font=F_BOLD)
    style.map("Spec.Primary.TButton",
              background=[("active", "#8b5cf6"), ("disabled", "#241a38")],
              foreground=[("disabled", MUTED)])
    style.configure("Spec.TButton", background=ROW_ALT, foreground=TEXT,
                    bordercolor=SEPARATOR, lightcolor=ROW_ALT,
                    darkcolor=ROW_ALT, padding=(8, 3))
    style.map("Spec.TButton", background=[("active", "#232344")])

# ---------------------------------------------------------------------------
# Small widgets ------------------------------------------------------------
# ---------------------------------------------------------------------------
class Tooltip:
    """Minimal hover tooltip (tk has none built in). Delays 450 ms, follows
    the widget's bottom-left, kills itself on leave/click."""

    _DELAY_MS = 450

    def __init__(self, widget: tk.Widget, text: str):
        self._widget = widget
        self._text = text
        self._tip = None
        self._after_id = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")
        # Cancel the pending after-job on widget destroy (breaker fix,
        # 2026-07-20): a hover-armed _show timer firing post-destroy raises a
        # Tcl "invalid command name" bgerror. Kept in lock-step with Preview's.
        widget.bind("<Destroy>", self.hide, add="+")

    def _schedule(self, _event=None):
        self._cancel()
        self._after_id = self._widget.after(self._DELAY_MS, self._show)

    def _cancel(self):
        if self._after_id is not None:
            try:
                self._widget.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None

    def _show(self):
        self._after_id = None
        if self._tip is not None or not self._widget.winfo_viewable():
            return
        tip = tk.Toplevel(self._widget)
        tip.wm_overrideredirect(True)
        tip.wm_attributes("-topmost", True)
        x = self._widget.winfo_rootx() + 4
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 4
        # Pack the label BEFORE positioning so the border clamp can read the
        # popup's resolved size (winfo_reqwidth/reqheight).
        lbl = tk.Label(tip, text=self._text, justify=tk.LEFT, wraplength=280,
                       background=PANEL, foreground=TEXT, font=F_SMALL,
                       padx=7, pady=5, highlightthickness=1,
                       highlightbackground=PURPLE_EDGE)
        lbl.pack()
        # Border-aware: shift/flip so the tooltip never opens past the visible
        # edge of the monitor it lives on (mirrors the main app's
        # _position_popup_with_bounds; every tooltip must be edge-aware).
        self._position_with_bounds(tip, x, y, margin=8)
        self._tip = tip

    def _position_with_bounds(self, popup, x, y, margin=8):
        """Clamp a tooltip Toplevel to the visible monitor work-area, flipping
        above the host widget if it would overflow the bottom. Standalone-safe
        port of the main app's _position_popup_with_bounds; falls back to plain
        geometry() on any error."""
        try:
            popup.update_idletasks()
            pw = popup.winfo_reqwidth()
            ph = popup.winfo_reqheight()
            m_left, m_top, m_right, m_bottom = self._monitor_bounds(x, y)
            if x + pw > m_right - margin:                 # right-edge clip
                x = max(m_left + margin, m_right - pw - margin)
            if y + ph > m_bottom - margin:                # bottom-edge clip
                y_above = self._widget.winfo_rooty() - ph - 4
                if y_above >= m_top + margin:             # flip above host
                    y = y_above
                else:
                    y = max(m_top + margin, m_bottom - ph - margin)
            x = max(m_left + margin, x)                   # left / top guards
            y = max(m_top + margin, y)
            popup.geometry("+%d+%d" % (int(x), int(y)))
        except Exception:
            try:
                popup.geometry("+%d+%d" % (int(x), int(y)))
            except Exception:
                pass

    def _monitor_bounds(self, x, y):
        """(left, top, right, bottom) work-area of the monitor under screen
        point (x, y): Win32 MonitorFromPoint on Windows (taskbar excluded),
        falling back to the primary screen's winfo dims elsewhere. Uses the
        host widget -- the Tooltip has no self.root."""
        try:
            if os.name == "nt":
                import ctypes

                class POINT(ctypes.Structure):
                    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

                class RECT(ctypes.Structure):
                    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                                ("right", ctypes.c_long),
                                ("bottom", ctypes.c_long)]

                class MONITORINFO(ctypes.Structure):
                    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT),
                                ("rcWork", RECT), ("dwFlags", ctypes.c_ulong)]

                user32 = ctypes.windll.user32
                user32.MonitorFromPoint.argtypes = [POINT, ctypes.c_ulong]
                user32.MonitorFromPoint.restype = ctypes.c_void_p
                user32.GetMonitorInfoW.argtypes = [
                    ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
                user32.GetMonitorInfoW.restype = ctypes.c_bool

                pt = POINT(int(x), int(y))
                monitor = user32.MonitorFromPoint(pt, 2)  # DEFAULTTONEAREST
                info = MONITORINFO()
                info.cbSize = ctypes.sizeof(MONITORINFO)
                if user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                    w = info.rcWork
                    return (w.left, w.top, w.right, w.bottom)
        except Exception:
            pass
        try:
            return (0, 0, self._widget.winfo_screenwidth(),
                    self._widget.winfo_screenheight())
        except Exception:
            return (0, 0, 1920, 1080)

    def hide(self, _event=None):
        self._cancel()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None


class Chip(tk.Label):
    """A toggle 'chip' -- v4's small option buttons use coloured OUTLINES, so
    this is a Label (not a Checkbutton) with a 1 px outline that lights up in
    the accent colour when on, and dims to a neutral edge when off."""

    def __init__(self, parent, text, accent=CYAN, on=False, command=None,
                 tooltip=None, off_edge=None):
        super().__init__(parent, text=text, font=F_SMALL, padx=8, pady=2,
                         cursor="hand2", highlightthickness=1)
        self.accent = accent
        self.off_edge = off_edge
        self._on = bool(on)
        self._command = command
        self._hover = False
        self._enabled = True
        self.bind("<Button-1>", self._clicked)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        if tooltip:
            Tooltip(self, tooltip)
        self._restyle()

    def _clicked(self, _event=None):
        if self._enabled:
            self.set(not self._on)

    def _enter(self, _event=None):
        self._hover = True
        self._restyle()

    def _leave(self, _event=None):
        self._hover = False
        self._restyle()

    def _restyle(self):
        if not self._enabled:
            fg, edge, bg = "#4a4a60", "#2c2c42", PANEL
        elif self._on:
            fg, edge = self.accent, self.accent
            bg = blend(PANEL, self.accent, 0.16)
        else:
            off_edge = self.off_edge or CHIP_OFF_EDGE
            fg = TEXT if self._hover else (self.off_edge or MUTED)
            edge = "#55557a" if (self._hover and not self.off_edge) else off_edge
            bg = PANEL
        self.configure(foreground=fg, background=bg, highlightbackground=edge,
                       highlightcolor=edge)

    def set(self, value: bool, fire=True):
        value = bool(value)
        changed = value != self._on
        self._on = value
        self._restyle()
        if fire and changed and self._command:
            self._command(value)

    def get(self) -> bool:
        return self._on

    def set_enabled(self, enabled: bool):
        self._enabled = bool(enabled)
        self.configure(cursor="hand2" if enabled else "arrow")
        self._restyle()


class PrimaryButton(tk.Label):
    """Filled purple primary-action button. A tk.Label (not ttk.Button) because
    ttk on Windows/ttkbootstrap ignores `background` on buttons unless the whole
    theme is `clam` -- which the EMBEDDED tab must not force. So the Compare CTA
    rendered navy in v4 despite Spec.Primary.TButton=PURPLE. A Label gives exact
    fill control on any host theme (2026-07-20)."""

    def __init__(self, parent, text, command=None, tooltip=None):
        super().__init__(parent, text=text, font=F_BOLD, padx=16, pady=6,
                         cursor="hand2", background=PURPLE, foreground="#ffffff",
                         highlightthickness=1, highlightbackground=PURPLE_EDGE,
                         highlightcolor=PURPLE_EDGE)
        self._command = command
        self._enabled = True
        self.bind("<Button-1>", self._clicked)
        self.bind("<Enter>", lambda _e: self._restyle(hover=True))
        self.bind("<Leave>", lambda _e: self._restyle(hover=False))
        if tooltip:
            Tooltip(self, tooltip)

    def _clicked(self, _event=None):
        if self._enabled and self._command:
            self._command()

    def _restyle(self, hover=False):
        if not self._enabled:
            self.configure(background="#241a38", foreground=MUTED,
                           highlightbackground="#2c2c42")
        else:
            self.configure(background="#8b5cf6" if hover else PURPLE,
                           foreground="#ffffff", highlightbackground=PURPLE_EDGE)

    def set_text(self, text):
        self.configure(text=text)

    def set_enabled(self, enabled: bool):
        self._enabled = bool(enabled)
        self.configure(cursor="hand2" if enabled else "arrow")
        self._restyle()


class Segmented(tk.Frame):
    """Two-option segmented control (Per-Lane | Spectrogram)."""

    def __init__(self, parent, options, command=None):
        super().__init__(parent, background=DARKER, highlightthickness=1,
                         highlightbackground=SEPARATOR)
        self._command = command
        self._index = 0
        self._labels = []
        for i, text in enumerate(options):
            lbl = tk.Label(self, text=text, font=F_SMALL, padx=12, pady=3,
                           cursor="hand2")
            lbl.pack(side=tk.LEFT)
            lbl.bind("<Button-1>", functools.partial(self._clicked, i))
            self._labels.append(lbl)
        self._restyle()

    def _clicked(self, index, _event=None):
        self.set(index)

    def set(self, index: int, fire=True):
        index = int(index)
        changed = index != self._index
        self._index = index
        self._restyle()
        if fire and changed and self._command:
            self._command(index)

    def get(self) -> int:
        return self._index

    def _restyle(self):
        for i, lbl in enumerate(self._labels):
            if i == self._index:
                lbl.configure(background=PURPLE_DEEP, foreground=PURPLE_LT)
            else:
                lbl.configure(background=DARKER, foreground=MUTED)


class OutlineButton(tk.Label):
    """Momentary button in v4's coloured-outline option-button language."""

    def __init__(self, parent, text, accent=PURPLE_EDGE, command=None,
                 tooltip=None, enabled=True):
        super().__init__(parent, text=text, font=F_SMALL, padx=9, pady=2,
                         cursor="hand2", highlightthickness=1)
        self.accent = accent
        self._command = command
        self._enabled = bool(enabled)
        self._hover = False
        self.bind("<Button-1>", self._clicked)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        if tooltip:
            Tooltip(self, tooltip)
        self._restyle()

    def _clicked(self, _event=None):
        if self._enabled and self._command:
            self._command()

    def _enter(self, _event=None):
        self._hover = True
        self._restyle()

    def _leave(self, _event=None):
        self._hover = False
        self._restyle()

    def _restyle(self):
        if not self._enabled:
            fg, edge, bg = "#4a4a60", "#2c2c42", PANEL
        elif self._hover:
            fg, edge = "#ffffff", self.accent
            bg = blend(PANEL, self.accent, 0.30)
        else:
            fg, edge, bg = self.accent, self.accent, PANEL
        self.configure(foreground=fg, background=bg, highlightbackground=edge,
                       highlightcolor=edge)

    def set_enabled(self, enabled: bool):
        self._enabled = bool(enabled)
        self.configure(cursor="hand2" if enabled else "arrow")
        self._restyle()


class PlaceholderEntry(ttk.Frame):
    """ttk.Entry with placeholder text (ttk has none). Uses the Spec.* styles
    so it is safe inside an embedded tab."""

    def __init__(self, parent, placeholder="", tooltip=None, width=30):
        super().__init__(parent, style="Spec.TFrame")
        self._placeholder = placeholder
        self._has_text = False
        self.entry = ttk.Entry(self, style="Spec.Placeholder.TEntry",
                               width=width)
        self.entry.pack(fill=tk.X, expand=True)
        self.entry.insert(0, placeholder)
        self.entry.bind("<FocusIn>", self._focus_in)
        self.entry.bind("<FocusOut>", self._focus_out)
        if tooltip:
            Tooltip(self.entry, tooltip)

    def _focus_in(self, _event=None):
        if not self._has_text:
            self.entry.delete(0, tk.END)
            self.entry.configure(style="Spec.TEntry")
            self._has_text = True

    def _focus_out(self, _event=None):
        if not self.entry.get().strip():
            self._has_text = False
            self.entry.configure(style="Spec.Placeholder.TEntry")
            self.entry.delete(0, tk.END)
            self.entry.insert(0, self._placeholder)

    def get(self) -> str:
        return self.entry.get().strip() if self._has_text else ""

    def set(self, text: str):
        self.entry.configure(style="Spec.TEntry")
        self.entry.delete(0, tk.END)
        self.entry.insert(0, text)
        self._has_text = bool(text)
        if not self._has_text:
            self._focus_out()

    def clear(self):
        self.set("")

# ---------------------------------------------------------------------------
# Model objects --------------------------------------------------------------
# ---------------------------------------------------------------------------
class MockSpectralModel:
    """Synthetic stand-in for the dict SpectralWorker produces. It generates a
    plausible 120 BPM drum loop, a chart for it, and 5 planted disagreements
    (3 MISS + 2 PHANTOM) so the flags have something to show.

    The tab consumes these attributes:
      dur, bpm, notes, issues, lane_onsets, band_onsets, lane_ticks, band_ticks,
      lane_env, band_env, lane_max, band_max, env_fps,
      gram_rows (bytearray rows, top = high freq), gram_fmin, gram_fmax
    """

    def __init__(self, seed: int = 20260719):
        rng = random.Random(seed)
        self.bpm = 120.0
        beat = 60.0 / self.bpm
        self.dur = 36.0
        n_beats = int(self.dur / beat)

        notes = []
        for b in range(n_beats):
            t = b * beat
            notes.append((t, 7, 100))
            if b % 4 == 2:
                notes.append((t + 0.75 * beat, 7, 90))
            if b % 2 == 1:
                notes.append((t, 2, 105))
            ride_section = 32 <= b < 48
            cym_lane = 6 if ride_section else 0
            notes.append((t, cym_lane, 80))
            notes.append((t + 0.5 * beat, cym_lane, 65))
            if b in (0, 32, 64):
                notes.append((t, 1, 110))
        for k in range(6):
            notes.append((68 * beat + k * beat / 4.0, 3 + (k % 3), 95))
        notes.sort(key=lambda n: n[0])
        self.notes = notes

        lane_onsets = [[] for _ in range(8)]
        lane_ticks = [[] for _ in range(8)]
        planted_phantom = {(10.5, 2), (22.0, 7)}
        for t, lane, vel in notes:
            if (round(t, 2), lane) in planted_phantom:
                continue
            lane_onsets[lane].append(t)
            lane_ticks[lane].append(t)
        for t, lane in ((13.13, 7), (21.61, 0), (33.14, 2)):
            lane_onsets[lane].append(t)
            lane_ticks[lane].append(t)
        for b in range(8, n_beats - 8):
            if b % 2 == 0 and rng.random() < 0.4:
                lane_ticks[0].append(b * beat + 0.25 * beat)
        for lst in lane_onsets + lane_ticks:
            lst.sort()
        self.lane_onsets = lane_onsets
        self.lane_ticks = lane_ticks
        self.band_onsets = {}
        self.band_ticks = {}
        for band, _label, _lo, _hi in BANDS:
            members = [i for i in range(8) if LANE_TO_BAND[i] == band]
            self.band_onsets[band] = sorted(
                t for i in members for t in lane_onsets[i])
            self.band_ticks[band] = sorted(
                t for i in members for t in lane_ticks[i])

        n_frames = int(self.dur * ENV_FPS)
        tau = {"hat": 0.045, "snare": 0.07, "tom": 0.08, "kick": 0.09}
        lane_env = [[0.0] * n_frames for _ in range(8)]

        def add_burst(env, t0, amp, band):
            start = int(t0 * ENV_FPS)
            length = int(0.35 * ENV_FPS)
            for j in range(start, min(n_frames, start + length)):
                dt = (j - start) / ENV_FPS
                env[j] += amp * math.exp(-dt / tau[band])

        for lane in range(8):
            band = LANE_TO_BAND[lane]
            for t in lane_onsets[lane]:
                add_burst(lane_env[lane], t, rng.uniform(0.75, 1.0), band)
            for t in lane_ticks[lane]:
                add_burst(lane_env[lane], t, rng.uniform(0.15, 0.3), band)
            for other in range(8):
                if other != lane and LANE_TO_BAND[other] == band:
                    for t in lane_onsets[other]:
                        add_burst(lane_env[lane], t, 0.12, band)
            for j in range(n_frames):
                lane_env[lane][j] += 0.015 + 0.02 * rng.random()
        self.lane_env = lane_env
        self.lane_max = [max(e) or 1.0 for e in lane_env]
        self.env_fps = ENV_FPS

        self.band_env = {}
        self.band_max = {}
        for band, _label, _lo, _hi in BANDS:
            env = [0.0] * n_frames
            for t in self.band_onsets[band]:
                add_burst(env, t, rng.uniform(0.75, 1.0), band)
            for j in range(n_frames):
                env[j] += 0.015 + 0.02 * rng.random()
            self.band_env[band] = env
            self.band_max[band] = max(env) or 1.0

        self.issues = self.compute_issues(self.notes)

        self.gram_fmin = GRAM_FMIN
        self.gram_fmax = GRAM_FMAX
        self.gram_rows = self._build_gram(rng, tau)
        # The mock has no audio file to read an amplitude envelope from, so it
        # leaves this None ON PURPOSE: that is the state GramView's Waveform
        # style must survive, and the demo/self-test path is where it gets
        # exercised. Do not synthesize one here just to make the view prettier.
        self.wave_env = None

    def _build_gram(self, rng, tau):
        rows, cols = GRAM_ROWS, GRAM_COLS
        dt = self.dur / cols
        row_freq = [GRAM_FMIN * (GRAM_FMAX / GRAM_FMIN) **
                    ((rows - 1 - r) / (rows - 1)) for r in range(rows)]
        centres = {"kick": 62.0, "snare": 255.0, "tom": 150.0, "hat": 8500.0}
        sigmas = {"kick": 0.45, "snare": 0.55, "tom": 0.5, "hat": 0.75}
        profiles = {}
        for band, _label, _lo, _hi in BANDS:
            fc, sig = centres[band], sigmas[band]
            profiles[band] = [
                math.exp(-0.5 * (math.log(f / fc, 2) / sig) ** 2)
                for f in row_freq]
        band_env = {}
        for band, _label, _lo, _hi in BANDS:
            env = [0.0] * cols
            for t0 in self.band_onsets[band]:
                start = int(t0 / dt)
                length = int(0.4 / dt)
                for j in range(start, min(cols, start + length)):
                    env[j] += 0.9 * math.exp(-((j - start) * dt) / tau[band])
            for t0 in self.band_ticks[band]:
                start = int(t0 / dt)
                length = int(0.25 / dt)
                for j in range(start, min(cols, start + length)):
                    env[j] += 0.22 * math.exp(-((j - start) * dt) / tau[band])
            band_env[band] = env
        grid = [[0.02 + 0.035 * rng.random() for _ in range(cols)]
                for _ in range(rows)]
        for band, _label, _lo, _hi in BANDS:
            prof, env = profiles[band], band_env[band]
            for c in range(cols):
                e = env[c]
                if e > 0.004:
                    for r in range(rows):
                        grid[r][c] += e * prof[r]
        out = []
        for r in range(rows):
            row = grid[r]
            out.append(bytearray(min(255, int(v * 255)) if v < 1.0 else 255
                                 for v in row))
        return out

    def gram_row_of_freq(self, freq: float) -> float:
        freq = max(GRAM_FMIN, min(GRAM_FMAX, freq))
        frac = math.log(freq / GRAM_FMIN) / math.log(GRAM_FMAX / GRAM_FMIN)
        return (1.0 - frac) * (GRAM_ROWS - 1)

    def compute_issues(self, notes):
        """Mock version of spectral_engine.find_issues."""
        issues = []
        for t, lane, _vel in notes:
            if not any(abs(t - o) <= NEAR for o in self.lane_onsets[lane]):
                issues.append({"time": t, "lane": lane, "type": "phantom"})
        note_times = [t for t, _lane, _vel in notes]
        for lane in range(8):
            for o in self.lane_onsets[lane]:
                if not any(abs(o - t) <= NEAR for t in note_times):
                    issues.append({"time": o, "lane": lane, "type": "miss"})
        issues.sort(key=lambda i: i["time"])
        return issues


def _build_gram_rows(spec: dict):
    """Adapt the engine's ``db`` spectrogram into the mock's gram_rows format:
    a list of ``GRAM_ROWS`` bytearrays (length ``GRAM_COLS``), row 0 = highest
    frequency. Runs in the worker thread (numpy is available there)."""
    import numpy as np

    db = spec.get("db")
    if db is None or db.size == 0:
        return [bytearray(GRAM_COLS) for _ in range(GRAM_ROWS)]
    half, n_frames = db.shape
    if half < 2 or n_frames < 1:
        return [bytearray(GRAM_COLS) for _ in range(GRAM_ROWS)]

    max_db = float(spec.get("maxDb", -80.0))
    floor = max_db - 80.0
    if n_frames == 1:
        col_idx = [0] * GRAM_COLS
    else:
        col_idx = np.clip(
            np.round(np.linspace(0, n_frames - 1, GRAM_COLS)).astype(np.int32),
            0, n_frames - 1)

    bin_hz = float(spec.get("binHz", 1.0))
    src_freqs = np.arange(1, half) * bin_hz
    out_freqs = np.array([
        GRAM_FMIN * (GRAM_FMAX / GRAM_FMIN) **
        ((GRAM_ROWS - 1 - r) / max(1, GRAM_ROWS - 1))
        for r in range(GRAM_ROWS)])
    src_idx = np.searchsorted(src_freqs, out_freqs)
    src_idx = np.clip(src_idx, 0, len(src_freqs) - 1)

    rows = []
    for r in range(GRAM_ROWS):
        si = int(src_idx[r])
        vals = np.clip(((db[si, col_idx] - floor) / 80.0) * 255.0, 0, 255)
        rows.append(bytearray(vals.astype(np.uint8).tobytes()))
    return rows


# Envelope resolution, in samples per SECOND of audio -- deliberately NOT tied to
# GRAM_COLS. The spectrogram's 6000-column master grid is a whole-song grid: on a
# 581 s song that is 10.3 columns/second, so at 300 px/s a 4.4 s viewport spans ~46
# columns stretched across ~1300 pixels and the trace renders as ~29 px blocks. A
# heatmap degrades into a smear and still reads; a waveform degrades into visible
# stair-steps, which defeats the one thing this view is for. GramView's zoom ceiling
# is 1400 px/s, so ~800/s keeps roughly one envelope sample per pixel through the
# useful range and the cap bounds memory on very long files (float32: 1.5M samples
# per channel = 6 MB).
WAVE_ENV_RATE = 800.0
WAVE_ENV_MAX = 1_500_000


def _build_wave_env(path, mono_samples, sr):
    """Max-pooled amplitude envelope for the Waveform view: ``(top, bot)`` as
    float32 arrays in 0..1, or ``None`` if nothing usable could be read.

    WHY AMPLITUDE AND NOT THE SPECTROGRAM GRID. This view exists because the
    heatmap gets hard to read on rough audio, so the whole point is transient
    legibility. Measured on a real drum stem (peak-to-median of the drawn
    extent, higher = transients stand out): amplitude max-pool **2.75** vs
    **1.09** for a spectral column collapsed with mean() and **1.18** with
    max(). Collapsing the grid across frequency destroys the very thing that
    made it informative, by any reducer -- so this deliberately does NOT reuse
    ``gram_rows``, cheap though that would have been.

    STEREO, WITH A MONO FALLBACK. Left drives the upper half and right the
    lower, which is what makes the two sides differ and carry information; 12
    real drum stems measured a median L/R difference of 0.42 with **none**
    effectively mono. A mono source (or any decode failure) sets both halves to
    the same envelope, so the view degrades to an ordinary symmetric waveform
    instead of breaking -- the same fallback the MIDI Editor's two-tone
    waveform already uses.

    ⚠ BOTH CHANNELS SHARE ONE DIVISOR. Normalising each side by its own peak
    would rescale a quiet channel to full height and manufacture an asymmetry
    that is not in the audio -- the asymmetry is the signal here, so faking it
    would be worse than showing none.

    Never raises: a failure here must not take down Compare, since the other
    two views do not need this data at all.
    """
    import numpy as np

    # One column count for both channels, from the audio's own length, so the two
    # halves stay index-aligned and the caller can map time -> index without
    # knowing which channel it is looking at.
    try:
        _n = int(np.asarray(mono_samples).size)
    except Exception:
        _n = 0
    _dur = (_n / float(sr)) if (sr and _n) else 0.0
    cols = int(min(WAVE_ENV_MAX, max(GRAM_COLS, round(_dur * WAVE_ENV_RATE))))

    def pool(a):
        a = np.abs(np.asarray(a, dtype=np.float32))
        if a.size < cols:
            a = np.pad(a, (0, cols - a.size))
        chunk = a.size // cols
        # MAX, not mean: a drum hit is one or two samples of peak inside a window
        # of near-silence, and averaging it away is exactly how the spectral-ridge
        # variants of this view ended up unreadable.
        return a[:chunk * cols].reshape(cols, chunk).max(axis=1)

    left = right = None
    try:
        import librosa
        y = librosa.load(path, sr=None, mono=False)[0]
        y = np.asarray(y)
        if y.ndim == 2 and min(y.shape) >= 2:
            # channels is always the smaller axis, whichever layout the decoder
            # used -- the same channels-first/channels-last ambiguity the engine
            # guards against (see compute_spectral's mean(axis=...) note).
            if y.shape[0] > y.shape[1]:
                y = y.T
            left, right = pool(y[0]), pool(y[1])
        elif y.size:
            left = right = pool(y.reshape(-1))
    except Exception:
        left = right = None

    if left is None:                       # stereo read failed -> reuse the mono
        try:                               # samples the caller already decoded
            a = np.asarray(mono_samples)
            if a.ndim > 1:
                a = a.mean(axis=(1 if a.shape[0] > a.shape[1] else 0))
            if not a.size:
                return None
            left = right = pool(a)
        except Exception:
            return None

    peak = float(max(left.max(), right.max()))
    if not (peak > 0.0) or not math.isfinite(peak):
        return None                        # silent or non-finite -> no view
    # float32 arrays, not lists of Python floats: at 800 samples/s a 10-minute song
    # is ~480k values per channel, which as boxed floats costs ~12 MB per channel
    # against ~2 MB here.
    return ((left / peak).astype(np.float32), (right / peak).astype(np.float32))


class _SpectralModel:
    """Adapter wrapping the real ``parakit_spectral_engine`` output into the
    same shape MockSpectralModel provides, so the views need no changes."""

    def __init__(self, spec: dict, notes, issues, bpm, wave_env=None):
        self._spec = spec
        # Optional: (top, bot) amplitude envelopes for the Waveform render
        # style. None is a supported state -- GramView falls back to deriving a
        # shape from gram_rows so the view is never blank.
        self.wave_env = wave_env
        self.dur = float(spec.get("dur", 0.0))
        self.bpm = _safe_bpm(bpm)
        self.notes = list(notes)
        self.issues = list(issues)
        # onsets_from_env expects nFrames; patch it if the caller's spec is
        # missing it (the real engine always provides it).
        if "nFrames" not in spec:
            spec = dict(spec)
            spec["nFrames"] = len(spec["laneEnv"][0])
            self._spec = spec
        import parakit_spectral_engine as eng

        self.lane_env = [list(map(float, spec["laneEnv"][i])) for i in range(8)]
        self.band_env = {k: list(map(float, v))
                         for k, v in spec["bandEnv"].items()}
        self.lane_max = [max(self.lane_env[i]) or 1.0 for i in range(8)]
        self.band_max = {k: max(self.band_env[k]) or 1.0
                         for k in self.band_env}
        self.env_fps = float(spec.get("sr", 44100.0)) / float(
            spec.get("hop", 512.0))

        self.lane_onsets = [eng.lane_onsets(spec, i, eng.ONSET)
                            for i in range(8)]
        self.lane_ticks = [eng.lane_onsets(spec, i, eng.TICK_THRESH)
                           for i in range(8)]
        self.band_onsets = {k: eng.band_onsets(spec, k, eng.ONSET)
                            for k in self.band_env}
        self.band_ticks = {k: eng.band_onsets(spec, k, eng.TICK_THRESH)
                           for k in self.band_env}

        self.gram_fmin = GRAM_FMIN
        self.gram_fmax = GRAM_FMAX
        self.gram_rows = _build_gram_rows(spec)

    def gram_row_of_freq(self, freq: float) -> float:
        freq = max(GRAM_FMIN, min(GRAM_FMAX, freq))
        frac = math.log(freq / GRAM_FMIN) / math.log(GRAM_FMAX / GRAM_FMIN)
        return (1.0 - frac) * (GRAM_ROWS - 1)

    def compute_issues(self, notes):
        import parakit_spectral_engine as eng
        return eng.find_issues(self._spec, notes)

# ---------------------------------------------------------------------------
# Per-Lane view --------------------------------------------------------------
# ---------------------------------------------------------------------------
class LaneViewCanvas(tk.Frame):
    """Per-Lane view: one row per drum lane (editor order) with the energy
    ribbon, onset ticks, chart-note diamonds and MISS/PHANTOM flags, plus a
    time ruler on top. Pure tk.Canvas; redraws only the visible time window.

    Row height is DERIVED from the live canvas height: on every <Configure>
    height change (debounced 120 ms) row_h becomes
    (canvas_h - RULER_H) / len(LANES), clamped to [LANE_ROW_H_MIN,
    LANE_ROW_H_MAX], so the 8 rows fill the canvas vertically at ANY window
    size.
    """

    def __init__(self, parent, on_seek=None, on_edit_click=None, on_zoom=None,
                 on_row_h=None):
        super().__init__(parent, background=BG)
        self._on_seek = on_seek
        self._on_edit_click = on_edit_click
        self._on_zoom = on_zoom
        self._on_row_h = on_row_h
        self._model = None
        self._snotes, self._sntimes = [], []     # v4.9.0 windowed-overlay cache
        self._sissues, self._sitimes = [], []
        self._pps = ZOOM_DEFAULT
        self._playhead = None
        self._react_last = None
        self._hover = None
        self._redraw_pending = False
        self.row_h = float(LANE_ROW_H)
        self._resize_job = None
        self._scroll_job = None
        self._last_h = 0
        self.opt = dict(energy=True, ticks=True, notes=True, flags=True,
                        per_lane_raw=True, note_size=1.0, ghost=False,
                        ghost_opacity=0.30, flash=True, grid=False,
                        grid_div=4, note_shape="diamond",
                        edit=False, snap=True, lane_visible=None)
        self.canvas = tk.Canvas(self, background=CANVAS_BG, height=200,
                                highlightthickness=0, cursor="arrow")
        self.hbar = ttk.Scrollbar(self, orient=tk.HORIZONTAL,
                                  style="Spec.Horizontal.TScrollbar",
                                  command=self._on_scrollbar)
        self.canvas.configure(xscrollcommand=self.hbar.set)
        self.canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.hbar.pack(side=tk.TOP, fill=tk.X)
        self.canvas.bind("<Configure>", self._on_configure)
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<Motion>", self._motion)
        self.canvas.bind("<Leave>", self._leave)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self._refresh_scrollregion()

    # ----- wiring -----------------------------------------------------------
    def _on_configure(self, event):
        self._redraw_now()
        h = int(event.height)
        if h > 1 and h != self._last_h:
            self._last_h = h
            if self._resize_job is not None:
                self.after_cancel(self._resize_job)
            self._resize_job = self.after(120, self._relayout)

    def _relayout(self):
        self._resize_job = None
        avail = self.canvas.winfo_height()
        new_h = max(float(LANE_ROW_H_MIN),
                    min(float(LANE_ROW_H_MAX),
                        (avail - RULER_H) / float(len(LANES))))
        if abs(new_h - self.row_h) < 0.5:
            return
        self.row_h = new_h
        self._refresh_scrollregion()
        if self._on_row_h is not None:
            self._on_row_h(new_h)
        self._redraw_now()

    def _clamp_xview(self):
        # Never scroll left of the chart start (t=0). When the content is
        # shorter than the viewport (short chart, or zoomed out so
        # scrollregion < canvas width), Tk's confine lets xview_scroll push the
        # origin to the right, detaching the lanes from the beginning of the
        # chart with empty space on the left (owner-reported). Snap back.
        try:
            if self.canvas.canvasx(0) < 0:
                self.canvas.xview_moveto(0.0)
        except Exception:
            pass

    def _on_scrollbar(self, *args):
        self.canvas.xview(*args)
        self._clamp_xview()
        self._scroll_render_soon()

    def _scroll_render_soon(self):
        # Throttle the redraw on scroll (v4.9.2 lag fix): the per-lane view
        # recomputes every energy-ribbon polygon on each scroll event; the
        # canvas xview already moved, so coalesce the redraw to ~36 fps.
        # Leading-throttle (see GramView._scroll_render_soon). Cancelled in
        # destroy().
        if self._scroll_job is not None:
            return
        self._scroll_job = self.after(_SCROLL_RENDER_MS, self._scroll_render_now)

    def _scroll_render_now(self):
        self._scroll_job = None
        self._redraw_now()

    def _wheel(self, event):
        if event.state & 0x0004:
            if self._on_zoom:
                self._on_zoom(1 if event.delta > 0 else -1)
        else:
            self.canvas.xview_scroll(-1 * (event.delta // 120) * 3, "units")
            self._clamp_xview()
            self._scroll_render_soon()

    def _click(self, event):
        if self._model is None:
            return
        t = self._t_of(self.canvas.canvasx(event.x))
        y = self.canvas.canvasy(event.y)
        if self.opt["edit"] and y >= RULER_H:
            row = int((y - RULER_H) // self.row_h)
            row = max(0, min(len(LANES) - 1, row))
            if self._on_edit_click:
                self._on_edit_click(t, row)
        elif self._on_seek:
            self._on_seek(t)

    def _motion(self, event):
        self._hover = (self.canvas.canvasx(event.x),
                       self.canvas.canvasy(event.y))
        if self.opt["edit"] and self._model is not None:
            self.redraw_overlay()

    def _leave(self, _event):
        self._hover = None
        if self.opt["edit"]:
            self.redraw_overlay()

    # ----- geometry ----------------------------------------------------------
    def _refresh_scrollregion(self):
        total = 800 if self._model is None else max(
            800, int(px_span(self._model.dur, self._pps)))
        h = RULER_H + self.row_h * len(LANES)
        self.canvas.configure(scrollregion=(0, 0, total, h))

    def _x_of(self, t):
        return t * self._pps

    def _t_of(self, x):
        return x / self._pps if self._pps else 0.0

    def snap_time(self, t):
        # _safe_bpm at the model boundary keeps this finite/positive; the
        # re-guard is belt-and-suspenders for a hand-built model (R8B2-1).
        bpm = _safe_bpm(self._model.bpm if self._model else 120.0)
        step = (60.0 / bpm) / 4.0
        return max(0.0, round(t / step) * step)

    def playhead_x(self):
        return None if self._playhead is None else self._x_of(self._playhead)

    # ----- public state -------------------------------------------------------
    def set_model(self, model):
        self._model = model
        self._playhead = None
        # v4.9.0 — cache time-sorted notes/issues so redraw_overlay windows to
        # the on-screen slice (see _sort_cache): kills the per-frame O(N) scan.
        self._snotes, self._sntimes = _sort_cache(
            getattr(model, "notes", None), lambda n: n[0]) if model else ([], [])
        self._sissues, self._sitimes = _sort_cache(
            getattr(model, "issues", None), lambda x: x["time"]) if model else ([], [])
        self._active_lanes = _active_lane_set(model) if model else set()
        self._refresh_scrollregion()
        self.canvas.xview_moveto(0)
        self._redraw_now()

    def refresh_notes_cache(self):
        # Rebuild the time-sorted overlay caches after an IN-PLACE edit, WITHOUT
        # resetting scroll or rebuilding the heatmap (review #1, 2026-07-21):
        # _apply_notes mutates model.notes/issues but redraw_overlay reads these
        # sorted caches, so an edit was invisible/stale (added notes missing,
        # deleted still drawn + flashed) until the next full re-compare.
        m = self._model
        self._snotes, self._sntimes = _sort_cache(
            getattr(m, "notes", None), lambda n: n[0]) if m else ([], [])
        self._sissues, self._sitimes = _sort_cache(
            getattr(m, "issues", None), lambda x: x["time"]) if m else ([], [])
        self._active_lanes = _active_lane_set(m) if m else set()
        self.request_redraw()

    def set_zoom(self, pps):
        # Floor matches ZOOM_FIT_MIN, not 20.0 (breaker R4B3-1b, 2026-07-20):
        # the view-level 20 px/s clamp silently overrode a below-floor Fit —
        # the tab said 6 px/s while the view rendered at 20.
        self._pps = max(ZOOM_FIT_MIN, min(1400.0, float(pps)))
        self._refresh_scrollregion()
        self._redraw_now()

    def set_options(self, **kw):
        self.opt.update(kw)
        self.canvas.configure(cursor="tcross" if self.opt["edit"] else "arrow")
        self.request_redraw()

    def set_playhead(self, t):
        self._react_last = self._playhead
        self._playhead = None if t is None else float(t)
        # Decoupled sweep (v4.9.2 smoothness fix): a playhead tick redraws ONLY
        # the thin 'playhead' layer (the line + the handful of notes it is
        # flashing over), never the full note/flag overlay. Re-rendering every
        # visible note per tick cost ~34 ms/tick (~29 fps) PER view zoomed out on
        # a big/maximized window; both views did it every tick -> the 10-15 fps
        # choppiness. The overlay itself only rebuilds on scroll/zoom/model change.
        self._draw_playhead()

    # ----- drawing --------------------------------------------------------------
    def request_redraw(self):
        if not self._redraw_pending:
            self._redraw_pending = True
            # tracked so destroy() can cancel it (breaker R2E-2, 2026-07-20)
            self._redraw_job = self.after_idle(self._redraw_now)

    def _redraw_now(self):
        self._redraw_pending = False
        # A DIRECT _redraw_now() call (set_zoom/_relayout do this) used to
        # orphan the still-queued idle job from an earlier request_redraw —
        # harmless double-draw in life, but a bgerror after destroy (breaker
        # R2E-2). Cancel it; cancelling the id we are currently running FROM
        # is a safe no-op.
        job = getattr(self, "_redraw_job", None)
        self._redraw_job = None
        if job is not None:
            try:
                self.after_cancel(job)
            except Exception:
                pass
        self._draw_static()
        self.redraw_overlay()

    def destroy(self):
        # Cancel pending relayout/redraw after-callbacks BEFORE teardown
        # (breaker R2E-2, 2026-07-20): they fired on the destroyed view as
        # "invalid command name" Tcl bgerrors.
        for _attr in ("_resize_job", "_redraw_job", "_scroll_job"):
            _job = getattr(self, _attr, None)
            if _job is not None:
                try:
                    self.after_cancel(_job)
                except Exception:
                    pass
                setattr(self, _attr, None)
        # Drop the PhotoImage ref ON THE MAIN THREAD (breaker R3B1-1,
        # 2026-07-20): left in the widget's reference cycles it dies via the
        # cyclic GC — possibly on a worker thread, where Image.__del__ raises
        # an ignored RuntimeError and leaks the Tcl-side image.
        if getattr(self, "_photo", None) is not None:
            self._photo = None
        super().destroy()

    def _visible_x(self):
        x0 = self.canvas.canvasx(0)
        x1 = self.canvas.canvasx(max(1, self.canvas.winfo_width()))
        return max(0.0, x0 - 2), x1 + 2

    def _draw_static(self):
        c = self.canvas
        c.delete("static")
        if self._model is None:
            self._draw_empty_skeleton(c)
            return
        x0, x1 = self._visible_x()
        h = RULER_H + self.row_h * len(LANES)
        # On-screen time window for tick slicing (F3, 2026-07-22): ticks lists
        # are time-sorted, so bisect to [t_of(x0-2), t_of(x1+2)] and iterate ONLY
        # the visible ticks instead of every tick in the song (O(all) -> O(vis +
        # log n)). _x_of is monotonic (t*pps, pps>0) so the sliced set is exactly
        # what the per-tick x-range guard below would keep. Skip windowing only
        # at a non-finite bound (denormal pps overflow); the guard still holds.
        _tw_ok = self._pps > 0
        _tw0 = _tw1 = 0.0
        if _tw_ok:
            _tw0, _tw1 = (x0 - 2) / self._pps, (x1 + 2) / self._pps
            if not (math.isfinite(_tw0) and math.isfinite(_tw1)):
                _tw_ok = False
        ghost = self.opt["ghost"]
        fade = (1.0 - self.opt["ghost_opacity"]) if ghost else 0.0

        def strip_color(color):
            return blend(color, CANVAS_BG, fade) if ghost else color

        env_fps = getattr(self._model, "env_fps", ENV_FPS)
        _vis = self.opt.get("lane_visible")   # None -> all lanes shown
        for i, (idx, name, color, band, _slice, _blabel) in enumerate(LANES):
            y0 = RULER_H + i * self.row_h
            yc = y0 + self.row_h / 2.0
            _hidden = _vis is not None and idx not in _vis
            active = self._lane_active(idx) and not _hidden
            tint = blend(CANVAS_BG, strip_color(color),
                         0.02 if _hidden else (0.10 if active else 0.05))
            c.create_rectangle(x0, y0, x1, y0 + self.row_h, fill=tint,
                               outline="", tags="static")
            if self.opt["energy"] and not _hidden:
                env = (self._model.lane_env[idx] if self.opt["per_lane_raw"]
                       else self._model.band_env[band])
                emax = ((self._model.lane_max[idx] if self.opt["per_lane_raw"]
                         else self._model.band_max[band]) or 1.0)
                hh = self.row_h * 0.42
                step = 1 if self._pps < 300 else 2
                top_pts, bot_pts = [], []
                x = int(x0)
                while x <= x1:
                    t = self._t_of(x)
                    e = 0.0
                    if 0.0 <= t <= self._model.dur:
                        frame = min(int(t * env_fps), len(env) - 1)
                        e = max(0.0, min(1.0, env[frame] / emax))
                        if not active:
                            e *= 0.5
                    top_pts.extend((x, yc - e * hh))
                    bot_pts.extend((x, yc + e * hh))
                    x += step
                if len(top_pts) >= 4:
                    alpha = 1.0 if active else 0.45
                    fill = blend(CANVAS_BG, strip_color(color),
                                 0.55 * alpha)
                    bot_rev = []
                    for j in range(len(bot_pts) - 2, -1, -2):
                        bot_rev.extend((bot_pts[j], bot_pts[j + 1]))
                    c.create_polygon(top_pts + bot_rev, fill=fill,
                                     outline="", tags="static")
            if self.opt["ticks"] and active:
                ticks = (self._model.lane_ticks[idx]
                         if self.opt["per_lane_raw"]
                         else self._model.band_ticks[band])
                ty0, ty1 = y0 + self.row_h * 0.12, y0 + self.row_h * 0.44
                tcol = strip_color(color)
                _tick_iter = (_iter_window(ticks, ticks, _tw0, _tw1)
                              if _tw_ok else ticks)
                for t in _tick_iter:
                    x = self._x_of(t)
                    if x0 - 2 <= x <= x1 + 2:
                        c.create_line(x, ty0, x, ty1, fill="#000000",
                                      width=3, tags="static")
                        c.create_line(x, ty0, x, ty1, fill=tcol, width=1,
                                      tags="static")
            c.create_line(x0, y0 + 0.5, x1, y0 + 0.5, fill=SEPARATOR,
                          tags="static")
        if self.opt["grid"]:
            self._draw_grid(x0, x1, h)
        self._draw_ruler(x0, x1)

    def _draw_empty_skeleton(self, c):
        """Nothing loaded yet: draw the empty lane skeleton -- one tinted band
        per drum lane, matching the gutter exactly (blend(PANEL, color, 0.20)
        fill + blend(SEPARATOR, color, 0.45) bottom divider), so the tab reads
        as a populated chart instead of a blank slab. row_h + RULER_H are the
        SAME values _fill_gutter draws with (kept in sync via the on_row_h
        callback), so the bands stay row-aligned with the gutter at every
        window size. The hint text is drawn last, centred over the skeleton on
        a small dark card so it stays legible over the tints."""
        x0, x1 = self._visible_x()
        # Top ruler strip, matching the gutter's RULER_H DARKER spacer so the
        # first band starts at the same y as the gutter's first row.
        c.create_rectangle(x0, 0, x1, RULER_H, fill=DARKER, outline="",
                           tags="static")
        c.create_line(x0, RULER_H - 0.5, x1, RULER_H - 0.5, fill=SEPARATOR,
                      tags="static")
        for i, (idx, name, color, band, _slice, _blabel) in enumerate(LANES):
            y0 = RULER_H + i * self.row_h
            y1 = y0 + self.row_h
            c.create_rectangle(x0, y0, x1, y1,
                               fill=blend(PANEL, color, 0.20),
                               outline="", tags="static")
            c.create_line(x0, y1 - 0.5, x1, y1 - 0.5,
                          fill=blend(SEPARATOR, color, 0.45), tags="static")
        # Hint text last, on a dark card so MUTED stays readable over any tint.
        cx = max(x0 + 8, min(x1 - 8, (x0 + x1) / 2.0))
        cy = RULER_H + self.row_h * len(LANES) / 2.0
        hint = c.create_text(
            cx, cy,
            text=("Load a drums stem + a candidate chart, then Compare --\n"
                  "each drum lane's energy, onsets, notes and MISS/PHANTOM "
                  "flags appear here.\n(standalone demo: Compare generates "
                  "synthetic data)"),
            fill=MUTED, font=F_BASE, justify=tk.CENTER, tags="static")
        bb = c.bbox(hint)
        if bb:
            pad = 10
            card = c.create_rectangle(bb[0] - pad, bb[1] - pad, bb[2] + pad,
                                      bb[3] + pad, fill=CANVAS_BG,
                                      outline=SEPARATOR, tags="static")
            c.tag_lower(card, hint)

    def _lane_active(self, idx):
        # O(1) membership on the precomputed active-lane set (F3, 2026-07-22)
        # instead of re-scanning every note + issue on each redraw. Falls back
        # to the linear scan if the cache is somehow absent (defensive).
        la = getattr(self, "_active_lanes", None)
        if la is not None:
            return idx in la
        m = self._model
        noted = any(int(n[1]) == idx for n in m.notes)
        flagged = any(int(i["lane"]) == idx for i in m.issues)
        return noted or flagged

    def _draw_grid(self, x0, x1, h):
        # v4.9.0 — bar/beat/subdivision timing lines (1/4..1/32) like the MIDI
        # editor; the subdivision is opt['grid_div']. Shared painter, so the
        # spectrogram view draws an identical grid.
        _paint_time_grid(self, x0, x1, RULER_H, h)

    def _draw_ruler(self, x0, x1):
        c = self.canvas
        c.create_rectangle(x0, 0, x1, RULER_H, fill=DARKER, outline="",
                           tags="static")
        step = next((s for s in (1, 2, 5, 10, 15, 30, 60, 120)
                     if s * self._pps >= 55), 120)
        draw_ticks = True
        if step * self._pps < 55:
            # Table caps at 120 s — extend to whole minutes so labels keep
            # >=55 px spacing at fit-level zooms (breaker R4B3-2). Use the
            # TRUE pps, never a floored copy (breaker R5B2-3: the old 1e-9
            # floor broke the spacing guarantee below it — dur=1e100 hung the
            # redraw); past ~1e12 s between labels there is nothing
            # meaningful to draw, so skip the tick loop entirely.
            needed = 55.0 / self._pps if self._pps > 0 else float("inf")
            if needed > 1e12:
                draw_ticks = False
            else:
                step = max(120, int(math.ceil(needed / 60.0)) * 60)
        stride = 1 if self._pps >= 2.0 else step
        # Seed the loop only if there is anything to draw (breaker R6B2-4,
        # 2026-07-20): _t_of(x0) = x0/pps overflows to inf at denormal pps
        # with a SCROLLED window (x0>0), and int(inf) raised OverflowError
        # BEFORE the skip below could take effect (x0==0 was safe, which is
        # why the round-5 guard missed it).
        t_seed = self._t_of(x0)
        if not (abs(t_seed) < 1e15):
            draw_ticks = False
        t = int(t_seed) if draw_ticks else 0
        if stride > 1:
            t -= t % stride
        while draw_ticks and self._x_of(t) <= x1:
            x = self._x_of(t)
            if t % step == 0:
                c.create_line(x, RULER_H - 6, x, RULER_H, fill=MUTED,
                              tags="static")
                c.create_text(x + 3, RULER_H - 8, text=fmt_time(t),
                              anchor=tk.W, fill=MUTED, font=F_MONO,
                              tags="static")
            else:
                c.create_line(x, RULER_H - 3, x, RULER_H, fill="#3a3a55",
                              tags="static")
            t += stride
        c.create_line(x0, RULER_H - 0.5, x1, RULER_H - 0.5, fill=SEPARATOR,
                      tags="static")

    def redraw_overlay(self):
        c = self.canvas
        c.delete("overlay")
        if self._model is None:
            return
        x0, x1 = self._visible_x()
        t0, t1 = self._t_of(x0) - 0.1, self._t_of(x1) + 0.1
        r = max(3.0, min(self.row_h * 0.42,
                         min(6.0, self.row_h * 0.28) * self.opt["note_size"]))

        def yc_of(lane):
            return RULER_H + lane * self.row_h + self.row_h / 2.0

        _vis = self.opt.get("lane_visible")   # None -> all lanes shown

        if self.opt["notes"]:
            # Base notes only (no flash here). Flashing moved to the cheap
            # per-tick 'playhead' layer (see _draw_playhead) so the overlay no
            # longer has to be re-rendered on every playhead tick.
            for t, lane, _vel in _iter_window(self._snotes, self._sntimes, t0, t1):
                if _vis is not None and lane not in _vis:
                    continue
                x, yc = self._x_of(t), yc_of(lane)
                _paint_note(c, x, yc, r, LANES[lane][2],
                            self.opt.get("note_shape", "diamond"))
        if self.opt["flags"]:
            for issue in _iter_window(self._sissues, self._sitimes, t0, t1):
                t = issue["time"]
                lane = int(issue["lane"])
                if _vis is not None and lane not in _vis:
                    continue
                x, yc = self._x_of(t), yc_of(lane)
                is_miss = issue["type"] == "miss"
                col = MISS_GREEN if is_miss else PHANTOM_ORANGE
                c.create_oval(x - 9, yc - 9, x + 9, yc + 9, outline="#000000",
                              width=3, tags="overlay")
                c.create_oval(x - 9, yc - 9, x + 9, yc + 9, outline=col,
                              width=2, tags="overlay")
                c.create_text(x, yc, text="+" if is_miss else "\u00d7",
                              fill=col, font=F_MONO_B, tags="overlay")
        if self.opt["edit"] and self._hover is not None:
            hx, hy = self._hover
            if hy >= RULER_H:
                row = int((hy - RULER_H) // self.row_h)
                row = max(0, min(len(LANES) - 1, row))
                t = self._t_of(hx)
                if self.opt["snap"]:
                    t = self.snap_time(t)
                x, yc = self._x_of(t), yc_of(row)
                pts = (x, yc - r, x + r, yc, x, yc + r, x - r, yc)
                c.create_polygon(pts, fill="", outline=LANES[row][2],
                                 width=1, dash=(3, 2), tags="overlay")
        # Playhead + flash live on their own thin layer now.
        self._draw_playhead()

    def _draw_playhead(self):
        """Thin per-tick layer (v4.9.2): the transport playhead line + the white
        flash markers for the FEW notes the playhead is currently crossing.
        Tagged 'playhead' (separate from 'overlay') so set_playhead repaints only
        this — a line plus ~0-5 flash dots — instead of every visible note/flag.
        The r / yc math mirrors redraw_overlay so the flash sits on its note."""
        c = self.canvas
        c.delete("playhead")
        if self._playhead is None or self._model is None:
            return
        pos = self._playhead
        if self.opt.get("notes") and self.opt.get("flash"):
            r = max(3.0, min(self.row_h * 0.42,
                             min(6.0, self.row_h * 0.28) * self.opt["note_size"]))
            _vis = self.opt.get("lane_visible")
            last = self._react_last if self._react_last is not None else pos
            advanced = last < pos <= last + 0.12
            # Only notes inside the flash window can light up -> a bisect slice
            # of a handful, not the whole visible set.
            f_lo = min(pos - 0.05, last) - 0.001
            for t, lane, _vel in _iter_window(self._snotes, self._sntimes,
                                              f_lo, pos + 0.001):
                if _vis is not None and lane not in _vis:
                    continue
                dt = pos - t
                if (0.0 <= dt <= 0.05) or (advanced and last < t <= pos):
                    x = self._x_of(t)
                    yc = RULER_H + lane * self.row_h + self.row_h / 2.0
                    _paint_note(c, x, yc, r, NOTE_OUTLINE,
                                self.opt.get("note_shape", "diamond"),
                                tag="playhead")
        px = self._x_of(pos)
        h = RULER_H + self.row_h * len(LANES)
        c.create_line(px, 0, px, h, fill=PLAYHEAD_COL, width=1, tags="playhead")

# ---------------------------------------------------------------------------
# Spectrogram view -------------------------------------------------------------
# ---------------------------------------------------------------------------
_FREQ_TICKS = (60, 120, 250, 500, 1000, 2000, 4000, 8000, 16000)


class GramView(tk.Frame):
    """Spectrogram (Comparison) view: a log-freq heatmap + drum-band guides +
    a per-lane note-row strip, with a fixed left freq-axis gutter.

    Tk has no QImage, so the heatmap is assembled as an in-memory binary PPM
    and handed to tk.PhotoImage in one call. The master grid stays at
    GRAM_COLS x GRAM_ROWS; the display image resamples it to the current
    zoom/brightness/scale/colormap.

    The heatmap height is DERIVED from the live canvas height: gram_h =
    canvas_h - RULER_H - note-strip, clamped to [GRAM_H_MIN, GRAM_H_MAX].
    """

    def _total_h(self):
        return RULER_H + self.gram_h + STRIP_ROW_H * len(ROLL_ROWS)

    def __init__(self, parent, on_seek=None, on_zoom=None):
        super().__init__(parent, background=BG)
        self._on_seek = on_seek
        self._on_zoom = on_zoom
        self._model = None
        self._snotes, self._sntimes = [], []     # v4.9.0 windowed-overlay cache
        self._sissues, self._sitimes = [], []
        self._pps = ZOOM_DEFAULT
        self._eff_pps = ZOOM_DEFAULT
        self._playhead = None
        self._react_last = None   # prev playhead, for the note-strip flash window
        self._cursor = None
        self._photo = None
        self._img_x0 = 0.0        # scroll-x offset of the windowed heatmap image
        self._redraw_pending = False
        self.gram_h = float(GRAM_H)
        self._resize_job = None
        self._scroll_job = None
        self._last_h = 0
        self.opt = dict(notes=True, flags=True, bands=True, chart_colors=True,
                        hz_readout=False, grid=False, grid_div=4, flash=True,
                        note_shape="diamond", lane_visible=None)
        # style: "gram" = the log-freq heatmap; "wave" = the mirrored amplitude
        # waveform. Both live on THIS view rather than in a separate widget so
        # the ruler, playhead, note strip, scroll-sync and zoom are shared code
        # instead of a second copy that drifts.
        self.render = dict(brightness=1.15, fmax=16000.0, log_scale=True,
                           cmap="magma", style="gram")

        self.axis = tk.Canvas(self, width=AXIS_W, height=200,
                              background=CANVAS_BG, highlightthickness=0)
        self.axis.pack(side=tk.LEFT, fill=tk.Y)
        right = tk.Frame(self, background=BG)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(right, background=CANVAS_BG,
                                height=200, highlightthickness=0)
        self.hbar = ttk.Scrollbar(right, orient=tk.HORIZONTAL,
                                  style="Spec.Horizontal.TScrollbar",
                                  command=self._on_scrollbar)
        self.canvas.configure(xscrollcommand=self.hbar.set)
        self.canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.hbar.pack(side=tk.TOP, fill=tk.X)
        self.canvas.bind("<Configure>", self._on_configure)
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<Motion>", self._motion)
        self.canvas.bind("<Leave>", self._leave)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self._refresh_scrollregion()

    # ----- wiring ------------------------------------------------------------
    def _on_configure(self, event):
        self._build_image()      # viewport width changed -> re-window the image
        self._redraw_now()
        h = int(event.height)
        if h > 1 and h != self._last_h:
            self._last_h = h
            if self._resize_job is not None:
                self.after_cancel(self._resize_job)
            self._resize_job = self.after(120, self._relayout)

    def _relayout(self):
        self._resize_job = None
        avail = self.canvas.winfo_height()
        slack = avail - RULER_H - STRIP_ROW_H * len(ROLL_ROWS)
        new_h = max(float(GRAM_H_MIN), min(float(GRAM_H_MAX), float(slack)))
        if abs(new_h - self.gram_h) < 0.5:
            return
        self.gram_h = new_h
        self._refresh_scrollregion()
        if self._model is not None:
            self._build_image()
        self._redraw_now()

    def _clamp_xview(self):
        # Never scroll left of the chart start (t=0) — see LaneViewCanvas.
        try:
            if self.canvas.canvasx(0) < 0:
                self.canvas.xview_moveto(0.0)
        except Exception:
            pass

    def _on_scrollbar(self, *args):
        self.canvas.xview(*args)      # cheap: the viewport moves immediately
        self._clamp_xview()
        self._scroll_render_soon()    # expensive PPM re-window is throttled

    def _wheel(self, event):
        if event.state & 0x0004:
            if self._on_zoom:
                self._on_zoom(1 if event.delta > 0 else -1)
        else:
            self.canvas.xview_scroll(-1 * (event.delta // 120) * 3, "units")
            self._clamp_xview()
            self._scroll_render_soon()

    def _scroll_render_soon(self):
        # Throttle the re-window+redraw on scroll (v4.9.2 lag fix): rebuilding
        # the whole PPM heatmap on EVERY scroll event (a pure-Python byte loop)
        # was the spectrogram-scroll lag. The canvas xview already moved the
        # viewport, so coalesce the rebuild to ~36 fps. Leading-throttle: the
        # first event schedules a render; events inside the window are dropped;
        # the render then paints the CURRENT scroll position. Cancelled in
        # destroy().
        if self._scroll_job is not None:
            return
        self._scroll_job = self.after(_SCROLL_RENDER_MS, self._scroll_render_now)

    def _scroll_render_now(self):
        self._scroll_job = None
        if self._model is not None:
            self._build_image()
        self._redraw_now()

    def _click(self, event):
        if self._model is None:
            return
        if self._on_seek:
            self._on_seek(self._t_of(self.canvas.canvasx(event.x)))

    def _motion(self, event):
        self._cursor = (self.canvas.canvasx(event.x),
                        self.canvas.canvasy(event.y))
        if self.opt["hz_readout"]:
            self.redraw_overlay()

    def _leave(self, _event):
        self._cursor = None
        if self.opt["hz_readout"]:
            self.redraw_overlay()

    # ----- geometry ------------------------------------------------------------
    def _refresh_scrollregion(self):
        # The THIRD int(dur*pps) site — missed by the R6B2-3 fold, so
        # _build_image's own dur guard was undone by the very next call in
        # set_model (breaker R7B2-1, 2026-07-20).
        total = 800 if self._model is None else max(
            800, int(px_span(self._model.dur, self._eff_pps)))
        self.canvas.configure(scrollregion=(0, 0, total, self._total_h()))

    def _x_of(self, t):
        return t * self._eff_pps

    def _t_of(self, x):
        return x / self._eff_pps if self._eff_pps else 0.0

    def _fmax_eff(self):
        fmax = float(self.render["fmax"])
        if self._model is not None:
            fmax = min(fmax, self._model.gram_fmax)
        return max(GRAM_FMIN + 1.0, fmax)

    def _fy(self, freq):
        fmax = self._fmax_eff()
        freq = max(GRAM_FMIN, min(fmax, freq))
        if self.render["log_scale"]:
            frac = math.log(freq / GRAM_FMIN) / math.log(fmax / GRAM_FMIN)
        else:
            frac = (freq - GRAM_FMIN) / (fmax - GRAM_FMIN)
        return RULER_H + (1.0 - frac) * self.gram_h

    def _y_to_freq(self, y):
        frac = 1.0 - max(0.0, min(1.0, (y - RULER_H) / self.gram_h))
        fmax = self._fmax_eff()
        if self.render["log_scale"]:
            return GRAM_FMIN * (fmax / GRAM_FMIN) ** frac
        return GRAM_FMIN + (fmax - GRAM_FMIN) * frac

    # ----- public state -----------------------------------------------------------
    def set_model(self, model):
        self._model = model
        self._playhead = None
        self._snotes, self._sntimes = _sort_cache(          # v4.9.0 — see lane view
            getattr(model, "notes", None), lambda n: n[0]) if model else ([], [])
        self._sissues, self._sitimes = _sort_cache(
            getattr(model, "issues", None), lambda x: x["time"]) if model else ([], [])
        self._build_image()
        self._refresh_scrollregion()
        self.canvas.xview_moveto(0)
        self._redraw_now()

    def refresh_notes_cache(self):
        # Rebuild the time-sorted overlay caches after an IN-PLACE edit, WITHOUT
        # resetting scroll or rebuilding the heatmap (review #1, 2026-07-21):
        # _apply_notes mutates model.notes/issues but redraw_overlay reads these
        # sorted caches, so an edit was invisible/stale (added notes missing,
        # deleted still drawn + flashed) until the next full re-compare.
        m = self._model
        self._snotes, self._sntimes = _sort_cache(
            getattr(m, "notes", None), lambda n: n[0]) if m else ([], [])
        self._sissues, self._sitimes = _sort_cache(
            getattr(m, "issues", None), lambda x: x["time"]) if m else ([], [])
        self._active_lanes = _active_lane_set(m) if m else set()
        self.request_redraw()

    def set_zoom(self, pps):
        # Floor matches ZOOM_FIT_MIN, not 20.0 (breaker R4B3-1b, 2026-07-20):
        # the view-level 20 px/s clamp silently overrode a below-floor Fit —
        # the tab said 6 px/s while the view rendered at 20.
        self._pps = max(ZOOM_FIT_MIN, min(1400.0, float(pps)))
        self._build_image()
        self._refresh_scrollregion()
        self._redraw_now()

    def set_options(self, **kw):
        self.opt.update(kw)
        self.request_redraw()

    def set_render_params(self, **kw):
        changed = False
        for k, v in kw.items():
            if k in self.render and self.render[k] != v:
                self.render[k] = v
                changed = True
        if changed and self._model is not None:
            self._build_image()
        self.request_redraw()

    def set_playhead(self, t):
        self._react_last = self._playhead
        self._playhead = None if t is None else float(t)
        self._draw_playhead()   # decoupled sweep (v4.9.2) — see LaneView.set_playhead

    def _draw_playhead(self):
        """Thin per-tick playhead layer (v4.9.2) — tagged 'playhead', separate
        from 'overlay', so a sweep tick repaints only this line plus the handful
        of note-strip markers it is flashing over, rather than every visible
        note/flag. Photo guard mirrors redraw_overlay (no sweep over an empty
        heatmap). The flash mirrors LaneView._draw_playhead so the note-strip
        lights up in step with the lane view (owner-reported 2026-07-23: the
        reactive flash was missing from the spectrogram view)."""
        c = self.canvas
        c.delete("playhead")
        if self._playhead is None or self._model is None or self._photo is None:
            return
        pos = self._playhead
        if self.opt.get("notes") and self.opt.get("flash"):
            strip_y0 = RULER_H + self.gram_h
            _vis = self.opt.get("lane_visible")
            last = self._react_last if self._react_last is not None else pos
            advanced = last < pos <= last + 0.12
            # Only notes inside the flash window can light up -> a windowed slice
            # of a handful, not the whole visible set (matches the lane view).
            f_lo = min(pos - 0.05, last) - 0.001
            for t, lane, _vel in _iter_window(self._snotes, self._sntimes,
                                              f_lo, pos + 0.001):
                if _vis is not None and lane not in _vis:
                    continue
                if lane not in ROLL_ROWS:
                    continue
                dt = pos - t
                if (0.0 <= dt <= 0.05) or (advanced and last < t <= pos):
                    x = self._x_of(t)
                    yc = (strip_y0 + ROLL_ROWS.index(lane) * STRIP_ROW_H
                          + STRIP_ROW_H / 2.0)
                    _paint_note(c, x, yc, 5.0, NOTE_OUTLINE,
                                self.opt.get("note_shape", "diamond"),
                                tag="playhead")
        px = self._x_of(pos)
        c.create_line(px, 0, px, self._total_h(), fill=PLAYHEAD_COL,
                      width=1, tags="playhead")

    def playhead_x(self):
        return None if self._playhead is None else self._x_of(self._playhead)

    # ----- heatmap image ----------------------------------------------------------
    def _build_image(self):
        # dur<=0 guard (breaker B2-1/EDGE-1, 2026-07-20): a zero-length decode
        # produced a dur=0 model and `cols / m.dur` threw ZeroDivisionError on
        # the MAIN thread (past the worker's try/except), wedging the tab on
        # "Analyzing…". _finish_real_compare now rejects such models upstream;
        # this is the belt-and-suspenders render guard.
        # `not (0 < dur < inf)` instead of `dur <= 0`: NaN fails every
        # comparison, so it slid past the round-1 guard straight into
        # int(NaN*pps) (breaker R2B2-2, 2026-07-20); inf would OverflowError.
        if self._model is None or not (0 < self._model.dur < float("inf")):
            self._photo = None
            self._eff_pps = self._pps
            self._img_x0 = 0.0
            return
        m = self._model
        pps = self._pps
        # WINDOWED render (owner-reported spectrogram-zoom fix, 2026-07-20):
        # render ONLY the visible time-slice at the TRUE requested pps, so the
        # image width stays ~viewport-sized and zoom works at ANY pps. The old
        # code built ONE whole-song image and clamped its width to 8192 px, so
        # `_eff_pps = 8192/dur` capped at ~40 px/s on a multi-minute song — the
        # slider "did nothing" above that. `_eff_pps` is now just `pps` (uncapped;
        # the whole horizontal scale, overlay + scrollregion, follows it), and the
        # image is a moving window positioned at `_img_x0` in scroll coords.
        self._eff_pps = pps
        total = max(1.0, px_span(m.dur, pps))       # full scroll-region width
        try:
            vx0 = float(self.canvas.canvasx(0))
            vw = max(1, int(self.canvas.winfo_width()))
        except Exception:
            vx0, vw = 0.0, 1200
        vx0 = max(0.0, min(vx0, total))
        win = int(max(1, min(8192, min(vw + 4, total - vx0 + 4))))
        self._img_x0 = vx0
        dur = m.dur
        last = GRAM_COLS - 1
        col_idx = []
        for i in range(win):
            cm = int((vx0 + i) / pps / dur * last) if dur > 0 else 0
            col_idx.append(0 if cm < 0 else (last if cm > last else cm))
        gh = int(round(self.gram_h))
        if self.render.get("style") == "wave":
            # vx0/pps, NOT col_idx: col_idx indexes the 6000-column whole-song
            # spectrogram grid, and the waveform envelope is much finer (see
            # WAVE_ENV_RATE). Reusing col_idx would throw that resolution away and
            # render ~29 px stair-steps at 300 px/s.
            self._photo = self._build_wave_photo(m, vx0, pps, win, gh)
            return
        # FULL-RESOLUTION heatmap straight from the STFT, when the model carries one.
        # Falls through to the gram_rows path below for models that do not (the demo
        # mock, and anything hand-built) — see _build_gram_photo_hires.
        _hi = self._build_gram_photo_hires(m, vx0, pps, win, gh)
        if _hi is not None:
            self._photo = _hi
            return
        lut = cmap_lut(self.render["cmap"], float(self.render["brightness"]))
        rows_bytes = []
        append = rows_bytes.append
        for y in range(gh):
            freq = self._y_to_freq(RULER_H + y + 0.5)
            mrow = m.gram_rows[int(m.gram_row_of_freq(freq))]
            append(b"".join(map(lut.__getitem__,
                                map(mrow.__getitem__, col_idx))))
        header = ("P6 %d %d 255\n" % (win, gh)).encode("ascii")
        self._photo = tk.PhotoImage(data=header + b"".join(rows_bytes),
                                    format="PPM")

    def _build_gram_photo_hires(self, m, vx0, pps, win, gh):
        """The visible heatmap window built straight from the STFT. None if the model
        has no spectrum, in which case the caller uses the gram_rows grid.

        WHY THIS EXISTS — the master grid was throwing away most of the analysis.
        `_build_gram_rows` resamples the STFT into a fixed GRAM_COLS x GRAM_ROWS
        (6000 x 300) WHOLE-SONG grid. Measured on a 581 s stem at the engine's own
        N_FFT=2048 / HOP=512: the STFT produces **86.1 frames/sec** (50,034 frames),
        while 6000 columns over 581 s is **10.33 columns/sec** — so **8.3x of the
        time resolution that was already computed and paid for** was discarded
        before anything was drawn. On screen each master column then stretched to
        ~19 px at 200 px/s, ~29 px at 300, **~87 px at 900**, and ~136 px at the
        1400 px/s zoom ceiling: the heatmap read as blocks, not as spectral detail.
        The loss is duration-dependent and begins past 6000/86.1 = ~70 s of audio,
        which is why no short test fixture could reproduce it.
        (Owner report: the spectrogram is "sometimes not very helpful". A large part
        of that was this, not the audio.)
        This is the same coarse-grid trap that made the Waveform style stair-step
        until its envelope was decoupled from GRAM_COLS.

        WINDOWED, NOT A BIGGER GRID. The image is already windowed to the viewport,
        so the fix is to build only the visible span at display resolution rather
        than to enlarge the stored grid — a full-resolution 300 x 50,034 grid would
        cost ~15 MB per model, and this costs nothing beyond the (gh x win) image
        that was being produced anyway.

        The vertical mapping is deliberately IDENTICAL to `_build_gram_rows` — same
        `arange(1, half) * bin_hz` search — so nothing moves on screen; only the
        detail changes. Display rows now resolve to STFT bins directly instead of
        being quantised through 300 grid rows first, which removes a second
        rounding step on the frequency axis as well.
        """
        try:
            import numpy as np

            spec = getattr(m, "_spec", None)
            if not isinstance(spec, dict):
                return None
            db = spec.get("db")
            if db is None or getattr(db, "ndim", 0) != 2 or db.size == 0:
                return None
            half, n_frames = db.shape
            if half < 2 or n_frames < 1:
                return None
            bin_hz = float(spec.get("binHz", 0.0) or 0.0)
            if not (bin_hz > 0.0):
                return None
            floor = float(spec.get("maxDb", -80.0)) - 80.0
            dur = float(m.dur)

            # time: display pixel -> song seconds -> STFT frame
            t = (np.arange(win, dtype=np.float64) + float(vx0)) / max(1e-9, float(pps))
            fr = np.clip(np.rint(np.clip(t / dur, 0.0, 1.0) * (n_frames - 1)),
                         0, n_frames - 1).astype(np.intp)

            # frequency: display row -> Hz -> STFT bin, matching _build_gram_rows
            src_freqs = np.arange(1, half) * bin_hz
            freqs = np.array([self._y_to_freq(RULER_H + y + 0.5) for y in range(gh)],
                             dtype=np.float64)
            ri = np.clip(np.searchsorted(src_freqs, freqs), 0, src_freqs.size - 1).astype(np.intp)

            # Outer-product indexing, NOT db[ri][:, fr]: the latter materialises an
            # intermediate (gh x n_frames) — 250 x 50,034 float32 = ~50 MB on a
            # 10-minute song, on the MAIN thread, every repaint.
            vals = db[ri[:, None], fr[None, :]]
            norm = np.clip(((vals - floor) / 80.0) * 255.0, 0, 255).astype(np.uint8)

            lut = cmap_lut(self.render["cmap"], float(self.render["brightness"]))
            lut_arr = np.frombuffer(b"".join(lut), dtype=np.uint8).reshape(256, 3)
            img = lut_arr[norm]
            header = ("P6 %d %d 255\n" % (win, gh)).encode("ascii")
            return tk.PhotoImage(data=header + img.tobytes(), format="PPM")
        except Exception:
            # Never let this take the tab down: the gram_rows path below is a
            # complete, working renderer and is a correct answer, just a coarser one.
            return None

    # ----- Waveform render style ------------------------------------------------
    #
    # Monochrome by owner direction (2026-08-17): get the SHAPE working first,
    # colour later. So there is deliberately no colormap, no brightness and no
    # per-channel hue here -- one foreground colour, and the only information
    # carried is the outline. Left drives the upper half, right the lower, so
    # the two halves differ on a stereo source and the asymmetry is real audio
    # rather than decoration.
    WAVE_FG = (0x00, 0xd4, 0xd4)        # CYAN, the app accent
    WAVE_MID = (0x46, 0x46, 0x5a)       # centre reference line
    WAVE_BG = (0x07, 0x07, 0x10)        # CANVAS_BG, so the strip matches the canvas

    def _build_wave_photo(self, m, vx0, pps, win, gh):
        """Mirrored amplitude waveform as a PPM, same geometry as the heatmap.

        Time axis is derived from the SAME (vx0, pps) the heatmap uses, so the
        trace lands on exactly the same time positions as the spectrogram, the
        ruler, the note strip and the playhead -- that shared mapping is the
        reason this is a style of GramView rather than a separate widget. What it
        does NOT share is the column INDEX, because the envelope is finer than
        the 6000-column spectrogram grid.
        """
        import numpy as np

        env = getattr(m, "wave_env", None)
        top_src = bot_src = None
        if env is not None and len(env) == 2:
            try:
                _t = np.asarray(env[0], np.float32)
                _b = np.asarray(env[1], np.float32)
                # `.size` and not just `is not None`: an EMPTY envelope passed the
                # not-None check, and then `size - 1` = -1 turned the index clamp
                # into a negative bound -> IndexError on the MAIN thread, past the
                # worker's try/except. Non-finite values would silently become a
                # zero-height trace, so they are rejected here too rather than
                # drawn as a flat line that looks like real silence.
                if _t.size and _b.size and np.isfinite(_t).all() and np.isfinite(_b).all():
                    top_src, bot_src = _t, _b
            except (TypeError, ValueError):
                top_src = bot_src = None
        if top_src is None:
            # No amplitude envelope (the demo model, or a decode that failed).
            # Derive a shape from the spectrogram grid so the view is never
            # blank. This is measurably WORSE at showing transients (~1.2 vs
            # ~2.8 peak-to-median) and is a fallback, not the intended path.
            g = np.asarray([bytes(r) for r in m.gram_rows], dtype=np.uint8) \
                if not isinstance(m.gram_rows[0], (bytes, bytearray)) \
                else np.frombuffer(b"".join(bytes(r) for r in m.gram_rows),
                                   dtype=np.uint8).reshape(len(m.gram_rows), -1)
            col_max = g.max(axis=0).astype(np.float32)
            peak = float(col_max.max()) or 1.0
            top_src = bot_src = col_max / peak

        # Display pixel -> song time -> envelope index, at the envelope's own
        # resolution. `dur` is already known finite and > 0 (checked by the caller).
        dur = float(m.dur)
        t = (np.arange(win, dtype=np.float64) + float(vx0)) / max(1e-9, float(pps))
        frac = np.clip(t / dur, 0.0, 1.0)
        # np.clip on the index too: rounding at frac==1.0 can land one past the end,
        # and an IndexError here surfaces on the MAIN thread past the worker's
        # try/except. Also covers a short/hand-built envelope.
        ti = np.clip((frac * (top_src.size - 1)).astype(np.int64), 0, top_src.size - 1)
        bi = np.clip((frac * (bot_src.size - 1)).astype(np.int64), 0, bot_src.size - 1)
        top = top_src[ti]
        bot = bot_src[bi]
        # One display pixel can span MANY envelope samples when zoomed out (at
        # 30 px/s each pixel is ~27 samples at 800/s). Point-sampling would then
        # drop peaks between samples and the trace would flicker as it scrolls, so
        # reduce each pixel's whole span by max() instead of picking one value.
        step = float(top_src.size - 1) / max(1e-9, dur) / max(1e-9, float(pps))
        if step >= 2.0:
            k = int(min(64, step))          # cap the work; 64 is ample past ~8 px/s
            offs = np.linspace(0, step, k, endpoint=False).astype(np.int64)[None, :]
            top = top_src[np.clip(ti[:, None] + offs, 0, top_src.size - 1)].max(axis=1)
            bot = bot_src[np.clip(bi[:, None] + offs, 0, bot_src.size - 1)].max(axis=1)

        mid = gh // 2
        room = max(1, mid - 1)
        # gamma < 1 lifts quiet detail. Ghost notes and hi-hat articulation sit
        # far below the kick/snare peaks, and on a linear scale they flatten to
        # nothing -- which is the exact complaint this view exists to answer.
        g_exp = 0.55
        t_px = np.clip(np.power(np.clip(top, 0.0, 1.0), g_exp) * room, 0, room)
        b_px = np.clip(np.power(np.clip(bot, 0.0, 1.0), g_exp) * room, 0, room)

        yy = np.arange(gh, dtype=np.float32)[:, None]
        dy = yy - mid
        on = np.where(dy < 0, (-dy) <= t_px[None, :], dy <= b_px[None, :])
        img = np.empty((gh, win, 3), np.uint8)
        img[:] = np.asarray(self.WAVE_BG, np.uint8)
        img[on] = np.asarray(self.WAVE_FG, np.uint8)
        if 0 <= mid < gh:
            img[mid] = np.asarray(self.WAVE_MID, np.uint8)
        header = ("P6 %d %d 255\n" % (win, gh)).encode("ascii")
        return tk.PhotoImage(data=header + img.tobytes(), format="PPM")

    # ----- drawing ------------------------------------------------------------------
    def request_redraw(self):
        if not self._redraw_pending:
            self._redraw_pending = True
            # tracked so destroy() can cancel it (breaker R2E-2, 2026-07-20)
            self._redraw_job = self.after_idle(self._redraw_now)

    def _redraw_now(self):
        self._redraw_pending = False
        # A DIRECT _redraw_now() call (set_zoom/_relayout do this) used to
        # orphan the still-queued idle job from an earlier request_redraw —
        # harmless double-draw in life, but a bgerror after destroy (breaker
        # R2E-2). Cancel it; cancelling the id we are currently running FROM
        # is a safe no-op.
        job = getattr(self, "_redraw_job", None)
        self._redraw_job = None
        if job is not None:
            try:
                self.after_cancel(job)
            except Exception:
                pass
        self._draw_static()
        self.redraw_overlay()

    def destroy(self):
        # Cancel pending relayout/redraw after-callbacks BEFORE teardown
        # (breaker R2E-2, 2026-07-20): they fired on the destroyed view as
        # "invalid command name" Tcl bgerrors.
        for _attr in ("_resize_job", "_redraw_job", "_scroll_job"):
            _job = getattr(self, _attr, None)
            if _job is not None:
                try:
                    self.after_cancel(_job)
                except Exception:
                    pass
                setattr(self, _attr, None)
        # Drop the PhotoImage ref ON THE MAIN THREAD (breaker R3B1-1,
        # 2026-07-20): left in the widget's reference cycles it dies via the
        # cyclic GC — possibly on a worker thread, where Image.__del__ raises
        # an ignored RuntimeError and leaks the Tcl-side image.
        if getattr(self, "_photo", None) is not None:
            self._photo = None
        super().destroy()

    def _visible_x(self):
        x0 = self.canvas.canvasx(0)
        x1 = self.canvas.canvasx(max(1, self.canvas.winfo_width()))
        return max(0.0, x0 - 2), x1 + 2

    def _draw_static(self):
        c = self.canvas
        c.delete("static")
        if self._model is None or self._photo is None:
            self._draw_empty_skeleton(c)
            return
        x0, x1 = self._visible_x()
        # Windowed image: placed at its scroll-x offset (see _build_image).
        c.create_image(getattr(self, "_img_x0", 0.0), RULER_H, anchor=tk.NW,
                       image=self._photo, tags="static")
        if self.opt["bands"]:
            for _key, _label, _lo, hi in BANDS:
                y = self._fy(hi)
                c.create_line(x0, y, x1, y, fill="#3d3358", width=1,
                              tags="static")
        _vis = self.opt.get("lane_visible")   # None -> all lanes shown
        for i, lane in enumerate(ROLL_ROWS):
            y0 = RULER_H + self.gram_h + i * STRIP_ROW_H
            _hidden = _vis is not None and lane not in _vis
            c.create_rectangle(x0, y0, x1, y0 + STRIP_ROW_H,
                               fill=blend(CANVAS_BG, LANES[lane][2],
                                          0.02 if _hidden else 0.08),
                               outline="", tags="static")
            c.create_line(x0, y0 + 0.5, x1, y0 + 0.5, fill=SEPARATOR,
                          tags="static")
        if self.opt.get("grid"):    # v4.9.0 — same timing grid as the per-lane view
            _paint_time_grid(self, x0, x1, RULER_H,
                             RULER_H + self.gram_h + STRIP_ROW_H * len(ROLL_ROWS))
        self._draw_ruler(x0, x1)
        self._draw_axis()

    def _draw_empty_skeleton(self, c):
        """Nothing loaded yet: draw the empty Spectrogram skeleton -- the (empty)
        heatmap area + band guide lines, the per-lane note-strip rows below it,
        the ruler, and the full frequency axis with band + lane labels -- so the
        Spectrogram view shows its real layout before any Compare. The hint sits
        over the empty heatmap on a dark card so it stays legible."""
        x0, x1 = self._visible_x()
        # Empty heatmap area (where the spectrogram image would render).
        c.create_rectangle(x0, RULER_H, x1, RULER_H + self.gram_h,
                           fill=CANVAS_BG, outline="", tags="static")
        if self.opt["bands"]:
            for _key, _label, _lo, hi in BANDS:
                y = self._fy(hi)
                c.create_line(x0, y, x1, y, fill="#3d3358", width=1,
                              tags="static")
        # Per-lane note-strip rows below the heatmap (match the loaded look).
        for i, lane in enumerate(ROLL_ROWS):
            y0 = RULER_H + self.gram_h + i * STRIP_ROW_H
            c.create_rectangle(x0, y0, x1, y0 + STRIP_ROW_H,
                               fill=blend(CANVAS_BG, LANES[lane][2], 0.08),
                               outline="", tags="static")
            c.create_line(x0, y0 + 0.5, x1, y0 + 0.5, fill=SEPARATOR,
                          tags="static")
        self._draw_ruler(x0, x1)
        self._draw_axis()
        # Hint over the empty heatmap area, on a dark card.
        cx = max(x0 + 8, min(x1 - 8, (x0 + x1) / 2.0))
        cy = RULER_H + self.gram_h / 2.0
        hint = c.create_text(
            cx, cy,
            text=("Run Compare to see the spectrogram with the detected notes\n"
                  "overlaid on the audio's frequency content.\n"
                  "(standalone demo: synthetic data)"),
            fill=MUTED, font=F_BASE, justify=tk.CENTER, tags="static")
        bb = c.bbox(hint)
        if bb:
            pad = 10
            card = c.create_rectangle(bb[0] - pad, bb[1] - pad, bb[2] + pad,
                                      bb[3] + pad, fill=CANVAS_BG,
                                      outline=SEPARATOR, tags="static")
            c.tag_lower(card, hint)

    def _draw_ruler(self, x0, x1):
        c = self.canvas
        c.create_rectangle(x0, 0, x1, RULER_H, fill=DARKER, outline="",
                           tags="static")
        step = next((s for s in (1, 2, 5, 10, 15, 30, 60, 120)
                     if s * self._eff_pps >= 55), 120)
        draw_ticks = True
        if step * self._eff_pps < 55:
            # The table caps at 120 s; at a collapsed eff_pps that puts a
            # label every ~1 px (breaker R4B3-2). Extend to whole minutes so
            # labels always keep >=55 px spacing — using the TRUE eff_pps,
            # never a floored copy (breaker R5B2-3: the old 1e-9 floor broke
            # the guarantee below it — dur=1e100 hung the redraw); past
            # ~1e12 s between labels, skip the tick loop entirely.
            needed = 55.0 / self._eff_pps if self._eff_pps > 0 else float("inf")
            if needed > 1e12:
                draw_ticks = False
            else:
                step = max(120, int(math.ceil(needed / 60.0)) * 60)
        # Bound the loop by PIXELS, not song seconds (breaker R4B3-2,
        # 2026-07-20): iterating t += 1 put one canvas item per SECOND of the
        # visible span — O(dur) once the cols cap collapses eff_pps (dur=1e6
        # -> 100k items/redraw; 1e9 -> a 23 GB blowup). When per-second minor
        # ticks are sub-pixel noise (< ~2 px apart), walk label steps instead.
        stride = 1 if self._eff_pps >= 2.0 else step
        # Seed the loop only if there is anything to draw (breaker R6B2-4,
        # 2026-07-20): _t_of(x0) = x0/pps overflows to inf at denormal pps
        # with a SCROLLED window (x0>0), and int(inf) raised OverflowError
        # BEFORE the skip below could take effect (x0==0 was safe, which is
        # why the round-5 guard missed it).
        t_seed = self._t_of(x0)
        if not (abs(t_seed) < 1e15):
            draw_ticks = False
        t = int(t_seed) if draw_ticks else 0
        if stride > 1:
            t -= t % stride
        while draw_ticks and self._x_of(t) <= x1:
            x = self._x_of(t)
            if t % step == 0:
                c.create_line(x, RULER_H - 6, x, RULER_H, fill=MUTED,
                              tags="static")
                c.create_text(x + 3, RULER_H - 8, text=fmt_time(t),
                              anchor=tk.W, fill=MUTED, font=F_MONO,
                              tags="static")
            else:
                c.create_line(x, RULER_H - 3, x, RULER_H, fill="#3a3a55",
                              tags="static")
            t += stride
        c.create_line(x0, RULER_H - 0.5, x1, RULER_H - 0.5, fill=SEPARATOR,
                      tags="static")

    def _draw_axis(self):
        a = self.axis
        a.delete("all")
        w = AXIS_W
        a.create_rectangle(0, 0, w, RULER_H, fill=DARKER, outline="")
        # NB: the axis (freq ticks + band + lane labels) is geometry-only, so it
        # renders the same with or without a model — the empty Spectrogram
        # skeleton relies on this (owner 2026-07-20: show the layout when empty).
        for f in _FREQ_TICKS:
            if f >= self._fmax_eff():
                continue
            y = self._fy(f)
            a.create_line(w - 4, y, w, y, fill="#3d3358")
            lbl = ("%gk" % (f / 1000.0)) if f >= 1000 else str(f)
            a.create_text(w - 5, y, text=lbl, anchor=tk.E, fill="#968eb0",
                          font=F_MONO)
        if self.opt["bands"]:
            for _key, label, lo, hi in BANDS:
                yc = (self._fy(lo) + self._fy(hi)) / 2.0
                short = label.split("/")[0].strip()
                a.create_text(4, yc - (7 if self.opt["hz_readout"] else 0),
                              text=short, anchor=tk.W, fill="#d2cce8",
                              font=F_SMALL)
                if self.opt["hz_readout"]:
                    def hz(f):
                        return ("%gk" % (f / 1000.0)).replace(".0k", "k") \
                            if f >= 1000 else str(int(f))
                    a.create_text(4, yc + 7, text="%s-%s" % (hz(lo), hz(hi)),
                                  anchor=tk.W, fill=MUTED, font=F_MONO)
        _vis = self.opt.get("lane_visible")
        for i, lane in enumerate(ROLL_ROWS):
            yc = RULER_H + self.gram_h + i * STRIP_ROW_H + STRIP_ROW_H / 2.0
            _hidden = _vis is not None and lane not in _vis
            a.create_text(w - 5, yc, text=LANES[lane][1], anchor=tk.E,
                          fill=(MUTED if _hidden else LANES[lane][2]),
                          font=F_SMALL)
        a.create_line(w - 0.5, 0, w - 0.5, self._total_h(), fill=SEPARATOR)

    def redraw_overlay(self):
        c = self.canvas
        c.delete("overlay")
        if self._model is None or self._photo is None:
            return
        x0, x1 = self._visible_x()
        t0, t1 = self._t_of(x0) - 0.1, self._t_of(x1) + 0.1

        def strip_yc(lane):
            i = ROLL_ROWS.index(lane)
            return RULER_H + self.gram_h + i * STRIP_ROW_H + STRIP_ROW_H / 2.0

        _vis = self.opt.get("lane_visible")   # None -> all lanes shown

        if self.opt["flags"]:
            for issue in _iter_window(self._sissues, self._sitimes, t0, t1):
                t = issue["time"]
                x = self._x_of(t)
                lane = int(issue["lane"])
                if _vis is not None and lane not in _vis:
                    continue
                is_miss = issue["type"] == "miss"
                col = MISS_GREEN if is_miss else PHANTOM_ORANGE
                yc = strip_yc(lane)
                c.create_line(x, RULER_H, x, RULER_H + self.gram_h, fill=col,
                              dash=(2, 3), width=1, tags="overlay")
                c.create_oval(x - 8, yc - 8, x + 8, yc + 8, outline="#000000",
                              width=3, tags="overlay")
                c.create_oval(x - 8, yc - 8, x + 8, yc + 8, outline=col,
                              width=2, tags="overlay")
                c.create_text(x, yc, text="+" if is_miss else "\u00d7",
                              fill=col, font=F_MONO_B, tags="overlay")
        if self.opt["notes"]:
            r = 5.0
            for t, lane, _vel in _iter_window(self._snotes, self._sntimes, t0, t1):
                if _vis is not None and lane not in _vis:
                    continue
                x, yc = self._x_of(t), strip_yc(lane)
                color = (LANES[lane][2] if self.opt["chart_colors"]
                         else BAND_COLORS[LANE_TO_BAND[lane]])
                _paint_note(c, x, yc, r, color, self.opt.get("note_shape", "diamond"))
        if self.opt["hz_readout"] and self._cursor is not None:
            cx, cy = self._cursor
            if RULER_H <= cy <= RULER_H + self.gram_h:
                c.create_line(x0, cy, x1, cy, fill=PURPLE_LT, dash=(4, 3),
                              width=1, tags="overlay")
                freq = self._y_to_freq(cy)
                t = self._t_of(cx)
                label = ("%d Hz  \u00b7  %s" % (freq, fmt_time(t))) if freq < 1000 \
                    else ("%.2f kHz  \u00b7  %s" % (freq / 1000.0, fmt_time(t)))
                bx, by = cx + 12, max(RULER_H + 4, cy - 24)
                tid = c.create_text(bx, by, text=label, anchor=tk.W,
                                    fill="#e9e4ff", font=F_MONO,
                                    tags="overlay")
                bb = c.bbox(tid)
                if bb:
                    c.create_rectangle(bb[0] - 4, bb[1] - 2, bb[2] + 4,
                                       bb[3] + 2, fill=DARKER,
                                       outline=PURPLE_LT, tags="overlay")
                    c.tag_raise(tid)
        # Playhead lives on its own thin layer now (see _draw_playhead).
        self._draw_playhead()

# ---------------------------------------------------------------------------
# The tab ----------------------------------------------------------------------
# ---------------------------------------------------------------------------
class SpectralTab(ttk.Frame):
    """The Spectral Comparison tab. Builds into any parent frame; in v4 this
    becomes a Notebook page. Theme lives in apply_theme_*(); everything below is
    layout + behaviour.

    ``hooks`` is None for the standalone review host, or a dict supplied by v4
    with any subset of: decode_audio, get_cfg, set_cfg, mixer_play, mixer_stop,
    mixer_pos. Missing or raising hooks degrade to the standalone behaviour for
    that seam.
    """

    def __init__(self, parent, hooks=None, **kw):
        super().__init__(parent, style="Spec.TFrame", **kw)
        self.hooks = hooks if hooks is not None else {}
        self._model = None
        self._zoom = ZOOM_DEFAULT
        self._playing = False
        self._paused = False          # distinct from stopped: resume, don't reload
        self._play_t = 0.0
        self._speed = 1.0             # playback speed (pitch preserved by the host)
        self._tick_job = None
        self._tick_last = 0.0
        self._zoom_job = None
        self._bright_job = None
        self._synth_job = None
        self._comparing = False
        self._compare_queue = None
        self._compare_thread = None
        self._poll_compare_job = None
        self._undo_stack = []
        self._redo_stack = []
        self._overwrite_target = ""
        self._lane_visible = set(range(len(LANES)))   # all instruments shown

        if self.hooks:
            apply_theme_embedded(self)

        self._build_header()
        self._build_sources()
        self._build_viewbar()
        self._build_options()
        self._build_advanced()
        self._build_inst_panel()
        self._build_canvas()
        self._build_footer()
        self._bind_keys()
        self._apply_lane_options()
        self._apply_gram_options()
        # NO audio is loaded by default (2026-07-20 owner request): the tab
        # opens with empty Reference/Chart fields. We intentionally do NOT
        # restore the last-used paths here. (`spec_last_*` is still written on
        # browse and only used to seed the file-dialog's initial directory.)
        self._update_readout()
        self._update_transport()

    # ----- hook helpers ------------------------------------------------------
    def _cfg_get(self, key: str, default=""):
        if "get_cfg" not in self.hooks:
            return default
        try:
            val = self.hooks["get_cfg"](key, default)
        except Exception as e:
            self._status("config read failed: %s" % e, AMBER)
            return default
        # Type-guard against a garbage config store (breaker B2-4, 2026-07-20):
        # a non-str where a path is expected reached os.path.isfile() and threw
        # in the CONSTRUCTOR — hooks must never crash the tab. Wrong type ->
        # behave as if the key were unset.
        if default is not None and val is not None \
                and not isinstance(val, type(default)):
            return default
        return val

    def _cfg_set(self, key: str, value):
        if "set_cfg" not in self.hooks:
            return
        try:
            self.hooks["set_cfg"](key, value)
        except Exception as e:
            self._status("config save failed: %s" % e, AMBER)

    def external_chart_changed(self, path: str):
        """The HOST rewrote `path` (a MIDI Editor save) while this tab may hold a
        compare of that same file (breaker H6, reverse direction, 2026-07-29).
        Deliberately does NOT touch the model -- it only disarms Overwrite, so a
        now-stale model can never be written back over the newer file, and says
        why. Re-run Compare to pick the new file up."""
        try:
            tgt = self._overwrite_target
            if not tgt or not path:
                return
            if (os.path.normcase(os.path.abspath(tgt))
                    != os.path.normcase(os.path.abspath(path))):
                return
            self._overwrite_target = ""
            # The status line scrolls away; the BUTTON is the durable signal, so grey
            # it out too. Without this the changelog's promise that saving in the
            # editor "disables an Overwrite that would write a stale comparison back
            # over it" was only half true -- the write was blocked, but nothing looked
            # blocked until you pressed it and got the wrong reason.
            self._update_transport()
            self._status("%s was rewritten outside this tab -- Overwrite is "
                         "disarmed; run Compare again to reload it."
                         % os.path.basename(path), AMBER)
        except Exception:
            pass

    def _hook_call(self, name: str, *args, **kwargs):
        """Call a hook by name, returning (ok, result). On missing/exception,
        report to the status line and return (False, None)."""
        if name not in self.hooks:
            return (False, None)
        try:
            return (True, self.hooks[name](*args, **kwargs))
        except Exception as e:
            self._status("%s hook failed: %s" % (name, e), AMBER)
            return (False, None)

    # ----- header ------------------------------------------------------------
    def _build_header(self):
        bar = tk.Frame(self, background=PANEL, highlightthickness=1,
                       highlightbackground=PURPLE_DEEP)
        bar.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(8, 6))
        left = tk.Frame(bar, background=PANEL)
        left.pack(side=tk.LEFT, padx=10, pady=6)
        ttk.Label(left, text="SPECTRAL COMPARISON",
                  style="Spec.Header.TLabel").pack(side=tk.LEFT)
        ttk.Label(
            left,
            text="   Did the detector chart it right?  Overlay the chart on "
                 "the audio and flag the disagreements.",
            style="Spec.PanelMuted.TLabel").pack(side=tk.LEFT, pady=(2, 0))
        right = tk.Frame(bar, background=PANEL)
        right.pack(side=tk.RIGHT, padx=10)
        self.notes_lbl = tk.Label(right, text="— notes", background=PANEL,
                                  foreground=MUTED, font=F_BOLD)
        self.miss_lbl = tk.Label(right, text="0 MISS", background=PANEL,
                                 foreground=MUTED, font=F_BOLD)
        self.phantom_lbl = tk.Label(right, text="0 PHANTOM", background=PANEL,
                                    foreground=MUTED, font=F_BOLD)
        self.notes_lbl.pack(side=tk.LEFT, padx=(0, 10))
        self.miss_lbl.pack(side=tk.LEFT, padx=(0, 10))
        self.phantom_lbl.pack(side=tk.LEFT)
        Tooltip(self.miss_lbl, "Green + on a lane: an audio hit with no "
                               "charted note near it.")
        Tooltip(self.phantom_lbl, "Orange \u00d7 on a lane: a charted note "
                                  "with no audio energy under it.")

    # ----- sources -------------------------------------------------------------
    def _build_sources(self):
        box = ttk.LabelFrame(self, text="1 \u00b7 Sources \u2014 load, then Compare",
                             style="Spec.TLabelframe")
        box.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(0, 6))
        box.columnconfigure(1, weight=1)

        # Chart FIRST (top row) — owner 2026-07-20: the MIDI / chart is the
        # primary thing you're reviewing, so it sits above the audio fields.
        ttk.Label(box, text="Chart:",
                  style="Spec.Panel.TLabel").grid(row=0, column=0, sticky=tk.E,
                                                  padx=(8, 6), pady=(6, 2))
        self.candidate_field = PlaceholderEntry(
            box, "detected chart (.mid .json .rlrr)",
            tooltip="The candidate chart whose notes are overlaid on the "
                    "audio's energy and mapped to drum lanes.")
        self.candidate_field.grid(row=0, column=1, sticky=tk.EW, pady=(6, 2))
        self.cand_load_btn = OutlineButton(
            box, "Load chart", accent=PURPLE_EDGE,
            command=self._on_cand_browse,
            tooltip="Choose the candidate chart file.")
        self.cand_load_btn.grid(row=0, column=2, padx=(6, 2), pady=(6, 2))
        self.cand_clear_btn = OutlineButton(
            box, "Clear", accent=MUTED, command=self._on_cand_clear,
            tooltip="Clear the chart field.")
        self.cand_clear_btn.grid(row=0, column=3, padx=(2, 8), pady=(6, 2))

        ttk.Label(box, text="Drums stem:",
                  style="Spec.Panel.TLabel").grid(row=1, column=0, sticky=tk.E,
                                                  padx=(8, 6), pady=(2, 2))
        self.reference_field = PlaceholderEntry(
            box, "isolated drums stem (.ogg .wav .flac) — drawn on the graph",
            tooltip="The isolated drums-only stem. THIS is the audio drawn on "
                    "the spectral graph and checked against the chart. A full "
                    "mix pollutes the view with every other instrument, so the "
                    "drums stem reads cleanest -- it is the important file here.")
        self.reference_field.grid(row=1, column=1, sticky=tk.EW, pady=(2, 2))
        self.ref_load_btn = OutlineButton(
            box, "Load drums", accent=PURPLE_EDGE, command=self._on_ref_browse,
            tooltip="Choose the drums-only stem.")
        self.ref_load_btn.grid(row=1, column=2, padx=(6, 2), pady=(2, 2))
        self.ref_clear_btn = OutlineButton(
            box, "Clear", accent=MUTED, command=self._on_ref_clear,
            tooltip="Clear the drums-stem field.")
        self.ref_clear_btn.grid(row=1, column=3, padx=(2, 8), pady=(2, 2))

        # Optional FULL MIX: the drums stem above is the important file; the
        # full mix is optional. When loaded, the Play-source toggle can hear it
        # and an Analyze toggle can view it for masking context -- but the graph
        # still analyses the drums by default. Compare does NOT need it.
        ttk.Label(box, text="Full mix (opt.):",
                  style="Spec.Panel.TLabel").grid(row=2, column=0, sticky=tk.E,
                                                  padx=(8, 6), pady=(0, 8))
        self.stem_field = PlaceholderEntry(
            box, "optional full mix — for playback / masking context",
            tooltip="Optional. The full mix (all instruments). When loaded, the "
                    "Play-source toggle lets you hear it, and an Analyze toggle "
                    "lets you view the Full Mix for masking context. The graph "
                    "still analyzes the drums stem by default.")
        self.stem_field.grid(row=2, column=1, sticky=tk.EW, pady=(0, 8))
        self.stem_load_btn = OutlineButton(
            box, "Load mix", accent=PURPLE_EDGE, command=self._on_stem_browse,
            tooltip="Choose the full-mix audio (optional).")
        self.stem_load_btn.grid(row=2, column=2, padx=(6, 2), pady=(0, 8))
        self.stem_clear_btn = OutlineButton(
            box, "Clear", accent=MUTED, command=self._on_stem_clear,
            tooltip="Clear the full-mix field.")
        self.stem_clear_btn.grid(row=2, column=3, padx=(2, 8), pady=(0, 8))

        # Analyze toggle: which audio the VISUAL/flags use. Default Drums (clean
        # -- the stem above); Full Mix is available for masking context. Only
        # shown when a full mix is loaded (nothing to swap otherwise).
        self._analyze_row = ttk.Frame(box, style="Spec.Panel.TFrame")
        self._analyze_row.grid(row=3, column=1, sticky=tk.W, pady=(0, 6))
        ttk.Label(self._analyze_row, text="Analyze (spectral view): ",
                  style="Spec.PanelMuted.TLabel").pack(side=tk.LEFT)
        self._analyze_seg = Segmented(self._analyze_row, ("Drums", "Full Mix"),
                                      command=lambda _i: self._on_analyze_toggle())
        self._analyze_seg.pack(side=tk.LEFT)
        Tooltip(self._analyze_seg,
                "Which audio the spectrogram + energy + flags analyze. Drums "
                "(the isolated stem) reads far cleaner than the full mix and "
                "gives more accurate flags; Full Mix shows everything for "
                "masking context. This is independent of the Play source.")
        self._analyze_row.grid_remove()   # hidden until a full mix is loaded

        self.compare_btn = PrimaryButton(
            box, "Compare", command=self._on_compare_pressed,
            tooltip="Run the spectral comparison, then overlay the detected "
                    "notes + MISS/PHANTOM flags on both views.\n(standalone "
                    "demo: generates synthetic data, no files needed)")
        self.compare_btn.grid(row=0, column=4, rowspan=3, padx=(4, 10),
                              pady=6, sticky=tk.NS)

    # ----- view bar ---------------------------------------------------------------
    def _build_viewbar(self):
        bar = tk.Frame(self, background=PANEL, highlightthickness=1,
                       highlightbackground=PURPLE_DEEP)
        bar.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(0, 6))
        inner = tk.Frame(bar, background=PANEL)
        inner.pack(fill=tk.X, padx=8, pady=5)

        self.play_btn = OutlineButton(
            inner, "\u25ba Play", accent=MAGENTA, command=self._on_play,
            tooltip="Play / pause the audio (Space) -- the drums stem by "
                    "default -- the playhead sweeps both views.")
        self.play_btn.pack(side=tk.LEFT)
        self.stop_btn = OutlineButton(
            inner, "\u25a0 Stop", accent="#e63946", command=self._on_stop,
            tooltip="Stop playback and reset the playhead (Home).")
        self.stop_btn.pack(side=tk.LEFT, padx=(6, 0))
        self.time_lbl = tk.Label(inner, text="0:00.0 / \u2013:\u2013\u2013.\u2013",
                                 background=PANEL, foreground=TIME_YELLOW,
                                 font=F_MONO)
        self.time_lbl.pack(side=tk.LEFT, padx=(10, 0))

        # Playback speed (pitch preserved by the host) -- same feel as the MIDI
        # Editor's Review Speed. Slower = easier to hear which lane a hit is in.
        self._sep(inner).pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=2)
        OutlineButton(inner, "\u2013", accent=PURPLE_EDGE,
                      command=lambda: self._speed_step(-0.10),
                      tooltip="Slow down (pitch preserved).").pack(side=tk.LEFT)
        self.speed_lbl = tk.Label(inner, text="Speed 1.00x", background=PANEL,
                                  foreground=TEXT, font=F_MONO, width=11,
                                  anchor=tk.CENTER, cursor="hand2")
        self.speed_lbl.pack(side=tk.LEFT, padx=2)
        self.speed_lbl.bind("<Button-1>", lambda _e: self._speed_reset())
        Tooltip(self.speed_lbl, "Playback speed; click to reset to 1.00x. "
                                "Pitch is preserved so it stays understandable "
                                "when slowed down.")
        OutlineButton(inner, "+", accent=PURPLE_EDGE,
                      command=lambda: self._speed_step(0.10),
                      tooltip="Speed up (pitch preserved).").pack(side=tk.LEFT,
                                                                  padx=(0, 0))

        # Play source: the Drums stem (primary) vs the optional Full Mix.
        self._sep(inner).pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=2)
        ttk.Label(inner, text="Play", style="Spec.PanelMuted.TLabel").pack(
            side=tk.LEFT, padx=(0, 4))
        self._stem_seg = Segmented(inner, ("Drums", "Full Mix"),
                                   command=lambda _i: self._on_stem_toggle())
        self._stem_seg.pack(side=tk.LEFT)
        Tooltip(self._stem_seg, "Which audio Play uses: the Drums stem or the "
                                "loaded Full Mix. Independent of the Analyze "
                                "toggle — you can play the Full Mix while the "
                                "view analyzes the Drums.")

        self._sep(inner).pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=2)
        self.view_seg = Segmented(inner, ("Per-Lane", "Spectrogram", "Waveform"),
                                  command=self._on_view_switch)
        self.view_seg.pack(side=tk.LEFT)
        Tooltip(self.view_seg, "Per-Lane: one energy strip per drum lane.\n"
                               "Spectrogram: the full frequency heatmap + a "
                               "note strip.\n"
                               "Waveform: a mirrored amplitude trace on the "
                               "same time axis — easier to read than the\n"
                               "heatmap when the audio is rough, since it shows "
                               "hit shape rather than colour intensity.\n"
                               "Left channel above the line, right below.")

        # v4.9.0 — Grid + subdivision + note-shape apply to BOTH views.
        self._sep(inner).pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=2)
        self.grid_chip = Chip(
            inner, "Grid", accent=PURPLE_EDGE, command=self._chips_changed,
            tooltip="Overlay bar / beat / subdivision timing lines (like the "
                    "MIDI editor) on both the per-lane and spectrogram views.")
        self.grid_chip.pack(side=tk.LEFT)
        self.grid_div_var = tk.StringVar(value="1/16")
        self.grid_div_combo = ttk.Combobox(
            inner, textvariable=self.grid_div_var, state="readonly", width=5,
            style="Spec.TCombobox", values=[d[0] for d in GRID_DIVS])
        self.grid_div_combo.pack(side=tk.LEFT, padx=(6, 0))
        self.grid_div_combo.bind("<<ComboboxSelected>>", self._chips_changed)
        Tooltip(self.grid_div_combo,
                "Grid subdivision: 1/4, 1/8, 1/16, 1/32 of the beat.")
        self.shape_chip = Chip(
            inner, "Bars", accent=PURPLE_EDGE, command=self._chips_changed,
            tooltip="Note shape (both views): OFF = diamonds, ON = classic thin "
                    "bars like the MIDI editor.")
        self.shape_chip.pack(side=tk.LEFT, padx=(6, 0))
        self.inst_chip = Chip(
            inner, "Instruments ▼", accent=PURPLE_LT, off_edge=PURPLE_EDGE,
            command=self._on_inst_chip,
            tooltip="Show / hide individual drums in both views, to declutter a "
                    "busy chart. Hidden lanes keep their row but drop their "
                    "notes, flags and energy.")
        self.inst_chip.pack(side=tk.LEFT, padx=(6, 0))

        self._sep(inner).pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=2)
        ttk.Label(inner, text="Zoom", style="Spec.PanelMuted.TLabel").pack(
            side=tk.LEFT)
        _zoom_sf = tk.Frame(inner, background=PANEL)
        _zoom_sf.pack(side=tk.LEFT, padx=(6, 4))
        self.zoom_scale = ttk.Scale(_zoom_sf, from_=ZOOM_MIN, to=ZOOM_MAX,
                                    orient=tk.HORIZONTAL, length=130,
                                    style="Spec.Horizontal.TScale",
                                    command=self._on_zoom_scale)
        self.zoom_scale.set(self._zoom)
        self.zoom_scale.pack(side=tk.TOP)
        _spec_tick_strip(_zoom_sf, 130, bg=PANEL)
        Tooltip(self.zoom_scale, "Time-axis zoom (Ctrl + mouse wheel on the "
                                 "canvas works too).")
        self.zoom_lbl = tk.Label(inner, text="%d px/s" % self._zoom,
                                 background=PANEL, foreground=MUTED,
                                 font=F_MONO, width=7, anchor=tk.W)
        self.zoom_lbl.pack(side=tk.LEFT)
        self.fit_btn = OutlineButton(
            inner, "Fit", accent=PURPLE_EDGE, command=self._on_zoom_fit,
            tooltip="Zoom so the whole song fits in the window.")
        self.fit_btn.pack(side=tk.LEFT, padx=(4, 0))
        # Auto Fetch Audio -- MAGENTA (matches Play) so it stands out: finds the
        # drums stem + full mix that match the loaded chart's file name.
        self.autofetch_btn = OutlineButton(
            inner, "Auto Fetch Audio", accent=MAGENTA,
            command=self._on_auto_fetch,
            tooltip="Find the drums stem + full mix that match the loaded "
                    "chart's file name and load them automatically.")
        self.autofetch_btn.pack(side=tk.LEFT, padx=(6, 0))
        # Load demo -- a short built-in synthetic comparison so a first-time user
        # can see the view working before loading their own files.
        self.demo_btn = OutlineButton(
            inner, "Load demo", accent=PURPLE_LT,
            command=self._on_load_demo,
            tooltip="Load a short built-in demo comparison to see how this view "
                    "works before loading your own song.\n\nNote: the cleaner "
                    "your drum stems, the cleaner these readings will be. Stems "
                    "with more bleed render a messier spectrogram and more "
                    "similar-looking signatures across the per-lane view. The "
                    "demo looks this clean because it is pure, even synth notes.")
        self.demo_btn.pack(side=tk.LEFT, padx=(6, 0))

        self.export_btn = OutlineButton(
            inner, "Export MIDI", accent=CYAN, command=self._on_export_midi,
            tooltip="Export a separate .mid copy of the current (edited) "
                    "notes -- leaves the loaded chart untouched.")
        self.export_btn.pack(side=tk.RIGHT)
        self.overwrite_btn = OutlineButton(
            inner, "Overwrite MIDI", accent=GREEN,
            command=self._on_overwrite_midi,
            tooltip="Write the current (edited) notes back over the loaded "
                    ".mid in place -- asks first.")
        self.overwrite_btn.pack(side=tk.RIGHT, padx=(0, 6))

    @staticmethod
    def _sep(parent):
        return tk.Frame(parent, width=1, background=SEPARATOR)

    # ----- options rows (one per view) --------------------------------------
    def _build_options(self):
        self._opt_box = tk.Frame(self, background=BG)
        self._opt_box.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(0, 6))
        self._opt_box.columnconfigure(0, weight=1)
        self._opt_lane = tk.Frame(self._opt_box, background=PANEL,
                                  highlightthickness=1,
                                  highlightbackground=PURPLE_DEEP)
        self._opt_gram = tk.Frame(self._opt_box, background=PANEL,
                                  highlightthickness=1,
                                  highlightbackground=PURPLE_DEEP)
        for f in (self._opt_lane, self._opt_gram):
            f.grid(row=0, column=0, sticky=tk.EW)
        self._fill_lane_options(self._opt_lane)
        self._fill_gram_options(self._opt_gram)
        self._opt_lane.tkraise()

    def _fill_lane_options(self, page):
        row = tk.Frame(page, background=PANEL)
        row.pack(fill=tk.X, padx=8, pady=5)
        changed = self._chips_changed
        ttk.Label(row, text="Chart", style="Spec.PanelMuted.TLabel").pack(
            side=tk.LEFT, padx=(0, 5))
        self.edit_chip = Chip(
            row, "Edit", accent=GREEN, command=changed,
            tooltip="Edit mode: click a lane to add a note, click a note to "
                    "remove it (Ctrl+Z undo / Ctrl+Y redo). Turns Notes on "
                    "if needed.")
        self.edit_chip.pack(side=tk.LEFT)
        self.snap_chip = Chip(
            row, "Snap 1/16", accent=GREEN, on=True, command=changed,
            tooltip="Snap added notes to the nearest 1/16 of the beat grid. "
                    "Turn off for free placement.")
        self.snap_chip.pack(side=tk.LEFT, padx=(6, 0))
        self.undo_btn = OutlineButton(
            row, "Undo", accent=GREEN, command=self._on_undo,
            tooltip="Undo the last chart edit (Ctrl+Z).", enabled=False)
        self.undo_btn.pack(side=tk.LEFT, padx=(10, 0))
        self.redo_btn = OutlineButton(
            row, "Redo", accent=GREEN, command=self._on_redo,
            tooltip="Redo (Ctrl+Y).", enabled=False)
        self.redo_btn.pack(side=tk.LEFT, padx=(4, 0))
        self._sep(row).pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=2)

        ttk.Label(row, text="Show", style="Spec.PanelMuted.TLabel").pack(
            side=tk.LEFT, padx=(0, 5))
        self.energy_chip = Chip(row, "Energy", on=True, command=changed,
                                tooltip="Each lane's band-energy ribbon.")
        self.ticks_chip = Chip(row, "Onset ticks", on=True, command=changed,
                               tooltip="Detected onset attacks (spectral-flux "
                                       "peaks) as ticks.")
        self.raw_chip = Chip(row, "Per-lane raw", on=True, command=changed,
                             tooltip="ON: each lane gets its own frequency "
                                     "slice. OFF: cymbal/tom lanes share "
                                     "their physical band.")
        self.notes_chip = Chip(row, "Notes", on=True, command=changed,
                               tooltip="The chart's notes as lane-colored "
                                       "diamonds (or bars — see the Bars "
                                       "toggle in the toolbar).\n\n"
                                       "Locked on while Edit is enabled: you "
                                       "cannot click notes you cannot see. "
                                       "Turn Edit off to hide them.")
        self.flash_chip = Chip(row, "Flash", on=True, command=changed,
                               tooltip="Notes flash white as the playhead "
                                       "passes (timing check).")
        self.flags_chip = Chip(row, "Flag issues", on=True, command=changed,
                               tooltip="The disagreements: green + MISS / "
                                       "orange \u00d7 PHANTOM.")
        # (Grid + subdivision + note-shape live in the main toolbar now \u2014 v4.9.0
        # \u2014 so they apply to BOTH views.)
        self.ghost_chip = Chip(row, "Ghost", command=changed,
                               tooltip="Fade the energy strips behind the "
                                       "notes (MIDI-editor overlay sim).")
        for chip in (self.energy_chip, self.ticks_chip, self.raw_chip,
                     self.notes_chip, self.flash_chip, self.flags_chip,
                     self.ghost_chip):
            chip.pack(side=tk.LEFT, padx=(6, 0))
        _ghost_sf = tk.Frame(row, background=PANEL)
        _ghost_sf.pack(side=tk.LEFT, padx=(3, 0))
        self.ghost_scale = ttk.Scale(_ghost_sf, from_=0, to=70,
                                     orient=tk.HORIZONTAL,
                                     length=56, style="Spec.Horizontal.TScale",
                                     command=self._on_ghost_scale)
        self.ghost_scale.set(30)
        self.ghost_scale.pack(side=tk.TOP)
        _spec_tick_strip(_ghost_sf, 56, bg=PANEL)
        Tooltip(self.ghost_scale, "Ghost fade amount (strip opacity).")
        ttk.Label(row, text="Note size", style="Spec.PanelMuted.TLabel").pack(
            side=tk.LEFT, padx=(12, 3))
        _nsz_sf = tk.Frame(row, background=PANEL)
        _nsz_sf.pack(side=tk.LEFT)
        self.note_size_scale = ttk.Scale(_nsz_sf, from_=60, to=180,
                                         orient=tk.HORIZONTAL, length=64,
                                         style="Spec.Horizontal.TScale",
                                         command=self._on_note_size_scale)
        self.note_size_scale.set(100)
        self.note_size_scale.pack(side=tk.TOP)
        _spec_tick_strip(_nsz_sf, 64, bg=PANEL)

    def _fill_gram_options(self, page):
        row = tk.Frame(page, background=PANEL)
        row.pack(fill=tk.X, padx=8, pady=5)
        changed = self._chips_changed
        ttk.Label(row, text="Show", style="Spec.PanelMuted.TLabel").pack(
            side=tk.LEFT, padx=(0, 5))
        self.bands_chip = Chip(row, "Drum bands", on=True, command=changed,
                               tooltip="The four drum-band guide lines + "
                                       "labels on the spectrogram.")
        self.g_notes_chip = Chip(row, "Notes", on=True, command=changed,
                                 tooltip="The chart's notes overlaid on the "
                                         "note-row strip.")
        self.g_flash_chip = Chip(row, "Flash", on=True, command=changed,
                                 tooltip="Notes flash white as the playhead "
                                         "crosses them.")
        self.g_colors_chip = Chip(row, "Chart colors", on=True, command=changed,
                                  tooltip="Color note markers by LANE (on) "
                                          "or by drum BAND (off).")
        self.g_flags_chip = Chip(row, "Flag issues", on=True, command=changed,
                                 tooltip="The disagreements: green + MISS / "
                                         "orange \u00d7 PHANTOM.")
        self.hz_chip = Chip(row, "Hz readout", command=changed,
                            tooltip="Per-band Hz ranges in the axis + a "
                                    "cursor-follow frequency readout.")
        for chip in (self.bands_chip, self.g_notes_chip, self.g_flash_chip,
                     self.g_colors_chip, self.g_flags_chip, self.hz_chip):
            chip.pack(side=tk.LEFT, padx=(6, 0))
        self._sep(row).pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=2)
        self.render_chip = Chip(row, "Render \u25bc", accent=PURPLE_LT,
                                off_edge=PURPLE_EDGE,
                                command=self._on_render_chip,
                                tooltip="Spectrogram render controls: "
                                        "brightness, top frequency, frequency "
                                        "scale, colormap.")
        self.render_chip.pack(side=tk.LEFT)

    # ----- advanced render panel ---------------------------------------------------
    def _build_advanced(self):
        self.adv_panel = tk.Frame(self, background=DARKER,
                                  highlightthickness=1,
                                  highlightbackground=PURPLE_DEEP)
        row = tk.Frame(self.adv_panel, background=DARKER)
        row.pack(fill=tk.X, padx=10, pady=5)
        ttk.Label(row, text="Brightness",
                  style="Spec.Muted.TLabel").pack(side=tk.LEFT)
        _bright_sf = tk.Frame(row, background=DARKER)
        _bright_sf.pack(side=tk.LEFT, padx=(6, 14))
        self.bright_scale = ttk.Scale(_bright_sf, from_=40, to=240,
                                      orient=tk.HORIZONTAL, length=110,
                                      style="Spec.Horizontal.TScale",
                                      command=self._on_brightness_scale)
        self.bright_scale.set(115)
        self.bright_scale.pack(side=tk.TOP)
        _spec_tick_strip(_bright_sf, 110, bg=DARKER)
        Tooltip(self.bright_scale, "Spectrogram brightness (0.4x - 2.4x) -- "
                                   "lifts quiet content toward the hot end "
                                   "of the colormap.")

        ttk.Label(row, text="Top freq", style="Spec.Muted.TLabel").pack(
            side=tk.LEFT)
        self.fmax_combo = ttk.Combobox(row, state="readonly", width=7,
                                       style="Spec.TCombobox",
                                       values=("8000", "16000", "22050"))
        self.fmax_combo.current(1)
        self.fmax_combo.pack(side=tk.LEFT, padx=(6, 14))
        self.fmax_combo.bind("<<ComboboxSelected>>", self._on_adv_combo)
        Tooltip(self.fmax_combo, "Top of the frequency axis. Lower it to zoom "
                                 "the drum range.")

        ttk.Label(row, text="Freq scale", style="Spec.Muted.TLabel").pack(
            side=tk.LEFT)
        self.scale_combo = ttk.Combobox(row, state="readonly", width=12,
                                        style="Spec.TCombobox",
                                        values=("log (musical)", "linear"))
        self.scale_combo.current(0)
        self.scale_combo.pack(side=tk.LEFT, padx=(6, 14))
        self.scale_combo.bind("<<ComboboxSelected>>", self._on_adv_combo)
        Tooltip(self.scale_combo, "log reads musically (each octave the same "
                                  "height); linear is laboratory-style.")

        ttk.Label(row, text="Colormap", style="Spec.Muted.TLabel").pack(
            side=tk.LEFT)
        self.cmap_combo = ttk.Combobox(row, state="readonly", width=14,
                                       style="Spec.TCombobox",
                                       values=("magma (ParaKit)", "cyan ice",
                                               "grayscale"))
        self.cmap_combo.current(0)
        self.cmap_combo.pack(side=tk.LEFT, padx=(6, 0))
        self.cmap_combo.bind("<<ComboboxSelected>>", self._on_adv_combo)

    # ----- canvas ----------------------------------------------------------------------
    def _build_canvas(self):
        self.canvas_box = tk.Frame(self, background=BG)
        self.canvas_box.pack(side=tk.TOP, fill=tk.BOTH, expand=True,
                             padx=10, pady=(0, 6))
        self.canvas_box.columnconfigure(0, weight=1)
        self.canvas_box.rowconfigure(0, weight=1)

        self._lane_page = tk.Frame(self.canvas_box, background=BG)
        self._lane_gutter = tk.Frame(self._lane_page, width=GUTTER_W,
                                     background=PANEL, highlightthickness=1,
                                     highlightbackground=SEPARATOR)
        self._lane_gutter.pack(side=tk.LEFT, fill=tk.Y)
        self._lane_gutter.pack_propagate(False)
        self._fill_gutter(LANE_ROW_H)
        # A distinct 4px divider between the note boxes and their lanes (owner
        # 2026-07-20 -- the gutter used to blend into the canvas). PURPLE_LT is
        # the bright accent so it reads clearly against every dark lane colour.
        tk.Frame(self._lane_page, width=4, background=PURPLE_LT).pack(
            side=tk.LEFT, fill=tk.Y)
        self.lane_view = LaneViewCanvas(
            self._lane_page, on_seek=self._on_seek,
            on_edit_click=self._on_lane_edit, on_zoom=self._on_wheel_zoom,
            on_row_h=self._fill_gutter)
        self.lane_view.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._gram_page = tk.Frame(self.canvas_box, background=BG)
        self.gram_view = GramView(self._gram_page, on_seek=self._on_seek,
                                  on_zoom=self._on_wheel_zoom)
        self.gram_view.pack(fill=tk.BOTH, expand=True)

        for page in (self._lane_page, self._gram_page):
            page.grid(row=0, column=0, sticky=tk.NSEW)
        self._lane_page.tkraise()

    @staticmethod
    def _outlined_text(parent, text, font=F_MONO, fill="#0a0a0a",
                       outline="#ffffff", bg=PANEL):
        """A small Canvas showing `text` in `fill` with a 1px `outline` halo --
        the GitHub-graph number look, which plain tk labels can't do. Sized to
        the text so it packs like a label."""
        import tkinter.font as tkfont
        f = tkfont.Font(font=font)
        w = f.measure(text) + 4
        h = f.metrics("linespace") + 2
        c = tk.Canvas(parent, width=w, height=h, background=bg,
                      highlightthickness=0, bd=0)
        cx, cy = w / 2.0, h / 2.0
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx or dy:
                    c.create_text(cx + dx, cy + dy, text=text, font=font,
                                  fill=outline)
        c.create_text(cx, cy, text=text, font=font, fill=fill)
        return c

    def _fill_gutter(self, row_h):
        gutter = self._lane_gutter
        for w in gutter.winfo_children():
            w.destroy()
        tk.Frame(gutter, height=RULER_H, background=DARKER).pack(
            side=tk.TOP, fill=tk.X)
        top_pad = max(2, int(row_h * 0.12))
        # "Per-lane raw" ON -> cymbal/tom lanes read their OWN frequency slice,
        # not the shared physical band, so a "(shared)" band label reads "(own)"
        # (owner 2026-07-20). Snare/Kick are always their own band and never
        # change. Remembered so _apply_lane_options can re-fill on a toggle flip.
        raw_on = self.raw_chip.get() if hasattr(self, "raw_chip") else True
        self._gutter_raw_state = raw_on
        self._gutter_row_h = row_h
        for idx, name, color, band, slice_lbl, band_lbl in LANES:
            # Per-lane TINTED background so each box reads as its drum's colour,
            # not grey (2026-07-20 owner request); the lane NAME + MIDI number
            # sit in the lane colour on top of the tint. `row_bg` is propagated
            # to EVERY child so no grey PANEL patches show through.
            name_col = readable_on_dark(color)
            row_bg = blend(PANEL, color, 0.20)
            rowf = tk.Frame(gutter, height=max(1, int(round(row_h))),
                            background=row_bg)
            rowf.pack(side=tk.TOP, fill=tk.X)
            rowf.pack_propagate(False)
            # Section DIVIDER (2026-07-20 owner request): a clear 1px line at the
            # BOTTOM of each box, INSIDE its fixed height so the gutter stays
            # row-aligned with the spectral canvas. A soft tint of the lane
            # colour so each divider also hints at the lane above it.
            tk.Frame(rowf, height=1, background=blend(SEPARATOR, color, 0.45)
                     ).pack(side=tk.BOTTOM, fill=tk.X)
            # Row 1: colour swatch + lane name. The name is OUTLINED text
            # (2026-07-20 owner request): the bright lane colour with a dark
            # halo, so it stays crisp and legible against the tinted row.
            head = tk.Frame(rowf, background=row_bg)
            head.pack(side=tk.TOP, anchor=tk.W, padx=8, pady=(top_pad, 0))
            tk.Frame(head, width=10, height=10,
                     background=color).pack(side=tk.LEFT)
            tk.Frame(head, width=5, height=1, background=row_bg).pack(side=tk.LEFT)
            self._outlined_text(head, name, font=F_BOLD, fill=name_col,
                                outline=OUTLINE_DARK, bg=row_bg).pack(side=tk.LEFT)
            # Row 2: MIDI note number in the LANE's colour + Hz range as an
            # outlined (black-on-white-halo) chip.
            if row_h >= 34:
                info = tk.Frame(rowf, background=row_bg)
                info.pack(side=tk.TOP, anchor=tk.W, padx=8, pady=(1, 0))
                midi = LANE_MIDI_OUT[idx] if idx < len(LANE_MIDI_OUT) else "-"
                # MIDI number is also OUTLINED (lane colour + dark halo) to
                # match the name and stand out on the tint.
                self._outlined_text(info, "MIDI %s" % midi, font=F_MONO_SMALL_B,
                                    fill=color, outline=OUTLINE_DARK,
                                    bg=row_bg).pack(side=tk.LEFT)
                tk.Frame(info, width=6, height=1, background=row_bg).pack(
                    side=tk.LEFT)
                hz = slice_lbl.split("(")[0].strip()   # "8k-16k Hz"
                self._outlined_text(info, hz, bg=row_bg).pack(side=tk.LEFT)
            if row_h >= 58:
                # Band label dimmed toward the lane colour (a soft tint, still
                # clearly secondary to the name).
                _blbl = (band_lbl.replace("(shared)", "(own)")
                         if raw_on else band_lbl)
                tk.Label(rowf, text=_blbl, background=row_bg,
                         foreground=blend(MUTED, name_col, 0.55), font=F_SMALL,
                         anchor=tk.W).pack(side=tk.TOP, fill=tk.X, padx=8)
            Tooltip(rowf, "%s\nMIDI note %s -- raw slice %s"
                    % (band_lbl, LANE_MIDI_OUT[idx] if idx < len(LANE_MIDI_OUT)
                       else "?", slice_lbl))

    # ----- footer -------------------------------------------------------------------------
    def _build_footer(self):
        bar = tk.Frame(self, background=BG)
        bar.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(0, 8))
        legend = tk.Frame(bar, background=BG)
        legend.pack(side=tk.LEFT)

        def piece(text, color):
            tk.Label(legend, text=text, background=BG, foreground=color,
                     font=F_SMALL).pack(side=tk.LEFT)
        piece("\u2666", PURPLE_LT)
        piece(" = charted note    ", MUTED)
        piece("+", MISS_GREEN)
        piece(" = MISS (audio hit, no note)    ", MUTED)
        piece("\u00d7", PHANTOM_ORANGE)
        piece(" = PHANTOM (note, no audio)    ", MUTED)
        piece("energy alone is not a miss -- click the canvas to seek",
              "#5d5d78")
        self.status_lbl = tk.Label(bar, text="Ready.", background=BG,
                                   foreground=CYAN, font=F_SMALL, anchor=tk.E)
        self.status_lbl.pack(side=tk.RIGHT)

    # =========================================================================
    # Behaviour
    # =========================================================================
    # ----- status / readouts --------------------------------------------------
    def _status(self, text, color=CYAN):
        try:
            self.status_lbl.configure(text=text, foreground=color)
        except tk.TclError:
            pass   # destroyed/mid-teardown tab (breaker R5B2-2, 2026-07-20)

    def _update_readout(self):
        if self._model is None:
            self.notes_lbl.configure(text="\u2014 notes", foreground=MUTED)
            self.miss_lbl.configure(text="0 MISS", foreground=MUTED)
            self.phantom_lbl.configure(text="0 PHANTOM", foreground=MUTED)
            return
        n_miss = sum(1 for i in self._model.issues if i["type"] == "miss")
        n_phantom = sum(1 for i in self._model.issues
                        if i["type"] == "phantom")
        self.notes_lbl.configure(text="%d notes" % len(self._model.notes),
                                 foreground=CYAN)
        self.miss_lbl.configure(text="%d MISS" % n_miss,
                                foreground=MISS_GREEN if n_miss else MUTED)
        self.phantom_lbl.configure(text="%d PHANTOM" % n_phantom,
                                   foreground=PHANTOM_ORANGE if n_phantom
                                   else MUTED)

    def _update_transport(self):
        has = self._model is not None
        self.play_btn.set_enabled(has and not self._comparing)
        self.stop_btn.set_enabled(has)
        self.undo_btn.set_enabled(has and bool(self._undo_stack))
        self.redo_btn.set_enabled(has and bool(self._redo_stack))
        # Overwrite writes IN PLACE, so it must look dead whenever nothing is armed.
        # Disarming only cleared _overwrite_target and left the button live; pressing
        # it then fell into the "not a .mid file" branch, which blames the file type
        # for what was actually a deliberate disarm. Centralised here because the arm
        # (_finish_compare) and the synthetic-path clear both already refresh the
        # transport -- external_chart_changed is the one caller that has to ask.
        # getattr: _update_transport can run before the button row is built.
        _ob = getattr(self, "overwrite_btn", None)
        if _ob is not None:
            _ob.set_enabled(bool(self._overwrite_target))

    # ----- sources -------------------------------------------------------------
    def _load_persisted_paths(self):
        ref = self._cfg_get("spec_last_reference", "")
        cand = self._cfg_get("spec_last_candidate", "")
        if ref and os.path.isfile(ref):
            self.reference_field.set(ref)
        if cand and os.path.isfile(cand):
            self.candidate_field.set(cand)

    def _on_ref_browse(self):
        initial = ""
        if self.hooks:
            initial = self._cfg_get("spec_last_reference", "")
        path = filedialog.askopenfilename(
            parent=self, title="Select the drums-only stem",
            initialdir=os.path.dirname(initial) if initial else "",
            filetypes=(("Audio files", "*.ogg *.mp3 *.wav *.flac *.m4a *.aac"),
                       ("All files", "*.*")))
        if path:
            self.reference_field.set(path)
            # status BEFORE _cfg_set (breaker R2B2-5, 2026-07-20): a raising
            # set_cfg posts "config save failed" — writing our info line
            # AFTER it made that contract-mandated note invisible.
            self._status("Drums stem: %s" % os.path.basename(path))
            self._cfg_set("spec_last_reference", path)

    def _on_ref_clear(self):
        self.reference_field.clear()
        self._on_stop()
        self._status("Drums stem cleared.")

    def _on_cand_browse(self):
        initial = ""
        if self.hooks:
            initial = self._cfg_get("spec_last_candidate", "")
        path = filedialog.askopenfilename(
            parent=self, title="Select the candidate chart",
            initialdir=os.path.dirname(initial) if initial else "",
            filetypes=(("Chart files", "*.mid *.midi *.json *.rlrr"),
                       ("All files", "*.*")))
        if path:
            self.candidate_field.set(path)
            self._status("Chart: %s" % os.path.basename(path))
            self._cfg_set("spec_last_candidate", path)   # last: see R2B2-5

    def _on_cand_clear(self):
        self.candidate_field.clear()
        self._status("Chart cleared.")

    def _on_stem_browse(self):
        initial = ""
        if self.hooks:
            initial = (self._cfg_get("spec_last_stem", "")
                       or self._cfg_get("spec_last_reference", ""))
        path = filedialog.askopenfilename(
            parent=self, title="Select the full mix (optional)",
            initialdir=os.path.dirname(initial) if initial else "",
            filetypes=(("Audio files", "*.ogg *.mp3 *.wav *.flac *.m4a *.aac"),
                       ("All files", "*.*")))
        if path:
            _prev_src = self._play_source()      # R7E-2: rebuild only on change
            self.stem_field.set(path)
            if getattr(self, "_stem_seg", None) is not None:
                # Auto-select the just-loaded Full Mix for PLAYBACK so you can
                # confirm it loaded (ANALYSIS still stays on the drums). fire=
                # False (breaker R6E-2, 2026-07-20): Segmented.set fires its
                # command on a CHANGED value and _on_stem_toggle rebuilds the
                # stream — one click would restart playback twice. The explicit
                # rebuild below must stay: it runs AFTER the info line, which is
                # what keeps a failed rebuild's AMBER warning visible (R3E-3a).
                self._stem_seg.set(1, fire=False)   # 1 = Full Mix
            self._sync_analyze_toggle()        # reveal the Analyze toggle
            # info line BEFORE the rebuild (breaker R3E-3a): a failed rebuild's
            # AMBER warning must survive; cfg_set stays last (R2B2-5).
            self._status("Full mix loaded (optional) — now playing the mix; "
                         "the graph still analyzes the drums.")
            self._rebuild_if_source_changed(_prev_src)
            self._cfg_set("spec_last_stem", path)

    def _on_stem_clear(self):
        _prev_src = self._play_source()         # R7E-2: rebuild only on change
        self.stem_field.clear()
        if getattr(self, "_stem_seg", None) is not None:
            self._stem_seg.set(0, fire=False)  # fall back to the Drums (R6E-2)
        if getattr(self, "_analyze_seg", None) is not None:
            self._analyze_seg.set(0, fire=False)   # analysis back to Drums
        self._sync_analyze_toggle()            # hide the Analyze toggle
        # info line BEFORE the rebuild (breaker R3E-3a, 2026-07-20): a failed
        # rebuild posts an AMBER warning that must not be clobbered by this.
        self._status("Full mix cleared — playback and view use the drums.")
        self._rebuild_if_source_changed(_prev_src)

    def _on_auto_fetch(self):
        """Auto Fetch Audio: from the loaded chart's file name, find and load
        the matching drums stem (into Drums stem) + full mix (into Full mix) via
        the host's audio-search hook. Needs the app (no-op in standalone)."""
        chart = self.candidate_field.get()
        if not chart:
            self._status("Load a chart first — Auto Fetch finds the matching "
                         "drums + full mix from its file name.", AMBER)
            return
        if "auto_fetch_audio" not in self.hooks:
            self._status("Auto Fetch needs the app's audio library "
                         "(not available in standalone mode).", AMBER)
            return
        ok, result = self._hook_call("auto_fetch_audio", chart)
        if not ok:
            return   # _hook_call already posted the failure to the status line
        # Normalize any hook shape: a documented {drums,mix} dict or an exact
        # 2-sequence; every other shape (scalar / 1- or 3-item) is "no result"
        # instead of an unpack crash (breaker fix F6, 2026-07-21).
        if isinstance(result, dict):
            drums, mix = result.get("drums"), result.get("mix")
        elif isinstance(result, (list, tuple)) and len(result) == 2:
            drums, mix = result
        else:
            drums, mix = None, None
        # A well-shaped result can still carry non-str path values (e.g.
        # {"drums": 123}) -> os.path.isfile() below would raise (review #6,
        # 2026-07-21). Only a real string is a usable path.
        drums = drums if isinstance(drums, str) else None
        mix = mix if isinstance(mix, str) else None
        got = False
        if drums and os.path.isfile(drums):
            self.reference_field.set(drums)
            got = True
        if mix and os.path.isfile(mix):
            self.stem_field.set(mix)
            self._sync_analyze_toggle()   # reveal the Analyze toggle
        if got:
            self._status("Auto-fetched audio — press Compare.")
        else:
            self._status("No matching drums stem found for this chart.", AMBER)

    # ----- compare -------------------------------------------------------------
    def _analysis_source(self):
        """Which AUDIO the spectral analysis + heatmap use (independent of what
        plays). Defaults to the DRUMS STEM (the primary field) -- a full-mix
        spectrogram is dense, patternless noise, and mix bleed inflates false
        MISS; the isolated drums read cleanly. Only when a full mix is loaded
        AND 'Analyze: Full Mix' is selected do we fall back to it for masking
        context."""
        mix = self.stem_field.get() if hasattr(self, "stem_field") else ""
        want_mix = (getattr(self, "_analyze_seg", None) is not None
                    and self._analyze_seg.get() == 1)   # 1 = Full Mix
        if want_mix and mix and os.path.isfile(mix):
            return mix
        return self.reference_field.get()               # 0 = Drums (default)

    def _sync_analyze_toggle(self):
        """Show the Analyze Drums|Full Mix toggle only when a full mix is loaded
        (there is nothing to swap otherwise)."""
        row = getattr(self, "_analyze_row", None)
        if row is None:
            return
        has_mix = bool(self.stem_field.get()
                       and os.path.isfile(self.stem_field.get()))
        if has_mix:
            row.grid()
        else:
            row.grid_remove()

    def _on_analyze_toggle(self):
        # Re-run the comparison on the newly chosen analysis source.
        if self._comparing:
            return
        if self._model is not None or (self.reference_field.get()
                                       and self.candidate_field.get()):
            src = "Drums" if self._analyze_seg.get() == 0 else "Full Mix"
            self._status("Re-analyzing the %s…" % src)
            self._on_compare()

    def _on_compare_pressed(self):
        """The Compare BUTTON: set the Analyze view default before running
        (owner rule 2026-07-20). Analyze DRUMS whenever a drums stem is loaded
        (both loaded, or drums only); Analyze the FULL MIX only when the full mix
        is the ONLY audio loaded (no drums stem). Manual Analyze-toggle
        re-compares call _on_compare directly and keep the user's choice."""
        ref = self.reference_field.get()
        mix = self.stem_field.get() if hasattr(self, "stem_field") else ""
        has_drums = bool(ref and os.path.isfile(ref))
        has_mix = bool(mix and os.path.isfile(mix))
        if getattr(self, "_analyze_seg", None) is not None:
            # 1 = Full Mix, 0 = Drums. Full Mix only when it is the sole audio.
            want = 1 if (has_mix and not has_drums) else 0
            self._analyze_seg.set(want, fire=False)
        self._on_compare()

    def _on_compare(self):
        if self._comparing:
            return
        src = self._analysis_source()          # drums stem or mix
        cand = self.candidate_field.get()
        if self.hooks:
            if not src or not os.path.isfile(src):
                self._status("Drums stem not found: choose a drums-only audio "
                             "file.", AMBER)
                return
            if not cand or not os.path.isfile(cand):
                self._status("Chart not found: choose a .mid/.json/.rlrr file.",
                             AMBER)
                return
            if "decode_audio" not in self.hooks:
                self._status("decode_audio hook missing -- falling back to "
                             "synthetic demo.", AMBER)
                self._run_synthetic_compare()
                return
            self._start_real_compare(src, cand)
            return
        self._run_synthetic_compare()

    def _run_synthetic_compare(self):
        """Standalone / fallback path: deterministic mock data + fake latency."""
        self._comparing = True
        self.compare_btn.set_text("Comparing\u2026"); self.compare_btn.set_enabled(False)
        if not self.reference_field.get() or not self.candidate_field.get():
            self._status("No files chosen -- building the synthetic demo "
                         "comparison\u2026")
        else:
            self._status("Analyzing (mock engine)\u2026")
        self._update_transport()
        # tracked so destroy() can cancel it (breaker R2E-2, 2026-07-20)
        self._synth_job = self.after(650, self._finish_compare)

    def _on_load_demo(self):
        """Load the built-in demo comparison: force the synthetic
        MockSpectralModel path (the real engine needs real audio + a chart), so
        a first-time user can see the spectrogram / per-lane view / flags
        working before loading their own files. Reuses the tested
        _finish_compare path; the disclaimer lives on the button's tooltip."""
        if self._comparing:
            return
        self._comparing = True
        self.compare_btn.set_text("Comparing…")
        self.compare_btn.set_enabled(False)
        self._status("Loading the built-in demo comparison…")
        self._update_transport()
        # Tracked so destroy() can cancel it (same as _run_synthetic_compare).
        self._synth_job = self.after(400, self._finish_compare)

    def _start_real_compare(self, ref: str, cand: str):
        """Real engine pipeline: decode -> spectral -> chart -> issues. Runs in
        a worker thread; results are marshalled back via a queue polled with
        after(). The Compare button is disabled until the run finishes; a
        second Compare while busy is ignored."""
        self._comparing = True
        self.compare_btn.set_text("Comparing\u2026"); self.compare_btn.set_enabled(False)
        self._status("Analyzing\u2026")
        self._update_transport()
        self._compare_queue = queue.Queue()
        self._compare_thread = threading.Thread(
            target=self._compare_worker,
            args=(self.hooks["decode_audio"], ref, cand, self._compare_queue),
            daemon=True)
        self._compare_thread.start()
        self._poll_compare_job = self.after(100, self._poll_compare)

    @staticmethod
    def _compare_worker(decode_audio, ref, cand, q):
        """No widget access. All heavy lifting (librosa/numpy/mido) happens
        here; the result dict is queued for the main thread."""
        try:
            import parakit_spectral_engine as eng
            samples, sr = decode_audio(ref)
            spec = eng.compute_spectral(samples, sr)
            notes = eng.load_chart_notes(cand)
            bpm = eng.chart_bpm(cand)
            issues = eng.find_issues(spec, notes)
            # Waveform-view envelope. Built HERE because this is the worker
            # thread where the heavy lifting belongs, and because `ref` (the
            # audio path) is in scope only at this point.
            #
            # `decode_audio` is left strictly alone. Its contract is
            # mono (`librosa.load(..., mono=True)`), the two existing views and
            # every issue/onset number downstream derive from that exact array,
            # and widening it to stereo would push the channel mixdown into
            # compute_spectral -- arithmetically equivalent, but not
            # bit-for-bit, which is enough to shift a threshold comparison. So
            # the envelope does its own separate stereo read instead of
            # reshaping the analysis path. Measured cost of that read on a
            # 581 s song: ~0.6 s, and stereo decoding is if anything slightly
            # CHEAPER than mono because librosa skips the mixdown.
            wave_env = _build_wave_env(ref, samples, sr)
            q.put({
                "ok": {
                    "spec": spec,
                    "notes": notes,
                    "issues": issues,
                    "bpm": bpm,
                    "ref": ref,
                    "cand": cand,
                    "wave_env": wave_env,
                }
            })
        except Exception as e:
            q.put({"error": str(e)})

    def _poll_compare(self):
        if self._compare_queue is None:
            self._comparing = False
            self.compare_btn.set_text("Compare"); self.compare_btn.set_enabled(True)
            self._update_transport()
            return
        try:
            msg = self._compare_queue.get_nowait()
        except queue.Empty:
            self._poll_compare_job = self.after(100, self._poll_compare)
            return
        self._poll_compare_job = None
        self._comparing = False
        self.compare_btn.set_text("Compare"); self.compare_btn.set_enabled(True)
        self._update_transport()
        if "error" in msg:
            self._status("Compare failed: %s" % msg["error"], AMBER)
            return
        self._finish_real_compare(msg["ok"])

    def _finish_real_compare(self, data: dict):
        self._on_stop()
        spec = data["spec"]
        notes = data["notes"]
        issues = data["issues"]
        bpm = data["bpm"] or 120.0
        # Reject a degenerate decode BEFORE installing the model (breaker
        # B2-1/EDGE-1, 2026-07-20): dur<=0 meant 0 decoded samples — rendering
        # it crashed, and every readout would be confidently meaningless.
        try:
            _dur = float(spec.get("dur", 0.0) or 0.0)
        except (TypeError, ValueError):
            _dur = 0.0
        # NaN/inf fail this positively-phrased range check (breaker R2B2-2:
        # `<= 0` let NaN through to a main-thread crash in the renderer).
        if not (0.0 < _dur < float("inf")):
            self._status("Compare failed: the audio decoded to no usable "
                         "duration -- is the file empty or corrupt?", AMBER)
            return
        # .get(), not ["wave_env"]: a result dict queued by an older/other code
        # path (or a hand-built one in a test) has no such key, and a missing
        # waveform must not break a Compare the other two views can serve.
        self._model = _SpectralModel(spec, notes, issues, bpm,
                                     wave_env=data.get("wave_env"))
        self._undo_stack.clear()
        self._redo_stack.clear()
        cand = data["cand"]
        self._overwrite_target = (
            cand if os.path.splitext(cand)[1].lower() in (".mid", ".midi")
            else "")
        self.lane_view.set_model(self._model)
        self.gram_view.set_model(self._model)
        self._apply_lane_options()
        self._apply_gram_options()
        self._apply_render_params()
        self._update_readout()
        self._update_transport()
        self._update_time_label()
        n_miss = sum(1 for i in self._model.issues if i["type"] == "miss")
        n_phantom = sum(1 for i in self._model.issues
                        if i["type"] == "phantom")
        if spec.get("silent"):
            # Surface the engine's silence verdict (breaker B2-2, 2026-07-20):
            # a silent/near-silent decode used to report a confident compare.
            self._status("Compared: %d notes, %d MISS + %d PHANTOM over %.1fs "
                         "-- WARNING: the audio is silent/near-silent; "
                         "check the file or stem."
                         % (len(self._model.notes), n_miss, n_phantom,
                            self._model.dur), AMBER)
        else:
            self._status("Compared: %d notes, %d MISS + %d PHANTOM over %.1fs"
                         % (len(self._model.notes), n_miss, n_phantom,
                            self._model.dur))

    def _finish_compare(self):
        self._synth_job = None
        self._on_stop()
        self._model = MockSpectralModel()
        self._undo_stack.clear()
        self._redo_stack.clear()
        # NEVER arm Overwrite MIDI from the SYNTHETIC path (breaker R3B2-3,
        # 2026-07-20): these are demo notes — pointing the overwrite at the
        # user's real .mid would replace their chart with mock data after one
        # generic confirm.
        self._overwrite_target = ""
        self.lane_view.set_model(self._model)
        self.gram_view.set_model(self._model)
        self._apply_lane_options()
        self._apply_gram_options()
        self._apply_render_params()
        self._comparing = False
        self.compare_btn.set_text("Compare"); self.compare_btn.set_enabled(True)
        self._update_readout()
        self._update_transport()
        self._update_time_label()
        n_miss = sum(1 for i in self._model.issues if i["type"] == "miss")
        n_phantom = sum(1 for i in self._model.issues
                        if i["type"] == "phantom")
        self._status("Compared: %d notes, %d MISS + %d PHANTOM over %.0fs "
                     "(synthetic demo data)"
                     % (len(self._model.notes), n_miss, n_phantom,
                        self._model.dur))

    # ----- view switching ------------------------------------------------------------
    def _on_view_switch(self, index):
        # index 1 = Spectrogram, 2 = Waveform. Both are GramView render styles
        # sharing one page, so `>= 1` raises the same page for either and only
        # the style differs -- that is what keeps the ruler, playhead, note
        # strip, scroll-sync and zoom as one implementation.
        # set_render_params, NOT a direct poke at render["style"].
        # `_draw_static` only re-PLACES the cached `self._photo`; nothing in the
        # redraw path rebuilds it. So assigning the style and calling
        # request_redraw() left the previous image on screen and the view
        # silently never changed — caught by a render-hash check, because to the
        # eye "the spectrogram is still there" looks like a switch that did
        # nothing rather than like a bug. set_render_params is the API that
        # rebuilds on change.
        self.gram_view.set_render_params(style=("wave" if index == 2 else "gram"))
        (self._gram_page if index >= 1 else self._lane_page).tkraise()
        (self._opt_gram if index >= 1 else self._opt_lane).tkraise()
        # The advanced panel (colormap / brightness / fmax / log) is
        # spectrogram-only, and _sync_adv_panel's `== 1` keeps it hidden on the
        # Waveform view on purpose: those four controls do nothing to a
        # monochrome amplitude trace, and showing inert controls is worse than
        # showing none.
        self._sync_adv_panel()

    def _on_render_chip(self, _on):
        self._sync_adv_panel()

    def _sync_adv_panel(self):
        show = self.render_chip.get() and self.view_seg.get() == 1
        if show and not self.adv_panel.winfo_ismapped():
            self.adv_panel.pack(fill=tk.X, padx=10, pady=(0, 6),
                                before=self.canvas_box)
        elif not show and self.adv_panel.winfo_ismapped():
            self.adv_panel.pack_forget()

    # ----- per-instrument show/hide (v4.9.2) --------------------------------------
    def _build_inst_panel(self):
        # One toggle per drum lane, applied to BOTH views (declutter a busy
        # chart). Mirrors the Advanced (render) panel's show/hide; a hidden lane
        # keeps its strip row (dimmed) but drops its notes/flags/energy.
        self.inst_panel = tk.Frame(self, background=DARKER,
                                   highlightthickness=1,
                                   highlightbackground=PURPLE_DEEP)
        row = tk.Frame(self.inst_panel, background=DARKER)
        row.pack(fill=tk.X, padx=10, pady=5)
        ttk.Label(row, text="Show drums", style="Spec.Muted.TLabel").pack(
            side=tk.LEFT, padx=(0, 8))
        self._inst_chips = {}
        for idx, name, color, _band, _sl, _bl in LANES:
            chip = Chip(row, name, accent=color, on=True,
                        command=functools.partial(self._on_inst_toggle, idx),
                        tooltip="Show / hide %s in both views." % name)
            chip.pack(side=tk.LEFT, padx=(0, 4))
            self._inst_chips[idx] = chip
        self._sep(row).pack(side=tk.LEFT, fill=tk.Y, padx=8, pady=2)
        OutlineButton(row, "All", accent=GREEN,
                      command=lambda: self._set_all_lanes(True),
                      tooltip="Show every drum.").pack(side=tk.LEFT)
        OutlineButton(row, "None", accent=PURPLE_EDGE,
                      command=lambda: self._set_all_lanes(False),
                      tooltip="Hide every drum.").pack(side=tk.LEFT, padx=(4, 0))

    def _on_inst_chip(self, _on=None):
        self._sync_inst_panel()

    def _sync_inst_panel(self):
        show = self.inst_chip.get()
        if show and not self.inst_panel.winfo_ismapped():
            self.inst_panel.pack(fill=tk.X, padx=10, pady=(0, 6),
                                 before=self.canvas_box)
        elif not show and self.inst_panel.winfo_ismapped():
            self.inst_panel.pack_forget()

    def _on_inst_toggle(self, idx, _on=None):
        if self._inst_chips[idx].get():
            self._lane_visible.add(idx)
        else:
            self._lane_visible.discard(idx)
        self._apply_lane_options()
        self._apply_gram_options()

    def _set_all_lanes(self, shown):
        self._lane_visible = set(range(len(LANES))) if shown else set()
        for _idx, chip in self._inst_chips.items():
            chip.set(shown, fire=False)   # apply once below, not per chip
        self._apply_lane_options()
        self._apply_gram_options()

    # ----- options ------------------------------------------------------------------------
    def _chips_changed(self, _on=None):
        # Edit mode needs the notes on screen to click them, so it PINS Notes
        # on. v4.9.5 — pin it VISIBLY. Previously the chip stayed lit, enabled
        # and hand-cursored while every click was silently reverted here, which
        # reads as a dead button: the owner reported Notes "disabled / stuck"
        # and pinned it on the Bars button, which is unrelated (Bars only shares
        # this handler). Greying it out and swapping the cursor makes the lock
        # obvious, and its tooltip says which control to release.
        editing = bool(self.edit_chip.get())
        if editing and not self.notes_chip.get():
            self.notes_chip.set(True)
        self.notes_chip.set_enabled(not editing)
        self._apply_lane_options()
        self._apply_gram_options()

    def _grid_div_subs(self):
        label = self.grid_div_var.get() if hasattr(self, "grid_div_var") else "1/16"
        return dict(GRID_DIVS).get(label, 4)

    def _note_shape(self):
        return "bar" if (hasattr(self, "shape_chip")
                         and self.shape_chip.get()) else "diamond"

    def _visible_lanes(self):
        # None -> every lane shown (the draw loops fast-path this). Otherwise a
        # frozenset of the lane indices still enabled in the Instruments panel.
        v = getattr(self, "_lane_visible", None)
        # Only None (never set) or ALL-visible collapse to the "show all" fast
        # path. An EMPTY set means the user hid every lane via "None" and MUST
        # stay empty (hide all) — the old `if not v` treated {} as None -> showed
        # everything, so "None" did the opposite of nothing (breaker grok F1).
        if v is None or len(v) >= len(LANES):
            return None
        return frozenset(v)

    def _apply_lane_options(self):
        if not hasattr(self, "lane_view"):
            return
        self.lane_view.set_options(
            energy=self.energy_chip.get(), ticks=self.ticks_chip.get(),
            notes=self.notes_chip.get(), flags=self.flags_chip.get(),
            per_lane_raw=self.raw_chip.get(),
            note_size=self.note_size_scale.get() / 100.0,
            ghost=self.ghost_chip.get(),
            ghost_opacity=self.ghost_scale.get() / 100.0,
            flash=self.flash_chip.get(), grid=self.grid_chip.get(),
            grid_div=self._grid_div_subs(), note_shape=self._note_shape(),
            edit=self.edit_chip.get(), snap=self.snap_chip.get(),
            lane_visible=self._visible_lanes())
        # Keep the gutter's (shared)/(own) band labels in sync with the Per-lane
        # raw toggle (owner 2026-07-20): re-fill the gutter only when it flipped.
        if (hasattr(self, "raw_chip")
                and self.raw_chip.get() != getattr(self, "_gutter_raw_state",
                                                   None)):
            self._fill_gutter(getattr(self, "_gutter_row_h",
                                      self.lane_view.row_h))

    def _apply_gram_options(self):
        if not hasattr(self, "gram_view"):
            return
        self.gram_view.set_options(
            notes=self.g_notes_chip.get(), flags=self.g_flags_chip.get(),
            flash=self.g_flash_chip.get(),
            bands=self.bands_chip.get(),
            chart_colors=self.g_colors_chip.get(),
            hz_readout=self.hz_chip.get(),
            grid=self.grid_chip.get(), grid_div=self._grid_div_subs(),
            note_shape=self._note_shape(),
            lane_visible=self._visible_lanes())

    def _on_ghost_scale(self, _value):
        self._apply_lane_options()

    def _on_note_size_scale(self, _value):
        self._apply_lane_options()

    # ----- zoom -------------------------------------------------------------------------
    def _on_zoom_scale(self, value):
        # Reentrancy guard (breaker R4B1-1/R4B2-1/R4B3-1a, 2026-07-20):
        # ttk.Scale CLAMPS a programmatic below-floor set() to from_=ZOOM_MIN
        # and FIRES this command with the clamped value — without the guard
        # that scheduled a debounced _set_zoom(30) that silently reverted
        # every below-floor Fit ~90 ms later.
        if getattr(self, "_zoom_syncing", False):
            return
        if abs(float(value) - self._zoom) < 0.5:
            return
        if self._zoom_job is not None:
            self.after_cancel(self._zoom_job)
        self._zoom_job = self.after(90, functools.partial(
            self._set_zoom, float(value), False))

    def _on_wheel_zoom(self, direction):
        # Smoother than the old *1.4/notch (a 40% jump per tick felt jarring and
        # forced a full spectrogram rebuild each time): a gentle 1.12x step, and
        # DEBOUNCED through the same job the slider uses so a fast scroll coalesces
        # into one rebuild instead of one-per-notch.
        target = self._zoom * (1.12 if direction > 0 else 1.0 / 1.12)
        # Floor = min(current, ZOOM_MIN) (breaker R4B3-1, 2026-07-20): from a
        # fitted (below-floor) view, the old max(ZOOM_MIN, …) made a zoom-OUT
        # gesture jump IN to 30 px/s. Now zoom-out can't go below where the
        # fit already is, and zoom-in climbs smoothly back into the UI range.
        target = max(min(self._zoom, ZOOM_MIN), min(ZOOM_MAX, target))
        # Update the cheap lane view + label immediately for a live feel; defer
        # the expensive gram rebuild to the debounce.
        self._zoom = target
        self.lane_view.set_zoom(target)
        self.zoom_lbl.configure(
            text=("%d px/s" % target) if target >= 10
            else ("%.2f px/s" % target))
        # Guarded sync (breaker R5B1-1 sub-note): the scale's clamp-fired
        # command was a live copy of the R4B1-1 revert mechanism.
        self._zoom_syncing = True
        try:
            self.zoom_scale.set(target)
        finally:
            self._zoom_syncing = False
        if self._zoom_job is not None:
            self.after_cancel(self._zoom_job)
        # ui_clamp=False (breaker R5B1-1/R5B2-1 MAJOR, 2026-07-20 — three legs
        # converged): the debounced follow-up re-applied _set_zoom's DEFAULT
        # ZOOM_MIN floor, snapping a fitted below-floor wheel target back to
        # 30 px/s 70 ms later — the wheel half of the R4B3-1 fix defeated
        # itself. `target` is already floored correctly two lines up.
        self._zoom_job = self.after(70, functools.partial(
            self._set_zoom, target, False, ui_clamp=False))

    def _on_zoom_fit(self):
        if self._model is None:
            self._status("Run Compare first -- Fit zooms the analyzed song.")
            return
        view = self.gram_view if self.view_seg.get() >= 1 else self.lane_view
        vw = max(100, view.canvas.winfo_width() - 4)
        # ui_clamp=False (breaker R3E-2, 2026-07-20): Fit's whole job is to go
        # BELOW the slider's ZOOM_MIN for songs longer than ~viewport/30 px/s
        # (~37 s at 1100 px) — the UI clamp made Fit a no-op for real songs.
        # Manual zooming from a fitted view re-enters the normal UI range.
        self._set_zoom(vw / max(1.0, self._model.dur), ui_clamp=False)

    def _set_zoom(self, pps, sync_slider=True, ui_clamp=True):
        lo = ZOOM_MIN if ui_clamp else ZOOM_FIT_MIN
        self._zoom = max(lo, min(ZOOM_MAX, float(pps)))
        # cancel-before-null (breaker R4B2-2, 2026-07-20; supersedes the
        # EDGE-5 null): an armed debounce job left pending here fired later,
        # overriding this direct set — and outlived destroy() as an
        # untracked orphan (the R2E-2 bgerror class).
        if self._zoom_job is not None:
            try:
                self.after_cancel(self._zoom_job)
            except Exception:
                pass
        self._zoom_job = None
        self.lane_view.set_zoom(self._zoom)
        self.gram_view.set_zoom(self._zoom)
        self.zoom_lbl.configure(
            text=("%d px/s" % self._zoom) if self._zoom >= 10
            else ("%.2f px/s" % self._zoom))
        if sync_slider:
            # Guarded programmatic sync — see _on_zoom_scale (R4B3-1a).
            self._zoom_syncing = True
            try:
                self.zoom_scale.set(self._zoom)
            finally:
                self._zoom_syncing = False

    # ----- advanced render params ---------------------------------------------------------
    def _on_brightness_scale(self, _value):
        if self._bright_job is not None:
            self.after_cancel(self._bright_job)
        self._bright_job = self.after(150, self._apply_render_params)

    def _on_adv_combo(self, _event):
        self._apply_render_params()

    def _apply_render_params(self):
        if not hasattr(self, "gram_view"):
            return
        # cancel-before-null (breaker R4B2-2, 2026-07-20) — same orphaned-
        # debounce hazard as _set_zoom.
        if self._bright_job is not None:
            try:
                self.after_cancel(self._bright_job)
            except Exception:
                pass
        self._bright_job = None
        cmap = {"magma (ParaKit)": "magma", "cyan ice": "cyan",
                "grayscale": "gray"}.get(self.cmap_combo.get(), "magma")
        self.gram_view.set_render_params(
            brightness=self.bright_scale.get() / 100.0,
            fmax=float(self.fmax_combo.get()),
            log_scale=self.scale_combo.get().startswith("log"),
            cmap=cmap)

    # ----- transport (hooks-aware; synthetic fallback) -----------------------------
    #
    # State machine (fixes the play-after-pause double/desynced audio, 2026-07-20):
    #   stopped  --Play-->  playing         (mixer_play: fresh stream at _play_t)
    #   playing  --Play-->  paused          (mixer_pause: FREEZE the stream)
    #   paused   --Play-->  playing         (mixer_unpause: RESUME, do NOT reload)
    #   *        --Stop-->  stopped         (mixer_stop: release + reset to 0)
    # The old bug: pause left the mixer running and Play always called mixer_play,
    # so a second stream started over the still-running first one. Position is the
    # tab's own wall-clock * speed (v4's model); there is no mixer_pos hook.

    def _play_source(self):
        """Which audio path playback uses (Play-source switch): the Full Mix if
        it is loaded AND 'Full Mix' is selected, else the Drums stem (the
        primary field)."""
        try:
            if (getattr(self, "_stem_seg", None) is not None
                    and self._stem_seg.get() == 1):   # 1 = Full Mix
                mix = self.stem_field.get()
                if mix and os.path.isfile(mix):
                    return mix
        except Exception:
            pass
        ref = self.reference_field.get()                  # 0 = Drums (default)
        return ref if ref and os.path.isfile(ref) else None

    def _start_stream(self, at_t):
        """Fresh mixer stream at song-second at_t and the current speed; returns
        True if real audio started (False -> synthetic clock only)."""
        if not (self.hooks and "mixer_play" in self.hooks):
            # No audio hook at all (standalone/demo): the transport IS running
            # on the synthetic clock, so say so (breaker R6B2-2, 2026-07-20 —
            # this silent branch let a stale "Paused." assert pause during
            # live playback whenever Play took the stopped->playing path).
            self._status("Playing (%.2fx)." % self._speed)
            return False
        path = self._play_source()
        if not path:
            self._status("No audio to play \u2014 using the silent clock.", AMBER)
            return False
        ok, started = self._hook_call("mixer_play", path, at_t, self._speed)
        # The hook can FAIL BY RETURN VALUE, not just by raising (breaker
        # R2B2-1, 2026-07-20): v4's player returns False on every internal
        # failure (e.g. soundfile can't decode the m4a/aac our own picker
        # offers), and discarding it ran a silent moving playhead with no
        # status. Only an explicit False counts \u2014 None (hooks with no return
        # contract) still means "started".
        if not ok or started is False:
            self._status("Audio playback unavailable \u2014 using the silent "
                         "clock.", AMBER)
            return False
        # A SUCCESSFUL start must clear any stale silent-clock warning from an
        # earlier failed attempt (breaker R3B2-2, 2026-07-20).
        self._status("Playing (%.2fx)." % self._speed)
        return True

    def _on_play(self):
        if self._model is None:
            self._status("Run Compare first, then Play.")
            return
        if self._playing:
            self._pause()
            return
        if self._paused:
            self._resume()
            return
        # stopped -> playing: fresh stream
        self._playing = True
        self._paused = False
        self.play_btn.configure(text="\u25aa\u25aa Pause")
        # ANCHOR AFTER THE STREAM STARTS (2026-08-17). _start_stream decodes and
        # resamples synchronously -- on a cold _spec_sound_cache miss that is the
        # full file (0.9 s for a 6.5-minute 48 kHz mix), and the 4-entry LRU only
        # shrinks a warm hit, it does not remove it. Anchoring _tick_last first
        # meant the FIRST _play_tick added the whole decode to _play_t, so the
        # playhead jumped ahead of the audio and stayed there. _on_seek below
        # already had this order right; this path was the odd one out.
        self._start_stream(self._play_t)
        self._tick_last = time.monotonic()
        self._tick_job = self.after(30, self._play_tick)

    def _pause(self):
        # playing -> paused: FREEZE audio in place, keep _play_t.
        self._playing = False
        self._paused = True
        self._status("Paused.")   # see R4B3-5 in _on_stop
        self.play_btn.configure(text="\u25ba Play")
        if self._tick_job is not None:
            self.after_cancel(self._tick_job)
            self._tick_job = None
        if self.hooks and "mixer_pause" in self.hooks:
            self._hook_call("mixer_pause")

    def _resume(self):
        # paused -> playing: RESUME the same stream (never reload -> no layering).
        self._playing = True
        self._paused = False
        # Status must follow the transport (breaker R5B1-2/R5E-2, 2026-07-20):
        # without this, "Paused." asserted pause during live playback \u2014 the
        # mirror image of the R4B3-5 stale-"Playing" fix.
        self._status("Playing (%.2fx)." % self._speed)
        self.play_btn.configure(text="\u25aa\u25aa Pause")
        # Same order as _on_play / _on_seek. Unpause is cheap today (it resumes an
        # existing channel rather than decoding), so this is consistency rather
        # than a live fix -- but it removes the last place in this file where the
        # clock is anchored before a hook call, so the rule holds without an
        # exception a later edit could widen.
        if self.hooks and "mixer_unpause" in self.hooks:
            self._hook_call("mixer_unpause")
        self._tick_last = time.monotonic()
        self._tick_job = self.after(30, self._play_tick)

    def _play_tick(self):
        if not self._playing or self._model is None:
            return
        now = time.monotonic()
        # Song time advances at speed * wall-clock: a 0.5x stream is stretched
        # to 2x length, so 1 s of wall = 0.5 s of song.
        self._play_t += (now - self._tick_last) * self._speed
        self._tick_last = now
        if self._play_t >= self._model.dur:
            self._on_stop()
            return
        self._push_playhead()
        self._tick_job = self.after(30, self._play_tick)

    def _push_playhead(self):
        self.lane_view.set_playhead(self._play_t)
        self.gram_view.set_playhead(self._play_t)
        self._update_time_label()
        self._follow_playhead()

    def _follow_playhead(self):
        view = self.gram_view if self.view_seg.get() >= 1 else self.lane_view
        px = view.playhead_x()
        if px is None:
            return
        c = view.canvas
        vw = max(1, c.winfo_width())
        left = c.canvasx(0)
        sr = c.cget("scrollregion")
        parts = sr.split() if isinstance(sr, str) else list(sr)
        total = max(1.0, float(parts[2]) if len(parts) >= 3 else 1.0)
        if px < left + 0.10 * vw or px > left + 0.75 * vw:
            c.xview_moveto(max(0.0, min(1.0, (px - 0.3 * vw) / float(total))))
            # v4.9.0 — the gram view renders only the visible window, so an
            # auto-scroll must re-window its heatmap image (xview_moveto here
            # bypasses _on_scrollbar). Guarded: the lane view has no _build_image.
            _bi = getattr(view, "_build_image", None)
            if callable(_bi):
                _bi()
            view._redraw_now()

    def _on_stop(self):
        # AUDIO FIRST, widgets after (breaker R5B2-2, 2026-07-20): on a
        # destroyed tab the widget writes raise TclError BEFORE the hook ran,
        # external_stop swallowed it, and the host's audio was left playing
        # with no remedy through the documented "safe at any time" API. Flags,
        # tick-cancel, and the mixer hook now run unconditionally; every
        # widget touch is grouped after them and TclError-guarded.
        was_active = self._playing or self._paused
        self._playing = False
        self._paused = False
        if self._tick_job is not None:
            try:
                self.after_cancel(self._tick_job)
            except Exception:
                pass
            self._tick_job = None
        # State line BEFORE the hook (breaker R6B2-1/OBS-1, 2026-07-20: the
        # round-5 audio-first reorder made "Stopped." clobber a raising hook's
        # AMBER "hook failed" warning — the warning must win, as it did
        # pre-fold). Safe here because _status is itself teardown-guarded; the
        # remaining widget writes stay after the hook, which is what R5B2-2
        # actually needed. Only when there WAS something to stop (R4B3-5).
        if was_active:
            self._status("Stopped.")
        if self.hooks and "mixer_stop" in self.hooks:
            self._hook_call("mixer_stop")
        self._play_t = 0.0
        try:
            self.play_btn.configure(text="► Play")
            self.lane_view.set_playhead(None)
            self.gram_view.set_playhead(None)
            self._update_time_label()
        except tk.TclError:
            pass   # destroyed/mid-teardown tab: the audio is already stopped

    def external_stop(self):
        """Public stop for the HOST app (v4's tab-changed handler calls this
        when the user leaves the tab while our audio owns the shared mixer):
        stops the transport AND resets the transport UI, unlike a raw mixer
        stop which would leave the playhead running on the synthetic clock.
        Safe to call at any time, including before any compare has run."""
        try:
            self._on_stop()
        except Exception:
            pass

    def _on_seek(self, t):
        if self._model is None:
            return
        self._play_t = max(0.0, min(self._model.dur, float(t)))
        # A channel can't be repositioned, so seeking mid-play restarts the
        # stream at the new spot. Paused stays paused (position moves; audio
        # re-anchors on the next Play).
        if self._playing:
            if not self._start_stream(self._play_t):
                # A FAILED restart must not leave the PREVIOUS stream sounding
                # under the silent clock (breaker R3E-3, 2026-07-20).
                if self.hooks and "mixer_stop" in self.hooks:
                    self._hook_call("mixer_stop")
            self._tick_last = time.monotonic()
        elif self._paused:
            if self.hooks and "mixer_stop" in self.hooks:
                self._hook_call("mixer_stop")
            self._paused = False
        self.lane_view.set_playhead(self._play_t)
        self.gram_view.set_playhead(self._play_t)
        self._update_time_label()

    def _rebuild_stream_if_playing(self):
        """Speed or stem changed: re-anchor the audio at the current position
        so the change takes effect immediately (position + playhead unchanged)."""
        if self._playing:
            if not self._start_stream(self._play_t):
                # A FAILED restart must not leave the PREVIOUS stream sounding
                # under the silent clock (breaker R3E-3, 2026-07-20).
                if self.hooks and "mixer_stop" in self.hooks:
                    self._hook_call("mixer_stop")
            self._tick_last = time.monotonic()
        elif self._paused:
            # Drop the frozen stream; next Play rebuilds at the new setting.
            if self.hooks and "mixer_stop" in self.hooks:
                self._hook_call("mixer_stop")
            self._paused = False

    def _speed_step(self, delta):
        self._speed = max(0.25, min(2.0, round(self._speed + delta, 2)))
        if getattr(self, "speed_lbl", None) is not None:
            self.speed_lbl.configure(text="Speed %.2fx" % self._speed)
        self._rebuild_stream_if_playing()

    def _speed_reset(self):
        self._speed = 1.0
        if getattr(self, "speed_lbl", None) is not None:
            self.speed_lbl.configure(text="Speed %.2fx" % self._speed)
        self._rebuild_stream_if_playing()

    def _on_stem_toggle(self):
        # Switch which audio plays (Drums stem vs Full Mix); analysis unaffected.
        # "Full Mix" with NO mix loaded is a LIE (breaker R7E-1, 2026-07-20):
        # the segment showed Full Mix while _play_source() silently fell back to
        # the Drums, with no status saying so. Snap back and explain instead.
        if (getattr(self, "_stem_seg", None) is not None
                and self._stem_seg.get() == 1):
            mix = self.stem_field.get()
            if not (mix and os.path.isfile(mix)):
                self._stem_seg.set(0, fire=False)
                self._status("No full mix loaded — playing the drums. Load a "
                             "full mix to hear it.", AMBER)
                return
        self._rebuild_stream_if_playing()

    def _rebuild_if_source_changed(self, prev_source):
        """Rebuild the stream only when the PLAYED audio actually changed
        (breaker R7E-2, 2026-07-20): no-change stem ops — re-browsing the same
        path, Clear with nothing loaded, Clear while Mix is selected — each
        tore down and restarted playback (a full decode+resample in v4).
        Deliberately NOT inside _rebuild_stream_if_playing: _speed_step and the
        Advanced options legitimately rebuild the SAME source at a new speed."""
        if self._play_source() != prev_source:
            self._rebuild_stream_if_playing()

    def _update_time_label(self):
        if self._model is None:
            self.time_lbl.configure(text="0:00.0 / \u2013:\u2013\u2013.\u2013")
        else:
            self.time_lbl.configure(text="%s / %s" % (
                fmt_time(self._play_t), fmt_time(self._model.dur)))

    # ----- chart editing -----------------------------------------------------
    def _push_undo(self):
        self._undo_stack.append(list(self._model.notes))
        if len(self._undo_stack) > 100:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def _apply_notes(self, notes):
        self._model.notes = notes
        self._model.issues = self._model.compute_issues(notes)
        # Rebuild the views' sorted overlay caches, not just request a redraw
        # (review #1): a bare request_redraw re-windowed the STALE sorted lists.
        self.lane_view.refresh_notes_cache()
        self.gram_view.refresh_notes_cache()
        self._update_readout()
        self._update_transport()
        n_miss = sum(1 for i in self._model.issues if i["type"] == "miss")
        n_phantom = sum(1 for i in self._model.issues
                        if i["type"] == "phantom")
        self._status("Chart edit: %d notes, now %d MISS + %d PHANTOM."
                     % (len(notes), n_miss, n_phantom))

    def _on_lane_edit(self, t, lane):
        if self._model is None or self._comparing:
            return
        notes = list(self._model.notes)
        tol_s = min(8.0 / (self._zoom or 1.0), _LANE_DEL_TOL_MAX_S)
        hit, best = -1, float("inf")
        for i, nt in enumerate(notes):
            if int(nt[1]) != int(lane):
                continue
            d = abs(float(nt[0]) - t)
            if d < best:
                best, hit = d, i
        self._push_undo()
        if hit >= 0 and best <= tol_s:
            del notes[hit]
        else:
            t_new = (self.lane_view.snap_time(t)
                     if self.snap_chip.get() else max(0.0, t))
            notes.append((float(t_new), int(lane), 100))
            notes.sort(key=lambda nt: nt[0])
        self._apply_notes(notes)

    def _on_undo(self):
        if self._model is None or self._comparing or not self._undo_stack:
            return
        self._redo_stack.append(list(self._model.notes))
        self._apply_notes(self._undo_stack.pop())

    def _on_redo(self):
        if self._model is None or self._comparing or not self._redo_stack:
            return
        self._undo_stack.append(list(self._model.notes))
        self._apply_notes(self._redo_stack.pop())

    # ----- MIDI out (hooks-aware; standalone stubs) ----------------------------
    def _write_midi(self, path: str):
        try:
            import parakit_spectral_engine as eng
            eng.write_chart_midi(self._model.notes, self._model.bpm, path)
            self._status("Wrote %d notes to %s"
                         % (len(self._model.notes), os.path.basename(path)),
                         GREEN)
        except Exception as e:
            self._status("MIDI write failed: %s" % e, AMBER)
            return
        # Tell the host a .mid on disk just changed under it (breaker H6,
        # 2026-07-29): the MIDI Editor can be holding this exact path in memory,
        # and its next Save wrote the pre-Spectral copy straight back over these
        # corrections, with no notice in EITHER direction. Deliberately OUTSIDE
        # the try above so a hook failure can never be reported as "MIDI write
        # failed" -- the write already succeeded by this point. _hook_call
        # no-ops (returns (False, None)) when the hook is absent, so running
        # this file standalone is unaffected.
        self._hook_call("midi_written", path)

    def _on_overwrite_midi(self):
        if self._model is None:
            self._status("Run Compare first -- Overwrite writes the compared "
                         "(edited) notes back over the loaded .mid.")
            return
        if not self.hooks:
            self._status("Standalone: would overwrite %s with %d notes (MIDI "
                         "out is only wired when hooks are present)."
                         % (os.path.basename(self._overwrite_target) or
                            "(choose a .mid target)",
                            len(self._model.notes)))
            return
        target = self._overwrite_target
        if not target:
            # Still reachable with the button greyed (a programmatic invoke, or a
            # disarm that races the click), so give the real reason. Folding this
            # into the extension check below is what produced "not a .mid file"
            # for a target that was simply never armed.
            self._status("Nothing is armed for Overwrite -- run Compare again to "
                         "reload the chart first.", AMBER)
            return
        if os.path.splitext(target)[1].lower() not in (".mid", ".midi"):
            self._status("Overwrite target is not a .mid file -- use Export "
                         "MIDI to save as.", AMBER)
            return
        if not os.path.isfile(target):
            self._status("Overwrite target not found: %s" % target, AMBER)
            return
        if not messagebox.askyesno(
                "Overwrite MIDI",
                "Replace\n%s\nwith the current %d notes?"
                % (target, len(self._model.notes)),
                parent=self):
            self._status("Overwrite canceled.")
            return
        self._write_midi(target)

    def _on_export_midi(self):
        if self._model is None:
            self._status("Run Compare first -- Export saves the compared "
                         "(edited) notes as a .mid copy.")
            return
        if not self.hooks:
            self._status("Standalone: would export a .mid copy of %d notes @ "
                         "%d BPM (MIDI out is only wired when hooks are "
                         "present)."
                         % (len(self._model.notes), round(self._model.bpm)))
            return
        path = filedialog.asksaveasfilename(
            parent=self, title="Export MIDI",
            defaultextension=".mid",
            filetypes=(("MIDI files", "*.mid"), ("All files", "*.*")),
            initialfile="chart.mid")
        if not path:
            self._status("Export canceled.")
            return
        self._write_midi(path)

    # ----- keyboard (only when the tab is visible) ---------------------------------
    def _bind_keys(self):
        # add="+" (breaker B2-3, 2026-07-20): the host app (v4) binds the SAME
        # sequences on this toplevel for the MIDI Editor; a plain bind() here
        # REPLACES those handlers and silently kills the other tab's shortcuts
        # (and theirs would kill ours if bound later). Both sides guard on
        # their own tab's visibility, so chained handlers coexist cleanly.
        top = self.winfo_toplevel()
        self._key_binds = []
        for seq, fn in (("<space>", self._key_space),
                        ("<Home>", self._key_home),
                        ("<Control-z>", self._key_undo),
                        ("<Control-y>", self._key_redo),
                        ("<Control-Z>", self._key_redo)):
            self._key_binds.append((seq, top.bind(seq, fn, add="+")))

    def _unbind_keys(self):
        """Remove ONLY our chained handlers from the toplevel (breaker R2E-3,
        2026-07-20: the add="+" binds pinned every destroyed tab alive forever
        and grew the bind script unboundedly). Hand-rolled line removal —
        3.12's Misc.unbind(seq, funcid) clears the WHOLE script, which would
        kill the HOST's handlers on the same sequences."""
        try:
            top = self.winfo_toplevel()
        except Exception:
            return
        for seq, fid in getattr(self, "_key_binds", ()):
            try:
                script = top.bind(seq) or ""
                # Byte-exact removal (breaker R3E-1, 2026-07-20): drop our
                # line AND the one separator newline Tk inserted before it on
                # append — every other handler's stored bytes survive intact.
                # Match the funcid at its INVOCATION position "[fid arg…]",
                # not as a bare substring (breaker R4B2-3, 2026-07-20): a
                # host funcid embedding ours as a substring must survive.
                marker = "[" + fid
                keep = []
                for ln in script.split("\n"):
                    if (marker + " ") in ln or (marker + "]") in ln:
                        if keep and keep[-1] == "":
                            keep.pop()
                        continue
                    keep.append(ln)
                top.tk.call("bind", top._w, seq, "\n".join(keep))
                top.deletecommand(fid)
            except Exception:
                pass
        self._key_binds = []

    def destroy(self):
        # Stop any live mixer stream first (breaker fix B-DEST-1, 2026-07-21):
        # PracticeTab.destroy calls external_stop, but Spectral only cancelled
        # jobs -> a frame rebuild / failed re-init while playing left audio going
        # with no tab owning it. (Normal tab-SWITCH already stops via the host's
        # _spectral_on_tab_changed_stop; this covers the destroy/teardown path.)
        try:
            self.external_stop()
        except Exception:
            pass
        # Cancel every pending after-job and release the root key binds BEFORE
        # teardown (breaker R2E-2/R2E-3, 2026-07-20): pending debounce/poll
        # callbacks on a destroyed tab raise "invalid command name" bgerrors,
        # and the chained binds leaked every destroyed instance.
        for attr in ("_tick_job", "_zoom_job", "_bright_job",
                     "_poll_compare_job", "_synth_job"):
            job = getattr(self, attr, None)
            if job is not None:
                try:
                    self.after_cancel(job)
                except Exception:
                    pass
                setattr(self, attr, None)
        self._unbind_keys()
        super().destroy()

    def _text_focus(self):
        w = self.focus_get()
        return (w is not None and w.winfo_class()
                in ("TEntry", "Entry", "TCombobox", "Text"))

    def _keys_active(self):
        """True when this tab should handle a root key event. TclError-safe:
        with add="+" the binds outlive a destroyed tab on the shared toplevel,
        and a guard that throws there becomes a background Tk error."""
        try:
            return self.winfo_ismapped() and not self._text_focus()
        except (tk.TclError, KeyError):
            # KeyError: focus_get() raises KeyError('popdown') when Tk's own
            # combobox popdown holds focus (breaker R2 hardening observation —
            # unreachable via key routing today, but a guard must never throw).
            return False

    def _key_space(self, _event):
        if not self._keys_active():
            return None
        self._on_play()
        return "break"

    def _key_home(self, _event):
        if not self._keys_active():
            return None
        self._on_stop()
        return "break"

    def _key_undo(self, _event):
        if not self._keys_active():
            return None
        self._on_undo()
        return "break"

    def _key_redo(self, _event):
        if not self._keys_active():
            return None
        self._on_redo()
        return "break"


# ---------------------------------------------------------------------------
# Standalone host --------------------------------------------------------------
# ---------------------------------------------------------------------------
def _selftest(root, tab):
    """Headless-ish smoke test (--selftest): runs the compare, exercises both
    views + transport + editing, and prints evidence. No mainloop."""
    tab._on_compare()
    deadline = time.time() + 15.0
    while tab._model is None and time.time() < deadline:
        root.update()
        time.sleep(0.05)
    if tab._model is None:
        print("SELFTEST FAIL: compare never finished")
        return 1
    root.update_idletasks()
    tab.view_seg.set(1)
    root.update_idletasks()
    tab.gram_view._redraw_now()
    gram_items = len(tab.gram_view.canvas.find_all())
    tab.view_seg.set(0)
    root.update_idletasks()
    tab.lane_view._redraw_now()
    lane_items = len(tab.lane_view.canvas.find_all())
    n_miss = sum(1 for i in tab._model.issues if i["type"] == "miss")
    n_phantom = sum(1 for i in tab._model.issues if i["type"] == "phantom")
    print("SELFTEST model: dur=%.1fs notes=%d miss=%d phantom=%d"
          % (tab._model.dur, len(tab._model.notes), n_miss, n_phantom))
    print("SELFTEST canvas items: lane=%d gram=%d" % (lane_items, gram_items))
    tab._on_seek(5.0)
    before_edit = len(tab._model.notes)
    tab._on_lane_edit(5.0, 2)
    after_edit = len(tab._model.notes)
    tab._on_undo()
    restored = len(tab._model.notes)
    tab._on_play()
    root.update()
    tab._on_stop()
    zoom0 = tab._zoom
    tab._on_wheel_zoom(1)
    root.update_idletasks()

    # --- embedded-mode construction proof -------------------------------------
    # A plain Toplevel with NO global theme must still build and render a
    # SpectralTab with empty hooks (each hook degrades to standalone fallback).
    plain = tk.Toplevel(root)
    plain.geometry("800x600")
    frame = tk.Frame(plain)
    frame.pack(fill=tk.BOTH, expand=True)
    tab2 = SpectralTab(frame, hooks={})
    tab2.pack(fill=tk.BOTH, expand=True)
    plain.deiconify()
    for _ in range(5):
        plain.update()
        time.sleep(0.02)
    plain.update_idletasks()
    embedded_ok = (tab2.winfo_exists()
                   and tab2.winfo_width() > 50
                   and tab2.winfo_height() > 50)
    print("SELFTEST embedded construction (no global theme): %s"
          % ("OK" if embedded_ok else "FAIL"))
    plain.withdraw()
    plain.destroy()

    def pump(seconds):
        end = time.time() + seconds
        while time.time() < end:
            root.update()
            time.sleep(0.03)
        root.update_idletasks()

    def layout_checks(width, height, label):
        root.geometry("%dx%d" % (width, height))
        pump(0.5)
        lv, gv = tab.lane_view, tab.gram_view
        lc, gc = lv.canvas, gv.canvas
        row_h, gram_h = lv.row_h, gv.gram_h
        ch, gh = lc.winfo_height(), gc.winfo_height()
        ok = True
        print("SELFTEST [%s] host=%dx%d canvas lane=%dpx gram=%dpx -> "
              "row_h=%.1f gram_h=%.1f"
              % (label, width, height, ch, gh, row_h, gram_h))
        lane_bottom = RULER_H + row_h * len(LANES)
        lane_clamped = (row_h >= LANE_ROW_H_MAX - 0.5
                        or row_h <= LANE_ROW_H_MIN + 0.5)
        lane_fill = lane_clamped or lane_bottom >= ch - 2
        print("SELFTEST [%s] lane fill: bottom=%.0f canvas=%d clamped=%s -> "
              "%s" % (label, lane_bottom, ch, lane_clamped, lane_fill))
        ok = ok and lane_fill
        gram_bottom = gv._total_h()
        gram_fill = (gram_h >= GRAM_H_MAX - 0.5) or gram_bottom >= gh - 2
        gram_fit = (gram_h <= GRAM_H_MIN + 0.5) or gram_bottom <= gh + 2
        print("SELFTEST [%s] gram fill: bottom=%.0f canvas=%d gram_h=%.1f -> "
              "fill=%s fit=%s" % (label, gram_bottom, gh, gram_h,
                                  gram_fill, gram_fit))
        ok = ok and gram_fill and gram_fit
        tab.view_seg.set(0)
        root.update_idletasks()
        lv._redraw_now()
        escaped = 0
        for item in lc.find_withtag("static"):
            if lc.type(item) != "polygon":
                continue
            bb = lc.bbox(item)
            if not any(bb[1] >= RULER_H + i * row_h - 1
                       and bb[3] <= RULER_H + (i + 1) * row_h + 1
                       for i in range(len(LANES))):
                escaped += 1
        print("SELFTEST [%s] bug1 lane-fill containment: escaped=%d"
              % (label, escaped))
        ok = ok and escaped == 0
        worst = 0.0
        for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
            lv._on_scrollbar("moveto", str(frac))
            lv._scroll_render_now()   # scroll render is throttled via after()
                                      # (v4.9.2) — force it before measuring
            need = lc.canvasx(max(1, lc.winfo_width()))
            drawn = max((lc.bbox(i)[2] for i in lc.find_withtag("static")
                         if lc.bbox(i)), default=0.0)
            worst = max(worst, need - drawn)
        print("SELFTEST [%s] bug2 lane right-edge cover: worst shortfall="
              "%.0f px" % (label, worst))
        tab.view_seg.set(1)
        root.update_idletasks()
        gworst = 0.0
        for frac in (0.0, 0.5, 1.0):
            gv._on_scrollbar("moveto", str(frac))
            gv._scroll_render_now()   # force the throttled render (v4.9.2)
            need = gc.canvasx(max(1, gc.winfo_width()))
            drawn = max((gc.bbox(i)[2] for i in gc.find_withtag("static")
                         if gc.bbox(i)), default=0.0)
            gworst = max(gworst, need - drawn)
        print("SELFTEST [%s] bug2 gram right-edge cover: worst shortfall="
              "%.0f px" % (label, gworst))
        tab.view_seg.set(0)
        root.update_idletasks()
        ok = ok and worst <= 4.0 and gworst <= 4.0
        return ok

    _stl = ttk.Style(root)
    grip_ok = all(
        _stl.lookup("Horizontal.TScrollbar", opt)
        == _stl.lookup("Horizontal.TScrollbar", "background")
        for opt in ("bordercolor", "lightcolor", "darkcolor"))
    print("SELFTEST bug3 scrollbar grip blends with thumb: %s" % grip_ok)
    ok = (lane_items > 50 and gram_items > 50
          and after_edit == before_edit + 1 and restored == before_edit
          and tab._zoom > zoom0 and grip_ok and embedded_ok)
    for wdt, hgt, lbl in ((1100, 720, "short/min"),
                          (1600, 950, "1080p-ish"),
                          (1900, 1150, "1440p-ish")):
        ok = layout_checks(wdt, hgt, lbl) and ok
    tab.lane_view._on_scrollbar("moveto", "0.5")
    tab.gram_view._on_scrollbar("moveto", "0.5")
    tab._on_seek(12.0)
    lx0, gx0 = tab.lane_view.canvas.xview()[0], tab.gram_view.canvas.xview()[0]
    ph0 = tab._play_t
    root.geometry("1600x900")
    pump(0.5)
    lx1, gx1 = tab.lane_view.canvas.xview()[0], tab.gram_view.canvas.xview()[0]
    scroll_kept = (abs(lx0 - lx1) < 0.01 and abs(gx0 - gx1) < 0.01
                   and tab._play_t == ph0)
    print("SELFTEST scroll/playhead preserved across resize: lane %.3f->%.3f"
          " gram %.3f->%.3f playhead %.1f->%.1f -> %s"
          % (lx0, lx1, gx0, gx1, ph0, tab._play_t, scroll_kept))
    ok = ok and scroll_kept
    print("SELFTEST zoom: %.0f -> %.0f px/s" % (zoom0, tab._zoom))

    # Transport state machine (regression guard for the 2026-07-20 play-after-
    # pause double-audio bug + speed/stem). Uses call-recording mock hooks on a
    # SECOND tab so the layout checks above stay on the real tab. Peak concurrent
    # streams must never exceed 1, and Play-after-Pause must UNPAUSE, not replay.
    tcalls = []
    tstate = {"open": 0, "peak": 0}
    def _tp(path, start_s=0.0, speed=1.0):
        tcalls.append(("play", speed)); tstate["open"] += 1
        tstate["peak"] = max(tstate["peak"], tstate["open"]); return True
    def _ts():
        tcalls.append(("stop",))
        if tstate["open"] > 0:
            tstate["open"] -= 1
    thooks = {"decode_audio": lambda p: (None, None),
              "get_cfg": lambda k, d=None: d, "set_cfg": lambda k, v: None,
              "mixer_play": _tp, "mixer_stop": _ts,
              "mixer_pause": lambda: tcalls.append(("pause",)),
              "mixer_unpause": lambda: tcalls.append(("unpause",))}
    ttab = SpectralTab(root, hooks=thooks)
    ttab._model = MockSpectralModel(seed=2)
    ttab.reference_field.set(os.path.abspath(__file__))
    ttab._on_play(); ttab._pause(); ttab._on_play()      # the repro
    seq = [c[0] for c in tcalls]
    trans_ok = (seq == ["play", "pause", "unpause"] and tstate["peak"] <= 1)
    ttab._on_stop()
    ttab._speed_step(-0.10); ttab._speed_step(-0.10)
    trans_ok = trans_ok and abs(ttab._speed - 0.80) < 1e-9
    ttab._speed = 0.5; ttab._play_t = 0.0; ttab._playing = True
    ttab._tick_last = time.monotonic() - 1.0
    ttab._play_tick()
    trans_ok = trans_ok and 0.45 <= ttab._play_t <= 0.55
    ttab._playing = False
    ttab.destroy()
    print("SELFTEST transport: play/pause/play=%r peak_streams=%d speed=%.2f "
          "clock@0.5x=%.3f -> %s"
          % (seq, tstate["peak"], 0.80, ttab._play_t, "OK" if trans_ok else "FAIL"))
    ok = ok and trans_ok

    # --- breaker round-1 fold guards (2026-07-20) -----------------------------
    # Each is RED against the pre-fix tab (crash or wrong state on these exact
    # inputs) and GREEN now.
    guard_ok = True

    # B2-1/EDGE-1: a dur=0 model must not crash the gram render (main-thread
    # ZeroDivisionError at cols/m.dur), and _finish_real_compare must refuse
    # to install a dur=0 model at all.
    class _DurZero:
        dur = 0.0
    saved_model = tab.gram_view._model
    try:
        tab.gram_view._model = _DurZero()
        tab.gram_view._build_image()
        g1 = tab.gram_view._photo is None
    except Exception as e:
        print("SELFTEST guard dur0-render raised: %r" % (e,))
        g1 = False
    finally:
        tab.gram_view._model = saved_model
        tab.gram_view._build_image()
    gtab = SpectralTab(root, hooks={"get_cfg": lambda k, d=None: d,
                                    "set_cfg": lambda k, v: None})
    try:
        gtab._finish_real_compare({"spec": {"dur": 0.0, "silent": True},
                                   "notes": [], "issues": [], "bpm": 120.0})
        g2 = gtab._model is None
    except Exception as e:
        print("SELFTEST guard dur0-compare raised: %r" % (e,))
        g2 = False
    print("SELFTEST guard dur<=0: render=%s reject=%s"
          % ("OK" if g1 else "FAIL", "OK" if g2 else "FAIL"))
    guard_ok = guard_ok and g1 and g2

    # B2-4: a garbage config store (non-str where a path is expected) must not
    # crash the CONSTRUCTOR, and _cfg_get must hand back the default.
    try:
        btab = SpectralTab(root, hooks={"get_cfg": lambda k, d=None: 42.5,
                                        "set_cfg": lambda k, v: None})
        g3 = btab._cfg_get("spec_last_reference", "") == ""
        btab.destroy()
    except Exception as e:
        print("SELFTEST guard cfg-type raised: %r" % (e,))
        g3 = False
    print("SELFTEST guard cfg type-garbage: %s" % ("OK" if g3 else "FAIL"))
    guard_ok = guard_ok and g3

    # EDGE-5: the zoom debounce must clear the REAL after-id field.
    gtab._zoom_job = "sentinel"
    gtab._set_zoom(100, False)
    g4 = gtab._zoom_job is None
    print("SELFTEST guard zoom_job cleared: %s" % ("OK" if g4 else "FAIL"))
    guard_ok = guard_ok and g4
    gtab.destroy()

    # B2-3: _bind_keys must CHAIN (add="+"), never replace a host handler
    # bound to the same sequence on the shared toplevel.
    fid = root.bind("<space>", lambda e: None)
    ktab = SpectralTab(root, hooks={"get_cfg": lambda k, d=None: d,
                                    "set_cfg": lambda k, v: None})
    script = root.bind("<space>") or ""
    g5 = fid in script
    ktab.destroy()
    # And the guards must be TclError-safe once the tab is destroyed (the
    # add="+" binds outlive it on the toplevel).
    try:
        g6 = ktab._keys_active() is False
    except Exception as e:
        print("SELFTEST guard destroyed-tab keys raised: %r" % (e,))
        g6 = False
    print("SELFTEST guard key-binds chain=%s destroyed-safe=%s"
          % ("OK" if g5 else "FAIL", "OK" if g6 else "FAIL"))
    guard_ok = guard_ok and g5 and g6

    ok = ok and guard_ok

    # --- breaker round-2 fold guards (2026-07-20) -----------------------------
    guard2_ok = True

    # R2B2-1: mixer_play returning False (fail-by-return, not raise) must
    # surface the "playback unavailable" status and report no real stream.
    ftab = SpectralTab(root, hooks={"get_cfg": lambda k, d=None: d,
                                    "set_cfg": lambda k, v: None,
                                    "mixer_play": lambda p, s, sp: False,
                                    "mixer_stop": lambda: None})
    ftab._model = MockSpectralModel(seed=3)
    ftab.reference_field.set(os.path.abspath(__file__))
    started = ftab._start_stream(0.0)
    stat = str(ftab.status_lbl.cget("text"))
    g7 = (started is False) and ("unavailable" in stat)
    print("SELFTEST guard mixer_play-False surfaced: started=%r status=%r -> %s"
          % (started, stat[:60], "OK" if g7 else "FAIL"))
    guard2_ok = guard2_ok and g7
    ftab.destroy()

    # R2E-2: pending after-jobs (zoom debounce, synthetic compare, brightness)
    # must be cancelled by destroy() — zero Tcl bgerrors after teardown.
    root.tk.eval("set ::spec_bgerrs {}")
    root.tk.eval("proc bgerror {m} {lappend ::spec_bgerrs $m}")
    dtab = SpectralTab(root, hooks={})
    dtab._on_wheel_zoom(1)                      # arms the zoom debounce
    dtab._run_synthetic_compare()               # arms the 650ms synth job
    dtab._on_brightness_scale(120)              # arms the brightness debounce
    dtab.destroy()
    end = time.time() + 1.0
    while time.time() < end:
        root.update()
        time.sleep(0.03)
    bgerrs = root.tk.eval("set ::spec_bgerrs")
    g8 = bgerrs == ""
    print("SELFTEST guard destroy cancels after-jobs: bgerrors=%r -> %s"
          % (bgerrs[:80], "OK" if g8 else "FAIL"))
    guard2_ok = guard2_ok and g8

    # R2E-3: destroyed tabs must UNBIND their root key handlers — the bind
    # script must not grow across create/destroy cycles and the host handler
    # must survive; destroyed instances must be collectable.
    import gc as _gc
    import weakref
    host_fid = root.bind("<space>", lambda e: None, add="+")
    base_len = len(root.bind("<space>") or "")
    refs = []
    for _ in range(8):
        w = SpectralTab(root, hooks={})
        refs.append(weakref.ref(w))
        w.destroy()
    del w                       # the loop variable itself pins the last one
    grown = len(root.bind("<space>") or "") - base_len
    _gc.collect()
    alive = sum(1 for r in refs if r() is not None)
    host_ok = host_fid in (root.bind("<space>") or "")
    # == 0 exactly since the R3E-1 byte-preserving unbind (empties kept).
    g9 = grown == 0 and alive == 0 and host_ok
    print("SELFTEST guard unbind-on-destroy: script-growth=%d alive=%d/8 "
          "host-bind-intact=%s -> %s"
          % (grown, alive, host_ok, "OK" if g9 else "FAIL"))
    guard2_ok = guard2_ok and g9

    ok = ok and guard2_ok

    # --- breaker round-3 fold guards (2026-07-20) -----------------------------
    guard3_ok = True

    # R3E-2 + R4B3-1: Fit must go BELOW the slider floor for songs longer
    # than ~37 s, the VIEWS must actually honor it (their old 20 px/s floor
    # silently overrode the tab), and it must SURVIVE the slider's clamp
    # write-back (the ~90 ms debounced revert three round-4 legs caught —
    # this guard's round-3 version read _zoom synchronously and stayed
    # green while the feature was broken).
    class _LongModel:
        dur = 200.0
    saved3 = tab._model
    saved3_zoom = tab._zoom
    tab._model = _LongModel()
    tab._on_zoom_fit()
    fit_zoom = tab._zoom
    view3 = tab.lane_view
    vw3 = max(100, view3.canvas.winfo_width() - 4)
    pump(0.4)                               # let any revert debounce fire
    settled_zoom = tab._zoom
    view_pps = view3._pps
    span = settled_zoom * 200.0
    tab._model = saved3
    tab._set_zoom(saved3_zoom)
    g10 = (0 < fit_zoom < ZOOM_MIN
           and abs(fit_zoom - vw3 / 200.0) < 0.5
           and settled_zoom == fit_zoom          # survived the debounce window
           and abs(view_pps - fit_zoom) < 1e-6   # views honor the fit
           and span <= vw3 + 4)                  # the song actually fits
    print("SELFTEST guard fit-zoom long song: fit=%.3f settled=%.3f "
          "view=%.3f span=%.0f/%d -> %s"
          % (fit_zoom, settled_zoom, view_pps, span, vw3,
             "OK" if g10 else "FAIL"))
    guard3_ok = guard3_ok and g10

    # R3B2-2 + R3E-3: a failed play leaves the amber status; the NEXT
    # successful play must replace it; a failed REBUILD mid-play must stop
    # the previous stream.
    st3 = {"ok": False, "stops": 0}
    htab = SpectralTab(root, hooks={
        "get_cfg": lambda k, d=None: d, "set_cfg": lambda k, v: None,
        "mixer_play": lambda p, s, sp: st3["ok"],
        "mixer_stop": lambda: st3.__setitem__("stops", st3["stops"] + 1)})
    htab._model = MockSpectralModel(seed=4)
    htab.reference_field.set(os.path.abspath(__file__))
    htab._start_stream(0.0)
    stat_fail = str(htab.status_lbl.cget("text"))
    st3["ok"] = True
    htab._start_stream(0.0)
    stat_okay = str(htab.status_lbl.cget("text"))
    g11 = "unavailable" in stat_fail and stat_okay.startswith("Playing")
    print("SELFTEST guard stale-status cleared on success: %r -> %r -> %s"
          % (stat_fail[:40], stat_okay[:40], "OK" if g11 else "FAIL"))
    guard3_ok = guard3_ok and g11
    htab._playing = True
    st3["ok"] = False
    stops_before = st3["stops"]
    htab._rebuild_stream_if_playing()
    g12 = st3["stops"] > stops_before
    print("SELFTEST guard failed rebuild stops old stream: stops %d->%d -> %s"
          % (stops_before, st3["stops"], "OK" if g12 else "FAIL"))
    guard3_ok = guard3_ok and g12
    htab._playing = False
    htab.destroy()

    # R3B2-3: the SYNTHETIC compare path must never arm Overwrite MIDI.
    otab = SpectralTab(root, hooks={"get_cfg": lambda k, d=None: d,
                                    "set_cfg": lambda k, v: None})
    # This guard was VACUOUS until 2026-08-01: it only set candidate_field and called
    # _finish_compare(), so _overwrite_target was "" because nothing had EVER armed it
    # -- deleting the protection line in _finish_compare left this printing OK. ARM it
    # first, exactly as a real Compare does, so the assertion has something to clear.
    # The scenario being defended: user Compares their real chart (target armed), then
    # presses "Load demo"; without the clear, Overwrite would replace their chart with
    # synthetic notes after one generic confirm.
    otab.candidate_field.set(os.path.join(os.path.dirname(
        os.path.abspath(__file__)), "some_real_chart.mid"))
    otab._overwrite_target = os.path.join(os.path.dirname(
        os.path.abspath(__file__)), "some_real_chart.mid")
    otab._finish_compare()
    g13 = otab._overwrite_target == ""
    print("SELFTEST guard synthetic path never arms overwrite: %r -> %s"
          % (otab._overwrite_target, "OK" if g13 else "FAIL"))
    guard3_ok = guard3_ok and g13
    otab.destroy()

    ok = ok and guard3_ok

    # --- breaker round-4 fold guards (2026-07-20) -----------------------------
    guard4_ok = True

    # R4B2-2: an armed debounce must not override a later DIRECT set (the
    # direct set cancels it), and nothing fires after.
    ztab = SpectralTab(root, hooks={})
    ztab._on_zoom_scale("700.0")            # arms the 90 ms debounce -> 700
    ztab._set_zoom(300)                     # direct set must cancel it
    pump(0.4)
    g14 = abs(ztab._zoom - 300.0) < 1e-6
    print("SELFTEST guard debounce-vs-direct set: zoom=%.0f -> %s"
          % (ztab._zoom, "OK" if g14 else "FAIL"))
    guard4_ok = guard4_ok and g14

    # R4B3-5: Pause and Stop write their own state lines — "Playing" must
    # never outlive the stream.
    ztab._model = MockSpectralModel(seed=5)
    ztab._on_play()
    ztab._pause()
    st_p = str(ztab.status_lbl.cget("text"))
    ztab._on_play()                         # resume
    st_r = str(ztab.status_lbl.cget("text"))   # R5B1-2: must say Playing again
    ztab._on_stop()
    st_s = str(ztab.status_lbl.cget("text"))
    g15 = (st_p == "Paused." and st_r.startswith("Playing")
           and st_s == "Stopped.")
    print("SELFTEST guard transport state lines: pause=%r resume=%r stop=%r "
          "-> %s" % (st_p, st_r, st_s, "OK" if g15 else "FAIL"))
    guard4_ok = guard4_ok and g15
    ztab.destroy()

    # R5B1-1/R5B2-1/R5E-1: a wheel gesture from a FITTED (below-floor) view
    # must SETTLE below the floor — the round-4 wheel fix's own debounce
    # re-floored it 70 ms later (three legs converged on this).
    wtab = SpectralTab(root, hooks={})
    class _WLong:
        dur = 200.0
    wtab._model = _WLong()
    wtab._on_zoom_fit()
    wfit = wtab._zoom
    wtab._on_wheel_zoom(1)                  # wheel-IN from fit
    pump(0.4)                               # let the debounce fire
    wsettled = wtab._zoom
    g17 = (0 < wfit < ZOOM_MIN
           and abs(wsettled - wfit * 1.12) < 1e-6
           and wsettled < ZOOM_MIN)
    print("SELFTEST guard wheel-from-fit settles below floor: fit=%.3f "
          "settled=%.3f -> %s" % (wfit, wsettled, "OK" if g17 else "FAIL"))
    guard4_ok = guard4_ok and g17
    # R6B2-1: a RAISING mixer_stop hook's amber warning must survive the
    # "Stopped." state line (the round-5 audio-first reorder clobbered it).
    def _boom():
        raise RuntimeError("simulated mixer failure")
    etab = SpectralTab(root, hooks={"mixer_stop": _boom})
    etab._model = MockSpectralModel(seed=6)
    etab._playing = True
    etab._on_stop()
    st_e = str(etab.status_lbl.cget("text"))
    g19 = "hook failed" in st_e
    print("SELFTEST guard raising stop-hook warning survives: %r -> %s"
          % (st_e[:52], "OK" if g19 else "FAIL"))
    guard4_ok = guard4_ok and g19
    etab.destroy()

    # R6B2-2: Play with NO mixer_play hook (standalone/silent clock) must not
    # leave a stale "Paused." asserting pause during live playback.
    ptab = SpectralTab(root, hooks={})
    ptab._model = MockSpectralModel(seed=7)
    ptab._on_play()
    ptab._pause()
    ptab._speed_step(0.25)          # reclassifies paused -> stopped
    ptab._on_play()                 # stopped -> playing branch
    st_np = str(ptab.status_lbl.cget("text"))
    g20 = st_np.startswith("Playing")
    print("SELFTEST guard hookless play clears stale Paused: %r -> %s"
          % (st_np, "OK" if g20 else "FAIL"))
    guard4_ok = guard4_ok and g20
    ptab.destroy()

    # R6B2-3/R6B2-4: an absurd-but-finite dur (int(dur*pps) -> OverflowError)
    # and a denormal-pps SCROLLED ruler must both render without raising.
    # All THREE int(dur*pps) sites, BOTH views, across every pathological dur
    # (breaker R7B2-1, 2026-07-20: the round-6 fold guarded only two sites, and
    # min() is not a NaN filter — gram's _refresh_scrollregion still raised on
    # inf and both views raised on NaN, undoing _build_image's own guard).
    otab = SpectralTab(root, hooks={})
    g21 = True
    for _bad_dur in (1e308, float("inf"), float("nan")):
        for _v in (otab.lane_view, otab.gram_view):
            _hm = MockSpectralModel(seed=8)
            _hm.dur = _bad_dur
            try:
                _v.set_model(_hm)
            except Exception as _e:
                print("SELFTEST guard px_span dur=%r %s raised: %r"
                      % (_bad_dur, type(_v).__name__, _e))
                g21 = False
    # R8B4-1: fmt_time / _update_time_label / _on_stop are a FOURTH _model.dur
    # consumer the px_span render fold didn't reach — +inf dur made fmt_time
    # raise (inf//60 = nan -> int(nan)), freezing the playhead loop and
    # hard-raising _on_stop.
    ttab = SpectralTab(root, hooks={})
    _tm = MockSpectralModel(seed=8)
    _tm.dur = float("inf")
    ttab._model = _tm
    g25 = True
    try:
        _ = fmt_time(float("inf"))
        ttab._update_time_label()
        ttab._playing = True
        ttab._on_stop()
    except Exception as _e:
        print("SELFTEST guard fmt_time +inf raised: %r" % (_e,))
        g25 = False
    print("SELFTEST guard fmt_time/time-label +inf-dur safe: %s"
          % ("OK" if g25 else "FAIL"))
    guard4_ok = guard4_ok and g25
    ttab.destroy()

    # R8B2-1: a non-finite chart BPM (a .rlrr with "bpm": Infinity, which
    # json.load accepts) made snap_time's (60/bpm)/4 collapse to 0.0 and
    # note-edit divide by zero. _safe_bpm at the model boundary must keep
    # snap finite, and bpm=120 must still snap to the grid.
    g26 = True
    _snapvals = []
    # inf/nan/0/neg (R8B2-1) + FINITE-astronomical 1e308 / 1.8e308 (R9-B4-1:
    # these passed the finiteness guard but overflowed round(t/step) at an
    # edit time past ~15 s). Edit at t=30.0 to hit the R9-B4-1 overflow window.
    # inf/nan/0/neg (R8B2-1) + finite-astronomical 1e308/1.8e308 (R9-B4-1,
    # overflow) + denormal-tiny 5e-324 (R9E-1, snap-collapse-to-0). All must
    # fall back to 120 so snap(30.0) lands exactly on 30.0 and edit is safe.
    for _bad_bpm in (float("inf"), float("nan"), 0, -120, 1e308, 1.8e308,
                     5e-324):
        btab = SpectralTab(root, hooks={})
        _bm = MockSpectralModel(seed=8)
        _bm.bpm = _bad_bpm
        btab._model = _bm
        try:
            _snapvals.append(btab.lane_view.snap_time(30.0))
            btab._on_lane_edit(30.0, 2)      # the real crash path (snap on)
        except Exception as _e:
            print("SELFTEST guard bpm=%r edit raised: %r" % (_bad_bpm, _e))
            g26 = False
        btab.destroy()
    # bpm=120 must still quantize (0.125 s grid at 120 BPM 16ths)
    ntab = SpectralTab(root, hooks={})
    _nm = MockSpectralModel(seed=8)
    _nm.bpm = 120.0
    ntab._model = _nm
    snapped = ntab.lane_view.snap_time(1.20)
    # every bad bpm -> 120 fallback -> snap(30.0) lands exactly on 30.0
    # (step 0.125); a live 120 model snaps 1.20 -> 1.25.
    g26 = g26 and abs(snapped - 1.25) < 1e-9 and all(
        abs(s - 30.0) < 1e-9 for s in _snapvals)
    # R10-B2-1: a bpm the MIDI exporter can't encode (< ~3.58) must be
    # rejected by _safe_bpm so export never surfaces a raw mido tempo error.
    g26 = g26 and _safe_bpm(2.0) == 120.0 and _safe_bpm(3.5) == 120.0 \
        and _safe_bpm(4.0) == 4.0 and _safe_bpm(23.0947) == 23.0947
    print("SELFTEST guard non-finite bpm edit-safe + snaps: vals=%r 120->%.3f "
          "-> %s" % (_snapvals, snapped, "OK" if g26 else "FAIL"))
    guard4_ok = guard4_ok and g26
    ntab.destroy()

    huge = MockSpectralModel(seed=8)
    huge.dur = 1e308                       # finite, passes every dur guard
    try:
        otab.lane_view.set_model(huge)
        otab.gram_view.set_model(huge)
        otab.lane_view._pps = 5e-324
        otab.lane_view._draw_ruler(500, 900)      # x0 > 0 => x0/pps = inf
        otab.gram_view._eff_pps = 5e-324
        otab.gram_view._draw_ruler(500, 900)
    except Exception as _e:
        print("SELFTEST guard overflow raised: %r" % (_e,))
        g21 = False
    print("SELFTEST guard dur/pps overflow safe: %s" % ("OK" if g21 else "FAIL"))
    guard4_ok = guard4_ok and g21
    otab.destroy()

    # R6E-2: stem load/clear WHILE PLAYING must rebuild the stream exactly
    # ONCE per click (the Segmented auto-select used to fire its own rebuild
    # on top of the handler's) — AND the R3E-3a ordering must still hold, i.e.
    # a FAILED rebuild's amber warning must survive the info line.
    s6 = {"plays": 0, "ok": True}
    def _s6play(p, s, sp):
        s6["plays"] += 1
        return s6["ok"]
    stab = SpectralTab(root, hooks={
        "get_cfg": lambda k, d=None: d, "set_cfg": lambda k, v: None,
        "mixer_play": _s6play, "mixer_stop": lambda: None})
    stab._model = MockSpectralModel(seed=9)
    stab.reference_field.set(os.path.abspath(__file__))
    # The stem must be a DIFFERENT real file from the reference, or the played
    # source never changes and the R7E-2 skip correctly suppresses the rebuild.
    _stem_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "parakit_spectral_engine.py")
    stab._playing = True
    _real_ask = filedialog.askopenfilename
    filedialog.askopenfilename = lambda **kw: _stem_path
    try:
        s6["plays"] = 0
        stab._on_stem_browse()
        browse_plays = s6["plays"]
        s6["plays"] = 0
        stab._on_stem_clear()
        clear_plays = s6["plays"]
        # failure path: the amber warning must be the LAST word
        s6["ok"] = False
        stab._playing = True
        stab._on_stem_browse()
        fail_status = str(stab.status_lbl.cget("text"))
    finally:
        filedialog.askopenfilename = _real_ask
    g22 = (browse_plays == 1 and clear_plays == 1
           and "unavailable" in fail_status)
    print("SELFTEST guard stem rebuild once per click: browse=%d clear=%d "
          "fail-status=%r -> %s"
          % (browse_plays, clear_plays, fail_status[:34],
             "OK" if g22 else "FAIL"))
    guard4_ok = guard4_ok and g22

    # R7E-2: stem ops that change NOTHING must not restart playback — while a
    # real source change still must. (The guard lives at the stem call sites,
    # not in _rebuild_stream_if_playing: speed/render changes legitimately
    # rebuild the same source.)
    s6["ok"] = True
    stab._playing = True
    filedialog.askopenfilename = lambda **kw: _stem_path
    try:
        s6["plays"] = 0
        stab._on_stem_browse()          # SAME path already loaded + Drums
        same_browse = s6["plays"]
        s6["plays"] = 0
        stab._on_stem_clear()           # real change (Drums -> Mix)
        real_clear = s6["plays"]
        s6["plays"] = 0
        stab._on_stem_clear()           # nothing loaded: no change
        empty_clear = s6["plays"]
    finally:
        filedialog.askopenfilename = _real_ask
    g23 = same_browse == 0 and real_clear == 1 and empty_clear == 0
    print("SELFTEST guard no-change stem ops don't restart: same-browse=%d "
          "real-clear=%d empty-clear=%d -> %s"
          % (same_browse, real_clear, empty_clear, "OK" if g23 else "FAIL"))
    guard4_ok = guard4_ok and g23

    # R7E-1: selecting "Full Mix" with NO mix loaded must snap back and say so —
    # never display Full Mix while silently playing the drums.
    stab._playing = False
    stab._stem_seg.set(1)               # user click, fire=True
    lie = (stab._stem_seg.get() == 1
           and stab._play_source() != stab.stem_field.get())
    st_lie = str(stab.status_lbl.cget("text"))
    g24 = stab._stem_seg.get() == 0 and "No full mix" in st_lie and not lie
    print("SELFTEST guard FullMix-without-mix snaps back: seg=%d status=%r "
          "-> %s" % (stab._stem_seg.get(), st_lie[:38], "OK" if g24 else "FAIL"))
    guard4_ok = guard4_ok and g24
    stab.destroy()

    # R5B2-2: external_stop on a DESTROYED tab must still reach the mixer hook.
    wcalls = {"stop": 0}
    wtab.hooks = {"mixer_stop": lambda: wcalls.__setitem__("stop",
                                                           wcalls["stop"] + 1)}
    wtab._playing = True
    wtab.destroy()
    # B-DEST-1 (2026-07-21): destroy() itself now stops the mixer (reaches the
    # hook once). Isolate the POST-destroy explicit call — the original R5B2-2
    # intent — by resetting the counter after destroy.
    g18_destroy_stopped = wcalls["stop"] >= 1     # destroy stopped audio (B-DEST-1)
    wcalls["stop"] = 0
    wtab.external_stop()
    g18 = wcalls["stop"] == 1 and g18_destroy_stopped
    print("SELFTEST guard destroyed-tab external_stop reaches hook: %d "
          "(destroy-stopped=%s) -> %s"
          % (wcalls["stop"], g18_destroy_stopped, "OK" if g18 else "FAIL"))
    guard4_ok = guard4_ok and g18

    # R4B3-2: the ruler must be pixel-bounded, not O(dur) — at the collapsed
    # eff_pps of a dur=1e6 song (0.0082 px/s) the old loop drew ~100k items.
    gv4 = tab.gram_view
    saved_pps4 = gv4._eff_pps
    gv4._eff_pps = 0.0082
    n0 = len(gv4.canvas.find_all())
    t0 = time.time()
    gv4._draw_ruler(0, max(200, gv4.canvas.winfo_width()))
    dt4 = time.time() - t0
    added4 = len(gv4.canvas.find_all()) - n0
    gv4._eff_pps = saved_pps4
    gv4._redraw_now()
    lv4 = tab.lane_view
    saved_lpps4 = lv4._pps
    lv4._pps = 0.0082
    n0l = len(lv4.canvas.find_all())
    lv4._draw_ruler(0, max(200, lv4.canvas.winfo_width()))
    addedl4 = len(lv4.canvas.find_all()) - n0l
    lv4._pps = saved_lpps4
    lv4._redraw_now()
    # R5B2-3: the dur=1e100 collapse shape (eff ~8e-97) must stay bounded —
    # the old 1e-9 floor in the step math hung the redraw here.
    gv4._eff_pps = 8.192e-97
    n0h = len(gv4.canvas.find_all())
    t0h = time.time()
    gv4._draw_ruler(0, max(200, gv4.canvas.winfo_width()))
    dth = time.time() - t0h
    addedh = len(gv4.canvas.find_all()) - n0h
    gv4._eff_pps = saved_pps4
    gv4._redraw_now()
    g16 = (added4 < 200 and dt4 < 0.3 and addedl4 < 200
           and addedh < 10 and dth < 0.3)
    print("SELFTEST guard ruler pixel-bounded: gram %d items/%.0f ms, "
          "lane %d items -> %s"
          % (added4, dt4 * 1000, addedl4, "OK" if g16 else "FAIL"))
    guard4_ok = guard4_ok and g16

    ok = ok and guard4_ok
    print("SELFTEST %s" % ("OK" if ok else "FAIL"))
    return 0 if ok else 1


def main(argv):
    root = tk.Tk()
    apply_theme_global(root)
    root.title("Spectral Comparison \u2014 TTK redesign (standalone review)")
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    w = max(1040, min(1900, int(sw * 0.85)))
    h = max(720, min(1250, int(sh * 0.85)))
    root.geometry("%dx%d" % (w, h))
    root.minsize(1040, 720)
    tab = SpectralTab(root)
    tab.pack(fill=tk.BOTH, expand=True)

    if "--selftest" in argv:
        return _selftest(root, tab)
    for arg in argv:
        if arg.startswith("--autoclose="):
            root.after(int(arg.split("=", 1)[1]), root.destroy)
    if "--no-demo" not in argv:
        root.after(400, tab._on_compare)
    if "--start-gram" in argv:
        root.after(500, lambda: tab.view_seg.set(1))
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
