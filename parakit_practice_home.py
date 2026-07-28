"""parakit_practice_home -- the Practice tab's HOME screen (songs library /
search / sort / inline pre-play / Setup card / Input card).

Faithful Tkinter/ttk port of the v5 PySide6 `PracticeHomeScreen`
(`ParaKit-v5-BETA-py-rebuild-phase/.../parakit_v5/ui/widgets/practice/
home_screen.py`, 1298 lines) + its off-thread scan worker
(`.../services/practice_library.py`, 236 lines), per
`DISPATCH_practice_preview_kimi-k3.md` section 3b. The pattern this file
copies end-to-end is `parakit_spectral_tab.py`: the three-helper hook seam
(`_cfg_get` / `_cfg_set` / `_hook_call`), the private `Prac.*` ttk namespace,
and the worker-thread + `after()`-polled-queue pattern for anything
off-thread (here: the folder scan instead of spectral compare).

`class PracticeHomeScreen(ttk.Frame)` is a router-composed screen: it never
plays audio itself. Every Play button resolves a session config (chart,
routing, difficulty, speed, kit, you-drum, audio paths) and hands it to the
Practice tab router via ONE of four locked `self.on_*` callback attributes
(the Home<->tab signal contract, DISPATCH section 5):

    self.on_play_request(config: dict)       -- any Play button
    self.on_open_settings(tab: str | None)    -- Settings / MIDI-advanced /
                                                  rebind chips
    self.on_open_kit_studio()                 -- Kit Studio buttons
    self.on_open_calibration()                -- Calibrate latency...

All four default to None in __init__ and are called through a single
`_emit()` helper that degrades to a status note (never a crash) when the
attribute is unset or raises.

BUILD-NOW (fully live, no hook required): the whole Home UI, the off-thread
library scan (worker thread + queue polled by after(), mirroring
parakit_spectral_tab.py's `_start_real_compare`/`_poll_compare`), live
search/sort, the inline pre-play panel, the Setup/Input cards, .rlrr parse
via `parakit_practice_engine`, config persistence through the hook seam, and
file dialogs.

DEFER (routed through `_hook_call`, degrade + a status note, hooks_none-safe):
  - `load_cover(path, size)` -- host hook still honoured if present; this
    module also ships a local `load_cover` helper (PIL → PhotoImage, with a
    tk.PhotoImage PNG/GIF fallback). Absent/failing either path → Canvas
    gradient + initials placeholder (accepted v5 behaviour).
  - `midi_list_ports()` / `midi_on_devices_changed(cb)` -- native MIDI. The
    MIDI chip always reads "none" without these; Enable MIDI degrades to a
    status note.
  - `get_kit_layout(name)` -- resolving a user/pinned kit layout by name
    (an addition beyond the DISPATCH's DEFER list, but the same shape: no
    hook -> falls back to `default_layout()`, never a crash).
  - `list_kit_presets()` -- user kit preset names for the KIT dropdown.

Stdlib + tkinter/ttk only for the UI (no numpy, no mido -- .rlrr/.json charts
are plain JSON text per the engine module). Cover loading optionally uses
Pillow when installed; absence falls back to tk.PhotoImage / initials.
"""
from __future__ import annotations

import colorsys
import functools
import json
import math
import os
import queue
import re
import threading
import time
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Any, Callable, Dict, List, Optional, Tuple

from parakit_practice_widgets import (
    AMBER,
    BG,
    CYAN,
    DARKER,
    EMBER,
    EMBER_LT,
    F_BASE,
    F_BOLD,
    F_H1,
    F_MONO,
    F_SMALL,
    INPUT_BG,
    MAGENTA,
    MUTED,
    MUTED_LAV,
    PANEL,
    PANEL2,
    PURPLE,
    PURPLE_DEEP,
    PURPLE_EDGE,
    ROW_ALT,
    SEPARATOR,
    TEXT,
    TEXT_BRIGHT,
    Chip,
    GradientButton,
    OutlineButton,
    PlaceholderEntry,
    Segmented,
    ToggleSwitch,
    Tooltip,
    apply_theme_embedded,
    apply_theme_global,
    blend,
)

import parakit_practice_engine as engine
from parakit_practice_engine import (
    BUILTIN_PRESETS,
    CORE_VOICES,
    DEFAULT_KEYBINDS,
    DEFAULT_PREFS,
    DIFFICULTIES,
    LANE_DEFS,
    PALETTES,
    STANDARD_ORDER,
    build_lane_notes,
    decode_chart_bytes,
    default_layout,
    difficulty_from_filename,
    lane_color,
    parse_rlrr,
    resolve_routing,
    route_for_class,
    song_duration_from_lanes,
    song_key,
    demo_chart,
)

# ---------------------------------------------------------------------------
# Module constants (ported from the v5 home_screen.py / practice_library.py
# headers -- see file docstring for provenance).
# ---------------------------------------------------------------------------
_DIFF_LETTER = {"Easy": "E", "Medium": "M", "Hard": "H", "Expert": "X"}
_COVER = 44
_SORTS: Tuple[Tuple[str, str], ...] = (
    ("title", "Sort: Title"), ("artist", "Sort: Artist"),
    ("complexity", "Sort: Complexity"), ("recent", "Sort: Recent"),
)
#: pre-play speed choices, percent (Kitsmith speed select, verbatim from v5)
_SPEEDS_PCT: Tuple[int, ...] = (50, 60, 70, 75, 80, 85, 90, 95, 100, 105,
                                110, 120, 130, 150)
_KIT_PINNED = "(pinned to this song)"
_KIT_CURRENT = "(current kit)"
_DRUMISH = re.compile(r"drum", re.IGNORECASE)


def _legible_lane_color(hex_color: str, min_l: float = 0.60) -> str:
    """Raise a lane colour's HLS lightness to a floor so it stays readable as
    TEXT on the dark INPUT-row background (ROW_ALT #181830). A solid dot showed
    the raw colour fine, but dark lanes (Tom 1 navy, Tom 3 purple) vanish as
    text -- this lifts them to >=3:1 contrast while preserving hue+saturation
    and leaving already-bright lanes essentially unchanged."""
    try:
        h = hex_color.lstrip("#")
        r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
        hh, ll, ss = colorsys.rgb_to_hls(r, g, b)
        if ll < min_l:
            r, g, b = colorsys.hls_to_rgb(hh, min_l, ss)
        return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))
    except Exception:
        return hex_color
_EMPTY_LIB = ("No library yet -- open your songs folder above, or press "
              "“Play the demo groove” to play instantly.")

#: file-type filters (Kitsmith IMG_EXT / AUDIO_EXT, verbatim from
#: practice_library.py)
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif")
AUDIO_EXT = (".ogg", ".mp3", ".flac", ".wav")

#: punctuation code -> keycap glyph (matches the v5 chip labels)
_CODE_LABELS = {"Semicolon": ";", "Comma": ",", "Period": ".", "Slash": "/",
                "Quote": "'", "Backquote": "`", "BracketLeft": "[",
                "BracketRight": "]", "Backslash": "\\", "Minus": "-",
                "Equal": "="}


def _key_label(code: str) -> str:
    """JS KeyboardEvent.code -> a short keycap label ('KeyA' -> 'A')."""
    if code.startswith("Key"):
        return code[3:]
    if code.startswith("Digit"):
        return code[5:]
    return _CODE_LABELS.get(code, code)


def _fmt_dur(sec: float) -> str:
    s = int(sec or 0)
    return f"{s // 60}:{s % 60:02d}" if s else ""


def _num(v, default: float) -> float:
    """Coerce a persisted pref to a finite float (breaker fix, 2026-07-21): a
    non-numeric or NaN/Inf fallTime/noteSize used to raise out of __init__ and
    crash the whole tab build — the module's contract is degrade, never crash."""
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _hash_hue(title: str) -> int:
    """Kitsmith cover-fallback hash: h = (h*31 + charCode) >>> 0."""
    h = 0
    for ch in title:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return h


def _initials(title: str) -> str:
    words = [w for w in (title or "").split() if w]
    return "".join(w[0] for w in words[:2]).upper() or "?"


def _title_sort_key(e: dict):
    return e["title"].lower()


def _artist_sort_key(e: dict):
    return ((e["artist"] or "~").lower(), e["title"].lower())


def _complexity_sort_key(e: dict):
    return (-e.get("complexity", 0), e["title"].lower())


def _recent_sort_key(e: dict):
    return (-e.get("lastPlayed", 0), e["title"].lower())


_SORT_KEYS = {
    "artist": _artist_sort_key,
    "complexity": _complexity_sort_key,
    "recent": _recent_sort_key,
    "title": _title_sort_key,
}


# ===========================================================================
# Pure library scan -- ported from
# ParaKit-v5-BETA-py-rebuild-phase/.../services/practice_library.py, with
# the QObject worker wrapper stripped (these functions never touch Qt or Tk;
# `PracticeHomeScreen` drives them from a plain threading.Thread below).
# ===========================================================================


def _list_files(folder: str) -> Dict[str, str]:
    """Map of lowercase filename -> absolute path for a folder's plain files."""
    files: Dict[str, str] = {}
    try:
        for name in os.listdir(folder):
            full = os.path.join(folder, name)
            if os.path.isfile(full):
                files[name.lower()] = full
    except OSError:
        pass
    return files


def discover_candidates(root: str) -> List[Tuple[str, str]]:
    """(display_name, folder_path) candidates under `root`, depth <= 2."""
    out: List[Tuple[str, str]] = []
    try:
        top = sorted(os.listdir(root))
    except OSError:
        return out
    loose_rlrr = any(n.lower().endswith(".rlrr")
                     for n in top if os.path.isfile(os.path.join(root, n)))
    for name in top:
        sub = os.path.join(root, name)
        if not os.path.isdir(sub):
            continue
        out.append((name, sub))
        try:
            for inner in sorted(os.listdir(sub)):
                deep = os.path.join(sub, inner)
                if not os.path.isdir(deep):
                    continue
                try:
                    has_rlrr = any(f.lower().endswith(".rlrr")
                                   for f in os.listdir(deep)
                                   if os.path.isfile(os.path.join(deep, f)))
                except OSError:
                    has_rlrr = False
                if has_rlrr:
                    out.append((f"{name}/{inner}", deep))
        except OSError:
            pass
    if loose_rlrr:
        out.append((os.path.basename(root.rstrip("\\/")) or root, root))
    return out


def scan_folder(folder_name: str, folder: str) -> Tuple[Optional[dict], Optional[dict]]:
    """Scan ONE candidate folder -> (entry | None, problem | None). A folder
    with no .rlrr returns (None, None) -- silently ignored. A folder whose
    charts ALL fail to parse returns (None, fatal-problem) -- quarantined."""
    files = _list_files(folder)
    rlrr_names = [n for n in files if n.endswith(".rlrr")]
    if not rlrr_names:
        return None, None

    items: List[str] = []
    diffs: Dict[str, dict] = {}
    diff_files: List[str] = []
    meta: Optional[dict] = None
    extras: set = set()

    for lname in sorted(rlrr_names):
        path = files[lname]
        base = os.path.basename(path)
        d = difficulty_from_filename(base)
        if d is None:
            items.append(f"{base}: no difficulty suffix -- skipped")
            continue
        try:
            with open(path, "rb") as fh:
                chart = parse_rlrr(decode_chart_bytes(fh.read()))
        except Exception as exc:   # noqa: BLE001
            # A deeply-nested chart (RecursionError) or a huge file (MemoryError)
            # is NOT OSError/ValueError, so it used to escape here, unwind past
            # scan_songs_root, and make the whole library scan report failure on
            # ONE bad file (review #10, 2026-07-21). Any parse failure is now a
            # per-file problem; the scan keeps going.
            items.append(f"{base}: {exc}")
            continue
        if meta is None:
            meta = chart["meta"]
        for w in chart["warnings"]:
            items.append(f"{base}: {w}")
        for inst in chart["instruments"]:
            route = route_for_class(inst["class"])
            if route.get("unknown") or route["voice"] not in CORE_VOICES:
                extras.add(inst["class"] or inst["name"])
        diffs[d] = {
            "path": path,
            "complexity": chart["meta"]["complexity"],
            "notes": len(chart["events"]),
            "songTracks": list(chart["audio"]["songTracks"]),
            "drumTracks": list(chart["audio"]["drumTracks"]),
        }
        diff_files.append(base)

    if not diffs:
        items = items or ["no parseable difficulty charts"]
        return None, {"folder": folder_name, "items": items, "fatal": True}

    meta = meta or {}
    title = meta.get("title") or folder_name
    artist = meta.get("artist") or ""

    cover_path = ""
    cover_name = (meta.get("coverImagePath") or "").lower()
    if cover_name and cover_name in files:
        cover_path = files[cover_name]
    else:
        for lname in sorted(files):
            if lname.endswith(IMG_EXT):
                cover_path = files[lname]
                break

    audio_refs = {os.path.basename(files[lname]): files[lname]
                  for lname in sorted(files) if lname.endswith(AUDIO_EXT)}

    entry = {
        "folderName": folder_name,
        "folder": folder,
        "title": title,
        "artist": artist,
        "creator": meta.get("creator") or "",
        "complexity": max(d["complexity"] for d in diffs.values()),
        "lengthS": meta.get("lengthS") or 0,
        "diffs": diffs,
        "audioRefs": audio_refs,
        "coverPath": cover_path,
        "key": song_key(title, artist, diff_files),
        "problems": items,
        "extras": sorted(extras),
        "lastPlayed": 0,
    }
    problem = ({"folder": folder_name, "items": items, "fatal": False}
               if items else None)
    return entry, problem


