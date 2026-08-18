"""parakit_preview_tab.py -- Preview tab, v4-embeddable module.

A single-file, stdlib+tKinter re-port of the v5 PySide6 "Preview" tab (the
falling-note chart previewer: watch the chart fall toward a hit-line synced
to a play clock, tune speed/fall-window, scrub, edit notes in place, tap-chart
during playback, import/export, and stress the renderer with a demo chart).
Sidecar pattern IDENTICAL to the shipped Spectral Comparison tab
(``parakit_spectral_tab.py``): a ``ttk.Frame`` with a ``hooks=None`` seam,
self-contained ``Prev.*`` styling, and a standalone review host.

WHAT THIS TAB DOES (v5 parity, dispatch DISPATCH_practice_preview_grok45.md):
  Notes fall down 8 fixed lane columns toward a hit-line near the bottom of
  the view, in lock-step with a play clock. Renderer, chart model/demo/undo,
  edit mode (place/move/reclassify/delete/erase/tap-chart), Settings + Help
  popovers, seek, transport, A/B loop, and chart Import/Export are all
  BUILD-NOW and fully self-contained. Audio (stems + the built-in synth) and
  auto-fetch-audio are DEFERRED behind hooks -- with no hooks the tab still
  runs completely on a silent internal clock. (v4.9.0: the native MIDI-input
  device dropdown was removed -- this tab is for WATCHING notes fall + spot-
  editing, not playing along; live recording captures keyboard 1-8 taps.)

EMBEDDING CONTRACT (ParaKit v4):
  ``PreviewTab(parent, hooks=None)`` builds into any parent frame. ``hooks``
  is either ``None`` (standalone review host) or a dict supplied by the v4
  side with any subset of:
    - ``get_cfg(key, default)`` / ``set_cfg(key, value)``
    - ``mixer_load_stems(specs)``, ``mixer_play(from_song, speed)``,
      ``mixer_pause()``, ``mixer_unpause()``, ``mixer_stop()``,
      ``mixer_seek(t)``, ``mixer_set_bus_gain(bus, gain)``,
      ``mixer_song_time()``, ``mixer_has_bus(bus)``, ``mixer_audio_duration()``
    - ``synth_ensure_rendered()``, ``synth_play(voice, vel)``,
      ``synth_met_click(accent)``, ``synth_set_muted(bool)``
    - ``auto_fetch_audio(chart_path) -> {"mix": path, "drums": path}``
  Missing or raising hooks degrade to the standalone behaviour for that seam
  with a one-line status note (missing hook = silent no-op, per the
  ``_hook_call`` contract below); they never crash the tab.

STYLES:
  All ttk styles used by the tab live in the ``Prev.*`` namespace so the tab
  is a self-contained panel that does not clobber v4's app-wide theming.
  ``apply_theme_global(root)`` (standalone ``__main__``) configures both the
  bare legacy names and the ``Prev.*`` names; ``apply_theme_embedded(widget)``
  (called inside ``PreviewTab`` when ``hooks`` is present) configures only the
  ``Prev.*`` names. Palette + font constants are copied verbatim from
  ``parakit_spectral_tab.py`` so Preview and Spectral read identically.

Structure:
    apply_theme_global / apply_theme_embedded  -- ttk style setup.
    Tooltip / Chip / OutlineButton / PrimaryButton / Segmented /
    PlaceholderEntry                           -- widgets (spectral-derived).
    Popover                                    -- border-aware floating panel
                                                   (Settings / Help).
    FallingCanvas                              -- the renderer + in-view editor.
    PreviewTab(ttk.Frame)                       -- the embeddable tab.
    __main__                                   -- standalone review host.
"""
from __future__ import annotations

import math
import os
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import parakit_preview_engine as eng

# v4.9.1 — optional HQ note-sprite sidecar (Pillow-rendered gradient+glow
# sprites, the HTML prototype's look). Missing/broken => None and _draw_notes
# keeps its legacy flat-vector branch. Import guarded so the Preview tab can
# never fail to load because of a sprite/Pillow problem.
try:
    import parakit_preview_sprites as _spr
except Exception:
    _spr = None

# ---------------------------------------------------------------------------
# Palette -- copied VERBATIM from parakit_spectral_tab.py so Preview reads
# identically to Spectral (ParaKit v4 dark-purple identity).
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
RED = "#e5484d"           # Stop / Record accent (Preview-local; spectral has
                           # no red primitive of its own)
CHIP_OFF_EDGE = "#3a3a55"
SEPARATOR = "#2b2b45"
TICK_COL = "#554477"       # faint slider tick marks (matches the app convention)


def _decoder_audio_patterns():
    """File patterns the ACTUAL stem decoder can open. The host decodes with
    soundfile (ParaKit v4.0.py _pp_decode_stem), so ask soundfile which
    formats its libsndfile build supports instead of hardcoding a list — the
    old hardcoded list offered *.m4a *.aac, which libsndfile has never
    decoded: the pick went green as "Loaded stems" and Play ran a silent
    moving playhead (audit fix 2026-08-02; verified soundfile 0.13.1's
    available_formats() has no M4A/AAC/MP4). Falls back to the
    always-supported trio when soundfile isn't importable (standalone run)."""
    exts = ["wav", "flac", "ogg"]
    try:
        import soundfile as sf
        fmts = set(sf.available_formats())
        cand = [("WAV", "wav"), ("FLAC", "flac"), ("OGG", "ogg"),
                ("MP3", "mp3"), ("MPEG", "mp3"),   # key renamed across versions
                ("AIFF", "aiff"), ("AIFF", "aif")]
        got = []
        for key, ext in cand:
            if key in fmts and ext not in got:
                got.append(ext)
        if got:
            exts = got
    except Exception:
        pass
    return " ".join("*." + e for e in exts)


