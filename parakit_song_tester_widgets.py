"""parakit_song_tester_widgets.py -- the two canvas widgets for the rebuilt Song Tester tab.

GradientBar  : the score meter (one PhotoImage gradient, single create_image, end-cap).
SyncTimeline : the horizontal per-section drift view (dashed midline, signed drift bars,
               tick lanes for MIDI notes and real audio onsets).

Assembled 2026-09-03 from two independently built and harness-verified scratch files:
  fleet_out/st_gradient_bar_grok.py    (grok,  7/7 checks PASS)
  fleet_out/st_sync_timeline_codex.py  (codex, 12/12 checks PASS)
Their harnesses and implementation reports stay in those files; this module is the
product. Both classes are UI-thread only and copy the lifecycle idiom of the app's
existing loader bars (_suppress_configure, named <Destroy>/<Configure> handlers).
Both must be listed in the host's mousewheel isinstance tuple (they are, via
_ST_CANVAS_CLASSES) -- a tk.Canvas used as a widget steals scroll otherwise.
Plan: PLAN_song_tester_v5_look_2026-09-03.md rev 4, sections 3-4.
"""
from __future__ import annotations

# ============================================================================
# SyncTimeline (codex)
# ============================================================================
"""Tkinter horizontal sync timeline for the Song Tester tab."""


import tkinter as tk

try:
    from parakit_spectral_tab import (
        PURPLE_LT,
        CYAN,
        PANEL,
        CANVAS_BG,
        PURPLE_EDGE,
        GREEN,
        AMBER,
        MUTED,
        TEXT,
    )
except ImportError:
    PURPLE_LT = "#b388ff"
    CYAN = "#00d4d4"
    PANEL = "#15152a"
    CANVAS_BG = "#070710"
    PURPLE_EDGE = "#8b5cf6"
    GREEN = "#00c853"
    AMBER = "#e09a3a"
    MUTED = "#7e7e96"
    TEXT = "#e0e0e0"

MAGENTA_TICK = "#ff5cf0"
ERR_RED = "#e94560"

LEGEND = (
    ("OK", GREEN),
    ("Drift", AMBER),
    ("Bad", ERR_RED),
    ("MIDI notes", MAGENTA_TICK),
    ("Audio onsets", CYAN),
)


def _blend_hex(foreground: str, background: str, alpha: float) -> str:
    """Blend two #RRGGBB colors and return an opaque Tk-compatible color."""
    fg = tuple(int(foreground[i : i + 2], 16) for i in (1, 3, 5))
    bg = tuple(int(background[i : i + 2], 16) for i in (1, 3, 5))
    rgb = tuple(round(alpha * f + (1.0 - alpha) * b) for f, b in zip(fg, bg))
    return "#{:02x}{:02x}{:02x}".format(*rgb)


_STATUS_COLORS = {"✓": GREEN, "⚠": AMBER, "✗": ERR_RED}
# Tk Canvas has no alpha fills, so cache each 8% status tint blended over CANVAS_BG.
_STATUS_TINTS = {
    symbol: _blend_hex(color, CANVAS_BG, 0.08)
    for symbol, color in _STATUS_COLORS.items()
}
_MIDLINE = _blend_hex(PURPLE_EDGE, CANVAS_BG, 0.38)
_DIVIDER = _blend_hex(PURPLE_EDGE, CANVAS_BG, 0.22)


