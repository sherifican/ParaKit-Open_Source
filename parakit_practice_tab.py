"""parakit_practice_tab -- the Practice gameplay half of ParaKit's Practice tab.

`class PracticeTab(ttk.Frame)` is a SCREEN ROUTER (Home -> Play -> Results)
plus the Play/Results/KitStudio/overlay screen classes it composes. It mirrors
`parakit_spectral_tab.py`'s sidecar shape: an `__init__(self, parent,
hooks=None, **kw)` with the three-helper hook seam (`_cfg_get` / `_cfg_set` /
`_hook_call`), self-contained `Prac.*` theming, a host-callable
`external_stop()`, and a standalone `main()`.

This is Chunk A of `DISPATCH_practice_preview_sol.md` -- it does NOT edit
`ParaKit v4.0.py`; the v4 host wiring (the `mixer_*`/`synth_*`/`midi_*`
closures) is the coordinator's Chunk B. Everything audio/synth/MIDI here is
routed through `_hook_call` and DEGRADES cleanly when absent:

  * mixer_* absent  -> the highway runs on a SILENT INTERNAL CLOCK
    (`now = anchor + wall*speed`), so play still animates and scores.
  * synth_* absent  -> hits/metronome are silent (a one-line status note).
  * midi_*  absent  -> MIDI reads "none"; play/score run fully via keyboard.

Behaviour is a 1:1 Tkinter port of the v5 PySide6 Practice widgets
(`parakit_v5/ui/tabs/practice.py` + `ui/widgets/practice/*`). The pure game
core (scoring/routing/parse) is Kimi's `parakit_practice_engine`; the shared
widgets + palette are Kimi's `parakit_practice_widgets`; the Home screen is
Kimi's `parakit_practice_home`. This module imports all three and NEVER
re-implements scoring.

DIVERGENCES from the v5 renderer (documented, not hidden): Tkinter's Canvas
has no per-item alpha, no radial-gradient fill, and no additive-composite
blend. Alpha is approximated by blending the colour toward the highway
background; the note "gradient" fill is a flat lane colour; particle
"additive" glow is a plain blended fill. Geometry, the 9-glyph vocabulary,
the fall window, the velocity scaling, and every game formula are 1:1.

Stdlib + tkinter/ttk only. No numpy, no PySide6, no third-party deps.

PUBLIC API the v4 wiring uses:
    PracticeTab(parent, hooks=None)   -- construct (embedded or standalone)
    tab.external_stop()               -- stop playback/timers/threads (tab change)
    tab.receive(kind, payload)        -- host handoff; kind "load_chart" supported
"""
from __future__ import annotations

import functools
import math
import os
import random
import sys
import time
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Any, Callable, Dict, List, Optional, Tuple

from parakit_practice_widgets import (
    AMBER,
    BG,
    CHIP_OFF_EDGE,
    CYAN,
    DARKER,
    F_BASE,
    F_BOLD,
    F_H1,
    F_MONO,
    F_MONO_B,
    F_SMALL,
    GREEN,
    INPUT_BG,
    INPUT_FG,
    MAGENTA,
    MUTED,
    PANEL,
    PURPLE,
    PURPLE_DEEP,
    PURPLE_EDGE,
    PURPLE_LT,
    ROW_ALT,
    SEPARATOR,
    TEXT,
    Chip,
    OutlineButton,
    PlaceholderEntry,
    Segmented,
    ToggleSwitch,
    Tooltip,
    apply_theme_embedded,
    apply_theme_global,
    blend,
    _round_rect_points,
)

import parakit_practice_engine as engine

# v4.9.1 — optional HQ glyph-sprite sidecar for the falling-note highway
# (Pillow gradient+glow sprites of the 9-glyph vocabulary, arbitrary Kit-Studio
# colors). Missing/broken => None and _paint_notes keeps its flat-vector draw.
# Guarded so the Practice tab can never fail to load over a sprite/Pillow issue.
try:
    import parakit_practice_sprites as _psprites
except Exception:
    _psprites = None
from parakit_practice_engine import (
    ACCENT_VEL,
    BUILTIN_PRESETS,
    CLASS_ROUTES,
    COMBO_MIN,
    CORE_VOICES,
    DEFAULT_KEYBINDS,
    DEFAULT_PREFS,
    DIFFICULTIES,
    GHOST_VEL,
    HIGHWAY_BG,
    KB_ACCENT_VEL,
    KB_BASE_VEL,
    KEYBIND_PRESETS,
    LANE_DEFS,
    MAX_LANES,
    PALETTE,
    PALETTES,
    PRESET_FOLDS,
    SHAPES,
    STANDARD_ORDER,
    WINDOW_MODES,
    Session,
    apply_layout_snapshot,
    build_lane_notes,
    compute_grade,
    contrast_ratio,
    decode_chart_bytes,
    default_layout,
    demo_chart,
    lane_color,
    parse_rlrr,
    resolve_routing,
    route_for_class,
    snapshot_layout,
    song_duration_from_lanes,
    velocity_flash_scale,
    windows_for,
)
from parakit_practice_home import PracticeHomeScreen, _key_label

# ---------------------------------------------------------------------------
# Renderer palette + constants (ported from highway_view.py; alpha values are
# the source rgba() alphas, applied here via _blend toward the highway bg).
# ---------------------------------------------------------------------------
_HW_BG = HIGHWAY_BG                       # "#17171B"
_PAD_TOP = 8.0
_MIRROR_H = 46.0
_TAP_VEL = 105
_TICK_MS = 16
_GRACE_S = 0.7
_LOOP_GRACE_S = 0.25
# Minimum A->B loop length. A zero-length (A==B) or sub-frame loop makes the
# tick wrap `now >= loop_b` fire every frame -> a seek-to-A every 16 ms (score
# farming + a mixer_seek storm). Reject degenerate loops (review #3, 2026-07-21).
_MIN_LOOP_S = 0.25

_POPUP_COLORS = {"miss": "#FF7B6B", "perfect": "#00e5ff",
                 "early": "#9FB6C4", "late": "#9FB6C4"}
_MILESTONE_COLORS = ("#9F67FF", "#00e5ff", "#C4B5FD", "#EFECFF")
_LABEL_KEYS = "#beb9aa"

_DOCK_KEYS = {"1": "youDrum", "2": "metronome", "3": "autoKick",
              "4": "beatGrid", "5": "kickLine", "6": "square",
              "m": "muteSynth", "8": "compact"}

# Dedicated bindtag for the in-game key handler. Inserted at the FRONT of the
# toplevel's bindtags so the drum handler fires BEFORE any app-level single-key
# shortcut bound to the toplevel (the MIDI Editor binds <s>/<S>/<d>/<D>/<p>/<t>/
# <b> to root; a *specific* <s> shadows our *generic* <KeyPress> on the SAME
# tag, silently eating Crash(S)/Snare(D)). Our handler returns "break" to stop
# the shadowing shortcut. Owner-reported; rebinding those lanes to unshadowed
# keys (X/C) was the tell.
_PRACTICE_KEY_TAG = "ParaKitPracticeKeys"

# Minimum ms between two same-key presses for the second to count as a distinct
# hit (rather than an OS auto-repeat). Tk exposes no e.repeat flag like the
# HTML/v5 reference, so a boolean "is-held" guard alone drops genuine fast
# re-taps whenever a KeyRelease lags/coalesces under render+audio load — the
# rate-limited-roll bug (owner-reported 2026-07-23: rolls capped ~one tone at a
# time). Windows steady auto-repeat lands at the system repeat rate (default
# KeyboardSpeed=31 -> ~33ms), while a human single-key roll never re-taps this
# fast (45ms == 22 Hz ceiling), so the interval cleanly separates the two.
KB_REPEAT_GAP_MS = 45


def _sanitize_binds(raw, preset=None):
    """Coerce a persisted ``binds`` value to a valid ``[{code:str, lane:str}]``
    list, or fall back to the active keyboard preset's binds (v5 parity fix,
    2026-07-21) and finally ``DEFAULT_KEYBINDS`` (breaker fix P1, 2026-07-21).
    A corrupt persisted pref -- a bare string, a list of strings, or dicts
    missing ``lane``/``code`` -- used to reach ``b["lane"]`` at the bind-chip /
    Kit-Studio / capture / view-build sites and crash the whole Practice tab
    build (Settings is built eagerly in ``PracticeTab.__init__``). Contract:
    degrade, never crash. Every reader routes ``binds`` through here.

    ``preset``: the ``kbPreset`` pref id. Mirrors the HTML's
    ``prefs.binds || KEYBIND_PRESETS[prefs.kbPreset].binds`` (:5525) — before
    this, Sticking-Focus/Lefty silently behaved as Standard whenever no custom
    binds were stored, i.e. "the keys don't do what the preset says"."""
    out = []
    if isinstance(raw, list):
        for b in raw:
            if not isinstance(b, dict):
                continue
            code, lane = b.get("code"), b.get("lane")
            if isinstance(code, str) and isinstance(lane, str):
                out.append({"code": code, "lane": lane})
    if out:
        return out
    p = KEYBIND_PRESETS.get(str(preset or ""))
    if p and p.get("binds"):
        return [dict(b) for b in p["binds"]]
    return list(DEFAULT_KEYBINDS)


