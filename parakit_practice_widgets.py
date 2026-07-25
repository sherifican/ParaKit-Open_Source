"""parakit_practice_widgets.py -- shared Tkinter widget kit for the Practice
Home screen (`parakit_practice_home.py`) and the Practice tab
(`parakit_practice_tab.py`). Single source of the ParaKit dark-purple palette,
fonts, and the small reusable controls both files build with.

Ported faithfully from the shipped `parakit_spectral_tab.py` (palette block,
Tooltip, Chip, Segmented, OutlineButton, PlaceholderEntry) per
`DISPATCH_practice_preview_kimi-k3.md` section 3c. `ToggleSwitch` is a new
Tkinter port of the v5 PySide6 widget
`parakit_v5/ui/widgets/practice/toggle_switch.py` (`PracticeToggle`) -- see
its class docstring for the one documented divergence (gradient fill).

PUBLIC API (freeze this surface -- Sol/Grok import against it):

    Palette constants (str hex):
        PURPLE, PURPLE_LT, PURPLE_EDGE, PURPLE_DEEP, MAGENTA, CYAN, PANEL,
        ROW_ALT, BG, DARKER, INPUT_BG, INPUT_FG, TEXT, MUTED, GREEN, AMBER,
        CANVAS_BG, CHIP_OFF_EDGE, SEPARATOR, OUTLINE_DARK
        (additive V3 web-reference tokens -- do not rename existing):
        EMBER, EMBER_LT, HOT_PINK, STEEL, TEXT_BRIGHT, MUTED_LAV, PANEL2

    Font constants (Tk font tuples):
        F_H1, F_BASE, F_BOLD, F_SMALL, F_MONO, F_MONO_B, F_MONO_SMALL_B

    Colour helpers:
        hex_rgb(color: str) -> (r, g, b)
        rgb_hex(r, g, b) -> str
        blend(c1: str, c2: str, t: float) -> str

    Theming (private `Prac.*` ttk namespace):
        apply_theme_global(root: tk.Misc) -> ttk.Style
            Standalone-host entry point: theme_use('clam'), root bg, combobox
            popdown colours, then _configure_prac_styles(style).
        apply_theme_embedded(widget: tk.Widget) -> ttk.Style
            Embedded-host entry point: only _configure_prac_styles(style),
            leaves the parent app's own ttk theme untouched.
        _configure_prac_styles(style: ttk.Style)
            Configures every 'Prac.*' style name used by the widgets below.

    Widgets:
        Tooltip(widget: tk.Widget, text: str)
            Screen-edge/border-aware hover tooltip. No public methods besides
            hide(event=None); attach-on-construct like the spectral original.
        Chip(parent, text, accent=CYAN, on=False, command=None, tooltip=None,
             off_edge=None)
            Coloured-outline toggle chip. .get() -> bool, .set(value, fire=True),
            .set_enabled(enabled).
        OutlineButton(parent, text, accent=PURPLE_EDGE, command=None,
                      tooltip=None, enabled=True, filled=False, ...)
            Momentary coloured-outline button (optional filled=True solid look).
            .set_enabled(enabled).
        GradientButton(parent, text, command, stops=(...), width=None,
                       height=44, font=...)
            Canvas multi-stop horizontal gradient button (Play CTAs).
            .configure_state(normal|disabled), .set_enabled(enabled).
        Segmented(parent, options: list[str], command=None)
            Multi-option segmented control. .get() -> int, .set(index, fire=True).
        PlaceholderEntry(parent, placeholder="", tooltip=None, width=30)
            ttk.Entry with placeholder text. .get() -> str, .set(text), .clear().
        ToggleSwitch(parent, on=False, command=None, enabled=True, tooltip=None)
            Canvas-drawn pill on/off switch (Tk port of v5 PracticeToggle).
            .get() -> bool, .set(value, fire=True), .set_enabled(enabled).

Hard constraints: stdlib + tkinter/ttk only for most widgets. ToggleSwitch
additionally uses Pillow (PIL) for 4×-supersampled pill/knob rendering
(same technique as parakit_*_sprites.py) — already a ParaKit runtime dep.
No numpy.
"""
from __future__ import annotations

import functools
import math
import os
import sys
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk

# ---------------------------------------------------------------------------
# Palette -- ParaKit v4 dark-purple identity. Copied verbatim from
# parakit_spectral_tab.py so every Practice surface reads identically to the
# rest of the app. Kept as module constants so a future theming pass can swap
# them in one place.
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

# V3 web-reference tokens (Practice Window v3 HTML :root) -- additive only.
EMBER = "#9F67FF"         # primary ember / purple accent
EMBER_LT = "#C4B5FD"      # lavender text / light ember
HOT_PINK = "#ff69b4"      # play-button gradient end
STEEL = "#00e5ff"         # steel cyan accent
TEXT_BRIGHT = "#EFECFF"   # main bright text (lavender-white)
MUTED_LAV = "#9A95C0"     # muted lavender secondary text
PANEL2 = "#1b1b35"        # elevated panel / pill chip bg