def scan_songs_root(
    root: str,
    progress: Optional[Callable[[int, int], None]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
) -> Tuple[List[dict], List[dict]]:
    """Scan the whole songs root -> (entries sorted by title, problems)."""
    entries: List[dict] = []
    problems: List[dict] = []
    candidates = discover_candidates(root)
    total = len(candidates)
    for i, (name, folder) in enumerate(candidates):
        if cancelled is not None and cancelled():
            break
        try:
            entry, problem = scan_folder(name, folder)
        except Exception as e:   # noqa: BLE001 -- belt-and-suspenders (review #10)
            # Even if scan_folder itself blows up anywhere (not just the parse),
            # quarantine THAT folder and keep scanning the rest of the library.
            entry, problem = None, {"folder": name, "items": [str(e)], "fatal": True}
        if entry is not None:
            entries.append(entry)
        if problem is not None:
            problems.append(problem)
        if progress is not None:
            progress(i + 1, total)
    entries.sort(key=_title_sort_key)
    return entries, problems


def _scan_thread_main(root: str, q: "queue.Queue", cancel_event: threading.Event) -> None:
    """No widget access -- runs on the worker thread. Mirrors
    parakit_spectral_tab.py's `_compare_worker` staticmethod."""
    try:
        entries, problems = scan_songs_root(
            root,
            progress=functools.partial(_report_progress, q),
            cancelled=cancel_event.is_set)
        q.put(("done", entries, problems))
    except Exception as e:  # noqa: BLE001 -- report any scan failure
        q.put(("error", str(e)))


def _report_progress(q: "queue.Queue", cur: int, total: int) -> None:
    q.put(("progress", cur, total))


# ===========================================================================
# Cover art -- a 44x44 Canvas cell.
# Prefer the host `load_cover` hook when present; otherwise the module-level
# `load_cover` helper (PIL → PhotoImage, tk.PhotoImage PNG/GIF fallback).
# Any failure falls through to the gradient + initials placeholder (v5 parity).
# ===========================================================================

# (path, size) -> PhotoImage. Cap keeps memory bounded for large libraries.
_COVER_CACHE: Dict[Tuple[str, int], Any] = {}
_COVER_CACHE_MAX = 200


def load_cover(path: str, size: int):
    """Load album art as a Tk PhotoImage scaled to `size` x `size`.

    Tries Pillow first (any format Pillow can open), then `tk.PhotoImage`
    (PNG/GIF only). Returns None on any failure so the caller can draw the
    initials placeholder. Cached by (abspath, size), hard-capped at ~200.
    Callers MUST keep a live reference to the returned PhotoImage (Tk GC).
    """
    if not path or not isinstance(path, str):
        return None
    try:
        if not os.path.isfile(path):
            return None
    except (OSError, TypeError, ValueError):
        return None
    try:
        key = (os.path.normcase(os.path.abspath(path)), int(size))
    except (OSError, TypeError, ValueError):
        return None
    cached = _COVER_CACHE.get(key)
    if cached is not None:
        return cached

    img = None
    # --- Pillow path (preferred; any common image format) ---
    try:
        from PIL import Image, ImageTk  # type: ignore
        with Image.open(path) as im:
            im = im.convert("RGBA")
            # thumbnail mutates in place, preserves aspect, fits in box.
            try:
                resample = Image.Resampling.LANCZOS
            except AttributeError:  # older Pillow
                resample = getattr(Image, "LANCZOS", Image.BICUBIC)
            im.thumbnail((int(size), int(size)), resample)
            img = ImageTk.PhotoImage(im)
    except ImportError:
        # --- stdlib tk.PhotoImage (PNG / GIF only) ---
        try:
            ph = tk.PhotoImage(file=path)
            w, h = ph.width(), ph.height()
            if w > 0 and h > 0 and (w > size or h > size):
                factor = max(1, int(math.ceil(max(w / float(size),
                                                  h / float(size)))))
                if factor > 1:
                    ph = ph.subsample(factor, factor)
            img = ph
        except Exception:  # noqa: BLE001 -- any decode failure -> None
            img = None
    except Exception:  # noqa: BLE001 -- PIL open/convert failure -> None
        img = None

    if img is not None:
        # Cap: drop an arbitrary oldest entry (dict preserves insert order).
        while len(_COVER_CACHE) >= _COVER_CACHE_MAX:
            try:
                _COVER_CACHE.pop(next(iter(_COVER_CACHE)))
            except StopIteration:
                break
        _COVER_CACHE[key] = img
    return img


class _CoverArt(tk.Canvas):
    def __init__(self, parent, title: str, path: str, hook_call, size: int = _COVER):
        try:
            bg = parent.cget("background")
        except tk.TclError:
            bg = PANEL
        super().__init__(parent, width=size, height=size,
                         highlightthickness=0, bd=0, background=bg)
        self._img_ref = None   # keep a live reference -- PhotoImage GC trap
        drawn = False
        if path:
            img = None
            # Host hook wins when it returns a usable image; else local helper.
            ok, hook_img = hook_call("load_cover", path, size)
            if ok and hook_img is not None:
                img = hook_img
            else:
                img = load_cover(path, size)
            if img is not None:
                try:
                    self.create_image(size / 2, size / 2, image=img)
                    self._img_ref = img
                    drawn = True
                except tk.TclError:
                    drawn = False
        if not drawn:
            self._draw_placeholder(title, size)

    def _draw_placeholder(self, title: str, size: int) -> None:
        pal = PALETTES.get("forge") or ["#7c3aed"]
        accent = pal[_hash_hue(title) % len(pal)]
        bands = 8
        for i in range(bands):
            t = i / max(1, bands - 1)
            color = blend(blend(PANEL, accent, 0.35), PANEL2, t)
            x0 = size * i / bands
            x1 = size * (i + 1) / bands
            self.create_rectangle(x0, 0, x1, size, fill=color, outline="")
        self.create_text(size / 2, size / 2, text=_initials(title),
                         fill=TEXT_BRIGHT,
                         font=("Segoe UI", max(8, int(size * 0.30)), "bold"))


def _bind_click_recursive(widget: tk.Widget, handler) -> None:
    """Bind <Button-1> on `widget` and every descendant so clicking anywhere
    in a composite row triggers the same row-click handler (Tk has no single
    'row widget' the way a QFrame subclass with mousePressEvent does)."""
    widget.bind("<Button-1>", handler, add="+")
    for child in widget.winfo_children():
        _bind_click_recursive(child, handler)


# ===========================================================================
# PracticeHomeScreen
# ===========================================================================
class PracticeHomeScreen(ttk.Frame):
    """The Practice tab's Home / library screen. Composes the SONG card
    (library + inline pre-play + single-chart loader), the SETUP card, and
    the INPUT card under a topbar. View + local session-config building;
    playback, Settings, Kit Studio, and calibration all live outside this
    screen and are reached through the four locked `self.on_*` callbacks."""

    def __init__(self, parent, hooks=None, **kw):
        super().__init__(parent, style="Prac.TFrame", **kw)
        self.hooks = hooks if hooks is not None else {}

        # ----- locked Home<->tab signal contract (DISPATCH section 5) -----
        self.on_play_request: Optional[Callable[[dict], None]] = None
        self.on_open_settings: Optional[Callable[[Optional[str]], None]] = None
        self.on_open_kit_studio: Optional[Callable[[], None]] = None
        self.on_open_calibration: Optional[Callable[[], None]] = None
        self.on_keyboard_test: Optional[Callable[[], None]] = None

        # ----- persisted state (via the hook seam) -------------------------
        stored_prefs = self._cfg_get("practice_prefs", {})
        if not isinstance(stored_prefs, dict):
            stored_prefs = {}
        self._prefs: Dict[str, Any] = dict(DEFAULT_PREFS)
        self._prefs.update(stored_prefs)

        self._songs_folder = self._cfg_get("practice_songs_folder", "")
        if not isinstance(self._songs_folder, str):
            self._songs_folder = ""

        self._sort = self._cfg_get("sort", "title")
        if self._sort not in _SORT_KEYS:
            self._sort = "title"

        records = self._cfg_get("practice_records", {})
        self._records: Dict[str, dict] = records if isinstance(records, dict) else {}

        # ----- library state -------------------------------------------------
        self._entries: List[dict] = []
        self._problems: List[dict] = []
        self._problems_open = False
        self._selected_key: Optional[str] = None
        self._preplay_open = False
        self._pp_state: Dict[str, dict] = {}    # per-song ephemeral pre-play choices
        self._kit_pins: set = set()
        self._kit_presets: List[str] = []

        # ----- single-chart loader state --------------------------------------
        self._single_chart: Optional[dict] = None
        self._single_chart_path = ""
        self._single_audio_paths: List[str] = []

        # ----- scan-thread state (mirrors spectral's compare-thread fields) ---
        self._scan_thread: Optional[threading.Thread] = None
        self._scan_queue: Optional["queue.Queue"] = None
        self._scan_cancel: Optional[threading.Event] = None
        self._scanning = False
        self._poll_scan_job = None
        self._filter_job = None

        # ----- root-level key binds (the '/' search shortcut) -----------------
        self._key_binds: List[Tuple[tk.Misc, str, str]] = []

        if self.hooks:
            apply_theme_embedded(self)

        self._build()
        self._bind_keys()

        if self._songs_folder and os.path.isdir(self._songs_folder):
            self.set_folder_label(self._songs_folder)
            self._start_scan(self._songs_folder)
        else:
            self.set_folder_label("")

    # =======================================================================
    # Hook seam -- verbatim pattern from parakit_spectral_tab.py lines
    # 2092-2126 (DISPATCH section 1): missing hook OR exception -> a clean
    # degrade + a one-line status note, never a crash.
    # =======================================================================
    def _cfg_get(self, key: str, default=None):
        if "get_cfg" not in self.hooks:
            return default
        try:
            val = self.hooks["get_cfg"](key, default)
        except Exception as e:
            self._note(f"config read failed: {e}", AMBER)
            return default
        if default is not None and val is not None \
                and not isinstance(val, type(default)):
            return default
        return val

    def _cfg_set(self, key: str, value) -> None:
        if "set_cfg" not in self.hooks:
            return
        try:
            self.hooks["set_cfg"](key, value)
        except Exception as e:
            self._note(f"config save failed: {e}", AMBER)

    def _hook_call(self, name: str, *args, **kwargs) -> Tuple[bool, Any]:
        """Call a hook by name, returning (ok, result). Missing hook or
        exception -> (False, None); never raises into the caller."""
        if name not in self.hooks:
            return (False, None)
        try:
            return (True, self.hooks[name](*args, **kwargs))
        except Exception as e:
            self._note(f"{name} hook failed: {e}", AMBER)
            return (False, None)

    def _emit(self, attr_name: str, *args) -> None:
        """Call one of the four locked self.on_* callbacks. Unset -> a status
        note (the seam is exercised in the standalone host by stub prints,
        never a crash if the router hasn't wired it yet)."""
        fn = getattr(self, attr_name, None)
        if fn is None:
            self._note(f"{attr_name} is not wired yet (stub).", MUTED)
            return
        try:
            fn(*args)
        except Exception as e:
            self._note(f"{attr_name} failed: {e}", AMBER)

    def _note(self, text: str, color: str = MUTED) -> None:
        # Tolerate being called BEFORE the topbar is built (breaker fix r10,
        # 2026-07-21): __init__ reads config before _build() creates note_lbl, so
        # a RAISING get_cfg reached _note -> note_lbl AttributeError crashed the
        # whole tab build. Contract is degrade, never crash.
        lbl = getattr(self, "note_lbl", None)
        if lbl is None:
            return
        try:
            lbl.configure(text=text, foreground=color)
        except tk.TclError:
            pass   # destroyed/mid-teardown screen

    # =======================================================================
    # Build
    # =======================================================================
    def _build(self) -> None:
        self._build_topbar()   # fixed at the top (never scrolls)

        # Scroll container (v4.9.2): SONG + SETUP + INPUT stacked can exceed a
        # 1080p window and clip the bottom (the keybind card was below the fold).
        # Wrap the body in a vertical scroll canvas so nothing is ever cut off on
        # any monitor. The scrollbar is the guarantee; the mouse wheel is routed
        # by pointer (song list vs the whole page) in _on_page_wheel.
        outer = tk.Frame(self, background=BG)
        outer.pack(fill=tk.BOTH, expand=True)
        self._page_canvas = tk.Canvas(outer, background=BG, highlightthickness=0)
        page_vsb = ttk.Scrollbar(outer, orient=tk.VERTICAL,
                                 command=self._page_canvas.yview)
        self._page_canvas.configure(yscrollcommand=page_vsb.set)
        page_vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._page_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        body = tk.Frame(self._page_canvas, background=BG)
        self._page_window = self._page_canvas.create_window(
            (0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda _e: self._page_canvas.configure(
            scrollregion=self._page_canvas.bbox("all")))
        self._page_canvas.bind(
            "<Configure>", lambda e: self._page_canvas.itemconfigure(
                self._page_window, width=e.width))   # body fills canvas width
        # Retain the funcids so destroy() can remove ONLY these global handlers
        # (breaker codex #1, 2026-07-22): bind_all persists on the "all" bindtag,
        # so without cleanup each destroy/rebuild leaks another live _on_page_wheel
        # (and pins this screen in memory). unbind_all is NOT an option — the host
        # app has its own global wheel handler on "all" that must survive.
        self._page_wheel_binds = []
        for _seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self._page_wheel_binds.append(
                (_seq, self._page_canvas.bind_all(_seq, self._on_page_wheel,
                                                  add="+")))

        # fill=X (NOT expand): in the scroll body the grid takes its NATURAL
        # height, so the song list stays at its bounded 340 px (internal scroll)
        # instead of growing 133-songs tall and making the page enormous.
        grid = ttk.Frame(body, style="Prac.TFrame")
        grid.pack(fill=tk.X, padx=14, pady=(0, 10))
        grid.columnconfigure(0, weight=5)
        grid.columnconfigure(1, weight=4)
        grid.rowconfigure(0, weight=1)
        song_card = self._build_song_card(grid)
        song_card.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        setup_card = self._build_setup_card(grid)
        setup_card.grid(row=0, column=1, sticky="new", padx=(7, 0))
        input_card = self._build_input_card(body)
        input_card.pack(fill=tk.X, padx=14, pady=(0, 14))
        self._rebuild_list()
        self._update_play_loaded()

    def _on_page_wheel(self, event) -> None:
        # Route the wheel by pointer: over the song list -> scroll the list (it
        # has its own internal scroll); anywhere else on the page -> scroll the
        # whole page. Guarded to no-op when the Practice tab is not the visible
        # notebook page (bind_all fires app-wide).
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
        target = self._page_canvas
        try:
            n = self.winfo_containing(event.x_root, event.y_root)
        except tk.TclError:
            n = None
        while n is not None:
            if n is getattr(self, "_list_canvas", None):
                target = self._list_canvas
                break
            n = getattr(n, "master", None)
        try:
            target.yview_scroll(delta, "units")
        except tk.TclError:
            pass

    # ----- small factories (mirrors the v5 _h2/_eyebrow/_hint/_sep helpers) ---
    @staticmethod
    def _h2(parent, text: str) -> ttk.Label:
        return ttk.Label(parent, text=text, style="Prac.Header.TLabel")

    @staticmethod
    def _eyebrow(parent, text: str) -> ttk.Label:
        return ttk.Label(parent, text=text, style="Prac.Cyan.TLabel", font=F_SMALL)

    @staticmethod
    def _hint(parent, text: str, wraplength: int = 520) -> ttk.Label:
        return ttk.Label(parent, text=text, style="Prac.PanelMuted.TLabel",
                         font=F_SMALL, wraplength=wraplength, justify=tk.LEFT)

    @staticmethod
    def _sep(parent) -> ttk.Separator:
        return ttk.Separator(parent, orient=tk.HORIZONTAL)

    # ----- topbar -----------------------------------------------------------
    def _build_topbar(self) -> None:
        bar = tk.Frame(self, background=BG)
        bar.pack(fill=tk.X, padx=14, pady=(12, 8))

        word = tk.Frame(bar, background=BG)
        word.pack(side=tk.LEFT)
        mark = tk.Frame(word, background=BG)
        mark.pack(anchor=tk.W)
        tk.Label(mark, text="P R A C T I C E ", background=BG,
                foreground=TEXT_BRIGHT, font=F_H1).pack(side=tk.LEFT)
        tk.Label(mark, text="V3", background=BG, foreground=EMBER,
                font=F_H1).pack(side=tk.LEFT)
        tk.Label(word, text="PARAKIT DRUM TRAINER · PLAY + KIT STUDIO",
                background=BG, foreground=MUTED_LAV,
                font=F_SMALL).pack(anchor=tk.W)

        right = tk.Frame(bar, background=BG)
        right.pack(side=tk.RIGHT)
        self.settings_btn = OutlineButton(right, "Settings", accent=PURPLE_EDGE,
                                          command=functools.partial(self._emit, "on_open_settings", None))
        self.settings_btn.pack(side=tk.RIGHT)
        self.folder_chip = self._make_chip(right, "Folder: none", "warn")
        self.folder_chip.pack(side=tk.RIGHT, padx=(0, 8))
        self.midi_chip = self._make_chip(right, "MIDI: none", "warn")
        self.midi_chip.pack(side=tk.RIGHT, padx=(0, 8))

        self.note_lbl = ttk.Label(self, text="", style="Prac.Muted.TLabel",
                                  font=F_SMALL)
        self.note_lbl.pack(fill=tk.X, padx=14, pady=(0, 6), anchor=tk.W)

    @staticmethod
    def _make_chip(parent, text: str, state: str, led: Optional[str] = None) -> tk.Frame:
        """Pill chip: PANEL2 body, lavender text, LED canvas circle for state.

        `led=` optional explicit LED colour; default amber, green when state
        indicates ready/ok. Leading '● ' in text is stripped (LED replaces it).
        """
        led_colors = {"ok": "#00ff88", "warn": "#ffb347", "off": MUTED}
        led_c = led if led is not None else led_colors.get(state, "#ffb347")
        edge = blend(EMBER, BG, 0.6)
        display = text[1:].lstrip() if text.startswith("●") else text

        chip = tk.Frame(parent, background=PANEL2, highlightthickness=1,
                        highlightbackground=edge, highlightcolor=edge)
        led_cv = tk.Canvas(chip, width=9, height=9, background=PANEL2,
                           highlightthickness=0, bd=0)
        led_cv.pack(side=tk.LEFT, padx=(7, 0), pady=4)
        led_id = led_cv.create_oval(1, 1, 8, 8, fill=led_c, outline="")
        lbl = tk.Label(chip, text=display, background=PANEL2,
                       foreground=MUTED_LAV, font=F_SMALL)
        lbl.pack(side=tk.LEFT, padx=(5, 8), pady=3)
        # Stash children so _set_chip can restyle without rebuilding.
        chip._led_cv = led_cv  # type: ignore[attr-defined]
        chip._led_id = led_id  # type: ignore[attr-defined]
        chip._lbl = lbl        # type: ignore[attr-defined]
        chip._edge = edge      # type: ignore[attr-defined]
        return chip

    def _set_chip(self, chip: tk.Frame, text: str, state: str,
                  led: Optional[str] = None) -> None:
        led_colors = {"ok": "#00ff88", "warn": "#ffb347", "off": MUTED}
        led_c = led if led is not None else led_colors.get(state, "#ffb347")
        display = text[1:].lstrip() if text.startswith("●") else text
        try:
            chip._lbl.configure(text=display, foreground=MUTED_LAV)  # type: ignore[attr-defined]
            cv = chip._led_cv  # type: ignore[attr-defined]
            cv.itemconfigure(chip._led_id, fill=led_c)  # type: ignore[attr-defined]
        except (tk.TclError, AttributeError):
            # Fallback for any legacy Label chips that might still exist.
            try:
                chip.configure(text=text)  # type: ignore[call-arg]
            except tk.TclError:
                pass

    def set_folder_label(self, path: str) -> None:
        if path:
            self._set_chip(self.folder_chip, f"Folder: {os.path.basename(path.rstrip(chr(92)+'/')) or path}", "ok")
            self.rescan_btn.pack(side=tk.LEFT, padx=(6, 0))
        else:
            self._set_chip(self.folder_chip, "Folder: none", "warn")
            self.rescan_btn.pack_forget()

    # ----- SONG card ----------------------------------------------------------
    def _build_song_card(self, parent) -> tk.Frame:
        card = tk.Frame(parent, background=PANEL, highlightthickness=1,
                        highlightbackground="#463a6b")   # v4.9.2 de-barren:
        # brighter card edge so each section (SONG / SETUP / INPUT) reads as a
        # distinct card against the deep bg, matching the v3 web reference (the
        # old PURPLE_DEEP #2a1235 border was too dark to see).
        pad = tk.Frame(card, background=PANEL)
        pad.pack(fill=tk.BOTH, expand=True, padx=16, pady=14)

        self._h2(pad, "SONG").pack(anchor=tk.W, pady=(0, 8))

        play_row = tk.Frame(pad, background=PANEL)
        play_row.pack(fill=tk.X)
        # Filled primary gradient (deep purple) -- starts disabled until a
        # chart loads; _update_play_loaded toggles via .set_enabled().
        self.play_loaded_btn = GradientButton(
            play_row, "▶  Play Loaded Song",
            command=self._on_play_loaded,
            stops=("#8B4DF7", "#7A3BE8", "#6D2BD9"),
            height=36,
            tooltip="Play the selected library song at your chosen "
                    "difficulty -- or the loaded single chart if no library "
                    "song is selected.")
        self.play_loaded_btn.set_enabled(False)
        self.play_loaded_btn.pack(side=tk.LEFT)
        # Matches Play Loaded Song's face exactly (owner-directed 2026-07-23):
        # same purple gradient stops + height, so the two buttons read as a pair.
        self.demo_btn = GradientButton(
            play_row, "▶ Play the demo groove",
            command=self._on_demo,
            stops=("#8B4DF7", "#7A3BE8", "#6D2BD9"),
            height=36,
            tooltip="A built-in chart with a fully synthesized backing "
                    "track -- plays instantly, no files needed.")
        self.demo_btn.pack(side=tk.LEFT, padx=(8, 0))
        # Loaded/queued-song readout (v4.9.2): shows WHICH song "Play Loaded
        # Song" will start, so the user never launches the wrong one by mistake.
        # Only shown when a library song is selected or a single chart is loaded;
        # hidden otherwise. Packed on demand by _update_play_loaded.
        self.loaded_readout = tk.Label(
            pad, text="", background=PANEL, foreground=CYAN,
            font=F_SMALL, anchor=tk.W, justify=tk.LEFT)
        self._loaded_hint = self._hint(
            pad, "“Play Loaded Song” uses the song you pick "
                 "below (or a single chart you load); the demo needs "
                 "no files.")
        self._loaded_hint.pack(anchor=tk.W, pady=(6, 8))
        self._sep(pad).pack(fill=tk.X, pady=(0, 8))

        self._eyebrow(pad, "YOUR SONGS FOLDER").pack(anchor=tk.W)
        folder_row = tk.Frame(pad, background=PANEL)
        folder_row.pack(fill=tk.X, pady=(4, 0))
        # Filled-ember look (reference primary fill) via OutlineButton.filled.
        self.pick_folder_btn = OutlineButton(
            folder_row, "Open songs folder…", accent=PURPLE_EDGE,
            command=self._on_pick_folder, filled=True,
            fill_bg="#6D2BD9", fill_fg=TEXT_BRIGHT, fill_hover="#7C3AED")
        self.pick_folder_btn.pack(side=tk.LEFT)
        self.rescan_btn = OutlineButton(
            folder_row, "Rescan", accent=CYAN, command=self._on_rescan)
        # packed on demand by set_folder_label()

        self.scan_status = self._hint(
            pad, "Pick your Paradiddle songs folder -- one subfolder per song.")
        self.scan_status.pack(anchor=tk.W, pady=(6, 8))

        controls = tk.Frame(pad, background=PANEL)
        controls.pack(fill=tk.X, pady=(0, 6))
        self.search_entry = PlaceholderEntry(
            controls, placeholder="Search title / artist / creator…  ( / )",
            width=30)
        self.search_entry.entry.bind("<KeyRelease>", self._on_search_changed)
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.sort_combo = ttk.Combobox(controls, style="Prac.TCombobox",
                                       state="readonly", width=16,
                                       values=[label for _, label in _SORTS])
        idx = [k for k, _ in _SORTS].index(self._sort)
        self.sort_combo.current(idx)
        self.sort_combo.bind("<<ComboboxSelected>>", self._on_sort_changed)
        self.sort_combo.pack(side=tk.LEFT, padx=(8, 0))
        self.count_lbl = ttk.Label(controls, text="0 songs",
                                   style="Prac.PanelMuted.TLabel", font=F_SMALL)
        self.count_lbl.pack(side=tk.LEFT, padx=(10, 0))

        # List field uses the neutral page-black (BG), not DARKER (which has a
        # magenta cast) — matches the Audio->MIDI Song Library's flatter
        # near-black (owner-reported: Practice library read too purple).
        list_frame = tk.Frame(pad, background=BG, highlightthickness=1,
                              highlightbackground=SEPARATOR)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 6))
        self._list_canvas = tk.Canvas(list_frame, background=BG,
                                      highlightthickness=0, height=340)
        vsb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL,
                            style="Prac.Vertical.TScrollbar",
                            command=self._list_canvas.yview)
        self._list_canvas.configure(yscrollcommand=vsb.set)
        self._list_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._list_body = tk.Frame(self._list_canvas, background=BG)
        self._list_window = self._list_canvas.create_window(
            (0, 0), window=self._list_body, anchor="nw")
        self._list_body.bind("<Configure>", self._on_list_body_configure)
        self._list_canvas.bind("<Configure>", self._on_list_canvas_configure)
        # (Wheel is routed centrally by _on_page_wheel, which scrolls the list
        # when the pointer is over it and the whole page otherwise — v4.9.2.)

        self.problems_btn = Chip(pad, "", accent=AMBER, on=False,
                                 command=self._on_problems_toggled)
        self.problems_lbl = self._hint(pad, "")
        # packed on demand by _update_problems_drawer()

        self._sep(pad).pack(fill=tk.X, pady=(8, 8))
        self._eyebrow(pad, "OR LOAD A SINGLE CHART").pack(anchor=tk.W)

        single1 = tk.Frame(pad, background=PANEL)
        single1.pack(fill=tk.X, pady=(6, 0))
        self.load_rlrr_btn = OutlineButton(single1, "Choose .rlrr",
                                           accent=PURPLE_EDGE,
                                           command=self._on_load_rlrr)
        self.load_rlrr_btn.pack(side=tk.LEFT)
        self.single_hint = tk.Label(single1, text="no chart loaded",
                                    background=PANEL, foreground=MUTED,
                                    font=F_MONO)
        self.single_hint.pack(side=tk.LEFT, padx=(8, 0))

        single2 = tk.Frame(pad, background=PANEL)
        single2.pack(fill=tk.X, pady=(6, 0))
        self.load_audio_btn = OutlineButton(
            single2, "Add audio (Full Mix / Stem)", accent=PURPLE_EDGE,
            command=self._on_load_single_audio)
        self.load_audio_btn.pack(side=tk.LEFT)
        self.single_audio_hint = tk.Label(single2, text="— synth used if none",
                                          background=PANEL, foreground=MUTED,
                                          font=F_MONO)
        self.single_audio_hint.pack(side=tk.LEFT, padx=(8, 0))

        single3 = tk.Frame(pad, background=PANEL)
        single3.pack(fill=tk.X, pady=(8, 0))
        self.play_single_btn = OutlineButton(
            single3, "▶ Play loaded chart", accent=MAGENTA,
            command=self._on_play_single, enabled=False)
        self.play_single_btn.pack(side=tk.LEFT)
        self._hint(pad, "Load a single Paradiddle .rlrr (and optionally its "
                       "audio), then Play -- no folder needed.",
                  wraplength=300).pack(side=tk.LEFT, padx=(8, 0))
        return card

    def _on_list_body_configure(self, _event=None) -> None:
        try:
            self._list_canvas.configure(scrollregion=self._list_canvas.bbox("all"))
        except tk.TclError:
            pass

    def _on_list_canvas_configure(self, event) -> None:
        try:
            self._list_canvas.itemconfigure(self._list_window, width=event.width)
        except tk.TclError:
            pass

    def _on_mousewheel(self, event) -> None:
        delta = 0
        if getattr(event, "num", None) == 4:
            delta = -1
        elif getattr(event, "num", None) == 5:
            delta = 1
        elif getattr(event, "delta", 0):
            delta = -1 if event.delta > 0 else 1
        if delta:
            try:
                self._list_canvas.yview_scroll(delta, "units")
            except tk.TclError:
                pass

    # ----- SETUP card ---------------------------------------------------------
    def _build_setup_card(self, parent) -> tk.Frame:
        card = tk.Frame(parent, background=PANEL, highlightthickness=1,
                        highlightbackground="#463a6b")   # v4.9.2 de-barren:
        # brighter card edge so each section (SONG / SETUP / INPUT) reads as a
        # distinct card against the deep bg, matching the v3 web reference (the
        # old PURPLE_DEEP #2a1235 border was too dark to see).
        pad = tk.Frame(card, background=PANEL)
        pad.pack(fill=tk.BOTH, expand=True, padx=16, pady=14)
        self._h2(pad, "SETUP").pack(anchor=tk.W, pady=(0, 8))

        self.auto_kick_cb = self._switch_row(
            pad, "Auto-kick", "(auto-hits kick notes)", "autoKick")
        self.square_cb = self._switch_row(
            pad, "Square notes", "(all lanes as bars)", "square")
        self.kick_line_cb = self._switch_row(
            pad, "Kick as full-width line", "", "kickLine")
        self.beat_grid_cb = self._switch_row(
            pad, "Beat-grid lines", "", "beatGrid")
        # "Classic Style notes" toggle removed 2026-07-23 (owner): note shapes
        # are owned by Kit Studio now, and the switch didn't refresh the live
        # highway mid-session anyway. Pref key stays in DEFAULT_PREFS (defaults
        # off) so existing configs and the renderer plumbing load unchanged.

        self.fall_slider, self.fall_val = self._slider_row(
            pad, "Fall time", 0.8, 8.0, self._on_fall_time,
            _num(self._prefs.get("fallTime", 2.6), 2.6), "{:.1f} s")
        self.size_slider, self.size_val = self._slider_row(
            pad, "Note size", 0.5, 2.0, self._on_note_size,
            _num(self._prefs.get("noteSize", 1.0), 1.0), "{:g}×", reset=1.0)

        btns = tk.Frame(pad, background=PANEL)
        btns.pack(fill=tk.X, pady=(10, 0))
        OutlineButton(btns, "Calibrate latency…", accent=PURPLE_EDGE,
                     command=functools.partial(self._emit, "on_open_calibration")
                     ).pack(side=tk.LEFT)
        OutlineButton(btns, "Kit Studio →", accent=PURPLE_EDGE,
                     command=functools.partial(self._emit, "on_open_kit_studio")
                     ).pack(side=tk.LEFT, padx=(8, 0))

        self._hint(pad, "Kit Studio: rearrange lanes, set per-lane color / "
                       "shape / width, add aux lanes, lefty flip. These "
                       "options also live on the in-play Live settings dock "
                       "(toggle with H).").pack(anchor=tk.W, pady=(10, 0))
        return card

    def _switch_row(self, parent, label: str, hint: str, pref_key: str) -> ToggleSwitch:
        row = tk.Frame(parent, background=PANEL)
        row.pack(fill=tk.X, pady=3)
        tk.Label(row, text=label, background=PANEL, foreground=TEXT,
                font=F_BASE).pack(side=tk.LEFT)
        if hint:
            tk.Label(row, text=" " + hint, background=PANEL, foreground=MUTED,
                    font=F_SMALL).pack(side=tk.LEFT)
        sw = ToggleSwitch(row, on=bool(self._prefs.get(pref_key, False)),
                          command=functools.partial(self._on_setup_toggle, pref_key),
                          background=PANEL)
        sw.pack(side=tk.RIGHT)
        return sw

    def _on_setup_toggle(self, pref_key: str, value: bool) -> None:
        self._set_pref(pref_key, value)

    def _slider_row(self, parent, label: str, lo: float, hi: float,
                    on_change, initial: float, fmt: str, reset=None):
        row = tk.Frame(parent, background=PANEL)
        row.pack(fill=tk.X, pady=(8, 0))
        tk.Label(row, text=label, background=PANEL, foreground=TEXT,
                font=F_BASE).pack(side=tk.LEFT)
        val_lbl = tk.Label(row, text=fmt.format(initial), background=PANEL,
                           foreground=CYAN, font=F_BOLD, width=7, anchor=tk.E)
        val_lbl.pack(side=tk.RIGHT)
        var = tk.DoubleVar(value=initial)
        scale = ttk.Scale(row, from_=lo, to=hi, orient=tk.HORIZONTAL,
                          variable=var, style="Prac.Horizontal.TScale",
                          command=functools.partial(self._on_scale_move, var, val_lbl, fmt, on_change))
        scale.pack(side=tk.LEFT, fill=tk.X, expand=True,
                   padx=(10, 6 if reset is not None else 10))
        # v4.9.1 (owner) — optional reset-to-default button next to the slider.
        if reset is not None:
            def _do_reset(_v=reset):
                var.set(_v)
                val_lbl.configure(text=fmt.format(_v))
                on_change(_v)
            OutlineButton(row, "↺", accent=PURPLE_EDGE, command=_do_reset,
                          tooltip="Reset to default (%s)" % fmt.format(reset)
                          ).pack(side=tk.RIGHT, padx=(0, 6))
        return scale, val_lbl

    def _on_scale_move(self, var, val_lbl, fmt, on_change, _value) -> None:
        v = var.get()
        val_lbl.configure(text=fmt.format(v))
        on_change(v)

    def _on_fall_time(self, v: float) -> None:
        self._set_pref("fallTime", round(v, 2))

    def _on_note_size(self, v: float) -> None:
        self._set_pref("noteSize", round(v, 2))

    def _set_pref(self, key: str, value) -> None:
        self._prefs[key] = value
        self._cfg_set("practice_prefs", self._prefs)

    # ----- INPUT card ----------------------------------------------------------
    def _build_input_card(self, parent) -> tk.Frame:
        card = tk.Frame(parent, background=PANEL, highlightthickness=1,
                        highlightbackground="#463a6b")   # v4.9.2 de-barren:
        # brighter card edge so each section (SONG / SETUP / INPUT) reads as a
        # distinct card against the deep bg, matching the v3 web reference (the
        # old PURPLE_DEEP #2a1235 border was too dark to see).
        pad = tk.Frame(card, background=PANEL)
        pad.pack(fill=tk.BOTH, expand=True, padx=16, pady=14)
        self._h2(pad, "INPUT").pack(anchor=tk.W, pady=(0, 8))

        row = tk.Frame(pad, background=PANEL)
        row.pack(fill=tk.X)
        self.midi_chip_home = self._make_chip(row, "MIDI: none", "warn")
        self.midi_chip_home.pack(side=tk.LEFT)
        OutlineButton(row, "Enable MIDI", accent=PURPLE_EDGE,
                     command=self._on_enable_midi).pack(side=tk.LEFT, padx=(8, 0))
        OutlineButton(row, "MIDI device + advanced…", accent=PURPLE_EDGE,
                     command=functools.partial(self._emit, "on_open_settings", "input")
                     ).pack(side=tk.LEFT, padx=(8, 0))
        OutlineButton(row, "Keyboard / MIDI test…", accent=PURPLE_EDGE,
                     tooltip=("Open the input tester: hit your bound keys (and "
                              "MIDI pads, when connected) and see which "
                              "instrument each one triggers."),
                     command=functools.partial(self._emit, "on_keyboard_test")
                     ).pack(side=tk.LEFT, padx=(8, 0))
        self._hint(row, "Keyboard works out of the box · Shift = accent",
                  wraplength=260).pack(side=tk.LEFT, padx=(10, 0))

        self._hint(pad, "Each lane and the key(s) bound to it -- click any "
                       "chip to rebind in Settings.").pack(anchor=tk.W,
                                                            pady=(8, 8))

        self._kb_grid = tk.Frame(pad, background=PANEL)
        self._kb_grid.pack(fill=tk.X)
        self._build_keybind_grid(list(DEFAULT_KEYBINDS))
        return card

    def _build_keybind_grid(self, binds: List[dict]) -> None:
        for child in self._kb_grid.winfo_children():
            child.destroy()
        layout = default_layout()
        for i, lane_id in enumerate(STANDARD_ORDER):
            chip = tk.Frame(self._kb_grid, background=ROW_ALT,
                            highlightthickness=1, highlightbackground=SEPARATOR)
            chip.grid(row=i // 2, column=i % 2, sticky="ew", padx=3, pady=3)
            self._kb_grid.columnconfigure(0, weight=1)
            self._kb_grid.columnconfigure(1, weight=1)
            inner = tk.Frame(chip, background=ROW_ALT)
            inner.pack(fill=tk.X, padx=9, pady=6)
            # The lane-colour DOT (a childless 12x12 tk.Frame) refused to render
            # on the owner's box even with pack_propagate(False) — invisible
            # there while fine here, twice reported. A frame can collapse; text
            # cannot, so the lane NAME carries the colour instead (matches the
            # in-game legend + MIDI editor). Guaranteed-visible, owner-directed.
            tk.Label(inner, text=LANE_DEFS[lane_id]["label"], background=ROW_ALT,
                    foreground=_legible_lane_color(
                        lane_color(layout, lane_id, "forge")),
                    font=F_SMALL).pack(side=tk.LEFT)
            # Per-lane colour on the KEY BADGE (owner-directed 2026-07-23): the
            # dot + colour-name kept not rendering on the owner's box, so the
            # assigned-key badge carries the lane colour instead — cyan Hi-Hat,
            # red Snare, orange Crash, etc. Legibility floor keeps dark lanes
            # (Tom 1 navy / Tom 3 purple) readable on the DARKER badge fill.
            lane_col = _legible_lane_color(lane_color(layout, lane_id, "forge"))
            for b in binds:
                if b.get("lane") != lane_id:
                    continue
                cap = tk.Label(inner, text=_key_label(b.get("code", "")),
                              background=DARKER, foreground=lane_col, font=F_MONO,
                              padx=5, pady=1, highlightthickness=1,
                              highlightbackground=lane_col)
                cap.pack(side=tk.RIGHT, padx=2)
                if b.get("vel") is not None:
                    Tooltip(cap, f"fires at velocity {b['vel']}")
            OutlineButton(inner, "+ghost", accent=MUTED,
                         command=functools.partial(self._on_rebind, lane_id),
                         tooltip="Add a ghost-note key (fires at velocity 32)"
                         ).pack(side=tk.RIGHT, padx=(4, 2))
            OutlineButton(inner, "+", accent=PURPLE_EDGE,
                         command=functools.partial(self._on_rebind, lane_id),
                         tooltip="Add another key for this lane (Settings)"
                         ).pack(side=tk.RIGHT, padx=2)

    def _on_rebind(self, _lane_id: str) -> None:
        self._emit("on_open_settings", "keybinds")

    def _on_enable_midi(self) -> None:
        ok, ports = self._hook_call("midi_list_ports")
        if not ok:
            self._note("MIDI not wired yet (keyboard still works).", MUTED)
            return
        text = f"MIDI: {len(ports) if ports else 0} device(s)"
        for chip in (self.midi_chip, self.midi_chip_home):
            self._set_chip(chip, text, "ok" if ports else "warn")

    # =======================================================================
    # Scanning (off-thread; mirrors parakit_spectral_tab.py's
    # _start_real_compare / _compare_worker / _poll_compare, DISPATCH section
    # 1 + 3b's "off-thread scan / worker + queue polled by after()").
    # =======================================================================
    def _on_pick_folder(self) -> None:
        # initialdir hardening (owner-reported 2026-07-23): seeding the native
        # folder dialog with a very large / OneDrive-backed songs folder made it
        # hang and then throw `TclError: Unspecified error` (the shell dialog
        # enumerates initialdir up front), dead-ending the button so the user
        # couldn't even pick a smaller folder. Only seed initialdir with a path
        # that still exists, and if the dialog fails anyway, retry once with no
        # initialdir instead of letting the exception bubble into the Tk
        # callback.
        init = (self._songs_folder
                if self._songs_folder and os.path.isdir(self._songs_folder)
                else None)
        title = "Choose your Paradiddle songs folder"
        try:
            chosen = filedialog.askdirectory(initialdir=init, title=title)
        except tk.TclError:
            try:
                chosen = filedialog.askdirectory(title=title)
            except tk.TclError as e:
                self._note(f"Could not open the folder picker: {e}", AMBER)
                return
        if chosen:
            self._set_songs_folder(chosen)

    def _on_rescan(self) -> None:
        if self._songs_folder and os.path.isdir(self._songs_folder):
            self._start_scan(self._songs_folder)

    def _set_songs_folder(self, path: str) -> None:
        self._songs_folder = path
        self._cfg_set("practice_songs_folder", path)
        self.set_folder_label(path)
        self._start_scan(path)

    def _start_scan(self, root: str) -> None:
        self._cancel_scan()
        self._scan_cancel = threading.Event()
        self._scan_queue = queue.Queue()
        self._scanning = True
        self._set_scan_status(f"Scanning {os.path.basename(root.rstrip(chr(92)+'/')) or root}…")
        self._scan_thread = threading.Thread(
            target=_scan_thread_main,
            args=(root, self._scan_queue, self._scan_cancel),
            daemon=True)
        self._scan_thread.start()
        self._poll_scan_job = self.after(100, self._poll_scan)

    def _cancel_scan(self) -> None:
        if self._scan_cancel is not None:
            self._scan_cancel.set()
        if self._poll_scan_job is not None:
            try:
                self.after_cancel(self._poll_scan_job)
            except Exception:
                pass
            self._poll_scan_job = None
        self._scan_queue = None
        self._scanning = False

    def _poll_scan(self) -> None:
        if self._scan_queue is None:
            self._poll_scan_job = None
            return
        finished = False
        try:
            while True:
                msg = self._scan_queue.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    _, cur, total = msg
                    self._set_scan_status(f"Scanning… {cur}/{total}")
                elif kind == "done":
                    _, entries, problems = msg
                    self._on_scan_done(entries, problems)
                    finished = True
                elif kind == "error":
                    _, err = msg
                    self._on_scan_error(err)
                    finished = True
        except queue.Empty:
            pass
        if finished:
            self._scanning = False
            self._poll_scan_job = None
            return
        try:
            self._poll_scan_job = self.after(120, self._poll_scan)
        except tk.TclError:
            self._poll_scan_job = None

    def _play_history(self) -> dict:
        h = self._cfg_get("practice_last_played", {})
        return h if isinstance(h, dict) else {}

    def _mark_played(self, key: str) -> None:
        # Persist a play timestamp so 'Sort: Recent' actually reflects recency
        # (breaker fix r5, 2026-07-21): lastPlayed was hardcoded 0 everywhere, so
        # Recent collapsed to Title. Keyed by song_key (same as records).
        if not key:
            return
        try:
            hist = self._play_history()
            hist[key] = time.time()
            self._cfg_set("practice_last_played", hist)
            for e in self._entries:
                if e.get("key") == key:
                    e["lastPlayed"] = hist[key]
        except Exception:
            pass

    def _on_scan_done(self, entries: List[dict], problems: List[dict]) -> None:
        # Hydrate play-recency onto each entry so Recent-sort works (breaker fix
        # r5). COERCE via _num (breaker fix r7): a corrupt persisted history value
        # (non-numeric string) otherwise reached _recent_sort_key's unary minus
        # and raised TypeError, crashing the list rebuild on every scan.
        hist = self._play_history()
        for e in entries:
            e["lastPlayed"] = _num(hist.get(e.get("key"), 0), 0)
        self._entries = entries
        self._problems = problems
        self._problems_open = False
        if self._selected_key not in {e["key"] for e in entries}:
            self._selected_key = None
            self._preplay_open = False
        n = len(entries)
        self._set_scan_status(
            f"{n} song{'s' if n != 1 else ''} found." if n
            else "No songs found in that folder.")
        self._update_problems_drawer()
        self._rebuild_list()
        self._update_play_loaded()

    def _on_scan_error(self, err: str) -> None:
        self._set_scan_status(f"Scan failed: {err}")
        self._note(f"Library scan failed: {err}", AMBER)

    def _set_scan_status(self, text: str) -> None:
        try:
            self.scan_status.configure(text=text)
        except tk.TclError:
            pass

    # ----- problems drawer ---------------------------------------------------
    def _on_problems_toggled(self, value: bool) -> None:
        self._problems_open = value
        self._update_problems_drawer()

    def _update_problems_drawer(self) -> None:
        n = len(self._problems)
        if n == 0:
            self.problems_btn.pack_forget()
            self.problems_lbl.pack_forget()
            self.problems_btn.set(False, fire=False)
            return
        word = "song needs" if n == 1 else "songs need"
        prefix = "▼" if self._problems_open else "▶"
        self.problems_btn.configure(
            text=f"{prefix} {n} {word} attention (click for reasons -- "
                 f"nothing is silently dropped)")
        if not self.problems_btn.winfo_ismapped():
            self.problems_btn.pack(anchor=tk.W, pady=(4, 0))
        lines = []
        for p in self._problems:
            head = p["folder"] + (" — quarantined" if p.get("fatal") else "")
            lines.append(head)
            lines.extend(f"    {item}" for item in p.get("items", []))
        self.problems_lbl.configure(text="\n".join(lines))
        if self._problems_open:
            if not self.problems_lbl.winfo_ismapped():
                self.problems_lbl.pack(anchor=tk.W, pady=(2, 0))
        else:
            self.problems_lbl.pack_forget()

    # =======================================================================
    # Search / sort / list rebuild
    # =======================================================================
    def _on_search_changed(self, _event=None) -> None:
        if self._filter_job is not None:
            try:
                self.after_cancel(self._filter_job)
            except Exception:
                pass
        self._filter_job = self.after(140, self._apply_filter)

    def _apply_filter(self) -> None:
        self._filter_job = None
        self._rebuild_list()

    def _on_sort_changed(self, _event=None) -> None:
        idx = self.sort_combo.current()
        self._sort = _SORTS[idx][0] if 0 <= idx < len(_SORTS) else "title"
        self._cfg_set("sort", self._sort)
        self._rebuild_list()

    def _filtered_sorted(self) -> List[dict]:
        q = (self.search_entry.get() or "").strip().lower()
        rows = [e for e in self._entries
                if not q or q in e["title"].lower()
                or q in (e["artist"] or "").lower()
                or q in (e["creator"] or "").lower()]
        seen: set = set()
        deduped: List[dict] = []
        for e in rows:
            # Dedup by song_key: the SAME song commonly lives in two folders
            # (base library + ParaDB EXPANDED LIBRARY), and the list shows each
            # unique song ONCE. (A brief (key,folder) experiment on 2026-07-21
            # was reverted: it double-showed every dual-folder song AND left the
            # row/selection/pp_state identity — all keyed by `key` alone — unable
            # to tell the two rows apart. The rare case of two GENUINELY-DIFFERENT
            # songs colliding on song_key is tracked in FOLLOWUPS.)
            if e["key"] in seen:
                continue
            seen.add(e["key"])
            deduped.append(e)
        deduped.sort(key=_SORT_KEYS.get(self._sort, _title_sort_key))
        return deduped

    def _rebuild_list(self) -> None:
        for child in list(self._list_body.winfo_children()):
            child.destroy()
        total = len({e["key"] for e in self._entries})
        self.count_lbl.configure(text=f"{total} song{'s' if total != 1 else ''}")
        rows = self._filtered_sorted()
        if not self._entries:
            ttk.Label(self._list_body, text=_EMPTY_LIB,
                     style="Prac.PanelMuted.TLabel", font=F_SMALL,
                     wraplength=600, justify=tk.LEFT).pack(
                fill=tk.X, padx=12, pady=12, anchor=tk.W)
        elif not rows:
            ttk.Label(self._list_body, text="No songs match the search.",
                     style="Prac.PanelMuted.TLabel", font=F_SMALL).pack(
                fill=tk.X, padx=12, pady=12, anchor=tk.W)
        else:
            for e in rows:
                row = self._make_row(e)
                row.pack(fill=tk.X, pady=(0, 1))
                if self._preplay_open and e["key"] == self._selected_key:
                    pp = self._build_preplay(e)
                    pp.pack(fill=tk.X, pady=(0, 6), padx=(0, 0))
        self._list_body.update_idletasks()
        self._on_list_body_configure()

    def _make_row(self, e: dict) -> tk.Frame:
        selected = e["key"] == self._selected_key
        # Neutral list field (BG) like the page + list canvas; selection reads as
        # a purple EDGE, not a purple fill wash (was blend(DARKER, PURPLE, 0.22)).
        row_bg = BG
        row = tk.Frame(self._list_body, background=row_bg,
                       highlightthickness=1,
                       highlightbackground=(PURPLE_EDGE if selected else SEPARATOR))
        inner = tk.Frame(row, background=row_bg)
        inner.pack(fill=tk.X, padx=10, pady=7)

        cover = _CoverArt(inner, e["title"], e.get("coverPath", ""), self._hook_call)
        cover.configure(background=row_bg)
        cover.pack(side=tk.LEFT)

        meta = tk.Frame(inner, background=row_bg)
        meta.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(10, 6))
        tk.Label(meta, text=e["title"], background=row_bg,
                foreground=TEXT_BRIGHT, font=F_BOLD, anchor=tk.W).pack(fill=tk.X)
        artist_text = e["artist"] or "—"
        if e.get("problems"):
            artist_text += "  ⚠"
        tk.Label(meta, text=artist_text, background=row_bg, foreground=MUTED_LAV,
                font=F_SMALL, anchor=tk.W).pack(fill=tk.X)

        pills = tk.Frame(inner, background=row_bg)
        pills.pack(side=tk.LEFT, padx=(0, 8))
        rec = self._records.get(e["key"], {})
        for d in DIFFICULTIES:
            info = e["diffs"].get(d)
            text = _DIFF_LETTER[d]
            tip = f"{d} — not charted"
            has = info is not None
            if info:
                if info.get("complexity"):
                    text += f"·{info['complexity']}"
                tip = f"{d} — {info['notes']} notes"
                best = rec.get(f"{d}|{self._prefs.get('windowMode', 'standard')}")
                if isinstance(best, dict) and best.get("grade"):
                    text += f" {best['grade']}"
                    tip += f" · best {best['grade']}"
            if has:
                pill = tk.Label(pills, text=text, background=PANEL2,
                                foreground=EMBER_LT, font=F_SMALL,
                                padx=5, pady=1, highlightthickness=1,
                                highlightbackground=EMBER)
            else:
                pill = tk.Label(pills, text=text, background=row_bg,
                                foreground=blend(MUTED_LAV, BG, 0.35),
                                font=F_SMALL, padx=5, pady=1,
                                highlightthickness=1,
                                highlightbackground=blend(EMBER, BG, 0.75))
            pill.pack(side=tk.LEFT, padx=2)
            Tooltip(pill, tip)

        dur = tk.Label(inner, text=_fmt_dur(e.get("lengthS", 0)),
                       background=row_bg, foreground=MUTED, font=F_MONO,
                       width=5, anchor=tk.E)
        dur.pack(side=tk.RIGHT)

        _bind_click_recursive(row, functools.partial(self._on_row_clicked, e["key"]))
        return row

    def _on_row_clicked(self, key: str, _event=None) -> None:
        if key != self._selected_key:
            self._selected_key = key
            self._preplay_open = True
            self._pp_state.setdefault(key, self._default_pp_state(self.entry_by_key(key)))
        else:
            self._preplay_open = not self._preplay_open
        self._rebuild_list()
        self._update_play_loaded()

    def entry_by_key(self, key: str) -> Optional[dict]:
        return next((e for e in self._entries if e["key"] == key), None)

    def _default_pp_state(self, entry: Optional[dict]) -> dict:
        avail = [d for d in DIFFICULTIES if entry and d in entry["diffs"]]
        pref_d = str(self._prefs.get("preferredDifficulty", "Expert"))
        diff = pref_d if pref_d in avail else (avail[-1] if avail else "")
        return {
            "difficulty": diff,
            # speed via _num too (breaker fix r4, 2026-07-21): a corrupt persisted
            # speed (NaN / 'fast' / a list) used to raise out of this row-click
            # path as an unhandled Tk callback — the same class _num already
            # hardens for fallTime/noteSize.
            # SNAP to an allowed pre-play choice (breaker fix r8, 2026-07-21): a
            # persisted speed off the fixed _SPEEDS_PCT list (e.g. 65% from the
            # play-dock's 0.05 nudges) made the combo fall back to DISPLAYING
            # 100% while state stayed 65% -> Play ran at 65% behind a "100%"
            # label. Snapping keeps display == state == what actually plays.
            "speed_pct": min(_SPEEDS_PCT, key=lambda p: abs(p - max(
                10, min(400, int(round(_num(self._prefs.get("speed", 1.0), 1.0) * 100)))))),
            "you_drum": bool(self._prefs.get("youDrum", True)),
            "kit_choice": _KIT_CURRENT,
            "audio_source": "auto",       # auto | fullmix | stems (per-song)
            "linked_audio_open": False,
        }

    # =======================================================================
    # Inline pre-play panel
    # =======================================================================
    def _build_preplay(self, entry: dict) -> tk.Frame:
        key = entry["key"]
        state = self._pp_state.setdefault(key, self._default_pp_state(entry))
        panel = tk.Frame(self._list_body, background=PANEL,
                         highlightthickness=1, highlightbackground=PURPLE_EDGE)
        pad = tk.Frame(panel, background=PANEL)
        pad.pack(fill=tk.BOTH, expand=True, padx=14, pady=10)

        avail = [d for d in DIFFICULTIES if d in entry["diffs"]]

        drow = tk.Frame(pad, background=PANEL)
        drow.pack(fill=tk.X)
        tk.Label(drow, text="DIFFICULTY", background=PANEL, foreground=EMBER_LT,
                font=F_SMALL, width=10, anchor=tk.W).pack(side=tk.LEFT)
        if avail:
            options = []
            for d in avail:
                comp = entry["diffs"][d].get("complexity") or 0
                options.append(f"{d} ·{comp}" if comp else d)
            seg = Segmented(drow, options, command=functools.partial(
                self._on_preplay_diff, key, avail))
            try:
                seg.set(avail.index(state["difficulty"]), fire=False)
            except ValueError:
                pass
            seg.pack(side=tk.LEFT, padx=(8, 0))
        else:
            tk.Label(drow, text="no charted difficulties", background=PANEL,
                    foreground=MUTED, font=F_SMALL).pack(side=tk.LEFT, padx=(8, 0))

        srow = tk.Frame(pad, background=PANEL)
        srow.pack(fill=tk.X, pady=(8, 0))
        tk.Label(srow, text="SONG", background=PANEL, foreground=EMBER_LT,
                font=F_SMALL, width=10, anchor=tk.NW).pack(side=tk.LEFT)
        song_col = tk.Frame(srow, background=PANEL)
        song_col.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))

        source = self._audio_source_status(entry, state["difficulty"], state)
        notes_text, stems_text, youdrum_hint = self._preplay_readout(
            entry, state["difficulty"], doubled=bool(source["doubled"]))
        tk.Label(song_col, text=notes_text, background=PANEL, foreground=TEXT,
                font=F_SMALL, anchor=tk.W).pack(fill=tk.X)
        tk.Label(song_col, text=stems_text, background=PANEL, foreground=MUTED,
                font=F_SMALL, anchor=tk.W, justify=tk.LEFT, wraplength=500
                ).pack(fill=tk.X)

        source_row = tk.Frame(song_col, background=PANEL)
        source_row.pack(fill=tk.X, pady=(7, 0))
        # When the "backing" is really a full mix, calling this button "Backing +
        # Drums" asserts a drumless backing the song does not have. Name what it
        # would actually play, so the choice reads as the mistake it is.
        if source["doubled"]:
            backing_label = "Full Mix + Drums  (not recommended — doubles them)"
        else:
            backing_label = "Backing + Drums"
        self._audio_source_button(
            source_row, backing_label,
            selected=source["effective"] == "stems",
            enabled=source["stems_available"],
            command=functools.partial(self._on_audio_source, key, "stems")
        ).pack(side=tk.LEFT)
        self._audio_source_button(
            source_row, "Full Mix",
            selected=source["effective"] == "fullmix",
            enabled=source["fullmix_available"],
            command=functools.partial(self._on_audio_source, key, "fullmix")
        ).pack(side=tk.LEFT, padx=(6, 0))

        if source["note"]:
            tk.Label(song_col, text=source["note"], background=PANEL,
                     foreground=(AMBER if source["warning"] else MUTED),
                     font=F_SMALL, anchor=tk.W, justify=tk.LEFT, wraplength=620
                     ).pack(fill=tk.X, pady=(4, 0))

        linked_row = tk.Frame(song_col, background=PANEL)
        linked_row.pack(fill=tk.X, pady=(5, 0))
        # Flat purple text link (a tk.Label + click binding — the app's idiom
        # for text links). Was a tk.Button, the last native button in the
        # library; a tk.Label never picks up Windows button chrome.
        linked_lbl = tk.Label(
            linked_row,
            text=("Linked audio ▾" if state.get("linked_audio_open")
                  else "Linked audio ▸"),
            background=PANEL, foreground=PURPLE_EDGE, font=F_SMALL,
            cursor="hand2")
        linked_lbl.pack(side=tk.LEFT)
        linked_lbl.bind("<Button-1>",
                        lambda _e, k=key: self._on_linked_audio_toggle(k))

        if state.get("linked_audio_open"):
            linked_items = source["linked"]["song"] + source["linked"]["drums"]
            details = tk.Frame(song_col, background=PANEL2)
            details.pack(fill=tk.X, pady=(4, 0))
            if not linked_items:
                tk.Label(details,
                         text="No songTracks or drumTracks are linked in this chart.",
                         background=PANEL2, foreground=MUTED, font=F_SMALL,
                         anchor=tk.W).pack(fill=tk.X, padx=8, pady=6)
            else:
                for item in linked_items:
                    status = "[OK]" if item["exists"] else "[MISSING]"
                    bus_label = ("songTracks" if item["bus"] == "song"
                                 else "drumTracks")
                    tk.Label(details,
                             text=(f"{status} {bus_label}: {item['name']}\n"
                                   f"    {item['path'] or '(unresolved path)'}"),
                             background=PANEL2,
                             foreground=(TEXT if item["exists"] else AMBER),
                             font=F_MONO, anchor=tk.W, justify=tk.LEFT,
                             wraplength=610).pack(fill=tk.X, padx=8, pady=3)

        prow = tk.Frame(pad, background=PANEL)
        prow.pack(fill=tk.X, pady=(8, 0))
        tk.Label(prow, text="SPEED", background=PANEL, foreground=EMBER_LT,
                font=F_SMALL, width=10, anchor=tk.W).pack(side=tk.LEFT)
        speed_combo = ttk.Combobox(prow, style="Prac.TCombobox", state="readonly",
                                   width=6, values=[f"{p}%" for p in _SPEEDS_PCT])
        try:
            speed_combo.current(_SPEEDS_PCT.index(state["speed_pct"]))
        except ValueError:
            speed_combo.current(_SPEEDS_PCT.index(100))
        speed_combo.bind("<<ComboboxSelected>>", functools.partial(
            self._on_preplay_speed, key, speed_combo))
        speed_combo.pack(side=tk.LEFT, padx=(8, 12))
        yd = ToggleSwitch(prow, on=state["you_drum"], background=PANEL,
                          command=functools.partial(self._on_preplay_youdrum, key))
        yd.pack(side=tk.LEFT)
        tk.Label(prow, text=" You Drum (Mute on Miss)", background=PANEL, foreground=TEXT,
                font=F_BASE).pack(side=tk.LEFT)
        tk.Label(prow, text="  " + youdrum_hint, background=PANEL,
                foreground=MUTED, font=F_SMALL).pack(side=tk.LEFT)

        krow = tk.Frame(pad, background=PANEL)
        krow.pack(fill=tk.X, pady=(8, 0))
        tk.Label(krow, text="KIT", background=PANEL, foreground=EMBER_LT,
                font=F_SMALL, width=10, anchor=tk.W).pack(side=tk.LEFT)
        kit_values = []
        if key in self._kit_pins:
            kit_values.append(_KIT_PINNED)
        kit_values.append(_KIT_CURRENT)
        kit_values.extend(BUILTIN_PRESETS.keys())
        kit_values.extend(f"{n} (yours)" for n in self._kit_presets)
        kit_combo = ttk.Combobox(krow, style="Prac.TCombobox", state="readonly",
                                 width=22, values=kit_values)
        try:
            kit_combo.current(kit_values.index(state["kit_choice"]))
        except ValueError:
            kit_combo.current(0)
        kit_combo.bind("<<ComboboxSelected>>", functools.partial(
            self._on_preplay_kit, key, kit_combo))
        kit_combo.pack(side=tk.LEFT, padx=(8, 8))
        OutlineButton(krow, "Kit Studio…", accent=PURPLE_EDGE,
                     command=functools.partial(self._emit, "on_open_kit_studio")
                     ).pack(side=tk.LEFT)

        play_btn = OutlineButton(
            pad, f"▶ Play {entry['title']}", accent=MAGENTA,
            command=functools.partial(self._on_preplay_play, key),
            enabled=bool(state["difficulty"]))
        play_btn.pack(anchor=tk.W, pady=(10, 0))
        return panel

    def _linked_audio(self, entry: dict,
                      info: Optional[dict]) -> Dict[str, List[dict]]:
        refs = entry.get("audioRefs") or {}
        refs_by_name: Dict[str, str] = {}
        for ref_name, ref_path in refs.items():
            if isinstance(ref_path, str) and ref_path:
                refs_by_name[os.path.basename(str(ref_name)).casefold()] = ref_path
        folder = entry.get("folder") or ""

        def _resolve(bus: str, names) -> List[dict]:
            out: List[dict] = []
            for raw_name in names or ():
                linked_name = str(raw_name)
                base_name = os.path.basename(linked_name)
                path = refs_by_name.get(base_name.casefold(), "")
                if not path:
                    if os.path.isabs(linked_name):
                        path = linked_name
                    elif folder:
                        path = os.path.join(folder, linked_name)
                    else:
                        path = linked_name
                    path = os.path.normpath(path) if path else ""
                out.append({"bus": bus, "name": linked_name, "path": path,
                            "exists": bool(path and os.path.isfile(path))})
            return out

        info = info or {}
        return {"song": _resolve("song", info.get("songTracks")),
                "drums": _resolve("drums", info.get("drumTracks"))}

    def _audio_source_status(self, entry: dict, difficulty: str,
                             state: dict) -> dict:
        info = entry["diffs"].get(difficulty)
        linked = self._linked_audio(entry, info)
        song_paths = [i["path"] for i in linked["song"] if i["exists"]]
        drum_paths = [i["path"] for i in linked["drums"] if i["exists"]]
        stems_available = bool(song_paths and drum_paths)
        # Doubled-source probe FIRST: it decides whether the linked backing is
        # actually a self-contained full mix (drums baked in) or a clean
        # drumless backing. fullmix_available depends on the answer.
        doubled = False
        detect_known = True
        if len(song_paths) == 1 and drum_paths:
            detect_known, result = self._hook_call(
                "mixer_detect_doubled_source", song_paths[0], drum_paths[0])
            doubled = bool(result) if detect_known else False
        # "Full Mix" means ONE source that already carries the whole song:
        # either (a) a song file with no separate drum stem (the file *is* the
        # whole track), or (b) a backing detected to already contain the drums
        # (doubled). A clean drumless backing + a separate drum stem is NOT a
        # full mix -- it's stems only, so the button must gray out. (Owner-
        # reported 2026-07-23: Full Mix was clickable on stem-split / PD-download
        # songs that have no combined mix, and selecting it silently played the
        # drumless backing.) If the probe is unavailable, keep it available
        # rather than hide a genuine mix we simply couldn't classify.
        fullmix_available = bool(song_paths) and (
            not drum_paths or doubled or not detect_known)

        requested = str(state.get("audio_source", "auto")).lower()
        if requested not in {"auto", "fullmix", "stems"}:
            requested = "auto"
        mode = requested
        if mode == "fullmix" and not fullmix_available:
            mode = "auto"
        elif mode == "stems" and not stems_available:
            mode = "auto"

        if mode == "fullmix":
            effective = "fullmix"
        elif mode == "stems":
            effective = "stems"
        elif doubled and fullmix_available:
            effective = "fullmix"
        elif stems_available:
            effective = "stems"
        elif fullmix_available:
            effective = "fullmix"
        else:
            effective = ""

        note = ""
        warning = False
        if not song_paths and not drum_paths:
            note = ("No linked audio files are available on disk; playback "
                    "will use synth hits and the silent clock.")
            warning = True
        elif not song_paths:
            note = ("No linked backing/full-mix file exists on disk; the "
                    "source selector is unavailable.")
            warning = True
        elif mode != requested:
            note = ("The saved source choice is unavailable for this "
                    "difficulty; Auto is using the available audio.")
            warning = True
        elif doubled and mode == "auto":
            note = ("Auto: this backing already contains the drums, so Full "
                    "Mix is selected to avoid doubling.")
        elif doubled and mode == "stems":
            note = ("Override: this backing is a full mix, so playing it with "
                    "the drum stem layers the drums on top of themselves — it "
                    "doubles them and sounds worse, and it does NOT give you "
                    "Mute-on-Miss (see below).")
            warning = True
        elif not drum_paths:
            note = ("Only Full Mix is available because no linked drum stem "
                    "exists on disk.")
        elif not fullmix_available:
            note = ("Full Mix is unavailable: this chart has separate backing + "
                    "drum stems with no combined mix on disk. Backing + Drums "
                    "plays the full song.")
        elif not detect_known and mode == "auto":
            note = ("Auto: the doubling check is unavailable; Backing + Drums "
                    "is selected.")
            warning = True

        # Can "You drum" / Mute-on-Miss actually DO anything for this chart?
        # Both work by zeroing the DRUMS bus, so they only produce silence-where-
        # the-drums-were when the SONG bus carries a DRUMLESS backing. Two shapes
        # make that impossible, and until 4.9.5 the UI advertised the toggle
        # identically in all three (owner-reported 2026-07-27 on a ParaDB pack):
        #   * no drum stem at all      -> nothing is routed to the drums bus
        #   * backing is a full mix    -> its baked-in drums stay audible on the
        #                                 song bus no matter what the drums bus does
        # Measured on the owner's 158-pack ParaDB library: only 43 packs have a
        # genuine drumless backing + drum stem. 85 are full-mix-only and 16 more
        # have a full mix sitting in songTracks, so Mute-on-Miss is inert on ~73%
        # of the library and used to give no sign of it.
        mom_supported = bool(song_paths and drum_paths) and not doubled
        mom_active = mom_supported and effective == "stems"
        # Say it plainly whenever the toggle is inert, and say WHY -- the two
        # causes have different answers for the user (one is fixable by picking
        # the other source, the other needs a drumless backing that this song
        # simply does not ship).
        if song_paths and not mom_active:
            if not drum_paths:
                mom_note = ("“You drum” / Mute-on-Miss cannot work here: this "
                            "chart links no separate drum stem, so there are no "
                            "recorded drums to mute — you always hear the mix.")
            elif doubled:
                mom_note = ("“You drum” / Mute-on-Miss cannot work here: muting "
                            "the drum stem still leaves the drums that are baked "
                            "into this full mix. It needs a DRUMLESS backing, "
                            "which this song does not ship.")
            else:
                mom_note = ("“You drum” / Mute-on-Miss needs Backing + Drums — "
                            "on Full Mix there is no separate drum track to mute.")
            note = f"{note}\n{mom_note}" if note else mom_note
            warning = True
        return {"linked": linked, "song_paths": song_paths,
                "drum_paths": drum_paths, "fullmix_available": fullmix_available,
                "stems_available": stems_available, "doubled": doubled,
                "mode": mode, "effective": effective, "note": note,
                "warning": warning, "mom_supported": mom_supported,
                "mom_active": mom_active}

    @staticmethod
    def _audio_source_button(parent, text: str, *, selected: bool,
                             enabled: bool, command) -> OutlineButton:
        # Colored-outline OPTION button in the app's badge language. This MUST
        # be an OutlineButton (a tk.Label subclass), NOT a tk.Button: on Windows
        # a native tk.Button renders as a raised navy button regardless of the
        # color props, which is why every color-only patch failed (owner-
        # reported 3x — the practice library was the only one still using
        # tk.Button here). filled=selected -> solid purple; unselected -> the
        # purple outline pill matching the row's other option buttons; disabled
        # -> the standard dim outline.
        return OutlineButton(
            parent, text, accent=PURPLE_EDGE, command=command,
            enabled=enabled, filled=selected,
            fill_bg=PURPLE, fill_fg=TEXT_BRIGHT, fill_hover=PURPLE_EDGE)

    def _preplay_readout(self, entry: dict, difficulty: str,
                         doubled: bool = False) -> Tuple[str, str, str]:
        info = entry["diffs"].get(difficulty)
        if info is None:
            return "", "", "(your hits make the drum sounds)"
        notes_text = f"{info['notes']:,} notes · {difficulty}"
        linked = self._linked_audio(entry, info)
        song_t = linked["song"]
        drum_t = linked["drums"]
        if not song_t and not drum_t:
            return notes_text, "No linked audio stems — synth hits only.", \
                "(this chart links no drum stems — your hits ARE the drums)"
        nd = len(drum_t)
        missing = sum(not i["exists"] for i in song_t + drum_t)
        # Call the song bus what it actually is. `doubled` means the linked
        # "backing" was measured to already contain the drums, so labelling it
        # "backing" reads as a drumless stem that does not exist.
        song_word = "full mix" if doubled else "backing"
        text = (f"Audio: {len(song_t)} linked {song_word} + {nd} linked "
                f"stem{'s' if nd != 1 else ''} ({nd} drums)")
        if missing:
            text += f" · {missing} missing on disk"
        on_disk_drums = sum(i["exists"] for i in drum_t)
        if not on_disk_drums:
            hint = "(no linked drum stem is available — your hits ARE the drums)"
        elif doubled:
            # The drums bus exists, but muting it cannot silence drums that are
            # baked into the song bus — so do not offer "off to hear them".
            text += ("\nThe drums are baked into this mix, so muting the drum "
                     "stem cannot remove them.")
            hint = "(this song has no drumless backing — the drums always play)"
        else:
            text += ("\nStems route to the drums bus — turn “You drum” OFF "
                     "to hear the full backing drums.")
            hint = "(on = you play the drums · off = hear the backing drums)"
        return notes_text, text, hint

    def _on_linked_audio_toggle(self, key: str) -> None:
        state = self._pp_state.setdefault(key, {})
        state["linked_audio_open"] = not bool(state.get("linked_audio_open", False))
        self._rebuild_list()

    def _on_audio_source(self, key: str, mode: str) -> None:
        entry = self.entry_by_key(key)
        state = self._pp_state.get(key)
        if entry is None or not state:
            return
        status = self._audio_source_status(entry, state.get("difficulty", ""), state)
        available = (status["stems_available"] if mode == "stems"
                     else status["fullmix_available"])
        if not available:
            self._note("That audio source is not available on disk.", AMBER)
            return
        state["audio_source"] = mode
        self._rebuild_list()

    def _on_preplay_diff(self, key: str, avail: List[str], index: int) -> None:
        if 0 <= index < len(avail):
            d = avail[index]
            self._pp_state.setdefault(key, {})["difficulty"] = d
            self._set_pref("preferredDifficulty", d)
            self._rebuild_list()

    def _on_preplay_speed(self, key: str, combo: ttk.Combobox, _event=None) -> None:
        idx = combo.current()
        if 0 <= idx < len(_SPEEDS_PCT):
            pct = _SPEEDS_PCT[idx]
            self._pp_state.setdefault(key, {})["speed_pct"] = pct
            self._set_pref("speed", pct / 100.0)

    def _on_preplay_youdrum(self, key: str, value: bool) -> None:
        self._pp_state.setdefault(key, {})["you_drum"] = value
        self._set_pref("youDrum", value)

    def _on_preplay_kit(self, key: str, combo: ttk.Combobox, _event=None) -> None:
        idx = combo.current()
        values = combo.cget("values")
        if 0 <= idx < len(values):
            self._pp_state.setdefault(key, {})["kit_choice"] = values[idx]

    def _on_preplay_play(self, key: str) -> None:
        entry = self.entry_by_key(key)
        state = self._pp_state.get(key)
        if entry is None or not state or not state.get("difficulty"):
            self._note("Pick a charted difficulty before playing.", AMBER)
            return
        info = entry["diffs"].get(state["difficulty"])
        if info is None:
            self._note("That difficulty isn't charted for this song.", AMBER)
            return
        try:
            with open(info["path"], "rb") as fh:
                chart = parse_rlrr(decode_chart_bytes(fh.read()))
        except (OSError, ValueError) as e:
            self._note(f"Failed to load chart: {e}", AMBER)
            return
        source_status = self._audio_source_status(
            entry, state["difficulty"], state)
        audio_paths = self._resolve_audio_paths(entry, info)
        config = self._build_session_config(
            source="library", chart=chart, key=key, title=entry["title"],
            artist=entry["artist"], difficulty=state["difficulty"],
            speed_pct=state["speed_pct"], you_drum=state["you_drum"],
            kit_choice=state["kit_choice"], audio_paths=audio_paths,
            audio_source=source_status["mode"])
        self._mark_played(key)          # record recency for 'Sort: Recent'
        self._emit("on_play_request", config)

    def _resolve_audio_paths(self, entry: dict, info: dict) -> dict:
        linked = self._linked_audio(entry, info)
        return {"song": [i["path"] for i in linked["song"] if i["exists"]],
                "drums": [i["path"] for i in linked["drums"] if i["exists"]]}

    # =======================================================================
    # Play buttons (demo / loaded / single)
    # =======================================================================
    def _update_play_loaded(self) -> None:
        single_ready = self._single_chart is not None
        try:
            self.play_single_btn.set_enabled(single_ready)
        except tk.TclError:
            pass
        selected = self._selected_key is not None
        try:
            self.play_loaded_btn.set_enabled(selected or single_ready)
        except tk.TclError:
            pass
        self._update_loaded_readout(selected, single_ready)

    def _update_loaded_readout(self, selected: bool, single_ready: bool) -> None:
        # Show WHICH song "Play Loaded Song" will start (v4.9.2). A selected
        # library song wins over a loaded single chart (that is the play order in
        # _on_play_loaded). Hidden entirely when nothing is queued.
        readout = getattr(self, "loaded_readout", None)
        if readout is None:
            return
        text = ""
        if selected:
            entry = self.entry_by_key(self._selected_key)
            if entry:
                title = entry.get("title") or entry.get("key") or "Selected song"
                artist = entry.get("artist") or ""
                text = "▶ Ready: %s%s" % (title, "  ·  " + artist if artist else "")
        elif single_ready:
            meta = (self._single_chart or {}).get("meta", {})
            title = (meta.get("title")
                     or (os.path.basename(self._single_chart_path)
                         if getattr(self, "_single_chart_path", "") else "Loaded chart"))
            artist = meta.get("artist") or ""
            text = "▶ Ready (single chart): %s%s" % (
                title, "  ·  " + artist if artist else "")
        try:
            if text:
                readout.configure(text=text)
                if not readout.winfo_manager():
                    readout.pack(anchor=tk.W, pady=(2, 0),
                                 before=self._loaded_hint)
            elif readout.winfo_manager():
                readout.pack_forget()
        except tk.TclError:
            pass

    def _on_demo(self) -> None:
        chart = demo_chart()
        config = self._build_session_config(
            source="demo", chart=chart, key=None, title=chart["meta"]["title"],
            artist=chart["meta"]["artist"], difficulty="Demo", speed_pct=100,
            you_drum=bool(self._prefs.get("youDrum", True)),
            kit_choice=_KIT_CURRENT, audio_paths={})
        self._emit("on_play_request", config)

    def _on_play_loaded(self) -> None:
        if self._selected_key is not None:
            self._on_preplay_play(self._selected_key)
        elif self._single_chart is not None:
            self._on_play_single()
        else:
            self._note("Select a library song below, or load a single "
                       "chart, to enable this.", MUTED)

    def _on_load_rlrr(self) -> None:
        last_dir = self._cfg_get("practice_last_rlrr_dir", "")
        path = filedialog.askopenfilename(
            title="Choose a .rlrr chart",
            initialdir=last_dir or None,
            filetypes=[("Paradiddle chart", "*.rlrr"), ("All files", "*.*")])
        if path:
            self._load_rlrr_path(path)

    def _load_rlrr_path(self, path: str) -> None:
        try:
            with open(path, "rb") as fh:
                chart = parse_rlrr(decode_chart_bytes(fh.read()))
        except (OSError, ValueError) as e:
            self._note(f"Failed to load chart: {e}", AMBER)
            self.single_hint.configure(text=f"failed to load: {e}",
                                       foreground=AMBER)
            return
        self._single_chart = chart
        self._single_chart_path = path
        self._cfg_set("practice_last_rlrr_dir", os.path.dirname(path))
        meta = chart["meta"]
        title = meta.get("title") or os.path.basename(path)
        n = len(chart["events"])
        self.single_hint.configure(
            text=f"{title} — {n:,} notes" + (f" ({meta['artist']})" if meta.get("artist") else ""),
            foreground=TEXT)
        self._update_play_loaded()

    def _on_load_single_audio(self) -> None:
        last_dir = self._cfg_get("practice_last_audio_dir", "")
        paths = filedialog.askopenfilenames(
            title="Add audio (Full Mix / Stem)",
            initialdir=last_dir or None,
            filetypes=[("Audio", "*.wav *.mp3 *.flac *.ogg"), ("All files", "*.*")])
        if paths:
            self._load_single_audio_paths(list(paths))

    def _load_single_audio_paths(self, paths: List[str]) -> None:
        self._single_audio_paths = paths
        self._cfg_set("practice_last_audio_dir", os.path.dirname(paths[0]))
        n = len(paths)
        self.single_audio_hint.configure(
            text=f"{n} file{'s' if n != 1 else ''} loaded", foreground=TEXT)

    def _on_play_single(self) -> None:
        if self._single_chart is None:
            self._note("Load a .rlrr chart first.", AMBER)
            return
        config = self._build_session_config(
            source="single", chart=self._single_chart, key=None,
            title=self._single_chart["meta"].get("title") or "Custom chart",
            artist=self._single_chart["meta"].get("artist") or "",
            difficulty="Custom", speed_pct=100,
            you_drum=bool(self._prefs.get("youDrum", True)),
            kit_choice=_KIT_CURRENT,
            audio_paths={"manual": list(self._single_audio_paths)})
        self._emit("on_play_request", config)

    # =======================================================================
    # Session-config building (shared by demo / library / single Play paths)
    # =======================================================================
    def _resolve_kit_layout(self, kit_choice: str) -> dict:
        base_name = kit_choice
        if base_name.endswith(" (yours)"):
            base_name = base_name[: -len(" (yours)")]
        if base_name in BUILTIN_PRESETS:
            return BUILTIN_PRESETS[base_name]()
        if base_name not in (_KIT_CURRENT, _KIT_PINNED):
            ok, layout = self._hook_call("get_kit_layout", base_name)
            if ok and isinstance(layout, dict):
                return layout
        return default_layout()

    def _build_session_config(self, *, source: str, chart: dict,
                              key: Optional[str], title: str, artist: str,
                              difficulty: str, speed_pct: int, you_drum: bool,
                              kit_choice: str, audio_paths: dict,
                              audio_source: str = "auto") -> dict:
        layout = self._resolve_kit_layout(kit_choice)
        resolved = resolve_routing(chart, layout, None)
        lane_notes = build_lane_notes(chart, resolved)
        duration = song_duration_from_lanes(lane_notes)
        prefs = self._prefs
        return {
            "source": source,
            "key": key,
            "title": title,
            "artist": artist,
            "difficulty": difficulty,
            "speed_pct": speed_pct,
            "speed": speed_pct / 100.0,
            "you_drum": bool(you_drum),
            "kit_choice": kit_choice,
            "layout": layout,
            "chart": chart,
            "lane_note_counts": [len(x) for x in lane_notes],
            "duration_s": duration,
            "audio_paths": audio_paths,
            "audio_source": audio_source,
            "window_mode": prefs.get("windowMode", "standard"),
            "auto_kick": bool(prefs.get("autoKick", False)),
            "input_latency_ms": prefs.get("inputLatencyMs", 0),
        }

    # =======================================================================
    # Keyboard: '/' focuses search (mirrors parakit_spectral_tab.py's
    # bind/unbind-on-destroy discipline so a destroyed screen never leaks a
    # root-level bind).
    # =======================================================================
    def _bind_keys(self) -> None:
        top = self.winfo_toplevel()
        funcid = top.bind("<Key>", self._on_key_global, add="+")
        self._key_binds.append((top, "<Key>", funcid))

    def _unbind_keys(self) -> None:
        for widget, seq, funcid in self._key_binds:
            try:
                widget.unbind(seq, funcid)
            except Exception:
                pass
        self._key_binds = []

    def _text_focus(self) -> bool:
        try:
            w = self.focus_get()
        except (tk.TclError, KeyError):
            return True
        return w is not None and w.winfo_class() in ("TEntry", "Entry",
                                                       "TCombobox", "Text")

    def _keys_active(self) -> bool:
        try:
            return (getattr(self, "_shown", True) and self.winfo_ismapped()
                    and not self._text_focus())
        except (tk.TclError, KeyError):
            return False

    def set_shown(self, shown: bool) -> None:
        # The router tells Home whether it is the RAISED screen (breaker fix,
        # 2026-07-21): in a tkraise stack a COVERED Home stays
        # winfo_ismapped()==True, so the '/' search shortcut used to fire during
        # Play/Results and steal keyboard focus into the hidden search box.
        self._shown = bool(shown)

    def _on_key_global(self, event) -> None:
        if event.keysym != "slash" or not self._keys_active():
            return
        self._focus_search()

    def _focus_search(self) -> None:
        try:
            self.search_entry.entry.focus_set()
        except tk.TclError:
            pass

    # =======================================================================
    # Teardown
    # =======================================================================
    def external_stop(self) -> None:
        """Public stop for the host app -- cancels the scan thread + any
        pending after() timers. Safe to call at any time, including before
        any scan has run (mirrors parakit_spectral_tab.py's external_stop)."""
        try:
            self._cancel_scan()
        except Exception:
            pass
        if self._filter_job is not None:
            try:
                self.after_cancel(self._filter_job)
            except Exception:
                pass
            self._filter_job = None

    def _unbind_page_wheel(self) -> None:
        """Remove ONLY our bind_all wheel handlers from the 'all' bindtag (never
        unbind_all — that would kill the host app's own global wheel handler).
        Surgical funcid removal, mirroring the key-bind cleanup."""
        w = getattr(self, "_page_canvas", None)
        if w is None:
            return
        for seq, fid in getattr(self, "_page_wheel_binds", ()):
            try:
                # Remove ONLY this funcid's line from the 'all' bindtag script so
                # the handler stops firing and is no longer referenced there. Do
                # NOT deletecommand(fid): the command was registered against this
                # canvas, so Tk deletes it itself in super().destroy() right
                # after — a manual delete would double-delete and raise
                # "can't delete Tcl command" (breaker regression fix).
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
            self.external_stop()
        except Exception:
            pass
        try:
            self._unbind_keys()
        except Exception:
            pass
        try:
            self._unbind_page_wheel()
        except Exception:
            pass
        super().destroy()


