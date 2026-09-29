"""parakit_practice_home_widgets -- Home-only shell, token and geometry
helpers for the Practice tab's Home screen (4.14.3 redesign, batches B1-B3).

Why a separate module: `parakit_practice_widgets.py` is shared with the Play,
Results and Kit Studio screens, which keep their own dark look and must not
change in this pass (owner decision B, 2026-09-21; build plan
`fleet_out/ASTRA_plan_practice_home_build_r2_2026-09-19.md`, section
"B1 -- shell, topbar, helper and packaging declaration"). Everything here is
used ONLY by `parakit_practice_home.py`. The palette constants are imported
from the shared module with literal fallbacks so this file still imports
standalone (the same try/except shape `parakit_song_tester_tab.py:29-34` uses
against the Spectral sidecar).

Provided:

  - ``LED_OK`` / ``LED_WARN`` / ``LED_OFF`` -- the topbar and Input chip LED
    colors, GREEN/AMBER from the established kit, replacing the one-off
    "#00ff88" / "#ffb347" literals the chips used before B1.
  - ``STACK_BELOW`` -- allocated Home width (px) below which the 58/42 columns
    stack. Measured on Home itself, BEFORE the reserved scrollbar gutter, so
    an appearing scrollbar cannot flip the mode back.
  - ``MIN_ROOT_WIDTH`` / ``MIN_ROOT_HEIGHT`` -- the app's own minimum root
    size (640x760), kept here so geometry fixtures pin one source.
  - ``ViewportFrame`` -- a propagation-disabled viewport whose REQUESTED
    height is explicit and allocation-driven. The document inside (the song
    library) may be arbitrarily tall; it never sizes the page (the geometry
    contract's Song/page feedback prevention).
  - ``compute_viewport_height`` -- the pure allocation-minus-fixed-chrome
    computation, clamped to a positive readable minimum and to the budget.
  - ``measure_fixed_chrome`` -- direct measurement of a card's fixed vertical
    chrome from its packed children, borders and paddings (bypassing the
    lagging aggregate request), returning None on missing card or failure.
  - ``PendingSettlement`` -- one pending after_idle geometry settlement; the
    contract's "recompute only on outer allocation / density / wrapped chrome
    / card membership changes, one pending idle settlement, no nested
    update() calls inside Configure callbacks".
  - Wheel-scope helpers (``install_wheel_scope`` / ``remove_wheel_scope`` /
    ``tag_subtree``) -- Home consumes its own wheel events exactly once
    through a Home-scoped bindtag placed ahead of "all"; the host's global
    scroll router stays registered for every other tab. No bind_all, no
    unbind_all.
  - ``set_wraplength`` -- compare-before-write wraplength updates for
    width-aware hint labels.
  - ``make_host_card`` -- ttk.LabelFrame chrome for a Home card (B2 Song;
    later batches reuse it). Title is the LabelFrame text; the host
    labelframe_title hook arrives with B5.
  - ``make_convert_button`` / ``set_ttk_enabled`` / ``ttk_is_enabled`` --
    the Song-card play pair: standard Convert.TButton with native disabled
    maps, not custom gradient widgets.
  - ``make_setup_switch_row`` / ``make_setup_scale_row`` -- Setup card
    recipes (B3): one switch per row; full-width fall-time and note-size
    scales with a numeric label and reset 1.0. Ranges 0.8-8.0 and 0.5-2.0.

Stdlib + tkinter only; importing this module never creates a Tk root.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

# -- palette: import the shared module's constants so every Practice surface
# reads from one palette; literal fallbacks keep this module importable
# standalone (review hosts, the packaging gate's isolated-import fixture).
try:
    from parakit_practice_widgets import GREEN, AMBER, MUTED
except Exception:                                   # standalone / review host
    GREEN, AMBER, MUTED = "#00c853", "#e09a3a", "#7e7e96"

# Chip LED states (B1: the old "#00ff88"/"#ffb347" literals became the kit's
# GREEN/AMBER; "off" stays the muted gray).
LED_OK = GREEN
LED_WARN = AMBER
LED_OFF = MUTED

# -- geometry contract constants ---------------------------------------------
STACK_BELOW = 1100          # allocated Home width (px); below it, columns stack
MIN_ROOT_WIDTH = 640        # the app's own minimum root size
MIN_ROOT_HEIGHT = 760
COL_LEFT_WEIGHT = 58        # Song column
COL_RIGHT_WEIGHT = 42       # Setup-over-Input column
COL_UNIFORM = "parakit_home_cols"
MIN_VIEWPORT_HEIGHT = 160   # readable minimum for a bounded viewport
WRAP_FLOOR = 180            # never wrap a hint narrower than this
WRAP_INSET = 40             # column width minus inset = label wraplength


def shell_padding(compact: bool) -> int:
    """Outer shell padding by density: 10 px in Compact, 14 px in Roomy.
    Applied at Home construction and on live density changes."""
    return 10 if compact else 14


def compute_viewport_height(body_budget, fixed_chrome,
                            minimum: int = MIN_VIEWPORT_HEIGHT) -> int:
    """Explicit viewport request: outer allocation minus measured fixed chrome.

    ``fixed_chrome`` must be MEASURED chrome (topbar/status, card header,
    actions, search, docked footer) -- never a document's own request. The
    result is clamped to a positive readable minimum and to the budget; when
    the minimum still exceeds what is left the caller records a fit failure
    rather than hiding controls or forcing a zero viewport.
    """
    try:
        budget = int(body_budget)
        chrome = int(fixed_chrome)
    except (TypeError, ValueError):
        return minimum
    if budget <= 0:
        return minimum
    return max(minimum, min(budget, budget - max(0, chrome)))


def _is_unset(val) -> bool:
    """True only for None and empty or whitespace-only strings. Truthiness is
    wrong here because empty sequences must reach _parse_pad and be rejected."""
    if val is None:
        return True
    if isinstance(val, str):
        return not val.strip()
    return False


def _parse_pad(val, kind: str = "pack") -> int:
    """Sum total vertical padding from a pack or ttk padding value.

    For pack: scalar applies top+bottom (2*val); 2-tuple is (top, bottom);
    4-tuple is (left, top, right, bottom) -> top + bottom.
    For ttk: scalar applies to all 4 sides (2*val); 2-tuple is (lr, tb) -> 2*tb;
    3-tuple is (l, t, r) with bottom=top -> 2*t; 4-tuple is (l, t, r, b) -> t+b.
    Raises on any unparsable element or shape (ValueError for a bad length or
    type, whatever int() raises for a bad element); measure_fixed_chrome turns
    the exception into None.
    """
    if val is None:
        return 0
    if isinstance(val, (int, float)):
        return 2 * int(val)
    if isinstance(val, str):
        raw = val.split()
        if not raw:
            return 0
        parts = [int(x) for x in raw]
    elif isinstance(val, (tuple, list)):
        parts = [int(x) for x in val]
    else:
        raise ValueError(f"unsupported padding type: {type(val).__name__}")

    n = len(parts)
    if n == 1:
        return 2 * parts[0]
    if kind == "ttk":
        if n == 2:      # (left_right, top_bottom) -> 2 * top_bottom
            return 2 * parts[1]
        if n == 3:      # (left, top, right) -> top + top
            return 2 * parts[1]
        if n == 4:      # (left, top, right, bottom) -> top + bottom
            return parts[1] + parts[3]
        raise ValueError(f"invalid ttk padding length: {n}")
    else:               # pack
        if n == 2:      # (top, bottom) -> top + bottom
            return parts[0] + parts[1]
        if n == 4:      # (left, top, right, bottom) -> top + bottom
            return parts[1] + parts[3]
        raise ValueError(f"invalid pack padding length: {n}")


def _widget_vchrome(w: tk.Misc) -> int:
    """Vertical border, highlight thickness and internal padding of a widget."""
    total = 0
    for attr in ("highlightthickness", "borderwidth"):
        try:
            val = w.cget(attr)
        except tk.TclError as e:
            if "unknown option" in str(e).lower():
                val = None
            else:
                raise
        if not _is_unset(val):
            total += 2 * int(val)
    try:
        val = w.cget("pady")
    except tk.TclError as e:
        if "unknown option" in str(e).lower():
            val = None
        else:
            raise
    if not _is_unset(val):
        total += 2 * int(val)
    try:
        val = w.cget("padding")
    except tk.TclError as e:
        if "unknown option" in str(e).lower():
            val = None
        else:
            raise
    else:
        if _is_unset(val):
            try:
                style_name = str(w.cget("style") or "")
            except tk.TclError as e:
                if "unknown option" in str(e).lower():
                    style_name = ""
                else:
                    raise
            if not style_name:
                style_name = w.winfo_class()
            val = ttk.Style().lookup(style_name, "padding")
    if not _is_unset(val):
        total += _parse_pad(val, kind="ttk")
    return total


def _pack_pady(w: tk.Misc) -> int:
    """Vertical pack padding (top + bottom) of a widget."""
    info = w.pack_info()
    return _parse_pad(info.get("pady", 0), kind="pack")


def _labelframe_label_chrome(w: tk.Misc) -> int:
    """Extra vertical space a ttk.LabelFrame spends on its title.

    Packed children sit in the interior; the title sits on the border and
    is not in any child's reqheight. Returns 0 when the widget is not a
    LabelFrame or has no title; any Tk error (class, text, style, or font
    query) propagates to measure_fixed_chrome, whose outer handler records
    the failure as None.
    """
    cls = str(w.winfo_class())
    if "labelframe" not in cls.lower():
        return 0
    text = str(w.cget("text") or "")
    if not text.strip():
        return 0
    from tkinter import font as tkfont
    style = ttk.Style()
    font_spec = (style.lookup("TLabelframe.Label", "font")
                 or style.lookup(cls, "font"))
    if font_spec:
        f = tkfont.Font(font=font_spec)
    else:
        f = tkfont.nametofont("TkDefaultFont")
    return int(f.metrics("linespace"))


def measure_fixed_chrome(card: Optional[tk.Misc],
                         viewport: Optional[tk.Misc] = None) -> Optional[int]:
    """Measure the fixed vertical chrome of a card directly from its children.

    Why the aggregate cannot be used: ``card.winfo_reqheight()`` is an
    aggregate recomputed by Tk at idle. In every settlement pass it still
    reflects the viewport request written by the PREVIOUS pass, so computing
    ``fixed = card_req - vp_cache`` is wrong by exactly the last delta,
    causing the viewport sizing to oscillate and settle at an incorrect height.
    Direct summation over the card's packed children (excluding the viewport
    wrapper) plus borders and paddings produces the true chrome independently
    of the viewport request.

    Returns the measured integer height on success, or None if the card is
    missing, any child request cannot be read, or any measurement exception
    occurs. The outer exception handler is the single place that turns a
    propagated measurement failure into None.
    """
    if card is None:
        return None
    try:
        chain = []
        if viewport is not None:
            curr = viewport
            while curr is not None and curr != card:
                chain.append(curr)
                curr = getattr(curr, "master", None)
            if curr == card and chain:
                chain.append(card)
                chain.reverse()   # [card, ..., (pad), (wrapper), viewport]

        wrapper = None
        container = card
        if len(chain) >= 3:
            container = chain[-3]
            wrapper = chain[-2]
        elif len(chain) == 2:
            container = chain[0]
            wrapper = chain[1]

        total = _widget_vchrome(card)
        total += _labelframe_label_chrome(card)
        if chain and len(chain) >= 3:
            # Intermediate containers between card and container (inclusive)
            for w in chain[1:-2]:
                total += _pack_pady(w) + _widget_vchrome(w)

        for child in container.pack_slaves():
            if child == wrapper:
                total += _pack_pady(wrapper) + _widget_vchrome(wrapper)
            else:
                req_h = child.winfo_reqheight()
                total += req_h + _pack_pady(child)

        return max(0, total)
    except Exception:
        return None


class ViewportFrame(tk.Frame):
    """A bounded viewport with geometry propagation disabled.

    The explicit requested size comes from ``set_request`` (allocation-driven,
    computed by the owning screen's settlement), never from the document
    packed inside. Writes are compared against the last written values and
    skipped when unchanged, so a settled viewport produces no further
    Configure traffic.
    """

    def __init__(self, parent, initial_height: int = MIN_VIEWPORT_HEIGHT,
                 **kw):
        super().__init__(parent, **kw)
        self.pack_propagate(False)
        self.grid_propagate(False)
        self._req_w: Optional[int] = None
        self._req_h: Optional[int] = None
        self.set_request(height=initial_height)

    def set_request(self, width: Optional[int] = None,
                    height: Optional[int] = None) -> bool:
        """Write the requested size; returns True when something changed."""
        kw = {}
        if width is not None and width != self._req_w:
            kw["width"] = int(width)
        if height is not None and height != self._req_h:
            kw["height"] = int(height)
        if not kw:
            return False
        try:
            self.configure(**kw)
        except tk.TclError:
            return False   # mid-teardown
        self._req_w = kw.get("width", self._req_w)
        self._req_h = kw.get("height", self._req_h)
        return True

    def requested_height(self) -> int:
        return self._req_h if self._req_h is not None else 0


class PendingSettlement:
    """One pending after_idle geometry settlement.

    Configure callbacks only call ``schedule``; the callback itself runs once
    idle. No update()/update_idletasks() is involved anywhere, per the
    geometry contract. ``cancel`` is for teardown.
    """

    def __init__(self, widget: tk.Misc, callback: Callable[[], None]):
        self._widget = widget
        self._callback = callback
        self._job = None

    def schedule(self) -> None:
        if self._job is not None:
            return
        try:
            self._job = self._widget.after_idle(self._run)
        except tk.TclError:
            self._job = None

    def _run(self) -> None:
        self._job = None
        try:
            self._callback()
        except tk.TclError:
            pass   # destroyed mid-idle

    def cancel(self) -> None:
        job, self._job = self._job, None
        if job is not None:
            try:
                self._widget.after_cancel(job)
            except Exception:
                pass

    @property
    def pending(self) -> bool:
        return self._job is not None


# -- Home-scoped wheel routing (wheel co-tenancy) ------------------------------
#: Private bindtag for Home's wheel events. It is inserted into Home
#: descendants' bindtags directly after the widget's own tag, i.e. BEFORE the
#: class/toplevel/"all" tags; the handler's "break" return then consumes the
#: event exactly once and the host's global router (registered on "all")
#: never re-handles a Home event.
WHEEL_TAG = "ParakitPracticeHomeWheel"
WHEEL_SEQUENCES = ("<MouseWheel>", "<Button-4>", "<Button-5>")


def install_wheel_scope(screen: tk.Misc, handler) -> None:
    """Bind ``handler`` on Home's private bindtag. NOT bind_all: the host's
    global scroll router stays live and unchanged for every other tab."""
    for seq in WHEEL_SEQUENCES:
        screen.bind_class(WHEEL_TAG, seq, handler)


def remove_wheel_scope(screen: tk.Misc) -> None:
    """Remove Home's class-level wheel bindings (teardown). Only the private
    tag is touched; the host's "all" handlers are never unbound."""
    for seq in WHEEL_SEQUENCES:
        try:
            screen.unbind_class(WHEEL_TAG, seq)
        except tk.TclError:
            pass


def tag_subtree(root: tk.Widget) -> None:
    """Insert the wheel bindtag into ``root`` and every descendant's bindtags,
    right after the widget's own tag so it precedes "all". Idempotent; call it
    after building or rebuilding any Home region."""
    stack = [root]
    while stack:
        w = stack.pop()
        try:
            tags = list(w.bindtags())
        except tk.TclError:
            continue
        if WHEEL_TAG not in tags:
            tags.insert(1 if tags else 0, WHEEL_TAG)
            try:
                w.bindtags(tuple(tags))
            except tk.TclError:
                pass
        try:
            stack.extend(w.winfo_children())
        except tk.TclError:
            pass


def set_wraplength(label: tk.Widget, width, floor: int = WRAP_FLOOR) -> bool:
    """Width-aware label wrap: set wraplength to ``width`` (floored), compared
    against the current value (treating unset or unreadable values as -1) and
    skipped when unchanged. Returns True when a write happened."""
    try:
        target = max(floor, int(width))
    except (TypeError, ValueError):
        return False
    try:
        raw = label.cget("wraplength")
    except tk.TclError:
        return False
    try:
        cur = int(raw)
    except (ValueError, TypeError):
        cur = -1
    if cur == target:
        return False
    try:
        label.configure(wraplength=target)
    except tk.TclError:
        return False
    return True


def make_host_card(parent, title: str, padding=(16, 14)):
    """Host-style ttk.LabelFrame for a Home card.

    Matches Song Tester's fallback when labelframe_title is absent
    (lf.configure(text=...)): spaces around the title so the label reads
    as a card heading. padding is the interior ttk padding; B2's Song
    card passes (0, 0) and keeps an inner pad Frame so existing custom
    widgets and B1 chrome measurement stay on packed children.
    """
    text = title if (not title or title[:1] == " ") else " %s " % title.strip()
    return ttk.LabelFrame(parent, text=text, padding=padding)


def make_convert_button(parent, text, command=None):
    """Standard Convert.TButton widget with native disabled maps.

    Native disabled maps come from Convert.TButton; callers use
    set_ttk_enabled rather than a custom set_enabled. Construction is
    the INV202 PRACTICE_PLAY / PRACTICE_DEMO extraction target.
    """
    return ttk.Button(parent, text=text, style="Convert.TButton",
                      command=command)


def set_ttk_enabled(btn, enabled: bool) -> None:
    """Enable or disable a ttk.Button through native state flags."""
    try:
        if enabled:
            btn.state(["!disabled"])
        else:
            btn.state(["disabled"])
    except tk.TclError:
        pass


def ttk_is_enabled(btn) -> bool:
    """True when the ttk widget is not in the disabled state."""
    try:
        return "disabled" not in btn.state()
    except tk.TclError:
        return False


# -- Setup card recipes (4.14.3 B3) ------------------------------------------
SETUP_FALL_RANGE = (0.8, 8.0)
SETUP_NOTE_RANGE = (0.5, 2.0)
SETUP_RESET_VALUE = 1.0


def make_setup_switch_row(parent, label, hint, on, command, background):
    """One Setup switch on its own full-width row. Returns the switch.

    Shared ToggleSwitch is imported lazily inside this function so importing
    this module without the Play/Studio widgets file still succeeds (INV177
    isolated import). If parakit_practice_widgets cannot be imported when
    building the row, the ImportError is raised. Pack is fill=X on a dedicated
    row; two switches never share a row (INV-PH-SETUP LAYOUT).
    """
    from parakit_practice_widgets import (
        F_BASE, F_SMALL, MUTED, TEXT, ToggleSwitch,
    )
    row = tk.Frame(parent, background=background)
    row.pack(fill=tk.X, pady=3)  # one switch per row
    tk.Label(row, text=label, background=background, foreground=TEXT,
             font=F_BASE).pack(side=tk.LEFT)
    if hint:
        tk.Label(row, text=" " + hint, background=background, foreground=MUTED,
                 font=F_SMALL).pack(side=tk.LEFT)
    sw = ToggleSwitch(row, on=bool(on), command=command, background=background)
    sw.pack(side=tk.RIGHT)
    return sw


def make_setup_scale_row(parent, label, lo, hi, initial, fmt, on_change,
                         reset, background):
    """Full-width Setup scale with a numeric label and optional reset.

    reset is the value the ↺ button writes (B3: 1.0 for both fall time
    and note size). Returns (scale, val_lbl, var, reset_btn).
    """
    try:
        from parakit_practice_widgets import (
            CYAN, F_BASE, F_BOLD, OutlineButton, PURPLE_EDGE, TEXT,
        )
    except Exception:
        CYAN, TEXT = "#00d4d4", "#e0e0e0"
        F_BASE, F_BOLD = ("Segoe UI", 9), ("Segoe UI", 9, "bold")
        OutlineButton, PURPLE_EDGE = None, "#8b5cf6"
    row = tk.Frame(parent, background=background)
    row.pack(fill=tk.X, pady=(8, 0))
    tk.Label(row, text=label, background=background, foreground=TEXT,
             font=F_BASE).pack(side=tk.LEFT)
    val_lbl = tk.Label(row, text=fmt.format(initial), background=background,
                       foreground=CYAN, font=F_BOLD, width=7, anchor=tk.E)
    val_lbl.pack(side=tk.RIGHT)
    var = tk.DoubleVar(value=initial)

    def _moved(_value, _var=var, _lbl=val_lbl, _fmt=fmt, _cb=on_change):
        v = _var.get()
        _lbl.configure(text=_fmt.format(v))
        if _cb is not None:
            _cb(v)

    scale = ttk.Scale(row, from_=lo, to=hi, orient=tk.HORIZONTAL,
                      variable=var, style="Prac.Horizontal.TScale",
                      command=_moved)
    scale.pack(side=tk.LEFT, fill=tk.X, expand=True,
               padx=(10, 6 if reset is not None else 10))
    reset_btn = None
    if reset is not None:
        def _do_reset(_v=reset, _var=var, _lbl=val_lbl, _fmt=fmt, _cb=on_change):
            _var.set(_v)
            _lbl.configure(text=_fmt.format(_v))
            if _cb is not None:
                _cb(_v)
        if OutlineButton is not None:
            reset_btn = OutlineButton(
                row, "↺", accent=PURPLE_EDGE, command=_do_reset,
                tooltip="Reset to default (%s)" % fmt.format(reset))
        else:
            reset_btn = ttk.Button(row, text="↺", command=_do_reset)
        reset_btn.pack(side=tk.RIGHT, padx=(0, 6))
    return scale, val_lbl, var, reset_btn