# Canvas-specific colours (match the v5 views so the two apps read alike).
CANVAS_BG = "#070710"
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
F_H2 = ("Segoe UI", 10, "bold")            # GradientButton default label


# ---------------------------------------------------------------------------
# Colour helpers -- verbatim from parakit_spectral_tab.py.
# ---------------------------------------------------------------------------
def hex_rgb(color: str):
    """'#rrggbb' -> (r, g, b) ints."""
    color = color.lstrip("#")
    return (int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16))


def rgb_hex(r: int, g: int, b: int) -> str:
    return "#%02x%02x%02x" % (max(0, min(255, int(r))),
                              max(0, min(255, int(g))),
                              max(0, min(255, int(b))))


def blend(c1: str, c2: str, t: float) -> str:
    """Linear blend c1 -> c2, t in [0,1]."""
    a, b = hex_rgb(c1), hex_rgb(c2)
    return rgb_hex(a[0] + (b[0] - a[0]) * t,
                   a[1] + (b[1] - a[1]) * t,
                   a[2] + (b[2] - a[2]) * t)


# ---------------------------------------------------------------------------
# Theme -- ALL ttk styling lives here, under the private 'Prac.*' namespace
# (mirrors parakit_spectral_tab.py's 'Spec.*' namespace, renamed Spec->Prac)
# so this module never clobbers a host app's own theming.
# ---------------------------------------------------------------------------
def apply_theme_global(root: tk.Misc) -> ttk.Style:
    """Standalone-host entry point (e.g. `python parakit_practice_home.py`)."""
    style = ttk.Style(root)
    # 'clam' is the only stock theme whose colours are fully configurable on
    # Windows (vista/xpnative ignore background overrides).
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    _configure_prac_styles(style)
    root.configure(bg=BG)
    root.option_add("*TCombobox*Listbox.background", INPUT_BG)
    root.option_add("*TCombobox*Listbox.foreground", INPUT_FG)
    root.option_add("*TCombobox*Listbox.selectBackground", PURPLE)
    return style


def apply_theme_embedded(widget: tk.Widget) -> ttk.Style:
    """Set up only the Prac.* styles for an embedded screen/tab. No
    theme_use, no option_add -- the parent application owns those."""
    style = ttk.Style(widget)
    _configure_prac_styles(style)
    return style


