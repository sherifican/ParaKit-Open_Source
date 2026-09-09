"""parakit_song_tester_tab.py -- the Song Tester tab, rebuilt to its v5 design.

Sidecar for ParaKit v4.0.py, in the same shape as parakit_spectral_tab.py: the host
builds ``SongTesterTab(parent, hooks=...)`` into a Notebook page and talks to it
through a hooks dict plus four seams. The analysis thread stays in the host; this
module never sees a thread -- every callback below is invoked by the host via
``root.after(0, ...)``, the same idiom the host's own ``_tester_log`` has always used.

Why a sidecar: the whole tab lives here so the monolith diff stays small and
auditable, and a broken/missing sidecar degrades to an explanatory label instead
of killing the app (the host guards both the import and the constructor).

Layout: two columns at roomy (0.92 : 1.08, the prototype's grid), stacked to one
column at compact -- the prototype's own sub-940px rule, and the answer to 1080p.
Both columns are children of one scrolling frame so the swap is a re-grid, never a
re-parent (Tk cannot re-parent widgets).

Design authority: prototypes/song-tester-web/parakit-song-tester.html.
Plan: PLAN_song_tester_v5_look_2026-09-03.md (revision 4). Stubs:
fleet_out/STUBS_song_tester_2026-09-03.md.
"""

import os
import tkinter as tk
from tkinter import ttk, scrolledtext

# ── palette: the Spectral sidecar's constants are what make this tab look like the
# rest of the app. Literal fallbacks let the module import standalone (harness/review).
try:
    from parakit_spectral_tab import (PURPLE_LT, CYAN, PANEL, CANVAS_BG, PURPLE_EDGE,
                                      GREEN, AMBER, MUTED, TEXT)
except Exception:                                   # standalone / review host
    PURPLE_LT, CYAN, PANEL, CANVAS_BG = "#b388ff", "#00d4d4", "#15152a", "#070710"
    PURPLE_EDGE, GREEN, AMBER, MUTED, TEXT = "#8b5cf6", "#00c853", "#e09a3a", "#7e7e96", "#e0e0e0"

MAGENTA_TICK = "#ff5cf0"      # MIDI-note tick (prototype-native; no sidecar role)
SCORE_CAP = "#f2fff9"         # score-bar end-cap (prototype-native)
ERR_RED = "#e94560"           # this tab's existing error red
LOG_BG, LOG_FG = "#0d1117", "#58a6ff"   # the app's log-widget colors (same as the old tab)
ST_ANIMATE = True             # host may flip from config key "st_animate"

DRIFT_OK = 0.15               # a section is "in sync" below this |drift| (prototype + host agree)

# The two new canvases. Fallbacks keep the shell runnable before the widget module
# is assembled from the legs' scratch files; the real classes replace them at merge.
_LEGEND_FALLBACK = (("OK", GREEN), ("Drift", AMBER), ("Bad", ERR_RED),
                    ("MIDI notes", MAGENTA_TICK), ("Audio onsets", CYAN))
try:
    import parakit_song_tester_widgets as _stw
except Exception:
    _stw = None
# Per-name so a partially assembled widget module still provides what it has.
GradientBar = getattr(_stw, "GradientBar", None)
SyncTimeline = getattr(_stw, "SyncTimeline", None)
LEGEND = getattr(_stw, "LEGEND", _LEGEND_FALLBACK)

PLACEHOLDER_TIMELINE = "The section-by-section sync timeline appears here after a test."
PLACEHOLDER_REPORT = "Run a sync test to see the section-by-section report here."
ANALYZING = "Analyzing…"   # ellipsis as in the prototype (HTML :609/:613); the host's log lines use "..."
BADGE_FG = "#08060f"       # rating pill text on its colored background (prototype .vbadge)

BADGE_COLORS = {"EXCELLENT": GREEN, "GOOD": AMBER, "NEEDS WORK": ERR_RED}


def rating_of(results):
    """Derived, never stored -- mirrors the host's own branch at the rating block:
    no issues -> EXCELLENT, exactly one -> GOOD, more -> NEEDS WORK. balance_issues do
    not enter it there, so they do not enter it here."""
    issues = results.get("issues") or []
    if not issues:
        return "EXCELLENT"
    if len(issues) == 1:
        return "GOOD"
    return "NEEDS WORK"


def apply_theme_embedded(tab):
    """Re-assert the tab's tk (non-ttk) colors after the host's theme pass. ttkbootstrap
    re-apply passes clobber embedded dark styles exactly as they do Spectral's; the host
    calls this from _apply_theme next to the Spectral/Preview/Practice blocks."""
    try:
        tab._apply_colors()
    except Exception:
        pass