class SyncTimeline(tk.Canvas):
    """Horizontal per-section drift view with real MIDI and audio tick lanes."""

    _MIN_HEIGHT = 184
    _CONFIGURE_DELAY_MS = 60

    def __init__(self, parent, **kw):
        try:
            requested_height = int(kw.get("height", self._MIN_HEIGHT))
        except (TypeError, ValueError):
            requested_height = self._MIN_HEIGHT
        kw["height"] = max(self._MIN_HEIGHT, requested_height)
        kw.setdefault("bg", CANVAS_BG)
        kw.setdefault("highlightthickness", 0)
        kw.setdefault("bd", 0)
        super().__init__(parent, **kw)

        self._suppress_configure = False
        self._configure_after_id = None
        self._map_after_id = None
        self._note_events = ()
        self._audio_onsets = ()
        self._offset = 0.0
        self._section_results = ()
        self._audio_duration = 0.0
        self._source_label = "MIDI"
        self._song_len = 1.0
        self._has_data = False
        self._placeholder_text = None

        self.bind("<Destroy>", self._timeline_on_destroy)
        self.bind("<Configure>", self._timeline_on_configure)
        self.bind("<Map>", self._timeline_on_map)

    def draw(
        self,
        note_events,
        audio_onsets,
        offset,
        section_results,
        audio_duration,
        source_label="MIDI",
    ) -> None:
        """Draw per-section drift and real event lanes for one analysis result."""
        # Never truth-test these: the host hands audio_onsets over as a numpy array,
        # and `arr or ()` raises. _as_tuple converts anything iterable, else ().
        self._note_events = _as_tuple(note_events)
        self._audio_onsets = _as_tuple(audio_onsets)
        self._offset = float(offset)
        self._section_results = _as_tuple(section_results)
        self._audio_duration = float(audio_duration)
        self._source_label = str(source_label)

        last_note = (
            max(float(t) for t in self._note_events) + self._offset
            if self._note_events
            else 0.0
        )
        last_onset = (
            max(float(t) for t in self._audio_onsets)
            if self._audio_onsets
            else 0.0
        )
        last_section_end = max(
            (e for e in (_section_end(s) for s in self._section_results) if e is not None),
            default=0.0,
        )
        self._song_len = max(
            self._audio_duration,
            last_note,
            last_onset,
            last_section_end,
            1.0,
        )
        self._has_data = True
        self._placeholder_text = None
        self._redraw()

    def set_placeholder(self, text: str) -> None:
        """Clear the timeline and center the supplied placeholder text."""
        self._note_events = ()
        self._audio_onsets = ()
        self._section_results = ()
        self._has_data = False
        self._placeholder_text = str(text)
        self._redraw()

    def clear(self) -> None:
        """Remove timeline data and all canvas items."""
        self._note_events = ()
        self._audio_onsets = ()
        self._section_results = ()
        self._has_data = False
        self._placeholder_text = None
        self.delete("all")

    def _timeline_on_configure(self, _event) -> None:
        if self._suppress_configure:
            return
        if self._configure_after_id is not None:
            try:
                self.after_cancel(self._configure_after_id)
            except tk.TclError:
                pass
        self._configure_after_id = self.after(
            self._CONFIGURE_DELAY_MS,
            self._timeline_redraw_after_configure,
        )

    def _timeline_redraw_after_configure(self) -> None:
        self._configure_after_id = None
        self._redraw()

    def _timeline_on_map(self, _event) -> None:
        if self._map_after_id is not None:
            try:
                self.after_cancel(self._map_after_id)
            except tk.TclError:
                pass
        self._map_after_id = self.after_idle(self._timeline_redraw_after_map)

    def _timeline_redraw_after_map(self) -> None:
        self._map_after_id = None
        self._redraw()

    def _timeline_on_destroy(self, event) -> None:
        if event.widget is not self:
            return
        for attr in ("_configure_after_id", "_map_after_id"):
            after_id = getattr(self, attr)
            if after_id is not None:
                try:
                    self.after_cancel(after_id)
                except tk.TclError:
                    pass
                setattr(self, attr, None)

    def _redraw(self) -> None:
        try:
            width = int(self.winfo_width())
            height = max(self._MIN_HEIGHT, int(self.winfo_height()))
        except tk.TclError:
            return
        if width <= 1:
            return

        self.delete("all")
        if not self._has_data:
            if self._placeholder_text:
                self.create_text(
                    width / 2.0,
                    height / 2.0,
                    text=self._placeholder_text,
                    fill=MUTED,
                    font=("Segoe UI", 9),
                    justify="center",
                    width=max(40, width - 32),
                    tags=("placeholder",),
                )
            return

        pad_l = 8.0
        pad_r = 8.0
        top = 24.0
        bot = float(height - 34)
        mid = (top + bot) / 2.0
        plot_width = width - pad_l - pad_r

        def x_of(time_value):
            return pad_l + (float(time_value) / self._song_len) * plot_width

        self.create_line(
            pad_l,
            mid,
            width - pad_r,
            mid,
            fill=_MIDLINE,
            width=1,
            dash=(4, 4),
            tags=("centerline",),
        )

        for index, section in enumerate(self._section_results):
            try:
                start, end, drift, _count, status_symbol, _color_hex = section
                start, end, drift = float(start), float(end), float(drift)
            except (ValueError, TypeError, KeyError, IndexError):
                continue   # a malformed section is skipped; it must not abort the draw
            color = _STATUS_COLORS.get(status_symbol, AMBER)
            tint = _STATUS_TINTS.get(status_symbol, _STATUS_TINTS["⚠"])
            x0 = x_of(start)
            x1 = x_of(end)

            self.create_rectangle(
                x0,
                top,
                max(x0 + 1.0, x1 - 1.0),
                bot,
                fill=tint,
                outline="",
                tags=("section_tint", f"section_{index}"),
            )

            magnitude = min(abs(drift), 1.8) / 1.8
            bar_height = max(3.0, magnitude * max(0.0, bot - mid - 6.0))
            bar_x = x0 + 3.0
            bar_width = max(6.0, (x1 - x0) - 7.0)
            bar_y = mid if drift >= 0.0 else mid - bar_height
            self.create_rectangle(
                bar_x,
                bar_y,
                bar_x + bar_width,
                bar_y + bar_height,
                fill=color,
                outline="",
                tags=("drift_bar", f"section_{index}"),
            )

            sign = "+" if drift >= 0.0 else ""
            self.create_text(
                (x0 + x1) / 2.0,
                bot + 2.0,
                text=f"{sign}{drift:.2f}s",
                fill=TEXT,
                font=("Consolas", 8),
                anchor="n",
                tags=("drift_label", f"section_{index}"),
            )

            if index > 0:
                self.create_line(
                    x0,
                    top,
                    x0,
                    bot,
                    fill=_DIVIDER,
                    width=1,
                    tags=("section_divider",),
                )

        for note_time in self._note_events:
            x = x_of(float(note_time) + self._offset)
            if pad_l <= x <= width - pad_r:
                self.create_line(
                    x,
                    top,
                    x,
                    top + 10.0,
                    fill=MAGENTA_TICK,
                    width=1.2,
                    tags=("midi_tick",),
                )

        for onset_time in self._audio_onsets:
            x = x_of(onset_time)
            if pad_l <= x <= width - pad_r:
                self.create_line(
                    x,
                    bot - 10.0,
                    x,
                    bot,
                    fill=CYAN,
                    width=1.2,
                    tags=("onset_tick",),
                )

        self.create_text(
            pad_l,
            height - 4,
            text="0:00",
            fill=MUTED,
            font=("Consolas", 8),
            anchor="sw",
            tags=("axis_label", "axis_start"),
        )
        whole_seconds = int(self._song_len)
        end_label = f"{whole_seconds // 60}:{whole_seconds % 60:02d}"
        self.create_text(
            width - pad_r,
            height - 4,
            text=end_label,
            fill=MUTED,
            font=("Consolas", 8),
            anchor="se",
            tags=("axis_label", "axis_end"),
        )