# ===========================================================================
# Standalone host -- `python parakit_practice_home.py`. dict-backed
# get_cfg/set_cfg (the hook seam) + stub on_* handlers that PRINT the
# resolved payload so the Home<->tab signal contract is exercised end to end
# with hooks=None-equivalent behaviour for the DEFERRED hooks (load_cover,
# midi_list_ports, get_kit_layout, list_kit_presets are simply absent here).
# ===========================================================================
if __name__ == "__main__":
    _CFG_STORE: Dict[str, Any] = {
        "practice_songs_folder": os.path.join(
            os.path.expanduser("~"), "OneDrive", "Desktop", "Paradiddle Songs"),
        "practice_prefs": dict(DEFAULT_PREFS),
        "sort": "title",
    }

    def _get_cfg(key, default=None):
        return _CFG_STORE.get(key, default)

    def _set_cfg(key, value):
        _CFG_STORE[key] = value
        print(f"[cfg] {key} = {value!r}")

    def _print_play(config: dict) -> None:
        print("=" * 72)
        print("on_play_request fired:")
        for k, v in config.items():
            if k == "chart":
                print(f"  chart: title={v['meta']['title']!r} "
                      f"events={len(v['events'])} warnings={v['warnings']}")
            elif k == "layout":
                print(f"  layout: order={v['order']} lefty={v['lefty']}")
            else:
                print(f"  {k}: {v!r}")
        print("=" * 72)

    def _print_settings(tab=None) -> None:
        print(f"[stub] on_open_settings(tab={tab!r})")

    def _print_kit_studio() -> None:
        print("[stub] on_open_kit_studio()")

    def _print_calibration() -> None:
        print("[stub] on_open_calibration()")

    root = tk.Tk()
    root.title("Practice Home -- standalone host")
    root.geometry("1280x920")
    apply_theme_global(root)

    hooks = {"get_cfg": _get_cfg, "set_cfg": _set_cfg}
    screen = PracticeHomeScreen(root, hooks=hooks)
    screen.pack(fill=tk.BOTH, expand=True)
    screen.on_play_request = _print_play
    screen.on_open_settings = _print_settings
    screen.on_open_kit_studio = _print_kit_studio
    screen.on_open_calibration = _print_calibration

    def _on_close():
        screen.external_stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", _on_close)
    root.mainloop()