class SongTesterTab(ttk.Frame):
    """The Song Tester tab. ``hooks`` is a dict from the host (any subset; a missing
    hook degrades). ``compact`` selects the stacked layout; ``relayout`` swaps it live."""

    def __init__(self, parent, hooks=None, compact=False, **kw):
        super().__init__(parent, **kw)
        self.hooks = hooks if hooks is not None else {}
        self._compact = bool(compact)
        self._has_run = False
        self._last_results = None
        # (widget, kind) pairs whose padding depends on the layout flag; relayout()
        # reconfigures them so a live layout swap reaches this tab (Job 1, Step F).
        self._mode_bound = []

        self._build_scroll_host()
        self._build_left_column(self._inner)
        self._build_right_column(self._inner)
        self.relayout(self._compact)
        self._apply_colors()
        self._set_initial_state()

    # ── hook helpers (copied shape from SpectralTab) ─────────────────────────
    def _hook_call(self, name, *args, **kwargs):
        """(ok, result). Missing or raising hooks degrade; they never take the tab down."""
        fn = self.hooks.get(name)
        if fn is None:
            return (False, None)
        try:
            return (True, fn(*args, **kwargs))
        except Exception as e:
            if name != "status":   # a raising status hook must not recurse through _status
                self._status("%s hook failed: %s" % (name, e))
            return (False, None)

    def _icon(self, name):
        ok, img = self._hook_call("fluent_icon", name)
        return img if ok else None

    def _title(self, lf, text, icon):
        ok, _ = self._hook_call("labelframe_title", lf, text, icon)
        if not ok:
            lf.configure(text=text)

    def _status(self, text):
        self._hook_call("status", text)

    # ── scroll host: both columns live inside one scrolling frame ────────────
    def _build_scroll_host(self):
        # Same pattern the old tab used (canvas + inner frame + create_window), kept
        # because it is the one that already survives every platform's resize quirks.
        self._canvas = tk.Canvas(self, bg=PANEL, highlightthickness=0)
        sb = ttk.Scrollbar(self, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._inner = ttk.Frame(self._canvas, padding=12)
        self._win = self._canvas.create_window((0, 0), window=self._inner, anchor="nw")

        def _fit_fill():
            """Match the window item's height to the canvas when the content is shorter.

            Tk's -confine only holds the view inside the scrollregion while that region
            is TALLER than the window. When it is SHORTER, Tk lets the origin rise by
            the difference and paints blank ABOVE the content, and yview goes on
            reporting 0..1 the whole time -- so the tab could be scrolled above its own
            heading on every display where everything fit, and the identical gap sat
            unused below the content. Measured before the fix: 381 px of both on a
            1440p compact window. Matching the item to the canvas closes the two at
            once, since there is then no slack to scroll and the right column's Report
            card grows into the space. Taller-than-canvas content keeps its own height
            and still scrolls. The equality check is what keeps re-firing from feeding
            back into <Configure>; the host's _make_scrollable_tab guards the same way.
            """
            try:
                target = max(self._inner.winfo_reqheight(), self._canvas.winfo_height())
                if str(self._canvas.itemcget(self._win, "height")) != str(target):
                    self._canvas.itemconfigure(self._win, height=target)
            except Exception:
                pass

        def _on_inner(e=None):
            _fit_fill()
            bb = self._canvas.bbox("all")
            if bb:
                # Force the top to y=0 -- bbox can report a non-zero y1 on some
                # resize sequences, which lets the user scroll above the header.
                self._canvas.configure(scrollregion=(0, 0, bb[2], bb[3]))
        self._inner.bind("<Configure>", _on_inner)

        def _on_canvas(e):
            self._canvas.itemconfig(self._win, width=e.width)
            # The width check lives HERE, on every resize -- relayout() alone runs once
            # at construction (width 1, unmapped) and would never see the real width.
            self._maybe_restack(e.width)
            _on_inner()          # the canvas just changed height: refit, then re-measure
        self._canvas.bind("<Configure>", _on_canvas)
        self._fit_fill = _fit_fill

    def _refit(self):
        """Re-run the fill after the tab's content has grown or shrunk.

        Needed because the fill is what breaks its own trigger: once the window item's
        height is pinned, the inner frame stops resizing when its content changes, so
        the <Configure> that would shrink it back never arrives and the tab stays at
        its tallest -- scrollable again, with the dead space returned. winfo_reqheight
        stays correct throughout, so only the call is missing. Every place that hides
        or shows part of the tab calls this: the caveat disclosure, the Auto-Fix card,
        and relayout.

        Deferred with after_idle, and then update_idletasks BEFORE measuring: Tk carries
        a packed child's new size up through nested frames one idle pass per level, so
        a single idle callback scheduled alongside the pack still read the OLD requested
        height (traced 2026-09-03: 809 at the idle call, 881 one call later).
        update_idletasks drains that whole chain and processes no events.
        """
        def _now():
            try:
                self.update_idletasks()
            except Exception:
                pass
            self._fit_fill()
        try:
            self.after_idle(_now)
        except Exception:
            pass

    def _show_fix_card(self, show):
        """Show or hide the Auto-Fix card. Centralized because every one of these
        changes the tab's content height, and the fill has to be told (see _refit)."""
        if show:
            self.fix_card.pack(fill=tk.X, pady=(0, 8), after=self._fix_hint)
        else:
            self.fix_card.pack_forget()
        self._refit()

    # ── left column ───────────────────────────────────────────────────────────
    def _build_left_column(self, parent):
        left = ttk.Frame(parent)
        self._left = left
        pad, vpad = self._mode_pad(self._compact), self._mode_vpad(self._compact)

        # Font and color set explicitly as well as via style: in the app this label
        # reported style='TLabel' at runtime and rendered small and plain, and the walker
        # responsible was not found (the only style-mapping walker, _a2m_tint_card, is
        # Audio->MIDI-local). Explicit options survive any style reset. Root cause open.
        ttk.Label(left, text="Song Tester", image=self._icon("beaker") or "",
                  compound="left", style="Header.TLabel",
                  font=("Segoe UI", 16, "bold"), foreground=PURPLE_LT).pack(anchor="w")
        ttk.Label(left, text="Analyze sync quality between your MIDI and audio files before converting.",
                  style="Sub.TLabel").pack(anchor="w", pady=(2, 6))

        # Collapsible caveat (the prototype's <details>): summary row + hidden body.
        cav = ttk.Frame(left)
        cav.pack(fill=tk.X, pady=(0, 8))
        self._caveat_open = tk.BooleanVar(value=not self._compact)
        self._caveat_btn = ttk.Button(cav, text="", width=2, command=self._toggle_caveat)
        self._caveat_btn.pack(side=tk.LEFT)
        ttk.Label(cav, text="If you hand-edited the MIDI, the tester may report false errors — that's expected",
                  style="Sub.TLabel", foreground=AMBER).pack(side=tk.LEFT, padx=(6, 0))
        self._caveat_body = ttk.Label(
            left, style="Sub.TLabel", foreground=AMBER, justify=tk.LEFT, wraplength=520,
            text=("The tester compares note timings against audio onsets and has no way to "
                  "account for manual adjustments, so after MIDI-Editor edits it can show BPM "
                  "mismatches, drift warnings, or low alignment scores that aren't real problems. "
                  "Use your own judgment and confirm in-game rather than relying solely on the "
                  "score for edited MIDIs."))
        self._caveat_after = cav
        self._toggle_caveat(init=True)

        # ── Files card ────────────────────────────────────────────────────────
        files = self._bind_mode(ttk.LabelFrame(left, text=" Files ", padding=pad), "padding")
        self._title(files, " Files ", "folder_open")
        self._bind_mode(files, "pady").pack(fill=tk.X, pady=vpad)
        self.tester_midi_var = tk.StringVar()
        self.tester_audio_var = tk.StringVar()
        self.tester_drum_var = tk.StringVar()
        self.tester_rlrr_var = tk.StringVar()

        def tfile_row(label, var, row, ft, config_key=None):
            lbl = ttk.Label(files, text=label + ":")
            lbl.grid(row=row, column=0, sticky="w", padx=(0, 5), pady=2)
            ent = ttk.Entry(files, textvariable=var)
            ent.grid(row=row, column=1, sticky="ew", padx=(0, 5), pady=2)
            btns = ttk.Frame(files)
            btns.grid(row=row, column=2, pady=2, sticky="w")
            b = ttk.Button(btns, text="Browse...",
                           command=lambda v=var, f=ft, ck=config_key: self._hook_call("browse", v, f, ck))
            b.pack(side=tk.LEFT)
            if config_key:
                ok, rb = self._hook_call("make_recent_btn", btns, config_key, var, ft)
                if ok and rb is not None:
                    rb.pack(side=tk.LEFT, padx=(2, 0))
            ttk.Button(btns, text="✕", width=2, command=lambda v=var: v.set("")
                       ).pack(side=tk.LEFT, padx=(4, 0))
            for w in (lbl, ent, b):
                self._hook_call("enable_drop", w, var, config_key=config_key)

        tfile_row("MIDI File", self.tester_midi_var, 0,
                  [("MIDI", "*.mid *.midi"), ("All", "*.*")], "recent_tester_midi")
        tfile_row("Song Audio *", self.tester_audio_var, 1,
                  [("Audio", "*.ogg *.mp3 *.wav *.flac"), ("All", "*.*")], "recent_tester_audio")
        tfile_row("Drum Audio", self.tester_drum_var, 2,
                  [("Audio", "*.ogg *.mp3 *.wav *.flac"), ("All", "*.*")], "recent_tester_drum")
        tfile_row(".rlrr File", self.tester_rlrr_var, 3,
                  [("RLRR files", "*.rlrr"), ("All", "*.*")], "recent_tester_rlrr")
        files.columnconfigure(1, weight=1)

        af_outer = ttk.Frame(files)
        af_outer.grid(row=4, column=1, columnspan=2, sticky="w", pady=(4, 2))
        af_border = tk.Frame(af_outer, bg=CYAN)
        af_border.pack(side=tk.LEFT)
        self._af_border = af_border   # explicit tk color; re-asserted by _apply_colors
        af_btn = ttk.Button(af_border, text="Auto Fetch Audio", image=self._icon("music_note_2") or "",
                            compound="left", width=21,
                            command=lambda: self._hook_call("auto_fetch_audio"))
        af_btn.pack(padx=2, pady=2)
        self._hook_call("add_tooltip", af_btn,
                        "From the MIDI (or .rlrr) file name, finds the matching Song Audio +\n"
                        "Drum Audio (from your Stem Splitter / YouTube output folders and next\n"
                        "to the file) and fills those fields. Same as the MIDI Editor's Auto Fetch.")
        ttk.Label(af_outer, text="  Fills Song Audio + Drum Audio from the MIDI / .rlrr name",
                  style="Sub.TLabel", foreground=MUTED).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(files, style="Sub.TLabel", foreground=PURPLE_LT, justify=tk.LEFT, wraplength=560,
                  text=("Test the MIDI or the .rlrr. The .rlrr tests your actual output (difficulty "
                        "applied); the MIDI is fine if you haven't built the .rlrr yet. With both set, "
                        "the .rlrr supplies the notes and the MIDI only its tempo. Either way, it's best "
                        "to finish your edits first so you test the chart you'll ship. A separate "
                        "drum-audio file makes the analysis more accurate.")
                  ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(6, 0))

        # ── Difficulty card ───────────────────────────────────────────────────
        diff = self._bind_mode(ttk.LabelFrame(left, text=" Difficulty Settings ", padding=pad), "padding")
        self._title(diff, " Difficulty Settings ", "options")
        self._bind_mode(diff, "pady").pack(fill=tk.X, pady=vpad)
        self.tester_reduce_var = tk.BooleanVar(value=False)
        self.tester_diff_var = tk.StringVar(value="Expert")
        # Two rows, not one. A single packed row (checkbox + label + combobox + a
        # 380-wrapped note) requested 803 px, which set the whole left column's width
        # and inverted the 92:108 split (measured 993:838). The note wraps underneath.
        drow = ttk.Frame(diff)
        drow.pack(fill=tk.X)
        ttk.Checkbutton(drow, text="Note reduction is enabled for this conversion",
                        variable=self.tester_reduce_var).pack(side=tk.LEFT, padx=(0, 20))
        ttk.Label(drow, text="Difficulty:").pack(side=tk.LEFT, padx=(0, 5))
        ttk.Combobox(drow, textvariable=self.tester_diff_var, width=10, state="readonly",
                     values=["Easy", "Medium", "Hard", "Expert"]).pack(side=tk.LEFT)
        ttk.Label(diff, style="Sub.TLabel", wraplength=560, justify=tk.LEFT,
                  text="Match these to your Single Song Creator settings for accurate density analysis. "
                       "The reduction is simulated for the test only — it does not change any file."
                  ).pack(anchor="w", pady=(4, 0))

        # ── Run + Send to Spectral ────────────────────────────────────────────
        self.tester_btn = ttk.Button(left, text="Run Sync Test", image=self._icon("beaker") or "",
                                     compound="left", style="Convert.TButton",
                                     command=lambda: self._hook_call("start_analysis"))
        self.tester_btn.pack(fill=tk.X, pady=(5, 6), ipady=8)
        # Prototype `.runnote` (HTML :316): the amber prerequisite note under the Run button.
        ttk.Label(left, style="Sub.TLabel", foreground=AMBER, justify=tk.LEFT, wraplength=560,
                  text=("Test with your MIDI or your finished .rlrr (plus the Song Audio). With both "
                        "set, the .rlrr supplies the notes and the MIDI only its tempo. It's "
                        "recommended to finish your edits first so the chart you test is the one you'll ship.")
                  ).pack(anchor="w", pady=(0, 6))
        spec = ttk.Button(left, text="Send to Spectral Comparison", image=self._icon("device_eq") or "",
                          compound="left", command=lambda: self._hook_call("send_to_spectral"))
        spec.pack(fill=tk.X, pady=(0, 6), ipady=4)
        self._hook_call("add_tooltip", spec,
                        "Open this song in the Spectral Comparison tab to check the detection\n"
                        "against the audio -- sends the Drums stem, chart, and Full Mix so\n"
                        "MISS / PHANTOM disagreements show on the graph.")

        # ── Adjust & Re-test (starts disabled; enabled by the first completed run) ──
        adj = self._bind_mode(ttk.LabelFrame(left, text=" Adjust & Re-test ", padding=pad), "padding")
        self._title(adj, " Adjust & Re-test ", "arrow_clockwise")
        adj.pack(fill=tk.X, pady=(0, 8))
        self.adjust_card = adj
        ttk.Label(adj, text="BPM:").grid(row=0, column=0, sticky="w", padx=(0, 5))
        self.tester_bpm_var = tk.StringVar()
        ttk.Entry(adj, textvariable=self.tester_bpm_var, width=10).grid(row=0, column=1, sticky="w", padx=(0, 15))
        ttk.Label(adj, text="Offset (s):").grid(row=0, column=2, sticky="w", padx=(0, 5))
        self.tester_offset_var = tk.StringVar()
        ttk.Entry(adj, textvariable=self.tester_offset_var, width=10).grid(row=0, column=3, sticky="w", padx=(0, 15))
        # Buttons on their own row: one seven-column row was the left column's minimum
        # width (~1000 px) and beat the 92:108 grid weights. The prototype wraps this row.
        btns = ttk.Frame(adj)
        btns.grid(row=1, column=0, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Button(btns, text="Re-test with these values", image=self._icon("arrow_clockwise") or "",
                   compound="left", command=lambda: self._hook_call("retest")).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btns, text="Send to Song Creator", image=self._icon("send") or "", compound="left",
                   style="Convert.TButton", command=lambda: self._hook_call("send_to_creator")
                   ).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btns, text="Send to Preview/Practice Track", image=self._icon("tv") or "",
                   compound="left", command=lambda: self._hook_call("send_to_visualizer")
                   ).pack(side=tk.LEFT)
        ttk.Label(adj, style="Sub.TLabel", wraplength=560,
                  text="After a test, tweak BPM/offset and re-test, or send the values straight to the Song Creator or Preview/Practice Track."
                  ).grid(row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))

        # The pre-redesign tab's always-visible hint for why the Auto-Fix card is usually
        # absent. Copy unchanged; the first shell dropped it (caught by the kimi cross-check).
        self._fix_hint = ttk.Label(
            left, style="Sub.TLabel", foreground=PURPLE_LT, wraplength=560, justify=tk.LEFT,
            text="The Auto-Fix panel appears here only when the tester finds issues — on a clean / "
                 "EXCELLENT result it stays hidden, because there's nothing to fix.")
        self._fix_hint.pack(anchor="w", pady=(0, 4))

        # ── Auto-Fix card (built, NOT packed -- shown only when a run finds issues) ──
        fix = self._bind_mode(ttk.LabelFrame(left, text=" Issues Found — Auto-Fix Options ", padding=pad), "padding")
        self._title(fix, " Issues Found — Auto-Fix Options ", "wrench")
        self.tester_fix_frame = fix
        self.fix_card = fix
        # Two halves: the options on the left, the Fix Log on the right, the same shape
        # as the Audio->MIDI tab's Log card. The log used to sit UNDER the options as a
        # five-line strip, which made the card tall enough to drop below the fold in
        # roomy on a 1080p window (owner, 2026-09-03). uniform= makes the halves equal;
        # the log frame is sticky in a weighted row, so the log is as tall as the
        # options are, a block rather than a bar.
        fix.columnconfigure(0, weight=1, uniform="fixcols")
        fix.columnconfigure(1, weight=1, uniform="fixcols")
        fix.rowconfigure(0, weight=1)
        opts = ttk.Frame(fix)
        opts.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        logf = ttk.Frame(fix)
        logf.grid(row=0, column=1, sticky="nsew")
        _wrap = 400          # starting value; re-bound to the half's real width below
        _intro = ttk.Label(opts, style="Sub.TLabel", wraplength=_wrap, justify=tk.LEFT,
                           text=("The tester found potential issues. Choose what the auto-fix may change, then "
                                 "apply it or open the chart in the MIDI Editor with the problem notes flagged."))
        _intro.pack(anchor="w", pady=(0, 8))
        _wrapped = [(_intro, 4)]          # (label, horizontal inset it sits behind)
        self.fix_safe_var = tk.BooleanVar(value=True)
        self.fix_reclassify_var = tk.BooleanVar(value=False)
        self.fix_timing_var = tk.BooleanVar(value=False)
        def _fix_option(var, label, detail, tooltip=None, pady=(0, 2)):
            """A checkbox with the prototype's wrapping sub-span beneath it. A ttk.Checkbutton
            cannot wrap, and the reclassify line with its example measured 870 px -- wider
            than the left column -- so the detail lives in a wrapped label instead."""
            cb = ttk.Checkbutton(opts, variable=var, text=label)
            cb.pack(anchor="w")
            lbl = ttk.Label(opts, text=detail, style="Sub.TLabel", foreground=MUTED, justify=tk.LEFT,
                            wraplength=_wrap)
            lbl.pack(anchor="w", padx=(24, 0), pady=pady)
            _wrapped.append((lbl, 28))
            if tooltip:
                self._hook_call("add_tooltip", cb, tooltip)
            return cb
        _fix_option(self.fix_safe_var, "Safe fixes",
                    "remove notes past audio end, remove duplicates within 10 ms")
        _fix_option(self.fix_reclassify_var, "Reclassify instruments",
                    "reassign notes that look like the wrong drum, e.g. crash hits with snare-like "
                    "timing → snare — may be wrong on some songs; review after",
                    "This may cause incorrect changes on some songs.\nAlways review in the MIDI Editor after applying.")
        _fix_option(self.fix_timing_var, "Shift note timing",
                    "nudge notes in sections with drift > 0.15 s toward audio onsets — can alter feel",
                    "Can alter the feel of the song.\nOnly shifts sections where drift was flagged.\n"
                    "Always review in the MIDI Editor after applying.",
                    pady=(0, 8))
        # The two buttons stack: side by side they measured wider than the half.
        row = ttk.Frame(opts)
        row.pack(anchor="w", fill=tk.X)
        ttk.Button(row, text="Apply Auto-Fix & Open in Editor", image=self._icon("wrench") or "",
                   compound="left", style="Convert.TButton",
                   command=lambda: self._hook_call("apply_autofix")).pack(anchor="w", fill=tk.X, pady=(0, 4))
        # Secondary at its natural width (grok review): stretched to the half it read as a
        # second Convert; the prototype and the Audio->MIDI card keep it a plain button.
        ttk.Button(row, text="Open in MIDI Editor (flagged, no changes)", image=self._icon("music_note_2") or "",
                   compound="left", command=lambda: self._hook_call("open_flagged")).pack(anchor="w")

        def _rewrap(e):
            """Follow the half's real width: a fixed 400 px wrap behind a 24 px inset clipped
            the last character of two details at 1080p compact and lost 81 px in a narrower
            two-column window (grok, measured). The rewrap changes the requested height, so
            the fill is told."""
            for lbl, inset in _wrapped:
                try:
                    lbl.configure(wraplength=max(120, e.width - inset))
                except Exception:
                    pass
            self._refit()
        opts.bind("<Configure>", _rewrap)
        hdr = ttk.Frame(logf)
        hdr.pack(fill=tk.X, pady=(0, 2))
        ttk.Label(hdr, text="Fix Log:", style="Sub.TLabel").pack(side=tk.LEFT)
        ttk.Button(hdr, text="Export Log", image=self._icon("document") or "", compound="left",
                   command=lambda: self._hook_call("export_fix_log")).pack(side=tk.RIGHT)
        # height is a floor; the frame's weighted row stretches it to the options' height.
        # width= is a floor too: without it a ScrolledText requests 564 px and the card asked
        # the column for 1178 px it could never get (grok, measured).
        self.fix_log_text = scrolledtext.ScrolledText(logf, height=8, width=40, bg=LOG_BG, fg=LOG_FG,
                                                      font=("Consolas", 9), wrap=tk.WORD,
                                                      insertbackground=LOG_FG, state="disabled")
        self.fix_log_text.pack(fill=tk.BOTH, expand=True, pady=(0, 4))

    def _toggle_caveat(self, init=False):
        if not init:
            self._caveat_open.set(not self._caveat_open.get())
        opened = self._caveat_open.get()
        self._caveat_btn.configure(image=self._icon("chevron_down" if opened else "chevron_right") or "",
                                   text="" if self._icon("chevron_down") else ("v" if opened else ">"))
        if opened:
            self._caveat_body.pack(anchor="w", pady=(0, 8), after=self._caveat_after)
        else:
            self._caveat_body.pack_forget()
        self._refit()

    # ── right column ──────────────────────────────────────────────────────────
    def _build_right_column(self, parent):
        right = ttk.Frame(parent)
        self._right = right
        pad, vpad = self._mode_pad(self._compact), self._mode_vpad(self._compact)

        # ── Result card ───────────────────────────────────────────────────────
        res = self._bind_mode(ttk.LabelFrame(right, text=" Result ", padding=pad), "padding")
        self._title(res, " Result ", "clipboard_task")
        self._bind_mode(res, "pady").pack(fill=tk.X, pady=vpad)
        top = ttk.Frame(res)
        top.pack(fill=tk.X)
        self._badge = tk.Label(top, text="— not run —", bg=PANEL, fg=MUTED,
                               font=("Segoe UI", 11, "bold"), padx=12, pady=4)
        self._badge.pack(side=tk.LEFT, padx=(0, 14))
        self._file_lbl = ttk.Label(res, text="", style="Sub.TLabel", foreground=MUTED)
        self._file_lbl.place(relx=1.0, y=0, anchor="ne")
        stats = ttk.Frame(top)
        stats.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._stat = {}
        for i, (key, label) in enumerate((("bpm", "Detected BPM"), ("offset", "Offset"),
                                           ("avg", "Avg distance"), ("worst", "Worst drift"),
                                           ("notes", "Notes (% of expected)"))):
            cell = ttk.Frame(stats)
            cell.grid(row=0, column=i, padx=(0, 14), sticky="w")
            ttk.Label(cell, text=label.upper(), style="Sub.TLabel", foreground=MUTED,
                      font=("Segoe UI", 7)).pack(anchor="w")
            v = tk.Label(cell, text="—", bg=PANEL, fg=CYAN if key in ("bpm", "offset") else TEXT,
                         font=("Consolas", 11, "bold"))
            v.pack(anchor="w")
            self._stat[key] = v

        # Sync (process) bar -- the app's own loader, via hook (the class lives in the
        # monolith and a sidecar cannot import the main script). Falls back to ttk.
        self._sync_caption = ttk.Label(res, text="Sync analysis", style="Sub.TLabel", foreground=MUTED)
        self._sync_caption.pack(anchor="w", pady=(10, 0))
        ok, bar = self._hook_call("make_progress_bar", res, 400, 30)
        if ok and bar is not None:
            self._sync_bar = bar
            self._sync_set = lambda f: bar.set(max(0.0, min(1.0, f)))
        else:
            self._sync_bar = ttk.Progressbar(res, mode="determinate", maximum=1.0)
            self._sync_set = lambda f: self._sync_bar.configure(value=max(0.0, min(1.0, f)))
        self._sync_bar.pack(fill=tk.X, pady=(2, 0))

        # Score (result) bar -- the one new widget. Fallback keeps the shell runnable.
        if GradientBar is not None:
            self._score_bar = GradientBar(res)
        else:
            self._score_bar = _FallbackScoreBar(res)
        self._score_bar.pack(fill=tk.X, pady=(18, 2))

        # ── Sync Timeline card ────────────────────────────────────────────────
        tl = self._bind_mode(ttk.LabelFrame(right, text=" Sync Timeline ", padding=pad), "padding")
        self._title(tl, " Sync Timeline ", "data_bar_vertical")
        self._bind_mode(tl, "pady").pack(fill=tk.BOTH, pady=vpad)
        if SyncTimeline is not None:
            self._timeline = SyncTimeline(tl)
        else:
            self._timeline = _FallbackTimeline(tl)
        self._timeline.configure(bg=CANVAS_BG, highlightthickness=1, highlightbackground=PURPLE_EDGE)
        self._timeline.pack(fill=tk.BOTH, expand=True)
        legend = ttk.Frame(tl)
        legend.pack(anchor="w", pady=(3, 0))
        self._legend_src_label = None
        self._legend_swatches = []
        for name, color in LEGEND:
            sw = tk.Label(legend, bg=color, width=1, height=1)
            sw.pack(side=tk.LEFT, padx=(0, 4))
            self._legend_swatches.append((sw, color))
            lbl = ttk.Label(legend, text=name, style="Sub.TLabel", foreground=MUTED,
                            font=("Segoe UI", 7))
            lbl.pack(side=tk.LEFT, padx=(0, 12))
            if name.startswith("MIDI") and name.endswith("notes"):
                # Follows the chart source after a run ("RLRR notes" for an .rlrr test).
                # SyncTimeline stores the source label but does not render it.
                self._legend_src_label = lbl

        # ── Report card ───────────────────────────────────────────────────────
        rep = self._bind_mode(ttk.LabelFrame(right, text=" Report ", padding=pad), "padding")
        self._title(rep, " Report ", "document")
        rep.pack(fill=tk.BOTH, expand=True)
        rh = ttk.Frame(rep)
        rh.pack(fill=tk.X)
        ttk.Button(rh, text="Export", image=self._icon("arrow_download") or "", compound="left",
                   command=self._export_report).pack(side=tk.RIGHT)
        self.tester_log = scrolledtext.ScrolledText(rep, height=14, bg=LOG_BG, fg=LOG_FG,
                                                    font=("Consolas", 9), wrap=tk.WORD,
                                                    insertbackground=LOG_FG, state="disabled")
        self.tester_log.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

    # ── layout modes ──────────────────────────────────────────────────────────
    # Stacking keys on the tab's ACTUAL width, not on the app's compact flag. The first
    # in-app capture showed why: the app calls any display <= 1920 wide "compact", so a
    # flag-driven stack would hand every 1080p user the old single-column shape -- the
    # very thing this rebuild replaces. The prototype stacks below 940 CSS px; at 1920
    # the two columns (~870 / ~1020 px) fit with room. `compact` now means what it
    # means everywhere else in the app: tighter padding. Width is re-checked on every
    # canvas <Configure>, so a live resize restacks -- the media query, for free.
    STACK_BELOW = 1100   # px of tab width; below this the columns stack

    @staticmethod
    def _mode_pad(compact):
        return 6 if compact else 10

    @staticmethod
    def _mode_vpad(compact):
        return (0, 5) if compact else (0, 10)

    def _bind_mode(self, widget, kind):
        """Remember a widget whose padding follows the layout flag: kind 'padding' for a
        card's inner padding, 'pady' for a packed card's gap."""
        self._mode_bound.append((widget, kind))
        return widget

    def relayout(self, compact):
        """Apply the layout flag to this tab now. Called from __init__ and from the host's
        live layout swap. Paddings first, then the column grid: when the tab is not
        mapped (width <= 1) the grid is left for the next <Configure>, which sees the
        real width; at build time nothing is gridded yet, so the roomy default is laid."""
        self._compact = bool(compact)
        if not all(hasattr(self, a) for a in ("_canvas", "_left", "_right")):
            self._stacked = None              # called before the columns exist: nothing to lay out
            return
        pad, vpad = self._mode_pad(self._compact), self._mode_vpad(self._compact)
        for widget, kind in list(self._mode_bound):
            try:
                if kind == "padding":
                    widget.configure(padding=pad)
                elif kind == "pady":
                    widget.pack_configure(pady=vpad)
            except Exception:
                pass                              # a destroyed widget must not stop the pass
        self._refit()                             # the paddings just changed the height
        self._stacked = None                      # force a re-grid on the next check
        width = self._canvas.winfo_width()
        if (width is None or width <= 1) and self._left.winfo_manager():
            return                                # unmapped and already gridded: <Configure> decides
        self._maybe_restack(width)

    def _maybe_restack(self, width):
        stacked = bool(width and width > 1) and width < self.STACK_BELOW
        if width is None or width <= 1:           # unmapped: default to the roomy grid
            stacked = False
        if stacked == getattr(self, "_stacked", None):
            return
        self._stacked = stacked
        inner = self._inner
        for c in (0, 1):
            inner.columnconfigure(c, weight=0, uniform="")
        for r in (0, 1):
            inner.rowconfigure(r, weight=0)
        self._left.grid_forget()
        self._right.grid_forget()
        if stacked:
            inner.columnconfigure(0, weight=1)
            # The inner frame is filled to the canvas height, so give the growth to the
            # row holding the Report -- the one card built to take it.
            inner.rowconfigure(1, weight=1)
            self._left.grid(row=0, column=0, sticky="nsew", padx=0, pady=(0, 10))
            self._right.grid(row=1, column=0, sticky="nsew")
        else:
            # uniform= makes 92:108 a true ratio. Without it grid shares only the
            # LEFTOVER space by weight, so whichever column requests more wins the
            # base -- the left did (803 px) and the split came out 993:838.
            inner.columnconfigure(0, weight=92, uniform="stcols")
            inner.columnconfigure(1, weight=108, uniform="stcols")
            inner.rowconfigure(0, weight=1)
            self._left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
            self._right.grid(row=0, column=1, sticky="nsew")
        # The re-grid changes the requested height (one row vs two); the synchronous
        # measurement in _on_canvas can read the old one (codex review, 2026-09-03).
        self._refit()

    def _apply_colors(self):
        """Re-assert every explicit tk color (ttk widgets ride the theme). Runs from
        __init__ and from the host's _apply_theme, so it must survive a half-built tab
        and must re-apply the badge's CURRENT rating colors -- the first version forced
        the badge back to PANEL and left a finished run's rating dark-on-dark."""
        badge_bg, badge_fg = getattr(self, "_badge_colors", (PANEL, MUTED))
        targets = (
            (getattr(self, "_badge", None), dict(bg=badge_bg, fg=badge_fg)),
            (getattr(self, "_canvas", None), dict(bg=PANEL)),
            (getattr(self, "_timeline", None), dict(bg=CANVAS_BG, highlightbackground=PURPLE_EDGE)),
            (getattr(self, "_af_border", None), dict(bg=CYAN)),
            (getattr(self, "tester_log", None), dict(bg=LOG_BG, fg=LOG_FG, insertbackground=LOG_FG)),
            (getattr(self, "fix_log_text", None), dict(bg=LOG_BG, fg=LOG_FG, insertbackground=LOG_FG)),
        )
        for w, kw in targets:
            if w is None:
                continue
            try:
                w.configure(**kw)
            except Exception:
                pass
        for key, v in getattr(self, "_stat", {}).items():
            try:
                v.configure(bg=PANEL, fg=CYAN if key in ("bpm", "offset") else TEXT)
            except Exception:
                pass
        for sw, color in getattr(self, "_legend_swatches", ()):
            try:
                sw.configure(bg=color)
            except Exception:
                pass

    # ── states (the table in plan §2) ─────────────────────────────────────────
    def _set_initial_state(self):
        self._badge_colors = (PANEL, MUTED)
        self._badge.configure(text="— not run —", bg=PANEL, fg=MUTED)
        for v in self._stat.values():
            v.configure(text="—")
        self._file_lbl.configure(text="")
        self._sync_caption.configure(text="Sync analysis")
        self._sync_set(0.0)
        self._score_bar.reset()
        self._timeline.set_placeholder(PLACEHOLDER_TIMELINE)
        self._set_report_placeholder(PLACEHOLDER_REPORT)
        self._set_adjust_enabled(False)
        self._show_fix_card(False)

    def on_run_start(self):
        """UI thread, called by the host BEFORE thread.start() -- replaces the four lines
        _tester_start and _tester_retest used to do themselves."""
        self.tester_btn.configure(state="disabled", text=ANALYZING)
        self._clear_report()
        self._sync_caption.configure(text=ANALYZING)
        self._sync_set(0.0)
        self._score_bar.mark_scored(False)
        self._timeline.set_placeholder(ANALYZING)
        self._show_fix_card(False)
        self._set_adjust_enabled(False)   # plan §2: Adjust is disabled while a run is in flight

    def on_log(self, text, color=None):
        """The widget half of the host's _tester_log; the host marshals to the UI thread."""
        try:
            self.tester_log.configure(state="normal")
            try:
                if color:
                    tag = "color_" + color.replace("#", "")
                    self.tester_log.tag_configure(tag, foreground=color)
                    self.tester_log.insert(tk.END, text + "\n", tag)
                else:
                    ok, _ = self._hook_call("pretty_log_insert", self.tester_log, text + "\n")
                    if not ok:
                        self.tester_log.insert(tk.END, text + "\n")
                self.tester_log.see(tk.END)
            finally:
                self.tester_log.configure(state="disabled")   # never leave the report editable
        except Exception:
            pass   # widget may be gone during shutdown

    def on_phase(self, i, n):
        self._sync_set(float(i) / float(n or 1))

    def on_complete(self, results):
        """One terminal transition, on the UI thread. ``results`` is the host's
        _tester_last_results dict, or None when the run did not complete (an early
        return or an exception -- the reason is already in the report)."""
        self.tester_btn.configure(state="normal", text="Run Sync Test")
        if results is None:   # an empty dict is a (degenerate) result, not a failure
            self._sync_caption.configure(text="Stopped")
            self._sync_set(0.0)
            self._timeline.set_placeholder(PLACEHOLDER_TIMELINE)
            self._show_fix_card(False)
            return
        self._last_results = results
        self._has_run = True
        self._sync_set(1.0)
        self._sync_caption.configure(text="Sync complete")

        rating = rating_of(results)
        self._badge_colors = (BADGE_COLORS.get(rating, MUTED), BADGE_FG)
        self._badge.configure(text=rating, bg=self._badge_colors[0], fg=self._badge_colors[1])
        bpm = results.get("detected_bpm")
        off = results.get("best_offset")
        avg = results.get("avg_distance")
        secs = results.get("section_results") or []
        worst = results.get("worst_drift")
        if worst is None and secs:
            worst = max(abs(s[2]) for s in secs)
        notes = results.get("effective_note_count")
        if notes is None:
            notes = len(results.get("note_events") or [])
        pct = results.get("density_pct")
        self._stat["bpm"].configure(text="%.2f" % bpm if bpm is not None else "—")
        self._stat["offset"].configure(text="%+.3fs" % off if off is not None else "—")
        self._stat["avg"].configure(text="%.3fs" % avg if avg is not None else "—")
        self._stat["worst"].configure(text="%.2fs" % worst if worst is not None else "—")
        self._stat["notes"].configure(text=("%d (%d%%)" % (notes, round(pct))) if pct is not None else str(notes))
        src = results.get("rlrr_path") or results.get("midi_path") or ""
        self._file_lbl.configure(text=os.path.splitext(os.path.basename(src))[0] if src else "")
        # Prefill the Adjust fields (plan §2 "enabled, prefilled"); same formats the host
        # uses, so the two agree when both write.
        if bpm is not None:
            self.tester_bpm_var.set("%.2f" % bpm)
        if off is not None:
            self.tester_offset_var.set("%.3f" % off)

        in_sync = sum(1 for s in secs if abs(s[2]) < DRIFT_OK)
        total = len(secs)
        self._score_bar.set(in_sync / total if total else 0.0, animate=ST_ANIMATE)
        self._score_bar.set_label("%d of %d sections within %.2f s drift" % (in_sync, total, DRIFT_OK))
        self._score_bar.mark_scored(True)

        src_kind = "RLRR" if results.get("rlrr_path") else "MIDI"
        self._timeline.draw(_as_list(results.get("note_events")), _as_list(results.get("audio_onsets")),
                            results.get("best_offset", 0.0), secs, results.get("audio_duration", 0.0),
                            src_kind)
        if getattr(self, "_legend_src_label", None) is not None:
            self._legend_src_label.configure(text=src_kind + " notes")

        self._show_fix_card(bool(results.get("issues") or results.get("balance_issues")))
        self._set_adjust_enabled(True)

    # ── helpers ───────────────────────────────────────────────────────────────
    def _set_adjust_enabled(self, on):
        state = "normal" if on else "disabled"
        def _walk(parent):
            for w in parent.winfo_children():
                if isinstance(w, ttk.Frame):
                    _walk(w)                     # the button row
                    continue
                try:
                    if isinstance(w, ttk.Combobox):
                        w.configure(state="readonly" if on else "disabled")
                    else:
                        w.configure(state=state)
                except Exception:
                    pass
        _walk(self.adjust_card)

    def _clear_report(self):
        self.tester_log.configure(state="normal")
        self.tester_log.delete("1.0", tk.END)
        self.tester_log.configure(state="disabled")

    def _set_report_placeholder(self, text):
        self._clear_report()
        self.tester_log.configure(state="normal")
        self.tester_log.tag_configure("placeholder", foreground=MUTED)
        self.tester_log.insert(tk.END, text, "placeholder")
        self.tester_log.configure(state="disabled")

    def _export_report(self):
        text = self.tester_log.get("1.0", tk.END).strip()
        if not self._has_run or not text or text == PLACEHOLDER_REPORT:
            self._status("Nothing to export yet")
            return
        self._hook_call("export_report", text)