def _as_tuple(value):
    """Result values may be lists, tuples, numpy arrays or None. Convert; never
    truth-test (an ndarray's truth value is ambiguous and raises)."""
    if value is None:
        return ()
    try:
        return tuple(value)
    except TypeError:
        return ()


def _section_end(section):
    """End time of a (start, end, drift, count, symbol, color) section, or None if the
    section is not shaped like that -- a bad section must not abort the whole draw."""
    try:
        return float(section[1])
    except (TypeError, KeyError, IndexError, ValueError):
        return None


# ============================================================================
# GradientBar (grok) -- score meter. One PhotoImage gradient placed with a
# single create_image; fill = crop width. UI-thread only.
# ============================================================================


import sys
import time
import tkinter as tk

from PIL import Image, ImageDraw, ImageTk

# ---------------------------------------------------------------------------
# Palette — stub §1 / plan §1. Import the Spectral sidecar constants; fall
# back to the same literals so this file runs standalone (any cwd).
# ---------------------------------------------------------------------------
try:
    from parakit_spectral_tab import (  # type: ignore
        PURPLE_LT, CYAN, PANEL, CANVAS_BG, PURPLE_EDGE, GREEN, AMBER, MUTED, TEXT,
    )
except Exception:
    PURPLE_LT = "#b388ff"
    CYAN = "#00d4d4"
    PANEL = "#15152a"
    CANVAS_BG = "#070710"
    PURPLE_EDGE = "#8b5cf6"
    GREEN = "#00c853"
    AMBER = "#e09a3a"
    MUTED = "#7e7e96"
    TEXT = "#e0e0e0"

MAGENTA_TICK = "#ff5cf0"   # MIDI-note tick (prototype-native)
SCORE_CAP = "#f2fff9"      # score-bar end-cap (prototype-native)
ERR_RED = "#e94560"        # this tab's existing error red
ST_ANIMATE = True          # module constant; host may set from config key "st_animate"