def _finite_or(v, default):
    """Coerce v to a finite float or return default (breaker fix F2, 2026-07-21).
    A host mixer hook (mixer_song_time / mixer_audio_duration) that returned a
    dict/None crashed a bare float(), and a NaN/inf slipped through to poison
    the clock or the session length. Every numeric hook result routes here."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default

#: tk keysym -> the JS KeyboardEvent.code vocabulary the binds use
_PUNCT_CODES = {"semicolon": "Semicolon", "comma": "Comma", "period": "Period",
                "slash": "Slash", "apostrophe": "Quote", "grave": "Backquote",
                "bracketleft": "BracketLeft", "bracketright": "BracketRight",
                "backslash": "Backslash", "minus": "Minus", "equal": "Equal"}

_VOICE_CHOICES = ["hihat", "crash", "crash13", "china", "snare", "tom1", "tom2",
                  "tom3", "ride", "kick", "tambourine", "triangle", "neutral"]

_CFG_PREFS_KEY = "practice_prefs"
_CFG_KIT_KEY = "practice_kit_layout"
_CFG_PINS_KEY = "practice_kit_pins"


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _fmt_time(t: float) -> str:
    t = max(0.0, t)
    return f"{int(t // 60)}:{int(t % 60):02d}"


def _tk_code(event) -> str:
    """tk key event -> JS KeyboardEvent.code ('a' -> 'KeyA', space -> 'Space')."""
    ks = event.keysym
    if len(ks) == 1 and ks.isalpha():
        return "Key" + ks.upper()
    if len(ks) == 1 and ks.isdigit():
        return "Digit" + ks
    if ks == "space":
        return "Space"
    return _PUNCT_CODES.get(ks.lower(), "")


def _blend_a(color: str, alpha: float, bg: str = _HW_BG) -> str:
    """Approximate an alpha-over-bg composite (Tk Canvas has no alpha)."""
    return blend(bg, color, _clamp(alpha, 0.0, 1.0))


# ===========================================================================
# Glyph drawing on a tk.Canvas (the 9-shape vocabulary, geometry 1:1 with
# highway_view._glyph_path). All items are tagged so the dynamic layer can be
# cleared per frame; the caller supplies fill/outline/width/dash.
# ===========================================================================
def _glyph_draw(cv: tk.Canvas, glyph: str, x: float, y: float, r: float,
                w: float, *, fill: str = "", outline: str = "", width: float = 1,
                dash=None, tags: str = "dyn") -> None:
    kw: Dict[str, Any] = {"tags": tags}
    if dash:
        kw["dash"] = dash
    if glyph == "lozenge":
        cv.create_oval(x - r * 1.35, y - r * 0.62, x + r * 1.35, y + r * 0.62,
                       fill=fill, outline=outline, width=width, **kw)
    elif glyph == "lozengeS":
        cv.create_oval(x - r * 1.05, y - r * 0.5, x + r * 1.05, y + r * 0.5,
                       fill=fill, outline=outline, width=width, **kw)
    elif glyph == "roundrect":
        pts = _round_rect_points(x - w / 2, y - r * 0.62, x + w / 2,
                                 y + r * 0.62, 5)
        cv.create_polygon(pts, smooth=True, fill=fill, outline=outline,
                          width=width, **kw)
    elif glyph == "bar":
        pts = _round_rect_points(x - w / 2, y - r * 0.42, x + w / 2,
                                 y + r * 0.42, 4)
        cv.create_polygon(pts, smooth=True, fill=fill, outline=outline,
                          width=width, **kw)
    elif glyph == "diamond":
        cv.create_polygon(x, y - r, x + r * 0.95, y, x, y + r, x - r * 0.95, y,
                          fill=fill, outline=outline, width=width, **kw)
    elif glyph == "spike":
        k = r * 1.2
        cv.create_polygon(x - k, y + r * 0.5, x - k * 0.3, y - r * 0.55,
                          x, y + r * 0.1, x + k * 0.3, y - r * 0.55,
                          x + k, y + r * 0.5,
                          fill=fill, outline=outline, width=width, **kw)
    elif glyph == "ring":
        ow = max(2.0, r * 0.45)
        cv.create_oval(x - r, y - r, x + r, y + r, fill="",
                       outline=(outline or fill or "#ffffff"), width=ow, **kw)
    elif glyph == "triangle":
        cv.create_polygon(x, y - r, x + r * 0.95, y + r * 0.8,
                          x - r * 0.95, y + r * 0.8,
                          fill=fill, outline=outline, width=width, **kw)
    else:  # circle
        cv.create_oval(x - r, y - r, x + r, y + r,
                       fill=fill, outline=outline, width=width, **kw)


# ===========================================================================
# _AudioClock -- the transport. Prefers the mixer_* hooks when present; else a
# silent internal clock (now = anchor + wall*speed). Exposes the engine-shaped
# surface the play screen calls (song_time / play / pause / resume / seek /
# set_speed / has_bus / stop / audio_duration / speed).
# ===========================================================================
class _AudioClock:
    def __init__(self, hook_call: Callable[..., Tuple[bool, Any]],
                 note: Callable[[str], None]) -> None:
        self._hook = hook_call
        self._note = note
        self.speed = 1.0
        self._anchor_song = 0.0
        self._anchor_wall = time.perf_counter()
        self._running = False
        self._want_play = False   # play-INTENT (True between play/resume and pause/stop)
        self._last_song = 0.0     # last good mixer reading (hand-off on demote)
        self._noted = False
        ok, _ = self._hook("mixer_song_time")
        self._use_mixer = ok

    def _n(self) -> None:
        if not self._noted:
            self._noted = True
            self._note("Audio wired at integration -- highway on the silent clock.")

    # ----- transport -----
    def load_stems(self, stems: Any) -> None:
        self._hook("mixer_load_stems", stems)

    def play(self, from_song: float) -> None:
        self.speed = self.speed or 1.0
        self._want_play = True
        if self._use_mixer:
            # Branch on the mixer's OWN success bool, not hook-presence (breaker
            # fix, 2026-07-21): _hook_call returns (True, retval) whenever the
            # hook merely EXISTS, so the old `if ok:` treated a FAILED mixer_play
            # (return False = the documented "use the silent clock" signal) as
            # success — the clock then read a frozen mixer time and the highway
            # HUNG (the song never completed).
            ok, res = self._hook("mixer_play", from_song, self.speed)
            if ok and res:
                return
            self._use_mixer = False   # mixer failed -> silent clock from here
        self._anchor_song = from_song
        self._anchor_wall = time.perf_counter()
        self._running = True
        self._n()

    def pause(self) -> None:
        self._want_play = False
        if self._use_mixer:
            self._hook("mixer_pause")
            return
        self._anchor_song = self.song_time()
        self._running = False

    def resume(self) -> None:
        self._want_play = True
        if self._use_mixer:
            self._hook("mixer_unpause")
            return
        self._anchor_wall = time.perf_counter()
        self._running = True

    def stop(self) -> None:
        self._want_play = False
        if self._use_mixer:
            self._hook("mixer_stop")
        self._running = False

    def free_stems(self) -> None:
        self._hook("mixer_free_stems")

    def seek(self, t: float) -> None:
        if self._use_mixer:
            # Same fix as play(): honor the mixer's success bool. A rejected seek
            # (False) used to be treated as done, so the Session jumped to `t`
            # while the audio/clock stayed put and every note in between was
            # silently consumed. On failure, drop to the silent clock re-anchored
            # at t (resuming at the current play-intent so it never freezes).
            ok, res = self._hook("mixer_seek", t)
            if ok and res:
                return
            self._use_mixer = False
            self._running = self._want_play
        self._anchor_song = t
        self._anchor_wall = time.perf_counter()

    def set_speed(self, s: float) -> None:
        if not self._use_mixer:
            self._anchor_song = self.song_time()
            self._anchor_wall = time.perf_counter()
        self.speed = s
        self._hook("mixer_set_speed", s)

    def song_time(self) -> float:
        if self._use_mixer:
            ok, val = self._hook("mixer_song_time")
            fv = _finite_or(val, None) if (ok and val is not None) else None
            if fv is not None:                 # a dict/NaN/inf clock -> demote
                self._last_song = fv           # cache for the demote hand-off
                return self._last_song
            # Mixer clock unavailable -> fall to the silent clock, resuming from
            # the LAST GOOD mixer reading at the current play-intent (breaker fix
            # r2: r1 left _anchor_song at its stale 0.0, so a mid-play demote
            # jumped the highway BACKWARD to ~0s; and a False _running froze it).
            self._use_mixer = False
            self._anchor_song = self._last_song
            self._running = self._want_play
            self._anchor_wall = time.perf_counter()
        if not self._running:
            return self._anchor_song
        return self._anchor_song + (time.perf_counter() - self._anchor_wall) * self.speed

    def has_bus(self, bus: str) -> bool:
        ok, val = self._hook("mixer_has_bus", bus)
        return bool(ok and val)

    def set_bus_gain(self, bus: str, gain: float) -> None:
        self._hook("mixer_set_bus_gain", bus, gain)

    @property
    def audio_duration(self) -> float:
        ok, val = self._hook("mixer_audio_duration")
        return float(val) if ok and val else 0.0


class _Synth:
    """Thin wrapper over the synth_* hooks; silent + one note when absent."""

    def __init__(self, hook_call, note) -> None:
        self._hook = hook_call
        self._note = note
        self._noted = False
        self.available = self._hook("synth_ensure_rendered")[0]

    def _n(self) -> None:
        if not self._noted and not self.available:
            self._noted = True
            self._note("Drum synth wired at integration -- hits are silent for now.")

    def play(self, voice: str, vel: int) -> None:
        ok, _ = self._hook("synth_play", voice, vel)
        if not ok:
            self._n()

    def met_click(self, accent: bool) -> None:
        self._hook("synth_met_click", accent)

    def set_muted(self, muted: bool) -> None:
        self._hook("synth_set_muted", muted)


# ===========================================================================
# PracticeHighway -- the falling-note renderer on ONE tk.Canvas. Static layer
# (wells/labels/keycaps) is tagged "static" and rebuilt only on resize/view
# change; everything else is tagged "dyn" and redrawn per ~16 ms tick with
# visible-window culling ([now-0.18, now+fall+0.05]).
# ===========================================================================
class PracticeHighway(tk.Canvas):
    def __init__(self, parent, on_lane_tapped: Callable[[int, int], None]) -> None:
        super().__init__(parent, background=_HW_BG, highlightthickness=0, bd=0)
        self._on_lane_tapped = on_lane_tapped
        self._view: dict = {}
        self._session: Optional[Session] = None
        self._now = 0.0
        self._beats: List[dict] = []
        self._loop: Tuple[Optional[float], Optional[float]] = (None, None)
        self._combo_shown = False
        self._fx_on = True
        self._cols: List[dict] = []
        self._kick_col: Optional[dict] = None
        self._kick_line_active = False
        self._static_key: tuple = ()
        self._popups: List[dict] = []
        self._particles: List[dict] = []
        self._shake = 0.0
        self._combo_pulse = 0.0
        self._mirror_flash: Dict[int, float] = {}
        self._rng = random.Random()
        self._last_drawn = 0
        self.bind("<Button-1>", self._on_click)
        self.bind("<Configure>", self._on_configure)

    # ----- wiring -----
    def set_view(self, view: dict) -> None:
        self._view = dict(view)
        self._fx_on = bool(view.get("fx", True)) and not view.get("reduceMotion")
        self._static_key = ()
        self.delete("static")

    def set_session(self, session) -> None:
        self._session = session
        self.clear_fx()

    def clear_fx(self) -> None:
        self._popups.clear()
        self._particles.clear()
        self._shake = 0.0
        self._combo_pulse = 0.0
        self._mirror_flash.clear()

    @property
    def last_drawn(self) -> int:
        return self._last_drawn

    def render_frame(self, now, dt, beats, loop_a, loop_b, combo_shown) -> None:
        self._now = now
        self._beats = beats or []
        self._loop = (loop_a, loop_b)
        self._combo_shown = combo_shown
        self._advance_fx(dt)
        self._paint()

    # ----- FX -----
    def add_event(self, ev: dict) -> None:
        lane = ev.get("lane", -1)
        grade = ev.get("grade", "")
        self._mirror_flash[lane] = ev.get("t", self._now)
        col = self._col_for_lane(lane)
        if col is not None:
            x = col["x"]
        elif (self._kick_line_active and self._kick_col
              and lane == self._view.get("kick_idx")):
            x = self._kick_col["x"]
        else:
            x = self.winfo_width() / 2
        y = self._hit_y()
        if grade == "miss":
            if self._fx_on:
                self._burst(x, y, "#E04545", 5, 70, down=True, alpha=1.0)
                self._add_shake(0.12)
            self._add_popup(x, y - 34, "MISS", _POPUP_COLORS["miss"], 0.7)
            return
        a_mul, s_mul = velocity_flash_scale(ev.get("vel", 90) or 90)
        color = _POPUP_COLORS["perfect"] if grade == "perfect" else "#9FB6C4"
        if self._fx_on:
            count = round(9 * s_mul) if grade == "perfect" else 5
            self._burst(x, y, color, count, 110 * s_mul, down=False, alpha=a_mul)
        life = 0.6 if grade == "perfect" else 0.5
        self._add_popup(x, y - 34, grade.upper(),
                        _POPUP_COLORS.get(grade, "#9FB6C4"), life)

    def milestone_burst(self) -> None:
        self._combo_pulse = 0.5
        if not self._fx_on:
            return
        g = self._geometry()
        cx = g["pad_x"] + g["play_w"] / 2
        cy = self.winfo_height() * 0.38
        for i in range(40):
            ang = self._rng.random() * math.tau
            sp = 110 + self._rng.random() * 210
            self._particles.append({
                "x": cx, "y": cy, "vx": math.cos(ang) * sp,
                "vy": math.sin(ang) * sp,
                "life": 0.6 + self._rng.random() * 0.5, "max": 1.1,
                "size": 2.5 + self._rng.random() * 3,
                "color": _MILESTONE_COLORS[i % len(_MILESTONE_COLORS)],
                "alpha": 1.0})
        del self._particles[:-300]
        self._add_shake(0.45)

    def _advance_fx(self, dt: float) -> None:
        for p in self._popups:
            p["life"] -= dt
            p["y"] += p["vy"] * dt
            p["vy"] *= (1 - dt * 1.5)
        self._popups = [p for p in self._popups if p["life"] > 0]
        for pt in self._particles:
            pt["life"] -= dt
            pt["x"] += pt["vx"] * dt
            pt["y"] += pt["vy"] * dt
            pt["vy"] += 340 * dt
        self._particles = [p for p in self._particles if p["life"] > 0]
        if self._shake > 0:
            self._shake = max(0.0, self._shake - dt * 3.6)
        if self._combo_pulse > 0:
            self._combo_pulse = max(0.0, self._combo_pulse - dt)

    def _add_popup(self, x, y, text, color, life) -> None:
        self._popups.append({"x": x, "y": y, "text": text, "color": color,
                             "life": life, "max": life,
                             "vy": -52.0 if self._fx_on else 0.0})
        del self._popups[:-36]

    def _burst(self, x, y, color, count, speed, down, alpha) -> None:
        for _ in range(max(0, int(count))):
            ang = ((math.pi / 2 + (self._rng.random() - 0.5) * 1.0) if down
                   else (-math.pi / 2 + (self._rng.random() - 0.5) * 2.2))
            sp = speed * (0.4 + self._rng.random() * 0.9)
            self._particles.append({
                "x": x, "y": y, "vx": math.cos(ang) * sp,
                "vy": math.sin(ang) * sp,
                "life": 0.45 + self._rng.random() * 0.4, "max": 0.85,
                "size": 2 + self._rng.random() * 3, "color": color,
                "alpha": alpha})
        del self._particles[:-300]

    def _add_shake(self, a: float) -> None:
        if self._fx_on:
            self._shake = min(1.0, max(self._shake, a))

    # ----- geometry -----
    def _geometry(self) -> dict:
        v = self._view
        w_px = max(1, self.winfo_width())
        h_px = max(1, self.winfo_height())
        width_frac = float(v.get("width_frac", 0.82))
        play_w = max(120.0, w_px * width_frac)
        cx = w_px * float(v.get("pos_x", 0.5))
        pad_x = max(4.0, min(w_px - play_w - 4.0, cx - play_w / 2))
        return {"pad_x": pad_x, "play_w": play_w,
                "hit_y": h_px * float(v.get("hit_line_frac", 0.78)),
                "note_scale": float(v.get("note_scale", 1.0))}

    def _hit_y(self) -> float:
        return max(1, self.winfo_height()) * float(self._view.get("hit_line_frac", 0.78))

    def _rebuild_columns(self) -> None:
        v = self._view
        lanes = v.get("lanes", [])
        gap = float(v.get("lane_gap", 4))
        g = self._geometry()
        kick_idx = v.get("kick_idx", -1)
        self._kick_line_active = bool(
            v.get("kick_line") and 0 <= kick_idx < len(lanes)
            and lanes[kick_idx].get("visible", True))
        vis = [i for i, ln in enumerate(lanes) if ln.get("visible", True)]
        col_lanes = [i for i in vis
                     if not (self._kick_line_active and i == kick_idx)]
        total_w = sum(float(lanes[i].get("widthW", 1.0)) for i in col_lanes) or 1.0
        inner_w = g["play_w"] - gap * max(0, len(col_lanes) - 1)
        self._cols = []
        x = g["pad_x"]
        square = bool(v.get("square"))
        self._classic_style = bool(v.get("classicStyle"))   # v4.9.2 note style
        for i in col_lanes:
            ln = lanes[i]
            w = inner_w * float(ln.get("widthW", 1.0)) / total_w
            shape = "bar" if square else ln.get("shape", "circle")
            self._cols.append({"lane_idx": i, "x": x + w / 2, "w": w,
                               "color": ln.get("color", "#ffffff"),
                               "shape": shape, "label": ln.get("label", ""),
                               "bind_label": ln.get("bind_label", "")})
            x += w + gap
        self._kick_col = None
        if self._kick_line_active:
            ln = lanes[kick_idx]
            self._kick_col = {"lane_idx": kick_idx,
                              "x": g["pad_x"] + g["play_w"] / 2,
                              "w": g["play_w"], "color": ln.get("color", "#ff69b4"),
                              "shape": "bar", "label": ln.get("label", "Kick")}

    def _col_for_lane(self, lane_idx: int) -> Optional[dict]:
        for c in self._cols:
            if c["lane_idx"] == lane_idx:
                return c
        return None

    def _y_of(self, t: float) -> float:
        fall = float(self._view.get("fall_time", 2.6)) or 2.6
        hit_y = self._hit_y()
        return hit_y - ((t - self._now) / fall) * (hit_y - _PAD_TOP)

    def _note_radius(self, col_w: float) -> float:
        return min(col_w * 0.3, 21.0) * self._geometry()["note_scale"]

    # ----- input -----
    def _on_click(self, event) -> None:
        x = event.x
        for c in self._cols:
            if abs(x - c["x"]) <= c["w"] / 2:
                self._on_lane_tapped(c["lane_idx"], _TAP_VEL)
                return
        if self._kick_line_active and self._kick_col is not None:
            self._on_lane_tapped(self._kick_col["lane_idx"], _TAP_VEL)

    def _on_configure(self, _event=None) -> None:
        self._static_key = ()
        self.delete("static")

    # ----- painting -----
    def _paint(self) -> None:
        self.delete("dyn")
        if not self._view:
            return
        key = (self.winfo_width(), self.winfo_height())
        if self._static_key != key:
            self._rebuild_columns()
            self._build_static()
            self._static_key = key
        g = self._geometry()
        self._paint_loop_region(g)
        self._paint_beat_grid(g)
        self._paint_hit_line(g)
        drawn = 0
        if self._session is not None:
            drawn += self._paint_kick_line_notes(g)
            drawn += self._paint_notes(g)
            self._paint_receptors(g)
            self._paint_mirror_pads(g)
            if self._combo_shown and self._session.streak >= COMBO_MIN:
                self._paint_combo(g)
        self._last_drawn = drawn
        self._paint_particles()
        self._paint_popups()
        if self._shake > 0:
            ox = (self._rng.random() - 0.5) * self._shake * 8
            oy = (self._rng.random() - 0.5) * self._shake * 8
            self.move("dyn", ox, oy)

    def _build_static(self) -> None:
        g = self._geometry()
        hit_y = g["hit_y"]
        compact = bool(self._view.get("compact"))
        well_a = float(self._view.get("well_opacity", 0.06))
        for c in self._cols:
            self.create_rectangle(c["x"] - c["w"] / 2, 0, c["x"] + c["w"] / 2,
                                  hit_y, fill=_blend_a(c["color"], well_a * 1.6),
                                  outline="", tags="static")
            sep = _blend_a("#c4b5fd", 0.07)
            self.create_line(c["x"] - c["w"] / 2, 0, c["x"] - c["w"] / 2,
                             hit_y + 6, fill=sep, tags="static")
            self.create_line(c["x"] + c["w"] / 2, 0, c["x"] + c["w"] / 2,
                             hit_y + 6, fill=sep, tags="static")
            if not compact:
                self.create_text(c["x"], hit_y + _MIRROR_H + 20,
                                 text=c["label"].upper(), fill=_blend_a(c["color"], 0.85),
                                 font=F_MONO_B, tags="static")
                if c["bind_label"]:
                    self.create_text(c["x"], hit_y + _MIRROR_H + 36,
                                     text=c["bind_label"], fill=_LABEL_KEYS,
                                     font=F_SMALL, tags="static")
        self.tag_lower("static")

    def _paint_loop_region(self, g: dict) -> None:
        a, b = self._loop
        if a is None or b is None:
            return
        y_b = max(_PAD_TOP, min(g["hit_y"], self._y_of(b)))
        y_a = max(_PAD_TOP, min(g["hit_y"], self._y_of(a)))
        if y_a > y_b:
            self.create_rectangle(g["pad_x"], y_b, g["pad_x"] + g["play_w"], y_a,
                                  fill=_blend_a(CYAN, 0.07), outline="", tags="dyn")

    def _paint_beat_grid(self, g: dict) -> None:
        for b in self._beats:
            y = self._y_of(b["t"])
            if y < _PAD_TOP or y > g["hit_y"]:
                continue
            if b.get("accent"):
                col, wd = _blend_a("#7c3aed", 0.34), 2
            else:
                col, wd = _blend_a("#c4b5fd", 0.16), 1
            self.create_line(g["pad_x"], y, g["pad_x"] + g["play_w"], y,
                             fill=col, width=wd, tags="dyn")

    def _paint_hit_line(self, g: dict) -> None:
        y = g["hit_y"]
        self.create_line(g["pad_x"], y, g["pad_x"] + g["play_w"], y,
                         fill=_blend_a("#7c3aed", 0.22), width=7, tags="dyn")
        self.create_line(g["pad_x"], y, g["pad_x"] + g["play_w"], y,
                         fill=_blend_a("#efecff", 0.55), width=2, tags="dyn")

    def _visible_notes(self, lane_notes: List) -> List:
        fall = float(self._view.get("fall_time", 2.6)) or 2.6
        t0 = self._now - 0.18
        t1 = self._now + fall + 0.05
        out = []
        # Lane lists are time-sorted (parakit_practice_engine.build_lane_notes).
        # Jump to the first note with time >= t0 instead of walking the whole past
        # of the chart every frame (was O(notes-already-passed) per lane per frame,
        # stuttering late in dense charts). Same notes, same order as the linear
        # scan that continued past time < t0; no cross-frame state, so seek/rewind/
        # loop are unaffected.
        lo, hi = 0, len(lane_notes)
        while lo < hi:
            mid = (lo + hi) // 2
            if lane_notes[mid].time < t0:
                lo = mid + 1
            else:
                hi = mid
        for i in range(lo, len(lane_notes)):
            n = lane_notes[i]
            if n.time > t1:
                break
            if n.hit or n.grade == "miss":
                continue
            out.append(n)
        return out

    def _paint_kick_line_notes(self, g: dict) -> int:
        if not self._kick_line_active or self._session is None:
            return 0
        kc = self._kick_col
        lane = kc["lane_idx"]
        if lane >= self._session.lane_count:
            return 0
        drawn = 0
        h_base = max(3.0, 4.0 * g["note_scale"])
        for n in self._visible_notes(self._session.lane_notes[lane]):
            y = self._y_of(n.time)
            if y < _PAD_TOP - 30:
                continue
            v_scale = 0.85 + min(1.0, n.vel / 110.0) * 0.4
            h = h_base * v_scale
            self.create_rectangle(g["pad_x"], y - h / 2, g["pad_x"] + g["play_w"],
                                  y + h / 2, fill=_blend_a(kc["color"], 0.85),
                                  outline="", tags="dyn")
            drawn += 1
        return drawn

    def _paint_notes(self, g: dict) -> int:
        drawn = 0
        for c in self._cols:
            if c["lane_idx"] >= self._session.lane_count:
                continue
            base_r = self._note_radius(c["w"])
            for n in self._visible_notes(self._session.lane_notes[c["lane_idx"]]):
                y = self._y_of(n.time)
                if y < _PAD_TOP - 30:
                    continue
                v_scale = max(0.7, 0.85 + min(1.0, n.vel / 110.0) * 0.4)
                r = base_r * v_scale
                w = c["w"] * 0.7 * v_scale
                # HQ sprite branch (v4.9.1): one gradient+glow image per note
                # (ghost/accent/normal state), blitted in place of the flat
                # vector glyph + its extra accent-ring items. Cache is size-
                # bucketed so gameplay never renders PIL per frame. A sprite
                # failure per note degrades to the vector draw below.
                if _psprites is not None:
                    st = ("ghost" if n.vel < GHOST_VEL
                          else ("accent" if n.vel >= ACCENT_VEL else "normal"))
                    try:
                        spr = _psprites.glyph_sprite(
                            c["color"], c["shape"], int(round(r)), int(round(w)),
                            st, master=self,
                            style=("classic" if getattr(self, "_classic_style",
                                                        False) else "hq"))
                        self.create_image(c["x"], y, image=spr, tags="dyn")
                        drawn += 1
                        continue
                    except Exception:
                        pass    # fall through to the vector draw
                if n.vel < GHOST_VEL:
                    _glyph_draw(self, c["shape"], c["x"], y, r, w,
                                fill=_blend_a(c["color"], 0.30),
                                outline=_blend_a(c["color"], 0.92), width=2,
                                dash=(4, 3))
                else:
                    _glyph_draw(self, c["shape"], c["x"], y, r, w,
                                fill=c["color"], outline=_blend_a("#ffffff", 0.6),
                                width=1)
                    if n.vel >= ACCENT_VEL:
                        _glyph_draw(self, c["shape"], c["x"], y, r + 5, w + 10,
                                    fill="", outline=_blend_a(c["color"], 0.85),
                                    width=2)
                        _glyph_draw(self, c["shape"], c["x"], y, r, w, fill="",
                                    outline=_blend_a("#ffffff", 0.95), width=2)
                drawn += 1
        return drawn

    def _paint_receptors(self, g: dict) -> None:
        y = g["hit_y"]
        ses = self._session
        for c in self._cols:
            r = self._note_radius(c["w"])
            w = c["w"] * 0.7
            _glyph_draw(self, c["shape"], c["x"], y, r, w, fill="",
                        outline=_blend_a(c["color"], 0.5), width=2)
            if c["lane_idx"] < ses.lane_count:
                dt_hit = self._now - ses.last_lane_hit_time[c["lane_idx"]]
                if 0 <= dt_hit <= 0.15:
                    k = 1 - dt_hit / 0.15
                    a_mul, s_mul = velocity_flash_scale(
                        ses.last_hit_velocity[c["lane_idx"]])
                    if not self._fx_on:
                        s_mul = 1.0
                    _glyph_draw(self, c["shape"], c["x"], y, r * s_mul,
                                w * s_mul,
                                fill=_blend_a(c["color"], min(1.0, k * 0.8 * a_mul)),
                                outline="")

    def _paint_mirror_pads(self, g: dict) -> None:
        y = g["hit_y"] + 12 + _MIRROR_H / 2
        for c in self._cols:
            lit = False
            t = self._mirror_flash.get(c["lane_idx"])
            if t is not None and 0 <= self._now - t < 0.18:
                lit = True
            x0 = c["x"] - c["w"] * 0.36
            x1 = c["x"] + c["w"] * 0.36
            y0 = y - _MIRROR_H * 0.38
            y1 = y + _MIRROR_H * 0.38
            pts = _round_rect_points(x0, y0, x1, y1, 9)
            if lit:
                self.create_polygon(pts, smooth=True, fill=_blend_a(c["color"], 0.55),
                                    outline=c["color"], width=2, tags="dyn")
            else:
                self.create_polygon(pts, smooth=True, fill=_blend_a("#1c1c20", 0.85),
                                    outline=_blend_a(c["color"], 0.65), width=2,
                                    tags="dyn")
            gr = min(c["w"] * 0.26, 15.0)
            _glyph_draw(self, c["shape"], c["x"], y, gr, c["w"] * 0.42,
                        fill=_blend_a(c["color"], 0.95), outline="")

    def _paint_combo(self, g: dict) -> None:
        cx = g["pad_x"] + g["play_w"] / 2
        cy = self.winfo_height() * 0.36
        pulse = 1 + self._combo_pulse * 0.5
        size = round(min(self.winfo_width(), self.winfo_height()) * 0.085 * pulse)
        self.create_text(cx, cy, text=str(self._session.streak),
                         fill=_blend_a("#efecff", 0.95),
                         font=("Segoe UI", max(10, int(size)), "bold"), tags="dyn")
        off = round(min(self.winfo_width(), self.winfo_height()) * 0.062)
        self.create_text(cx, cy + off, text="C O M B O",
                         fill=_blend_a("#7c3aed", 0.85),
                         font=("Segoe UI", 9, "bold"), tags="dyn")

    def _paint_particles(self) -> None:
        for pt in self._particles:
            a = max(0.0, pt["life"] / pt["max"]) * pt["alpha"]
            s = pt["size"]
            self.create_oval(pt["x"] - s, pt["y"] - s, pt["x"] + s, pt["y"] + s,
                             fill=_blend_a(pt["color"], a), outline="", tags="dyn")

    def _paint_popups(self) -> None:
        for pop in self._popups:
            a = max(0.0, pop["life"] / pop["max"])
            self.create_text(pop["x"], pop["y"], text=pop["text"],
                             fill=_blend_a(pop["color"], a),
                             font=("Segoe UI", 11, "bold"), tags="dyn")


# ===========================================================================
# _ProgressBar -- gradient fill + A-B loop tint/markers + drag-seek.
# ===========================================================================
class _ProgressBar(tk.Canvas):
    def __init__(self, parent, on_scrub_start, on_scrubbed, on_scrub_end) -> None:
        super().__init__(parent, height=22, background=PANEL,
                         highlightthickness=0, bd=0)
        self._start = on_scrub_start
        self._scrubbed = on_scrubbed
        self._end = on_scrub_end
        self._frac = 0.0
        self._loop_a: Optional[float] = None
        self._loop_b: Optional[float] = None
        self._total = 0.0
        self._down = False
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Configure>", lambda e: self._redraw())

    def set_state(self, now, total, loop_a, loop_b) -> None:
        self._total = max(0.001, total)
        self._frac = _clamp(now / self._total, 0.0, 1.0)
        self._loop_a = loop_a
        self._loop_b = loop_b
        self._redraw()

    def _press(self, e):
        self._down = True
        self._start()
        self._emit(e.x)

    def _motion(self, e):
        if self._down:
            self._emit(e.x)

    def _release(self, e):
        if self._down:
            self._down = False
            self._end()

    def _emit(self, x):
        self._scrubbed(_clamp(x / max(1, self.winfo_width()), 0.0, 1.0))

    def _redraw(self):
        self.delete("all")
        w = max(1, self.winfo_width())
        h = self.winfo_height()
        ty = h / 2 - 2.5
        self.create_rectangle(0, ty, w, ty + 5, fill="#232347", outline="")
        fw = w * self._frac
        bands = 24
        for i in range(bands):
            if (i + 1) / bands * w > fw:
                x1 = fw
            else:
                x1 = (i + 1) / bands * w
            x0 = i / bands * w
            if x0 >= fw:
                break
            self.create_rectangle(x0, ty, x1, ty + 5,
                                  fill=blend("#9F67FF", "#00e5ff", i / bands),
                                  outline="")
        if self._loop_a is not None and self._loop_b is not None:
            xa = w * _clamp(self._loop_a / self._total, 0, 1)
            xb = w * _clamp(self._loop_b / self._total, 0, 1)
            self.create_rectangle(min(xa, xb), ty, max(xa, xb), ty + 5,
                                  fill=_blend_a(CYAN, 0.45, PANEL), outline="")
        for mark in (self._loop_a, self._loop_b):
            if mark is not None:
                x = w * _clamp(mark / self._total, 0, 1)
                self.create_rectangle(x - 1, ty - 4, x + 1, ty + 10,
                                      fill="#5FD9E8", outline="")
        kx = w * self._frac
        self.create_rectangle(kx - 6.5, h / 2 - 6.5, kx + 6.5, h / 2 + 6.5,
                              fill="#EFECFF", outline="#9F67FF", width=2)


# ===========================================================================
# _LiveDock -- the in-play "Live settings" panel (toggle with H). 15 rows.
# ===========================================================================
_DOCK_TOGGLES = (("youDrum", "1", "You Drum (Mute on Miss)"), ("metronome", "2", "Metronome"),
                 ("autoKick", "3", "Auto-kick"), ("beatGrid", "4", "Beat grid"),
                 ("kickLine", "5", "Kick as line"), ("square", "6", "Square notes"),
                 ("muteSynth", "M", "Mute synth"),
                 ("compact", "8", "Compact lanes"))
_DOCK_ACTIONS = (("speed-", "−", "Speed down"), ("speed+", "+", "Speed up"),
                 ("loopA", "[", "Loop A at playhead"),
                 ("loopB", "]", "Loop B at playhead"),
                 ("loopX", "\\", "Clear loop"), ("hide", "H", "Hide this panel"))


class _LiveDock(tk.Frame):
    def __init__(self, parent, on_toggle, on_speed, on_loop_a, on_loop_b,
                 on_loop_clear, on_hide) -> None:
        super().__init__(parent, background=PANEL, highlightthickness=1,
                         highlightbackground=PURPLE_DEEP)
        self._on_toggle = on_toggle
        self._on_speed = on_speed
        self._on_loop_a = on_loop_a
        self._on_loop_b = on_loop_b
        self._on_loop_clear = on_loop_clear
        self._on_hide = on_hide
        self._states: Dict[str, tk.Label] = {}
        tk.Label(self, text="LIVE SETTINGS", background=PANEL, foreground=PURPLE_LT,
                 font=F_BOLD).pack(anchor=tk.W, padx=12, pady=(9, 4))
        for row_id, cap, label in _DOCK_TOGGLES + _DOCK_ACTIONS:
            self._make_row(row_id, cap, label)

    def _make_row(self, row_id, cap, label):
        row = tk.Frame(self, background=PANEL, cursor="hand2")
        row.pack(fill=tk.X, padx=8, pady=1)
        keycap = tk.Label(row, text=cap, background=DARKER, foreground=CYAN,
                          font=F_MONO, width=2, padx=3, highlightthickness=1,
                          highlightbackground=PURPLE_EDGE)
        keycap.pack(side=tk.LEFT)
        tk.Label(row, text=label, background=PANEL, foreground=TEXT,
                 font=F_SMALL).pack(side=tk.LEFT, padx=(7, 0))
        state = tk.Label(row, text="", background=PANEL, foreground=MUTED,
                         font=F_MONO_B)
        state.pack(side=tk.RIGHT)
        self._states[row_id] = state
        handler = functools.partial(self._clicked, row_id)
        for wgt in (row, keycap):
            wgt.bind("<Button-1>", handler)

    def _clicked(self, row_id, _event=None):
        if row_id == "speed-":
            self._on_speed(-0.05)
        elif row_id == "speed+":
            self._on_speed(+0.05)
        elif row_id == "loopA":
            self._on_loop_a()
        elif row_id == "loopB":
            self._on_loop_b()
        elif row_id == "loopX":
            self._on_loop_clear()
        elif row_id == "hide":
            self._on_hide()
        else:
            self._on_toggle(row_id)

    def set_state(self, prefs, speed):
        for key, _c, _l in _DOCK_TOGGLES:
            on = bool(prefs.get(key))
            lbl = self._states[key]
            lbl.configure(text="ON" if on else "OFF",
                          foreground=GREEN if on else MUTED)
        self._states["speed-"].configure(text=f"{round(speed * 100)}%",
                                          foreground=CYAN)


# ===========================================================================
# PlayScreen -- the gameplay screen (highway + HUD + frame loop + input).
# ===========================================================================
class PlayScreen(tk.Frame):
    def __init__(self, parent, *, hook_call, note, on_finished, on_exit,
                 on_kit_studio, on_calibrate, on_mixer, on_pref_changed) -> None:
        super().__init__(parent, background=_HW_BG)
        self._hook_call = hook_call
        self._note = note
        self._on_finished = on_finished
        self._on_exit = on_exit
        self._on_kit_studio = on_kit_studio
        self._on_calibrate = on_calibrate
        self._on_mixer = on_mixer
        self._on_pref_changed = on_pref_changed

        self._engine: Optional[_AudioClock] = None
        self._synth: Optional[_Synth] = None
        self._current: Optional[dict] = None
        self._prefs: Dict[str, Any] = {}
        self._view: dict = {}
        self._binds: List[dict] = []
        self._bind_of: Dict[str, dict] = {}
        self._lane_idx_of: Dict[str, int] = {}
        self._running = False
        self._paused = False
        self._scrubbing = False
        # Song position of the last frame painted while paused; lets the paused
        # tick skip the ~60 fps highway rebuild unless the position moved (seek).
        self._paused_paint_t = None
        self._finished = False
        self._end_time = 0.0
        self._event_cursor = 0
        self._loop_a: Optional[float] = None
        self._loop_b: Optional[float] = None
        self._grace_until = -1.0
        self._last_frame = 0.0
        self._miss_duck = False
        self._milestone_seen = -1.0
        self._last_beat: Optional[tuple] = None
        self._hud_cache: Dict[str, str] = {}
        self._studio = False
        self._studio_from_library = False
        self._pressed: set = set()
        # keysym -> event.time (ms) of its last accepted/seen press; feeds the
        # interval-based auto-repeat filter in _on_key_press (KB_REPEAT_GAP_MS).
        self._last_press_time: Dict[str, int] = {}

        self._tick_job = None
        self._count_jobs: List[Any] = []
        self._pending_begin_from = 0.0
        self._fps_n = 0
        self._fps_t0 = 0.0

        self.highway = PracticeHighway(self, self._on_lane_tapped)
        self.highway.place(x=0, y=0, relwidth=1.0, relheight=1.0)
        self._build_hud()

    # ----- HUD -----
    def _build_hud(self) -> None:
        # stats glass (top-left)
        self.stats = tk.Frame(self, background=_blend_a(PANEL, 0.9),
                              highlightthickness=1, highlightbackground=PURPLE_DEEP)
        self._stat_vals: Dict[str, tk.Label] = {}
        for r, (key, label) in enumerate((("score", "Score"), ("streak", "Streak"),
                                          ("hits", "Hits"), ("miss", "Misses"),
                                          ("acc", "Accuracy"))):
            tk.Label(self.stats, text=label, background=self.stats.cget("background"),
                     foreground=MUTED, font=F_SMALL).grid(row=r, column=0,
                                                          sticky="w", padx=(11, 14), pady=1)
            val = tk.Label(self.stats, text="0",
                           background=self.stats.cget("background"),
                           foreground=TEXT, font=F_MONO_B)
            val.grid(row=r, column=1, sticky="e", padx=(0, 11))
            self._stat_vals[key] = val
        self.stats.place(x=12, y=12)

        # song pill (top-center)
        self.song_pill = tk.Frame(self, background=PANEL, highlightthickness=1,
                                 highlightbackground=PURPLE_DEEP)
        self.song_title = tk.Label(self.song_pill, text="", background=PANEL,
                                  foreground=PURPLE_LT, font=F_H1)
        self.song_title.pack(padx=16, pady=(6, 0))
        self.song_sub = tk.Label(self.song_pill, text="", background=PANEL,
                                foreground=MUTED, font=F_SMALL)
        self.song_sub.pack(padx=16, pady=(0, 6))

        # buttons (top-right)
        self.btns = tk.Frame(self, background=_HW_BG)
        self.pause_btn = OutlineButton(self.btns, "Pause", accent=PURPLE_EDGE,
                                      command=self._on_pause_clicked)
        self.pause_btn.pack(side=tk.LEFT, padx=3)
        OutlineButton(self.btns, "Kit Studio", accent=PURPLE_EDGE,
                     command=self._on_studio_clicked).pack(side=tk.LEFT, padx=3)
        OutlineButton(self.btns, "Fullscreen", accent=PURPLE_EDGE,
                     command=self._on_fullscreen).pack(side=tk.LEFT, padx=3)
        OutlineButton(self.btns, "Exit", accent=MAGENTA,
                     command=self._on_exit_clicked).pack(side=tk.LEFT, padx=3)

        # progress row (bottom)
        self.progress_row = tk.Frame(self, background=_HW_BG)
        self.t_now = tk.Label(self.progress_row, text="0:00", background=_HW_BG,
                             foreground=MUTED, font=F_MONO)
        self.t_now.pack(side=tk.LEFT, padx=(0, 10))
        self.t_total = tk.Label(self.progress_row, text="0:00", background=_HW_BG,
                               foreground=MUTED, font=F_MONO)
        self.t_total.pack(side=tk.RIGHT, padx=(10, 0))
        self.progress = _ProgressBar(self.progress_row, self._on_scrub_started,
                                    self._on_scrubbed, self._on_scrub_ended)
        self.progress.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # perf HUD
        self.perf_hud = tk.Label(self, text="", background=_HW_BG,
                                foreground=CYAN, font=F_MONO)

        # live dock
        self.dock = _LiveDock(self, self._on_dock_toggle, self.nudge_speed,
                             functools.partial(self.set_loop_point, "A"),
                             functools.partial(self.set_loop_point, "B"),
                             self.clear_loop, self.toggle_dock)

        # pause overlay
        self.pause_overlay = tk.Frame(self, background=_blend_a("#000000", 0.62))
        panel = tk.Frame(self.pause_overlay, background=PANEL, highlightthickness=1,
                        highlightbackground=PURPLE_EDGE)
        panel.place(relx=0.5, rely=0.5, anchor="center")
        tk.Label(panel, text="PAUSED", background=PANEL, foreground=PURPLE_LT,
                 font=("Segoe UI", 16, "bold")).pack(padx=40, pady=(20, 12))
        for text, cmd, accent in (("Resume", self._on_resume_clicked, MAGENTA),
                                   ("Restart", self.restart_session, PURPLE_EDGE),
                                   ("Kit Studio", self._on_studio_clicked, PURPLE_EDGE),
                                   ("Mixer & offset…", self._on_mixer, PURPLE_EDGE),
                                   ("Calibrate latency…", self._on_calibrate, PURPLE_EDGE),
                                   ("Quit to library", self._on_exit_clicked, PURPLE_EDGE)):
            OutlineButton(panel, text, accent=accent, command=cmd).pack(
                fill=tk.X, padx=24, pady=3)
        tk.Frame(panel, background=PANEL, height=12).pack()

        # count-in overlay
        self.countdown_overlay = tk.Frame(self, background=_blend_a("#000000", 0.5))
        self.cd_num = tk.Label(self.countdown_overlay, text="", background=self.countdown_overlay.cget("background"),
                              foreground="#efecff", font=("Segoe UI", 96, "bold"))
        self.cd_num.place(relx=0.5, rely=0.45, anchor="center")
        tk.Label(self.countdown_overlay, text="C O U N T - I N",
                 background=self.countdown_overlay.cget("background"),
                 foreground=PURPLE_LT, font=F_BOLD).place(relx=0.5, rely=0.6, anchor="center")

        self.bind("<Configure>", self._relayout)

    def _relayout(self, _event=None) -> None:
        w, h = max(1, self.winfo_width()), max(1, self.winfo_height())
        self.song_pill.place_configure(relx=0.5, y=12, anchor="n")
        self.btns.place_configure(x=w - 12, y=12, anchor="ne")
        self.progress_row.place_configure(x=20, y=h - 34, width=w - 40)
        self.perf_hud.place_configure(x=12, y=h - 62)
        self.dock.place_configure(x=w - 242, y=64, width=230,
                                  height=min(520, max(160, h - 110)))
        # v4.9.1 FIX (owner: "song starts, then the PAUSED menu opens by itself
        # after the countdown"): _relayout runs on EVERY <Configure> (resize),
        # and place_configure on a place_forget()'d widget RE-PLACES it. So the
        # pause overlay that start_session hid was resurrected by the first
        # resize event after the count-in — the game was actually running
        # underneath a stuck PAUSED overlay. Only re-size these overlays when
        # they are ALREADY visible; never resurrect a hidden one.
        # winfo_manager()=="place" means it is CURRENTLY placed (shown); a
        # place_forget()'d overlay returns "" and is left hidden. (Precise vs
        # winfo_ismapped, which also flips false on any transient unmapped
        # ancestor and would drop a legitimately-shown overlay's resize.)
        if self.pause_overlay.winfo_manager() == "place":
            self.pause_overlay.place_configure(x=0, y=0, relwidth=1, relheight=1)
        if self.countdown_overlay.winfo_manager() == "place":
            self.countdown_overlay.place_configure(x=0, y=0, relwidth=1, relheight=1)

    # ----- session lifecycle -----
    def start_session(self, current, prefs, view, binds) -> None:
        self._current = current
        self._engine = _AudioClock(self._hook_call, self._note)
        self._synth = _Synth(self._hook_call, self._note)
        self._prefs = prefs
        # Apply the Mute-synth pref to the freshly-built synth NOW. It was only
        # pushed on toggle, so a session that started with Mute-synth ON still
        # played the synth until you toggled it off/on (owner-reported).
        self._synth.set_muted(bool(prefs.get("muteSynth", False)))
        self._view = view
        self._binds = list(binds)
        self._bind_of = {b["code"]: b for b in self._binds}
        order = current["resolved"]["activeOrder"]
        self._lane_idx_of = {lid: i for i, lid in enumerate(order)}
        self._end_time = float(current.get("end_time")
                              or current["session"].song_duration)
        self._event_cursor = 0
        self._loop_a = None
        self._loop_b = None
        self._finished = False
        self._miss_duck = False
        self._milestone_seen = -1.0
        self._hud_cache.clear()
        self._engine.set_speed(float(prefs.get("speed", 1.0) or 1.0))
        self._engine.load_stems(current.get("stems"))
        # v4.9.1 FIX (owner: "loaded a song, NO audio"): the host's _pp_bus_gain
        # is SHARED across the Preview + Practice tabs, and Preview's default
        # "synth" mode zeroes the song+drums buses at startup. Practice then
        # inherited song=0 and played the backing track at volume 0 (a busy but
        # SILENT channel). Assert OUR own bus gains at every session start so a
        # sibling tab's mixing intent can never mute our song.
        self._apply_bus_gains()
        self.highway.place_configure(x=0, y=0, relwidth=1, relheight=1)
        self.highway.set_view(view)
        self.highway.set_session(current["session"])

        chart = current["chart"]
        title = (current.get("title") or chart["meta"].get("title") or "Practice")
        bpm = chart["bpm"].get("primary")
        speed = float(prefs.get("speed", 1.0) or 1.0)
        sub = f"{current.get('difficulty', '')} · {current['session'].total_notes:,} notes"
        if bpm:
            sub += f" · {bpm:g} BPM"
        if abs(speed - 1.0) > 1e-9:
            sub += f" · {round(speed * 100)}%"
        self.song_title.configure(text=title)
        self.song_sub.configure(text=sub)
        self.t_total.configure(text=_fmt_time(self._end_time))
        self.pause_overlay.place_forget()
        if not bool(prefs.get("hideDock")):
            self._show_dock(True)
        else:
            self._show_dock(False)
        self.set_perf_hud(bool(prefs.get("perfHud")))
        self.update_dock()
        self._relayout()
        self._bind_keys()
        self.start_playback(-self._lead_in())

    def session_rebuilt(self, current, view) -> None:
        self._current = current
        order = current["resolved"]["activeOrder"]
        self._lane_idx_of = {lid: i for i, lid in enumerate(order)}
        self._event_cursor = len(current["session"].events)
        self._end_time = max(self._end_time, float(current.get("end_time") or 0.0))
        self._view = view
        self.highway.set_view(view)
        self.highway.set_session(current["session"])

    def _lead_in(self) -> float:
        return min(float(self._prefs.get("fallTime", 2.6) or 2.6), 2.5)

    def start_playback(self, from_song: float) -> None:
        self._running = True
        self._paused = False
        chart = self._current["chart"]
        count_in = int(self._prefs.get("countIn", 4) or 0)
        if count_in > 0 and chart["bpm"].get("sane"):
            self._engine.seek(from_song)
            self._start_count_in(chart["bpm"]["primary"], count_in, from_song)
        else:
            self._begin(from_song)
        self._last_frame = time.perf_counter()
        self._schedule_tick()

    def _schedule_tick(self) -> None:
        if self._tick_job is None:
            self._tick_job = self.after(_TICK_MS, self._on_tick)

    def _start_count_in(self, bpm, beats, from_song) -> None:
        self._cancel_count_in()
        beat_s = 60.0 / _clamp(bpm or 120.0, 30.0, 300.0)
        self.countdown_overlay.place_configure(x=0, y=0, relwidth=1, relheight=1)
        self.countdown_overlay.tkraise()
        self._pending_begin_from = from_song
        for i in range(beats):
            job = self.after(int(i * beat_s * 1000),
                             functools.partial(self._on_count_beat, i, beats))
            self._count_jobs.append(job)
        done = self.after(int(beats * beat_s * 1000), self._on_count_done)
        self._count_jobs.append(done)

    def _on_count_beat(self, i, beats) -> None:
        self.cd_num.configure(text=str(beats - i))
        if self._synth is not None:
            self._synth.met_click(i == 0)

    def _on_count_done(self) -> None:
        self.countdown_overlay.place_forget()
        self._count_jobs.clear()
        self._paused = False
        self._begin(self._pending_begin_from)

    def _cancel_count_in(self) -> None:
        for job in self._count_jobs:
            try:
                self.after_cancel(job)
            except Exception:
                pass
        self._count_jobs.clear()
        self.countdown_overlay.place_forget()

    @property
    def count_in_active(self) -> bool:
        return bool(self._count_jobs)

    def _begin(self, from_song) -> None:
        self._engine.play(from_song)
        self._grace_until = from_song + _GRACE_S
        self._last_frame = time.perf_counter()

    # ----- transport -----
    def pause_game(self) -> None:
        if self.count_in_active:
            self._cancel_count_in()
            self._paused = True
            self._engine.pause()
            if not self._studio:
                self._show_pause_overlay()
            return
        if self._paused:
            return
        self._paused = True
        self._engine.pause()
        self._show_pause_overlay()

    def resume_game(self, skip_count_in=False) -> None:
        if self.count_in_active:
            return
        self.pause_overlay.place_forget()
        t = self._engine.song_time()
        chart = self._current["chart"]
        count_in = int(self._prefs.get("countIn", 4) or 0)
        if not skip_count_in and count_in > 0 and chart["bpm"].get("sane"):
            self._start_count_in(chart["bpm"]["primary"], min(4, count_in), t)
        else:
            self._paused = False
            self._engine.resume()
            self._grace_until = t + _GRACE_S

    def _show_pause_overlay(self) -> None:
        self.pause_overlay.place_configure(x=0, y=0, relwidth=1, relheight=1)
        self.pause_overlay.tkraise()

    def do_seek(self, t, from_loop=False) -> None:
        t = _clamp(t, -self._lead_in(), self._end_time)
        self._engine.seek(t)
        ses = self._current["session"]
        ses.seek(t)
        self._event_cursor = len(ses.events)
        self._grace_until = t + (_LOOP_GRACE_S if from_loop else _GRACE_S)
        self.highway.clear_fx()
        if not self._scrubbing:
            self.progress.set_state(t, self._end_time, self._loop_a, self._loop_b)
        self.t_now.configure(text=_fmt_time(t))

    def restart_session(self, from_t=None) -> None:
        ses = self._current["session"]
        ses.reset()
        ses.seek(max(0.0, from_t if from_t is not None else 0.0))
        self._event_cursor = 0
        self._finished = False
        self._restore_drums_bus()
        self._engine.set_speed(float(self._prefs.get("speed", 1.0) or 1.0))
        self.highway.clear_fx()
        self.pause_overlay.place_forget()
        self.start_playback(from_t if from_t is not None else -self._lead_in())

    def practice_worst(self, worst) -> None:
        self._loop_a = max(0.0, worst["t0"] - 1)
        self._loop_b = min(self._end_time, worst["t1"] + 1)
        self._on_pref_changed("speed", 0.8)
        self._prefs["speed"] = 0.8
        self.restart_session(self._loop_a)

    def quit_to_library(self) -> None:
        self._cancel_count_in()
        self._running = False
        self._stop_tick()
        if self._engine is not None:
            self._engine.stop()
            self._engine.free_stems()
        self._unbind_keys()
        self.pause_overlay.place_forget()
        self._on_exit()

    def set_loop_point(self, which) -> None:
        t = self._engine.song_time()
        a = max(0.0, t) if which == "A" else self._loop_a
        b = t if which == "B" else self._loop_b
        if a is not None and b is not None:
            if b <= a:
                a, b = b, a
            if b - a < _MIN_LOOP_S:                 # degenerate loop -> reject
                self._note("Loop too short — set A and B further apart.")
                return
        self._loop_a, self._loop_b = a, b
        self._note(f"Loop {which} set")

    def clear_loop(self) -> None:
        self._loop_a = None
        self._loop_b = None
        self._note("Loop cleared")

    def nudge_speed(self, delta) -> None:
        s = _clamp(round((float(self._prefs.get("speed", 1.0)) + delta) * 20) / 20,
                   0.25, 1.5)
        self._prefs["speed"] = s
        self._on_pref_changed("speed", s)
        self._engine.set_speed(s)
        self.update_dock()
        self._note(f"Speed {round(s * 100)}% (pitch shifts with speed)")

    def update_dock(self) -> None:
        self.dock.set_state(self._prefs, float(self._prefs.get("speed", 1.0) or 1.0))

    def set_perf_hud(self, on) -> None:
        if on:
            self.perf_hud.configure(text="… fps")
            self.perf_hud.place_configure(x=12, y=max(1, self.winfo_height()) - 62)
            self.perf_hud.tkraise()
        else:
            self.perf_hud.place_forget()

    def _perf_visible(self) -> bool:
        return bool(self.perf_hud.winfo_ismapped())

    def _show_dock(self, show) -> None:
        if show:
            self.dock.place_configure(x=max(1, self.winfo_width()) - 242, y=64,
                                     width=230, height=min(520, max(160, self.winfo_height() - 110)))
            self.dock.tkraise()
        else:
            self.dock.place_forget()

    def toggle_dock(self) -> None:
        show = not self.dock.winfo_ismapped()
        self._show_dock(show)
        self._on_pref_changed("hideDock", not show)

    def _on_dock_toggle(self, key) -> None:
        self._toggle_pref(key)

    def set_binds(self, binds) -> None:
        self._binds = list(binds)
        self._bind_of = {b["code"]: b for b in self._binds}

    def _toggle_pref(self, key) -> None:
        self._prefs[key] = not bool(self._prefs.get(key))
        self._on_pref_changed(key, self._prefs[key])
        self.update_dock()
        if self._synth is not None and key == "muteSynth":
            self._synth.set_muted(self._prefs[key])
        # v4.9.2 FIX (owner-reported: "drums won't play with You-drum on OR off"):
        # the drums-bus gain follows the You-drum rule (muted while YOU play them,
        # audible otherwise) but was only applied at session start -- toggling
        # You-drum mid-song changed the pref without touching the already-playing
        # stems, so the reference drums could never be turned back on live. Re-push
        # the bus gains now. (Reset the miss-duck flag first so the duck state can't
        # leave drums stuck after an explicit toggle.)
        if key == "youDrum":
            # Mute-on-miss is now folded INTO You-drum (owner: the two were
            # confusing — you needed BOTH off to hear the backing drums). One
            # switch: You-drum ON = you play (backing drums muted, misses give
            # feedback); OFF = listen to the full backing drums, no miss-cut.
            self._prefs["muteOnMiss"] = self._prefs["youDrum"]
            self._miss_duck = False
            self._apply_bus_gains()

    # ----- frame loop -----
    def _on_tick(self) -> None:
        self._tick_job = None
        now_wall = time.perf_counter()
        dt = min(0.05, now_wall - self._last_frame)
        self._last_frame = now_wall
        if not self._running or self._current is None:
            return
        ses = self._current["session"]
        now = self._engine.song_time()
        # While paused behind the static overlay, skip the ~60 fps highway rebuild
        # (render_frame -> _paint -> delete("dyn") + full redraw). Repaint only
        # when the position actually moved -- a paused seek/scrub -- otherwise idle
        # at a slow cadence. The tick loop stays alive (still reschedules), so
        # resume_game / _on_count_done need no change: they rely on it running.
        if self._paused and not self._scrubbing and now == self._paused_paint_t:
            self._tick_job = self.after(120, self._on_tick)
            return
        self._paused_paint_t = now if self._paused else None
        if not self._paused:
            ses.song_time = now
            ses.process_auto_kick(now)
            if now > self._grace_until:
                ses.process_misses(now)
            self._drain_events(ses)
            self._metronome_tick(now)
            if (self._loop_a is not None and self._loop_b is not None
                    and now >= self._loop_b):
                self.do_seek(self._loop_a, from_loop=True)
                now = self._engine.song_time()
            elif (now >= self._end_time and not self._finished and not self._studio):
                self._finished = True
                self._finish("complete")
                return
        beats = self._beats_for(now) if self._prefs.get("beatGrid", True) else []
        self.highway.render_frame(now, dt, beats, self._loop_a, self._loop_b,
                                 combo_shown=not self._paused)
        if (ses.milestone_pulse_at != self._milestone_seen
                and ses.milestone_pulse_at >= 0
                and now - ses.milestone_pulse_at < 0.05):
            self._milestone_seen = ses.milestone_pulse_at
            self.highway.milestone_burst()
        self._update_hud(ses)
        self._fps_n += 1
        if now_wall - self._fps_t0 >= 0.5:
            if self._fps_t0 > 0 and self._perf_visible():
                fps = self._fps_n / (now_wall - self._fps_t0)
                self.perf_hud.configure(
                    text=f"{fps:.0f} fps · {self.highway.last_drawn} notes drawn")
            self._fps_n = 0
            self._fps_t0 = now_wall
        if not self._scrubbing:
            self.progress.set_state(now, self._end_time, self._loop_a, self._loop_b)
            self.t_now.configure(text=_fmt_time(now))
        self._schedule_tick()

    def _drain_events(self, ses) -> None:
        you_drum = bool(self._prefs.get("youDrum", True))
        while self._event_cursor < len(ses.events):
            ev = ses.events[self._event_cursor]
            self._event_cursor += 1
            self.highway.add_event(ev)
            if ev.get("auto") and self._synth is not None:
                if (you_drum or not self._engine.has_bus("drums")) and not self._miss_duck:
                    self._synth.play(self._voice_for_lane(ev["lane"]), 96)
            if ev.get("grade") == "miss":
                if (self._prefs.get("muteOnMiss", True) and self._engine.has_bus("drums")
                        and not self._miss_duck):
                    self._engine.set_bus_gain("drums", 0.0)
                    self._miss_duck = True
            elif self._miss_duck:
                self._restore_drums_bus()

    def _apply_bus_gains(self) -> None:
        """Push THIS tab's intended bus gains to the (shared) host mixer, so a
        sibling tab (Preview's synth-mode song/drums mute) can't leave our song
        muted. Drums follow the You-drum rule (muted while you play them)."""
        if self._engine is None:
            return
        song = float(self._prefs.get("busSong", 1.0) or 1.0)
        master = float(self._prefs.get("busMaster", 0.92) or 0.92)
        drums = (0.0 if self._prefs.get("youDrum", True)
                 else float(self._prefs.get("busDrums", 1.0) or 1.0))
        self._engine.set_bus_gain("song", song)
        self._engine.set_bus_gain("drums", drums)
        self._engine.set_bus_gain("master", master)

    def _restore_drums_bus(self) -> None:
        restore = (0.0 if self._prefs.get("youDrum", True)
                   else float(self._prefs.get("busDrums", 1.0) or 1.0))
        if self._engine is not None:
            self._engine.set_bus_gain("drums", restore)
        self._miss_duck = False

    def _metronome_tick(self, now) -> None:
        if self._synth is None or not self._prefs.get("metronome") or now < 0:
            return
        bpm = self._current["chart"]["bpm"]
        if not bpm.get("sane") or not bpm.get("events"):
            return
        seg = bpm["events"][0]
        for e in bpm["events"]:
            if e["time"] <= now:
                seg = e
            else:
                break
        if now < seg["time"]:
            return
        beat = 60.0 / seg["bpm"]
        k = int((now - seg["time"]) // beat)
        key = (seg["time"], k)
        if key != self._last_beat:
            self._last_beat = key
            self._synth.met_click(k % 4 == 0)

    def _voice_for_lane(self, lane_idx) -> str:
        lanes = self._view.get("lanes", [])
        if 0 <= lane_idx < len(lanes):
            return lanes[lane_idx].get("voice", "neutral")
        return "neutral"

    def _beats_for(self, now) -> List[dict]:
        bpm = self._current["chart"]["bpm"]
        if not bpm.get("sane") or not bpm.get("events"):
            return []
        fall = float(self._prefs.get("fallTime", 2.6) or 2.6)
        t0, t1 = now, now + fall + 0.1
        evs = bpm["events"]
        out: List[dict] = []
        for i, seg in enumerate(evs):
            seg_start = seg["time"]
            seg_end = evs[i + 1]["time"] if i + 1 < len(evs) else t1
            if seg_end < t0 or seg_start > t1:
                continue
            beat = 60.0 / seg["bpm"]
            k = max(0, math.ceil((t0 - seg_start) / beat - 1e-9))
            t = seg_start + k * beat
            while t <= min(seg_end, t1) + 1e-9:
                if t >= t0:
                    out.append({"t": t, "accent": k % 4 == 0})
                k += 1
                t = seg_start + k * beat
        return out

    def _update_hud(self, ses) -> None:
        vals = {"score": f"{ses.score:,}", "streak": str(ses.streak),
                "hits": str(ses.hits), "miss": str(ses.misses),
                "acc": f"{ses.accuracy_pct:g}%"}
        for key, text in vals.items():
            if self._hud_cache.get(key) != text:
                self._hud_cache[key] = text
                self._stat_vals[key].configure(text=text)

    # ----- finishing -----
    def _finish(self, reason) -> None:
        self._running = False
        self._paused = False
        self._stop_tick()
        self._unbind_keys()
        if self._engine is not None:
            self._engine.pause()
        ses = self._current["session"]
        res = ses.results(reason, _clamp(ses.song_time, 0.0, self._end_time))
        letter, sub = compute_grade(res["accuracy_pct"])
        lanes_payload = []
        for i, ln in enumerate(self._view.get("lanes", [])):
            if i >= ses.lane_count:
                break
            st = ses.lane_stats[i]
            lanes_payload.append({"label": ln.get("label", f"Lane {i}"),
                                  "color": ln.get("color", "#ffffff"),
                                  "notes": len(ses.lane_notes[i]),
                                  "hits": st["hits"], "miss": st["miss"],
                                  "off_sum_ms": st["offSumMs"]})
        mode = str(self._prefs.get("windowMode", "standard"))
        payload = {
            "grade": (letter, sub),
            "fc": res["miss_count"] == 0 and res["hits"] > 0,
            "window_label": WINDOW_MODES.get(mode, WINDOW_MODES["standard"])["label"],
            "speed": float(self._prefs.get("speed", 1.0) or 1.0),
            "song_line": f"{self.song_title.cget('text')} — {self.song_sub.cget('text')}",
            "score": ses.score, "accuracy_pct": res["accuracy_pct"],
            "best_streak": res["best_streak"], "perfect": res["perfect_count"],
            "early": res["early_count"], "late": res["late_count"],
            "miss": res["miss_count"], "hits": res["hits"],
            "notes": res["hits"] + res["miss_count"],
            "timing_offsets_ms": list(ses.timing_offsets),
            "hit_window_ms": ses.windows.hit * 1000.0, "lanes": lanes_payload,
            "worst": ses.worst_section(), "exit_reason": reason}
        self._on_finished(payload)

    # ----- input -----
    def hit_lane_id(self, lane_id, vel) -> Optional[str]:
        if not self._running or self._paused or self._current is None:
            return None
        idx = self._lane_idx_of.get(lane_id, -1)
        if idx < 0:
            return None
        return self.hit_lane_idx(idx, vel)

    def hit_lane_idx(self, idx, vel) -> Optional[str]:
        if not self._running or self._paused or self._current is None:
            return None
        latency = float(self._prefs.get("inputLatencyMs", 0) or 0)
        hit_time = self._engine.song_time() + latency / 1000.0
        grade = self._current["session"].judge_hit(idx, hit_time, vel)
        if self._synth is not None and (self._prefs.get("youDrum", True)
                                        or not self._engine.has_bus("drums")):
            self._synth.play(self._voice_for_lane(idx), vel)
        return grade

    def _on_lane_tapped(self, lane_idx, vel) -> None:
        self.hit_lane_idx(lane_idx, vel)

    def _bind_keys(self) -> None:
        # Idempotent (breaker fix r8, 2026-07-21): a double-invoke — a fast
        # double-click on the Results 'Play again'/'Practice worst' buttons (no
        # debounce) — used to OVERWRITE _kp_id/_kr_id and orphan the prior
        # toplevel handler, which then survived _unbind_keys()/external_stop()
        # and kept firing app-wide, accumulating one leak per mash. Clear first.
        self._unbind_keys()
        top = self.winfo_toplevel()
        # High-priority bindtag: fires BEFORE the toplevel's own tag, so drum
        # keys beat app-level single-key shortcuts (MIDI Editor <s>/<d>/…). The
        # handler returns "break" for keys it handles to stop those shortcuts.
        try:
            tags = list(top.bindtags())
            if _PRACTICE_KEY_TAG not in tags:
                top.bindtags([_PRACTICE_KEY_TAG] + tags)
            top.unbind_class(_PRACTICE_KEY_TAG, "<KeyPress>")
            top.unbind_class(_PRACTICE_KEY_TAG, "<KeyRelease>")
            top.bind_class(_PRACTICE_KEY_TAG, "<KeyPress>",
                           self._on_key_press, add="+")
            top.bind_class(_PRACTICE_KEY_TAG, "<KeyRelease>",
                           self._on_key_release, add="+")
        except Exception:
            pass
        # Fallback on the toplevel's own tag (covers focus sitting on a child
        # widget, where the custom tag isn't in the chain) — same as the
        # original bind. s/d are still shadowed there, but focus is pulled to
        # the toplevel below so the high-priority tag is what runs during play.
        self._kp_id = top.bind("<KeyPress>", self._on_key_press, add="+")
        self._kr_id = top.bind("<KeyRelease>", self._on_key_release, add="+")
        # Focus fix (2026-07-21): the browser reference gets window-level keydown
        # for free; in Tk, if focus is sitting on an Entry/Combobox (home search
        # box, sort/speed combos), the guard at the top of _on_key_press swallows
        # EVERY drum key and the session starts "keyboard-dead" until the user
        # happens to click the highway. Pull focus onto the toplevel at bind time
        # so a fresh session always hears the keys.
        try:
            top.focus_set()
        except Exception:
            pass

    def _unbind_keys(self) -> None:
        top = self.winfo_toplevel()
        for seq, attr in (("<KeyPress>", "_kp_id"), ("<KeyRelease>", "_kr_id")):
            fid = getattr(self, attr, None)
            if fid is not None:
                try:
                    top.unbind(seq, fid)
                except Exception:
                    pass
                setattr(self, attr, None)
        # Drop the high-priority bindtag + its class bindings so app-level
        # single-key shortcuts work normally again when not playing.
        try:
            top.unbind_class(_PRACTICE_KEY_TAG, "<KeyPress>")
            top.unbind_class(_PRACTICE_KEY_TAG, "<KeyRelease>")
            top.bindtags([t for t in top.bindtags() if t != _PRACTICE_KEY_TAG])
        except Exception:
            pass
        self._pressed.clear()
        self._last_press_time.clear()

    def _on_key_release(self, event) -> None:
        self._pressed.discard(event.keysym)
        self._last_press_time.pop(event.keysym, None)

    def _on_key_press(self, event) -> None:
        # focus guard: ignore while a text entry has focus
        try:
            fw = self.focus_get()
            if fw is not None and fw.winfo_class() in ("TEntry", "Entry", "TCombobox", "Text"):
                return
        except Exception:
            pass
        # auto-repeat filter. Tk has no e.repeat flag (the HTML/v5 reference
        # gates on `if (e.repeat) return`), so a boolean is-held guard alone
        # DROPS genuine fast re-taps whenever a KeyRelease lags/coalesces under
        # load -> rolls come out rate-limited (owner-reported). Gate on the
        # inter-press interval instead: a gap >= KB_REPEAT_GAP_MS is a distinct
        # human hit and fires even if _pressed is stale; anything faster is OS
        # auto-repeat (see the constant's note) and is swallowed.
        ks0 = event.keysym
        now_ms = int(getattr(event, "time", 0) or 0)
        last_ms = self._last_press_time.get(ks0)
        self._last_press_time[ks0] = now_ms
        if ks0 in self._pressed:
            if last_ms is None or 0 <= (now_ms - last_ms) < KB_REPEAT_GAP_MS:
                return
        else:
            self._pressed.add(ks0)
        code = _tk_code(event)
        ks = event.keysym
        # Every branch that HANDLES a key returns "break" so the event stops
        # here and does NOT fall through to an app-level shortcut on the
        # toplevel (e.g. the MIDI Editor's <s>/<d>). Unhandled keys return None
        # and pass through normally.
        if self.count_in_active:
            if ks in ("Escape", "p", "P"):
                self.pause_game()
            return "break"
        # a drum-bound key is ALWAYS a drum while unpaused (never a shortcut)
        if not self._paused and code and code in self._bind_of:
            b = self._bind_of[code]
            vel = b.get("vel")
            if vel is None:
                shift = bool(event.state & 0x0001)
                vel = KB_ACCENT_VEL if shift else KB_BASE_VEL
            self.hit_lane_id(b["lane"], int(vel))
            return "break"
        if ks == "Escape":
            self.quit_to_library() if self._paused else self.pause_game()
            return "break"
        if ks in ("p", "P"):
            self.resume_game() if self._paused else self.pause_game()
            return "break"
        if ks in ("k", "K"):
            if not self._paused:
                self.pause_game()
            self._on_kit_studio()
            return "break"
        if ks == "Left":
            self.do_seek(self._engine.song_time() - 5)
            return "break"
        if ks == "Right":
            self.do_seek(self._engine.song_time() + 5)
            return "break"
        if ks == "bracketleft":
            self.set_loop_point("A")
            return "break"
        if ks == "bracketright":
            self.set_loop_point("B")
            return "break"
        if ks == "backslash":
            self.clear_loop()
            return "break"
        if ks in ("equal", "plus"):
            self.nudge_speed(+0.05)
            return "break"
        if ks == "minus":
            self.nudge_speed(-0.05)
            return "break"
        if ks == "F11":
            self._on_fullscreen()
            return "break"
        if ks in ("h", "H"):
            self.toggle_dock()
            return "break"
        if ks == "F3":
            on = not self._perf_visible()
            self.set_perf_hud(on)
            self._on_pref_changed("perfHud", on)
            return "break"
        if ks.lower() in _DOCK_KEYS:
            self._toggle_pref(_DOCK_KEYS[ks.lower()])
            return "break"
        return None

    # ----- HUD slots -----
    def _on_pause_clicked(self):
        self.resume_game() if self._paused else self.pause_game()

    def _on_resume_clicked(self):
        self.resume_game()

    def _on_studio_clicked(self):
        if not self._paused:
            self.pause_game()
        self._on_kit_studio()

    def _on_fullscreen(self):
        win = self.winfo_toplevel()
        try:
            win.attributes("-fullscreen", not bool(win.attributes("-fullscreen")))
        except Exception:
            pass

    def _on_exit_clicked(self):
        self.quit_to_library()

    def _on_scrub_started(self):
        self._scrubbing = True

    def _on_scrubbed(self, frac):
        self.do_seek(frac * self._end_time)

    def _on_scrub_ended(self):
        self._scrubbing = False

    def _stop_tick(self) -> None:
        if self._tick_job is not None:
            try:
                self.after_cancel(self._tick_job)
            except Exception:
                pass
            self._tick_job = None

    def external_stop(self) -> None:
        self._cancel_count_in()
        self._stop_tick()
        self._unbind_keys()
        self._running = False
        if self._engine is not None:
            try:
                self._engine.stop()
            except Exception:
                pass


# ===========================================================================
# ResultsScreen -- grade header, 4-up cards, timing histogram + Apply, per-lane
# table, actions.  v3 HTML layout (#scrResults) + vertical scroll so short
# windows never clip the lane table / action buttons.
# ===========================================================================
_HIST_BINS = 31
# Breakdown swatches (match v3 HTML judgment colours — category chips only).
_C_PERFECT = "#9F67FF"
_C_EARLY = "#a78bfa"   # lavender (no yellow) — matches the early hist bars
_C_LATE = "#7c3aed"    # purple (no yellow) — matches the late hist bars
_C_MISS = "#e94560"
# Timing histogram BAR fills — purple / lavender / cyan ONLY (never yellow).
_HIST_EARLY = "#a78bfa"   # early (negative) bars
_HIST_LATE = "#7c3aed"    # late (positive) bars
_HIST_ZERO = "#c4b5fd"    # 0 ms centre line label colour base
_GRADE_DEFAULT = "#C4B5FD"  # #rGrade default (ember2)
_GRADE_FC = "#00e5ff"       # #rGrade.fc (steel) — literal matches HTML


def _js_round(x: float) -> int:
    return math.floor(x + 0.5)


def _signed_ms(value: float) -> str:
    n = _js_round(value) if value >= 0 else -_js_round(-value)
    return f"+{n}" if n >= 0 else str(n)


class _Histogram(tk.Canvas):
    """Timing offset histogram (−window … +window ms). Bars are purple-family
    only (constraint: never yellow). Layout 1:1 with v3 drawHistogram()."""

    def __init__(self, parent) -> None:
        super().__init__(parent, height=170, background=PANEL,
                         highlightthickness=0, bd=0)
        self._offsets: List[float] = []
        self._perfect = 35.0
        self._range = 75.0
        self.bind("<Configure>", lambda e: self._redraw())

    def set_data(self, offsets, perfect_ms, range_ms) -> None:
        self._offsets = list(offsets)
        self._perfect = perfect_ms if perfect_ms > 0 else 35.0
        self._range = range_ms if range_ms > 0 else 75.0
        self._redraw()

    def _redraw(self) -> None:
        self.delete("all")
        w = max(1, self.winfo_width())
        h = self.winfo_height()
        rng = self._range
        pad_b, pad_t = 22, 8
        cx = w / 2.0

        def to_x(ms):
            c = max(-rng, min(rng, ms))
            return cx + (c / rng) * (w / 2.0 - 8)

        # Perfect window band (purple, 10% over PANEL) — matches HTML rgba(124,58,237,0.10)
        bl, br = to_x(-self._perfect), to_x(self._perfect)
        self.create_rectangle(bl, pad_t, br, h - pad_b,
                              fill=_blend_a(PURPLE, 0.10, PANEL), outline="")
        counts = [0] * _HIST_BINS
        for off in self._offsets:
            cl = max(-rng, min(rng, off))
            idx = int(((cl + rng) / (rng * 2.0)) * _HIST_BINS)
            idx = max(0, min(_HIST_BINS - 1, idx))
            counts[idx] += 1
        peak = max(1, max(counts) if counts else 1)
        bw = (w - 16) / _HIST_BINS
        for i, count in enumerate(counts):
            if count <= 0:
                continue
            bar_h = (count / peak) * (h - pad_b - pad_t - 6)
            x = 8 + i * bw
            center = -rng + (i + 0.5) * (rng * 2.0 / _HIST_BINS)
            # NEVER yellow — early lavender, late primary purple
            color = _HIST_EARLY if center < 0 else _HIST_LATE
            self.create_rectangle(x + 1, (h - pad_b) - bar_h, x + bw - 2,
                                  h - pad_b, fill=color, outline="")
        self.create_line(cx, pad_t, cx, h - pad_b,
                         fill=_blend_a("#efecff", 0.6, PANEL), width=2)
        lab = _blend_a(_HIST_ZERO, 0.9, PANEL)
        self.create_text(8, h - 14, text="− early", anchor="w",
                         fill=lab, font=F_MONO)
        self.create_text(w - 8, h - 14, text="late +", anchor="e",
                         fill=lab, font=F_MONO)
        self.create_text(cx, h - 14, text="0 ms", fill=lab, font=F_MONO)


class ResultsScreen(tk.Frame):
    """Practice end-of-session results (v3 #scrResults).

    Public contract (PracticeTab router — do not change):
      __init__(parent, *, on_play_again, on_worst, on_back, on_apply_offset)
      set_results(data: dict)
      bind_all_esc()

    Layout hierarchy (matches HTML):
      grade → subtitle → window badge → song → FULL COMBO?
      → 4 cards (Score / Accuracy / Max combo / Breakdown)
      → Timing histogram + mean/median/suggested + Apply
      → per-lane table
      → Play again / Practice worst section? / Back to library

    #1 functional fix: whole body is vertically scrollable (Canvas +
    scrollbar + mousewheel) so ~500–700 px tall windows still reach the
    lane table and action buttons.
    """

    def __init__(self, parent, *, on_play_again, on_worst, on_back,
                 on_apply_offset) -> None:
        super().__init__(parent, background=BG)
        self._on_play_again = on_play_again
        self._on_worst = on_worst
        self._on_back = on_back
        self._on_apply_offset = on_apply_offset
        self._suggested = 0
        self._worst = None
        self._page_wheel_binds: List[Tuple[str, str]] = []
        self._build()

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        # Scroll shell — mirrors PracticeHomeScreen page scroll (v4.9.2).
        outer = tk.Frame(self, background=BG)
        outer.pack(fill=tk.BOTH, expand=True)

        self._page_canvas = tk.Canvas(outer, background=BG, highlightthickness=0,
                                      bd=0)
        page_vsb = ttk.Scrollbar(outer, orient=tk.VERTICAL,
                                 command=self._page_canvas.yview)
        self._page_canvas.configure(yscrollcommand=page_vsb.set)
        page_vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._page_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Inner body fills canvas width; natural height drives scrollregion.
        self._body = tk.Frame(self._page_canvas, background=BG)
        self._page_window = self._page_canvas.create_window(
            (0, 0), window=self._body, anchor="nw")
        self._body.bind("<Configure>", self._on_body_configure)
        self._page_canvas.bind("<Configure>", self._on_canvas_configure)

        # Surgical bind_all + retained funcids (same discipline as Home):
        # never unbind_all — host may own global wheel handlers.
        self._page_wheel_binds = []
        for _seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self._page_wheel_binds.append(
                (_seq, self._page_canvas.bind_all(_seq, self._on_page_wheel,
                                                  add="+")))

        # r-shell: centered column, max ~860px feel via side padding + pack.
        shell = tk.Frame(self._body, background=BG)
        shell.pack(fill=tk.X, expand=False, padx=24, pady=(28, 40))
        shell.columnconfigure(0, weight=1)

        # ---- head: grade / sub / badge / song / FC ----
        head = tk.Frame(shell, background=BG)
        head.grid(row=0, column=0, sticky="ew")

        self.grade_lbl = tk.Label(
            head, text="D", background=BG, foreground=_GRADE_DEFAULT,
            font=("Segoe UI", 90, "bold"))
        self.grade_lbl.pack()

        self.sub_lbl = tk.Label(
            head, text="BACK TO THE ANVIL", background=BG, foreground=TEXT,
            font=("Segoe UI", 13, "bold"))
        self.sub_lbl.pack(pady=(2, 0))

        # Window badge as a pill-ish bordered label (rWindowBadge).
        badge_wrap = tk.Frame(head, background=BG)
        badge_wrap.pack(pady=(8, 0))
        self.window_badge = tk.Label(
            badge_wrap, text="Standard windows", background=PANEL,
            foreground=MUTED, font=F_SMALL, padx=10, pady=3,
            highlightthickness=1, highlightbackground=SEPARATOR)
        self.window_badge.pack()

        self.song_lbl = tk.Label(
            head, text="", background=BG, foreground=MUTED, font=F_BASE)
        self.song_lbl.pack(pady=(8, 0))

        self.fc_lbl = tk.Label(
            head, text="★ FULL COMBO ★", background=BG, foreground=_GRADE_FC,
            font=("Segoe UI", 9, "bold"))
        # packed only when fc in set_results

        # ---- 4-up cards ----
        cards = tk.Frame(shell, background=BG)
        cards.grid(row=1, column=0, sticky="ew", pady=(22, 0))
        for c in range(4):
            cards.columnconfigure(c, weight=1, uniform="rcard")
        self.score_val = self._stat_card(cards, 0, "SCORE", "0")
        self.acc_val = self._stat_card(cards, 1, "ACCURACY", "0%")
        self.combo_val = self._stat_card(cards, 2, "MAX COMBO", "0×")
        self._build_breakdown(cards, 3)

        # ---- Timing hist card ----
        hist_card = tk.Frame(
            shell, background=PANEL, highlightthickness=1,
            highlightbackground=SEPARATOR)
        hist_card.grid(row=2, column=0, sticky="ew", pady=(14, 0))
        tk.Label(
            hist_card, text="TIMING", background=PANEL, foreground=PURPLE_LT,
            font=F_BOLD).pack(anchor=tk.W, padx=14, pady=(12, 6))
        self.hist = _Histogram(hist_card)
        self.hist.pack(fill=tk.X, padx=14)
        read_row = tk.Frame(hist_card, background=PANEL)
        read_row.pack(fill=tk.X, padx=14, pady=(6, 12))
        self.hist_read = tk.Label(
            read_row, text="No timed hits to analyze.", background=PANEL,
            foreground=MUTED, font=F_SMALL, justify=tk.LEFT, anchor=tk.W,
            wraplength=520)
        self.hist_read.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.apply_btn = OutlineButton(
            read_row, "Apply", accent=PURPLE_EDGE,
            command=self._apply_clicked)
        # packed when offsets exist

        # ---- per-lane table ----
        self.lane_card = tk.Frame(shell, background=BG)
        self.lane_card.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        self._lane_rows: List[tk.Widget] = []

        # ---- action buttons (always last so scroll reaches them) ----
        btns = tk.Frame(shell, background=BG)
        btns.grid(row=4, column=0, pady=(22, 8))
        self.again_btn = OutlineButton(
            btns, "Play again", accent=MAGENTA, command=self._on_play_again,
            filled=True)
        self.again_btn.pack(side=tk.LEFT, padx=5)
        self.worst_btn = OutlineButton(
            btns, "Practice worst section", accent=PURPLE_EDGE,
            command=self._on_worst)
        # packed conditionally in _set_worst_button
        self.back_btn = OutlineButton(
            btns, "Back to library", accent=PURPLE_EDGE,
            command=self._on_back)
        self.back_btn.pack(side=tk.LEFT, padx=5)

        self.bind_all_esc()

    # -------------------------------------------------------------- scroll
    def _on_body_configure(self, _event=None) -> None:
        try:
            self._page_canvas.configure(
                scrollregion=self._page_canvas.bbox("all"))
        except tk.TclError:
            pass

    def _on_canvas_configure(self, event) -> None:
        try:
            self._page_canvas.itemconfigure(self._page_window, width=event.width)
        except tk.TclError:
            pass
        # Keep hist_read wrap in sync with available width.
        try:
            wrap = max(200, int(event.width) - 120)
            self.hist_read.configure(wraplength=wrap)
        except (tk.TclError, AttributeError):
            pass

    def _on_page_wheel(self, event) -> None:
        # Only scroll when this results screen is the visible/mapped one
        # (bind_all fires app-wide).
        try:
            if not self.winfo_ismapped():
                return
        except tk.TclError:
            return
        delta = 0
        if getattr(event, "num", None) == 4:
            delta = -1
        elif getattr(event, "num", None) == 5:
            delta = 1
        elif getattr(event, "delta", 0):
            delta = -1 if event.delta > 0 else 1
        if not delta:
            return
        # Prefer scrolling only when pointer is over this screen.
        try:
            n = self.winfo_containing(event.x_root, event.y_root)
        except tk.TclError:
            n = None
        over = False
        while n is not None:
            if n is self:
                over = True
                break
            n = getattr(n, "master", None)
        if not over:
            return
        try:
            self._page_canvas.yview_scroll(delta, "units")
        except tk.TclError:
            pass

    def _unbind_page_wheel(self) -> None:
        """Remove ONLY our bind_all wheel handlers (never unbind_all)."""
        w = getattr(self, "_page_canvas", None)
        if w is None:
            return
        for seq, fid in getattr(self, "_page_wheel_binds", ()):
            try:
                script = w.bind_all(seq) or ""
                marker = "[" + fid
                keep = [ln for ln in script.split("\n")
                        if (marker + " ") not in ln and (marker + "]") not in ln]
                w.tk.call("bind", "all", seq, "\n".join(keep))
            except Exception:
                pass
        self._page_wheel_binds = []

    def destroy(self) -> None:
        try:
            self._unbind_page_wheel()
        except Exception:
            pass
        super().destroy()

    # -------------------------------------------------------------- public
    def bind_all_esc(self) -> None:
        self.bind("<Escape>", lambda e: self._on_back())

    # -------------------------------------------------------------- cards
    def _stat_card(self, parent, col, eyebrow, value) -> tk.Label:
        card = tk.Frame(
            parent, background=PANEL, highlightthickness=1,
            highlightbackground=SEPARATOR)
        card.grid(row=0, column=col, sticky="nsew", padx=5)
        tk.Label(
            card, text=eyebrow, background=PANEL, foreground=MUTED,
            font=F_SMALL).pack(anchor=tk.W, padx=12, pady=(12, 0))
        val = tk.Label(
            card, text=value, background=PANEL, foreground=TEXT,
            font=("Consolas", 18, "bold"))
        val.pack(anchor=tk.W, padx=12, pady=(4, 14))
        return val

    def _build_breakdown(self, parent, col) -> None:
        card = tk.Frame(
            parent, background=PANEL, highlightthickness=1,
            highlightbackground=SEPARATOR)
        card.grid(row=0, column=col, sticky="nsew", padx=5)
        tk.Label(
            card, text="BREAKDOWN", background=PANEL, foreground=MUTED,
            font=F_SMALL).pack(anchor=tk.W, padx=12, pady=(12, 4))
        self.perf_val = self._bd_row(card, "Perfect", _C_PERFECT)
        self.early_val = self._bd_row(card, "Early", _C_EARLY)
        self.late_val = self._bd_row(card, "Late", _C_LATE)
        self.miss_val = self._bd_row(card, "Miss", _C_MISS)
        tk.Frame(card, background=SEPARATOR, height=1).pack(
            fill=tk.X, padx=12, pady=3)
        self.hits_val = self._bd_row(card, "Hits", None)
        self.notes_val = self._bd_row(card, "Notes", None)
        tk.Frame(card, background=PANEL, height=8).pack()

    def _bd_row(self, parent, label, color) -> tk.Label:
        row = tk.Frame(parent, background=PANEL)
        row.pack(fill=tk.X, padx=12, pady=1)
        if color:
            sw = tk.Frame(row, background=color, width=8, height=8)
            sw.pack(side=tk.LEFT, padx=(0, 6))
            # Force size — Frame can collapse without a layout push.
            sw.pack_propagate(False)
        tk.Label(
            row, text=label, background=PANEL,
            foreground=TEXT if color else MUTED, font=F_SMALL).pack(
            side=tk.LEFT)
        val = tk.Label(
            row, text="0", background=PANEL,
            foreground=TEXT if color else MUTED, font=F_MONO)
        val.pack(side=tk.RIGHT)
        return val

    # ---------------------------------------------------------- set_results
    def set_results(self, data: Dict[str, Any]) -> None:
        letter, sub = data.get("grade") or compute_grade(0.0)
        fc = bool(data.get("fc"))
        self.grade_lbl.configure(
            text=str(letter),
            foreground=_GRADE_FC if fc else _GRADE_DEFAULT)
        self.sub_lbl.configure(text=str(sub).upper() if sub else "")
        window_label = str(data.get("window_label") or "Standard")
        speed = float(data.get("speed", 1.0) or 1.0)
        badge = f"{window_label} windows"
        if abs(speed - 1.0) > 1e-9:
            badge += f" · {_js_round(speed * 100)}% speed"
        self.window_badge.configure(text=badge)
        self.song_lbl.configure(text=str(data.get("song_line") or ""))
        if fc:
            self.fc_lbl.pack(pady=(6, 0))
        else:
            self.fc_lbl.pack_forget()

        self.score_val.configure(text=f"{int(data.get('score', 0) or 0):,}")
        acc = float(data.get("accuracy_pct", 0.0) or 0.0)
        self.acc_val.configure(text=f"{acc:g}%")
        self.combo_val.configure(
            text=f"{int(data.get('best_streak', 0) or 0)}×")
        perfect = int(data.get("perfect", 0) or 0)
        early = int(data.get("early", 0) or 0)
        late = int(data.get("late", 0) or 0)
        miss = int(data.get("miss", 0) or 0)
        hits = int(data.get("hits", 0) or 0)
        notes = int(data.get("notes", hits + miss) or 0)
        self.perf_val.configure(text=str(perfect))
        self.early_val.configure(text=str(early))
        self.late_val.configure(text=str(late))
        self.miss_val.configure(text=str(miss))
        self.hits_val.configure(text=str(hits))
        self.notes_val.configure(text=str(notes))

        self._set_histogram(data)
        self._set_lane_table(data.get("lanes") or [])
        self._set_worst_button(data.get("worst"), miss)

        # Reset scroll to top on each new result payload.
        try:
            self._page_canvas.yview_moveto(0)
            self._page_canvas.configure(
                scrollregion=self._page_canvas.bbox("all"))
        except tk.TclError:
            pass

    def _set_histogram(self, data) -> None:
        offsets = [float(x) for x in (data.get("timing_offsets_ms") or [])]
        hit_ms = float(data.get("hit_window_ms", 75.0) or 75.0)
        perfect_ms = hit_ms * (35.0 / 75.0)
        self.hist.set_data(offsets, perfect_ms, hit_ms)
        if not offsets:
            self.hist_read.configure(text="No timed hits to analyze.")
            self.apply_btn.pack_forget()
            self._suggested = 0
            return
        so = sorted(offsets)
        median = so[len(so) // 2]
        mean = sum(offsets) / len(offsets)
        suggested = max(-150, min(250, _js_round(-median)))
        self._suggested = suggested
        self.hist_read.configure(
            text=f"Mean {_signed_ms(mean)} ms · Median {_signed_ms(median)} ms "
                 f"· Suggested input offset {_signed_ms(suggested)} ms")
        self.apply_btn.set_enabled(True)
        self.apply_btn.pack(side=tk.RIGHT, padx=(8, 0))

    def _set_lane_table(self, lanes) -> None:
        for w in self._lane_rows:
            w.destroy()
        self._lane_rows = []

        # Header row (Lane / Notes / Hit / Acc / Bias) — muted uppercase.
        header = tk.Frame(self.lane_card, background=BG)
        header.pack(fill=tk.X, pady=(4, 0))
        self._lane_rows.append(header)
        tk.Frame(header, background=SEPARATOR, height=1).pack(
            fill=tk.X, side=tk.BOTTOM)
        for text, wd, side in (
                ("Lane", 16, tk.LEFT), ("Notes", 7, tk.LEFT),
                ("Hit", 6, tk.LEFT), ("Acc", 8, tk.LEFT),
                ("Bias", 14, tk.LEFT)):
            tk.Label(
                header, text=text.upper(), background=BG, foreground=MUTED,
                font=F_SMALL, width=wd, anchor=tk.W).pack(side=side, pady=4)

        for lane in lanes:
            total = int(lane.get("notes", 0) or 0)
            if not total:
                continue
            self._add_lane_row(lane)

    def _add_lane_row(self, lane) -> None:
        row = tk.Frame(self.lane_card, background=BG)
        row.pack(fill=tk.X)
        self._lane_rows.append(row)
        # Bottom hairline like #laneTable td border
        tk.Frame(row, background=SEPARATOR, height=1).pack(
            fill=tk.X, side=tk.BOTTOM)

        name_cell = tk.Frame(row, background=BG)
        name_cell.pack(side=tk.LEFT)
        sw = tk.Frame(
            name_cell, background=lane.get("color", "#888888"),
            width=9, height=9)
        sw.pack(side=tk.LEFT, padx=(0, 7), pady=6)
        sw.pack_propagate(False)
        tk.Label(
            name_cell, text=str(lane.get("label", "")), background=BG,
            foreground=TEXT, font=F_BOLD, width=13, anchor=tk.W).pack(
            side=tk.LEFT)

        hits = int(lane.get("hits", 0) or 0)
        miss = int(lane.get("miss", 0) or 0)
        off_sum = float(lane.get("off_sum_ms", 0.0) or 0.0)
        judged = hits + miss
        acc_text = (
            f"{_js_round(1000 * hits / judged) / 10:g}%" if judged else "—")
        if hits:
            bias = _js_round(off_sum / hits)
            tag = " late" if bias > 8 else (" early" if bias < -8 else "")
            sign = "+" if bias > 0 else ""
            bias_text = f"{sign}{bias} ms{tag}"
        else:
            bias_text = "—"
        for text, wd in (
                (str(lane.get("notes", 0)), 7), (str(hits), 6),
                (acc_text, 8), (bias_text, 14)):
            tk.Label(
                row, text=text, background=BG, foreground=TEXT, font=F_MONO,
                width=wd, anchor=tk.W).pack(side=tk.LEFT, pady=4)

    def _set_worst_button(self, worst, miss_count) -> None:
        self._worst = worst
        if not worst or miss_count <= 0:
            self.worst_btn.pack_forget()
            return
        t0 = float(worst.get("t0", 0.0) or 0.0)
        t1 = float(worst.get("t1", 0.0) or 0.0)
        acc = float(worst.get("acc", 0.0) or 0.0)
        # Match HTML wording: "Practice worst section (t0–t1 · N%) at 80%"
        self.worst_btn.configure(
            text=f"Practice worst section ({_fmt_time(t0)}–{_fmt_time(t1)} "
                 f"· {_js_round(acc * 100)}%) at 80%")
        self.worst_btn.pack(side=tk.LEFT, padx=5, before=self.back_btn)

    def _apply_clicked(self) -> None:
        self._on_apply_offset(self._suggested)
        self.apply_btn.set_enabled(False)


# ===========================================================================
# KitStudioPanel -- the 300px lane/routing editor (mouse editing fully wired;
# keyboard hotkeys are a v5 NO-OP -- ported as-is per DISPATCH PR-J).
# ===========================================================================
class KitStudioPanel(tk.Frame):
    def __init__(self, parent, *, get_layout, get_prefs, get_chart, on_layout_changed,
                 on_pref_changed, on_done, note, hook_call) -> None:
        super().__init__(parent, background=PANEL, highlightthickness=1,
                         highlightbackground=PURPLE_EDGE)
        self._get_layout = get_layout
        self._get_prefs = get_prefs
        self._get_chart = get_chart
        self._on_layout_changed = on_layout_changed
        self._on_pref_changed = on_pref_changed
        self._on_done = on_done
        self._note = note
        self._hook_call = hook_call
        self._sel: Optional[str] = None
        self._build()

    # keyboard hotkeys are a documented v5 NO-OP (PR-J). Present so the play
    # screen's _studio_hotkey forwarding never AttributeErrors, but they do
    # nothing -- mouse editing is the live path. (Flagged, not invented.)
    def select_delta(self, _d): pass
    def move_selected(self, _d): pass
    def width_delta(self, _d): pass
    def cycle_color(self): pass
    def cycle_shape(self): pass
    def toggle_hidden(self): pass

    def _build(self) -> None:
        head = tk.Frame(self, background=PANEL)
        head.pack(fill=tk.X, padx=12, pady=(10, 6))
        tk.Label(head, text="KIT STUDIO", background=PANEL, foreground=PURPLE_LT,
                 font=F_H1).pack(side=tk.LEFT)
        OutlineButton(head, "Done", accent=MAGENTA, command=self._on_done).pack(side=tk.RIGHT)

        body = tk.Frame(self, background=PANEL)
        body.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 10))

        # preset row
        prow = tk.Frame(body, background=PANEL)
        prow.pack(fill=tk.X, pady=(0, 6))
        tk.Label(prow, text="Preset", background=PANEL, foreground=MUTED,
                 font=F_SMALL).pack(side=tk.LEFT)
        self.preset_combo = ttk.Combobox(prow, style="Prac.TCombobox", state="readonly",
                                        width=16, values=list(BUILTIN_PRESETS.keys()))
        self.preset_combo.pack(side=tk.LEFT, padx=(6, 0))
        self.preset_combo.bind("<<ComboboxSelected>>", self._on_preset)

        act = tk.Frame(body, background=PANEL)
        act.pack(fill=tk.X, pady=(0, 6))
        OutlineButton(act, "Save as…", accent=PURPLE_EDGE,
                     command=self._save_as).pack(side=tk.LEFT, padx=(0, 4))
        OutlineButton(act, "Pin to song", accent=PURPLE_EDGE,
                     command=self._pin).pack(side=tk.LEFT, padx=4)
        self.lefty_sw = ToggleSwitch(act, on=bool(self._get_layout().get("lefty")),
                                    command=self._on_lefty, background=PANEL)
        self.lefty_sw.pack(side=tk.RIGHT)
        tk.Label(act, text="Lefty ", background=PANEL, foreground=TEXT,
                 font=F_SMALL).pack(side=tk.RIGHT)

        tk.Label(body, text="LANES", background=PANEL, foreground=PURPLE_LT,
                 font=F_SMALL).pack(anchor=tk.W)
        self.lanes_frame = tk.Frame(body, background=DARKER, highlightthickness=1,
                                   highlightbackground=SEPARATOR)
        self.lanes_frame.pack(fill=tk.X, pady=(2, 6))

        lrow = tk.Frame(body, background=PANEL)
        lrow.pack(fill=tk.X, pady=(0, 6))
        OutlineButton(lrow, "+ Aux lane", accent=PURPLE_EDGE,
                     command=self._add_aux).pack(side=tk.LEFT, padx=(0, 4))
        OutlineButton(lrow, "Reset kit", accent=AMBER,
                     command=self._reset_kit).pack(side=tk.LEFT, padx=4)

        # lane props
        self.props = tk.Frame(body, background=PANEL)
        self.props.pack(fill=tk.X, pady=(0, 6))

        # highway sliders
        tk.Label(body, text="HIGHWAY", background=PANEL, foreground=PURPLE_LT,
                 font=F_SMALL).pack(anchor=tk.W, pady=(6, 0))
        self._hw_sliders(body)

        self._rebuild_lanes()
        self._rebuild_props()

    # ----- lanes list -----
    def _rebuild_lanes(self) -> None:
        for w in self.lanes_frame.winfo_children():
            w.destroy()
        layout = self._get_layout()
        prefs = self._get_prefs()
        palette = str(prefs.get("palette", "forge"))
        order = layout["order"]
        for i, lid in enumerate(order):
            ln = layout["lanes"][lid]
            row = tk.Frame(self.lanes_frame,
                          background=blend(DARKER, PURPLE, 0.25) if lid == self._sel else DARKER)
            row.pack(fill=tk.X)
            bg = row.cget("background")
            tk.Frame(row, background=lane_color(layout, lid, palette), width=12,
                     height=12).pack(side=tk.LEFT, padx=6, pady=5)
            lbl = tk.Label(row, text=ln.get("label", lid), background=bg,
                          foreground=TEXT if ln.get("visible", True) else MUTED,
                          font=F_SMALL, anchor=tk.W)
            lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
            for wgt in (row, lbl):
                wgt.bind("<Button-1>", functools.partial(self._select_lane, lid))
            if len(order) > 4:
                tk.Label(row, text="✕", background=bg, foreground=MUTED,
                         font=F_SMALL, cursor="hand2").pack(side=tk.RIGHT, padx=2)
                row.winfo_children()[-1].bind(
                    "<Button-1>", functools.partial(self._remove_lane, lid))
            tk.Label(row, text="▶", background=bg, foreground=MUTED,
                     font=F_SMALL, cursor="hand2").pack(side=tk.RIGHT)
            row.winfo_children()[-1].bind("<Button-1>",
                                          functools.partial(self._move_lane, lid, 1))
            tk.Label(row, text="◀", background=bg, foreground=MUTED,
                     font=F_SMALL, cursor="hand2").pack(side=tk.RIGHT)
            row.winfo_children()[-1].bind("<Button-1>",
                                          functools.partial(self._move_lane, lid, -1))

    def _select_lane(self, lid, _e=None):
        self._sel = lid
        self._rebuild_lanes()
        self._rebuild_props()

    def _move_lane(self, lid, delta, _e=None):
        layout = self._get_layout()
        order = layout["order"]
        i = order.index(lid)
        j = i + delta
        if 0 <= j < len(order):
            order[i], order[j] = order[j], order[i]
            self._commit()

    def _remove_lane(self, lid, _e=None):
        layout = self._get_layout()
        if len(layout["order"]) > 4 and lid in layout["order"]:
            layout["order"].remove(lid)
            if self._sel == lid:
                self._sel = None
            self._commit()

    def _add_aux(self):
        layout = self._get_layout()
        for aux in ("ax1", "ax2"):
            if aux not in layout["order"] and len(layout["order"]) < MAX_LANES:
                layout["order"].append(aux)
                self._commit()
                return
        self._note("No more aux lanes available (max 10 lanes).")

    def _reset_kit(self):
        base = default_layout()
        layout = self._get_layout()
        layout["order"] = list(base["order"])
        layout["lanes"] = base["lanes"]
        layout["lefty"] = False
        self._sel = None
        self.lefty_sw.set(False, fire=False)
        self._commit()

    # ----- lane props -----
    def _rebuild_props(self) -> None:
        for w in self.props.winfo_children():
            w.destroy()
        if self._sel is None:
            tk.Label(self.props, text="Select a lane to edit its properties.",
                     background=PANEL, foreground=MUTED, font=F_SMALL,
                     wraplength=270, justify=tk.LEFT).pack(anchor=tk.W)
            return
        layout = self._get_layout()
        ln = layout["lanes"][self._sel]
        prefs = self._get_prefs()

        r1 = tk.Frame(self.props, background=PANEL)
        r1.pack(fill=tk.X, pady=2)
        tk.Label(r1, text="Show lane", background=PANEL, foreground=TEXT,
                 font=F_SMALL).pack(side=tk.LEFT)
        ToggleSwitch(r1, on=ln.get("visible", True) is not False,
                    command=self._on_show, background=PANEL).pack(side=tk.RIGHT)

        r2 = tk.Frame(self.props, background=PANEL)
        r2.pack(fill=tk.X, pady=2)
        tk.Label(r2, text="Width", background=PANEL, foreground=TEXT,
                 font=F_SMALL).pack(side=tk.LEFT)
        self._w_val = tk.Label(r2, text=f"{ln.get('widthW', 1.0):g}×",
                              background=PANEL, foreground=CYAN, font=F_MONO, width=5)
        self._w_val.pack(side=tk.RIGHT)
        wvar = tk.DoubleVar(value=float(ln.get("widthW", 1.0)))
        ttk.Scale(r2, from_=0.5, to=2.0, orient=tk.HORIZONTAL, variable=wvar,
                  style="Prac.Horizontal.TScale",
                  command=functools.partial(self._on_width, wvar)).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=8)

        r3 = tk.Frame(self.props, background=PANEL)
        r3.pack(fill=tk.X, pady=2)
        tk.Label(r3, text="Color", background=PANEL, foreground=TEXT,
                 font=F_SMALL).pack(side=tk.LEFT)
        self._color_entry = ttk.Entry(r3, style="Prac.TEntry", width=9)
        self._color_entry.insert(0, lane_color(layout, self._sel,
                                              str(prefs.get("palette", "forge"))))
        self._color_entry.pack(side=tk.LEFT, padx=6)
        self._color_entry.bind("<Return>", self._on_color)
        OutlineButton(r3, "Set", accent=PURPLE_EDGE, command=self._on_color).pack(side=tk.LEFT)
        self._contrast_lbl = tk.Label(r3, text="", background=PANEL,
                                     foreground=AMBER, font=F_SMALL)
        self._contrast_lbl.pack(side=tk.LEFT, padx=(6, 0))
        self._update_contrast()

        tk.Label(self.props, text="Note shape", background=PANEL, foreground=MUTED,
                 font=F_SMALL).pack(anchor=tk.W, pady=(4, 0))
        shape_grid = tk.Frame(self.props, background=PANEL)
        shape_grid.pack(fill=tk.X)
        for i, shp in enumerate(SHAPES):
            b = tk.Label(shape_grid, text=shp[:4], background=DARKER if shp != ln.get("shape") else PURPLE_DEEP,
                        foreground=PURPLE_LT if shp == ln.get("shape") else MUTED,
                        font=F_SMALL, padx=5, pady=2, cursor="hand2",
                        highlightthickness=1, highlightbackground=SEPARATOR)
            b.grid(row=i // 3, column=i % 3, padx=2, pady=2, sticky="ew")
            b.bind("<Button-1>", functools.partial(self._on_shape, shp))

        r4 = tk.Frame(self.props, background=PANEL)
        r4.pack(fill=tk.X, pady=(4, 2))
        tk.Label(r4, text="Fold into", background=PANEL, foreground=TEXT,
                 font=F_SMALL).pack(side=tk.LEFT)
        fold_vals = ["(none)"] + [l for l in layout["order"] if l != self._sel]
        self._fold_combo = ttk.Combobox(r4, style="Prac.TCombobox", state="readonly",
                                       width=10, values=fold_vals)
        self._fold_combo.current(0)
        self._fold_combo.pack(side=tk.LEFT, padx=6)

        r5 = tk.Frame(self.props, background=PANEL)
        r5.pack(fill=tk.X, pady=2)
        tk.Label(r5, text="Sound", background=PANEL, foreground=TEXT,
                 font=F_SMALL).pack(side=tk.LEFT)
        self._voice_combo = ttk.Combobox(r5, style="Prac.TCombobox", state="readonly",
                                        width=10, values=_VOICE_CHOICES)
        try:
            self._voice_combo.current(_VOICE_CHOICES.index(ln.get("voice", "neutral")))
        except ValueError:
            self._voice_combo.current(len(_VOICE_CHOICES) - 1)
        self._voice_combo.pack(side=tk.LEFT, padx=6)
        self._voice_combo.bind("<<ComboboxSelected>>", self._on_voice)
        OutlineButton(r5, "♪", accent=PURPLE_EDGE, command=self._audition).pack(side=tk.LEFT)

        r6 = tk.Frame(self.props, background=PANEL)
        r6.pack(fill=tk.X, pady=2)
        _p = self._get_prefs()
        binds = _sanitize_binds(_p.get("binds"), _p.get("kbPreset"))
        keys = " ".join(_key_label(b["code"]) for b in binds if b.get("lane") == self._sel)
        tk.Label(r6, text=f"Keys: {keys or '—'}", background=PANEL,
                 foreground=CYAN, font=F_MONO).pack(side=tk.LEFT)
        OutlineButton(r6, "Edit binds…", accent=PURPLE_EDGE,
                     command=lambda: self._on_pref_changed("_open_settings", "keybinds")
                     ).pack(side=tk.RIGHT)

    def _on_show(self, value):
        self._get_layout()["lanes"][self._sel]["visible"] = value
        self._commit()

    def _on_width(self, var, _v):
        w = round(var.get(), 2)
        self._get_layout()["lanes"][self._sel]["widthW"] = max(0.5, min(2.0, w))
        self._w_val.configure(text=f"{w:g}×")
        self._commit(rebuild_props=False)

    def _on_color(self, _e=None):
        val = self._color_entry.get().strip()
        if len(val) == 7 and val.startswith("#"):
            self._get_layout()["lanes"][self._sel]["customColor"] = val
            self._update_contrast()
            self._commit(rebuild_props=False)
        else:
            self._note("Color must be #rrggbb.")

    def _update_contrast(self):
        try:
            layout = self._get_layout()
            c = lane_color(layout, self._sel, str(self._get_prefs().get("palette", "forge")))
            ratio = contrast_ratio(c)
            self._contrast_lbl.configure(text="low contrast" if ratio < 3 else "")
        except Exception:
            pass

    def _on_shape(self, shape, _e=None):
        self._get_layout()["lanes"][self._sel]["shape"] = shape
        self._commit()

    def _on_voice(self, _e=None):
        self._get_layout()["lanes"][self._sel]["voice"] = self._voice_combo.get()
        self._commit(rebuild_props=False)

    def _audition(self):
        voice = self._get_layout()["lanes"][self._sel].get("voice", "neutral")
        ok, _ = self._hook_call("synth_play", voice, 100)
        if not ok:
            self._note(f"Audition '{voice}' -- synth wired at integration.")

    def _on_lefty(self, value):
        self._get_layout()["lefty"] = value
        self._commit()

    def _on_preset(self, _e=None):
        name = self.preset_combo.get()
        if name in BUILTIN_PRESETS:
            new = BUILTIN_PRESETS[name]()
            layout = self._get_layout()
            layout["order"] = new["order"]
            layout["lanes"] = new["lanes"]
            layout["lefty"] = new["lefty"]
            self.lefty_sw.set(new["lefty"], fire=False)
            self._sel = None
            self._commit()

    def _save_as(self):
        self._note("Save-as user presets are wired at integration (config seam).")

    def _pin(self):
        self._note("Pin-to-song persists to the song record at integration.")

    def _hw_sliders(self, parent):
        prefs = self._get_prefs()
        specs = (("Highway width", "hwWidth", 0.4, 1.0, "{:.2f}"),
                 ("Position", "hwPos", 0.0, 1.0, "{:.2f}"),
                 ("Hit line", "hitLineFrac", 0.5, 0.95, "{:.2f}"),
                 ("Lane gap", "laneGap", 0, 16, "{:.0f}"),
                 ("Scroll (fall)", "fallTime", 0.8, 8.0, "{:.1f}"),
                 ("Note size", "noteSize", 0.5, 2.0, "{:g}"))
        for label, key, lo, hi, fmt in specs:
            row = tk.Frame(parent, background=PANEL)
            row.pack(fill=tk.X, pady=1)
            tk.Label(row, text=label, background=PANEL, foreground=TEXT,
                     font=F_SMALL, width=12, anchor=tk.W).pack(side=tk.LEFT)
            val = tk.Label(row, text=fmt.format(float(prefs.get(key, lo) or lo)),
                          background=PANEL, foreground=CYAN, font=F_MONO, width=5)
            val.pack(side=tk.RIGHT)
            var = tk.DoubleVar(value=float(prefs.get(key, lo) or lo))
            ttk.Scale(row, from_=lo, to=hi, orient=tk.HORIZONTAL, variable=var,
                      style="Prac.Horizontal.TScale",
                      command=functools.partial(self._on_hw_slider, key, var, val, fmt)
                      ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)

    def _on_hw_slider(self, key, var, val_lbl, fmt, _v):
        v = round(var.get(), 3)
        val_lbl.configure(text=fmt.format(v))
        self._on_pref_changed(key, v)

    def _commit(self, rebuild_props=True) -> None:
        self._rebuild_lanes()
        if rebuild_props:
            self._rebuild_props()
        self._on_layout_changed()


# ===========================================================================
# Overlays: Settings (4 tabs) + Calibration + Mixer. Each is a dim-backdrop
# Frame placed over the whole tab; Close hides it.
# ===========================================================================
class _Overlay(tk.Frame):
    """Base: dim backdrop + centered panel; .open()/.close()."""

    def __init__(self, parent, title, width=560, height=520) -> None:
        # v4.9.1 (owner) — the settings/overlay backdrop is now a PURPLE-tinted
        # scrim (was flat black #000000) so the panel sits on a themed wash, not
        # a dead black rectangle.
        super().__init__(parent, background=_blend_a("#241241", 0.6))
        self._ov_w, self._ov_h = width, height
        self.panel = tk.Frame(self, background=PANEL, highlightthickness=1,
                             highlightbackground=PURPLE_EDGE)
        self.panel.place(relx=0.5, rely=0.5, anchor="center", width=width, height=height)
        top = tk.Frame(self.panel, background=PANEL)
        top.pack(fill=tk.X, padx=16, pady=(12, 8))
        tk.Label(top, text=title, background=PANEL, foreground=PURPLE_LT,
                 font=F_H1).pack(side=tk.LEFT)
        OutlineButton(top, "Close", accent=PURPLE_EDGE, command=self.close).pack(side=tk.RIGHT)
        self.body = tk.Frame(self.panel, background=PANEL)
        self.body.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 12))

    def open(self) -> None:
        self.place(x=0, y=0, relwidth=1, relheight=1)
        self.tkraise()

    def close(self) -> None:
        self.place_forget()


# ===========================================================================
# _KeyboardTestPopup -- Settings "Keyboard test…" self-test window
# ===========================================================================
# Dark themed Toplevel: 8-lane key map, Start/Stop test, hit log, Esc/Close.
# Keyboard path is always live while testing. MIDI is best-effort via the host
# hooks midi_list_ports / midi_start(port, on_note_on) / midi_stop already
# exposed through SettingsOverlay._hook_call (host _build_pp_hooks).
# ===========================================================================

# Minimal GM percussion note -> STANDARD_ORDER lane id (same note families as
# parakit_preview_engine._LANE_MIDI_IMPORT). Used only for log labels; unmapped
# notes still log as "MIDI note N".
_KB_TEST_MIDI_TO_LANE = {
    n: lid
    for lid, notes in zip(
        STANDARD_ORDER,
        (
            (42, 44, 46, 26, 21, 22, 23),  # Hi-Hat
            (49, 55, 57),                   # Crash
            (37, 38, 40),                   # Snare
            (48, 50),                       # Tom 1
            (45, 47),                       # Tom 2
            (41, 43),                       # Tom 3
            (51, 53, 59),                   # Ride
            (35, 36),                       # Kick
        ),
    )
    for n in notes
}


class _KeyboardTestPopup(tk.Toplevel):
    """Keyboard (+ optional MIDI) bind self-test popup."""

    _LOG_CAP = 200
    _FLASH_MS = 120

    def __init__(self, parent, *, get_binds, hook_call, on_closed=None) -> None:
        super().__init__(parent)
        self._get_binds = get_binds
        self._hook_call = hook_call
        self._on_closed = on_closed
        self._testing = False
        self._midi_active = False
        self._pressed: set = set()
        self._last_kp_ms: Dict[str, int] = {}   # keysym -> last KeyPress time
        self._key_bind_id = None
        self._flash_after: Dict[str, Any] = {}
        self._row_frames: Dict[str, tk.Frame] = {}
        self._row_key_labels: Dict[str, tk.Label] = {}
        self._closed = False

        self.title("Keyboard test")
        self.configure(background=PANEL)
        try:
            self.transient(parent)
        except Exception:
            pass
        self.resizable(False, False)

        self._build()
        self._rebuild_rows()
        self._position_border_aware()
        self.protocol("WM_DELETE_WINDOW", self.destroy_clean)
        # Capture keys on THIS window only (does not touch PlayScreen tags).
        self._key_bind_id = self.bind("<KeyPress>", self._on_key, add="+")
        self.bind("<KeyRelease>", self._on_key_release, add="+")
        self.bind("<Destroy>", self._on_destroy, add="+")
        # Modal grab instead of -topmost (owner-reported: with -topmost the
        # popup's Close / X did not register). A grab routes ALL input to this
        # window until it closes, which guarantees the Close button and the
        # window X both receive their click. Deferred until mapped so the grab
        # can't fail on an unviewable window.
        try:
            self.update_idletasks()
            self.focus_force()
        except Exception:
            try:
                self.focus_set()
            except Exception:
                pass
        try:
            self.grab_set()
        except Exception:
            pass

    # ----- build -----
    def _build(self) -> None:
        outer = tk.Frame(self, background=PANEL, highlightthickness=1,
                         highlightbackground=PURPLE_EDGE)
        outer.pack(fill=tk.BOTH, expand=True)

        head = tk.Frame(outer, background=PANEL)
        head.pack(fill=tk.X, padx=14, pady=(12, 6))
        tk.Label(head, text="Keyboard test", background=PANEL,
                 foreground=PURPLE_LT, font=F_H1).pack(side=tk.LEFT)
        OutlineButton(head, "Close", accent=PURPLE_EDGE,
                      command=self.destroy_clean).pack(side=tk.RIGHT)

        tk.Label(
            outer,
            text="Press Start test, then hit your bound keys. "
                 "MIDI: shown when a device is connected.",
            background=PANEL, foreground=MUTED, font=F_SMALL,
            wraplength=420, justify=tk.LEFT,
        ).pack(anchor=tk.W, padx=14, pady=(0, 8))

        # Two-column instrument list
        grid = tk.Frame(outer, background=PANEL)
        grid.pack(fill=tk.X, padx=12, pady=(0, 8))
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)
        self._grid = grid
        # rows filled by _rebuild_rows

        # Controls
        brow = tk.Frame(outer, background=PANEL)
        brow.pack(fill=tk.X, padx=14, pady=(2, 6))
        self._toggle_btn = OutlineButton(
            brow, "Start test", accent=CYAN, command=self._toggle_test)
        self._toggle_btn.pack(side=tk.LEFT)
        self._status = tk.Label(brow, text="Idle", background=PANEL,
                                foreground=MUTED, font=F_SMALL)
        self._status.pack(side=tk.LEFT, padx=(10, 0))

        # Log
        tk.Label(outer, text="Hit log", background=PANEL, foreground=MUTED,
                 font=F_SMALL).pack(anchor=tk.W, padx=14, pady=(4, 2))
        log_wrap = tk.Frame(outer, background=PANEL)
        log_wrap.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 12))
        self._log = tk.Text(
            log_wrap, height=10, width=48, wrap=tk.WORD,
            background=DARKER, foreground=TEXT, insertbackground=TEXT,
            font=F_MONO, relief=tk.FLAT, bd=0, highlightthickness=1,
            highlightbackground=PURPLE_EDGE, state=tk.DISABLED,
            padx=6, pady=4,
        )
        vsb = ttk.Scrollbar(log_wrap, orient=tk.VERTICAL,
                            command=self._log.yview)
        self._log.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    def _rebuild_rows(self) -> None:
        for w in self._grid.winfo_children():
            w.destroy()
        self._row_frames.clear()
        self._row_key_labels.clear()
        by_lane: Dict[str, List[str]] = {}
        for b in self._get_binds() or []:
            lid = b.get("lane")
            code = b.get("code")
            if isinstance(lid, str) and isinstance(code, str):
                by_lane.setdefault(lid, []).append(code)
        for i, lid in enumerate(STANDARD_ORDER):
            row = tk.Frame(self._grid, background=ROW_ALT,
                           highlightthickness=1, highlightbackground=SEPARATOR)
            row.grid(row=i // 2, column=i % 2, sticky="ew", padx=3, pady=3)
            label = LANE_DEFS.get(lid, {}).get("label", lid)
            tk.Label(row, text=label, background=ROW_ALT, foreground=TEXT,
                     font=F_SMALL).pack(side=tk.LEFT, padx=8, pady=5)
            codes = by_lane.get(lid, [])
            caps = " ".join(_key_label(c) for c in codes) if codes else "—"
            kl = tk.Label(row, text=caps, background=DARKER, foreground=CYAN,
                          font=F_MONO, padx=6, pady=1, highlightthickness=1,
                          highlightbackground=PURPLE_EDGE)
            kl.pack(side=tk.RIGHT, padx=6)
            self._row_frames[lid] = row
            self._row_key_labels[lid] = kl

    # ----- border-aware placement (Tooltip pattern, simplified) -----
    def _position_border_aware(self) -> None:
        """Center on parent; clamp to the monitor work-area under the parent."""
        try:
            self.update_idletasks()
            pw = max(self.winfo_reqwidth(), 460)
            ph = max(self.winfo_reqheight(), 420)
            try:
                px = self.master.winfo_rootx()
                py = self.master.winfo_rooty()
                mw = self.master.winfo_width()
                mh = self.master.winfo_height()
                x = px + max(0, (mw - pw) // 2)
                y = py + max(0, (mh - ph) // 2)
            except Exception:
                x = max(0, (self.winfo_screenwidth() - pw) // 2)
                y = max(0, (self.winfo_screenheight() - ph) // 2)
            m_left, m_top, m_right, m_bottom = self._monitor_bounds(x, y)
            margin = 8
            if x + pw > m_right - margin:
                x = max(m_left + margin, m_right - pw - margin)
            if y + ph > m_bottom - margin:
                y = max(m_top + margin, m_bottom - ph - margin)
            x = max(m_left + margin, x)
            y = max(m_top + margin, y)
            self.geometry("%dx%d+%d+%d" % (pw, ph, int(x), int(y)))
        except Exception:
            try:
                self.geometry("480x460")
            except Exception:
                pass

    def _monitor_bounds(self, x, y):
        """(left, top, right, bottom) of the monitor under (x, y)."""
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
                    _fields_ = [("cbSize", ctypes.c_ulong),
                                ("rcMonitor", RECT), ("rcWork", RECT),
                                ("dwFlags", ctypes.c_ulong)]

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
            return (0, 0, self.winfo_screenwidth(), self.winfo_screenheight())
        except Exception:
            return (0, 0, 1920, 1080)

    # ----- test toggle -----
    def _toggle_test(self) -> None:
        if self._testing:
            self._stop_test()
        else:
            self._start_test()

    def _start_test(self) -> None:
        self._rebuild_rows()  # pick up any rebinds made while popup was open
        self._testing = True
        self._pressed.clear()
        # OutlineButton is a tk.Label subclass — set caption via item access.
        try:
            self._toggle_btn["text"] = "Stop test"
        except Exception:
            pass
        self._status.configure(text="Listening…", foreground=CYAN)
        try:
            self.focus_set()
        except Exception:
            pass
        self._midi_start_if_available()
        self._append_log("— test started —")

    def _stop_test(self) -> None:
        self._testing = False
        self._pressed.clear()
        try:
            self._toggle_btn["text"] = "Start test"
        except Exception:
            pass
        self._status.configure(text="Idle", foreground=MUTED)
        self._midi_stop()
        self._append_log("— test stopped —")

    # ----- MIDI (optional, clean hooks only) -----
    def _midi_start_if_available(self) -> None:
        """Subscribe via host midi_start(port, on_note_on) if a port exists."""
        self._midi_active = False
        ok, ports = self._hook_call("midi_list_ports")
        if not ok or not isinstance(ports, (list, tuple)) or not ports:
            return
        port = str(ports[0])
        # Host callback signature: (note, velocity, stamp) on main thread.
        ok2, res = self._hook_call("midi_start", port, self._on_midi_note)
        if ok2 and res is not False:
            self._midi_active = True
            self._status.configure(
                text=f"Listening…  MIDI: {port}", foreground=CYAN)
        # If start failed, stay keyboard-only silently (status already Listening).

    def _midi_stop(self) -> None:
        if not self._midi_active:
            return
        self._midi_active = False
        try:
            self._hook_call("midi_stop")
        except Exception:
            pass

    def _on_midi_note(self, note, velocity=0, stamp=None) -> None:
        if not self._testing or self._closed:
            return
        # Host polls on main thread, but schedule UI work for safety.
        def _ui():
            if not self._testing or self._closed:
                return
            try:
                n = int(note)
            except (TypeError, ValueError):
                return
            lid = _KB_TEST_MIDI_TO_LANE.get(n)
            if lid:
                label = LANE_DEFS.get(lid, {}).get("label", lid)
                self._append_log(f"{label} — MIDI note {n}")
                self._flash_row(lid)
            else:
                self._append_log(f"MIDI note {n}")
        try:
            self.after(0, _ui)
        except Exception:
            pass

    # ----- keyboard -----
    def _on_key_release(self, event) -> None:
        self._pressed.discard(event.keysym)

    def _on_key(self, event):
        """Popup-local KeyPress. Esc always closes. Bound keys log while testing."""
        ks = getattr(event, "keysym", "") or ""
        if ks == "Escape":
            self.destroy_clean()
            return "break"
        if not self._testing:
            return "break"  # swallow while open so game/settings don't see it
        # Inter-press timing diagnostic (owner-facing): every KeyPress logs the
        # ms gap since the last press of the SAME key, so a fast roll shows the
        # true delivery cadence — and a held-key OS auto-repeat is logged as a
        # "filtered repeat" with its gap, so it's clear whether fast events
        # ARE arriving (and just filtered) vs the OS not delivering them.
        now_ms = int(getattr(event, "time", 0) or 0)
        prev_ms = self._last_kp_ms.get(ks)
        self._last_kp_ms[ks] = now_ms
        gap = (now_ms - prev_ms) if prev_ms is not None else -1
        gap_txt = f"  (+{gap}ms)" if 0 <= gap < 5000 else ""
        # auto-repeat filter (held key on Windows)
        if ks in self._pressed:
            self._append_log(f"   ·· filtered repeat{gap_txt}")
            return "break"
        self._pressed.add(ks)
        code = _tk_code(event)
        if not code:
            return "break"
        # code -> lane via current binds
        lane = None
        for b in self._get_binds() or []:
            if b.get("code") == code:
                lane = b.get("lane")
                break
        if not lane:
            return "break"
        label = LANE_DEFS.get(lane, {}).get("label", lane)
        cap = _key_label(code)
        self._append_log(f"{label} — [{cap}]{gap_txt}")
        self._flash_row(lane)
        return "break"

    # ----- log + flash -----
    def _append_log(self, line: str) -> None:
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        try:
            self._log.configure(state=tk.NORMAL)
            self._log.insert(tk.END, line + "\n")
            # Cap ~200 lines
            end_line = int(float(self._log.index("end-1c").split(".")[0]))
            if end_line > self._LOG_CAP:
                cut = end_line - self._LOG_CAP
                self._log.delete("1.0", f"{cut + 1}.0")
            self._log.see(tk.END)
            self._log.configure(state=tk.DISABLED)
        except Exception:
            pass

    def _flash_row(self, lid: str) -> None:
        row = self._row_frames.get(lid)
        if row is None:
            return
        # cancel prior restore
        aid = self._flash_after.pop(lid, None)
        if aid is not None:
            try:
                self.after_cancel(aid)
            except Exception:
                pass
        try:
            row.configure(background=PURPLE_DEEP,
                          highlightbackground=CYAN)
            for child in row.winfo_children():
                try:
                    if child is self._row_key_labels.get(lid):
                        continue
                    if isinstance(child, tk.Label):
                        child.configure(background=PURPLE_DEEP)
                except Exception:
                    pass
        except Exception:
            return

        def _restore():
            self._flash_after.pop(lid, None)
            try:
                if not row.winfo_exists():
                    return
                row.configure(background=ROW_ALT,
                              highlightbackground=SEPARATOR)
                for child in row.winfo_children():
                    try:
                        if child is self._row_key_labels.get(lid):
                            continue
                        if isinstance(child, tk.Label):
                            child.configure(background=ROW_ALT)
                    except Exception:
                        pass
            except Exception:
                pass

        try:
            self._flash_after[lid] = self.after(self._FLASH_MS, _restore)
        except Exception:
            pass

    # ----- teardown -----
    def destroy_clean(self) -> None:
        if self._closed:
            # Idempotent: cleanup already ran, but still ensure the window is
            # gone (and grab released) if a prior partial path left it up.
            try:
                if self.winfo_exists():
                    try:
                        self.grab_release()
                    except Exception:
                        pass
                    try:
                        self.destroy()
                    except Exception:
                        pass
            except Exception:
                pass
            return
        self._closed = True
        if self._testing:
            self._testing = False
            self._midi_stop()
        else:
            self._midi_stop()
        # unbind KeyPress
        fid = self._key_bind_id
        self._key_bind_id = None
        if fid is not None:
            try:
                self.unbind("<KeyPress>", fid)
            except Exception:
                try:
                    self.unbind("<KeyPress>")
                except Exception:
                    pass
        for aid in list(self._flash_after.values()):
            try:
                self.after_cancel(aid)
            except Exception:
                pass
        self._flash_after.clear()
        cb = self._on_closed
        self._on_closed = None
        if cb:
            try:
                cb()
            except Exception:
                pass
        try:
            self.grab_release()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass

    def _on_destroy(self, _event=None) -> None:
        # WM destroy path (Alt-F4 etc.) — still clean MIDI / callback ref.
        # <Destroy> bound on a Toplevel also fires for every descendant that is
        # destroyed (bubbles). Ignore those — otherwise _rebuild_rows() poisoning
        # _closed makes destroy_clean early-return and the window never closes.
        if _event is not None and getattr(_event, "widget", None) is not self:
            return
        if not self._closed:
            self._closed = True
            self._testing = False
            self._midi_stop()
            cb = self._on_closed
            self._on_closed = None
            if cb:
                try:
                    cb()
                except Exception:
                    pass
            try:
                self.grab_release()
            except Exception:
                pass


class SettingsOverlay(_Overlay):
    def __init__(self, parent, *, get_pref, set_pref, note, hook_call,
                 on_binds_changed) -> None:
        super().__init__(parent, "Settings", width=620, height=560)
        self._get = get_pref
        self._set = set_pref
        self._note = note
        self._hook_call = hook_call
        self._on_binds_changed = on_binds_changed
        self._active_tab = str(self._get("_settings_tab", "input") or "input")
        self._listening_lane: Optional[str] = None
        self._listen_id = None
        self._kb_test_popup = None  # _KeyboardTestPopup instance or None
        self._build()
        # Cancel any in-progress key-rebind LISTEN on teardown (breaker fix r9,
        # 2026-07-21): _start_listen binds <KeyPress> on the TOPLEVEL, which
        # outlives this overlay — without this, after tab.destroy() every
        # keystroke fired _capture_key on the destroyed overlay (TclError storm).
        self.bind("<Destroy>", lambda _e: (self._cancel_listen(),
                                           self._close_keyboard_test()), add="+")

    def _open_keyboard_test(self) -> None:
        """Open (or re-open) the Keyboard / MIDI self-test popup."""
        # A mid-rebind LISTEN would steal keys from the test; cancel it first.
        self._cancel_listen()
        self._close_keyboard_test()
        try:
            parent = self.winfo_toplevel()
        except tk.TclError:
            parent = self

        def _get_binds():
            # Live re-read so rebinds made while the popup is open still show.
            return _sanitize_binds(self._get("binds", None),
                                   self._get("kbPreset", "standard"))

        self._kb_test_popup = _KeyboardTestPopup(
            parent, get_binds=_get_binds, hook_call=self._hook_call,
            on_closed=lambda: setattr(self, "_kb_test_popup", None))

    def _close_keyboard_test(self) -> None:
        pop = getattr(self, "_kb_test_popup", None)
        self._kb_test_popup = None
        if pop is None:
            return
        try:
            pop.destroy_clean()
        except Exception:
            try:
                pop.destroy()
            except Exception:
                pass

    def _cancel_listen(self) -> None:
        fid = getattr(self, "_listen_id", None)
        if fid is not None:
            try:
                self.winfo_toplevel().unbind("<KeyPress>", fid)
            except Exception:
                pass
            self._listen_id = None
        self._listening_lane = None

    def close(self) -> None:
        # Closing the overlay mid-listen must cancel it (breaker fix r9): else the
        # binding stayed live and the next stray keystroke silently rebound the lane.
        self._cancel_listen()
        self._close_keyboard_test()
        try:
            self._rebuild_bind_chips()
        except Exception:
            pass
        super().close()
        # Focus fix (2026-07-21): a Combobox/Entry inside the (now hidden)
        # overlay can KEEP keyboard focus after place_forget, and the
        # _on_key_press guard then swallows every drum key — "keys died after I
        # opened Settings mid-song." Hand focus back to the toplevel.
        try:
            self.winfo_toplevel().focus_set()
        except Exception:
            pass

    def open(self, tab: Optional[str] = None) -> None:
        super().open()
        if tab:
            self._show_tab(tab)

    def _build(self) -> None:
        self.tabbar = tk.Frame(self.body, background=PANEL)
        self.tabbar.pack(fill=tk.X)
        self._tab_btns: Dict[str, tk.Label] = {}
        for key, label in (("input", "Input"), ("audio", "Audio"),
                           ("display", "Display"), ("data", "Data")):
            b = tk.Label(self.tabbar, text=label, background=DARKER, foreground=MUTED,
                        font=F_SMALL, padx=14, pady=4, cursor="hand2")
            b.pack(side=tk.LEFT, padx=(0, 2))
            b.bind("<Button-1>", functools.partial(lambda k, e: self._show_tab(k), key))
            self._tab_btns[key] = b
        self.content = tk.Frame(self.body, background=PANEL)
        self.content.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self._show_tab(self._active_tab)

    def _show_tab(self, key) -> None:
        # Keybinds live under the Input tab; callers (Kit Studio "Edit binds…",
        # Home rebind "+") ask for "keybinds" -- alias it instead of KeyError-ing
        # on the tab dispatch (breaker fix P2, 2026-07-21).
        if key in ("keybinds", "keys", "binds"):
            key = "input"
        elif key not in ("input", "audio", "display", "data"):
            key = "input"
        self._active_tab = key
        self._set("_settings_tab", key)
        for k, b in self._tab_btns.items():
            b.configure(background=PURPLE_DEEP if k == key else DARKER,
                        foreground=PURPLE_LT if k == key else MUTED)
        for w in self.content.winfo_children():
            w.destroy()
        {"input": self._tab_input, "audio": self._tab_audio,
         "display": self._tab_display, "data": self._tab_data}[key]()

    # ----- Input tab -----
    def _tab_input(self) -> None:
        c = self.content
        prow = tk.Frame(c, background=PANEL)
        prow.pack(fill=tk.X, pady=3)
        tk.Label(prow, text="Preset", background=PANEL, foreground=TEXT,
                 font=F_SMALL, width=16, anchor=tk.W).pack(side=tk.LEFT)
        combo = ttk.Combobox(prow, style="Prac.TCombobox", state="readonly",
                            width=18, values=["Standard", "Sticking-Focus", "Lefty"])
        cur = {"standard": 0, "sticking": 1, "lefty": 2}.get(
            str(self._get("kbPreset", "standard")), 0)
        combo.current(cur)
        combo.pack(side=tk.LEFT, padx=6)

        def _apply_kb_preset(_e=None):
            # v5 parity (parakit-practice-v3.html:5019-5024): changing the
            # preset REPLACES the active binds (setBinds + persist + rebuild
            # chips + toast). Before this, the combo only stored the id string
            # and the actual key map never changed — Sticking-Focus/Lefty were
            # silent no-ops ("some keys don't work").
            pid = ["standard", "sticking", "lefty"][combo.current()]
            self._set("kbPreset", pid)
            binds = [dict(b) for b in KEYBIND_PRESETS[pid]["binds"]]
            self._set("binds", binds)
            self._on_binds_changed(binds)
            self._rebuild_bind_chips()
            self._note("Keyboard preset: " + KEYBIND_PRESETS[pid]["label"])

        combo.bind("<<ComboboxSelected>>", _apply_kb_preset)
        OutlineButton(prow, "Keyboard test…", accent=PURPLE_EDGE,
                     command=self._open_keyboard_test,
                     ).pack(side=tk.LEFT, padx=(8, 0))

        tk.Label(c, text="Per-lane keys (click a chip, then press a key)",
                 background=PANEL, foreground=MUTED, font=F_SMALL).pack(anchor=tk.W, pady=(8, 2))
        self._binds_frame = tk.Frame(c, background=PANEL)
        self._binds_frame.pack(fill=tk.X)
        self._rebuild_bind_chips()

        self._seg_row(c, "Hit window", ["Relaxed", "Standard", "Strict"],
                      {"relaxed": 0, "standard": 1, "strict": 2}.get(
                          str(self._get("windowMode", "standard")), 1),
                      lambda i: self._set("windowMode",
                                          ["relaxed", "standard", "strict"][i]))
        self._spin_row(c, "Input offset (ms)", "inputLatencyMs", -150, 250)
        self._seg_row(c, "Count-in", ["Off", "4", "8"],
                      {0: 0, 4: 1, 8: 2}.get(int(self._get("countIn", 4) or 0), 1),
                      lambda i: self._set("countIn", [0, 4, 8][i]))

    def _rebuild_bind_chips(self) -> None:
        for w in self._binds_frame.winfo_children():
            w.destroy()
        binds = _sanitize_binds(self._get("binds", None), self._get("kbPreset", "standard"))
        by_lane: Dict[str, List[str]] = {}
        for b in binds:
            by_lane.setdefault(b["lane"], []).append(b["code"])
        for i, lid in enumerate(STANDARD_ORDER):
            row = tk.Frame(self._binds_frame, background=ROW_ALT,
                          highlightthickness=1, highlightbackground=SEPARATOR)
            row.grid(row=i // 2, column=i % 2, sticky="ew", padx=3, pady=3)
            self._binds_frame.columnconfigure(0, weight=1)
            self._binds_frame.columnconfigure(1, weight=1)
            tk.Label(row, text=LANE_DEFS[lid]["label"], background=ROW_ALT,
                     foreground=TEXT, font=F_SMALL).pack(side=tk.LEFT, padx=8, pady=4)
            codes = by_lane.get(lid, [])
            listening = self._listening_lane == lid
            cap = tk.Label(row, text="press…" if listening else
                          (" ".join(_key_label(c) for c in codes) or "—"),
                          background=PURPLE_DEEP if listening else DARKER,
                          foreground=CYAN, font=F_MONO, padx=6, pady=1, cursor="hand2",
                          highlightthickness=1, highlightbackground=PURPLE_EDGE)
            cap.pack(side=tk.RIGHT, padx=6)
            cap.bind("<Button-1>", functools.partial(self._start_listen, lid))

    def _start_listen(self, lid, _e=None):
        self._listening_lane = lid
        self._rebuild_bind_chips()
        self._note(f"Listening for {LANE_DEFS[lid]['label']} key… (Esc cancels)")
        top = self.winfo_toplevel()
        self._listen_id = top.bind("<KeyPress>", self._capture_key, add="+")

    def _capture_key(self, event):
        try:
            if not self.winfo_exists():   # destroyed overlay -> ignore (breaker fix r9)
                return
            top = self.winfo_toplevel()
        except tk.TclError:
            return
        fid = getattr(self, "_listen_id", None)
        if fid:
            top.unbind("<KeyPress>", fid)
            self._listen_id = None
        lid = self._listening_lane
        self._listening_lane = None
        if event.keysym == "Escape" or lid is None:
            self._rebuild_bind_chips()
            return
        code = _tk_code(event)
        if not code:
            self._note("That key can't be bound.")
            self._rebuild_bind_chips()
            return
        binds = [dict(b) for b in _sanitize_binds(self._get("binds", None),
                                                  self._get("kbPreset", "standard"))]
        # one-key-one-lane: remove this code from any other lane, set on this lane
        binds = [b for b in binds if b.get("code") != code]
        binds = [b for b in binds if b.get("lane") != lid] + [{"code": code, "lane": lid}]
        self._set("binds", binds)
        self._on_binds_changed(binds)
        self._rebuild_bind_chips()
        self._note(f"{LANE_DEFS[lid]['label']} bound to {_key_label(code)}.")

    # ----- Audio tab -----
    def _tab_audio(self) -> None:
        c = self.content
        for label, key in (("Song", "busSong"), ("Drums", "busDrums"),
                           ("Synth", "busSynth"), ("Master", "busMaster")):
            self._slider_row(c, label, key, 0.0, 1.0)
        r = tk.Frame(c, background=PANEL)
        r.pack(fill=tk.X, pady=3)
        tk.Label(r, text="Mute synth", background=PANEL, foreground=TEXT,
                 font=F_SMALL, width=16, anchor=tk.W).pack(side=tk.LEFT)
        ToggleSwitch(r, on=bool(self._get("muteSynth", False)),
                    command=lambda v: self._set("muteSynth", v),
                    background=PANEL).pack(side=tk.LEFT)
        mrow = tk.Frame(c, background=PANEL)
        mrow.pack(fill=tk.X, pady=3)
        tk.Label(mrow, text="MIDI Device", background=PANEL, foreground=TEXT,
                 font=F_SMALL, width=16, anchor=tk.W).pack(side=tk.LEFT)
        ok, ports = self._hook_call("midi_list_ports")
        # A scalar/None hook result must not reach list() (breaker fix F3): only a
        # non-empty list/tuple of names populates the combobox; else show "none".
        values = ([str(p) for p in ports]
                  if (ok and isinstance(ports, (list, tuple)) and ports) else ["none"])
        ttk.Combobox(mrow, style="Prac.TCombobox", state="readonly", width=20,
                    values=values).pack(side=tk.LEFT, padx=6)
        self._spin_row(c, "Debounce (ms)", "midiDebounceMs", 0, 100)
        self._seg_row(c, "Velocity curve", ["Soft", "Linear", "Hard"],
                      {"soft": 0, "linear": 1, "hard": 2}.get(
                          str(self._get("midiVelCurve", "linear")), 1),
                      lambda i: self._set("midiVelCurve",
                                          ["soft", "linear", "hard"][i]))
        self._spin_row(c, "Per-device offset (ms)", "midiDeviceOffsetMs", -150, 250)
        OutlineButton(c, "Pad-learn walk…", accent=PURPLE_EDGE,
                     tooltip=("Walks your MIDI kit one drum at a time: hit each "
                              "pad when prompted and it auto-maps that pad to "
                              "the lane. Needs a connected MIDI device — MIDI "
                              "input isn't wired in this build yet, so this is a "
                              "placeholder. Use “Keyboard test…” above to check "
                              "your key bindings now."),
                     command=lambda: self._note(
                         "Pad-learn walk needs a connected MIDI device — MIDI "
                         "input isn't wired in this build yet. Use Keyboard "
                         "test to check your bindings."),
                     ).pack(anchor=tk.W, pady=(6, 0))

    # ----- Display tab -----
    def _tab_display(self) -> None:
        c = self.content
        self._seg_row(c, "Palette", ["ParaKit", "Okabe-Ito", "Tritan"],
                      {"forge": 0, "okabe": 1, "tritan": 2}.get(
                          str(self._get("palette", "forge")), 0),
                      lambda i: self._set("palette", ["forge", "okabe", "tritan"][i]))
        for label, key in (("Particles & shake", "fx"),
                           ("Reduce motion", "reduceMotion"),
                           ("Perf HUD", "perfHud")):
            r = tk.Frame(c, background=PANEL)
            r.pack(fill=tk.X, pady=3)
            tk.Label(r, text=label, background=PANEL, foreground=TEXT,
                     font=F_SMALL, width=16, anchor=tk.W).pack(side=tk.LEFT)
            ToggleSwitch(r, on=bool(self._get(key, key == "fx")),
                        command=functools.partial(lambda k, v: self._set(k, v), key),
                        background=PANEL).pack(side=tk.LEFT)
        # UI-scale slider: built then hidden/inert (DISPATCH: do NOT port functional)
        # Intentionally NOT packed -- present in code as the documented inert control.
        self._ui_scale_inert = ttk.Scale(c, from_=0.8, to=1.5, orient=tk.HORIZONTAL,
                                        style="Prac.Horizontal.TScale")
        tk.Label(c, text="(UI-scale slider is inert per spec -- not shown)",
                 background=PANEL, foreground=MUTED, font=F_SMALL).pack(anchor=tk.W, pady=(8, 0))

    # ----- Data tab -----
    def _tab_data(self) -> None:
        c = self.content
        OutlineButton(c, "Export backup…", accent=PURPLE_EDGE,
                     command=self._export_backup).pack(anchor=tk.W, pady=3)
        OutlineButton(c, "Import backup…", accent=PURPLE_EDGE,
                     command=self._import_backup).pack(anchor=tk.W, pady=3)
        OutlineButton(c, "Reset everything…", accent=AMBER,
                     command=self._reset_everything).pack(anchor=tk.W, pady=3)

    def _export_backup(self):
        path = filedialog.asksaveasfilename(
            title="Export practice backup", defaultextension=".json",
            filetypes=[("JSON", "*.json")])
        if path:
            self._note(f"Backup export lands at integration (config seam). ({os.path.basename(path)})")

    def _import_backup(self):
        path = filedialog.askopenfilename(title="Import practice backup",
                                         filetypes=[("JSON", "*.json")])
        if path:
            self._note("Backup import lands at integration (config seam).")

    def _reset_everything(self):
        self._note("Reset-everything is a danger-confirm action wired at integration.")

    # ----- shared control factories -----
    def _seg_row(self, parent, label, options, idx, on_change):
        row = tk.Frame(parent, background=PANEL)
        row.pack(fill=tk.X, pady=3)
        tk.Label(row, text=label, background=PANEL, foreground=TEXT,
                 font=F_SMALL, width=16, anchor=tk.W).pack(side=tk.LEFT)
        seg = Segmented(row, options, command=on_change)
        seg.set(idx, fire=False)
        seg.pack(side=tk.LEFT)

    def _spin_row(self, parent, label, key, lo, hi):
        row = tk.Frame(parent, background=PANEL)
        row.pack(fill=tk.X, pady=3)
        tk.Label(row, text=label, background=PANEL, foreground=TEXT,
                 font=F_SMALL, width=16, anchor=tk.W).pack(side=tk.LEFT)
        var = tk.StringVar(value=str(int(self._get(key, 0) or 0)))
        sp = ttk.Spinbox(row, from_=lo, to=hi, textvariable=var, width=8,
                        style="Prac.TCombobox")
        sp.pack(side=tk.LEFT, padx=6)

        def commit(_e=None):
            try:
                v = max(lo, min(hi, int(float(var.get()))))
            except ValueError:
                v = 0
            var.set(str(v))
            self._set(key, v)
        sp.bind("<FocusOut>", commit)
        sp.bind("<Return>", commit)
        sp.configure(command=commit)

    def _slider_row(self, parent, label, key, lo, hi):
        row = tk.Frame(parent, background=PANEL)
        row.pack(fill=tk.X, pady=3)
        tk.Label(row, text=label, background=PANEL, foreground=TEXT,
                 font=F_SMALL, width=16, anchor=tk.W).pack(side=tk.LEFT)
        val = tk.Label(row, text=f"{float(self._get(key, 1.0) or 1.0):.2f}",
                      background=PANEL, foreground=CYAN, font=F_MONO, width=5)
        val.pack(side=tk.RIGHT)
        var = tk.DoubleVar(value=float(self._get(key, 1.0) or 1.0))

        def moved(_v):
            v = round(var.get(), 2)
            val.configure(text=f"{v:.2f}")
            self._set(key, v)
        ttk.Scale(row, from_=lo, to=hi, orient=tk.HORIZONTAL, variable=var,
                  style="Prac.Horizontal.TScale", command=moved).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=8)


class CalibrationOverlay(_Overlay):
    def __init__(self, parent, *, get_pref, set_pref, note, hook_call) -> None:
        super().__init__(parent, "Calibrate latency", width=460, height=380)
        self._get = get_pref
        self._set = set_pref
        self._note = note
        self._hook_call = hook_call
        self._taps: List[float] = []
        self._t0 = 0.0
        self._build()

    def open(self) -> None:
        super().open()
        self._taps = []
        self._t0 = time.perf_counter()
        self._update_readout()
        self.panel.focus_set()

    def _build(self) -> None:
        c = self.body
        tk.Label(c, text="Tap along with the metronome click. Press SPACE or "
                        "click the zone below on each beat.",
                 background=PANEL, foreground=MUTED, font=F_SMALL,
                 wraplength=400, justify=tk.LEFT).pack(anchor=tk.W, pady=(0, 10))
        self.zone = tk.Frame(c, background=DARKER, highlightthickness=2,
                            highlightbackground=PURPLE_EDGE, height=90)
        self.zone.pack(fill=tk.X, pady=6)
        tk.Label(self.zone, text="TAP HERE", background=DARKER, foreground=PURPLE_LT,
                 font=("Segoe UI", 16, "bold")).place(relx=0.5, rely=0.5, anchor="center")
        self.zone.bind("<Button-1>", self._tap)
        self.panel.bind("<space>", self._tap)
        self.panel.bind("<Key-space>", self._tap)
        self.readout = tk.Label(c, text="", background=PANEL, foreground=TEXT,
                               font=F_MONO)
        self.readout.pack(anchor=tk.W, pady=8)
        brow = tk.Frame(c, background=PANEL)
        brow.pack(fill=tk.X, pady=(6, 0))
        OutlineButton(brow, "Apply", accent=MAGENTA, command=self._apply).pack(side=tk.LEFT)
        OutlineButton(brow, "Reset", accent=PURPLE_EDGE, command=self._reset).pack(side=tk.LEFT, padx=6)

    def _tap(self, _e=None):
        t = time.perf_counter()
        self._taps.append(t)
        self._hook_call("synth_met_click", True)
        self._update_readout()

    def _reset(self):
        self._taps = []
        self._update_readout()

    def _offsets_ms(self) -> List[float]:
        # infer beat period from median inter-tap interval; offset = deviation
        if len(self._taps) < 2:
            return []
        intervals = [self._taps[i] - self._taps[i - 1] for i in range(1, len(self._taps))]
        intervals.sort()
        period = intervals[len(intervals) // 2]
        offs = []
        for i in range(1, len(self._taps)):
            dev = ((self._taps[i] - self._taps[0]) % period)
            if dev > period / 2:
                dev -= period
            offs.append(dev * 1000.0)
        return offs

    def _suggested(self) -> int:
        offs = self._offsets_ms()
        if not offs:
            return 0
        so = sorted(offs)
        return max(-150, min(250, _js_round(so[len(so) // 2])))

    def _update_readout(self):
        n = len(self._taps)
        offs = self._offsets_ms()
        if offs:
            mean = sum(offs) / len(offs)
            so = sorted(offs)
            median = so[len(so) // 2]
            self.readout.configure(
                text=f"{n} taps · mean {_signed_ms(mean)} ms · "
                     f"median {_signed_ms(median)} ms · "
                     f"suggested {_signed_ms(self._suggested())} ms")
        else:
            self.readout.configure(text=f"{n} taps · (need ≥6 to apply)")

    def _apply(self):
        if len(self._taps) < 6:
            self._note("Tap at least 6 times before applying.")
            return
        v = self._suggested()
        self._set("inputLatencyMs", v)
        self._note(f"Input offset set to {v:+d} ms.")
        self.close()


class MixerOverlay(_Overlay):
    def __init__(self, parent, *, get_pref, set_pref, note, hook_call) -> None:
        super().__init__(parent, "Mixer & offset", width=460, height=420)
        self._get = get_pref
        self._set = set_pref
        self._note = note
        self._hook_call = hook_call
        self._build()

    def _build(self) -> None:
        c = self.body
        for label, key, bus in (("Drums", "busDrums", "drums"),
                               ("Synth", "busSynth", "synth"),
                               ("Backing", "busSong", "song"),
                               ("Master", "busMaster", "master")):
            row = tk.Frame(c, background=PANEL)
            row.pack(fill=tk.X, pady=4)
            tk.Label(row, text=label, background=PANEL, foreground=TEXT,
                     font=F_SMALL, width=10, anchor=tk.W).pack(side=tk.LEFT)
            val = tk.Label(row, text=f"{float(self._get(key, 1.0) or 1.0):.2f}",
                          background=PANEL, foreground=CYAN, font=F_MONO, width=5)
            val.pack(side=tk.RIGHT)
            var = tk.DoubleVar(value=float(self._get(key, 1.0) or 1.0))

            def moved(_v, k=key, vl=val, vr=var, b=bus):
                v = round(vr.get(), 2)
                vl.configure(text=f"{v:.2f}")
                self._set(k, v)
                self._hook_call("mixer_set_bus_gain", b, v)
            ttk.Scale(row, from_=0.0, to=1.0, orient=tk.HORIZONTAL, variable=var,
                      style="Prac.Horizontal.TScale", command=moved).pack(
                side=tk.LEFT, fill=tk.X, expand=True, padx=8)

        nrow = tk.Frame(c, background=PANEL)
        nrow.pack(fill=tk.X, pady=(12, 4))
        tk.Label(nrow, text="Stem nudge (ms)", background=PANEL, foreground=TEXT,
                 font=F_SMALL, width=14, anchor=tk.W).pack(side=tk.LEFT)
        nval = tk.Label(nrow, text=f"{int(self._get('stemNudgeMs', 0) or 0)}",
                       background=PANEL, foreground=CYAN, font=F_MONO, width=6)
        nval.pack(side=tk.RIGHT)
        nvar = tk.DoubleVar(value=float(self._get("stemNudgeMs", 0) or 0))

        def nmoved(_v):
            v = int(round(nvar.get()))
            nval.configure(text=str(v))
            self._set("stemNudgeMs", v)
            self._hook_call("mixer_stem_nudge", v)
        ttk.Scale(nrow, from_=-500, to=500, orient=tk.HORIZONTAL, variable=nvar,
                  style="Prac.Horizontal.TScale", command=nmoved).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=8)


# ===========================================================================
# PracticeTab -- the screen router (Home -> Play -> Results) + overlays.
# ===========================================================================
class PracticeTab(ttk.Frame):
    def __init__(self, parent, hooks=None, **kw) -> None:
        super().__init__(parent, style="Prac.TFrame", **kw)
        self.hooks = hooks if hooks is not None else {}

        if self.hooks:
            apply_theme_embedded(self)

        # persisted prefs + kit layout via the seam
        stored = self._cfg_get(_CFG_PREFS_KEY, {})
        self._prefs: Dict[str, Any] = dict(DEFAULT_PREFS)
        if isinstance(stored, dict):
            self._prefs.update(stored)
        self._normalize_prefs()   # breaker fix r6: coerce corrupt persisted values
        stored_kit = self._cfg_get(_CFG_KIT_KEY, {})
        self._layout = apply_layout_snapshot(stored_kit) if isinstance(stored_kit, dict) and stored_kit \
            else default_layout()

        self._current: Optional[dict] = None
        self._last_payload: Optional[dict] = None

        self._stack = tk.Frame(self, background=BG)
        self._stack.pack(fill=tk.BOTH, expand=True)
        self._stack.rowconfigure(0, weight=1)
        self._stack.columnconfigure(0, weight=1)

        # Home
        self.home = PracticeHomeScreen(self._stack, hooks=self.hooks)
        self.home.grid(row=0, column=0, sticky="nsew")
        self.home.on_play_request = self._on_play_request
        self.home.on_open_settings = self._open_settings
        self.home.on_open_kit_studio = self._open_kit_studio_home
        self.home.on_open_calibration = self._open_calibration
        # Keyboard / MIDI test shortcut from the Home input section — reuses the
        # Settings overlay's tester popup (works whether or not Settings is open).
        self.home.on_keyboard_test = self._open_keyboard_test_home

        # Play
        self.play = PlayScreen(
            self._stack, hook_call=self._hook_call, note=self._status,
            on_finished=self._on_session_finished, on_exit=self._on_play_exit,
            on_kit_studio=self._enter_studio, on_calibrate=self._open_calibration,
            on_mixer=self._open_mixer, on_pref_changed=self._on_pref_changed)
        self.play.grid(row=0, column=0, sticky="nsew")

        # Results
        self.results = ResultsScreen(
            self._stack, on_play_again=self._on_results_play_again,
            on_worst=self._on_results_worst, on_back=self._on_results_back,
            on_apply_offset=self._on_apply_offset)
        self.results.grid(row=0, column=0, sticky="nsew")

        # Overlays
        self.settings = SettingsOverlay(
            self, get_pref=self._pref_get, set_pref=self._pref_set,
            note=self._status, hook_call=self._hook_call,
            on_binds_changed=self._on_binds_changed)
        self.calibration = CalibrationOverlay(
            self, get_pref=self._pref_get, set_pref=self._pref_set,
            note=self._status, hook_call=self._hook_call)
        self.mixer = MixerOverlay(
            self, get_pref=self._pref_get, set_pref=self._pref_set,
            note=self._status, hook_call=self._hook_call)

        # Kit Studio panel (built lazily over the play screen)
        self._studio_panel: Optional[KitStudioPanel] = None

        self._raise_screen(self.home)

    # ----- hook seam (spectral pattern) -----
    def _cfg_get(self, key, default=None):
        if "get_cfg" not in self.hooks:
            return default
        try:
            val = self.hooks["get_cfg"](key, default)
        except Exception:
            return default
        if default is not None and val is not None and not isinstance(val, type(default)):
            return default
        return val

    def _cfg_set(self, key, value) -> None:
        if "set_cfg" not in self.hooks:
            return
        try:
            self.hooks["set_cfg"](key, value)
        except Exception:
            pass

    def _hook_call(self, name, *args, **kwargs) -> Tuple[bool, Any]:
        if name not in self.hooks:
            return (False, None)
        try:
            return (True, self.hooks[name](*args, **kwargs))
        except Exception as e:
            self._status(f"{name} hook failed: {e}")
            return (False, None)

    def _status(self, text: str) -> None:
        # route to Home's note label when visible, else the host status hook
        try:
            self.home._note(text)
        except Exception:
            pass
        self._hook_call("status_message", text, 4000)

    # ----- prefs -----
    def _pref_get(self, key, default=None):
        return self._prefs.get(key, default)

    def _pref_set(self, key, value) -> None:
        self._prefs[key] = value
        self._cfg_set(_CFG_PREFS_KEY, self._prefs)
        # live-apply to the play screen view where relevant
        if self._current is not None and self.play.winfo_ismapped():
            self.play._prefs = self._prefs
            self.play.highway.set_view(self._view_for_render(self._current))
            self.play.update_dock()

    def _on_pref_changed(self, key, value) -> None:
        if key == "_open_settings":
            self._open_settings(value)
            return
        self._prefs[key] = value
        self._cfg_set(_CFG_PREFS_KEY, self._prefs)
        if self._current is not None:
            self.play.highway.set_view(self._view_for_render(self._current))

    def _on_binds_changed(self, binds) -> None:
        self._prefs["binds"] = binds
        self._cfg_set(_CFG_PREFS_KEY, self._prefs)
        try:
            self.play.set_binds(binds)
            if self._current is not None:
                self.play.highway.set_view(self._view_for_render(self._current))
        except Exception:
            pass

    def _binds(self) -> list:
        return _sanitize_binds(self._prefs.get("binds"), self._prefs.get("kbPreset"))

    # ----- view building (mirrors v5 _view_for_render) -----
    def _view_for_render(self, current: dict) -> dict:
        layout = self._layout
        order = current["resolved"]["activeOrder"]
        palette = str(self._prefs.get("palette", "forge"))
        bind_lbls: Dict[str, list] = {}
        for b in self._binds():
            bind_lbls.setdefault(b["lane"], []).append(_key_label(b["code"]))
        lanes = []
        for lid in order:
            ln = layout["lanes"][lid]
            lanes.append({"id": lid, "label": ln.get("label", lid),
                         "shape": ln.get("shape", "circle"),
                         "color": lane_color(layout, lid, palette),
                         "widthW": float(ln.get("widthW", 1.0) or 1.0),
                         "visible": ln.get("visible", True) is not False,
                         "voice": ln.get("voice", "neutral"),
                         "bind_label": " ".join(bind_lbls.get(lid, ()))})
        compact = bool(self._prefs.get("compact"))
        return {"lanes": lanes,
                "kick_idx": order.index("kk") if "kk" in order else -1,
                "kick_line": bool(self._prefs.get("kickLine", True)),
                "square": bool(self._prefs.get("square")), "compact": compact,
                "classicStyle": bool(self._prefs.get("classicStyle")),
                "hit_line_frac": float(self._prefs.get("hitLineFrac", 0.78) or 0.78),
                "fall_time": float(self._prefs.get("fallTime", 2.6) or 2.6),
                "width_frac": (float(self._prefs.get("hwWidth", 0.82) or 0.82)
                               * (0.65 if compact else 1.0)),
                "pos_x": float(self._prefs.get("hwPos", 0.5) or 0.5),
                "lane_gap": float(self._prefs.get("laneGap", 4) or 0),
                "note_scale": float(self._prefs.get("noteSize", 1.0) or 1.0),
                "well_opacity": 0.06, "fx": bool(self._prefs.get("fx", True)),
                "reduceMotion": bool(self._prefs.get("reduceMotion"))}

    # ----- session building from Home's config -----
    def _prepare_session(self, config: dict) -> dict:
        chart = config["chart"]
        # honour the Home-chosen kit for this play (fall back to the tab layout)
        layout = config.get("layout") or self._layout
        self._layout = apply_layout_snapshot(layout)
        resolved = resolve_routing(chart, self._layout, None)
        lane_notes = build_lane_notes(chart, resolved)
        order = resolved["activeOrder"]
        lane_visible = [self._layout["lanes"][lid].get("visible", True) is not False
                        for lid in order]
        kick_idx = order.index("kk") if "kk" in order else -1
        # sync prefs from config (window mode / speed chosen on Home)
        self._prefs["windowMode"] = config.get("window_mode", self._prefs.get("windowMode", "standard"))
        self._prefs["autoKick"] = bool(config.get("auto_kick", self._prefs.get("autoKick")))
        self._prefs["speed"] = float(config.get("speed", 1.0) or 1.0)
        self._prefs["youDrum"] = bool(config.get("you_drum", True))
        # Mute-on-miss follows You-drum (consolidated control — see _toggle_pref).
        self._prefs["muteOnMiss"] = self._prefs["youDrum"]
        windows = windows_for(self._prefs["windowMode"])
        # Load THIS song's stems into the host BEFORE reading its audio duration
        # (breaker fix r10, 2026-07-21): mixer_audio_duration reads the host's
        # currently-loaded stems, but start_session loads the current song's
        # stems only AFTER this returns — so a fresh session used to read the
        # PREVIOUS song's audio length (running dead air / hanging past the real
        # end), or 0 on the first play (the audio-extends-chart logic a no-op).
        source_mode = str(config.get("audio_source", "auto")).lower()
        if source_mode not in {"auto", "fullmix", "stems"}:
            source_mode = "auto"
        self._hook_call("mixer_set_source_mode", source_mode)
        self._hook_call("mixer_load_stems", config.get("audio_paths") or {})
        ok, dur = self._hook_call("mixer_audio_duration")
        audio_dur = max(0.0, _finite_or(dur, 0.0)) if ok else 0.0
        duration = max(song_duration_from_lanes(lane_notes), audio_dur)
        session = Session(lane_notes=lane_notes, lane_visible=lane_visible,
                          auto_kick=bool(self._prefs["autoKick"]), kick_lane_idx=kick_idx,
                          input_latency_ms=float(config.get("input_latency_ms", 0) or 0),
                          song_duration=duration, windows=windows)
        return {"session": session, "chart": chart, "resolved": resolved,
                "lane_notes": lane_notes, "difficulty": config.get("difficulty", ""),
                "title": config.get("title", ""), "artist": config.get("artist", ""),
                "key": config.get("key"), "end_time": duration,
                "stems": config.get("audio_paths"), "audio_source": source_mode,
                "entry": {"key": config.get("key"), "title": config.get("title", ""),
                          "artist": config.get("artist", "")}}

    # ----- Home callbacks -----
    def _raise_screen(self, screen) -> None:
        # Single navigation seam (breaker fix, 2026-07-21): raise the screen AND
        # tell Home whether it is now the top screen, so Home's global '/' search
        # shortcut only fires on Home (a covered frame stays winfo_ismapped()).
        screen.tkraise()
        try:
            self.home.set_shown(screen is self.home)
        except Exception:
            pass

    def _normalize_prefs(self) -> None:
        """Coerce every persisted pref to its DEFAULT_PREFS type once, so no
        downstream reader (the render-view builder, SettingsOverlay spinboxes,
        MixerOverlay sliders) ever calls a bare int()/float() on a corrupt value
        (breaker fix r6, 2026-07-21): a single non-numeric/NaN pref used to crash
        either play-start OR the whole tab build. Contract: degrade, never crash."""
        for k, dv in DEFAULT_PREFS.items():
            v = self._prefs.get(k, dv)
            if isinstance(dv, bool):
                self._prefs[k] = bool(v)
            elif isinstance(dv, (int, float)):
                try:
                    f = float(v)
                    if not math.isfinite(f):
                        f = float(dv)
                except (TypeError, ValueError):
                    f = float(dv)
                self._prefs[k] = int(f) if isinstance(dv, int) else f
        # binds is a list-of-dicts, not a scalar DEFAULT_PREFS key -- sanitize
        # it here too so no downstream reader hits b["lane"] on garbage (P1).
        # v5 parity: a None/corrupt binds falls back to the ACTIVE PRESET's
        # table (kbPreset), not blindly to Standard.
        self._prefs["binds"] = _sanitize_binds(self._prefs.get("binds"),
                                               self._prefs.get("kbPreset"))

    def _on_play_request(self, config: dict) -> None:
        try:
            current = self._prepare_session(config)
            view = self._view_for_render(current)   # inside the try (breaker fix r6)
        except Exception as e:
            self._status(f"Could not start play: {e}")
            return
        self._current = current
        self._raise_screen(self.play)
        self.play.start_session(current, self._prefs, view, self._binds())

    def _open_settings(self, tab=None) -> None:
        self.settings.open(tab)

    def _open_keyboard_test_home(self) -> None:
        # Home input-section shortcut to the Keyboard / MIDI test popup. The
        # SettingsOverlay owns the popup + its bind/hook access; open it there
        # without showing the whole Settings panel.
        try:
            self.settings._open_keyboard_test()
        except Exception as e:
            self._status(f"Could not open the input tester: {e}")

    def _open_kit_studio_home(self) -> None:
        # from Home: open a live demo-groove preview in the play screen + studio
        demo = demo_chart()
        config = {"source": "demo", "chart": demo, "layout": self._layout,
                  "title": "Kit Studio", "artist": "", "difficulty": "Demo",
                  "speed_pct": 100, "speed": 1.0, "you_drum": True,
                  "window_mode": self._prefs.get("windowMode", "standard"),
                  "auto_kick": bool(self._prefs.get("autoKick")),
                  "input_latency_ms": self._prefs.get("inputLatencyMs", 0),
                  "audio_paths": {}, "key": None}
        self._current = self._prepare_session(config)
        self._raise_screen(self.play)
        self.play.start_session(self._current, self._prefs,
                               self._view_for_render(self._current), self._binds())
        # Track this job so external_stop()/destroy() can cancel it (breaker fix,
        # 2026-07-21): a tab torn down inside the 60 ms window fired the timer on
        # the destroyed widget -> Tcl "invalid command name" bgerror. Cancel any
        # outstanding job first (breaker fix r5): a double-open (double-click the
        # demo button) used to OVERWRITE the id and orphan the first timer.
        prev = getattr(self, "_studio_open_job", None)
        if prev is not None:
            try:
                self.after_cancel(prev)
            except Exception:
                pass
        self._studio_open_job = self.after(
            60, functools.partial(self._enter_studio, True))

    def _open_calibration(self) -> None:
        self.calibration.open()

    def _open_mixer(self) -> None:
        self.mixer.open()

    # ----- Kit Studio over play -----
    def _ensure_studio_panel(self) -> KitStudioPanel:
        if self._studio_panel is None:
            self._studio_panel = KitStudioPanel(
                self.play, get_layout=lambda: self._layout, get_prefs=lambda: self._prefs,
                get_chart=lambda: (self._current or {}).get("chart"),
                on_layout_changed=self._rebuild_session_live,
                on_pref_changed=self._on_pref_changed, on_done=self._exit_studio,
                note=self._status, hook_call=self._hook_call)
        return self._studio_panel

    def _enter_studio(self, from_library: bool = False) -> None:
        panel = self._ensure_studio_panel()
        if not self.play._paused and not from_library:
            self.play.pause_game()
        self.play._studio = True
        panel.place(x=12, y=64, width=300,
                    height=max(160, self.play.winfo_height() - 110))
        panel.tkraise()

    def _exit_studio(self) -> None:
        if self._studio_panel is not None:
            self._studio_panel.place_forget()
        self.play._studio = False
        if self.play._paused:
            self.play._show_pause_overlay()

    def _rebuild_session_live(self) -> None:
        """A live kit edit -> re-route + rebuild the Session, preserving the
        running counters, and refresh the renderer without restarting."""
        cur = self._current
        if cur is None:
            return
        self._cfg_set(_CFG_KIT_KEY, snapshot_layout(self._layout))
        chart = cur["chart"]
        old = cur["session"]
        old_order = list((cur.get("resolved") or {}).get("activeOrder") or [])
        resolved = resolve_routing(chart, self._layout, None)
        lane_notes = build_lane_notes(chart, resolved)
        order = resolved["activeOrder"]
        lane_visible = [self._layout["lanes"][lid].get("visible", True) is not False
                        for lid in order]
        kick_idx = order.index("kk") if "kk" in order else -1
        windows = windows_for(self._prefs.get("windowMode", "standard"))
        session = Session(lane_notes=lane_notes, lane_visible=lane_visible,
                          auto_kick=bool(self._prefs.get("autoKick")),
                          kick_lane_idx=kick_idx,
                          input_latency_ms=float(self._prefs.get("inputLatencyMs", 0) or 0),
                          song_duration=cur["end_time"], windows=windows)
        # Keep-stats rebuild. Preserve the top-line counters AND, when the lane
        # ORDER is unchanged (the common color/size/visibility edit), the per-lane
        # table + worst-section too (breaker fix, 2026-07-21): the old copy kept
        # only the header counters, so a live kit edit silently ZEROED lane_stats
        # + sections -> Results showed header hits != per-lane sum. On an order
        # CHANGE the per-lane mapping is ambiguous, so we let the fresh (zeroed)
        # stats stand — scoring restarts from the edit point but stays internally
        # consistent (header total == per-lane sum).
        for attr in ("hits", "misses", "streak", "best_streak", "perfect",
                     "early", "late", "miss_count"):
            setattr(session, attr, getattr(old, attr))
        session.timing_offsets = list(old.timing_offsets)
        if list(order) == old_order:
            session.lane_stats = [dict(d) for d in old.lane_stats]
            session.sections = {k: dict(v) for k, v in old.sections.items()}
            session.last_lane_hit_time = list(old.last_lane_hit_time)
            session.last_hit_velocity = list(old.last_hit_velocity)
        else:
            # order changed -> restart scoring from here so Results is consistent
            for attr in ("hits", "misses", "streak", "best_streak", "perfect",
                         "early", "late", "miss_count"):
                setattr(session, attr, 0)
            session.timing_offsets = []
        now = old.song_time
        session.seek(now)
        cur["session"] = session
        cur["resolved"] = resolved
        cur["lane_notes"] = lane_notes
        self.play.session_rebuilt(cur, self._view_for_render(cur))

    # ----- play/results transitions -----
    def _on_play_exit(self) -> None:
        self._exit_studio()
        # Free the finished song's stems (breaker fix r10): the Results->library
        # path skipped this, so the next song could inherit the prior audio.
        try:
            self.play._engine.free_stems()
        except Exception:
            pass
        self._raise_screen(self.home)

    def _on_session_finished(self, payload: dict) -> None:
        self._last_payload = payload
        self.results.set_results(payload)
        self._raise_screen(self.results)
        self.results.focus_set()

    def _on_results_play_again(self) -> None:
        if self._current is None:
            return
        self._raise_screen(self.play)
        self.play._bind_keys()
        self.play.restart_session()

    def _on_results_worst(self) -> None:
        worst = (self._last_payload or {}).get("worst")
        if self._current is None or not worst:
            return
        self._raise_screen(self.play)
        self.play._bind_keys()
        self.play.practice_worst(worst)

    def _on_results_back(self) -> None:
        self._on_play_exit()

    def _on_apply_offset(self, suggested: int) -> None:
        cur = float(self._prefs.get("inputLatencyMs", 0) or 0)
        new = max(-150, min(250, int(round(cur + suggested))))
        self._prefs["inputLatencyMs"] = new
        self._cfg_set(_CFG_PREFS_KEY, self._prefs)
        self._status(f"Input offset applied: {new:+d} ms (was {cur:+.0f} ms).")

    # ----- host API -----
    def receive(self, kind: str, payload: dict) -> None:
        """Host handoff. 'load_chart' -> load a chart dict/path into the Home
        single-chart loader (mirrors the v4 'send to preview' path)."""
        if kind != "load_chart":
            return
        try:
            path = payload.get("path") if isinstance(payload, dict) else None
            if path and os.path.isfile(path):
                # Stop any running session first (breaker fix r6, 2026-07-21):
                # a receive() during active play used to leak the session + a
                # toplevel <KeyPress> binding that survived destroy -> Tcl "bad
                # window path name" on the next keystroke.
                self.external_stop()
                self.home._load_rlrr_path(path)
                self._raise_screen(self.home)
            else:
                # Surface a dropped hand-off (breaker fix r9): a missing/deleted
                # path or a directory used to vanish with no feedback — symmetric
                # now with the garbage-file path, which already status-notes.
                self._status(f"receive(load_chart): not a readable chart file: {path!r}")
        except Exception as e:
            self._status(f"receive(load_chart) failed: {e}")

    def external_stop(self) -> None:
        """Host-callable stop -- called on tab change. Stops playback/timers/
        threads on every screen (never raises)."""
        job = getattr(self, "_studio_open_job", None)   # breaker fix (see _open_kit_studio_home)
        if job is not None:
            try:
                self.after_cancel(job)
            except Exception:
                pass
            self._studio_open_job = None
        try:
            self.play.external_stop()
        except Exception:
            pass
        try:
            self.home.external_stop()
        except Exception:
            pass

    def destroy(self) -> None:
        try:
            self.external_stop()
        except Exception:
            pass
        super().destroy()


# ===========================================================================
# Standalone host -- `python parakit_practice_tab.py`. dict-backed
# get_cfg/set_cfg exercises the config seam; NO mixer/synth/midi hooks are
# supplied, so the highway runs on the silent internal clock and hits/MIDI are
# absent-with-note -- proving the hooks=None degrade path end to end.
# ===========================================================================
def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    cfg_store: Dict[str, Any] = {
        "practice_songs_folder": os.path.join(
            os.path.expanduser("~"), "OneDrive", "Desktop", "Paradiddle Songs"),
        "practice_prefs": dict(DEFAULT_PREFS),
        "sort": "title",
    }

    def get_cfg(key, default=None):
        return cfg_store.get(key, default)

    def set_cfg(key, value):
        cfg_store[key] = value

    root = tk.Tk()
    root.title("ParaKit Practice -- standalone host")
    root.geometry("1360x900")
    apply_theme_global(root)

    # NO mixer/synth/midi hooks -> silent internal clock + absent-with-note.
    tab = PracticeTab(root, hooks={"get_cfg": get_cfg, "set_cfg": set_cfg})
    tab.pack(fill=tk.BOTH, expand=True)

    def on_close():
        try:
            tab.external_stop()
        except Exception:
            pass
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