def _as_list(value):
    """Result values arrive as lists OR numpy arrays (`audio_onsets` is an ndarray), and
    `arr or []` raises "truth value of an array is ambiguous" -- which aborted on_complete
    mid-way on the first real-song run. Never truth-test a result value; convert it."""
    if value is None:
        return []
    try:
        return list(value)
    except TypeError:
        return []


# ── minimal fallbacks so the shell runs before the widget module is assembled ──
class _FallbackScoreBar(ttk.Frame):
    def __init__(self, parent, **kw):
        super().__init__(parent, **kw)
        self._bar = ttk.Progressbar(self, mode="determinate", maximum=1.0)
        self._bar.pack(fill=tk.X)
        self._lbl = ttk.Label(self, text="", style="Sub.TLabel")
        self._lbl.pack(anchor="e")
    def set(self, f, animate=True): self._bar.configure(value=max(0.0, min(1.0, f)))
    def set_label(self, t): self._lbl.configure(text=t)
    def mark_scored(self, on): pass
    def reset(self): self.set(0.0); self.set_label("")


class _FallbackTimeline(tk.Canvas):
    def __init__(self, parent, **kw):
        super().__init__(parent, height=184, **kw)
    def set_placeholder(self, text):
        self.delete("all")
        self.create_text(self.winfo_width() // 2 or 300, 90, text=text, fill=MUTED, font=("Segoe UI", 9))
    def draw(self, *a, **k):
        self.delete("all")
        self.create_text(300, 90, text="Timeline unavailable — the timeline module did not load.", fill=MUTED)
    def clear(self): self.delete("all")


if __name__ == "__main__":
    # Standalone smoke: builds the tab with print-stub hooks, drives it through the
    # four states with a fixture, and exits. Not a substitute for the in-app smoke.
    root = tk.Tk()
    root.title("SongTesterTab -- standalone shell smoke")
    root.geometry("1480x820+2560+0")   # second monitor, per the standing test rule
    hooks = {k: (lambda *a, _k=k, **kw: print("hook:", _k)) for k in
             ("start_analysis", "retest", "send_to_spectral", "send_to_creator", "send_to_visualizer",
              "apply_autofix", "open_flagged", "export_fix_log", "auto_fetch_audio", "browse",
              "enable_drop", "add_tooltip", "status", "export_report")}
    tab = SongTesterTab(root, hooks=hooks, compact=False)
    tab.pack(fill=tk.BOTH, expand=True)
    fixture = {
        "detected_bpm": 128.0, "best_offset": -0.02, "avg_distance": 0.121, "worst_drift": 0.31,
        "effective_note_count": 430, "density_pct": 104.0, "midi_path": "Bury the Light (Expert).mid",
        "section_results": [(0, 20, 0.02, 38, "✓", GREEN), (20, 40, 0.05, 44, "✓", GREEN),
                            (40, 60, 0.22, 48, "⚠", AMBER), (60, 80, 0.31, 46, "⚠", AMBER),
                            (80, 100, 0.12, 50, "✓", GREEN), (100, 120, -0.06, 42, "✓", GREEN),
                            (120, 140, 0.28, 36, "⚠", AMBER), (140, 160, 0.05, 30, "✓", GREEN)],
        "note_events": [i * 0.37 for i in range(430)], "audio_onsets": [i * 0.36 + 0.02 for i in range(440)],
        "audio_duration": 160.0, "issues": ["BPM mismatch"], "balance_issues": [],
    }
    def run():
        tab.on_run_start()
        for i in range(1, 8):
            root.after(120 * i, lambda i=i: tab.on_phase(i, 7))
            root.after(120 * i, lambda i=i: tab.on_log("phase %d" % i, "#888888"))
        root.after(1000, lambda: tab.on_complete(fixture))
        root.after(1600, lambda: tab.relayout(True))
        root.after(2400, lambda: tab.relayout(False))
        root.after(3000, lambda: (print("SHELL SMOKE: reached all four states + both layouts"), root.quit()))
    root.after(300, run)
    root.mainloop()