_CFG_DEBOUNCE_MS = 60
_TWEEN_DT_MS = 16
_LABEL_H = 16              # canvas strip above the 10-px trough for set_label
_CAP_W = 3
_FILL_INSET = 1


def _hex_rgb(color: str) -> tuple[int, int, int]:
    s = str(color).strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) < 6:
        return (0, 0, 0)
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def _render_track(width: int, height: int, fill_hex: str, edge_hex: str) -> Image.Image:
    """Procedural rounded trough (same idea as `_neon_render_trough`).

    RGBA image at exactly (width, height). Dark fill + 1-px purple edge.
    No magenta glow — the prototype bar is inset + border, not the neon loader.
    """
    width = max(int(width), 8)
    height = max(int(height), 4)
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    radius = max(height // 2, 1)
    draw.rounded_rectangle(
        (0, 0, width - 1, height - 1),
        radius=radius,
        fill=fill_hex,
        outline=edge_hex,
        width=1,
    )
    return img


def _render_fill_strip(width: int, height: int, stops: tuple) -> Image.Image:
    """Full-width CYAN→GREEN (or N-stop) strip with rounded-end alpha mask."""
    width = max(int(width), 1)
    height = max(int(height), 1)
    if not stops:
        stops = (CYAN, GREEN)
    rgbs = [_hex_rgb(s) for s in stops]
    n = len(rgbs)
    row = Image.new("RGB", (width, 1))
    px = row.load()
    denom = max(width - 1, 1)
    for x in range(width):
        if n == 1:
            rgb = rgbs[0]
        else:
            t = x / denom
            seg = t * (n - 1)
            i = min(int(seg), n - 2)
            f = seg - i
            a, b = rgbs[i], rgbs[i + 1]
            rgb = (
                int(a[0] + (b[0] - a[0]) * f),
                int(a[1] + (b[1] - a[1]) * f),
                int(a[2] + (b[2] - a[2]) * f),
            )
        px[x, 0] = rgb
    strip = row.resize((width, height), Image.BILINEAR).convert("RGBA")
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, width - 1, height - 1),
        radius=max(height // 2, 1),
        fill=255,
    )
    strip.putalpha(mask)
    return strip


def _ease_css(t: float) -> float:
    """Approximate CSS `ease` (cubic-bezier(0.25, 0.1, 0.25, 1))."""
    t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
    # cubic-bezier y for this curve is close to ease-out cubic over most of [0,1]
    return 1.0 - (1.0 - t) ** 3