def _configure_prac_styles(style: ttk.Style):
    """Prac.* namespace -- the ONLY styles used by the widgets in this module.
    Configured identically whether the host used apply_theme_global or
    apply_theme_embedded."""
    style.configure("Prac.", background=BG, foreground=TEXT, font=F_BASE,
                    bordercolor=SEPARATOR, darkcolor=BG, lightcolor=BG,
                    troughcolor=BG, fieldbackground=INPUT_BG,
                    selectbackground=PURPLE, selectforeground=INPUT_FG)
    style.configure("Prac.TFrame", background=BG)
    style.configure("Prac.Panel.TFrame", background=PANEL)
    style.configure("Prac.TLabel", background=BG, foreground=TEXT)
    style.configure("Prac.Panel.TLabel", background=PANEL, foreground=TEXT)
    style.configure("Prac.Muted.TLabel", background=BG, foreground=MUTED)
    style.configure("Prac.PanelMuted.TLabel", background=PANEL, foreground=MUTED)
    style.configure("Prac.Header.TLabel", background=PANEL, foreground=EMBER_LT,
                    font=F_H1)
    style.configure("Prac.Cyan.TLabel", background=PANEL, foreground=CYAN)
    style.configure("Prac.TLabelframe", background=PANEL, foreground=PURPLE_LT,
                    bordercolor=PURPLE_EDGE)
    style.configure("Prac.TLabelframe.Label", background=PANEL,
                    foreground=PURPLE_LT, font=F_BOLD)
    style.configure("Prac.TEntry", fieldbackground=INPUT_BG, foreground=INPUT_FG,
                    insertcolor=INPUT_FG, bordercolor=PURPLE_EDGE,
                    lightcolor=INPUT_BG, darkcolor=INPUT_BG, padding=3)
    style.configure("Prac.Placeholder.TEntry", fieldbackground=INPUT_BG,
                    foreground=MUTED, insertcolor=INPUT_FG,
                    bordercolor=PURPLE_EDGE, lightcolor=INPUT_BG,
                    darkcolor=INPUT_BG, padding=3)
    style.configure("Prac.TCombobox", fieldbackground=INPUT_BG,
                    foreground=INPUT_FG, background=INPUT_BG,
                    arrowcolor=PURPLE_LT, bordercolor=PURPLE_EDGE, padding=2)
    style.map("Prac.TCombobox",
              fieldbackground=[("readonly", INPUT_BG)],
              foreground=[("readonly", INPUT_FG)],
              selectbackground=[("readonly", INPUT_BG)],
              selectforeground=[("readonly", INPUT_FG)])
    style.configure("Prac.Horizontal.TScale", background=PURPLE_LT,
                    troughcolor=PURPLE_DEEP, bordercolor=PANEL,
                    lightcolor=PURPLE_LT, darkcolor=PURPLE)
    style.configure("Prac.Horizontal.TScrollbar", background="#26263e",
                    troughcolor=BG, arrowcolor=MUTED, bordercolor="#26263e",
                    lightcolor="#26263e", darkcolor="#26263e")
    style.map("Prac.Horizontal.TScrollbar",
              background=[("active", "#34345a")],
              bordercolor=[("active", "#34345a")],
              lightcolor=[("active", "#34345a")],
              darkcolor=[("active", "#34345a")])
    # VERTICAL variant (owner-reported 2026-07-23: the library scrollbar was
    # styled with the Horizontal name, so ttk gave a vertical widget the
    # horizontal element layout -> the thumb wouldn't drag). A ttk scrollbar
    # style MUST match the widget's orientation, so vertical bars need their own.
    style.configure("Prac.Vertical.TScrollbar", background="#26263e",
                    troughcolor=BG, arrowcolor=MUTED, bordercolor="#26263e",
                    lightcolor="#26263e", darkcolor="#26263e")
    style.map("Prac.Vertical.TScrollbar",
              background=[("active", "#34345a")],
              bordercolor=[("active", "#34345a")],
              lightcolor=[("active", "#34345a")],
              darkcolor=[("active", "#34345a")])
    style.configure("Prac.Primary.TButton", background=PURPLE,
                    foreground="#ffffff", bordercolor=PURPLE_EDGE,
                    focuscolor=PURPLE, lightcolor=PURPLE, darkcolor=PURPLE,
                    padding=(14, 5), font=F_BOLD)
    style.map("Prac.Primary.TButton",
              background=[("active", "#8b5cf6"), ("disabled", "#241a38")],
              foreground=[("disabled", MUTED)])
    style.configure("Prac.TButton", background=ROW_ALT, foreground=TEXT,
                    bordercolor=SEPARATOR, lightcolor=ROW_ALT,
                    darkcolor=ROW_ALT, padding=(8, 3))
    style.map("Prac.TButton", background=[("active", "#232344")])


# ---------------------------------------------------------------------------
# Small widgets ------------------------------------------------------------
# ---------------------------------------------------------------------------
class Tooltip:
    """Minimal hover tooltip (tk has none built in). Delays 450 ms, follows
    the widget's bottom-left, kills itself on leave/click.

    Screen-edge / border-aware (copied verbatim from parakit_spectral_tab.py,
    the origin of this rule, 2026-07-20): the popup is packed BEFORE
    positioning so `_position_with_bounds` can read its resolved size
    (winfo_reqwidth/reqheight) and clamp/flip it against the monitor's
    work-area so it never opens off-screen. Do not replace with a naive
    `geometry(+x+y)`.
    """

    _DELAY_MS = 450

    def __init__(self, widget: tk.Widget, text: str):
        self._widget = widget
        self._text = text
        self._tip = None
        self._after_id = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")
        # Cancel the pending after-job on widget destroy (breaker fix, 2026-07-21):
        # library rows (+ their tooltipped difficulty pills) are destroyed on
        # EVERY search keystroke/sort/scan via _rebuild_list, so a hover-armed
        # _show timer routinely fired post-destroy -> Tcl "invalid command name"
        # bgerror. Kept in lock-step with the Preview/Spectral Tooltip copies.
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
        # edge of the monitor it lives on.
        self._position_with_bounds(tip, x, y, margin=8)
        self._tip = tip

    def _position_with_bounds(self, popup, x, y, margin=8):
        """Clamp a tooltip Toplevel to the visible monitor work-area, flipping
        above the host widget if it would overflow the bottom. Falls back to
        plain geometry() on any error."""
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


class Segmented(tk.Frame):
    """Multi-option segmented control (e.g. difficulty tabs)."""

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
    """Momentary button in v4's coloured-outline option-button language.

    Additive `filled=True` variant paints a solid filled look (reference's
    filled-ember primary) instead of the coloured outline. Outline mode is
    unchanged when filled is False (the default).
    """

    def __init__(self, parent, text, accent=PURPLE_EDGE, command=None,
                 tooltip=None, enabled=True, filled=False,
                 fill_bg=None, fill_fg=None, fill_hover=None):
        super().__init__(parent, text=text, font=F_SMALL, padx=9, pady=2,
                         cursor="hand2", highlightthickness=1)
        self.accent = accent
        self._command = command
        self._enabled = bool(enabled)
        self._hover = False
        self._filled = bool(filled)
        # Filled-ember defaults match the V3 web reference primary fill.
        self._fill_bg = fill_bg or "#6D2BD9"
        self._fill_fg = fill_fg or TEXT_BRIGHT
        self._fill_hover = fill_hover or "#7C3AED"
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
        if self._filled:
            if not self._enabled:
                fg, edge, bg = "#4a4a60", "#2c2c42", PANEL
            elif self._hover:
                fg, edge, bg = self._fill_fg, self._fill_hover, self._fill_hover
            else:
                fg, edge, bg = self._fill_fg, self._fill_bg, self._fill_bg
        elif not self._enabled:
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