def _prev_tick_strip(parent, length, bg=PANEL, tick_px=10):
    """Pack a faint tick-mark canvas below a fixed-width slider — the app-wide
    convention (mirrors the host / Spectral _tick_strip). Call right after the
    Scale is packed inside a small wrapper Frame holding only scale + this."""
    c = tk.Canvas(parent, width=length, height=6, bg=bg,
                  highlightthickness=0, bd=0)
    c.pack(side=tk.TOP, anchor="w")
    n = max(2, int(length) // tick_px)
    for i in range(n + 1):
        x = int(round(i * (length - 1) / n))
        c.create_line(x, 0, x, 4, fill=TICK_COL, width=1)
    return c


def _prev_tick_strip_fill(parent, bg=BG, tick_px=14):
    """Tick strip for a FULL-WIDTH slider (e.g. the seek bar): a fill=X canvas
    that re-lays its ticks on resize so they always span the slider."""
    c = tk.Canvas(parent, height=6, bg=bg, highlightthickness=0, bd=0)
    c.pack(side=tk.TOP, fill=tk.X)

    def _redraw(_e=None):
        c.delete("all")
        w = max(2, c.winfo_width())
        n = max(2, w // tick_px)
        for i in range(n + 1):
            x = int(round(i * (w - 1) / n))
            c.create_line(x, 0, x, 4, fill=TICK_COL, width=1)
    c.bind("<Configure>", _redraw)
    return c

F_H1 = ("Segoe UI", 12, "bold")
F_BASE = ("Segoe UI", 9)
F_BOLD = ("Segoe UI", 9, "bold")
F_SMALL = ("Segoe UI", 8)
F_MONO = ("Consolas", 8)
F_MONO_B = ("Consolas", 10, "bold")
F_MONO_SMALL_B = ("Consolas", 8, "bold")


def _truncate_mid(name, max_len=24):
    """Short toolbar readout: middle-ellipsis when over max_len chars."""
    if not name:
        return ""
    name = str(name)
    if len(name) <= max_len:
        return name
    keep = max_len - 1  # room for the ellipsis glyph
    left = keep // 2
    right = keep - left
    if right < 1:
        return name[:max_len]
    return name[:left] + "…" + name[-right:]


def _looks_drumless_basename(path):
    """Best-effort v1 (filename heuristic only, no audio analysis): likely a
    drumless backing if the lowercased basename contains 'backing',
    'no_drums'/'nodrums', or 'instrumental'. A light audio-energy check could be
    added later."""
    try:
        name = os.path.basename(path or "").lower()
    except Exception:
        return False
    if not name:
        return False
    return ("backing" in name or "no_drums" in name or "nodrums" in name
            or "instrumental" in name)


# Lane palette -- SAME order/colours as spectral's LANES (v4 MIDI_EDITOR_LANES
# convention); sourced from the engine so the two modules never drift apart.
LANE_NAMES = eng.LANE_NAMES
LANE_COLORS = eng.LANE_COLORS
LANE_SHAPES = eng.LANE_SHAPES
LANE_COUNT = eng.LANE_COUNT

# A note time this large is corrupt, not a real song (>11 days). A FINITE-but-
# huge time (e.g. 1e308) passes the isfinite guard but overflows the engine's
# `floor(time / bucket)` and the grid's `int(t / sub)` to +inf (breaker fix F4,
# 2026-07-21). Bound it at the chart-load choke so those stay representable.
_MAX_NOTE_TIME = 1e7


def _finite_or(v, default):
    """Coerce v to a finite float or return default (breaker fix F5, 2026-07-21).
    Captured/forwarded MIDI velocities can arrive as a dict/NaN and crashed a
    bare int()/float() on the record path."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default

# ---------------------------------------------------------------------------
# Small colour helpers (verbatim logic from parakit_spectral_tab.py).
# ---------------------------------------------------------------------------
def hex_rgb(color: str):
    color = color.lstrip("#")
    return (int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16))


def rgb_hex(r: int, g: int, b: int) -> str:
    return "#%02x%02x%02x" % (max(0, min(255, int(r))),
                               max(0, min(255, int(g))),
                               max(0, min(255, int(b))))


def blend(c1: str, c2: str, t: float) -> str:
    a, b = hex_rgb(c1), hex_rgb(c2)
    t = max(0.0, min(1.0, t))
    return rgb_hex(a[0] + (b[0] - a[0]) * t,
                   a[1] + (b[1] - a[1]) * t,
                   a[2] + (b[2] - a[2]) * t)


def fmt_time(t: float) -> str:
    """Seconds -> m:ss.d for the transport readout."""
    t = max(0.0, float(t))
    if not math.isfinite(t):
        return "0:00.0"
    return "%d:%04.1f" % (int(t // 60), t % 60.0)


def fmt_short(t: float) -> str:
    """Seconds -> m:ss for the seek-row endpoints."""
    t = max(0.0, float(t))
    if not math.isfinite(t):
        return "0:00"
    return "%d:%02d" % (int(t // 60), int(t % 60))


# ---------------------------------------------------------------------------
# Theme -- ALL ttk styling lives here (Prev.* namespace mirrors spectral's
# Spec.* namespace 1:1).
# ---------------------------------------------------------------------------
def apply_theme_global(root: tk.Misc) -> ttk.Style:
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    _configure_bare_styles(style)
    _configure_prev_styles(style)
    root.configure(bg=BG)
    root.option_add("*TCombobox*Listbox.background", INPUT_BG)
    root.option_add("*TCombobox*Listbox.foreground", INPUT_FG)
    root.option_add("*TCombobox*Listbox.selectBackground", PURPLE)
    return style


def apply_theme_embedded(widget: tk.Widget) -> ttk.Style:
    """Set up only the Prev.* styles for an embedded tab. No theme_use, no
    option_add -- the parent application owns those."""
    style = ttk.Style(widget)
    _configure_prev_styles(style)
    return style


def _configure_bare_styles(style: ttk.Style):
    style.configure(".", background=BG, foreground=TEXT, font=F_BASE,
                     bordercolor=SEPARATOR, darkcolor=BG, lightcolor=BG,
                     troughcolor=BG, fieldbackground=INPUT_BG,
                     selectbackground=PURPLE, selectforeground=INPUT_FG)
    style.configure("TFrame", background=BG)
    style.configure("Panel.TFrame", background=PANEL)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Panel.TLabel", background=PANEL, foreground=TEXT)
    style.configure("Muted.TLabel", background=BG, foreground=MUTED)
    style.configure("Header.TLabel", background=PANEL, foreground=PURPLE_LT,
                     font=F_H1)
    style.configure("TLabelframe", background=PANEL, foreground=PURPLE_LT,
                     bordercolor=PURPLE_EDGE)
    style.configure("TLabelframe.Label", background=PANEL,
                     foreground=PURPLE_LT, font=F_BOLD)
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


def _configure_prev_styles(style: ttk.Style):
    """Prev.* namespace -- the ONLY styles used by PreviewTab widgets."""
    style.configure("Prev.", background=BG, foreground=TEXT, font=F_BASE,
                     bordercolor=SEPARATOR, darkcolor=BG, lightcolor=BG,
                     troughcolor=BG, fieldbackground=INPUT_BG,
                     selectbackground=PURPLE, selectforeground=INPUT_FG)
    style.configure("Prev.TFrame", background=BG)
    style.configure("Prev.Panel.TFrame", background=PANEL)
    style.configure("Prev.TLabel", background=BG, foreground=TEXT)
    style.configure("Prev.Panel.TLabel", background=PANEL, foreground=TEXT)
    style.configure("Prev.Muted.TLabel", background=BG, foreground=MUTED)
    style.configure("Prev.PanelMuted.TLabel", background=PANEL, foreground=MUTED)
    style.configure("Prev.Header.TLabel", background=PANEL, foreground=PURPLE_LT,
                     font=F_H1)
    style.configure("Prev.TLabelframe", background=PANEL, foreground=PURPLE_LT,
                     bordercolor=PURPLE_EDGE)
    style.configure("Prev.TLabelframe.Label", background=PANEL,
                     foreground=PURPLE_LT, font=F_BOLD)
    style.configure("Prev.TCombobox", fieldbackground=INPUT_BG,
                     foreground=INPUT_FG, background=INPUT_BG,
                     arrowcolor=PURPLE_LT, bordercolor=PURPLE_EDGE, padding=2)
    style.map("Prev.TCombobox",
              fieldbackground=[("readonly", INPUT_BG)],
              foreground=[("readonly", INPUT_FG)],
              selectbackground=[("readonly", INPUT_BG)],
              selectforeground=[("readonly", INPUT_FG)])
    style.configure("Prev.Horizontal.TScale", background=PURPLE_LT,
                     troughcolor=PURPLE_DEEP, bordercolor=PANEL,
                     lightcolor=PURPLE_LT, darkcolor=PURPLE)
    style.configure("Prev.Horizontal.TScrollbar", background="#26263e",
                     troughcolor=BG, arrowcolor=MUTED, bordercolor="#26263e",
                     lightcolor="#26263e", darkcolor="#26263e")


# ---------------------------------------------------------------------------
# Popover positioning -- shared, border-aware clamp (Settings/Help popovers
# reuse this; Tooltip below carries its own copy per the dispatch's "copy +
# harden the spectral Tooltip" instruction, so the two stay independently
# testable).
# ---------------------------------------------------------------------------
def _monitor_bounds(widget: tk.Misc, x: int, y: int):
    """(left, top, right, bottom) work-area of the monitor under screen point
    (x, y). Win32 MonitorFromPoint on Windows (taskbar excluded), falling
    back to the primary screen's winfo dims elsewhere."""
    try:
        if os.name == "nt":
            import ctypes

            class POINT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

            class RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                            ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

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
        return (0, 0, widget.winfo_screenwidth(), widget.winfo_screenheight())
    except Exception:
        return (0, 0, 1920, 1080)


def _position_popup_with_bounds(popup, widget, x, y, margin=8):
    """Clamp a Toplevel popup to the visible monitor work-area, flipping
    above the host widget if it would overflow the bottom. Falls back to
    plain geometry() on any error -- never raises."""
    try:
        popup.update_idletasks()
        pw = popup.winfo_reqwidth()
        ph = popup.winfo_reqheight()
        m_left, m_top, m_right, m_bottom = _monitor_bounds(widget, x, y)
        if x + pw > m_right - margin:
            x = max(m_left + margin, m_right - pw - margin)
        if y + ph > m_bottom - margin:
            y_above = widget.winfo_rooty() - ph - 4
            if y_above >= m_top + margin:
                y = y_above
            else:
                y = max(m_top + margin, m_bottom - ph - margin)
        x = max(m_left + margin, x)
        y = max(m_top + margin, y)
        popup.geometry("+%d+%d" % (int(x), int(y)))
    except Exception:
        try:
            popup.geometry("+%d+%d" % (int(x), int(y)))
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Small widgets -- copied from parakit_spectral_tab.py (Tooltip / Chip /
# OutlineButton / PrimaryButton / Segmented / PlaceholderEntry).
# ---------------------------------------------------------------------------
class Tooltip:
    """Minimal hover tooltip (tk has none built in). Delays 450 ms, follows
    the widget's bottom-left, kills itself on leave/click. Border-aware: it
    is clamped to the monitor work-area so it can never open off-screen
    (copied + kept in lock-step with parakit_spectral_tab.py's Tooltip, the
    origin of this rule)."""

    _DELAY_MS = 450

    def __init__(self, widget: tk.Widget, text: str):
        self._widget = widget
        self._text = text
        self._tip = None
        self._after_id = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")
        # Cancel the pending 450 ms after-job when the widget is destroyed
        # (breaker fix, 2026-07-20): otherwise a tooltip armed by a hover fires
        # its _show timer AFTER destroy() → "invalid command name" Tcl bgerror.
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
        lbl = tk.Label(tip, text=self._text, justify=tk.LEFT, wraplength=280,
                        background=PANEL, foreground=TEXT, font=F_SMALL,
                        padx=7, pady=5, highlightthickness=1,
                        highlightbackground=PURPLE_EDGE)
        lbl.pack()
        _position_popup_with_bounds(tip, self._widget, x, y, margin=8)
        self._tip = tip

    def hide(self, _event=None):
        self._cancel()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None


class Chip(tk.Label):
    """A toggle 'chip' -- v4's small option buttons use coloured OUTLINES."""

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


class PrimaryButton(tk.Label):
    """Filled purple primary-action button (tk.Label so `background` renders
    on any host theme, matching spectral's rationale)."""

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

    def set_enabled(self, enabled: bool):
        self._enabled = bool(enabled)
        self.configure(cursor="hand2" if enabled else "arrow")
        self._restyle()


class Segmented(tk.Frame):
    """N-option segmented control (Stems / Synth / Both)."""

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
            lbl.bind("<Button-1>", lambda _e, i=i: self.set(i))
            self._labels.append(lbl)
        self._restyle()

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


class PlaceholderEntry(ttk.Frame):
    """ttk.Entry with placeholder text (ttk has none)."""

    def __init__(self, parent, placeholder="", tooltip=None, width=30):
        super().__init__(parent, style="Prev.TFrame")
        self._placeholder = placeholder
        self._has_text = False
        self.entry = ttk.Entry(self, style="Prev.TEntry", width=width)
        self.entry.pack(fill=tk.X, expand=True)
        self.entry.insert(0, placeholder)
        self.entry.bind("<FocusIn>", self._focus_in)
        self.entry.bind("<FocusOut>", self._focus_out)
        if tooltip:
            Tooltip(self.entry, tooltip)

    def _focus_in(self, _event=None):
        if not self._has_text:
            self.entry.delete(0, tk.END)
            self._has_text = True

    def _focus_out(self, _event=None):
        if not self.entry.get().strip():
            self._has_text = False
            self.entry.delete(0, tk.END)
            self.entry.insert(0, self._placeholder)

    def get(self) -> str:
        return self.entry.get().strip() if self._has_text else ""

    def set(self, text: str):
        self.entry.delete(0, tk.END)
        self.entry.insert(0, text)
        self._has_text = bool(text)
        if not self._has_text:
            self._focus_out()


class Popover(tk.Toplevel):
    """A floating, border-aware popover (Settings / Help). Built once, shown
    below its anchor button and hidden with withdraw() -- never destroyed
    while the tab lives."""

    def __init__(self, master):
        super().__init__(master)
        self.wm_overrideredirect(True)
        self.wm_attributes("-topmost", True)
        self.configure(background=PANEL, highlightthickness=1,
                        highlightbackground=PURPLE_EDGE)
        self.withdraw()

    def show_below(self, anchor: tk.Widget):
        self.update_idletasks()
        x = anchor.winfo_rootx()
        y = anchor.winfo_rooty() + anchor.winfo_height() + 4
        self.deiconify()
        _position_popup_with_bounds(self, anchor, x, y, margin=8)
        self.lift()

    def hide(self):
        self.withdraw()

    def is_open(self) -> bool:
        try:
            return bool(self.winfo_viewable())
        except tk.TclError:
            return False


# ---------------------------------------------------------------------------
# FallingCanvas -- the renderer + in-view editor. ONE tk.Canvas, redrawn per
# tick with viewport culling: only the notes inside the visible fall window
# are ever iterated/drawn (via ChartModel.get_index().iter_window), so total
# chart size never costs frame time -- this is the whole de-risk property the
# dispatch calls out (the "Insane" ~9.7k-note demo must stay smooth).
#
# Geometry mirrors v5's FallingView 1:1 (y = time, x = 8 fixed lane columns,
# hit-line at 78% down, tail below = fall*0.22); gradients/opacity have no
# direct tk.Canvas equivalent so they are approximated with flat blended
# colours (see `blend()`) -- a visual, not behavioural, divergence.
# ---------------------------------------------------------------------------
PAD_L = 56.0
PAD_R = 16.0
PAD_TOP = 8.0
HIT_Y_FRAC = 0.78
TAIL_FRAC = 0.22
FLASH_WINDOW_S = 0.05
RECEPTOR_FLASH_S = 0.16
EDIT_HIT_TOL_FRAC = 0.035
MIN_BEAT_PX = 6.0
NOTE_SNAP_PX = 8.0
CLICK_SLOP_PX = 4.0
FALL_MIN = 1.0
FALL_MAX = 8.0
# v4.9.0 — Zoom is a SEPARATE axis from fall speed. It composes with _fall as
# effective_fall = _fall / _zoom, so it magnifies (zoom in = detail) / shows more
# (zoom out) WITHOUT changing the fall-speed the user set. 1.0 = no zoom.
ZOOM_MIN = 0.25
ZOOM_MAX = 4.0
ZOOM_DEFAULT = 1.0
TICK_MS = 16          # ~60 fps

# note/grid/hit-line colours (opaque approximations of v5's rgba palette)
WELL_LINE = "#2a2740"
BAR_EDIT = "#c4b5fd"
BAR_PLAY = "#5c5a78"
BEAT_EDIT = "#a0a0cd"
BEAT_PLAY = "#48485f"
SUB_EDIT = "#3c3a58"
# v4.9.1 — subdivision lines are now shown in VIEW mode too (owner: the 1/8–1/32
# grid selector "doesn't change the grid on the chart"). It was edit-only before
# (HTML-faithful), so outside edit mode the selector only affected snap. This is
# a deliberately DIM tint (below BEAT_PLAY) so a dense 1/32 grid reads as a quiet
# guide during view/playback, not clutter.
SUB_PLAY = "#25253a"
BARNUM_EDIT = "#8aa7cc"
BARNUM_PLAY = "#4d6c82"
HIT_EDIT = "#00ff88"
HIT_EDIT_HALO = "#123322"
HIT_PLAY = "#a89bd6"
HIT_PLAY_HALO = "#241a38"
NOW_EDIT = "#00ff88"
NOW_PLAY = "#a89bd6"
RING_ACCENT = "#ff69b4"
RING_FLAG = "#ff8c00"
SEL_RING = "#ffffff"
SNAP_GUIDE = "#ffff00"
HOVER_RING = "#ffffff"
EDIT_BORDER = "#00ff88"
EDIT_BADGE_BG = "#00ff88"
EDIT_BADGE_FG = "#0d0d1a"
COUNTIN_COLOR = "#ff5050"


class FallingCanvas(tk.Frame):
    """The falling-note preview + in-view editor of a shared drum chart."""

    def __init__(self, parent):
        super().__init__(parent, background=BG)
        self.canvas = tk.Canvas(self, background="#10101f",
                                 highlightthickness=0, cursor="arrow")
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self._model = eng.ChartModel(120.0)
        self._selection = eng.Selection()
        self._bpm = 120.0
        self._duration = 0.0
        self._now = 0.0
        self._fall = 3.0
        self._zoom = ZOOM_DEFAULT   # v4.9.0 — inspection zoom, independent of _fall
        self._speed = 1.0
        self._playing = False
        self._last_drawn = 0
        self._hide_passed = False
        self._note_scale = 1.0
        self._snap_on = True
        self._grid_subs = 4
        self._squares = False
        self._cymbal_oval = False   # v4.9.1 — cymbals drawn as wide ovals
        self._classic_style = False  # v4.9.2 — clean rect/circle + white-outline notes
        self._kick_line = False
        self._beat_grid = True
        self._lane_visible = [True] * LANE_COUNT
        self._edit_mode = False
        self._countin_text = ""
        self._capture_hook = None
        # interaction state
        self._mode = None            # None|maybe_place|maybe_move|move|erase|band
        self._press_xy = None
        self._band_rect = None       # (x0,y0,x1,y1) live rubber-band, or None
        self._band_additive = False  # Ctrl/Shift held when the band started
        self._move_orig = {}
        self._move_anchor_t = 0.0
        self._move_anchor_lane = 0
        self._move_id = None
        self._erased_any = False
        self._hover_id = None
        self._snap_guide_t = None
        # clock
        self._anchor_wall = 0.0
        self._anchor_song = 0.0
        self._tick_job = None

        # callbacks the host tab wires in (plain callables; never Qt signals)
        self.on_time = None            # (t) -- fired every tick/seek
        self.on_finished = None        # () -- playback reached duration
        self.on_edited = None          # () -- after any chart mutation
        self.on_fall_changed = None    # (f) -- fall-speed −/+ buttons
        self.on_zoom_changed = None    # (z) -- Ctrl+wheel / Zoom −/+ buttons

        self.canvas.bind("<Configure>", lambda _e: self._redraw())
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-3>", self._on_right_press)
        self.canvas.bind("<B3-Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-3>", self._on_right_release)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", self._on_leave)
        self.canvas.bind("<MouseWheel>", self._on_wheel)      # Windows
        self.canvas.bind("<Button-4>", lambda e: self._on_wheel_unix(e, 1))
        self.canvas.bind("<Button-5>", lambda e: self._on_wheel_unix(e, -1))

    def _emit(self, name, *args):
        cb = getattr(self, name, None)
        if cb is not None:
            try:
                cb(*args)
            except Exception:
                pass

    # ----- data --------------------------------------------------------
    def set_chart(self, notes, bpm):
        # Drop non-finite note times (breaker fix, 2026-07-20): a NaN/inf time
        # from a malformed import reaches the engine's TimeIndex.rebuild and
        # raises "cannot convert float NaN to integer", taking the tab down.
        notes = [n for n in (notes or [])
                 if math.isfinite(getattr(n, "time", 0.0))
                 and abs(getattr(n, "time", 0.0)) <= _MAX_NOTE_TIME]
        self._model = eng.ChartModel.from_notes(notes, bpm)
        self._selection.clear()
        self._bpm = float(bpm) if bpm > 0 else 120.0
        self._recalc_duration()
        self._hover_id = None
        self._snap_guide_t = None
        self.stop()

    @property
    def model(self):
        return self._model

    @property
    def total_notes(self) -> int:
        return len(self._model.notes)

    @property
    def last_drawn(self) -> int:
        return self._last_drawn

    @property
    def now(self) -> float:
        return self._now

    @property
    def duration(self) -> float:
        return self._duration

    @property
    def is_playing(self) -> bool:
        return self._playing

    @property
    def edit_mode(self) -> bool:
        return self._edit_mode

    @property
    def snap_on(self) -> bool:
        return self._snap_on

    @property
    def fall(self) -> float:
        return self._fall

    @property
    def zoom(self) -> float:
        return self._zoom

    def _eff_fall(self) -> float:
        # Seconds of chart shown in the window = fall speed / zoom. Zoom in
        # (>1) => fewer seconds visible, magnified; zoom out (<1) => more.
        z = self._zoom if self._zoom > 0 else 1.0
        return self._fall / z

    @property
    def selection_size(self) -> int:
        return len(self._selection.ids)

    def _recalc_duration(self):
        last = eng.chart_duration(self._model.notes)
        self._duration = (last + 0.5) if self._model.notes else 0.0

    # ----- transport -----------------------------------------------------
    def play(self):
        if self._playing or self._duration <= 0:
            return
        if self._now >= self._duration:
            self._now = 0.0
        self._anchor_wall = time.perf_counter()
        self._anchor_song = self._now
        self._playing = True
        self._schedule_tick()

    def pause(self):
        if not self._playing:
            return
        self._playing = False
        self._cancel_tick()

    def toggle(self):
        self.pause() if self._playing else self.play()

    def stop(self):
        self._playing = False
        self._cancel_tick()
        self._now = 0.0
        self._emit("on_time", 0.0)
        self._redraw()

    def seek(self, t_sec):
        self._now = max(0.0, min(self._duration, float(t_sec)))
        self._anchor_wall = time.perf_counter()
        self._anchor_song = self._now
        self._emit("on_time", self._now)
        self._redraw()

    def sync_clock(self, t_sec):
        """Re-anchor the play clock to an authoritative external time (a
        future audio-engine hook). Public seam; not proactively polled by
        this tab (mixer_song_time is DEFERRED -- see the module docstring)."""
        self._anchor_song = max(0.0, float(t_sec))
        self._anchor_wall = time.perf_counter()
        if not self._playing:
            self._now = self._anchor_song
            self._emit("on_time", self._now)
            self._redraw()

    def set_speed(self, speed):
        self._anchor_song = self._now
        self._anchor_wall = time.perf_counter()
        self._speed = max(0.1, float(speed))

    def set_fall(self, seconds):
        self._fall = max(FALL_MIN, min(FALL_MAX, float(seconds)))
        self._redraw()

    def set_zoom(self, zoom):
        self._zoom = max(ZOOM_MIN, min(ZOOM_MAX, float(zoom)))
        self._redraw()

    def set_hide_passed(self, hide):
        self._hide_passed = bool(hide)
        self._redraw()

    def set_note_scale(self, scale):
        self._note_scale = max(0.3, float(scale))
        self._redraw()

    def set_snap(self, on):
        self._snap_on = bool(on)

    def set_grid_subs(self, subs):
        self._grid_subs = max(1, int(subs))
        self._redraw()

    def set_squares(self, on):
        self._squares = bool(on)
        self._redraw()

    def set_cymbal_oval(self, on):
        self._cymbal_oval = bool(on)
        self._redraw()

    def set_classic_style(self, on):
        self._classic_style = bool(on)
        self._redraw()

    def set_kick_line(self, on):
        self._kick_line = bool(on)
        self._redraw()

    def set_beat_grid(self, on):
        self._beat_grid = bool(on)
        self._redraw()

    def set_lane_visible(self, lane, visible):
        if 0 <= lane < LANE_COUNT:
            self._lane_visible[lane] = bool(visible)
            self._redraw()

    def lane_visible(self, lane):
        """True if *lane* is shown. A hidden lane draws nothing, flashes
        nothing at the hit line, and (via the tab) fires no synth voice."""
        return bool(self._lane_visible[lane]) if 0 <= lane < LANE_COUNT else True

    def set_countin_text(self, text):
        self._countin_text = text or ""
        self._redraw()

    def set_capture_hook(self, hook):
        self._capture_hook = hook

    def commit_take(self, hits) -> int:
        # Harden against malformed hits (string/None lane, dict/NaN vel or time):
        # a bad tuple crashed the range test or int() below (breaker fix F5).
        clean = []
        for h in (hits or []):
            try:
                t, lane, vel = h[0], int(h[1]), h[2]
            except (TypeError, ValueError, IndexError):
                continue
            ft, fv = _finite_or(t, None), _finite_or(vel, None)
            if ft is None or fv is None or not (0 <= lane < LANE_COUNT):
                continue
            clean.append((ft, lane, max(1, min(127, int(fv)))))
        hits = clean
        if not hits:
            return 0
        self._model.push_undo()
        for t, lane, vel in hits:
            st = eng.snap_time(self._model, max(0.0, float(t)), self._snap_on,
                               self._grid_subs)
            self._model.add_note(st.t, int(lane), int(vel))
        self._recalc_duration()
        self._redraw()
        self._emit("on_edited")
        return len(hits)

    # ----- edit mode -----------------------------------------------------
    def set_edit_mode(self, on):
        self._edit_mode = bool(on)
        self._mode = None
        self._erased_any = False
        self._press_xy = None
        self._band_rect = None
        if self._move_orig:
            self.end_move()
        self._apply_cursor()
        self._redraw()

    def _apply_cursor(self):
        if not self._edit_mode:
            self.canvas.configure(cursor="arrow")
        elif self._mode in ("move", "maybe_move"):
            self.canvas.configure(cursor="fleur")
        elif self._hover_id is not None:
            self.canvas.configure(cursor="hand2")
        else:
            self.canvas.configure(cursor="crosshair")

    def place_at(self, x, y):
        lane = self._lane_at(x)
        if lane < 0:
            return None
        return self.place_at_time(self._time_at(y), lane)

    def place_at_time(self, t, lane):
        if not (0 <= lane < LANE_COUNT):
            return None
        st = eng.snap_time(self._model, max(0.0, t), self._snap_on, self._grid_subs)
        self._model.push_undo()
        n = self._model.add_note(st.t, lane, 100)
        self._selection.ids = {n.id}
        self._recalc_duration()
        self._redraw()
        self._emit("on_edited")
        return n

    def select_at(self, x, y, additive):
        n = self._note_at(x, y)
        if n is None:
            if not additive:
                self._selection.clear()
                self._redraw()
            return None
        if additive:
            self._selection.toggle(n.id)
        else:
            self._selection.ids = {n.id}
        self._redraw()
        return n

    def select_in_rect(self, x0, y0, x1, y1, additive):
        """Rubber-band select: every VISIBLE note whose glyph centre falls in
        the dragged rectangle. Additive (Ctrl/Shift) unions with the current
        selection; otherwise it replaces it. Hidden lanes are skipped."""
        x0, x1 = sorted((x0, x1))
        y0, y1 = sorted((y0, y1))
        if not additive:
            self._selection.clear()
        # y maps to time; widen the query slightly past the rect so a note whose
        # centre sits on the boundary still counts.
        t_a, t_b = self._time_at(y0), self._time_at(y1)
        lo, hi = min(t_a, t_b), max(t_a, t_b)
        for n in self._model.get_index().iter_window(max(0.0, lo - 0.5), hi + 0.5):
            if not self._lane_visible[n.lane]:
                continue
            ny = self._y_of(n.time)
            if not (y0 <= ny <= y1):
                continue
            # A full-width kick line counts on its y alone (spans every column).
            if self._kick_line and n.lane == 7:
                self._selection.ids.add(n.id)
            elif x0 <= self._col_x(n.lane) <= x1:
                self._selection.ids.add(n.id)
        self._selection.prune(self._model)
        self._redraw()

    def delete_at(self, x, y) -> bool:
        n = self._note_at(x, y)
        if n is None:
            return False
        self._model.push_undo()
        self._model.delete_by_ids({n.id})
        self._selection.prune(self._model)
        self._recalc_duration()
        self._redraw()
        self._emit("on_edited")
        return True

    def erase_at(self, x, y) -> bool:
        n = self._note_at(x, y)
        if n is None:
            return False
        if not self._erased_any:
            self._model.push_undo()
            self._erased_any = True
        self._model.delete_by_ids({n.id})
        self._selection.prune(self._model)
        self._recalc_duration()
        self._redraw()
        self._emit("on_edited")
        return True

    def delete_selection(self) -> int:
        if not self._selection.ids:
            return 0
        self._model.push_undo()
        k = self._model.delete_by_ids(set(self._selection.ids))
        self._selection.clear()
        self._recalc_duration()
        self._redraw()
        self._emit("on_edited")
        return k

    def begin_move(self, x, y) -> bool:
        n = self._note_at(x, y)
        if n is None:
            return False
        if n.id not in self._selection.ids:
            self._selection.ids = {n.id}
        self._model.push_undo()
        self._move_orig = {m.id: (m.time, m.lane) for m in self._model.notes
                           if m.id in self._selection.ids}
        self._move_anchor_t = self._time_at(y)
        # Anchor on the grabbed note's OWN lane, not the click column (breaker
        # fix, 2026-07-20): a full-width kickline can be grabbed from any column,
        # so a click-column anchor would skew the lane delta on the first drag.
        self._move_anchor_lane = n.lane
        self._move_id = n.id
        return True

    def update_move(self, x, y):
        if not self._move_orig:
            return
        dt_raw = self._time_at(y) - self._move_anchor_t
        d_lane = self._lane_at_clamped(x) - self._move_anchor_lane
        skip = set(self._move_orig.keys())
        tol_s = (NOTE_SNAP_PX / max(1.0, self._span())) * self._eff_fall()
        self._snap_guide_t = None
        prim = self._move_orig.get(self._move_id)
        snap_dt = dt_raw
        if prim is not None and self._snap_on:
            res = eng.snap_time(self._model, prim[0] + dt_raw, True,
                                self._grid_subs, note_snap_tol_s=tol_s,
                                skip_ids=skip)
            snap_dt = res.t - prim[0]
            self._snap_guide_t = res.guide_t
        mapping = {}
        for nid, (ot, ol) in self._move_orig.items():
            mapping[nid] = (max(0.0, ot + snap_dt), ol + d_lane)
        self._model.set_positions(mapping)
        self._recalc_duration()
        self._redraw()
        self._emit("on_edited")

    def end_move(self):
        self._model.sort_notes()
        self._recalc_duration()
        self._move_orig = {}
        self._move_id = None
        self._snap_guide_t = None

    def undo(self) -> bool:
        if not self._model.undo():
            return False
        self._selection.prune(self._model)
        self._recalc_duration()
        self._redraw()
        self._emit("on_edited")
        return True

    def redo(self) -> bool:
        if not self._model.redo():
            return False
        self._selection.prune(self._model)
        self._recalc_duration()
        self._redraw()
        self._emit("on_edited")
        return True

    # ----- mouse ---------------------------------------------------------
    def _on_press(self, event):
        if not self._edit_mode:
            return
        x, y = event.x, event.y
        self._press_xy = (x, y)
        # Additive = Shift OR Ctrl (owner: Ctrl+click multi-select).
        additive = bool(event.state & 0x0001) or bool(event.state & 0x0004)
        self._band_additive = additive
        n = self._note_at(x, y)
        if n is not None:
            # Keep an existing MULTI-selection on a plain press of a note already
            # in it (breaker fix, 2026-07-20): collapsing here defeated group
            # drag-move. A plain click that does NOT drag collapses to this note
            # on release instead (see _on_release).
            if additive or n.id not in self._selection.ids:
                self.select_at(x, y, additive=additive)
            self._mode = "maybe_move"
        else:
            self._mode = "maybe_place"
        self._apply_cursor()

    def _on_motion(self, event):
        x, y = event.x, event.y
        if not self._edit_mode:
            return
        if self._mode == "erase":
            self.erase_at(x, y)
            return
        if self._mode == "maybe_move" and self._press_xy is not None:
            moved = math.hypot(x - self._press_xy[0], y - self._press_xy[1])
            if moved > CLICK_SLOP_PX and self.begin_move(*self._press_xy):
                self._mode = "move"
                self._apply_cursor()
        if self._mode == "move":
            self.update_move(x, y)
            return
        # Drag from empty space = rubber-band select (instead of placing a note).
        if self._mode == "maybe_place" and self._press_xy is not None:
            moved = math.hypot(x - self._press_xy[0], y - self._press_xy[1])
            if moved > CLICK_SLOP_PX:
                self._mode = "band"
                self._apply_cursor()
        if self._mode == "band" and self._press_xy is not None:
            self._band_rect = (self._press_xy[0], self._press_xy[1], x, y)
            self._redraw()
            return
        n = self._note_at(x, y)
        new_hover = n.id if n is not None else None
        if new_hover != self._hover_id:
            self._hover_id = new_hover
            self._apply_cursor()
            self._redraw()

    def _on_release(self, event):
        if not self._edit_mode:
            return
        if self._mode == "band":
            if self._band_rect is not None:
                self.select_in_rect(*self._band_rect,
                                    additive=self._band_additive)
            self._band_rect = None
            self._redraw()
        elif self._mode == "maybe_place" and self._press_xy is not None:
            self.place_at(*self._press_xy)
        elif self._mode == "move":
            self.end_move()
        elif self._mode == "maybe_move" and self._press_xy is not None:
            # Plain click (no drag) on a note that was already in a MULTI
            # selection: collapse to just that note now (breaker fix). Kept out
            # of _on_press so a drag can still move the whole group.
            additive = bool(event.state & 0x0001) or bool(event.state & 0x0004)
            if not additive and self.selection_size > 1:
                self.select_at(*self._press_xy, additive=False)
        self._mode = None
        self._erased_any = False
        self._apply_cursor()

    def _on_right_press(self, event):
        if not self._edit_mode:
            return
        self._mode = "erase"
        self._erased_any = False
        self.erase_at(event.x, event.y)

    def _on_right_release(self, _event):
        if not self._edit_mode:
            return
        self._mode = None
        self._erased_any = False
        self._apply_cursor()

    def _on_leave(self, _event):
        if self._hover_id is not None:
            self._hover_id = None
            self._redraw()

    def _on_wheel(self, event):
        delta = event.delta
        if delta == 0:
            return "break"
        ctrl = bool(event.state & 0x0004)
        if ctrl:
            # v4.9.0 — Ctrl+wheel = ZOOM (separate from fall speed). Wheel up =
            # zoom IN (standard convention).
            factor = 1.12 if delta > 0 else (1.0 / 1.12)
            self.set_zoom(self._zoom * factor)
            self._emit("on_zoom_changed", self._zoom)
            return "break"
        if not self._playing:
            dt = (delta / max(1.0, self._span())) * self._eff_fall()
            self.seek(self._now + dt)
        return "break"

    def _on_wheel_unix(self, event, sign):
        ctrl = bool(event.state & 0x0004)
        if ctrl:
            # v4.9.0 — Ctrl+wheel = ZOOM. Wheel up = zoom IN.
            factor = 1.12 if sign > 0 else (1.0 / 1.12)
            self.set_zoom(self._zoom * factor)
            self._emit("on_zoom_changed", self._zoom)
            return "break"
        if not self._playing:
            dt = (sign * 40.0 / max(1.0, self._span())) * self._eff_fall()
            self.seek(self._now + dt)
        return "break"

    # ----- key hooks (place_at_time / capture_hook driven by the TAB) ----
    # (all keyboard handling lives on PreviewTab -- see its _key_* methods --
    # so this widget never needs Tk keyboard focus of its own.)

    # ----- clock -----------------------------------------------------------
    def _schedule_tick(self):
        self._cancel_tick()
        self._tick_job = self.after(TICK_MS, self._on_tick)

    def _cancel_tick(self):
        if self._tick_job is not None:
            try:
                self.after_cancel(self._tick_job)
            except Exception:
                pass
            self._tick_job = None

    def _on_tick(self):
        self._tick_job = None
        if not self._playing:
            return
        elapsed = (time.perf_counter() - self._anchor_wall) * self._speed
        self._now = self._anchor_song + elapsed
        if self._now >= self._duration:
            self._now = self._duration
            self.pause()
            self._emit("on_time", self._now)
            self._redraw()
            self._emit("on_finished")
            return
        self._emit("on_time", self._now)
        self._redraw()
        self._schedule_tick()

    # ----- geometry helpers ------------------------------------------------
    def _hit_y(self):
        return max(1, self.canvas.winfo_height()) * HIT_Y_FRAC

    def _span(self):
        return self._hit_y() - PAD_TOP

    def _play_w(self):
        return max(40.0, self.canvas.winfo_width() - PAD_L - PAD_R)

    def _col_w(self):
        return self._play_w() / LANE_COUNT

    def _col_x(self, i):
        return PAD_L + (i + 0.5) * self._col_w()

    def _y_of(self, t):
        return self._hit_y() - (t - self._now) / self._eff_fall() * self._span()

    def _time_at(self, y):
        span = self._span()
        if span <= 0:
            return self._now
        return max(0.0, self._now + (self._hit_y() - y) / span * self._eff_fall())

    def _lane_at(self, x):
        cw = self._col_w()
        if cw <= 0:
            return -1
        i = int((x - PAD_L) // cw)
        return i if 0 <= i < LANE_COUNT else -1

    def _lane_at_clamped(self, x):
        # Like _lane_at but maps an OUT-OF-BOUNDS x to the nearest EDGE lane by
        # position (breaker fix, 2026-07-20). update_move used max(0, _lane_at),
        # which collapsed BOTH edges to lane 0 — so dragging a note 1px past the
        # RIGHT edge flipped it to lane 0 instead of clamping to the last lane.
        raw = self._lane_at(x)
        if raw >= 0:
            return raw
        return 0 if x < PAD_L else (LANE_COUNT - 1)

    def _visible_window(self):
        # Derive the window from the ACTUAL canvas edges, not fall-based margins
        # (breaker fix, 2026-07-20). The visible PIXEL span is not `eff` seconds
        # — hit_y/span can exceed 1 — so the old (+0.1 top / TAIL_FRAC bottom)
        # under-covered the top/bottom bands at high zoom-out (eff = fall/zoom up
        # to 32 s): on-screen notes went unpainted AND unclickable. Map y=-30
        # (just above the top edge) and y=h+30 (just below the bottom) to time
        # via the same _time_at math, so every on-screen note is in-window.
        eff = self._eff_fall()
        span = self._span()
        if span <= 0:
            return (self._now - eff * TAIL_FRAC - 0.1, self._now + eff + 0.1)
        h = max(1, self.canvas.winfo_height())
        hit_y = self._hit_y()
        t_top = self._now + (hit_y + 30.0) / span * eff
        t_bot = self._now - (h + 30.0 - hit_y) / span * eff
        return (t_bot - 0.05, t_top + 0.05)

    def _note_at(self, x, y):
        lane = self._lane_at(x)
        if lane < 0:
            return None
        # PIXEL-distance hit test (breaker fix, 2026-07-20): the old test used
        # _time_at(y), which CLAMPS a below-render y to t=0 — so a click far
        # below an early note (the pre-roll band) collapsed onto t=0 and matched
        # a note at ~t=0 from 100+ px away. Measuring |_y_of(note) - y| in pixels
        # is immune to it.
        min_tol = EDIT_HIT_TOL_FRAC * self._span()
        t0, t1 = self._visible_window()
        best = None
        best_d = None
        for n in self._model.get_index().iter_window(t0, t1):
            # A Kick drawn as a full-width line (kick_line) is hittable ANYWHERE
            # on the line, not just column 7 (breaker fix, 2026-07-20): the
            # render spans the whole play width but the lane is derived from x.
            is_kickline = self._kick_line and n.lane == 7
            if not is_kickline and n.lane != lane:
                continue
            # Only hit notes that are actually RENDERED — mirror _draw_notes'
            # filters (breaker fix, 2026-07-20): otherwise an edit click / erase
            # sweep selects & DELETES notes in a hidden lane, or (hide-passed)
            # behind the hit line, that the user cannot see.
            if not self._lane_visible[n.lane]:
                continue
            if self._hide_passed and n.time < self._now:
                continue
            # Tolerance tracks the note's DRAWN size so the whole marker is
            # clickable, not a fixed ~15 px band (breaker fix): the old fixed
            # tolerance was smaller than a default circle (r≈27 px, up to 44).
            kind, _w, hgt, r = self._note_geom(n.lane, n.vel)
            reach = max(min_tol, (r if kind == "circle" else hgt / 2.0)) + 2.0
            d = abs(self._y_of(n.time) - y)
            if d <= reach and (best_d is None or d < best_d):
                best_d = d
                best = n
        return best

    def _note_geom(self, lane, vel):
        """(kind, w, h, r) in px. kind in {circle, bar, kickline}."""
        cw = self._col_w()
        ns = self._note_scale
        vscale = 0.85 + min(1.0, vel / 110.0) * 0.4
        if lane == 7 and self._kick_line:
            return ("kickline", self._play_w(), 5.0 * ns, 2.0)
        if lane == 7:
            return ("bar", cw * 0.86 * ns, 9.0 * ns * vscale, 5.0)
        shape = LANE_SHAPES[lane]
        if shape == "circle" and not self._squares:
            r = min(cw * 0.30, 22.0) * ns * vscale
            if self._cymbal_oval:
                # wide, flat ellipse — easier to read timing in rapid cymbal runs
                return ("oval", r * 2.6, r * 1.3, r)
            return ("circle", r * 2.0, r * 2.0, r)
        return ("bar", cw * 0.72 * ns, 15.0 * ns * vscale, 5.0)

    # ----- paint -------------------------------------------------------
    def _redraw(self):
        try:
            self.canvas.delete("dyn")
        except tk.TclError:
            return
        edit = self._edit_mode
        t0, t1 = self._visible_window()
        self._draw_wells(edit)
        if self._bpm > 0 and (edit or self._beat_grid):
            self._draw_grid(t0, t1, edit)
        self._draw_notes(t0, t1)
        self._draw_hit_line(edit)
        self._draw_receptors()
        self._draw_lane_labels()
        if edit:
            self._draw_edit_overlay()
        if self._countin_text:
            self._draw_countin()

    def _draw_wells(self, edit):
        cw = self._col_w()
        hit_y = self._hit_y()
        top = PAD_TOP
        alpha = 0.14 if edit else 0.08
        for i in range(LANE_COUNT):
            x0 = PAD_L + i * cw
            fill = blend(BG, LANE_COLORS[i], alpha)
            self.canvas.create_rectangle(x0 + 1, top, x0 + cw - 1, hit_y,
                                          fill=fill, outline="", tags="dyn")
        for i in range(LANE_COUNT + 1):
            x = PAD_L + i * cw
            self.canvas.create_line(x, top, x, hit_y + 26, fill=WELL_LINE,
                                     tags="dyn")

    def _draw_grid(self, t0, t1, edit):
        # A non-finite view window (from an absurd seek) would overflow int(t0/sub)
        # below (breaker fix F4): bail rather than crash the redraw.
        if not (math.isfinite(t0) and math.isfinite(t1)):
            return
        beat = 60.0 / self._bpm
        sub = beat / max(1, self._grid_subs)
        sub_px = sub / self._eff_fall() * self._span()
        if sub_px < MIN_BEAT_PX:
            sub = beat
        if sub <= 0 or (t1 - t0) / sub > 4096.0:
            return   # hostile/absurd bpm would loop per-subdivision per frame
        i0 = max(0, int(t0 / sub))
        i1 = int(t1 / sub) + 1
        h = self.canvas.winfo_height()
        x_left = PAD_L
        x_right = PAD_L + self._play_w()
        for i in range(i0, i1):
            t = i * sub
            y = self._y_of(t)
            if y < -2 or y > h + 2:
                continue
            beat_num = round(t / beat)
            is_beat = abs(t / beat - beat_num) < 1e-6
            is_bar = is_beat and beat_num % 4 == 0
            if is_bar:
                color, width = (BAR_EDIT if edit else BAR_PLAY), 2
            elif is_beat:
                color, width = (BEAT_EDIT if edit else BEAT_PLAY), 1
            else:
                # v4.9.1 — subdivisions now draw in BOTH modes (dim in view) so
                # the 1/8–1/32 selector visibly changes the chart grid, not just
                # snap. The MIN_BEAT_PX collapse above still prevents a sub-pixel
                # grid from cluttering at far zoom-out.
                color, width = (SUB_EDIT if edit else SUB_PLAY), 1
            self.canvas.create_line(x_left, y, x_right, y, fill=color,
                                     width=width, tags="dyn")
            if is_bar:
                self.canvas.create_text(
                    8, y - 6, text=str(int(beat_num // 4 + 1)), anchor="w",
                    fill=(BARNUM_EDIT if edit else BARNUM_PLAY), font=F_MONO,
                    tags="dyn")

    def _draw_notes(self, t0, t1):
        drawn = 0
        sel = self._selection.ids
        # v4.9.1 — HQ sprites (owner: notes looked "fuzzy/low-rez"): when the
        # parakit_preview_sprites sidecar is importable, notes blit pre-rendered
        # supersampled gradient+glow sprites (the HTML prototype's exact look:
        # white-hot core -> lane color -> translucent edge, 12px glow) instead
        # of flat aliased Tk ovals. One create_image per note also REPLACES the
        # old oval+ring pair, so per-frame canvas churn goes DOWN. Missing
        # sidecar => the legacy vector branch below is byte-identical behavior.
        for n in self._model.get_index().iter_window(t0, t1):
            if not self._lane_visible[n.lane]:
                continue
            if self._hide_passed and n.time < self._now:
                continue
            y = self._y_of(n.time)
            h = self.canvas.winfo_height()
            if y < PAD_TOP - 30 or y > h + 20:
                continue
            kind, w, hh, r = self._note_geom(n.lane, n.vel)
            cx = (self._col_x(n.lane) if kind != "kickline"
                  else PAD_L + self._play_w() / 2.0)
            past = n.time < self._now - 0.05
            drawn += 1
            base = LANE_COLORS[n.lane]
            ghost = n.vel < eng.GHOST_VEL and kind != "kickline"
            dt_flash = abs(n.time - self._now)
            flashing = dt_flash <= FLASH_WINDOW_S
            # rings: selection > flag > accent (shared by both branches)
            # Kick (lane 7) is a foot pedal -- it never gets the accent ring
            # (the accent ring flags a loud hand hit; a kick doesn't compete
            # for a hand, so it's excluded).
            ring_col = None
            if n.id in sel:
                ring_col = SEL_RING
            elif n.flag:
                ring_col = RING_FLAG
            elif n.lane != 7 and n.vel >= eng.ACCENT_VEL:
                ring_col = RING_ACCENT
            if _spr is not None:
                # ---- HQ sprite branch ----
                skind = kind
                if kind == "bar" and n.lane == 7:
                    skind = "kickbar"          # kick bar = flat+glow (HTML)
                if flashing:
                    # HTML overdraws white at fading alpha; Tk images can't
                    # alpha-fade per frame, so bake 3 bucketed white-blends —
                    # bounded cache, same "flash toward white" read.
                    frac = max(0.0, 1.0 - dt_flash / FLASH_WINDOW_S)
                    bucket = max(1, min(3, int(frac * 3) + 1)) / 3.0
                    scol = blend(base, "#ffffff", bucket)
                    sstate = "normal"
                elif ghost:
                    scol, sstate = base, "ghost"
                elif past:
                    scol, sstate = base, "past"
                else:
                    scol, sstate = base, "normal"
                try:
                    img = _spr.note_sprite(scol, skind, int(round(w)),
                                           int(round(hh)), sstate,
                                           ring=(ring_col if kind != "kickline"
                                                 else None),
                                           style=("classic" if self._classic_style
                                                  else "hq"),
                                           master=self.canvas)
                    self.canvas.create_image(cx, y, image=img, tags="dyn")
                    continue
                except Exception:
                    pass    # sprite failure -> vector fallback below
            # ---- legacy vector branch (fallback) ----
            if flashing:
                fill = blend(base, "#ffffff", max(0.0, 1.0 - dt_flash / FLASH_WINDOW_S))
                outline = "#ffffff"
            elif ghost:
                fill = ""
                outline = base
            elif past:
                fill = blend(base, BG, 0.55)
                outline = blend("#ffffff", BG, 0.55)
            else:
                fill = base
                outline = "#ffffff"
            coords = self._shape_coords(kind, cx, y, w, hh, r)
            if kind in ("circle", "oval"):
                self.canvas.create_oval(*coords, fill=fill, outline=outline,
                                         width=1.5, tags="dyn")
            else:
                self.canvas.create_rectangle(*coords, fill=fill, outline=outline,
                                              width=1.2, tags="dyn")
            if ring_col and kind != "kickline":
                rc = self._shape_coords(kind, cx, y, w + 3, hh + 3, r + 1.5)
                if kind == "circle":
                    self.canvas.create_oval(*rc, outline=ring_col, width=2,
                                             tags="dyn")
                else:
                    self.canvas.create_rectangle(*rc, outline=ring_col,
                                                  width=2, tags="dyn")
        self._last_drawn = drawn

    def _shape_coords(self, kind, cx, y, w, h, r):
        if kind == "circle":
            return (cx - r, y - r, cx + r, y + r)
        return (cx - w / 2.0, y - h / 2.0, cx + w / 2.0, y + h / 2.0)

    def _draw_hit_line(self, edit):
        hit_y = self._hit_y()
        x0 = PAD_L
        x1 = PAD_L + self._play_w()
        halo = HIT_EDIT_HALO if edit else HIT_PLAY_HALO
        thin = HIT_EDIT if edit else HIT_PLAY
        self.canvas.create_line(x0, hit_y, x1, hit_y, fill=halo, width=8,
                                 tags="dyn")
        self.canvas.create_line(x0, hit_y, x1, hit_y, fill=thin, width=2,
                                 tags="dyn")
        self.canvas.create_text(8, hit_y - 10, text="NOW", anchor="w",
                                 fill=(NOW_EDIT if edit else NOW_PLAY),
                                 font=F_MONO_SMALL_B, tags="dyn")

    def _draw_receptors(self):
        cw = self._col_w()
        hit_y = self._hit_y()
        ns = self._note_scale
        for lane in range(LANE_COUNT):
            col = LANE_COLORS[lane]
            visible = self._lane_visible[lane]
            # A hidden lane never animates: no crossing flash on its receptor
            # (its notes aren't drawn and its synth voice is muted either).
            flash_k = self._receptor_flash(lane) if visible else 0.0
            pen_col = blend(BG, col, (0.55 if visible else 0.20) + flash_k * 0.45)
            cx = self._col_x(lane)
            if lane == 7 and self._kick_line:
                # No persistent full-width kick receptor in line mode: a static
                # full-width kick-colored strip at the hit line reads as a kick
                # note stuck on the line (owner-reported; matches Practice/v5,
                # which omit it). The full-width NOW line already marks the hit
                # point. Show it only as a brief flash when a kick actually lands.
                if flash_k > 0.02:
                    self.canvas.create_rectangle(
                        PAD_L, hit_y - 3.5, PAD_L + self._play_w(), hit_y + 3.5,
                        outline=pen_col, width=2.5, tags="dyn")
            elif LANE_SHAPES[lane] == "circle" and not self._squares:
                r = min(cw * 0.30, 22.0) * ns * (1.0 + flash_k * 0.15)
                self.canvas.create_oval(cx - r, hit_y - r, cx + r, hit_y + r,
                                         outline=pen_col, width=2.5, tags="dyn")
            else:
                bw = cw * 0.72 * ns * (1.0 + flash_k * 0.1)
                bh = (10.0 if lane == 7 else 16.0) * ns * (1.0 + flash_k * 0.2)
                self.canvas.create_rectangle(
                    cx - bw / 2.0, hit_y - bh / 2.0, cx + bw / 2.0, hit_y + bh / 2.0,
                    outline=pen_col, width=2.5, tags="dyn")

    def _receptor_flash(self, lane) -> float:
        best = 0.0
        lo = self._now - RECEPTOR_FLASH_S
        for n in self._model.get_index().iter_window(lo, self._now + 0.001):
            if n.lane != lane:
                continue
            dt = self._now - n.time
            if 0.0 <= dt <= RECEPTOR_FLASH_S:
                best = max(best, 1.0 - dt / RECEPTOR_FLASH_S)
        return best

    def _draw_lane_labels(self):
        hit_y = self._hit_y()
        for lane in range(LANE_COUNT):
            if not self._lane_visible[lane]:
                continue
            cx = self._col_x(lane)
            self.canvas.create_text(
                cx, hit_y + 34, text=LANE_NAMES[lane].upper(),
                fill=LANE_COLORS[lane], font=F_MONO_SMALL_B, tags="dyn")

    def _draw_edit_overlay(self):
        w = max(1, self.canvas.winfo_width())
        h = max(1, self.canvas.winfo_height())
        self.canvas.create_rectangle(2, 2, w - 2, h - 2, outline=EDIT_BORDER,
                                      width=2, tags="dyn")
        if self._band_rect is not None:
            bx0, by0, bx1, by1 = self._band_rect
            self.canvas.create_rectangle(bx0, by0, bx1, by1, outline=SEL_RING,
                                         width=1, dash=(3, 2), tags="dyn")
        if self._snap_guide_t is not None:
            y = self._y_of(self._snap_guide_t)
            x0, x1 = PAD_L, PAD_L + self._play_w()
            self.canvas.create_line(x0, y - 1, x1, y - 1, fill=SNAP_GUIDE,
                                     tags="dyn")
            self.canvas.create_line(x0, y + 1, x1, y + 1, fill=SNAP_GUIDE,
                                     tags="dyn")
        if self._hover_id is not None and self._mode not in ("move", "maybe_move"):
            n = self._model.by_id(self._hover_id)
            if n is not None:
                kind, w2, hh, r2 = self._note_geom(n.lane, n.vel)
                cx = (self._col_x(n.lane) if kind != "kickline"
                      else PAD_L + self._play_w() / 2.0)
                y = self._y_of(n.time)
                coords = self._shape_coords(kind, cx, y, w2 + 8, hh + 8, r2 + 4)
                if kind == "circle":
                    self.canvas.create_oval(*coords, outline=HOVER_RING,
                                             width=1.5, tags="dyn")
                else:
                    self.canvas.create_rectangle(*coords, outline=HOVER_RING,
                                                  width=1.5, tags="dyn")
        badge = "EDIT MODE"
        bw = 10 * len(badge) + 20
        bx = PAD_L + self._play_w() - bw
        self.canvas.create_rectangle(bx, 8, bx + bw, 28, fill=EDIT_BADGE_BG,
                                      outline="", tags="dyn")
        self.canvas.create_text(bx + 10, 18, text=badge, anchor="w",
                                 fill=EDIT_BADGE_FG, font=F_MONO_SMALL_B,
                                 tags="dyn")

    def _draw_countin(self):
        w = max(1, self.canvas.winfo_width())
        h = max(1, self.canvas.winfo_height())
        self.canvas.create_text(w / 2.0, h * 0.4, text=self._countin_text,
                                 fill=COUNTIN_COLOR,
                                 font=("Consolas", 72, "bold"), tags="dyn")

    def destroy(self):
        self._cancel_tick()
        super().destroy()


# ---------------------------------------------------------------------------
# Config keys (all t9_*, persisted via the get_cfg/set_cfg hook seam) --
# verbatim from the dispatch's parity checklist section 3.
# ---------------------------------------------------------------------------
T9_DENSITY_KEY = "t9_demo_density"
T9_SPEED_KEY = "t9_speed"
T9_FALL_KEY = "t9_fall"
T9_ZOOM_KEY = "t9_zoom"
T9_GRID_KEY = "t9_grid_subs"
T9_SNAP_KEY = "t9_snap_on"
T9_HIDE_PASSED_KEY = "t9_hide_passed"
T9_AUDIO_DIR_KEY = "t9_audio_dir"
T9_IMPORT_DIR_KEY = "t9_import_dir"
T9_MODE_KEY = "t9_source_mode"
T9_OFFSET_KEY = "t9_stem_offset_ms"
T9_COUNTIN_KEY = "t9_countin"
T9_METRO_KEY = "t9_metronome"
T9_MIDI_KEY = "t9_midi_port"
T9_SQUARES_KEY = "t9_squares"
T9_CYMBAL_OVAL_KEY = "t9_cymbal_oval"
T9_CLASSIC_KEY = "t9_classic_notes"   # v4.9.2 — clean rect/circle note style
T9_KICK_LINE_KEY = "t9_kick_line"
T9_BEAT_GRID_KEY = "t9_beat_grid"
T9_NOTE_SCALE_KEY = "t9_note_scale"
T9_DRUMLESS_NOTICE_KEY = "t9_hide_drumless_notice"  # skip the drumless-Mix notice
T9_LANES_KEY = "t9_lane_visible"

_SPEEDS = (0.5, 0.75, 1.0, 1.25)
_GRIDS = (("1/4", 1), ("1/8", 2), ("1/16", 4), ("1/32", 8))
_DENSITIES = (("light", "Light  (~1.2k)"), ("dense", "Dense  (~4.2k)"),
              ("insane", "Insane  (~9.7k)"))
_MODES = ("stems", "fullmix", "synth", "both")
_FALL_STEP = 0.5
_FALL_DEFAULT = 3.0
_ZOOM_STEP = 1.25          # v4.9.0 — Zoom −/+ button multiplier
_SEEK_STEPS = 1000
_COUNTIN_BEATS = 4
_SYNTH_GAP_MAX_S = 0.25
#: lane index -> synth voice name (LANES order; DEFERRED hook, name-only here)
_VOICE_FOR_LANE = ("hihat", "crash", "snare", "tom1", "tom2", "tom3",
                    "ride", "kick")

_HINT_PLAY = ("Press ✎ Edit (E) to fix notes in place — or play and "
              "use keys 1–8 to tap notes in at the hit line")
_HINT_EDIT = ("EDIT — click = place at snapped grid · drag = move "
              "(vertical) / reclassify (horizontal) · right-click = "
              "delete (sweep = eraser) · wheel = scrub · "
              "Ctrl+wheel = zoom")
_HELP_TEXT = (
    "Space          Play / Pause\n"
    "Home           Stop & rewind\n"
    "E / Esc        enter / exit Edit\n"
    "G              snap on/off\n"
    "1–8            place at the hit line (works during playback)\n"
    "Del            delete selection\n"
    "Ctrl+Z / Y     undo / redo\n"
    "wheel          scrub (paused)\n"
    "Ctrl+wheel     zoom in / out\n\n"
    "Edit-mode mouse — click empty = place (snapped) · drag = move "
    "in time (vertical, yellow guide = onset snap when snap-to-notes is on) / "
    "reclassify lane (horizontal) · right-click = delete · hold + sweep = eraser."
)


class PreviewTab(ttk.Frame):
    """The Preview tab. Builds into any parent frame; in v4 this becomes a
    Notebook page. ``hooks`` is None for the standalone review host, or a
    dict supplied by v4 -- see the module docstring for the full seam list.
    Missing or raising hooks degrade to standalone behaviour for that seam."""

    def __init__(self, parent, hooks=None, **kw):
        super().__init__(parent, style="Prev.TFrame", **kw)
        self.hooks = hooks if hooks is not None else {}

        self._speed = 1.0
        self._mode = "synth"
        self._synth_on = True
        self._synth_ready = False
        self._stem_paths = {"song": "", "drums": ""}
        self._seek_guard = False
        self._synth_prev_t = 0.0
        self._metro_k = -1
        self._chart_source = None   # None = blank · "demo" · "custom"
        self._chart_title = ""
        # Basename for the Import-adjacent path readout (or cross-tab display str).
        self._chart_file_basename = ""
        # True once the user has edited the CURRENT chart and it has not been
        # exported since (2026-08-01). Preview has no in-place save, so this never
        # protects a file — it protects an unsaved editing session, which loading a
        # demo or importing another chart used to discard with no warning at all.
        # Deliberately only consulted when _chart_source == "custom": edits to the
        # built-in demo are disposable, and prompting on them would nag on every
        # density change, since that path swaps the chart too.
        self._chart_dirty = False
        self._loop_on = False
        self._loop_a = 0.0
        self._loop_b = 0.0
        self._rec_state = "idle"          # idle | countin | recording
        self._rec_buffer = []
        self._countin_step = 0
        self._countin_job = None
        self._perf_job = None
        self._lane_chips = []
        self._key_binds = []

        if self.hooks:
            apply_theme_embedded(self)

        self._build_header()
        self._build_transport_row1()
        self._build_transport_row2()
        self.canvas = FallingCanvas(self)
        self.canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10,
                          pady=(0, 6))
        self.canvas.on_time = self._on_view_time
        self.canvas.on_finished = self._on_finished
        self.canvas.on_edited = self._on_view_edited
        self.canvas.on_fall_changed = self._on_view_fall_changed
        self.canvas.on_zoom_changed = self._on_view_zoom_changed
        self._build_seek()
        self._build_status()
        self._build_popovers()
        self._bind_keys()

        # The hook FAILS BY RETURN VALUE, not by raising — same contract as the
        # mixer_play / mixer_load_stems checks further down. The host's
        # _pp_synth_ensure never raises and documents "Returns True when the
        # bank is ready"; discarding that bool into `_` left `ok` meaning only
        # "the hook exists", so _synth_ready was unconditionally True in the
        # embedded app and the flag could never go False (audit fix 2026-08-02).
        # Only an explicit False counts as not-ready; a MISSING hook
        # (standalone) still yields False exactly as before.
        # v4.9.11 — ASK, do not RENDER. This used to call synth_ensure_rendered, which
        # renders 14 voices x 3 layers before returning: 1.40 s on every launch, paid
        # whether or not this tab was ever opened, and measured as 27% of a 5.3 s
        # startup -- the single largest item in it, and not UI work at all.
        #
        # The question here has only ever been "will synth playback work?", so it asks
        # exactly that. `synth_probe` exercises the whole pipeline on ONE voice (~7 ms)
        # and the host warms the real bank off-thread; _pp_synth_play still renders on
        # demand if neither has happened, so the bank cannot go missing.
        #
        # The ok/ready contract below is UNCHANGED and still load-bearing: only an
        # explicit False counts as not-ready, a missing hook yields False. Standalone
        # (no host hooks at all) has no synth_probe either, so it falls through to
        # synth_ensure_rendered and behaves exactly as it did before this change.
        if "synth_probe" in self.hooks:
            ok, ready = self._hook_call("synth_probe")
        else:
            ok, ready = self._hook_call("synth_ensure_rendered")
        self._synth_ready = bool(ok) and ready is not False

        self._load_initial_state()
        self._perf_job = self.after(500, self._on_perf_tick)

    # ----- hook helpers (byte-identical contract to parakit_spectral_tab.py)
    def _cfg_get(self, key: str, default=""):
        if "get_cfg" not in self.hooks:
            return default
        try:
            val = self.hooks["get_cfg"](key, default)
        except Exception as e:
            self._status("config read failed: %s" % e, AMBER)
            return default
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

    def _hook_call(self, name: str, *args, **kwargs):
        """Call a hook by name, returning (ok, result). Missing hook -> a
        SILENT (False, None) (so a DEFER path exercised in standalone never
        spams the status line); a RAISING hook reports to the status line
        and returns (False, None). Never raises out of this tab."""
        if name not in self.hooks:
            return (False, None)
        try:
            return (True, self.hooks[name](*args, **kwargs))
        except Exception as e:
            self._status("%s hook failed: %s" % (name, e), AMBER)
            return (False, None)

    def _status(self, text, color=None):
        try:
            self.status_label.configure(text=text,
                                         foreground=(color or TEXT))
        except tk.TclError:
            pass

    # ----- header --------------------------------------------------------
    def _build_header(self):
        box = ttk.LabelFrame(self, text="Preview", style="Prev.TLabelframe")
        box.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(8, 6))
        blurb = (
            "Watch the chart fall toward the hit-line, synced to the play "
            "clock. Load an audio file and it plays along in lock-step; "
            "press Play (the notes fall and flash as they cross the line), "
            "tune the speed, fall, and zoom, scrub with the seek bar, or "
            "stress the renderer with the demo density. See a wrong note? "
            "Toggle ✎ Edit and fix it in place — place, drag-move, "
            "reclassify, and erase on the snapped grid — or tap keys "
            "1–8 during playback to chart at the hit line.")
        ttk.Label(box, text=blurb, style="Prev.Panel.TLabel",
                  wraplength=1100, justify=tk.LEFT).pack(
            anchor=tk.W, padx=10, pady=(4, 8))

    # ----- transport row 1 -- transport + view controls -------------------
    def _build_transport_row1(self):
        bar = tk.Frame(self, background=PANEL, highlightthickness=1,
                        highlightbackground=PURPLE_DEEP)
        bar.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(0, 4))
        pad = dict(side=tk.LEFT, padx=(0, 6), pady=6)

        self.play_btn = OutlineButton(bar, "▶ Play", accent=MAGENTA,
                                       command=self._on_play_toggle,
                                       tooltip="Play / pause the chart "
                                               "(Space). Playing exits edit "
                                               "mode.")
        self.play_btn.pack(**pad)
        self.stop_btn = OutlineButton(bar, "■ Stop", accent=RED,
                                       command=self._on_stop,
                                       tooltip="Stop and rewind to the "
                                               "start (Home).")
        self.stop_btn.pack(**pad)
        self.edit_chip = Chip(bar, "✎ Edit", accent=GREEN, on=False,
                               command=self._on_edit_toggled,
                               tooltip="Toggle edit mode (E / Esc) — "
                                       "pause into it, fix notes in place, "
                                       "Play resumes out of it.")
        self.edit_chip.pack(**pad)

        self.time_label = tk.Label(bar, text="0:00.0", background=PANEL,
                                    foreground=TEXT, font=F_MONO_B)
        self.time_label.pack(side=tk.LEFT, padx=(6, 6), pady=6)
        Tooltip(self.time_label, "Current song time at the hit line.")
        self.bpm_label = tk.Label(bar, text="%d BPM" % eng.DEMO_BPM,
                                   background=PANEL, foreground=PURPLE_LT,
                                   font=F_BOLD)
        self.bpm_label.pack(side=tk.LEFT, padx=(0, 10), pady=6)
        Tooltip(self.bpm_label, "The loaded chart's tempo (drives the grid).")

        self._sep(bar).pack(side=tk.LEFT, fill=tk.Y, padx=6, pady=4)
        tk.Label(bar, text="Speed", background=PANEL, foreground=MUTED,
                 font=F_SMALL).pack(side=tk.LEFT, padx=(0, 4), pady=6)
        self.speed_var = tk.StringVar(value="1x")
        self.speed_combo = ttk.Combobox(
            bar, textvariable=self.speed_var, state="readonly", width=5,
            style="Prev.TCombobox",
            values=["%gx" % s for s in _SPEEDS])
        self.speed_combo.pack(side=tk.LEFT, padx=(0, 8), pady=6)
        self.speed_combo.bind("<<ComboboxSelected>>", self._on_speed_changed)
        Tooltip(self.speed_combo, "Review speed — the chart clock "
                                   "slows down / speeds up.")

        # Fall speed — how fast notes drop (gameplay feel). Separate from Zoom.
        tk.Label(bar, text="Fall", background=PANEL, foreground=MUTED,
                 font=F_SMALL).pack(side=tk.LEFT, padx=(0, 4), pady=6)
        OutlineButton(bar, "−", accent=PURPLE_EDGE,
                      command=lambda: self._on_fall_step(-_FALL_STEP),
                      tooltip="Faster fall — shrink the fall window 0.5s.").pack(
                          side=tk.LEFT, padx=(0, 4), pady=6)
        self.fall_readout = tk.Label(bar, text="%.1fs" % _FALL_DEFAULT,
                                      background=PANEL, foreground=TEXT,
                                      font=F_MONO, width=6)
        self.fall_readout.pack(side=tk.LEFT, pady=6)
        Tooltip(self.fall_readout, "Fall speed — seconds a note takes to reach "
                                    "the hit line (how fast the chart drops).")
        OutlineButton(bar, "+", accent=PURPLE_EDGE,
                      command=lambda: self._on_fall_step(_FALL_STEP),
                      tooltip="Slower fall — grow the fall window 0.5s."
                      ).pack(side=tk.LEFT, padx=(4, 8), pady=6)

        # Divider + spacing so Fall speed and Zoom (both −/readout/+ groups) don't
        # visually blend together (owner 2026-07-23).
        self._sep(bar).pack(side=tk.LEFT, fill=tk.Y, padx=6, pady=4)

        # Zoom — magnify to inspect a detailed section / shrink to see more of
        # the chart. Independent of fall speed (v4.9.0). Ctrl+wheel zooms too.
        tk.Label(bar, text="Zoom", background=PANEL, foreground=MUTED,
                 font=F_SMALL).pack(side=tk.LEFT, padx=(0, 4), pady=6)
        OutlineButton(bar, "−", accent=PURPLE_EDGE,
                      command=lambda: self._on_zoom_step(1.0 / _ZOOM_STEP),
                      tooltip="Zoom out — see more of the chart at once.").pack(
                          side=tk.LEFT, padx=(0, 4), pady=6)
        self.zoom_readout = tk.Label(bar, text="1.00×",
                                     background=PANEL, foreground=TEXT,
                                     font=F_MONO, width=6)
        self.zoom_readout.pack(side=tk.LEFT, pady=6)
        Tooltip(self.zoom_readout, "Chart zoom. Higher = zoomed IN to inspect a "
                                    "detailed section; lower = zoomed OUT to see "
                                    "more (Ctrl+wheel in the view zooms too).")
        OutlineButton(bar, "+", accent=PURPLE_EDGE,
                      command=lambda: self._on_zoom_step(_ZOOM_STEP),
                      tooltip="Zoom in — inspect a detailed section of the chart."
                      ).pack(side=tk.LEFT, padx=(4, 8), pady=6)

        self._sep(bar).pack(side=tk.LEFT, fill=tk.Y, padx=6, pady=4)
        tk.Label(bar, text="Grid", background=PANEL, foreground=MUTED,
                 font=F_SMALL).pack(side=tk.LEFT, padx=(0, 4), pady=6)
        self.grid_var = tk.StringVar(value="1/16")
        self.grid_combo = ttk.Combobox(
            bar, textvariable=self.grid_var, state="readonly", width=6,
            style="Prev.TCombobox", values=[g[0] for g in _GRIDS])
        self.grid_combo.pack(side=tk.LEFT, padx=(0, 8), pady=6)
        self.grid_combo.bind("<<ComboboxSelected>>", self._on_grid_changed)
        Tooltip(self.grid_combo, "Snap-grid subdivision for placing/dragging "
                                  "notes in edit mode.")

        self.snap_chip = Chip(bar, "Snap", accent=CYAN, on=True,
                               command=self._on_snap_chip,
                               tooltip="Snap placed/dragged notes to the "
                                       "grid (G).")
        self.snap_chip.pack(side=tk.LEFT, padx=(0, 8), pady=6)

        # v4.9.0 — Note size in the toolbar (was buried in Settings), with the
        # app-standard tick strip under the slider.
        self._sep(bar).pack(side=tk.LEFT, fill=tk.Y, padx=6, pady=4)
        tk.Label(bar, text="Note size", background=PANEL, foreground=MUTED,
                 font=F_SMALL).pack(side=tk.LEFT, padx=(0, 4), pady=6)
        self.note_size_var = tk.DoubleVar(value=100.0)
        _nsz = tk.Frame(bar, background=PANEL)
        _nsz.pack(side=tk.LEFT, pady=6)
        note_scale = ttk.Scale(_nsz, from_=50, to=160, orient=tk.HORIZONTAL,
                               variable=self.note_size_var, length=110,
                               style="Prev.Horizontal.TScale",
                               command=self._on_note_size_scale)
        note_scale.pack(side=tk.TOP)
        _prev_tick_strip(_nsz, 110, bg=PANEL)
        Tooltip(note_scale, "Size of the falling-note markers (50–160%).")
        self.note_size_readout = tk.Label(bar, text="1.00×", background=PANEL,
                                          foreground=PURPLE_LT, font=F_MONO,
                                          width=6)
        self.note_size_readout.pack(side=tk.LEFT, padx=(4, 2), pady=6)
        # v4.9.1 (owner) — reset-to-100% button next to the note-size slider.
        OutlineButton(bar, "↺", accent=PURPLE_EDGE,
                      command=self._reset_note_size,
                      tooltip="Reset note size to 100%."
                      ).pack(side=tk.LEFT, padx=(0, 8), pady=6)

        OutlineButton(bar, "↩ Undo", accent=PURPLE_EDGE,
                      command=self._on_undo,
                      tooltip="Undo the last edit (Ctrl+Z)."
                      ).pack(side=tk.LEFT, padx=(0, 4), pady=6)
        OutlineButton(bar, "↪ Redo", accent=PURPLE_EDGE,
                      command=self._on_redo,
                      tooltip="Redo the undone edit (Ctrl+Y)."
                      ).pack(side=tk.LEFT, padx=(0, 8), pady=6)

        right = tk.Frame(bar, background=PANEL)
        right.pack(side=tk.RIGHT, padx=(6, 8), pady=6)
        # Demo controls (v4.9.2): the demo no longer auto-loads — the tab starts
        # blank (like Spectral) and the demo loads only on request. This whole
        # group is HIDDEN when a real/custom chart is loaded, since the density
        # selector is a demo-only setting (see _update_demo_ui).
        # Demo DENSITY selector -- a demo-only setting, so this group still hides
        # when a real/custom chart is loaded (see _update_demo_ui).
        self._demo_group = tk.Frame(right, background=PANEL)
        self._demo_group.pack(side=tk.LEFT)
        tk.Label(self._demo_group, text="Demo", background=PANEL,
                 foreground=MUTED, font=F_SMALL).pack(side=tk.LEFT, padx=(0, 4))
        self.density_var = tk.StringVar(value=_DENSITIES[1][1])
        self.density_combo = ttk.Combobox(
            self._demo_group, textvariable=self.density_var, state="readonly",
            width=16, style="Prev.TCombobox",
            values=[d[1] for d in _DENSITIES])
        self.density_combo.pack(side=tk.LEFT)
        self.density_combo.bind("<<ComboboxSelected>>", self._on_density_changed)
        Tooltip(self.density_combo, "Regenerate the built-in demo chart at "
                                     "a density (renderer stress-test).")
        # Persistent "Load demo" button (v4.9.3): unlike the density selector it
        # stays put next to Hide passed even after a custom chart loads, so the
        # built-in demo is always one click away -- new users can preview the tab
        # before loading anything, and it's easy to compare note densities.
        # Clicking it reloads the demo, which brings the density selector back.
        self.load_demo_btn = OutlineButton(
            right, "▶ Demo", accent=PURPLE_EDGE, command=self._on_load_demo,
            tooltip="Load the built-in demo chart (a renderer showcase / density "
                    "stress-test) at the selected density -- always available, "
                    "even after a custom chart is loaded.")
        self.load_demo_btn.pack(side=tk.LEFT, padx=(0, 8))
        self.hide_passed_chip = Chip(right, "Hide passed", accent=CYAN,
                                      on=False,
                                      command=self._on_hide_passed_toggled,
                                      tooltip="Hide notes once they cross "
                                              "the hit line.")
        self.hide_passed_chip.pack(side=tk.LEFT, padx=(10, 0))

    def _sep(self, parent):
        return tk.Frame(parent, background=SEPARATOR, width=1)

    # ----- transport row 2 -- audio + chart I/O ---------------------------
    def _build_transport_row2(self):
        bar = tk.Frame(self, background=PANEL, highlightthickness=1,
                        highlightbackground=PURPLE_DEEP)
        bar.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(0, 6))
        pad = dict(side=tk.LEFT, padx=(0, 6), pady=6)

        tk.Label(bar, text="Audio", background=PANEL, foreground=MUTED,
                 font=F_SMALL).pack(side=tk.LEFT, padx=(6, 4), pady=6)
        self.mix_btn = OutlineButton(
            bar, "♪ Mix…", accent=GREEN, command=self._on_load_mix,
            tooltip="Load (or replace) the full-mix stem — plays in "
                    "lock-step with the falling chart. Lit = loaded; "
                    "audible in Stems / Both mode.")
        self.mix_btn.pack(**pad)
        self.drums_btn = OutlineButton(
            bar, "🥁 Drums…", accent=GREEN, command=self._on_load_drums,
            tooltip="Load (or replace) the drums stem — plays alongside "
                    "the mix. Lit = loaded; audible in Stems / Both mode.")
        self.drums_btn.pack(**pad)
        # Loaded-stem basenames (compact, middle-ellipsis via _update_stem_path_label).
        self.stem_path_label = tk.Label(
            bar, text="", background=PANEL, foreground=MUTED, font=F_SMALL)
        self.stem_path_label.pack(side=tk.LEFT, padx=(0, 6), pady=6)

        self.mode_seg = Segmented(bar, ["Stems", "Full Mix", "Synth", "Both"],
                                   command=self._on_mode_changed)
        self.mode_seg.set(2, fire=False)   # default Synth (now index 2)
        self.mode_seg.pack(side=tk.LEFT, padx=(4, 10), pady=6)
        Tooltip(self.mode_seg, "Stems = the loaded DRUMS stem only · Full Mix = "
                                "the loaded full-mix track only (switch between "
                                "drums-only and the full mix) · Synth = the "
                                "built-in drum synth (works with no audio "
                                "loaded) · Both = drums stem + synth.")

        self._sep(bar).pack(side=tk.LEFT, fill=tk.Y, padx=6, pady=4)
        self.import_btn = OutlineButton(
            bar, "⇧ Import MIDI…", accent="#e09a3a", command=self._on_import,
            tooltip="Import a chart (.mid / .rlrr / parakit-chart .json) "
                    "into the preview.")
        self.import_btn.pack(**pad)
        # Loaded-chart file basename (or muted "no chart"), sitting by Import.
        self.chart_path_label = tk.Label(
            bar, text="no chart", background=PANEL, foreground=MUTED, font=F_SMALL)
        self.chart_path_label.pack(side=tk.LEFT, padx=(0, 6), pady=6)
        self.export_btn = OutlineButton(
            bar, "⇩ Export MIDI…", accent=CYAN, command=self._on_export,
            tooltip="Export the current chart — a GM drum .mid (the "
                    "default) or a lossless parakit-chart .json.")
        self.export_btn.pack(**pad)

        self._sep(bar).pack(side=tk.LEFT, fill=tk.Y, padx=6, pady=4)
        self.record_btn = OutlineButton(
            bar, "● Record", accent=RED, command=self._on_record_clicked,
            tooltip="Record live hits (keyboard 1–8) while the chart "
                    "plays — the whole take commits as ONE undo step "
                    "(Ctrl+Z removes it).")
        self.record_btn.pack(**pad)
        self.countin_chip = Chip(bar, "Count-in", accent=CYAN, on=True,
                                  command=self._on_countin_toggled,
                                  tooltip="1-bar count-in before recording.")
        self.countin_chip.pack(side=tk.LEFT, padx=(0, 6), pady=6)
        self.metro_chip = Chip(bar, "Metro", accent=CYAN, on=False,
                                command=self._on_metro_toggled,
                                tooltip="Metronome click during play/record.")
        self.metro_chip.pack(side=tk.LEFT, padx=(0, 8), pady=6)
        # Now-viewing readout (v4.9.2): shows what chart is loaded, so the user
        # always knows what they're previewing. Kept next to Metro; set by
        # _set_song_readout on every load / blank / demo.
        self.song_label = tk.Label(bar, text="", background=PANEL,
                                    foreground=CYAN, font=F_SMALL)
        self.song_label.pack(side=tk.LEFT, padx=(4, 8), pady=6)
        # (v4.9.0 — MIDI input device dropdown removed; see note by the old
        # _fill_midi_combo. This tab is for watching + spot-editing, not playing.)

        right = tk.Frame(bar, background=PANEL)
        right.pack(side=tk.RIGHT, padx=(6, 8), pady=6)
        # v4.9.2 — "Classic Style" toggle lives next to Settings (moved OUT of the
        # settings panel, where it collided with the Audio-offset row).
        self.classic_chip = Chip(
            right, "Classic notes", accent=CYAN, on=False,
            command=self._on_classic_style,
            tooltip="Draw notes as clean filled bars + circles with crisp white\n"
                    "outlines (a classic charting-game look), instead of the\n"
                    "default glow sprites.")
        self.classic_chip.pack(side=tk.LEFT, padx=(0, 6))
        self.settings_btn = OutlineButton(
            right, "⚙ Settings", accent=PURPLE_EDGE,
            command=self._on_settings_toggle,
            tooltip="Per-lane visibility, note shapes + size, beat grid, "
                    "stem audio offset, and the A/B loop.")
        self.settings_btn.pack(side=tk.LEFT, padx=(0, 4))
        self.help_btn = OutlineButton(
            right, "?", accent=PURPLE_EDGE, command=self._on_help_toggle,
            tooltip="Keyboard shortcuts + edit-mode gestures.")
        self.help_btn.pack(side=tk.LEFT)

    # ----- seek row --------------------------------------------------------
    def _build_seek(self):
        row = tk.Frame(self, background=BG)
        row.pack(side=tk.TOP, fill=tk.X, padx=12, pady=(0, 2))
        self.seek_t0 = tk.Label(row, text="0:00", background=BG,
                                 foreground=MUTED, font=F_MONO)
        self.seek_t0.pack(side=tk.LEFT, padx=(0, 8))
        self.seek_var = tk.DoubleVar(value=0.0)
        _seek_wrap = tk.Frame(row, background=BG)
        _seek_wrap.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.seek_scale = ttk.Scale(_seek_wrap, from_=0, to=_SEEK_STEPS,
                                     orient=tk.HORIZONTAL,
                                     variable=self.seek_var,
                                     style="Prev.Horizontal.TScale",
                                     command=self._on_seek_moved)
        self.seek_scale.pack(side=tk.TOP, fill=tk.X)
        _prev_tick_strip_fill(_seek_wrap, bg=BG)   # app-standard tick strip
        Tooltip(self.seek_scale, "Scrub anywhere in the chart (audio "
                                  "follows once wired).")
        self.seek_t1 = tk.Label(row, text="0:00", background=BG,
                                 foreground=MUTED, font=F_MONO)
        self.seek_t1.pack(side=tk.LEFT, padx=(8, 0))

    # ----- status row --------------------------------------------------------
    def _build_status(self):
        row = tk.Frame(self, background=BG)
        row.pack(side=tk.TOP, fill=tk.X, padx=12, pady=(2, 8))
        self.status_label = tk.Label(row, text="Ready.", background=BG,
                                      foreground=TEXT, font=F_BASE)
        self.status_label.pack(side=tk.LEFT)
        Tooltip(self.status_label, "Last action / note readout.")
        self.hint_label = tk.Label(row, text=_HINT_PLAY, background=BG,
                                    foreground=MUTED, font=F_SMALL)
        self.hint_label.pack(side=tk.LEFT, padx=(16, 0))
        Tooltip(self.hint_label, "What the mouse and keys do right now.")
        self.perf_label = tk.Label(row, text="", background=BG,
                                    foreground=MUTED, font=F_MONO)
        self.perf_label.pack(side=tk.RIGHT)
        Tooltip(self.perf_label, "drawn notes in the fall window / total "
                                  "notes — the renderer index-culls, so "
                                  "total size never costs frame time.")

    # ----- popovers: Settings + Help ---------------------------------------
    def _build_popovers(self):
        self.settings_popup = Popover(self)
        self.settings_popup.configure(width=430)
        body = tk.Frame(self.settings_popup, background=PANEL)
        body.pack(fill=tk.BOTH, expand=True, padx=14, pady=12)

        tk.Label(body, text="Settings", background=PANEL, foreground=TEXT,
                 font=F_BOLD).grid(row=0, column=0, columnspan=4, sticky=tk.W,
                                    pady=(0, 6))

        lane_row = tk.Frame(body, background=PANEL)
        lane_row.grid(row=1, column=0, columnspan=4, sticky=tk.W, pady=(0, 8))
        self._lane_chips = []
        for i, name in enumerate(LANE_NAMES):
            chip = Chip(lane_row, name, accent=LANE_COLORS[i], on=True,
                        command=lambda v, i=i: self._on_lane_vis(i, v),
                        tooltip="Show/hide %s notes in the falling view." % name)
            chip.grid(row=i // 4, column=i % 4, padx=(0, 4), pady=2, sticky=tk.W)
            self._lane_chips.append(chip)

        self.squares_chip = Chip(body, "Square notes", accent=CYAN, on=False,
                                  command=self._on_squares,
                                  tooltip="Render circle-shaped lanes as "
                                          "bars instead of circles.")
        self.squares_chip.grid(row=2, column=0, columnspan=2, sticky=tk.W,
                                pady=3)
        self.kick_line_chip = Chip(body, "Kick as full-width line",
                                    accent=CYAN, on=False,
                                    command=self._on_kick_line,
                                    tooltip="Draw Kick notes as a "
                                            "full-width line instead of a "
                                            "bar.")
        # Longest label -> own full-width row at the bottom of the option block.
        self.kick_line_chip.grid(row=4, column=0, columnspan=4, sticky=tk.W,
                                  pady=3)
        self.beat_grid_chip = Chip(body, "Beat grid during play",
                                    accent=CYAN, on=True,
                                    command=self._on_beat_grid,
                                    tooltip="Show the beat/bar grid during "
                                            "playback (edit mode always "
                                            "shows it).")
        self.beat_grid_chip.grid(row=3, column=0, columnspan=2, sticky=tk.W,
                                  pady=3)
        self.settings_hide_passed_chip = Chip(
            body, "Hide notes past hit line", accent=CYAN, on=False,
            command=self._on_settings_hide_passed,
            tooltip="Mirrors the toolbar 'Hide passed' checkbox.")
        self.settings_hide_passed_chip.grid(row=3, column=2, columnspan=2,
                                             sticky=tk.W, pady=3)
        # v4.9.1 (owner) — render cymbal lanes as wide ovals for readability
        # during rapid cymbal runs.
        self.cymbal_oval_chip = Chip(
            body, "Cymbals as ovals", accent=CYAN, on=False,
            command=self._on_cymbal_oval,
            tooltip="Draw cymbal lanes (Hi-Hat / Crash / Ride) as wide ovals\n"
                    "instead of circles — the flatter shape makes the timing of\n"
                    "rapid cymbal runs easier to read.")
        # Paired with "Square notes" on the top option row (both short labels).
        self.cymbal_oval_chip.grid(row=2, column=2, columnspan=2, sticky=tk.W,
                                   pady=3)

        # (Classic-Style toggle moved to the toolbar next to Settings — v4.9.2 —
        #  it collided with the Audio-offset row here. (Note size is on the
        #  toolbar too, v4.9.0.))

        tk.Label(body, text="Audio offset", background=PANEL, foreground=MUTED,
                 font=F_SMALL).grid(row=5, column=0, sticky=tk.W, pady=2)
        self.audio_offset_var = tk.DoubleVar(value=0.0)
        _off_sf = tk.Frame(body, background=PANEL)
        _off_sf.grid(row=5, column=1, columnspan=2, sticky=tk.W, pady=2)
        offset_scale = ttk.Scale(_off_sf, from_=-200, to=200,
                                  orient=tk.HORIZONTAL,
                                  variable=self.audio_offset_var,
                                  style="Prev.Horizontal.TScale",
                                  command=self._on_audio_offset_scale, length=140)
        offset_scale.pack(side=tk.TOP)
        _prev_tick_strip(_off_sf, 140, bg=PANEL)   # app-standard tick strip
        self.audio_offset_readout = tk.Label(body, text="0 ms", background=PANEL,
                                              foreground=PURPLE_LT, font=F_MONO)
        self.audio_offset_readout.grid(row=5, column=3, sticky=tk.W, pady=2)

        loop_row = tk.Frame(body, background=PANEL)
        loop_row.grid(row=6, column=0, columnspan=4, sticky=tk.W, pady=(8, 0))
        tk.Label(loop_row, text="Loop", background=PANEL, foreground=MUTED,
                 font=F_SMALL).pack(side=tk.LEFT, padx=(0, 6))
        OutlineButton(loop_row, "Set A", accent=PURPLE_EDGE,
                      command=self._on_loop_set_a,
                      tooltip="Capture the current playhead as loop point "
                              "A.").pack(side=tk.LEFT, padx=(0, 4))
        OutlineButton(loop_row, "Set B", accent=PURPLE_EDGE,
                      command=self._on_loop_set_b,
                      tooltip="Capture the current playhead as loop point "
                              "B. The loop arms only once B is past A."
                      ).pack(side=tk.LEFT, padx=(0, 4))
        OutlineButton(loop_row, "Clear", accent=MUTED,
                      command=self._on_loop_clear,
                      tooltip="Clear the A/B loop.").pack(side=tk.LEFT,
                                                           padx=(0, 8))
        self.loop_readout_lbl = tk.Label(loop_row, text="off", background=PANEL,
                                          foreground=CYAN, font=F_MONO)
        self.loop_readout_lbl.pack(side=tk.LEFT)

        self.help_popup = Popover(self)
        self.help_popup.configure(width=430)
        hbody = tk.Frame(self.help_popup, background=PANEL)
        hbody.pack(fill=tk.BOTH, expand=True, padx=14, pady=12)
        tk.Label(hbody, text="Shortcuts & gestures", background=PANEL,
                 foreground=TEXT, font=F_BOLD).pack(anchor=tk.W, pady=(0, 6))
        # wraplength keeps the popup from stretching to the width of the long
        # "Edit-mode mouse …" line (it used to span ~half the screen); the short
        # shortcut-table rows are well under this so they don't wrap.
        tk.Label(hbody, text=_HELP_TEXT, background=PANEL, foreground=TEXT,
                 font=F_MONO, justify=tk.LEFT, wraplength=400).pack(anchor=tk.W)

        # Close the Settings/Help popovers when this tab is switched AWAY from --
        # they are -topmost Toplevels, so otherwise they float over whatever tab
        # you move to (owner 2026-07-23). <Unmap> on the tab frame fires on a
        # notebook tab change (and on minimize, also fine to close then).
        self.bind("<Unmap>", self._close_popovers)

    def _close_popovers(self, _e=None):
        # Only react to THIS frame's own Unmap, not a child's.
        if _e is not None and getattr(_e, "widget", None) is not self:
            return
        for pop in (getattr(self, "settings_popup", None),
                    getattr(self, "help_popup", None)):
            try:
                if pop is not None:
                    pop.hide()
            except Exception:
                pass

    # ----- initial state (config restore) -----------------------------------
    def _load_initial_state(self):
        try:
            speed = float(self._cfg_get(T9_SPEED_KEY, 1.0))
        except (TypeError, ValueError):
            speed = 1.0
        if speed not in _SPEEDS:
            speed = 1.0
        self._speed = speed
        self.speed_var.set("%gx" % speed)
        self.canvas.set_speed(speed)

        try:
            fall = float(self._cfg_get(T9_FALL_KEY, _FALL_DEFAULT))
        except (TypeError, ValueError):
            fall = _FALL_DEFAULT
        # Non-finite check (breaker fix, 2026-07-20): float('nan')/('inf') do NOT
        # raise, and max(MIN, min(MAX, nan)) collapses to the MAX rail — a NaN in
        # a persisted config would restore the view stuck at 8 s / 4.0x.
        if not math.isfinite(fall):
            fall = _FALL_DEFAULT
        self.canvas.set_fall(max(FALL_MIN, min(FALL_MAX, fall)))
        self._sync_fall_readout()

        try:
            zoom = float(self._cfg_get(T9_ZOOM_KEY, ZOOM_DEFAULT))
        except (TypeError, ValueError):
            zoom = ZOOM_DEFAULT
        if not math.isfinite(zoom):
            zoom = ZOOM_DEFAULT
        self.canvas.set_zoom(max(ZOOM_MIN, min(ZOOM_MAX, zoom)))
        self._sync_zoom_readout()

        try:
            subs = int(self._cfg_get(T9_GRID_KEY, 4))
        except (TypeError, ValueError):
            subs = 4
        grid_label = next((g[0] for g in _GRIDS if g[1] == subs), "1/16")
        self.grid_var.set(grid_label)
        self.canvas.set_grid_subs(subs)

        snap_on = bool(self._cfg_get(T9_SNAP_KEY, True))
        self.snap_chip.set(snap_on, fire=False)
        self.canvas.set_snap(snap_on)

        hide_passed = bool(self._cfg_get(T9_HIDE_PASSED_KEY, False))
        self.hide_passed_chip.set(hide_passed, fire=False)
        self.settings_hide_passed_chip.set(hide_passed, fire=False)
        self.canvas.set_hide_passed(hide_passed)

        mode = str(self._cfg_get(T9_MODE_KEY, "synth"))
        if mode not in _MODES:
            mode = "synth"
        self._mode = mode
        self.mode_seg.set(_MODES.index(mode), fire=False)
        self._apply_mix_state()

        try:
            offset_ms = int(self._cfg_get(T9_OFFSET_KEY, 0))
        except (TypeError, ValueError):
            offset_ms = 0
        offset_ms = max(-200, min(200, offset_ms))
        self.audio_offset_var.set(float(offset_ms))
        self.audio_offset_readout.configure(text="%d ms" % offset_ms)

        self.countin_chip.set(bool(self._cfg_get(T9_COUNTIN_KEY, True)), fire=False)
        self.metro_chip.set(bool(self._cfg_get(T9_METRO_KEY, False)), fire=False)

        squares = bool(self._cfg_get(T9_SQUARES_KEY, False))
        kick_line = bool(self._cfg_get(T9_KICK_LINE_KEY, False))
        beat_grid = bool(self._cfg_get(T9_BEAT_GRID_KEY, True))
        self.squares_chip.set(squares, fire=False)
        cym_oval = bool(self._cfg_get(T9_CYMBAL_OVAL_KEY, False))
        self.cymbal_oval_chip.set(cym_oval, fire=False)
        self.canvas.set_cymbal_oval(cym_oval)
        classic = bool(self._cfg_get(T9_CLASSIC_KEY, False))
        self.classic_chip.set(classic, fire=False)
        self.canvas.set_classic_style(classic)
        self.kick_line_chip.set(kick_line, fire=False)
        self.beat_grid_chip.set(beat_grid, fire=False)
        self.canvas.set_squares(squares)
        self.canvas.set_kick_line(kick_line)
        self.canvas.set_beat_grid(beat_grid)

        try:
            note_scale = float(self._cfg_get(T9_NOTE_SCALE_KEY, 1.0))
        except (TypeError, ValueError):
            note_scale = 1.0
        if not math.isfinite(note_scale):   # breaker fix (see fall/zoom above)
            note_scale = 1.0
        note_scale = max(0.5, min(1.6, note_scale))
        self.note_size_var.set(note_scale * 100.0)
        self.note_size_readout.configure(text="%.2f×" % note_scale)
        self.canvas.set_note_scale(note_scale)

        lanes = self._cfg_get(T9_LANES_KEY, [True] * LANE_COUNT)
        if not isinstance(lanes, list) or len(lanes) != LANE_COUNT:
            lanes = [True] * LANE_COUNT
        for i, vis in enumerate(lanes):
            self._lane_chips[i].set(bool(vis), fire=False)
            self.canvas.set_lane_visible(i, bool(vis))

        # Restore the demo-density SELECTOR value, but do NOT auto-load the demo
        # (v4.9.2): the tab starts on a blank chart like the Spectral tab. The
        # demo loads only via "Load demo example" / picking a density; a custom
        # chart sent from another tab (import_chart) overrides the blank.
        density_value = self._cfg_get(T9_DENSITY_KEY, "dense")
        label = next((d[1] for d in _DENSITIES if d[0] == density_value),
                     _DENSITIES[1][1])
        self.density_var.set(label)
        self._load_blank()

    # ----- transport slots ---------------------------------------------------
    def _on_play_toggle(self):
        if not self.canvas.is_playing and self.edit_chip.get():
            self.edit_chip.set(False)
        self.canvas.toggle()
        playing = self.canvas.is_playing
        self.play_btn.configure(text="⏸ Pause" if playing else "▶ Play")
        if playing:
            start_at = self.canvas.now
            self._synth_prev_t = start_at
            ok, res = self._hook_call("mixer_play", start_at,
                                      self._speed)
            # RE-ANCHOR THE CLOCK (owner-reported desync, 2026-08-17): the chart
            # ran ahead of the audio from the very first note, on a song that
            # lined up correctly in the MIDI Editor.
            #
            # canvas.toggle() above calls play(), which anchors _anchor_wall to
            # perf_counter() IMMEDIATELY. The host's mixer_play then decodes and
            # resamples on THIS thread before a single sample sounds -- measured
            # 1.31 s for the reported song (a 6.5-minute 48 kHz full mix needing
            # 48k->44.1k resample_poly, 0.91 s, plus a 44.1 kHz drums stem,
            # 0.40 s). Every one of those seconds was already being counted as
            # song time, so the highway started that far ahead and STAYED there:
            # the error is constant, not drift, and nothing healed it, because
            # sync_clock has no other caller and mixer_song_time is DEFERRED.
            #
            # Re-anchoring here keeps _anchor_song at the requested position and
            # resets _anchor_wall to the moment audio actually began. It is
            # unconditional on purpose: a failed or missing mixer costs no decode
            # time, so the call is a no-op there rather than a special case.
            #
            # The MIDI Editor is unaffected because it plays from its cached
            # _me_sound_cache (decode already done); Practice is unaffected
            # because it hands position authority to the mixer, whose own anchor
            # is taken AFTER the decode -- Preview is the only tab that keeps the
            # clock locally AND never re-syncs.
            self.canvas.sync_clock(start_at)
            # The hook can FAIL BY RETURN VALUE, not just by raising (same
            # contract as Spectral's _start_stream, breaker R2B2-1): the host
            # returns False when stems were loaded but NONE decoded — before
            # this branch a moving playhead ran over total silence with no
            # warning. Only an explicit False counts; a missing hook
            # (standalone) stays silent as before, and the synth (if on)
            # still sounds.
            if ok and res is False:
                self._status("Stem audio unavailable — the loaded file could "
                             "not be decoded. Playing on the silent clock.",
                             AMBER)
        else:
            self._hook_call("mixer_pause")

    def _on_stop(self):
        if self._rec_state == "recording":
            self._stop_recording()
        elif self._rec_state == "countin":
            self._cancel_countin()
        self.canvas.stop()
        self._hook_call("mixer_stop")
        self._synth_prev_t = 0.0
        self._metro_k = -1
        self.play_btn.configure(text="▶ Play")

    def _mixer_recue(self, t):
        """Re-cue the audio to song-second `t`, then re-anchor the clock to the
        moment the audio actually resumed.

        WHY THIS EXISTS AS A HELPER RATHER THAN THREE CALL SITES. The host's
        mixer_seek is NOT cheap while playing: _pp_mix_seek routes straight back
        into _pp_mix_play, which re-decodes and re-resamples every loaded stem
        (measured 1.31 s for a 6.5-minute 48 kHz mix plus a 44.1 kHz drums stem).
        Every caller that moved the canvas and then asked the mixer to follow was
        therefore leaving the clock running across that decode, re-opening the
        exact gap _on_play_toggle closes -- scrubbing, loop-back and the
        audio-offset nudge each did it (found by an independent audit after the
        play-path fix, which covered none of them).

        Routing every re-cue through here means a FOURTH such site cannot be
        added wrong by accident, and gives the invariant one thing to check
        instead of an open-ended search for raw mixer_seek calls.
        """
        self._hook_call("mixer_seek", t)
        self.canvas.sync_clock(t)

    def _on_speed_changed(self, _event=None):
        text = self.speed_var.get()
        try:
            s = float(text.rstrip("xX"))
        except ValueError:
            s = 1.0
        self._speed = s
        self.canvas.set_speed(s)
        self._cfg_set(T9_SPEED_KEY, s)

    # ----- fall speed ----------------------------------------------------
    def _sync_fall_readout(self, persist=False):
        f = self.canvas.fall
        self.fall_readout.configure(text="%.1fs" % f)
        if persist:
            self._cfg_set(T9_FALL_KEY, f)

    def _on_fall_step(self, delta):
        self.canvas.set_fall(self.canvas.fall + delta)
        self._sync_fall_readout(persist=True)

    def _on_view_fall_changed(self, _f):
        self._sync_fall_readout(persist=True)

    # ----- zoom (independent of fall speed) ------------------------------
    def _sync_zoom_readout(self, persist=False):
        z = self.canvas.zoom
        self.zoom_readout.configure(text="%.2f×" % z)
        if persist:
            self._cfg_set(T9_ZOOM_KEY, z)

    def _on_zoom_step(self, factor):
        self.canvas.set_zoom(self.canvas.zoom * factor)
        self._sync_zoom_readout(persist=True)

    def _on_view_zoom_changed(self, _z):
        self._sync_zoom_readout(persist=True)

    # ----- grid + snap ---------------------------------------------------
    def _on_grid_changed(self, _event=None):
        label = self.grid_var.get()
        subs = next((g[1] for g in _GRIDS if g[0] == label), 4)
        self.canvas.set_grid_subs(subs)
        self._cfg_set(T9_GRID_KEY, subs)

    def _on_snap_chip(self, value):
        self.canvas.set_snap(value)
        self._cfg_set(T9_SNAP_KEY, value)

    def _sync_snap_from_key(self, on):
        self.snap_chip.set(on, fire=False)
        self.canvas.set_snap(on)
        self._cfg_set(T9_SNAP_KEY, on)
        self._status("Snap %s." % ("on" if on else "off"))

    # ----- undo / redo -----------------------------------------------------
    def _on_undo(self):
        ok = self.canvas.undo()
        self._status("Undid edit." if ok else "Nothing to undo.")

    def _on_redo(self):
        ok = self.canvas.redo()
        self._status("Redid edit." if ok else "Nothing to redo.")

    # ----- demo density ------------------------------------------------
    def _on_density_changed(self, _event=None):
        label = self.density_var.get()
        value = next((d[0] for d in _DENSITIES if d[1] == label), "dense")
        self._cfg_set(T9_DENSITY_KEY, value)
        self.play_btn.configure(text="▶ Play")
        self._set_density(value)

    def _set_density(self, value):
        self._before_chart_swap()
        notes = eng.generate_demo_chart(value)
        self.canvas.set_chart(notes, eng.DEMO_BPM)
        self.seek_t1.configure(text=fmt_short(self.canvas.duration))
        self._update_seek_from_view(0.0)
        self._sync_bpm_label()
        self._chart_source = "demo"
        self._chart_dirty = False   # a freshly installed chart is clean
        self._chart_title = "Demo track"
        self._chart_file_basename = ""
        self._set_song_readout()
        self._update_demo_ui()
        self._status("Loaded %d demo notes (%s). Press Play."
                     % (self.canvas.total_notes, value))

    def _on_load_demo(self):
        if not self._confirm_discard_edits("Loading the demo"):
            return
        # "Load demo example" button (v4.9.2): the demo loads only on request.
        value = next((d[0] for d in _DENSITIES if d[1] == self.density_var.get()),
                     "dense")
        self.play_btn.configure(text="▶ Play")
        self._set_density(value)

    def _load_blank(self):
        if not self._confirm_discard_edits("Clearing the chart"):
            return
        # Blank chart (v4.9.2): the tab opens empty, like the Spectral tab, until
        # the user imports a chart or presses "Load demo example".
        self._before_chart_swap()
        self.canvas.set_chart([], eng.DEMO_BPM)
        self.seek_t1.configure(text=fmt_short(self.canvas.duration))
        self._update_seek_from_view(0.0)
        self._sync_bpm_label()
        self.play_btn.configure(text="▶ Play")
        self._chart_source = None
        self._chart_title = ""
        self._chart_dirty = False   # a freshly installed chart is clean
        self._chart_file_basename = ""
        self._set_song_readout()
        self._update_demo_ui()
        self._status("No chart loaded — Import a chart, or press "
                     "“Load demo example”.")

    def _set_song_readout(self):
        if self._chart_source == "custom":
            self.song_label.configure(text="♪ %s" % self._chart_title,
                                      foreground=CYAN)
        elif self._chart_source == "demo":
            self.song_label.configure(text="Demo track", foreground=MUTED)
        else:
            self.song_label.configure(text="No chart loaded", foreground=MUTED)
        self._update_chart_path_label()

    def _update_chart_path_label(self):
        """Import-button path readout: basename or muted 'no chart'."""
        try:
            lbl = self.chart_path_label
        except AttributeError:
            return
        base = (self._chart_file_basename or "").strip()
        lbl.configure(text=(_truncate_mid(base, 24) if base else "no chart"),
                      foreground=MUTED)

    def _update_stem_path_label(self):
        """Mix/Drums path readout: 'mix: a.ogg · drums: b.ogg' (truncated)."""
        try:
            lbl = self.stem_path_label
        except AttributeError:
            return
        parts = []
        song = self._stem_paths.get("song") or ""
        drums = self._stem_paths.get("drums") or ""
        if song:
            parts.append("mix: %s" % _truncate_mid(os.path.basename(song), 24))
        if drums:
            parts.append("drums: %s" % _truncate_mid(os.path.basename(drums), 24))
        lbl.configure(text=" · ".join(parts), foreground=MUTED)

    def _maybe_show_drumless_notice(self, path):
        """Informational (non-blocking) notice when the Mix/song slot looks
        drumless. Always called AFTER the stem is loaded -- never blocks it.
        Suppressed forever once the user checks 'Don't show this again'."""
        if bool(self._cfg_get(T9_DRUMLESS_NOTICE_KEY, False)):
            return
        if not _looks_drumless_basename(path):
            return
        try:
            self.after(1, self._show_drumless_notice)
        except Exception:
            self._show_drumless_notice()

    def _show_drumless_notice(self):
        """Border-aware informational popup (clamped to the visible monitor)."""
        old = getattr(self, "_drumless_notice_pop", None)
        if old is not None:
            try:
                old.destroy()
            except tk.TclError:
                pass
            self._drumless_notice_pop = None
        anchor = getattr(self, "mix_btn", None) or self
        pop = tk.Toplevel(self)
        pop.wm_overrideredirect(True)
        try:
            pop.wm_attributes("-topmost", True)
        except tk.TclError:
            pass
        pop.configure(background=PANEL, highlightthickness=1,
                      highlightbackground=PURPLE_EDGE)
        self._drumless_notice_pop = pop
        body = tk.Frame(pop, background=PANEL, padx=12, pady=10)
        body.pack(fill=tk.BOTH, expand=True)
        tk.Label(body, text="Drumless audio", background=PANEL, foreground=TEXT,
                 font=F_BOLD, anchor=tk.W).pack(anchor=tk.W, pady=(0, 6))
        tk.Label(body, text=(
                    "This audio has no drums. That's fine for checking the synth "
                    "drums against a backing, but the Preview tab is meant for "
                    "drums audio -- you won't see notes fall for a drumless track."),
                 background=PANEL, foreground=MUTED, font=F_SMALL,
                 justify=tk.LEFT, wraplength=320, anchor=tk.W).pack(
                     anchor=tk.W, pady=(0, 8))
        dont_var = tk.BooleanVar(value=False)
        tk.Checkbutton(body, text="Don't show this again", variable=dont_var,
                       background=PANEL, foreground=MUTED, font=F_SMALL,
                       activebackground=PANEL, activeforeground=TEXT,
                       selectcolor=DARKER, highlightthickness=0, bd=0,
                       anchor=tk.W).pack(anchor=tk.W, pady=(0, 8))

        def _dismiss():
            if dont_var.get():
                self._cfg_set(T9_DRUMLESS_NOTICE_KEY, True)
            try:
                pop.destroy()
            except tk.TclError:
                pass
            if getattr(self, "_drumless_notice_pop", None) is pop:
                self._drumless_notice_pop = None
        OutlineButton(body, "Got it", accent=CYAN, command=_dismiss,
                      tooltip="Dismiss this notice.").pack(anchor=tk.E)
        try:
            pop.update_idletasks()
            x = anchor.winfo_rootx()
            y = anchor.winfo_rooty() + max(1, anchor.winfo_height()) + 4
            _position_popup_with_bounds(pop, anchor, x, y, margin=8)
            pop.lift()
        except Exception:
            pass
        try:
            self.bind("<Destroy>", lambda _e: _dismiss(), add="+")
        except tk.TclError:
            pass

    def _update_demo_ui(self):
        # The demo density selector is a demo-only setting — hide the whole demo
        # group when a real/custom chart is loaded (v4.9.2). Gate on
        # winfo_manager() (is it PACKED), NOT winfo_ismapped(): a chart can be
        # sent from another tab while Preview is the INACTIVE notebook page, where
        # ismapped() is False even though the group is packed — that would leave
        # the demo controls visible on the next switch-in.
        show = self._chart_source != "custom"
        try:
            packed = bool(self._demo_group.winfo_manager())
            if show and not packed:
                # Keep the density group just left of the persistent "▶ Demo"
                # button (which itself stays left of Hide passed).
                self._demo_group.pack(side=tk.LEFT, before=self.load_demo_btn)
            elif not show and packed:
                self._demo_group.pack_forget()
        except tk.TclError:
            pass

    def _sync_bpm_label(self):
        self.bpm_label.configure(text="%.0f BPM" % self.canvas.model.bpm)

    def _before_chart_swap(self):
        """Land the whole transport before replacing the chart: abort a live
        record take, cancel a count-in, stop the (deferred) mixer too."""
        if self._rec_state == "recording":
            self._abort_recording()
        elif self._rec_state == "countin":
            self._cancel_countin()
        self._hook_call("mixer_stop")
        self._synth_prev_t = 0.0
        self._metro_k = -1
        self.play_btn.configure(text="▶ Play")

    def _abort_recording(self):
        self._rec_state = "idle"
        self._set_record_look("● Record", rec=False)
        self.canvas.set_capture_hook(None)
        k = len(self._rec_buffer)
        self._rec_buffer = []
        if k:
            self._status("Recording aborted — %d uncommitted hit%s "
                         "discarded with the old chart."
                         % (k, "" if k == 1 else "s"))

    def _on_hide_passed_toggled(self, value):
        self.canvas.set_hide_passed(value)
        self._cfg_set(T9_HIDE_PASSED_KEY, value)
        self.settings_hide_passed_chip.set(value, fire=False)

    def _on_settings_hide_passed(self, value):
        self.hide_passed_chip.set(value, fire=False)
        self._on_hide_passed_toggled(value)

    # ----- seek --------------------------------------------------------
    def _on_seek_moved(self, value):
        if self._seek_guard:
            return
        if self.canvas.duration <= 0:
            return
        try:
            v = float(value)
        except (TypeError, ValueError):
            return
        t = v / _SEEK_STEPS * self.canvas.duration
        self._synth_prev_t = t
        self.canvas.seek(t)
        self._mixer_recue(t)

    def _update_seek_from_view(self, t):
        if self.canvas.duration <= 0:
            return
        self._seek_guard = True
        try:
            self.seek_var.set(t / self.canvas.duration * _SEEK_STEPS)
        finally:
            self._seek_guard = False

    # ----- view feedback (canvas.on_time) -----------------------------------
    def _on_view_time(self, t):
        if self._loop_on and self.canvas.is_playing and t >= self._loop_b:
            if self._synth_on and self._synth_ready:
                prev = self._synth_prev_t
                end = min(t, self._loop_b)
                if end > prev and end - prev <= _SYNTH_GAP_MAX_S:
                    for n in self.canvas.model.get_index().iter_window(prev, end):
                        if prev <= n.time <= end and self.canvas.lane_visible(n.lane):
                            self._hook_call("synth_play", _VOICE_FOR_LANE[n.lane],
                                            n.vel)
            a = self._loop_a
            self.canvas.seek(a)
            self._mixer_recue(a)
            self._synth_prev_t = a
            return
        self.time_label.configure(text=fmt_time(t))
        self.seek_t0.configure(text=fmt_short(t))
        self._update_seek_from_view(t)

        if self.canvas.is_playing:
            bpm = max(1.0, self.canvas.model.bpm)
            beat = 60.0 / bpm
            k = int(t / beat)
            if k != self._metro_k:
                if k == self._metro_k + 1 and self.metro_chip.get():
                    self._hook_call("synth_met_click", k % 4 == 0)
                self._metro_k = k

        if self.canvas.is_playing and self._synth_on:
            prev = self._synth_prev_t
            if t - prev > _SYNTH_GAP_MAX_S:
                self._synth_prev_t = t
            elif t > prev:
                for n in self.canvas.model.get_index().iter_window(prev, t):
                    if prev <= n.time <= t and self.canvas.lane_visible(n.lane):
                        self._hook_call("synth_play", _VOICE_FOR_LANE[n.lane],
                                        n.vel)
                self._synth_prev_t = t
        else:
            self._synth_prev_t = t

    def _on_finished(self):
        self._hook_call("mixer_pause")
        self.play_btn.configure(text="▶ Play")
        if self._rec_state == "recording":
            self._stop_recording()
        else:
            self._status("Reached the end of the chart.")

    def _on_view_edited(self):
        self._chart_dirty = True
        self._status("%d notes  ·  %d selected  ·  edit mode %s"
                     % (self.canvas.total_notes, self.canvas.selection_size,
                        "on" if self.canvas.edit_mode else "off"))

    def _confirm_discard_edits(self, what):
        """True to proceed. Warns only when the user's OWN imported chart has
        unedited-away changes; demo/blank charts are disposable, so this is silent
        for them (and therefore silent for demo density changes)."""
        if not (self._chart_dirty and self._chart_source == "custom"):
            return True
        return messagebox.askyesno(
            "Discard chart edits?",
            "You have unsaved edits to %s.\n\n%s will replace them, and Preview "
            "cannot undo across a chart swap.\n\nUse Export first if you want to "
            "keep them.\n\nDiscard the edits and continue?"
            % (self._chart_title or "the loaded chart", what))

    # ----- audio: stems + mode + synth --------------------------------------
    def _on_load_mix(self):
        self._pick_stem("song", "Load the full-mix stem")

    def _on_load_drums(self):
        self._pick_stem("drums", "Load the drums stem")

    def _pick_stem(self, bus, title):
        start_dir = self._cfg_get(T9_AUDIO_DIR_KEY, "") or None
        path = filedialog.askopenfilename(
            title=title, initialdir=start_dir,
            filetypes=[("Audio", _decoder_audio_patterns()),
                       ("All files", "*.*")])
        if not path:
            return
        self._cfg_set(T9_AUDIO_DIR_KEY, os.path.dirname(path))
        self._load_stem(bus, path)

    def _load_stem(self, bus, path):
        if not path:
            return
        self._stem_paths[bus] = path
        specs = [{"path": p, "bus": b} for b, p in self._stem_paths.items() if p]
        if self.canvas.is_playing:
            self._on_play_toggle()
        ok, res = self._hook_call("mixer_load_stems", specs)
        # The hook can FAIL BY RETURN VALUE, not just by raising — branch on
        # its result the way Spectral's _start_stream does (breaker R2B2-1
        # there): only an explicit False counts as failure; None (hooks with
        # no return contract) still means "recorded". Before this, the button
        # went GREEN and the status said "Loaded" whenever the hook merely
        # ran (audit fix 2026-08-02).
        failed = ok and res is False
        btn = self.mix_btn if bus == "song" else self.drums_btn
        btn.accent = AMBER if failed else GREEN
        btn._restyle()
        self._update_stem_path_label()
        if failed:
            self._status("Could not load stem: %s"
                         % os.path.basename(path), AMBER)
        elif ok:
            self._status("Loaded stems: %s" % os.path.basename(path))
        else:
            self._status("Stem set (audio wired at integration): %s"
                         % os.path.basename(path))
        self._apply_mix_state()
        # Informational drumless notice for the Mix/song slot only (never blocks
        # the load; see _maybe_show_drumless_notice).
        if bus == "song":
            self._maybe_show_drumless_notice(path)

    def _on_mode_changed(self, index):
        value = _MODES[index]
        self._mode = value
        self._cfg_set(T9_MODE_KEY, value)
        self._apply_mix_state()
        self._status({
            "stems": "Stems only — the loaded DRUMS stem plays, synth muted.",
            "fullmix": "Full Mix only — the loaded full-mix track plays, synth muted.",
            "synth": "Synth only — the chart is sonified by the built-in kit.",
            "both": "Drums stem + synth together.",
        }[value])

    def _apply_mix_state(self):
        # Preview plays ONE audio source at a time so a full mix and a drums stem
        # can never double up: Stems = the drums bus only, Full Mix = the song
        # (full-mix) bus only. Synth is independent; Both = drums stem + synth.
        drums_on = self._mode in ("stems", "both")
        song_on = self._mode == "fullmix"
        self._hook_call("mixer_set_bus_gain", "song", 1.0 if song_on else 0.0)
        self._hook_call("mixer_set_bus_gain", "drums", 1.0 if drums_on else 0.0)
        self._synth_on = self._mode in ("synth", "both")
        self._hook_call("synth_set_muted", not self._synth_on)

    # ----- record (idle -> countin -> recording; take = ONE undo) ----------
    def _on_record_clicked(self):
        if self._rec_state == "idle":
            if self.countin_chip.get():
                self._start_countin()
            else:
                self._start_recording()
        elif self._rec_state == "countin":
            self._cancel_countin()
        else:
            self._stop_recording()

    def _start_countin(self):
        if self.edit_chip.get():
            self.edit_chip.set(False)
        self._rec_state = "countin"
        self._set_record_look("Cancel", rec=False)
        self._countin_step = _COUNTIN_BEATS
        self._status("Count-in …")
        self._on_countin_tick()

    def _cancel_countin(self):
        if self._countin_job is not None:
            try:
                self.after_cancel(self._countin_job)
            except Exception:
                pass
            self._countin_job = None
        self.canvas.set_countin_text("")
        self._rec_state = "idle"
        self._set_record_look("● Record", rec=False)
        self._status("Count-in canceled.")

    def _on_countin_tick(self):
        if self._rec_state != "countin":
            return
        step = self._countin_step
        if step <= 0:
            self._countin_job = None
            self.canvas.set_countin_text("")
            self._start_recording()
            return
        self._hook_call("synth_met_click", step == _COUNTIN_BEATS)
        self.canvas.set_countin_text(str(step))
        self._countin_step -= 1
        beat_ms = int(60000.0 / max(1.0, self.canvas.model.bpm)
                      / max(0.1, self._speed))
        self._countin_job = self.after(max(50, beat_ms), self._on_countin_tick)

    def _start_recording(self):
        if self.edit_chip.get():
            self.edit_chip.set(False)
        self._rec_state = "recording"
        self._rec_buffer = []
        self._set_record_look("■ Stop", rec=True)
        self.canvas.set_capture_hook(self._on_capture_lane)
        if not self.canvas.is_playing:
            self._on_play_toggle()
        self._status("● Recording — keys 1–8 drop hits at the hit line.")

    def _stop_recording(self):
        self._rec_state = "idle"
        self._set_record_look("● Record", rec=False)
        self.canvas.set_capture_hook(None)
        k = self.canvas.commit_take(self._rec_buffer)
        self._rec_buffer = []
        if self.canvas.is_playing:
            self._on_play_toggle()
        if k:
            self._status("● Recorded %d hit%s — Ctrl+Z undoes the take."
                         % (k, "" if k == 1 else "s"))
        else:
            self._status("● Recording stopped — no hits captured.")

    def _set_record_look(self, text, rec=False):
        self.record_btn.configure(text=text)
        self.record_btn.accent = "#ff8080" if rec else RED
        self.record_btn._restyle()

    def _on_capture_lane(self, lane, vel=100):
        if self._rec_state != "recording":
            return
        # A raw/forwarded MIDI note can carry an out-of-range or non-int lane and
        # a dict/NaN velocity -> _VOICE_FOR_LANE[lane] / int(vel) crashed the
        # capture path (breaker fix F5, 2026-07-21). Validate before buffering.
        try:
            lane = int(lane)
        except (TypeError, ValueError):
            return
        if not (0 <= lane < LANE_COUNT):
            return
        v = _finite_or(vel, 100.0)
        vel = max(1, min(127, int(v)))
        self._rec_buffer.append((self.canvas.now, lane, vel))
        self._hook_call("synth_play", _VOICE_FOR_LANE[lane], vel)
        self._status("● captured %s" % LANE_NAMES[lane])

    def _on_countin_toggled(self, value):
        self._cfg_set(T9_COUNTIN_KEY, value)

    def _on_metro_toggled(self, value):
        self._cfg_set(T9_METRO_KEY, value)

    # (v4.9.0 — the native MIDI-input device dropdown was removed: the Preview
    # tab is for WATCHING notes fall + spot-editing, not playing along. Live
    # recording still captures keyboard 1–8 taps. The host's midi_* hooks stay
    # available but this tab no longer registers a MIDI sink.)

    # ----- A/B loop ----------------------------------------------------
    def _on_loop_set_a(self):
        self._loop_a = self.canvas.now
        self._arm_loop()

    def _on_loop_set_b(self):
        self._loop_b = self.canvas.now
        self._arm_loop()

    def _on_loop_clear(self):
        self._loop_on = False
        self._loop_a = 0.0
        self._loop_b = 0.0
        self.loop_readout_lbl.configure(text="off")
        self._status("A/B loop cleared.")

    def _arm_loop(self):
        self._loop_on = self._loop_b > self._loop_a
        text = self._loop_readout_text()
        self.loop_readout_lbl.configure(text=text)
        self._status("A/B loop: %s" % text)

    def _loop_readout_text(self):
        if not self._loop_on:
            return "off"
        return "%s → %s" % (fmt_time(self._loop_a), fmt_time(self._loop_b))

    # ----- ⚙ Settings / ? popovers ------------------------------------------
    def _on_settings_toggle(self):
        if self.settings_popup.is_open():
            self.settings_popup.hide()
            return
        self.help_popup.hide()
        self.settings_popup.show_below(self.settings_btn)

    def _on_help_toggle(self):
        if self.help_popup.is_open():
            self.help_popup.hide()
            return
        self.settings_popup.hide()
        self.help_popup.show_below(self.help_btn)

    # ----- Settings panel -> view/engine (each persists) --------------------
    def _on_lane_vis(self, lane, visible):
        self.canvas.set_lane_visible(lane, visible)
        self._cfg_set(T9_LANES_KEY, [c.get() for c in self._lane_chips])

    def _on_squares(self, value):
        self.canvas.set_squares(value)
        self._cfg_set(T9_SQUARES_KEY, value)

    def _on_cymbal_oval(self, value):
        self.canvas.set_cymbal_oval(value)
        self._cfg_set(T9_CYMBAL_OVAL_KEY, value)

    def _on_classic_style(self, value):
        self.canvas.set_classic_style(value)
        self._cfg_set(T9_CLASSIC_KEY, value)

    def _on_kick_line(self, value):
        self.canvas.set_kick_line(value)
        self._cfg_set(T9_KICK_LINE_KEY, value)

    def _on_beat_grid(self, value):
        self.canvas.set_beat_grid(value)
        self._cfg_set(T9_BEAT_GRID_KEY, value)

    def _on_note_size_scale(self, value):
        try:
            v = int(round(float(value)))
        except (TypeError, ValueError):
            return
        v = max(50, min(160, v))
        scale = v / 100.0
        self.note_size_readout.configure(text="%.2f×" % scale)
        self.canvas.set_note_scale(scale)
        self._cfg_set(T9_NOTE_SCALE_KEY, scale)

    def _reset_note_size(self):
        self.note_size_var.set(100.0)
        self._on_note_size_scale(100.0)

    def _on_audio_offset_scale(self, value):
        try:
            v = int(round(float(value)))
        except (TypeError, ValueError):
            return
        v = max(-200, min(200, v))
        self.audio_offset_readout.configure(text="%d ms" % v)
        self._cfg_set(T9_OFFSET_KEY, v)
        if "mixer_seek" in self.hooks:
            self._mixer_recue(self.canvas.now)   # re-cue nudge

    # ----- editing ---------------------------------------------------------
    def _on_edit_toggled(self, checked):
        if checked and self.canvas.is_playing:
            self.canvas.pause()
            self._hook_call("mixer_pause")
            self.play_btn.configure(text="▶ Play")
        self.canvas.set_edit_mode(checked)
        self.edit_chip.configure(text="✎ Editing" if checked else "✎ Edit")
        self.hint_label.configure(text=_HINT_EDIT if checked else _HINT_PLAY)

    # ----- import (.mid / .rlrr / parakit-chart .json) ----------------------
    def import_chart(self, path):
        """Load a .mid / .rlrr / .json chart into the preview (smoke-callable)."""
        try:
            notes, bpm = eng.load_chart_as_notes(path)
        except Exception as exc:
            self._status("Import failed — %s" % exc, AMBER)
            return False
        if not notes:
            self._status("No drum notes found in %s." % os.path.basename(path))
            return False
        bpm = eng.safe_bpm(bpm)
        self._before_chart_swap()
        self.canvas.set_chart(notes, bpm)
        self.seek_t1.configure(text=fmt_short(self.canvas.duration))
        self._update_seek_from_view(0.0)
        self._sync_bpm_label()
        self.play_btn.configure(text="▶ Play")
        self._chart_source = "custom"
        self._chart_dirty = False   # a freshly installed chart is clean
        self._chart_title = os.path.splitext(os.path.basename(path))[0]
        self._chart_file_basename = os.path.basename(path)
        self._set_song_readout()
        self._update_demo_ui()
        self._status("Imported %d notes from %s (bpm %.0f). Press Play."
                     % (len(notes), os.path.basename(path), bpm))
        ok, result = self._hook_call("auto_fetch_audio", path)
        if ok and isinstance(result, dict):
            mix = result.get("mix")
            drums = result.get("drums")
            if mix:
                self._load_stem("song", mix)
            if drums:
                self._load_stem("drums", drums)
        return True

    def _on_import(self):
        if not self._confirm_discard_edits("Importing another chart"):
            return
        start_dir = self._cfg_get(T9_IMPORT_DIR_KEY, "") or None
        path = filedialog.askopenfilename(
            title="Import a chart (.mid / .rlrr / .json)", initialdir=start_dir,
            filetypes=[("Charts", "*.mid *.midi *.rlrr *.json"),
                       ("MIDI", "*.mid *.midi"), ("Paradiddle", "*.rlrr"),
                       ("ParaKit chart", "*.json"), ("All files", "*.*")])
        if not path:
            return
        self._cfg_set(T9_IMPORT_DIR_KEY, os.path.dirname(path))
        self.import_chart(path)

    # ----- export (parakit-chart .json / GM .mid) ---------------------------
    def export_chart(self, path):
        """Write the current chart to path by extension (smoke-callable)."""
        notes = self.canvas.model.notes
        if not notes:
            self._status("Nothing to export — the chart is empty.")
            return False
        try:
            if path.lower().endswith((".mid", ".midi")):
                count = eng.write_smf0(notes, self.canvas.model.bpm, path)
            else:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(eng.dump_parakit_chart_v1(notes, self.canvas.model.bpm))
                count = len(notes)
        except Exception as exc:
            self._status("Export failed — %s" % exc, AMBER)
            return False
        self._chart_dirty = False   # exported: the edits are preserved on disk
        self._status("⇩ Exported %d notes → %s" % (count, os.path.basename(path)))
        return True

    def _on_export(self):
        start_dir = self._cfg_get(T9_IMPORT_DIR_KEY, "") or None
        path = filedialog.asksaveasfilename(
            title="Export the chart", initialdir=start_dir,
            initialfile="parakit-chart.mid", defaultextension=".mid",
            filetypes=[("MIDI", "*.mid"), ("ParaKit chart", "*.json")])
        if not path:
            return
        self._cfg_set(T9_IMPORT_DIR_KEY, os.path.dirname(path))
        self.export_chart(path)

    # ----- perf readout ------------------------------------------------
    def _on_perf_tick(self):
        self._perf_job = None
        # Only format the readout while the tab is actually on-screen. Preview is
        # built at launch, so this used to tick every 500 ms for the whole app
        # life even while the user sat on another tab. When hidden, fall back to a
        # passive 2 s heartbeat that just re-checks visibility -- this self-resumes
        # when the tab is shown again without relying on <Map> events firing.
        mapped = bool(self.winfo_ismapped())
        if mapped:
            try:
                self.perf_label.configure(text="%s · drawn %d / %d · %s / %s" % (
                    "playing" if self.canvas.is_playing else "paused",
                    self.canvas.last_drawn, self.canvas.total_notes,
                    fmt_time(self.canvas.now), fmt_short(self.canvas.duration)))
            except tk.TclError:
                return
        try:
            self._perf_job = self.after(500 if mapped else 2000, self._on_perf_tick)
        except Exception:
            pass

    # ----- cross-tab handoff (inbound from the MIDI Editor) -----------------
    def receive(self, action, payload):
        """Host-callable inbound handoff. ``action == "load_chart"`` with
        ``payload = {"notes": [...], "bpm": float, "source": str}``. Any
        exception is swallowed with a status note -- never crashes the host."""
        try:
            if action != "load_chart":
                return
            # A cross-tab send replaces the chart exactly like a local import, so it
            # gets the same warning (2026-08-01). Without this, every LOCAL swap path
            # prompted while a send from the MIDI Editor / Song Tester / Creator
            # silently destroyed edited work -- and the H7 hand-off ends in precisely
            # this method, so "save your editor edits and send to Preview" was the
            # one flow that could still lose Preview-side edits without asking.
            # Costs nothing on a clean or demo chart, so the selftest and any
            # programmatic caller are unaffected.
            if not self._confirm_discard_edits("Loading the received chart"):
                self._status("Kept the current chart — the incoming chart was "
                             "not loaded.")
                return
            payload = payload or {}
            core_notes = payload.get("notes") or []
            try:
                bpm = float(payload.get("bpm", 120.0)) or 120.0
            except (TypeError, ValueError):
                bpm = 120.0
            bpm = eng.safe_bpm(bpm)
            notes = []
            skipped = 0
            # Per-note guard (breaker fix, 2026-07-20): one malformed note (a
            # short tuple, a non-numeric vel, a NaN time) must NOT sink the whole
            # hand-off and silently revert to the old chart — skip it, keep the
            # valid notes, and report the count.
            for cn in core_notes:
                try:
                    if hasattr(cn, "time") and hasattr(cn, "lane"):
                        t, lane = float(cn.time), int(cn.lane)
                        vel = int(getattr(cn, "vel", 100))
                    elif isinstance(cn, dict):
                        t = float(cn.get("time", 0.0))
                        lane = int(cn.get("lane", 0))
                        vel = int(cn.get("vel", 100))
                    else:
                        t, lane, *rest = cn
                        t, lane = float(t), int(lane)
                        vel = int(rest[0]) if rest else 100
                    if (not math.isfinite(t) or abs(t) > _MAX_NOTE_TIME
                            or not (0 <= lane < LANE_COUNT)):
                        skipped += 1
                        continue
                    notes.append(eng.EdNote(time=max(0.0, t), lane=lane,
                                            vel=max(1, min(127, vel))))
                except Exception:
                    skipped += 1
            self._before_chart_swap()
            self.canvas.set_chart(notes, bpm)
            self.seek_t1.configure(text=fmt_short(self.canvas.duration))
            self._update_seek_from_view(0.0)
            self._sync_bpm_label()
            self.play_btn.configure(text="▶ Play")
            source = payload.get("source") or "the MIDI Editor"
            self._chart_source = "custom"
            self._chart_dirty = False   # a freshly installed chart is clean
            self._chart_title = payload.get("title") or source
            p = payload.get("path") or ""
            self._chart_file_basename = (os.path.basename(str(p)) if p
                                         else str(self._chart_title or ""))
            self._set_song_readout()
            self._update_demo_ui()
            msg = ("Received %d notes from %s (bpm %.0f)."
                   % (len(notes), source, bpm))
            if skipped:
                msg += " (%d malformed skipped.)" % skipped
            self._status(msg + " Press Play.")
        except Exception as exc:
            self._status("receive() failed: %s" % exc, AMBER)

    # ----- keyboard (only when the tab is visible) --------------------------
    def _bind_keys(self):
        top = self.winfo_toplevel()
        self._key_binds = []
        seqs = [("<space>", self._key_space), ("<Home>", self._key_home),
                ("<Control-z>", self._key_undo), ("<Control-y>", self._key_redo),
                ("<Control-Z>", self._key_redo),
                ("<e>", self._key_edit), ("<E>", self._key_edit),
                ("<Escape>", self._key_escape),
                ("<g>", self._key_snap), ("<G>", self._key_snap),
                ("<Delete>", self._key_delete), ("<BackSpace>", self._key_delete)]
        for digit in eng.HOTKEY_LANES:
            seqs.append(("<Key-%s>" % digit, self._key_digit))
        for seq, fn in seqs:
            self._key_binds.append((seq, top.bind(seq, fn, add="+")))
        # Focus fix (v4.9.2): the shortcuts are bound to the TOPLEVEL, so they
        # only fire when some widget in this tab's window holds keyboard focus.
        # When the notebook switches to this tab, nothing here is focused yet —
        # so E / digits / Space / etc. did NOTHING until the user first clicked
        # inside. Grab focus onto the canvas as soon as the tab is shown (<Map>)
        # and on any canvas click, so the shortcuts work the moment the tab is
        # visible. (Same reason the old Practice-v2 view used bind_all.)
        self.canvas.bind("<Map>",
                         lambda _e: self.after_idle(self._grab_key_focus),
                         add="+")
        self.canvas.bind("<Button-1>",
                         lambda _e: self._grab_key_focus(), add="+")

    def _grab_key_focus(self):
        try:
            if self.winfo_ismapped() and not self._text_focus():
                self.canvas.focus_set()
        except tk.TclError:
            pass

    def _unbind_keys(self):
        """Remove ONLY our chained handlers from the toplevel (never the
        whole script -- the host or other tabs may share these sequences)."""
        try:
            top = self.winfo_toplevel()
        except Exception:
            return
        for seq, fid in getattr(self, "_key_binds", ()):
            try:
                script = top.bind(seq) or ""
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

    def _text_focus(self):
        w = self.focus_get()
        return (w is not None and w.winfo_class()
                in ("TEntry", "Entry", "TCombobox", "Text"))

    def _keys_active(self):
        try:
            return self.winfo_ismapped() and not self._text_focus()
        except (tk.TclError, KeyError):
            return False

    def _gesture_active(self):
        return getattr(self.canvas, "_mode", None) in ("move", "maybe_move",
                                                        "erase")

    def _key_space(self, _event):
        if not self._keys_active():
            return None
        self._on_play_toggle()
        return "break"

    def _key_home(self, _event):
        if not self._keys_active():
            return None
        self._on_stop()
        return "break"

    def _key_undo(self, _event):
        if not self._keys_active() or self._gesture_active():
            return None
        self._on_undo()
        return "break"

    def _key_redo(self, _event):
        if not self._keys_active() or self._gesture_active():
            return None
        self._on_redo()
        return "break"

    def _key_edit(self, _event):
        if not self._keys_active():
            return None
        self.edit_chip.set(not self.edit_chip.get())
        return "break"

    def _key_escape(self, _event):
        if not self._keys_active():
            return None
        if self.canvas.edit_mode:
            self.edit_chip.set(False)
        return "break"

    def _key_snap(self, _event):
        if not self._keys_active() or self._gesture_active():
            return None
        self._sync_snap_from_key(not self.canvas.snap_on)
        return "break"

    def _key_delete(self, _event):
        if not self._keys_active() or self._gesture_active():
            return None
        k = self.canvas.delete_selection()
        self._status("Deleted %d note%s." % (k, "" if k == 1 else "s") if k
                     else "Nothing selected.")
        return "break"

    def _key_digit(self, event):
        if not self._keys_active() or self._gesture_active():
            return None
        lane = eng.HOTKEY_LANES.get(event.char)
        if lane is None:
            return None
        if self.canvas._capture_hook is not None:
            self.canvas._capture_hook(lane)
        else:
            self.canvas.place_at_time(self.canvas.now, lane)
        return "break"

    # ----- teardown ------------------------------------------------------
    def external_stop(self):
        """Public stop for the HOST app (v4's tab-changed handler calls this
        when the user leaves the tab): stops the transport AND resets the
        transport UI. Safe to call at any time."""
        try:
            self._on_stop()
        except Exception:
            pass

    def destroy(self):
        # Stop any live mixer stream FIRST (breaker H8, 2026-07-29): both siblings
        # already do this -- SpectralTab.destroy (B-DEST-1, 2026-07-21) and
        # PracticeTab.destroy -- but Preview only cancelled its timers, so a frame
        # rebuild / teardown while playing left audio going with no tab owning it.
        # (A normal tab SWITCH already stops via the host's
        # _preview_on_tab_changed_stop; this covers the destroy path.)
        # Order matters: stop BEFORE cancelling the jobs. external_stop -> _on_stop
        # cannot arm an after() job (_perf_job is re-armed only inside _on_perf_tick,
        # reachable only from the Tk timer), so nothing gets resurrected -- and
        # external_stop's try/except is what makes this safe on a tearing-down
        # widget, which is why this calls it rather than _on_stop directly.
        try:
            self.external_stop()
        except Exception:
            pass
        for attr in ("_perf_job", "_countin_job"):
            job = getattr(self, attr, None)
            if job is not None:
                try:
                    self.after_cancel(job)
                except Exception:
                    pass
                setattr(self, attr, None)
        self._unbind_keys()
        super().destroy()


# ---------------------------------------------------------------------------
# Standalone review host.
# ---------------------------------------------------------------------------
def main(argv):
    root = tk.Tk()
    apply_theme_global(root)
    root.title("Preview — TTK redesign (standalone review)")
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    w = max(1040, min(1900, int(sw * 0.85)))
    h = max(760, min(1250, int(sh * 0.85)))
    root.geometry("%dx%d" % (w, h))
    root.minsize(1040, 760)
    tab = PreviewTab(root)
    tab.pack(fill=tk.BOTH, expand=True)

    if "--selftest" in argv:
        return _selftest(root, tab)
    for arg in argv:
        if arg.startswith("--autoclose="):
            root.after(int(arg.split("=", 1)[1]), root.destroy)
    root.mainloop()
    return 0


def _selftest(root, tab):
    """Headless-safe (still needs a Tk display, but never enters mainloop)
    smoke covering the acceptance criteria in the dispatch: config round
    trip, hooks=None never crashes, demo load + index-culling proxy, edit
    ops + undo/redo, import/export round trip, receive(), external_stop(),
    and the Tooltip/Popover screen-edge clamp."""
    import tempfile

    ok = True

    # 1) v4.9.2: the tab now opens BLANK (like Spectral); "Load demo example"
    # loads the demo on request. Assert BOTH the blank start and a successful
    # demo load (hooks=None never crashes either way).
    g1a = tab.canvas.total_notes == 0 and tab._chart_source is None
    tab._on_load_demo()
    g1b = (tab.canvas.total_notes > 0 and tab.canvas.duration > 0
           and tab._chart_source == "demo")
    g1 = g1a and g1b
    print("SELFTEST blank start + Load demo: blank=%s demo notes=%d dur=%.1f -> %s"
          % (g1a, tab.canvas.total_notes, tab.canvas.duration,
             "OK" if g1 else "FAIL"))
    ok = ok and g1

    # 2) "Insane" density stays index-culled: drawn count independent of
    # total chart size (the whole de-risk property).
    tab.density_var.set("Insane  (~9.7k)")
    tab._on_density_changed()
    tab.canvas.seek(tab.canvas.duration * 0.5)
    tab.canvas._redraw()
    total_insane = tab.canvas.total_notes
    drawn_insane = tab.canvas.last_drawn
    g2 = total_insane > 5000 and 0 < drawn_insane < 500
    print("SELFTEST insane density index-culling: total=%d drawn=%d -> %s"
          % (total_insane, drawn_insane, "OK" if g2 else "FAIL"))
    ok = ok and g2

    # 3) config round trip via a dict-backed get_cfg/set_cfg (like spectral's
    # __main__ self-tests)
    store = {}
    cfg_hooks = {"get_cfg": lambda k, d=None: store.get(k, d),
                 "set_cfg": lambda k, v: store.__setitem__(k, v)}
    ctab = PreviewTab(root, hooks=cfg_hooks)
    ctab._on_fall_step(0.5)
    g3 = abs(store.get(T9_FALL_KEY, -1) - (_FALL_DEFAULT + 0.5)) < 1e-6
    ctab.destroy()
    print("SELFTEST config round-trip (fall): stored=%r -> %s"
          % (store.get(T9_FALL_KEY), "OK" if g3 else "FAIL"))
    ok = ok and g3

    # 3b) garbage config store (wrong type) must not crash construction
    try:
        btab = PreviewTab(root, hooks={"get_cfg": lambda k, d=None: 42.5,
                                       "set_cfg": lambda k, v: None})
        g3b = btab._cfg_get(T9_MIDI_KEY, "") == ""
        btab.destroy()
    except Exception as e:
        print("SELFTEST guard cfg-type raised: %r" % (e,))
        g3b = False
    print("SELFTEST guard cfg type-garbage: %s" % ("OK" if g3b else "FAIL"))
    ok = ok and g3b

    # 4) hooks=None: transport + edit ops never crash, degrade with a status
    # note (or silently, per _hook_call's missing-hook contract)
    try:
        tab._on_play_toggle()
        tab.canvas._on_tick()
        tab._on_play_toggle()   # pause
        tab.edit_chip.set(True)
        n = tab.canvas.place_at_time(1.0, 7)
        u_ok = tab.canvas.undo()
        r_ok = tab.canvas.redo()
        tab.edit_chip.set(False)
        g4 = n is not None and u_ok and r_ok
    except Exception as e:
        print("SELFTEST hooks=None transport/edit raised: %r" % (e,))
        g4 = False
    print("SELFTEST hooks=None transport+edit no crash: %s" % ("OK" if g4 else "FAIL"))
    ok = ok and g4

    # 5) import/export round trip (.json and .mid) through the TAB's own
    # methods (exercises the filedialog-free smoke-callable path)
    tmp_json = os.path.join(tempfile.gettempdir(), "parakit_preview_tab_selftest.json")
    tmp_mid = os.path.join(tempfile.gettempdir(), "parakit_preview_tab_selftest.mid")
    exp_ok = tab.export_chart(tmp_json) and tab.export_chart(tmp_mid)
    notes_before = tab.canvas.total_notes
    imp_ok = tab.import_chart(tmp_json)
    g5 = exp_ok and imp_ok and tab.canvas.total_notes == notes_before
    for p in (tmp_json, tmp_mid):
        try:
            os.remove(p)
        except OSError:
            pass
    print("SELFTEST import/export round-trip: export=%s import=%s notes=%d -> %s"
          % (exp_ok, imp_ok, tab.canvas.total_notes, "OK" if g5 else "FAIL"))
    ok = ok and g5

    # 6) receive("load_chart", ...) loads external notes
    payload = {"notes": [(0.0, 7, 100), (0.5, 2, 90), (1.0, 0, 80)],
               "bpm": 150.0, "source": "unit test"}
    tab.receive("load_chart", payload)
    g6 = tab.canvas.total_notes == 3 and abs(tab.canvas.model.bpm - 150.0) < 1e-6
    print("SELFTEST receive(load_chart): notes=%d bpm=%.1f -> %s"
          % (tab.canvas.total_notes, tab.canvas.model.bpm, "OK" if g6 else "FAIL"))
    ok = ok and g6

    # 7) external_stop() halts cleanly, incl. before any chart was ever loaded
    try:
        tab.external_stop()
        etab = PreviewTab(root, hooks={})
        etab.external_stop()
        etab.destroy()
        g7 = not tab.canvas.is_playing
    except Exception as e:
        print("SELFTEST external_stop raised: %r" % (e,))
        g7 = False
    print("SELFTEST external_stop: %s" % ("OK" if g7 else "FAIL"))
    ok = ok and g7

    # 8) popover/tooltip screen-edge clamp: an absurd off-screen anchor point
    # must resolve to a position fully inside the monitor work-area.
    class _FakePopup:
        def __init__(self):
            self._geo = None
        def update_idletasks(self):
            pass
        def winfo_reqwidth(self):
            return 430
        def winfo_reqheight(self):
            return 300
        def geometry(self, spec):
            self._geo = spec
    fake = _FakePopup()
    _position_popup_with_bounds(fake, root, 999999, 999999, margin=8)
    # Assert against the ACTUAL monitor work-area the point resolved to (not
    # winfo_screenwidth/height, which report only the PRIMARY monitor -- on
    # a multi-monitor box the clamp correctly lands on whichever monitor
    # MonitorFromPoint(DEFAULTTONEAREST) picks for that far-off-screen point,
    # which can legitimately sit outside the primary monitor's pixel range).
    m_left, m_top, m_right, m_bottom = _monitor_bounds(root, 999999, 999999)
    gx, gy = (int(v) for v in fake._geo.lstrip("+").split("+"))
    g8 = (m_left <= gx <= m_right and m_top <= gy <= m_bottom
          and gx + 430 <= m_right + 1 and gy + 300 <= m_bottom + 1)
    print("SELFTEST popup edge clamp: geo=%r monitor=(%d,%d,%d,%d) -> %s"
          % (fake._geo, m_left, m_top, m_right, m_bottom, "OK" if g8 else "FAIL"))
    ok = ok and g8

    # 9) destroy() must cancel pending after-jobs cleanly (no bgerror)
    root.tk.eval("set ::prev_bgerrs {}")
    root.tk.eval("proc bgerror {m} {lappend ::prev_bgerrs $m}")
    dtab = PreviewTab(root, hooks={})
    dtab.destroy()
    end = time.time() + 1.0
    while time.time() < end:
        root.update()
        time.sleep(0.03)
    bgerrs = root.tk.eval("set ::prev_bgerrs")
    g9 = bgerrs == ""
    print("SELFTEST destroy cancels after-jobs: bgerrors=%r -> %s"
          % (bgerrs[:80], "OK" if g9 else "FAIL"))
    ok = ok and g9

    tab.external_stop()
    print("SELFTEST %s" % ("OK" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