class GradientBar(tk.Canvas):
    """Score meter. One PhotoImage gradient placed with a single create_image; fill = crop width.
    UI-thread only. Lifecycle copies ParaKit v4.0.py:5387-5398 (_suppress_configure, named
    <Destroy>/<Configure> handlers)."""

    def __init__(self, parent, *, height=10, stops=(CYAN, GREEN), track=CANVAS_BG,
                 edge=PURPLE_EDGE, cap=SCORE_CAP, animate_ms=500, **kw):
        self._bar_h = max(int(height), 4)
        self._label_h = _LABEL_H
        self._stops = tuple(stops) if stops else (CYAN, GREEN)
        self._track_color = track
        self._edge_color = edge
        self._cap_color = cap
        self._animate_ms = max(int(animate_ms), 1)

        kw.setdefault("highlightthickness", 0)
        kw.setdefault("bd", 0)
        kw.setdefault("relief", "flat")
        kw.setdefault("takefocus", 0)
        kw.setdefault("bg", PANEL)
        kw.setdefault("width", 400)
        # Constructor `height` is the 10-px trough. The canvas is taller by
        # `_LABEL_H` so `set_label` can sit above the bar without becoming a
        # Frame (frozen signature is `GradientBar(tk.Canvas)`).
        kw["height"] = self._bar_h + self._label_h

        super().__init__(parent, **kw)

        try:
            self._width = max(int(float(str(self.cget("width")))), 8)
        except (TypeError, ValueError, tk.TclError):
            self._width = 400

        self._target = 0.0
        self._displayed = 0.0
        self._label_text = ""
        self._scored = False
        self._pending_map_apply = False

        self._suppress_configure = False
        self._tween_id = None
        self._cfg_after = None
        self._tween_from = 0.0
        self._tween_t0 = 0.0
        self._paint_n = 0  # harness: count fill paints (rapid-set fight detector)

        self._track_photo = None
        self._fill_pil = None
        self._fill_photo = None
        self._track_item = None
        self._fill_item = None
        self._cap_item = None
        self._label_item = None

        try:
            self._rebuild()
        except Exception:
            self.delete("all")

        self.bind("<Destroy>", self._gbar_on_destroy)
        self.bind("<Configure>", self._gbar_on_configure)
        self.bind("<Map>", self._gbar_on_map)

    # -- public API (frozen stub §1) ----------------------------------------

    def set(self, fraction: float, animate: bool = True) -> None:
        """0..1. after_cancel()s any running tween first. If unmapped (winfo_width() <= 1),
        store the target and apply without animation on <Map>. Honors ST_ANIMATE."""
        self._cancel_tween()
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        try:
            f = float(fraction)
        except (TypeError, ValueError):
            f = 0.0
        if f < 0.0:
            f = 0.0
        elif f > 1.0:
            f = 1.0
        self._target = f

        if self._is_unmapped():
            # Store only. Snap on <Map> with no tween (stub + plan §3).
            self._pending_map_apply = True
            return

        self._pending_map_apply = False
        do_anim = bool(animate) and bool(ST_ANIMATE)
        if (not do_anim) or abs(self._displayed - self._target) < 1e-9:
            self._displayed = self._target
            self._paint_fill()
            self._paint_cap()
            return

        self._tween_from = self._displayed
        self._tween_t0 = time.perf_counter()
        self._gbar_tween()

    def set_label(self, text: str) -> None:
        """Small right-aligned label above the bar (e.g. '6 of 8 sections within 0.15 s drift')."""
        self._label_text = "" if text is None else str(text)
        self._paint_label()

    def mark_scored(self, on: bool) -> None:
        """Show/hide the 3-px end-cap at the fill edge."""
        self._scored = bool(on)
        self._paint_cap()

    def reset(self) -> None:
        """Instant: fraction 0, label '', cap hidden, tween cancelled."""
        self._cancel_tween()
        self._pending_map_apply = False
        self._target = 0.0
        self._displayed = 0.0
        self._label_text = ""
        self._scored = False
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        self._paint_fill()
        self._paint_cap()
        self._paint_label()

    # -- lifecycle ----------------------------------------------------------

    def _is_unmapped(self) -> bool:
        """Unmapped: `winfo_width() <= 1` (stub) OR not ismapped (withdraw)."""
        try:
            if int(self.winfo_width()) <= 1:
                return True
            if not self.winfo_ismapped():
                return True
        except tk.TclError:
            return True
        return False

    def _cancel_tween(self) -> None:
        if self._tween_id is not None:
            try:
                self.after_cancel(self._tween_id)
            except Exception:
                pass
            self._tween_id = None

    def _cancel_cfg(self) -> None:
        if self._cfg_after is not None:
            try:
                self.after_cancel(self._cfg_after)
            except Exception:
                pass
            self._cfg_after = None

    def _cancel_all_afters(self) -> None:
        self._cancel_tween()
        self._cancel_cfg()

    def _gbar_on_destroy(self, _event=None) -> None:
        self._cancel_all_afters()

    def _gbar_on_configure(self, event) -> None:
        if self._suppress_configure:
            return
        if event.widget is not self:
            return
        self._cancel_cfg()
        self._cfg_after = self.after(_CFG_DEBOUNCE_MS, self._gbar_apply_configure)

    def _gbar_apply_configure(self) -> None:
        self._cfg_after = None
        try:
            if not self.winfo_exists():
                return
            new_w = int(self.winfo_width())
        except tk.TclError:
            return
        if new_w <= 1:
            return
        if abs(new_w - self._width) < 2 and self._fill_pil is not None:
            self._paint_label()
            self._paint_cap()
            return
        self._width = new_w
        try:
            self._rebuild()
        except Exception:
            try:
                self.delete("all")
            except tk.TclError:
                pass

    def _gbar_on_map(self, _event=None) -> None:
        if not self._pending_map_apply:
            return
        self._pending_map_apply = False
        self._cancel_tween()
        self._displayed = self._target
        try:
            w = int(self.winfo_width())
        except tk.TclError:
            return
        if w > 1:
            self._width = w
            try:
                self._rebuild()
            except Exception:
                self._paint_fill()
                self._paint_cap()
        else:
            self._paint_fill()
            self._paint_cap()

    def _gbar_tween(self) -> None:
        self._tween_id = None
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        elapsed_ms = (time.perf_counter() - self._tween_t0) * 1000.0
        dur = float(self._animate_ms)
        t = elapsed_ms / dur if dur > 0 else 1.0
        if t >= 1.0:
            self._displayed = self._target
            self._paint_fill()
            self._paint_cap()
            return
        eased = _ease_css(t)
        self._displayed = self._tween_from + (self._target - self._tween_from) * eased
        self._paint_fill()
        self._paint_cap()
        try:
            self._tween_id = self.after(_TWEEN_DT_MS, self._gbar_tween)
        except tk.TclError:
            self._tween_id = None

    # -- drawing ------------------------------------------------------------

    def _inner_wh(self) -> tuple[int, int]:
        inner_w = max(self._width - 2 * _FILL_INSET, 1)
        inner_h = max(self._bar_h - 2 * _FILL_INSET, 1)
        return inner_w, inner_h

    def _crop_px(self) -> int:
        inner_w, _ = self._inner_wh()
        px = int(round(self._displayed * inner_w))
        if px < 0:
            return 0
        if px > inner_w:
            return inner_w
        return px

    def _rebuild(self) -> None:
        inner_w, inner_h = self._inner_wh()
        track = _render_track(self._width, self._bar_h, self._track_color, self._edge_color)
        self._fill_pil = _render_fill_strip(inner_w, inner_h, self._stops)

        self._suppress_configure = True
        try:
            self.config(height=self._bar_h + self._label_h)
        except tk.TclError:
            pass
        finally:
            self._suppress_configure = False

        self.delete("all")
        self._track_item = None
        self._fill_item = None
        self._cap_item = None
        self._label_item = None

        self._track_photo = ImageTk.PhotoImage(track)
        self._track_item = self.create_image(
            0, self._label_h, image=self._track_photo, anchor="nw", tags=("track",),
        )
        # Single fill image item — updated in place via itemconfigure.
        self._fill_item = self.create_image(
            _FILL_INSET, self._label_h + _FILL_INSET,
            image=self._track_photo,  # placeholder; _paint_fill replaces
            anchor="nw", tags=("fill",),
        )
        y0 = self._label_h + _FILL_INSET
        y1 = y0 + inner_h
        self._cap_item = self.create_rectangle(
            0, y0, _CAP_W, y1,
            fill=self._cap_color, outline="", width=0, tags=("cap",),
        )
        self._label_item = self.create_text(
            max(self._width - 7, 1), 2,
            text="",
            fill=MUTED,
            font=("Segoe UI", 9),
            anchor="ne",
            tags=("label",),
        )
        self._paint_fill()
        self._paint_cap()
        self._paint_label()

    def _paint_fill(self) -> None:
        self._paint_n += 1
        if self._fill_item is None or self._fill_pil is None:
            return
        crop_w = self._crop_px()
        try:
            if crop_w <= 0:
                self.itemconfigure(self._fill_item, state="hidden")
                return
            cropped = self._fill_pil.crop((0, 0, crop_w, self._fill_pil.size[1]))
            self._fill_photo = ImageTk.PhotoImage(cropped)
            self.itemconfigure(
                self._fill_item, image=self._fill_photo, state="normal",
            )
            self.coords(self._fill_item, _FILL_INSET, self._label_h + _FILL_INSET)
        except tk.TclError:
            return

    def _paint_cap(self) -> None:
        if self._cap_item is None:
            return
        crop_w = self._crop_px()
        show = self._scored and crop_w > 0
        try:
            if not show:
                self.itemconfigure(self._cap_item, state="hidden")
                return
            x1 = _FILL_INSET + crop_w
            x0 = x1 - _CAP_W
            if x0 < _FILL_INSET:
                x0 = _FILL_INSET
            inner_w, inner_h = self._inner_wh()
            y0 = self._label_h + _FILL_INSET
            y1 = y0 + inner_h
            self.coords(self._cap_item, x0, y0, x1, y1)
            self.itemconfigure(self._cap_item, state="normal")
            self.tag_raise(self._cap_item)
        except tk.TclError:
            return

    def _paint_label(self) -> None:
        if self._label_item is None:
            return
        try:
            self.coords(self._label_item, max(self._width - 7, 1), 2)
            text = self._label_text
            self.itemconfigure(
                self._label_item,
                text=text,
                state=("hidden" if not text else "normal"),
            )
        except tk.TclError:
            return