class GradientButton(tk.Canvas):
    """Canvas multi-stop horizontal gradient button (V3 web play CTAs).

    Draws ~2px vertical colour strips interpolating the given stops and a bold
    centred label, filling the FULL canvas rectangle edge-to-edge (boxy /
    square corners — no rounded-rect masking). Hover brightens ~8%
    (precomputed strip set); press nudges the text 1px down; click fires
    `command`. Disabled state dims the whole face.

    API:
        configure_state("normal"|"disabled")
        set_enabled(enabled)   -- alias used by Home's play-button state logic
    """

    _STRIP = 2

    def __init__(self, parent, text, command=None,
                 stops=("#7C3AED", "#A044E3", "#ff69b4"),
                 width=None, height=44, font=None, tooltip=None,
                 padx=18):
        self._text = str(text)
        self._command = command
        self._stops = tuple(stops) if stops else ("#7C3AED", "#A044E3", "#ff69b4")
        self._height = int(height)
        self._font = font or F_H2
        self._enabled = True
        self._hover = False
        self._pressed = False
        self._padx = int(padx)

        # Measure label for auto-width when width is None.
        try:
            fnt = tkfont.Font(font=self._font)
            text_w = fnt.measure(self._text)
        except tk.TclError:
            text_w = max(80, len(self._text) * 8)
        self._width = int(width) if width is not None else max(120, text_w + 2 * self._padx)

        # Canvas bg = first gradient stop so any 1px strip seam is gradient-
        # coloured, never a dark parent-bg gap (the old rounded-corner fringe).
        face0 = self._stops[0]
        super().__init__(parent, width=self._width, height=self._height,
                         highlightthickness=0, bd=0, background=face0,
                         cursor="hand2")

        # Precompute normal + hover strip colours (hover = ~8% brighter).
        self._strips_normal = self._build_strips(self._stops, brighten=0.0)
        self._strips_hover = self._build_strips(self._stops, brighten=0.08)
        self._strips_disabled = [
            blend(c, BG, 0.55) for c in self._strips_normal
        ]

        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        if tooltip:
            Tooltip(self, tooltip)
        self._redraw()

    def _build_strips(self, stops, brighten=0.0):
        """Return a list of hex colours, one per strip column across width."""
        n = max(1, int(math.ceil(self._width / float(self._STRIP))))
        stops = list(stops)
        if len(stops) == 1:
            stops = [stops[0], stops[0]]
        segs = len(stops) - 1
        out = []
        for i in range(n):
            t = i / max(1, n - 1)
            seg_f = t * segs
            si = min(segs - 1, int(seg_f))
            local = seg_f - si
            c = blend(stops[si], stops[si + 1], local)
            if brighten:
                # Blend toward white by `brighten` fraction.
                c = blend(c, "#ffffff", brighten)
            out.append(c)
        return out

    def _redraw(self):
        self.delete("all")
        w, h = self._width, self._height
        if not self._enabled:
            strips = self._strips_disabled
            label_fg = blend(TEXT_BRIGHT, BG, 0.55)
        elif self._hover:
            strips = self._strips_hover
            label_fg = "#ffffff"
        else:
            strips = self._strips_normal
            label_fg = TEXT_BRIGHT

        # Keep canvas bg in the face palette so strip seams never flash parent bg.
        if strips:
            try:
                self.configure(background=strips[0])
            except tk.TclError:
                pass

        # Horizontal multi-stop gradient via vertical strips — full box,
        # edge-to-edge (0,0 → w,h). No corner patches, no rounded silhouette.
        for i, color in enumerate(strips):
            x0 = i * self._STRIP
            x1 = min(w, x0 + self._STRIP + 1)  # slight overlap, no seams
            self.create_rectangle(x0, 0, x1, h, fill=color, outline="", width=0)

        # Crisp 1px border rectangle in the mid-gradient colour.
        edge = blend(strips[len(strips) // 2], "#000000", 0.25) if strips else BG
        self.create_rectangle(0, 0, w - 1, h - 1, fill="", outline=edge, width=1)

        # Subtle boxy depth: 1px top highlight + 1px bottom shadow (no rounding).
        if strips and w > 2 and h > 2:
            hi = blend(strips[0], "#ffffff", 0.20)
            lo = blend(strips[-1], "#000000", 0.30)
            self.create_line(1, 1, w - 2, 1, fill=hi, width=1)
            self.create_line(1, h - 2, w - 2, h - 2, fill=lo, width=1)

        ty = h / 2 + (1 if self._pressed and self._enabled else 0)
        self.create_text(w / 2, ty, text=self._text, fill=label_fg,
                         font=self._font, tags=("label",))

    def _on_enter(self, _event=None):
        self._hover = True
        if self._enabled:
            self._redraw()

    def _on_leave(self, _event=None):
        self._hover = False
        self._pressed = False
        self._redraw()

    def _on_press(self, _event=None):
        if not self._enabled:
            return
        self._pressed = True
        self._redraw()

    def _on_release(self, _event=None):
        if not self._enabled:
            return
        was = self._pressed
        self._pressed = False
        self._redraw()
        if was and self._command:
            # Only fire if still over the button.
            try:
                x = self.winfo_pointerx() - self.winfo_rootx()
                y = self.winfo_pointery() - self.winfo_rooty()
                if 0 <= x <= self._width and 0 <= y <= self._height:
                    self._command()
            except tk.TclError:
                self._command()

    def configure_state(self, state: str):
        """state is 'normal' or 'disabled' (tk-style)."""
        self._enabled = str(state).lower() != "disabled"
        try:
            self.configure(cursor="hand2" if self._enabled else "arrow")
        except tk.TclError:
            pass
        self._redraw()

    def set_enabled(self, enabled: bool):
        """Alias matching OutlineButton -- Home uses this for Play Loaded Song."""
        self.configure_state("normal" if enabled else "disabled")


class PlaceholderEntry(ttk.Frame):
    """ttk.Entry with placeholder text (ttk has none). Uses the Prac.* styles
    so it is safe inside an embedded screen."""

    def __init__(self, parent, placeholder="", tooltip=None, width=30):
        super().__init__(parent, style="Prac.TFrame")
        self._placeholder = placeholder
        self._has_text = False
        self.entry = ttk.Entry(self, style="Prac.Placeholder.TEntry",
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
            self.entry.configure(style="Prac.TEntry")
            self._has_text = True

    def _focus_out(self, _event=None):
        if not self.entry.get().strip():
            self._has_text = False
            self.entry.configure(style="Prac.Placeholder.TEntry")
            self.entry.delete(0, tk.END)
            self.entry.insert(0, self._placeholder)

    def get(self) -> str:
        return self.entry.get().strip() if self._has_text else ""

    def set(self, text: str):
        self.entry.configure(style="Prac.TEntry")
        self.entry.delete(0, tk.END)
        self.entry.insert(0, text)
        self._has_text = bool(text)
        if not self._has_text:
            self._focus_out()

    def clear(self):
        self.set("")


def _round_rect_points(x0, y0, x1, y1, r):
    """Point list for a rounded-rect polygon (use with create_polygon,
    smooth=True). Tkinter Canvas has no native rounded-rect primitive."""
    r = max(0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
    return [
        x0 + r, y0,
        x1 - r, y0,
        x1, y0,
        x1, y0 + r,
        x1, y1 - r,
        x1, y1,
        x1 - r, y1,
        x0 + r, y1,
        x0, y1,
        x0, y1 - r,
        x0, y0 + r,
        x0, y0,
    ]


class ToggleSwitch(tk.Canvas):
    """Pill on/off switch -- Tk port of the v5 PySide6 `PracticeToggle`
    (`ui/widgets/practice/toggle_switch.py`): a 42x24 rounded-rect track with
    an 18x18 thumb that slides left (pad 2) -> right when on.

    Colours match the v5 widget: track off `#232347` + a faint violet border,
    thumb off `#6f6a94` / on `#efecff`; on-state track uses the v5 gradient
    stops `#6d2bd9`->`#9f67ff`.

    Rendering: pill track + thumb are painted as a single 4×-supersampled PIL
    image (true left→right gradient when ON) then LANCZOS-downsampled to a
    PhotoImage — crisp anti-aliased edges like a modern web toggle, not jagged
    Canvas polygons. Hard-ref held on ``self._photo`` (Tk GC trap).

    Drop-in get()/set()/command(bool) API matching Chip/Segmented in this
    module (not a literal tkinter Variable drop-in for QCheckBox -- there is
    no such 1:1 concept in Tk). Public surface unchanged:
    set(value, fire=), get(), set_enabled(enabled). Geometry 42×24.
    """

    _TRACK_W = 42
    _TRACK_H = 24
    _THUMB = 18
    _PAD = 2
    _RADIUS = 12  # full pill (half height) — modern rounded track

    _TRACK_OFF = "#232347"
    _TRACK_BORDER = "#4a4370"   # approx rgba(196,181,253,0.30) over dark bg
    _THUMB_OFF = "#6f6a94"
    _THUMB_ON = "#efecff"
    _ON_A = "#6d2bd9"
    _ON_B = "#9f67ff"
    _SS = 4  # supersample factor (match sprite modules)

    def __init__(self, parent, on=False, command=None, enabled=True,
                 tooltip=None, background=None):
        bg = background
        if bg is None:
            try:
                bg = parent.cget("background")
            except tk.TclError:
                bg = PANEL
        # Harden against a truly-invalid background= (F7, 2026-07-23): Tk raises
        # TclError straight from Canvas.__init__ on a colour string it can't
        # parse ("" / "#ffffff00" 8-hex / "rgb(...)" / "xkcd:*" / a non-string),
        # BEFORE _redraw's paint-side fallback can help. Validate via the
        # parent's colour engine first and fall back to PANEL so construction
        # never crashes. No real caller passes these (setup card passes hex
        # PANEL) — this is defensive.
        try:
            parent.winfo_rgb(bg)
        except (tk.TclError, TypeError):
            bg = PANEL
        self._bg = bg
        super().__init__(parent, width=self._TRACK_W, height=self._TRACK_H,
                         highlightthickness=0, bd=0, cursor="hand2",
                         background=bg)
        self._on = bool(on)
        self._command = command
        self._enabled = bool(enabled)
        self._photo = None  # hard ref — Tk GC trap for PhotoImage
        self.bind("<Button-1>", self._clicked)
        if tooltip:
            Tooltip(self, tooltip)
        self._redraw()

    def _clicked(self, _event=None):
        if self._enabled:
            self.set(not self._on)

    @staticmethod
    def _hex_rgba(hex_color: str, alpha: int = 255):
        r, g, b = hex_rgb(hex_color)
        return (r, g, b, max(0, min(255, int(alpha))))

    def _render_rgba(self) -> Image.Image:
        """Build track+thumb at 4× then LANCZOS-downsample to track size."""
        ss = self._SS
        w, h = self._TRACK_W, self._TRACK_H
        ss_w, ss_h = w * ss, h * ss
        # Transparent canvas — composite onto widget bg via PhotoImage on Canvas
        # that already has background=self._bg. Paint opaque track on clear.
        img = Image.new("RGBA", (ss_w, ss_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        if not self._enabled:
            track_fill = self._hex_rgba("#1a1a2e")
            border = self._hex_rgba("#2c2c42")
            thumb_fill = self._hex_rgba("#3a3a55")
            gradient = False
        elif self._on:
            track_fill = None
            border = self._hex_rgba(self._TRACK_BORDER)
            thumb_fill = self._hex_rgba(self._THUMB_ON)
            gradient = True
        else:
            track_fill = self._hex_rgba(self._TRACK_OFF)
            border = self._hex_rgba(self._TRACK_BORDER)
            thumb_fill = self._hex_rgba(self._THUMB_OFF)
            gradient = False

        # Inset half-pixel equivalent so the border isn't clipped after downsample.
        inset = max(1, ss // 2)
        x0, y0 = inset, inset
        x1, y1 = ss_w - 1 - inset, ss_h - 1 - inset
        rad = (y1 - y0) // 2  # true pill

        if gradient:
            # True horizontal gradient masked to a pill (v5 QLinearGradient).
            grad = Image.new("RGBA", (ss_w, ss_h), (0, 0, 0, 0))
            gpx = grad.load()
            ra, ga, ba = hex_rgb(self._ON_A)
            rb, gb, bb = hex_rgb(self._ON_B)
            denom = max(1, x1 - x0)
            for x in range(x0, x1 + 1):
                t = (x - x0) / denom
                rr = int(ra + (rb - ra) * t + 0.5)
                gg = int(ga + (gb - ga) * t + 0.5)
                bb_ = int(ba + (bb - ba) * t + 0.5)
                for y in range(y0, y1 + 1):
                    gpx[x, y] = (rr, gg, bb_, 255)
            mask = Image.new("L", (ss_w, ss_h), 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                [x0, y0, x1, y1], radius=rad, fill=255
            )
            img.paste(grad, (0, 0), mask)
            # Crisp border ring on top of gradient
            ImageDraw.Draw(img).rounded_rectangle(
                [x0, y0, x1, y1], radius=rad, outline=border,
                width=max(1, ss),
            )
        else:
            ImageDraw.Draw(img).rounded_rectangle(
                [x0, y0, x1, y1], radius=rad, fill=track_fill, outline=border,
                width=max(1, ss),
            )

        # Thumb (round knob)
        thumb = self._THUMB * ss
        pad = self._PAD * ss
        if self._on:
            tx0 = ss_w - pad - thumb
        else:
            tx0 = pad
        ty0 = pad
        tx1 = tx0 + thumb - 1
        ty1 = ty0 + thumb - 1
        # Thumb -- solid fill, no gloss/shine. The off-knob is a solid grey circle
        # and the on-knob a solid white one (the old translucent top-half highlight
        # was invisible on white but read as a shine on the grey off-state).
        ImageDraw.Draw(img).ellipse(
            [tx0, ty0, tx1, ty1], fill=thumb_fill
        )

        return img.resize((w, h), Image.Resampling.LANCZOS)

    def _redraw(self):
        self.delete("all")
        rgba = self._render_rgba()
        # Composite over widget bg so transparent corners match parent.
        try:
            bg_hex = self.cget("background")
        except tk.TclError:
            bg_hex = self._bg
        _bg = bg_hex if isinstance(bg_hex, str) else self._bg
        try:
            br, bg_, bb = hex_rgb(_bg)
        except (ValueError, TypeError):
            # _bg may be a Tk NAMED colour (e.g. "SystemButtonFace" from a parent
            # with no explicit background) — hex_rgb only parses #rrggbb. Resolve
            # via Tk's own colour engine (16-bit/chan -> 8-bit) so the switch never
            # crashes on a named-colour background.
            try:
                r16, g16, b16 = self.winfo_rgb(_bg)
                br, bg_, bb = r16 // 256, g16 // 256, b16 // 256
            except tk.TclError:
                br, bg_, bb = 27, 27, 53   # dark panel fallback
        bg_img = Image.new("RGBA", (self._TRACK_W, self._TRACK_H), (br, bg_, bb, 255))
        bg_img.alpha_composite(rgba, (0, 0))
        self._photo = ImageTk.PhotoImage(bg_img, master=self)
        self.create_image(0, 0, anchor=tk.NW, image=self._photo)

    def set(self, value: bool, fire=True):
        value = bool(value)
        changed = value != self._on
        self._on = value
        self._redraw()
        if fire and changed and self._command:
            self._command(value)

    def get(self) -> bool:
        return self._on

    def set_enabled(self, enabled: bool):
        self._enabled = bool(enabled)
        self.configure(cursor="hand2" if enabled else "arrow")
        self._redraw()


def _selftest() -> None:
    """Headless ToggleSwitch selftest — no mainloop, withdrawn root."""
    root = tk.Tk()
    root.withdraw()
    apply_theme_global(root)

    fired = []

    def on_cmd(v):
        fired.append(bool(v))

    # Construct on / off / disabled
    tog_on = ToggleSwitch(root, on=True, command=on_cmd, background=PANEL)
    tog_off = ToggleSwitch(root, on=False, command=on_cmd, background=PANEL)
    tog_dis = ToggleSwitch(root, on=True, enabled=False, background=PANEL)

    assert tog_on.get() is True
    assert tog_off.get() is False
    assert tog_dis.get() is True

    # Geometry contract (Setup card / _switch_row)
    assert int(tog_on.cget("width")) == ToggleSwitch._TRACK_W
    assert int(tog_on.cget("height")) == ToggleSwitch._TRACK_H

    # PhotoImage hard-ref present + non-empty
    assert tog_on._photo is not None, "must keep hard PhotoImage ref"
    assert isinstance(tog_on._photo, ImageTk.PhotoImage)
    assert tog_on._photo.width() == ToggleSwitch._TRACK_W
    assert tog_on._photo.height() == ToggleSwitch._TRACK_H

    # set() toggles + fires command only on change
    fired.clear()
    tog_off.set(True, fire=True)
    assert tog_off.get() is True
    assert fired == [True]
    tog_off.set(True, fire=True)  # no-op
    assert fired == [True]
    tog_off.set(False, fire=False)
    assert tog_off.get() is False
    assert fired == [True]  # fire=False

    # set_enabled
    tog_dis.set_enabled(True)
    assert tog_dis._enabled is True
    tog_dis.set_enabled(False)
    assert tog_dis._enabled is False

    # PIL path: supersampled render produces AA partial-alpha before bg bake
    # (inspect raw _render_rgba output)
    raw = tog_on._render_rgba()
    assert raw.size == (ToggleSwitch._TRACK_W, ToggleSwitch._TRACK_H)
    partial = 0
    for yy in range(raw.height):
        for xx in range(raw.width):
            a = raw.getpixel((xx, yy))[3]
            if 1 <= a <= 254:
                partial += 1
    assert partial > 0, "pill edges must be anti-aliased (partial alpha)"

    # ON vs OFF images differ
    tog_a = ToggleSwitch(root, on=True, background=PANEL)
    tog_b = ToggleSwitch(root, on=False, background=PANEL)
    assert tog_a._render_rgba().tobytes() != tog_b._render_rgba().tobytes()

    # SS factor is 4 (same discipline as sprites)
    assert ToggleSwitch._SS == 4

    # F7 hardening: a truly-invalid background= must NOT crash Canvas.__init__.
    # Each of these raises TclError from Tk's colour parser at construction if
    # passed through raw; the validate-and-fall-back-to-PANEL guard must absorb
    # them, and the resulting widget must have a REAL (paintable) background.
    _panel_rgb = root.winfo_rgb(PANEL)
    for _bad in ("", "#ffffff00", "rgb(1,2,3)", "xkcd:blue", "not a color", 12345):
        try:
            _t = ToggleSwitch(root, on=True, background=_bad)
        except tk.TclError as e:
            raise AssertionError(
                "invalid background=%r crashed construction: %s" % (_bad, e))
        # Fell back to PANEL (a valid colour Tk can resolve + we painted with).
        assert root.winfo_rgb(_t.cget("background")) == _panel_rgb, (
            "invalid background=%r did not fall back to PANEL" % (_bad,))
        assert _t._photo is not None, "hardened toggle must still render"
    # A VALID non-default colour must be honoured (guard isn't over-eager).
    _t_ok = ToggleSwitch(root, on=True, background="#123456")
    assert root.winfo_rgb(_t_ok.cget("background")) == root.winfo_rgb("#123456"), \
        "valid background= must be preserved, not overwritten by the guard"

    root.destroy()
    print("SELFTEST OK")
    print("SELFTEST PASS  parakit_practice_widgets  ToggleSwitch (PIL 4x SS)")


# ---------------------------------------------------------------------------
# Standalone smoke host -- proves every widget constructs + renders under
# apply_theme_global with hooks=None-equivalent (this module has no hooks
# seam of its own; Home/Tab own that).
# ---------------------------------------------------------------------------
def _smoke_host() -> None:
    root = tk.Tk()
    root.title("parakit_practice_widgets -- smoke host")
    root.geometry("520x420")
    apply_theme_global(root)

    frame = ttk.Frame(root, style="Prac.TFrame", padding=16)
    frame.pack(fill=tk.BOTH, expand=True)

    hdr = ttk.Label(frame, text="Practice widget kit smoke host",
                    style="Prac.Header.TLabel")
    hdr.pack(anchor=tk.W, pady=(0, 4))
    Tooltip(hdr, "Border-aware tooltip: hover near a screen edge and it "
                "should flip/clamp instead of opening off-screen.")

    row1 = ttk.Frame(frame, style="Prac.TFrame")
    row1.pack(fill=tk.X, pady=6)
    chip = Chip(row1, "Auto-kick", accent=CYAN, on=True,
               tooltip="Chip: coloured-outline toggle.")
    chip.pack(side=tk.LEFT, padx=(0, 8))
    ob = OutlineButton(row1, "Kit Studio...", accent=PURPLE_EDGE,
                       command=lambda: print("OutlineButton clicked"),
                       tooltip="OutlineButton: momentary action.")
    ob.pack(side=tk.LEFT, padx=(0, 8))
    filled = OutlineButton(row1, "Open folder…", filled=True,
                           command=lambda: print("filled OutlineButton"),
                           tooltip="OutlineButton filled=True.")
    filled.pack(side=tk.LEFT, padx=(0, 8))
    seg = Segmented(row1, ["Easy", "Medium", "Hard", "Expert"],
                    command=lambda i: print("Segmented ->", i))
    seg.pack(side=tk.LEFT, padx=(0, 8))

    row2 = ttk.Frame(frame, style="Prac.TFrame")
    row2.pack(fill=tk.X, pady=6)
    pe = PlaceholderEntry(row2, placeholder="Search songs...",
                          tooltip="PlaceholderEntry", width=24)
    pe.pack(side=tk.LEFT, padx=(0, 8))
    tog_on = ToggleSwitch(row2, on=True,
                          command=lambda v: print("ToggleSwitch(on) ->", v),
                          tooltip="ToggleSwitch: on state")
    tog_on.pack(side=tk.LEFT, padx=(0, 8))
    tog_off = ToggleSwitch(row2, on=False,
                           command=lambda v: print("ToggleSwitch(off) ->", v),
                           tooltip="ToggleSwitch: off state")
    tog_off.pack(side=tk.LEFT)

    row3 = ttk.Frame(frame, style="Prac.TFrame")
    row3.pack(fill=tk.X, pady=8)
    gb = GradientButton(row3, "▶ Play the demo groove",
                        command=lambda: print("GradientButton demo"),
                        stops=("#7C3AED", "#A044E3", "#ff69b4"), height=36)
    gb.pack(side=tk.LEFT, padx=(0, 8))
    gb2 = GradientButton(row3, "▶ Play Loaded Song",
                         command=lambda: print("GradientButton loaded"),
                         stops=("#8B4DF7", "#7A3BE8", "#6D2BD9"), height=36)
    gb2.set_enabled(False)
    gb2.pack(side=tk.LEFT)

    status = ttk.Label(frame, text="All widgets constructed OK.",
                       style="Prac.PanelMuted.TLabel")
    status.pack(anchor=tk.W, pady=(12, 0))

    root.mainloop()


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        _smoke_host()