# ---------------------------------------------------------------------------
# Acceptance harness — stub §1. Bare tk.Tk(), after+quit, no manual close.
# ---------------------------------------------------------------------------

def _harness() -> int:
    root = tk.Tk()
    root.title("GradientBar harness")
    root.geometry("480x140+40+40")
    root.configure(bg=PANEL)

    results: list[tuple[str, bool]] = []
    finished = {"done": False}

    bar = GradientBar(root)
    bar.pack(fill="x", padx=24, pady=(28, 16))

    def report(name: str, ok: bool, detail: str = "") -> None:
        line = f"{'PASS' if ok else 'FAIL'} {name}"
        if detail:
            line += f" — {detail}"
        print(line, flush=True)
        results.append((name, bool(ok)))

    def finish(code_if_empty: int = 1) -> None:
        if finished["done"]:
            return
        finished["done"] = True
        try:
            root.quit()
        except tk.TclError:
            pass

    def timeout() -> None:
        if not finished["done"]:
            print("FAIL harness timeout", flush=True)
            results.append(("harness timeout", False))
            finish()

    def step_mapped_then(fn) -> None:
        root.update()
        root.after(80, fn)

    def check_anim_start() -> None:
        try:
            globals()["ST_ANIMATE"] = True
            bar.reset()
            bar.set(0.0, animate=False)
            root.update_idletasks()
            bar.set(1.0, animate=True)
            root.after(120, check_anim_mid)
        except Exception as exc:
            report("set 0→1 animates", False, repr(exc))
            root.after(10, check_rapid)

    def check_anim_mid() -> None:
        try:
            mid = bar._displayed
            moving = bar._tween_id is not None
            ok = moving and (0.04 < mid < 0.96)
            report(
                "set 0→1 animates",
                ok,
                f"displayed={mid:.3f} tween={'on' if moving else 'off'} at ~120ms",
            )
            root.after(bar._animate_ms + 80, check_anim_end)
        except Exception as exc:
            report("set 0→1 animates", False, repr(exc))
            root.after(10, check_rapid)

    def check_anim_end() -> None:
        try:
            end = bar._displayed
            idle = bar._tween_id is None
            ok = idle and end >= 0.98
            if not ok:
                report(
                    "set 0→1 animates (settled)",
                    False,
                    f"displayed={end:.3f} tween={'on' if not idle else 'off'}",
                )
            # one-image check (hard rule)
            fill_ids = list(bar.find_withtag("fill"))
            rects = [
                i for i in bar.find_all()
                if bar.type(i) == "rectangle"
            ]
            ok_img = (len(fill_ids) == 1) and (len(rects) <= 1)
            report(
                "fill is one PhotoImage item",
                ok_img,
                f"fill_items={len(fill_ids)} rectangles={len(rects)}",
            )
        except Exception as exc:
            report("fill is one PhotoImage item", False, repr(exc))
        root.after(10, check_rapid)

    def check_rapid() -> None:
        try:
            globals()["ST_ANIMATE"] = True
            bar.reset()
            bar.set(0.0, animate=False)
            root.update_idletasks()
            paints0 = bar._paint_n
            for frac in (0.15, 0.35, 0.55, 0.75, 0.85):
                bar.set(frac, animate=True)
            target_ok = abs(bar._target - 0.85) < 1e-9
            one_tween = bar._tween_id is not None
            root.after(90, lambda: check_rapid_mid(paints0, target_ok, one_tween))
        except Exception as exc:
            report("five rapid set()s last wins", False, repr(exc))
            root.after(10, check_cap)

    def check_rapid_mid(paints0: int, target_ok: bool, one_tween: bool) -> None:
        paints = bar._paint_n - paints0
        # One tween + the five set()s: well under a fight of 5×(500/16)≈156.
        no_fight = paints < 25
        root.after(
            bar._animate_ms + 80,
            lambda: check_rapid_end(target_ok, one_tween, no_fight, paints),
        )

    def check_rapid_end(target_ok: bool, one_tween: bool, no_fight: bool, paints: int) -> None:
        try:
            settled = abs(bar._displayed - 0.85) < 0.03 and bar._tween_id is None
            ok = target_ok and one_tween and no_fight and settled
            report(
                "five rapid set()s last wins",
                ok,
                f"target_ok={target_ok} had_tween={one_tween} "
                f"paints_90ms={paints} displayed={bar._displayed:.3f}",
            )
        except Exception as exc:
            report("five rapid set()s last wins", False, repr(exc))
        root.after(10, check_cap)

    def check_cap() -> None:
        try:
            globals()["ST_ANIMATE"] = True
            bar.reset()
            bar.set(0.6, animate=False)
            bar.set_label("6 of 8 sections within 0.15 s drift")
            bar.mark_scored(True)
            root.update_idletasks()
            cap_ids = list(bar.find_withtag("cap"))
            state = bar.itemcget(bar._cap_item, "state") if bar._cap_item else "missing"
            coords = bar.coords(bar._cap_item) if bar._cap_item else []
            cap_w = abs(coords[2] - coords[0]) if len(coords) >= 4 else 0.0
            label_txt = bar.itemcget(bar._label_item, "text") if bar._label_item else ""
            ok = (
                len(cap_ids) == 1
                and state != "hidden"
                and abs(cap_w - _CAP_W) < 0.6
                and "6 of 8" in label_txt
            )
            report(
                "mark_scored(True) shows cap",
                ok,
                f"state={state!r} cap_w={cap_w:.1f} label={label_txt!r}",
            )
        except Exception as exc:
            report("mark_scored(True) shows cap", False, repr(exc))
        root.after(10, check_resize)

    def check_resize() -> None:
        try:
            bar.set(0.5, animate=False)
            root.update()
            w1 = int(bar.winfo_width())
            pil_w1 = bar._fill_pil.size[0] if bar._fill_pil is not None else 0
            root.geometry("820x160+40+40")
            root.update()
            root.after(100, lambda: check_resize_after(w1, pil_w1))
        except Exception as exc:
            report("resize redraws at new width", False, repr(exc))
            root.after(10, check_withdrawn)

    def check_resize_after(w1: int, pil_w1: int) -> None:
        try:
            w2 = int(bar.winfo_width())
            pil_w2 = bar._fill_pil.size[0] if bar._fill_pil is not None else 0
            photo_w = bar._fill_photo.width() if bar._fill_photo is not None else 0
            grew = (w2 > w1 + 20) and (pil_w2 > pil_w1 + 20)
            ratio = photo_w / float(max(pil_w2, 1))
            ratio_ok = abs(ratio - 0.5) < 0.08
            report(
                "resize redraws at new width",
                grew and ratio_ok,
                f"w {w1}->{w2} pil {pil_w1}->{pil_w2} crop_ratio={ratio:.3f}",
            )
        except Exception as exc:
            report("resize redraws at new width", False, repr(exc))
        root.after(10, check_withdrawn)

    def check_withdrawn() -> None:
        try:
            globals()["ST_ANIMATE"] = True
            bar.reset()
            bar.set(0.0, animate=False)
            root.update()
            root.withdraw()
            root.update()
            bar.set(0.37, animate=True)
            no_tween = bar._tween_id is None
            pending = bar._pending_map_apply
            still_zero = abs(bar._displayed - 0.0) < 1e-9
            root.deiconify()
            root.update()
            root.after(60, lambda: check_withdrawn_after(no_tween, pending, still_zero))
        except Exception as exc:
            report("set() while withdrawn then deiconify() no tween", False, repr(exc))
            root.after(10, check_st_animate)

    def check_withdrawn_after(no_tween: bool, pending: bool, still_zero: bool) -> None:
        try:
            snapped = abs(bar._displayed - 0.37) < 1e-9
            idle = bar._tween_id is None
            ok = no_tween and pending and still_zero and snapped and idle
            report(
                "set() while withdrawn then deiconify() no tween",
                ok,
                f"stored_unmapped={no_tween and pending and still_zero} "
                f"snapped={snapped} idle={idle} displayed={bar._displayed:.3f}",
            )
        except Exception as exc:
            report("set() while withdrawn then deiconify() no tween", False, repr(exc))
        root.after(10, check_st_animate)

    def check_st_animate() -> None:
        try:
            globals()["ST_ANIMATE"] = False
            bar.reset()
            bar.set(0.65, animate=True)
            root.update_idletasks()
            ok = abs(bar._displayed - 0.65) < 1e-9 and bar._tween_id is None
            report(
                "ST_ANIMATE = False is instant",
                ok,
                f"displayed={bar._displayed:.3f} tween_id={bar._tween_id!r}",
            )
        except Exception as exc:
            report("ST_ANIMATE = False is instant", False, repr(exc))
        finally:
            globals()["ST_ANIMATE"] = True
            finish()

    root.after(80, check_anim_start)
    root.after(8000, timeout)
    root.mainloop()
    try:
        root.destroy()
    except tk.TclError:
        pass

    if not results:
        print("FAIL harness produced no checks", flush=True)
        return 1
    return 0 if all(ok for _name, ok in results) else 1


if __name__ == "__main__":   # `py -3.12 parakit_song_tester_widgets.py` runs the GradientBar harness
    raise SystemExit(_harness())
