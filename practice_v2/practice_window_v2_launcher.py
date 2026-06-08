"""
practice_window_v2_launcher.py
==============================
Standalone launcher for ParaKit's Practice Window v2 (Pygame-CE mini-game).

Launch via "Run Practice Window v2.bat" or:
    py -3.12 practice_window_v2_launcher.py

Does NOT require the full ParaKit app. Needs pygame-ce (for the
subprocess window) and mido (for MIDI parsing + optional MIDI input).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import tkinter as tk
import tkinter.ttk as ttk
from pathlib import Path
from tkinter import filedialog, messagebox

try:
    from PIL import Image, ImageTk
    _HAS_PIL = True
except Exception:
    _HAS_PIL = False
    Image = None  # type: ignore
    ImageTk = None  # type: ignore

# ---------------------------------------------------------------------------
# Theme (matches Extractor Mini App)
# ---------------------------------------------------------------------------
C = {
    "bg":      "#222222",
    "deep":    "#0d1117",
    "fg":      "#e0e0e0",
    "muted":   "#aaaaaa",
    "btn_bg":  "#2a1235",
    "btn_fg":  "#f7efff",
    "btn_bdr": "#8a35a6",
    "accent":  "#e94560",
    "purple":  "#b388ff",
    "ok":      "#00cc66",
    "err":     "#ff6b81",
    "warn":    "#ffb347",
    "info":    "#58a6ff",
}

# Background colour of the launcher_bg.png top area – used for header widgets so
# they blend into the gradient background instead of showing a solid #222222 box.
TOP_BG = "#0B0C15"

LANE_NAMES = [
    "Hi-Hat", "Crash", "Snare", "Tom 1",
    "Tom 2", "Tom 3", "Ride", "Kick",
]

VIZ_LANES_MIDI_IN: dict[str, set[int]] = {
    "Hi-Hat": {42, 22, 23, 44},
    "Open Hi-Hat": {46, 26, 21},
    "Crash": {49, 52, 55, 57},
    "Snare": {38, 40, 37},
    "Tom 1": {48, 50},
    "Tom 2": {45, 47},
    "Tom 3": {43, 58},
    "Ride": {51, 53, 59},
    "Kick": {36, 35},
}

# Paradiddle instrument class → (lane_idx, midi_note, lane_name)
# Mirrors CLASS_TO_MIDI in Extractor Mini App/rlrr_parse.py. Inline copy
# keeps the standalone launcher self-contained (no cross-folder import).
CLASS_TO_MIDI = {
    "BP_HiHat_C":    (0, 42, "Hi-Hat"),
    "BP_Crash15_C":  (1, 49, "Crash"),
    "BP_Crash17_C":  (1, 49, "Crash"),
    "BP_Snare_C":    (2, 38, "Snare"),
    "BP_Tom1_C":     (3, 48, "Tom 1"),
    "BP_Tom2_C":     (4, 45, "Tom 2"),
    "BP_FloorTom_C": (5, 41, "Tom 3"),
    "BP_Ride17_C":   (6, 51, "Ride"),
    "BP_Ride20_C":   (6, 51, "Ride"),
    "BP_Kick_C":     (7, 35, "Kick"),
}

KEYBINDS = [
    [0, "a"], [1, "w"], [2, "s"], [3, "d"],
    [4, "f"], [5, "c"], [6, "r"], [7, "space"],
]


def _apply_style():
    s = ttk.Style()
    s.theme_use("clam")
    s.configure("TFrame",       background=C["bg"])
    s.configure("TLabel",       background=C["bg"], foreground=C["fg"])
    s.configure("TCheckbutton", background=C["bg"], foreground=C["fg"])
    s.map("TCheckbutton",       background=[("active", C["bg"])])
    s.configure("TEntry",       fieldbackground=C["deep"], foreground=C["fg"],
                insertcolor=C["fg"])
    s.configure("TCombobox",    fieldbackground=C["deep"], foreground=C["fg"])
    s.configure("TRadiobutton", background=C["bg"], foreground=C["fg"])


def _mk_btn(parent, text, cmd, width=None):
    kw = dict(text=text, command=cmd,
              bg=C["btn_bg"], fg=C["btn_fg"],
              activebackground=C["btn_bdr"], activeforeground=C["fg"],
              relief="flat", bd=0, highlightthickness=0, padx=10, pady=4, cursor="hand2")
    if width:
        kw["width"] = width
    return tk.Button(parent, **kw)


def _midi_note_to_lane(note: int) -> int | None:
    """Map a MIDI note number to a lane index (0-7)."""
    for lane_name, notes in VIZ_LANES_MIDI_IN.items():
        if lane_name == "Open Hi-Hat":
            continue
        if note in notes:
            return LANE_NAMES.index(lane_name)
    return None


def _parse_midi_to_notes(midi_path: str) -> tuple[list[list[float | int]], float]:
    """Parse a MIDI file into [(time_secs, lane_idx), ...] honoring tempo changes."""
    try:
        import mido as _mido
    except Exception as exc:
        raise RuntimeError(f"mido is required to parse MIDI files: {exc}")

    mid = _mido.MidiFile(midi_path)
    tpb = mid.ticks_per_beat if mid.ticks_per_beat else 480

    # Build absolute-tick tempo map across ALL tracks
    tempo_map = []
    for track in mid.tracks:
        abs_tick = 0
        for msg in track:
            abs_tick += msg.time
            if msg.type == "set_tempo":
                tempo_map.append((abs_tick, msg.tempo))
    if not tempo_map:
        tempo_map = [(0, 500000)]  # default 120 BPM only if NO tempo events
    tempo_map.sort(key=lambda x: x[0])

    def ticks_to_secs(ticks: int) -> float:
        t = 0.0
        prev_tick, prev_tempo = 0, 500000
        for ct, tempo in tempo_map:
            if ct >= ticks:
                break
            t += (ct - prev_tick) * prev_tempo / tpb / 1_000_000
            prev_tick, prev_tempo = ct, tempo
        t += (ticks - prev_tick) * prev_tempo / tpb / 1_000_000
        return t

    notes_out: list[list[float | int]] = []
    for track in mid.tracks:
        abs_tick = 0
        for msg in track:
            abs_tick += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                lane = _midi_note_to_lane(msg.note)
                if lane is not None:
                    notes_out.append([ticks_to_secs(abs_tick), lane])

    notes_out.sort(key=lambda x: x[0])
    bpm = 60_000_000.0 / tempo_map[0][1]
    return notes_out, bpm


def _parse_rlrr_to_notes(rlrr_path: str) -> tuple[list[list[float | int]], float] | tuple[None, None]:
    """Parse a Paradiddle .rlrr into ([(time_sec, lane_idx), ...], bpm).
    Returns (None, None) on any failure. The .rlrr's `events[].time` is
    authoritative seconds — independent of MIDI tick interpretation.
    """
    try:
        import json as _json
        text = None
        for enc in ("utf-8-sig", "utf-8", "utf-16", "cp1252"):
            try:
                with open(rlrr_path, "r", encoding=enc) as f:
                    text = f.read()
                break
            except (UnicodeDecodeError, LookupError):
                continue
        if text is None:
            return None, None
        data = _json.loads(text)
        events = data.get("events", []) or []
        instruments = data.get("instruments", []) or []
        bpm_events = data.get("bpmEvents", []) or []
        bpm = 120.0
        if bpm_events:
            try:
                bpm = float(bpm_events[0].get("bpm", 120.0))
            except Exception:
                bpm = 120.0
        # Build midi note → lane idx using LANE_NAMES + VIZ_LANES_MIDI_IN
        midi_to_lane: dict[int, int] = {}
        for lane_name, notes_set in VIZ_LANES_MIDI_IN.items():
            if lane_name == "Open Hi-Hat":
                continue
            if lane_name in LANE_NAMES:
                lane_idx = LANE_NAMES.index(lane_name)
                for n in notes_set:
                    midi_to_lane[n] = lane_idx
        notes_out: list[list[float | int]] = []
        for e in events:
            cls = ""
            if "name" in e:
                name_val = str(e.get("name") or "")
                for c in CLASS_TO_MIDI:
                    if name_val.startswith(c):
                        cls = c
                        break
            else:
                inst_idx = e.get("instrumentIndex", -1)
                if isinstance(inst_idx, int) and 0 <= inst_idx < len(instruments):
                    cls = instruments[inst_idx].get("class", "") or ""
            mapping = CLASS_TO_MIDI.get(cls)
            if not mapping:
                continue
            _, midi_note, _ = mapping
            lane_idx = midi_to_lane.get(midi_note)
            if lane_idx is None:
                continue
            t_raw = e.get("time", 0)
            try:
                t_sec = float(t_raw)
            except (TypeError, ValueError):
                continue
            notes_out.append([t_sec, lane_idx])
        notes_out.sort(key=lambda n: n[0])
        return notes_out, bpm
    except Exception:
        return None, None


def _find_sibling_rlrr(midi_path: str, expected_note_count: int):
    """Glob the MIDI's folder for *.rlrr files; return (rlrr_path, notes, bpm)
    of the first one whose note count matches `expected_note_count` ±2.
    Returns (None, None, None) on no match.
    """
    try:
        import glob as _glob
        import os as _os
        midi_dir = _os.path.dirname(midi_path)
        if not midi_dir or not _os.path.isdir(midi_dir):
            return None, None, None
        for s_path in sorted(_glob.glob(_os.path.join(midi_dir, "*.rlrr"))):
            r_notes, r_bpm = _parse_rlrr_to_notes(s_path)
            if r_notes is None:
                continue
            if abs(len(r_notes) - expected_note_count) <= 2:
                return s_path, r_notes, r_bpm
        return None, None, None
    except Exception:
        return None, None, None


def _get_audio_length_secs(audio_path: str):
    """Return audio file duration in seconds via mutagen, or None."""
    if not audio_path:
        return None
    try:
        import os as _os
        if not _os.path.exists(audio_path):
            return None
        import mutagen as _mutagen
        af = _mutagen.File(audio_path)
        if af and af.info and getattr(af.info, "length", None):
            return float(af.info.length)
    except Exception:
        pass
    return None


def _get_midi_devices() -> list[str]:
    """Return available MIDI input device names."""
    try:
        import mido as _mido
        return list(_mido.get_input_names())
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Launcher App
# ---------------------------------------------------------------------------
class PracticeLauncher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ParaKit Practice Launcher")
        self.geometry("720x860")
        self.resizable(False, False)
        self.configure(bg=C["bg"], highlightthickness=0, bd=0)

        _apply_style()
        self.option_add("*Background", C["bg"])
        self.option_add("*Foreground", C["fg"])

        self._midi_path: str | None = None
        self._audio_path: str = ""
        self._drum_path: str = ""
        # v4.4.57.91-9 -- MIDI hit echo: tracks the live rtmidi listener
        # opened when a real MIDI device is selected via the combobox.
        # Used to log incoming pad hits to the launcher's _log_msg system
        # so the user can verify drumkit signal is reaching the launcher
        # (mirrors v1 in-app Practice tab's MIDI Control Settings panel
        # which echoes hits as they arrive). Closed before launch (so the
        # Pygame game subprocess gets exclusive access on Windows MM API)
        # and on app exit.
        self._midi_listener = None
        self._proc: subprocess.Popen | None = None
        self._temp_config: str | None = None
        self._temp_results: str | None = None
        self._recent_path = Path(__file__).parent / "recent_files.json"
        self._recent: dict[str, list[str]] = {"midi": [], "audio": [], "drum": [], "rlrr": []}
        self._midi_locked = False
        self._load_recent()

        # Asset loading
        self._asset_dir = Path(__file__).parent / "assets" / "ui"
        self._tk_img: dict[str, tk.PhotoImage] = {}
        self._pil_cache: dict[str, Image.Image] = {}
        self._load_assets()

        self._build_ui()
        self._poll_proc()

    def _load_pil(self, name: str) -> Image.Image | None:
        if name in self._pil_cache:
            return self._pil_cache[name]
        if not _HAS_PIL:
            return None
        path = self._asset_dir / name
        if not path.is_file():
            return None
        img = Image.open(path)
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        self._pil_cache[name] = img
        return img

    def _pil_to_tk(self, img: Image.Image) -> tk.PhotoImage:
        return ImageTk.PhotoImage(img)

    def _stretch(self, name: str, w: int, h: int) -> tk.PhotoImage | None:
        img = self._load_pil(name)
        if img is None:
            return None
        return self._pil_to_tk(img.resize((w, h), Image.Resampling.LANCZOS))

    def _nine_slice(self, name: str, w: int, h: int, inset: int) -> tk.PhotoImage | None:
        src = self._load_pil(name)
        if src is None:
            return None
        src_w, src_h = src.size
        if w <= 2 * inset or h <= 2 * inset:
            return self._pil_to_tk(src.resize((w, h), Image.Resampling.LANCZOS))
        out = Image.new("RGBA", (w, h))
        # Corners
        out.paste(src.crop((0, 0, inset, inset)), (0, 0))
        out.paste(src.crop((src_w - inset, 0, src_w, inset)), (w - inset, 0))
        out.paste(src.crop((0, src_h - inset, inset, src_h)), (0, h - inset))
        out.paste(src.crop((src_w - inset, src_h - inset, src_w, src_h)), (w - inset, h - inset))
        # Edges
        out.paste(src.crop((inset, 0, src_w - inset, inset)).resize((w - 2 * inset, inset), Image.Resampling.LANCZOS), (inset, 0))
        out.paste(src.crop((inset, src_h - inset, src_w - inset, src_h)).resize((w - 2 * inset, inset), Image.Resampling.LANCZOS), (inset, h - inset))
        out.paste(src.crop((0, inset, inset, src_h - inset)).resize((inset, h - 2 * inset), Image.Resampling.LANCZOS), (0, inset))
        out.paste(src.crop((src_w - inset, inset, src_w, src_h - inset)).resize((inset, h - 2 * inset), Image.Resampling.LANCZOS), (w - inset, inset))
        # Center
        out.paste(src.crop((inset, inset, src_w - inset, src_h - inset)).resize((w - 2 * inset, h - 2 * inset), Image.Resampling.LANCZOS), (inset, inset))
        return self._pil_to_tk(out)

    def _load_assets(self):
        if not _HAS_PIL:
            return
        for key, path in [
            ("bg", "launcher/launcher_bg.png"),
            ("separator", "launcher/launcher_separator.png"),
            ("browse", "launcher/launcher_btn_browse.png"),
            ("browse_hover", "launcher/launcher_btn_browse_hover.png"),
            ("browse_pressed", "launcher/launcher_btn_browse_pressed.png"),
            ("launch", "launcher/launcher_btn_launch.png"),
            ("launch_hover", "launcher/launcher_btn_launch_hover.png"),
            ("launch_pressed", "launcher/launcher_btn_launch_pressed.png"),
        ]:
            img = self._load_pil(path)
            if img:
                self._tk_img[key] = self._pil_to_tk(img)
        # Logo – alpha-key dark border pixels so the resized image doesn't show a
        # baked-in near-black rectangle against the dark gradient background.
        logo_img = self._load_pil("FINAL PARAKIT LOGO.png")
        if logo_img:
            logo_img = logo_img.convert("RGBA")
            px = logo_img.load()
            w, h = logo_img.size
            for y in range(h):
                for x in range(w):
                    r, g, b, a = px[x, y]
                    if a > 0 and r < 34 and g < 26 and b < 54:
                        px[x, y] = (r, g, b, 0)
            # Crop to the remaining content bbox
            bbox = logo_img.getbbox()
            if bbox:
                logo_img = logo_img.crop(bbox)
            logo_img = logo_img.resize((48, 48), Image.Resampling.LANCZOS)
            self._tk_img["logo"] = self._pil_to_tk(logo_img)
        # Resize background to window height
        bg_img = self._load_pil("launcher/launcher_bg.png")
        if bg_img:
            bg_img = bg_img.resize((720, 860), Image.Resampling.LANCZOS)
            self._tk_img["bg"] = self._pil_to_tk(bg_img)

    def _img_btn(self, parent, img_key: str, cmd, hover_key: str | None = None, pressed_key: str | None = None, bg=None):
        """Return a tk.Label that acts like an image button with hover/press states."""
        lbl = tk.Label(parent, image=self._tk_img.get(img_key), bg=bg if bg else C["bg"], cursor="hand2", bd=0, highlightthickness=0)

        def on_enter(e):
            if hover_key and hover_key in self._tk_img:
                lbl.config(image=self._tk_img[hover_key])

        def on_leave(e):
            lbl.config(image=self._tk_img.get(img_key))

        def on_press(e):
            if pressed_key and pressed_key in self._tk_img:
                lbl.config(image=self._tk_img[pressed_key])

        def on_release(e):
            if hover_key and hover_key in self._tk_img:
                lbl.config(image=self._tk_img[hover_key])
            else:
                lbl.config(image=self._tk_img.get(img_key))
            cmd()

        lbl.bind("<Enter>", on_enter)
        lbl.bind("<Leave>", on_leave)
        lbl.bind("<ButtonPress-1>", on_press)
        lbl.bind("<ButtonRelease-1>", on_release)
        return lbl

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        W = 720
        H = 860
        PAD_X = 20
        CARD_W = W - 2 * PAD_X  # 680
        CARD_CONTENT_X = PAD_X + 12  # 32
        CARD_CONTENT_W = CARD_W - 24  # 656
        self._card_images: list[tk.PhotoImage] = []

        # --- Background ---
        if "bg" in self._tk_img:
            tk.Label(self, image=self._tk_img["bg"], bd=0, highlightthickness=0, padx=0, pady=0).place(
                x=0, y=0, width=W, height=H)
        else:
            self.configure(bg=C["bg"])

        # --- Header with logo ---
        if "logo" in self._tk_img:
            tk.Label(self, image=self._tk_img["logo"], bg=TOP_BG, bd=0,
                     highlightthickness=0, padx=0, pady=0).place(x=PAD_X, y=14)
            title_x = PAD_X + 56
        else:
            title_x = PAD_X
        tk.Label(self, bd=0, text="ParaKit Practice Window v2", bg=TOP_BG, fg=C["purple"],
                 font=("Segoe UI", 16, "bold"), highlightthickness=0,
                 padx=0, pady=0).place(x=title_x, y=16)
        tk.Label(self, bd=0, text="Standalone launcher", bg=TOP_BG, fg=C["muted"],
                 font=("Segoe UI", 9), highlightthickness=0,
                 padx=0, pady=0).place(x=title_x, y=44)

        # Info note about .rlrr vs MIDI
        tk.Label(self, bd=0,
                 text="Tip: Use .rlrr files for songs downloaded or created outside ParaKit. "
                      "MIDI files are fine for in-app creations, otherwise notes and audio may be out of sync.",
                 bg=TOP_BG, fg=C["warn"], font=("Segoe UI", 8),
                 wraplength=CARD_W, justify="left", highlightthickness=0,
                 padx=0, pady=0).place(x=PAD_X, y=64)

        # --- Separator ---
        sep_y = 90
        sep_img = self._nine_slice("launcher/launcher_separator.png", CARD_W, 2, 1)
        if sep_img:
            self._card_images.append(sep_img)
            tk.Label(self, image=sep_img, bd=0, highlightthickness=0, padx=0, pady=0).place(
                x=PAD_X, y=sep_y, width=CARD_W, height=2)

        def _card_bg(y: int, h: int) -> None:
            img = self._nine_slice("launcher/launcher_card_bg.png", CARD_W, h, 12)
            if img:
                self._card_images.append(img)
                tk.Label(self, image=img, bd=0, highlightthickness=0, padx=0, pady=0).place(
                    x=PAD_X, y=y, width=CARD_W, height=h)

        # --- MIDI Card ---
        midi_y = 96
        midi_h = 108
        _card_bg(midi_y, midi_h)
        midi_frame = tk.Frame(self, bd=0, bg="#131321", highlightthickness=0)
        midi_frame.place(x=CARD_CONTENT_X, y=midi_y + 4, width=CARD_CONTENT_W, height=26)

        self._midi_browse = self._img_btn(midi_frame, "browse", self._pick_midi,
                                          hover_key="browse_hover", pressed_key="browse_pressed",
                                          bg="#131321")
        self._midi_browse.pack(side="left")

        self._midi_var = tk.StringVar(value="No MIDI file selected")
        self._midi_label = tk.Label(midi_frame, bd=0, textvariable=self._midi_var, bg="#131321",
                 fg=C["muted"], font=("Segoe UI", 9), highlightthickness=0)
        self._midi_label.pack(side="left", padx=(8, 0))

        self._recent_midi_frame = tk.Frame(self, bd=0, bg="#131321", highlightthickness=0)
        self._recent_midi_frame.place(x=CARD_CONTENT_X, y=midi_y + 26,
                                      width=CARD_CONTENT_W, height=24)
        tk.Label(self._recent_midi_frame, bd=0, text="Recent:", bg="#131321",
                 fg=C["muted"], font=("Segoe UI", 8), highlightthickness=0).pack(side="left")
        self._recent_midi_combo = ttk.Combobox(self._recent_midi_frame, state="readonly", width=38, takefocus=0)
        self._recent_midi_combo.pack(side="left", padx=(6, 0))
        _mk_btn(self._recent_midi_frame, "×", self._remove_recent_midi, width=3).pack(side="left", padx=(2, 0))
        _mk_btn(self._recent_midi_frame, "Clear", self._clear_recent_midi, width=6).pack(side="left", padx=(2, 0))
        self._recent_midi_combo.bind("<<ComboboxSelected>>", lambda e: self._on_recent_midi_select())
        self._recent_midi_paths: list[str] = []

        # Source .rlrr row (inside MIDI card)
        rlrr_frame = tk.Frame(self, bd=0, bg="#131321", highlightthickness=0)
        rlrr_frame.place(x=CARD_CONTENT_X, y=midi_y + 50, width=CARD_CONTENT_W, height=26)
        tk.Label(rlrr_frame, bd=0, text="Source .rlrr:", bg="#131321", fg=C["fg"],
                 font=("Segoe UI", 9, "bold"), width=10, anchor="w",
                 highlightthickness=0).pack(side="left")
        self._rlrr_path = ""
        self._rlrr_var = tk.StringVar(value="")
        # v4.4.57.91-9 -- replaced ttk.Entry with tk.Entry. Windows native theme
        # intermittently overrides the clam-theme TEntry fieldbackground/foreground
        # style, causing white-on-white text in this field. tk.Entry respects
        # bg/fg directly without TTK style-engine override risk.
        tk.Entry(rlrr_frame, textvariable=self._rlrr_var,
                 font=("Segoe UI", 9),
                 bg=C["deep"], fg=C["fg"], insertbackground=C["fg"],
                 relief="flat", highlightthickness=1,
                 highlightbackground="#3a3a4a",
                 highlightcolor="#3a3a4a", bd=0).pack(
                     side="left", fill="x", expand=True, padx=(6, 6))
        self._rlrr_browse = self._img_btn(rlrr_frame, "browse", self._pick_rlrr,
                                          hover_key="browse_hover", pressed_key="browse_pressed",
                                          bg="#131321")
        self._rlrr_browse.pack(side="left")
        _mk_btn(rlrr_frame, "Clear", self._clear_rlrr, width=6).pack(side="left", padx=(4, 0))

        # Recent .rlrr files
        self._recent_rlrr_frame = tk.Frame(self, bd=0, bg="#131321", highlightthickness=0)
        self._recent_rlrr_frame.place(x=CARD_CONTENT_X, y=midi_y + 76,
                                      width=CARD_CONTENT_W, height=24)
        tk.Label(self._recent_rlrr_frame, bd=0, text="Recent:", bg="#131321",
                 fg=C["muted"], font=("Segoe UI", 8), highlightthickness=0).pack(side="left")
        self._recent_rlrr_combo = ttk.Combobox(self._recent_rlrr_frame, state="readonly", width=38, takefocus=0)
        self._recent_rlrr_combo.pack(side="left", padx=(6, 0))
        _mk_btn(self._recent_rlrr_frame, "×", self._remove_recent_rlrr, width=3).pack(side="left", padx=(2, 0))
        _mk_btn(self._recent_rlrr_frame, "Clear", self._clear_recent_rlrr, width=6).pack(side="left", padx=(2, 0))
        self._recent_rlrr_combo.bind("<<ComboboxSelected>>", lambda e: self._on_recent_rlrr_select())
        self._recent_rlrr_paths: list[str] = []

        # --- Audio Card ---
        audio_y = 212
        audio_h = 160
        _card_bg(audio_y, audio_h)

        # Full Mix row
        mix_frame = tk.Frame(self, bd=0, bg="#131321", highlightthickness=0)
        mix_frame.place(x=CARD_CONTENT_X, y=audio_y + 6, width=CARD_CONTENT_W, height=26)
        tk.Label(mix_frame, bd=0, text="Full Mix:", bg="#131321", fg=C["fg"],
                 font=("Segoe UI", 9, "bold"), width=10, anchor="w",
                 highlightthickness=0).pack(side="left")
        self._audio_var = tk.StringVar(value="")
        # v4.4.57.91-9 -- ttk.Entry → tk.Entry per white-on-white fix (see rlrr field above).
        tk.Entry(mix_frame, textvariable=self._audio_var,
                 font=("Segoe UI", 9),
                 bg=C["deep"], fg=C["fg"], insertbackground=C["fg"],
                 relief="flat", highlightthickness=1,
                 highlightbackground="#3a3a4a",
                 highlightcolor="#3a3a4a", bd=0).pack(
                     side="left", fill="x", expand=True, padx=(6, 6))
        self._audio_browse = self._img_btn(mix_frame, "browse", self._pick_audio,
                                           hover_key="browse_hover", pressed_key="browse_pressed",
                                           bg="#131321")
        self._audio_browse.pack(side="left")
        _mk_btn(mix_frame, "Clear", self._clear_audio, width=6).pack(side="left", padx=(4, 0))

        # Full Mix recent files
        self._recent_audio_frame = tk.Frame(self, bd=0, bg="#131321", highlightthickness=0)
        self._recent_audio_frame.place(x=CARD_CONTENT_X, y=audio_y + 34,
                                       width=CARD_CONTENT_W, height=24)
        tk.Label(self._recent_audio_frame, bd=0, text="Recent:", bg="#131321",
                 fg=C["muted"], font=("Segoe UI", 8), highlightthickness=0).pack(side="left")
        self._recent_audio_combo = ttk.Combobox(self._recent_audio_frame, state="readonly", width=38, takefocus=0)
        self._recent_audio_combo.pack(side="left", padx=(6, 0))
        _mk_btn(self._recent_audio_frame, "×", self._remove_recent_audio, width=3).pack(side="left", padx=(2, 0))
        _mk_btn(self._recent_audio_frame, "Clear", self._clear_recent_audio, width=6).pack(side="left", padx=(2, 0))
        self._recent_audio_combo.bind("<<ComboboxSelected>>", lambda e: self._on_recent_audio_select())
        self._recent_audio_paths: list[str] = []

        # Drum Stem row
        drum_frame = tk.Frame(self, bd=0, bg="#131321", highlightthickness=0)
        drum_frame.place(x=CARD_CONTENT_X, y=audio_y + 68, width=CARD_CONTENT_W, height=26)
        tk.Label(drum_frame, bd=0, text="Drum Stem:", bg="#131321", fg=C["fg"],
                 font=("Segoe UI", 9, "bold"), width=10, anchor="w",
                 highlightthickness=0).pack(side="left")
        self._drum_var = tk.StringVar(value="")
        # v4.4.57.91-9 -- ttk.Entry → tk.Entry per white-on-white fix (see rlrr field above).
        tk.Entry(drum_frame, textvariable=self._drum_var,
                 font=("Segoe UI", 9),
                 bg=C["deep"], fg=C["fg"], insertbackground=C["fg"],
                 relief="flat", highlightthickness=1,
                 highlightbackground="#3a3a4a",
                 highlightcolor="#3a3a4a", bd=0).pack(
                     side="left", fill="x", expand=True, padx=(6, 6))
        self._drum_browse = self._img_btn(drum_frame, "browse", self._pick_drum,
                                          hover_key="browse_hover", pressed_key="browse_pressed",
                                          bg="#131321")
        self._drum_browse.pack(side="left")
        _mk_btn(drum_frame, "Clear", self._clear_drum, width=6).pack(side="left", padx=(4, 0))

        # Drum Stem recent files
        self._recent_drum_frame = tk.Frame(self, bd=0, bg="#131321", highlightthickness=0)
        self._recent_drum_frame.place(x=CARD_CONTENT_X, y=audio_y + 96,
                                      width=CARD_CONTENT_W, height=24)
        tk.Label(self._recent_drum_frame, bd=0, text="Recent:", bg="#131321",
                 fg=C["muted"], font=("Segoe UI", 8), highlightthickness=0).pack(side="left")
        self._recent_drum_combo = ttk.Combobox(self._recent_drum_frame, state="readonly", width=38, takefocus=0)
        self._recent_drum_combo.pack(side="left", padx=(6, 0))
        _mk_btn(self._recent_drum_frame, "×", self._remove_recent_drum, width=3).pack(side="left", padx=(2, 0))
        _mk_btn(self._recent_drum_frame, "Clear", self._clear_recent_drum, width=6).pack(side="left", padx=(2, 0))
        self._recent_drum_combo.bind("<<ComboboxSelected>>", lambda e: self._on_recent_drum_select())
        self._recent_drum_paths: list[str] = []

        # Track selector
        track_frame = tk.Frame(self, bd=0, bg="#131321", highlightthickness=0)
        track_frame.place(x=CARD_CONTENT_X, y=audio_y + 130, width=CARD_CONTENT_W, height=22)
        tk.Label(track_frame, bd=0, text="Default track:", bg="#131321", fg=C["muted"],
                 font=("Segoe UI", 8), highlightthickness=0).pack(side="left")
        self._track_var = tk.StringVar(value="auto")
        for val, lbl in (("auto", "Auto"), ("mix", "Mix"), ("drums", "Drums")):
            tk.Radiobutton(track_frame, bd=0, text=lbl, variable=self._track_var, value=val,
                           bg="#131321", fg=C["fg"], selectcolor=C["deep"],
                           activebackground="#131321", activeforeground=C["fg"],
                           font=("Segoe UI", 9), highlightthickness=0).pack(
                               side="left", padx=(8, 8))

        # --- Settings Card ---
        set_y = 380
        set_h = 130
        _card_bg(set_y, set_h)

        toggles_frame = tk.Frame(self, bd=0, bg="#131321", highlightthickness=0)
        toggles_frame.place(x=CARD_CONTENT_X, y=set_y + 6, width=CARD_CONTENT_W, height=24)
        self._auto_kick_var = tk.BooleanVar(value=False)
        tk.Checkbutton(toggles_frame, bd=0, text="Auto-Kick", variable=self._auto_kick_var,
                       bg="#131321", fg=C["fg"], selectcolor=C["deep"],
                       activebackground="#131321", activeforeground=C["fg"],
                       font=("Segoe UI", 9), highlightthickness=0).pack(
                           side="left", padx=(0, 14))
        self._square_var = tk.BooleanVar(value=False)
        tk.Checkbutton(toggles_frame, bd=0, text="Square notes", variable=self._square_var,
                       bg="#131321", fg=C["fg"], selectcolor=C["deep"],
                       activebackground="#131321", activeforeground=C["fg"],
                       font=("Segoe UI", 9), highlightthickness=0).pack(
                           side="left", padx=(0, 14))
        self._kick_line_var = tk.BooleanVar(value=False)
        tk.Checkbutton(toggles_frame, bd=0, text="Kick line", variable=self._kick_line_var,
                       bg="#131321", fg=C["fg"], selectcolor=C["deep"],
                       activebackground="#131321", activeforeground=C["fg"],
                       font=("Segoe UI", 9), highlightthickness=0).pack(
                           side="left", padx=(0, 14))
        self._beat_grid_var = tk.BooleanVar(value=True)
        tk.Checkbutton(toggles_frame, bd=0, text="Beat grid", variable=self._beat_grid_var,
                       bg="#131321", fg=C["fg"], selectcolor=C["deep"],
                       activebackground="#131321", activeforeground=C["fg"],
                       font=("Segoe UI", 9), highlightthickness=0).pack(side="left")

        nums_frame = tk.Frame(self, bd=0, bg="#131321", highlightthickness=0)
        nums_frame.place(x=CARD_CONTENT_X, y=set_y + 34, width=CARD_CONTENT_W, height=32)
        ft_frm = tk.Frame(nums_frame, bd=0, bg="#131321", highlightthickness=0)
        ft_frm.pack(side="left")
        tk.Label(ft_frm, bd=0, text="Fall time", bg="#131321", fg=C["muted"],
                 font=("Segoe UI", 8), highlightthickness=0).pack(anchor="w")
        self._fall_var = tk.StringVar(value="4.0")
        ttk.Entry(ft_frm, textvariable=self._fall_var, width=8,
                 font=("Segoe UI", 9), justify="center").pack(anchor="w", pady=(2, 0))

        ns_frm = tk.Frame(nums_frame, bd=0, bg="#131321", highlightthickness=0)
        ns_frm.pack(side="left", padx=(18, 0))
        tk.Label(ns_frm, bd=0, text="Note size", bg="#131321", fg=C["muted"],
                 font=("Segoe UI", 8), highlightthickness=0).pack(anchor="w")
        self._size_var = tk.StringVar(value="2.3")
        ttk.Entry(ns_frm, textvariable=self._size_var, width=8,
                 font=("Segoe UI", 9), justify="center").pack(anchor="w", pady=(2, 0))

        tk.Label(self, bd=0, text="Lane visibility", bg="#131321",
                 fg=C["muted"], font=("Segoe UI", 8), highlightthickness=0).place(
                     x=CARD_CONTENT_X, y=set_y + 72)
        lrow = tk.Frame(self, bd=0, bg="#131321", highlightthickness=0)
        lrow.place(x=CARD_CONTENT_X, y=set_y + 92, width=CARD_CONTENT_W, height=28)
        self._lane_vars: list[tk.BooleanVar] = []
        for i, name in enumerate(LANE_NAMES):
            var = tk.BooleanVar(value=True)
            self._lane_vars.append(var)
            tk.Checkbutton(lrow, bd=0, text=name, variable=var,
                           bg="#131321", fg=C["fg"], selectcolor=C["deep"],
                           activebackground="#131321", activeforeground=C["fg"],
                           font=("Segoe UI", 9), highlightthickness=0).pack(
                               side="left", padx=(0, 12))

        # --- MIDI Input Card ---
        in_y = 518
        in_h = 68
        _card_bg(in_y, in_h)

        in_row = tk.Frame(self, bd=0, bg="#131321", highlightthickness=0)
        in_row.place(x=CARD_CONTENT_X, y=in_y + 6, width=CARD_CONTENT_W, height=28)
        tk.Label(in_row, bd=0, text="Device", bg="#131321", fg=C["fg"],
                 width=8, anchor="w", font=("Segoe UI", 9, "bold"),
                 highlightthickness=0).pack(side="left")
        self._device_combo = ttk.Combobox(in_row, state="readonly", width=28, takefocus=0)
        self._device_combo.pack(side="left", padx=(8, 8))
        # v4.4.57.91-9 -- bind device selection to MIDI hit echo handler so
        # the user gets log feedback when pads are hit at the device layer
        # (vs only at game-window layer post-launch). See _on_device_select.
        self._device_combo.bind("<<ComboboxSelected>>",
                                 lambda _e: self._on_device_select())
        _mk_btn(in_row, "Refresh", self._refresh_devices, width=10).pack(side="left")

        self._midi_status_var = tk.StringVar(value="<No device — keyboard mode>")
        tk.Label(self, bd=0, textvariable=self._midi_status_var,
                 bg="#131321", fg=C["muted"], font=("Segoe UI", 8),
                 highlightthickness=0).place(x=CARD_CONTENT_X, y=in_y + 40)

        # --- Launch button ---
        launch_y = 594
        launch_w = 400
        launch_h = 48
        launch_x = (W - launch_w) // 2
        self._launch_btn = self._img_btn(self, "launch", self._do_launch,
                                         hover_key="launch_hover", pressed_key="launch_pressed")
        self._launch_btn.place(x=launch_x, y=launch_y, width=launch_w, height=launch_h)
        self._launch_disabled = False

        # --- Log ---
        log_label_y = 650
        tk.Label(self, bd=0, text="Log", bg="#181122", fg=C["purple"],
                 font=("Segoe UI", 9, "bold"), highlightthickness=0,
                 padx=0, pady=0).place(x=PAD_X, y=log_label_y)

        log_y = 668
        log_h = 192
        log_frame = tk.Frame(self, bd=0, bg="#181122", highlightthickness=0)
        log_frame.place(x=PAD_X, y=log_y, width=CARD_W, height=log_h)

        self._log = tk.Text(log_frame, height=8, state="disabled", wrap="word",
                            bg=C["deep"], fg=C["info"],
                            insertbackground=C["fg"],
                            selectbackground=C["btn_bdr"],
                            relief="flat", bd=0, padx=6, pady=4,
                            font=("Consolas", 9), highlightthickness=0)
        _sb = ttk.Scrollbar(log_frame, orient="vertical", command=self._log.yview)
        self._log.configure(yscrollcommand=_sb.set)
        _sb.pack(side="right", fill="y")
        self._log.pack(fill="both", expand=True)
        self._log.tag_config("ok",   foreground=C["ok"])
        self._log.tag_config("err",  foreground=C["err"])
        self._log.tag_config("warn", foreground=C["warn"])
        self._log.tag_config("info", foreground=C["info"])

        self._refresh_devices()
        self._refresh_recent_ui()
        self._log_msg("Ready. Select a MIDI file and click Launch.", "info")

    # ------------------------------------------------------------------
    # Log helpers
    # ------------------------------------------------------------------
    def _log_msg(self, text: str, tag: str = "info") -> None:
        self._log.configure(state="normal")
        self._log.insert("end", f"{text}\n", tag)
        self._log.see("end")
        self._log.configure(state="disabled")

    # ------------------------------------------------------------------
    # Recent files
    # ------------------------------------------------------------------
    def _load_recent(self):
        try:
            if self._recent_path.is_file():
                data = json.loads(self._recent_path.read_text(encoding="utf-8"))
                self._recent = {
                    "midi": [p for p in data.get("midi", []) if Path(p).is_file()][:8],
                    "audio": [p for p in data.get("audio", []) if Path(p).is_file()][:8],
                    "drum": [p for p in data.get("drum", []) if Path(p).is_file()][:8],
                    "rlrr": [p for p in data.get("rlrr", []) if Path(p).is_file()][:8],
                }
        except Exception:
            pass

    def _save_recent(self):
        try:
            self._recent_path.write_text(json.dumps(self._recent, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _add_recent(self, category: str, path: str):
        if not path or not Path(path).is_file():
            return
        lst = self._recent.get(category, [])
        if path in lst:
            lst.remove(path)
        lst.insert(0, path)
        self._recent[category] = lst[:8]
        self._save_recent()
        self._refresh_recent_ui()

    def _on_recent_midi_select(self):
        if self._midi_locked:
            return
        idx = self._recent_midi_combo.current()
        if 0 <= idx < len(self._recent_midi_paths):
            self._set_midi(self._recent_midi_paths[idx])

    def _on_recent_audio_select(self):
        idx = self._recent_audio_combo.current()
        if 0 <= idx < len(self._recent_audio_paths):
            self._set_audio(self._recent_audio_paths[idx])

    def _on_recent_drum_select(self):
        idx = self._recent_drum_combo.current()
        if 0 <= idx < len(self._recent_drum_paths):
            self._set_drum(self._recent_drum_paths[idx])

    def _on_recent_rlrr_select(self):
        idx = self._recent_rlrr_combo.current()
        if 0 <= idx < len(self._recent_rlrr_paths):
            path = self._recent_rlrr_paths[idx]
            self._rlrr_path = path
            self._rlrr_var.set(path)
            self._update_midi_lock()
            self._log_msg(f"Source .rlrr selected from recent: {path}", "info")

    def _refresh_recent_ui(self):
        # MIDI
        midi_files = self._recent.get("midi", [])
        self._recent_midi_paths = midi_files
        if midi_files:
            self._recent_midi_combo["values"] = [Path(p).name for p in midi_files]
            self._recent_midi_combo.set(Path(midi_files[0]).name)
        else:
            self._recent_midi_combo["values"] = []
            self._recent_midi_combo.set("(none)")

        # Audio
        audio_files = self._recent.get("audio", [])
        self._recent_audio_paths = audio_files
        if audio_files:
            self._recent_audio_combo["values"] = [Path(p).name for p in audio_files]
            self._recent_audio_combo.set(Path(audio_files[0]).name)
        else:
            self._recent_audio_combo["values"] = []
            self._recent_audio_combo.set("(none)")

        # Drum
        drum_files = self._recent.get("drum", [])
        self._recent_drum_paths = drum_files
        if drum_files:
            self._recent_drum_combo["values"] = [Path(p).name for p in drum_files]
            self._recent_drum_combo.set(Path(drum_files[0]).name)
        else:
            self._recent_drum_combo["values"] = []
            self._recent_drum_combo.set("(none)")

        # RLRR
        rlrr_files = self._recent.get("rlrr", [])
        self._recent_rlrr_paths = rlrr_files
        if rlrr_files:
            self._recent_rlrr_combo["values"] = [Path(p).name for p in rlrr_files]
            self._recent_rlrr_combo.set(Path(rlrr_files[0]).name)
        else:
            self._recent_rlrr_combo["values"] = []
            self._recent_rlrr_combo.set("(none)")

    def _set_midi(self, path: str):
        if self._midi_locked:
            return
        self._midi_path = path
        self._midi_var.set(Path(path).name)
        self._log_msg(f"MIDI selected: {path}", "info")

    def _set_audio(self, path: str):
        self._audio_path = path
        self._audio_var.set(path)
        self._log_msg(f"Audio selected: {path}", "info")

    def _set_drum(self, path: str):
        self._drum_path = path
        self._drum_var.set(path)
        self._log_msg(f"Drum stem selected: {path}", "info")

    # ------------------------------------------------------------------
    # File pickers
    # ------------------------------------------------------------------
    def _pick_midi(self):
        if self._midi_locked:
            # v4.4.57.91-9 -- explanatory feedback instead of silent return.
            # Previously this exited silently if a .rlrr was loaded, reading
            # to the user as a "Browse button doesn't work" bug. Now log a
            # clear message so the user understands MIDI Browse is locked
            # because a .rlrr is loaded (RLRR + MIDI are alternatives by
            # design; loading both is ambiguous).
            self._log_msg(
                "MIDI Browse is locked because a Source .rlrr is loaded. "
                "Clear the .rlrr first (Clear button next to .rlrr field) "
                "to choose a MIDI manually.",
                "warn")
            return
        path = filedialog.askopenfilename(
            title="Select MIDI file",
            filetypes=[("MIDI files", "*.mid *.midi"), ("All files", "*.*")])
        if path:
            self._set_midi(path)

    def _pick_audio(self):
        path = filedialog.askopenfilename(
            title="Select audio file",
            filetypes=[("Audio", "*.ogg *.flac *.wav *.mp3"), ("All files", "*.*")])
        if path:
            self._set_audio(path)

    def _pick_drum(self):
        path = filedialog.askopenfilename(
            title="Select drum stem",
            filetypes=[("Audio", "*.ogg *.flac *.wav *.mp3"), ("All files", "*.*")])
        if path:
            self._set_drum(path)

    def _pick_rlrr(self):
        path = filedialog.askopenfilename(
            title="Select Paradiddle .rlrr file",
            filetypes=[("Paradiddle chart", "*.rlrr"), ("All files", "*.*")])
        if path:
            self._rlrr_path = path
            self._rlrr_var.set(path)
            self._add_recent("rlrr", path)
            self._update_midi_lock()
            self._log_msg(f"Source .rlrr selected: {path}", "info")

    def _clear_rlrr(self):
        self._rlrr_path = ""
        self._rlrr_var.set("")
        self._update_midi_lock()
        self._log_msg("Source .rlrr cleared.", "info")

    def _clear_audio(self):
        self._audio_path = ""
        self._audio_var.set("")
        self._log_msg("Full Mix cleared.", "info")

    def _clear_drum(self):
        self._drum_path = ""
        self._drum_var.set("")
        self._log_msg("Drum Stem cleared.", "info")

    def _remove_recent_midi(self):
        idx = self._recent_midi_combo.current()
        if 0 <= idx < len(self._recent_midi_paths):
            path = self._recent_midi_paths[idx]
            self._recent["midi"] = [p for p in self._recent["midi"] if p != path]
            self._save_recent()
            self._refresh_recent_ui()
            self._log_msg(f"Removed from recent MIDI: {Path(path).name}", "info")

    def _remove_recent_audio(self):
        idx = self._recent_audio_combo.current()
        if 0 <= idx < len(self._recent_audio_paths):
            path = self._recent_audio_paths[idx]
            self._recent["audio"] = [p for p in self._recent["audio"] if p != path]
            self._save_recent()
            self._refresh_recent_ui()
            self._log_msg(f"Removed from recent audio: {Path(path).name}", "info")

    def _remove_recent_drum(self):
        idx = self._recent_drum_combo.current()
        if 0 <= idx < len(self._recent_drum_paths):
            path = self._recent_drum_paths[idx]
            self._recent["drum"] = [p for p in self._recent["drum"] if p != path]
            self._save_recent()
            self._refresh_recent_ui()
            self._log_msg(f"Removed from recent drums: {Path(path).name}", "info")

    def _remove_recent_rlrr(self):
        idx = self._recent_rlrr_combo.current()
        if 0 <= idx < len(self._recent_rlrr_paths):
            path = self._recent_rlrr_paths[idx]
            self._recent["rlrr"] = [p for p in self._recent["rlrr"] if p != path]
            self._save_recent()
            self._refresh_recent_ui()
            self._log_msg(f"Removed from recent .rlrr: {Path(path).name}", "info")

    def _clear_recent_midi(self):
        self._recent["midi"] = []
        self._save_recent()
        self._refresh_recent_ui()
        self._log_msg("Recent MIDI files cleared.", "info")

    def _clear_recent_audio(self):
        self._recent["audio"] = []
        self._save_recent()
        self._refresh_recent_ui()
        self._log_msg("Recent audio files cleared.", "info")

    def _clear_recent_drum(self):
        self._recent["drum"] = []
        self._save_recent()
        self._refresh_recent_ui()
        self._log_msg("Recent drum stems cleared.", "info")

    def _clear_recent_rlrr(self):
        self._recent["rlrr"] = []
        self._save_recent()
        self._refresh_recent_ui()
        self._log_msg("Recent .rlrr files cleared.", "info")

    def _update_midi_lock(self):
        locked = bool(self._rlrr_path)
        self._midi_locked = locked
        if locked:
            self._midi_browse.config(cursor="", bg="#2a2a3a")
            self._midi_var.set(".rlrr selected — clear .rlrr to change MIDI")
            self._midi_label.config(fg=C["warn"])
        else:
            self._midi_browse.config(cursor="hand2", bg="#131321")
            if self._midi_path:
                self._midi_var.set(Path(self._midi_path).name)
            else:
                self._midi_var.set("No MIDI file selected")
            self._midi_label.config(fg=C["muted"])

    # ------------------------------------------------------------------
    # MIDI devices
    # ------------------------------------------------------------------
    def _refresh_devices(self):
        devices = _get_midi_devices()
        if not devices:
            self._device_combo["values"] = ["(MIDI not available)"]
            self._device_combo.set("(MIDI not available)")
            self._midi_status_var.set("<No device — keyboard mode>")
            self._log_msg("No MIDI input devices found. Keyboard-only mode.", "warn")
        else:
            self._device_combo["values"] = ["(None / Keyboard only)"] + devices
            self._device_combo.set("(None / Keyboard only)")
            self._midi_status_var.set(f"{len(devices)} device(s) found — pick one or use keyboard")
            self._log_msg(f"MIDI devices: {', '.join(devices)}", "info")
        # v4.4.57.91-9 -- close any existing live listener on refresh so a
        # rescan after re-plugging a device doesn't leave a stale port open.
        self._close_midi_listener()

    # ------------------------------------------------------------------
    # v4.4.57.91-9 -- MIDI hit echo
    # ------------------------------------------------------------------
    def _on_device_select(self):
        """Combobox selection handler. Opens a live mido input on the chosen
        device (if a real device is selected) and wires _on_midi_event as
        the callback. Closes any existing listener first. The listener logs
        incoming pad hits to the launcher's _log_msg system so the user can
        verify drumkit signal is reaching the launcher BEFORE the Pygame
        game launches. The listener is closed before launch (in _do_launch)
        so the game subprocess gets exclusive device access on Windows MM."""
        device = self._device_combo.get()
        self._close_midi_listener()
        # Real device selected? (anything that's not the keyboard/no-device sentinel)
        if not device or device.startswith("(") and device.endswith(")"):
            # "(None / Keyboard only)" or "(MIDI not available)" — no listener
            self._midi_status_var.set("<No device — keyboard mode>")
            return
        try:
            import mido as _mido
            self._midi_listener = _mido.open_input(
                device, callback=self._on_midi_event)
            self._midi_status_var.set(f"Listening to: {device}")
            self._log_msg(
                f"MIDI device selected: {device}. Hit your pads to "
                f"verify signal is reaching the launcher.", "info")
        except Exception as exc:
            self._midi_listener = None
            self._midi_status_var.set(f"<Failed to open: {device}>")
            self._log_msg(
                f"ERROR: could not open MIDI device {device!r}: {exc}", "err")

    def _on_midi_event(self, msg):
        """rtmidi callback (runs on a C++ thread). Logs note_on hits to the
        launcher log via tk-safe scheduling on the main thread.

        Per RCK round-2 §7 #6 thread-safety rule: NEVER touch Tk widgets
        directly from a non-main thread. Use `self.after(0, ...)` to
        marshal the log call back to the Tk main loop."""
        try:
            if msg.type == "note_on" and msg.velocity > 0:
                # Schedule the log message on the Tk main thread.
                self.after(0,
                    lambda n=msg.note, v=msg.velocity:
                        self._log_msg(
                            f"MIDI hit: note={n} velocity={v}", "info"))
        except Exception:
            # Swallow callback exceptions — never raise on the rtmidi thread,
            # would crash the C++ side and orphan the port.
            pass

    def _close_midi_listener(self):
        """Close + clear the live MIDI listener if open. Safe to call when
        no listener exists. Called before launch (so the Pygame game gets
        exclusive device access on Windows MM) and on device-refresh."""
        if self._midi_listener is not None:
            try:
                self._midi_listener.close()
            except Exception:
                pass
            self._midi_listener = None

    # ------------------------------------------------------------------
    # Launch
    # ------------------------------------------------------------------
    def _do_launch(self):
        if self._launch_disabled:
            return

        # v4.4.57.91-9 -- accept EITHER MIDI or .rlrr as chart source.
        # Previously this required MIDI even when .rlrr was loaded
        # (early-return before the Option C RLRR-override path at
        # L1183+ could run). Same structural bug pattern as v.91-8's
        # main-app `_viz_load_notes` fix.
        has_midi = bool(self._midi_path) and os.path.isfile(self._midi_path)
        has_rlrr = bool(self._rlrr_path) and os.path.isfile(self._rlrr_path)
        if not has_midi and not has_rlrr:
            self._log_msg(
                "ERROR: Please select a chart source — either a MIDI "
                "file OR a Source .rlrr file (or both).", "err")
            return

        # Require at least one audio file
        has_mix = self._audio_path and os.path.isfile(self._audio_path)
        has_drum = self._drum_path and os.path.isfile(self._drum_path)
        if not has_mix and not has_drum:
            self._log_msg("ERROR: Please select at least one audio file (Full Mix or Drum Stem).", "err")
            return

        # v4.4.57.91-9 -- close the live MIDI hit-echo listener before
        # launching so the Pygame game subprocess gets exclusive device
        # access on Windows MM API (rtmidi can't share a port between
        # two processes on Windows). The listener was opened by
        # `_on_device_select` when the user picked a device + has been
        # logging hits as they came in. Now hand it off to the game.
        self._close_midi_listener()

        # Determine audio path (prefer Full Mix, fall back to Drum Stem)
        track = self._track_var.get()
        audio_path = ""
        if track == "drums" and has_drum:
            audio_path = self._drum_path
        elif has_mix:
            audio_path = self._audio_path
        else:
            audio_path = self._drum_path

        # Save to recent files (only what's actually loaded)
        if has_midi:
            self._add_recent("midi", self._midi_path)
        if self._audio_path:
            self._add_recent("audio", self._audio_path)
        if self._drum_path:
            self._add_recent("drum", self._drum_path)
        if self._rlrr_path:
            self._add_recent("rlrr", self._rlrr_path)

        # v4.4.57.91-9 -- chart-source resolution supports RLRR-only.
        # Priority: explicit .rlrr override > sibling .rlrr autodetect
        # > MIDI fallback. Cases:
        #   - RLRR-only (no MIDI): parse RLRR directly; skip MIDI path
        #   - MIDI-only (no RLRR): parse MIDI; check sibling RLRR
        #   - Both loaded: parse MIDI for baseline note count; RLRR
        #     override takes priority
        midi_notes: list | None = None
        midi_bpm = 120.0
        chosen_notes = None
        chosen_bpm = midi_bpm
        chosen_source = "midi"
        chosen_rlrr_path = ""

        # Parse MIDI if loaded (for baseline + fallback)
        if has_midi:
            try:
                midi_notes, midi_bpm = _parse_midi_to_notes(self._midi_path)
                chosen_bpm = midi_bpm
                self._log_msg(f"Parsed {len(midi_notes)} notes from MIDI (BPM: {midi_bpm:.1f}).", "info")
            except Exception as exc:
                self._log_msg(f"ERROR parsing MIDI: {exc}", "err")
                return

        # Option C: manual .rlrr override (highest priority)
        if has_rlrr:
            override = self._rlrr_path.strip()
            try:
                r_notes, r_bpm = _parse_rlrr_to_notes(override)
            except Exception as exc:
                self._log_msg(f"ERROR parsing .rlrr: {exc}", "err")
                if not has_midi:
                    # RLRR-only path with parse failure — bail (no MIDI fallback)
                    return
                r_notes, r_bpm = None, None
            if r_notes:
                chosen_notes = r_notes
                chosen_bpm = r_bpm or chosen_bpm
                chosen_source = "override"
                chosen_rlrr_path = override
                self._log_msg(f"Loaded {len(r_notes)} notes from .rlrr (BPM: {chosen_bpm:.1f}).", "info")

        # Option B: sibling .rlrr autodetect (only if no override hit AND MIDI loaded)
        if chosen_notes is None and has_midi:
            s_path, s_notes, s_bpm = _find_sibling_rlrr(
                self._midi_path, len(midi_notes))
            if s_notes:
                chosen_notes = s_notes
                chosen_bpm = s_bpm or midi_bpm
                chosen_source = "sibling"
                chosen_rlrr_path = s_path

        # Final selection — MIDI fallback if nothing else matched
        if chosen_notes is None and midi_notes:
            chosen_notes = midi_notes

        # v4.4.57.91-9 -- explicit catch for RLRR-only + parse failure
        # path where chosen_notes ends up None with no MIDI fallback.
        if not chosen_notes:
            self._log_msg(
                "ERROR: No playable notes found in chart source. "
                "Check the MIDI file or .rlrr for content.", "err")
            return

        # Option A: suspicious-MIDI warning (only when MIDI fallback used)
        if chosen_source == "midi" and chosen_notes:
            _audio_secs = _get_audio_length_secs(audio_path)
            if _audio_secs and _audio_secs > 0:
                midi_end = chosen_notes[-1][0]
                diff_secs = abs(midi_end - _audio_secs)
                if diff_secs > 5.0 and (diff_secs / _audio_secs) > 0.03:
                    from tkinter import messagebox as _mb
                    _mb.showwarning(
                        "MIDI timing may be off",
                        "The MIDI you just loaded ends "
                        f"{midi_end:.1f}s into the chart, but the paired audio "
                        f"is {_audio_secs:.1f}s long — they don't match.\n\n"
                        "Most likely cause: this MIDI was extracted with an "
                        "older version of ParaKit that had a known sync bug. "
                        "The current MIDI Extractor is fixed.\n\n"
                        "What to do:\n"
                        "  - Re-extract this chart from its source .rlrr using "
                        "the MIDI Extractor in ParaKit.\n"
                        "  - OR fill in the 'Source .rlrr:' field above — "
                        "point it at the chart's .rlrr file.\n"
                        "  - OR keep the .rlrr in the same folder as the "
                        "MIDI and the launcher will find it automatically "
                        "next time.\n\n"
                        "If this is a MIDI from somewhere other than "
                        "Paradiddle, the warning may be a false alarm — the "
                        "MIDI might just not match the audio you loaded. Try "
                        "a different audio file.")

        # Status feedback
        if chosen_source == "override" and chosen_rlrr_path:
            self._log_msg(
                f"Loaded timing from {os.path.basename(chosen_rlrr_path)} "
                "(from the 'Source .rlrr:' field)", "info")
        elif chosen_source == "sibling" and chosen_rlrr_path:
            self._log_msg(
                f"Loaded timing from {os.path.basename(chosen_rlrr_path)} "
                "(auto-detected next to the MIDI)", "info")

        if not chosen_notes:
            self._log_msg("WARNING: No playable notes found in the MIDI file.", "warn")

        # Build song display name from audio metadata or filename
        song_display_name = ""
        _src_audio = audio_path or self._midi_path or ""
        if _src_audio:
            try:
                import mutagen as _mutagen2
                af = _mutagen2.File(_src_audio)
                if af:
                    title = ""
                    artist = ""
                    if hasattr(af, "tags") and af.tags:
                        title = af.tags.get("TIT2", [""])[0] if hasattr(af.tags, "get") else ""
                        artist = af.tags.get("TPE1", [""])[0] if hasattr(af.tags, "get") else ""
                    if not title and hasattr(af, "get"):
                        title = af.get("title", [""])[0] if af.get("title") else ""
                        artist = af.get("artist", [""])[0] if af.get("artist") else ""
                    if title and artist:
                        song_display_name = f"{artist} - {title}"
                    elif title:
                        song_display_name = title
                    elif artist:
                        song_display_name = artist
            except Exception:
                pass
            if not song_display_name:
                song_display_name = Path(_src_audio).stem.replace("_", " ")

        # Build config
        device = self._device_combo.get()
        midi_port_name = device if device not in ("(None / Keyboard only)", "(MIDI not available)") else None

        try:
            fall_time = float(self._fall_var.get())
        except Exception:
            fall_time = 4.0
        try:
            note_size = float(self._size_var.get())
        except Exception:
            note_size = 1.0

        lane_visible = [bool(v.get()) for v in self._lane_vars]

        config = {
            "notes": [[float(t), int(l)] for t, l in chosen_notes],
            "audio_path": audio_path,
            "audio_mix_path": self._audio_path if has_mix else "",
            "audio_drum_path": self._drum_path if has_drum else "",
            "audio_track": track,
            "bpm": chosen_bpm,
            "offset_secs": 0.0,
            "input_latency_ms": 0.0,
            "lane_visible": lane_visible,
            "auto_kick": bool(self._auto_kick_var.get()),
            "note_size_scale": note_size,
            "square_notes": bool(self._square_var.get()),
            "kick_full_line": bool(self._kick_line_var.get()),
            "beat_grid": bool(self._beat_grid_var.get()),
            "keybinds": KEYBINDS,
            "midi_device_profile": None,
            "midi_port_name": midi_port_name,
            "midi_user_lane_overrides": {},
            "midi_hh_mode": "auto",
            "midi_open_hihat_lane": False,
            "fall_time_secs": fall_time,
            "window_size": [1920, 1080],
            "fullscreen": False,
            "orientation_hint": "landscape",
            "results_path": "",
            "song_display_name": song_display_name,
        }

        # Write temp files
        try:
            fd_cfg, config_path = tempfile.mkstemp(suffix=".json", prefix="practice_cfg_")
            with os.fdopen(fd_cfg, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2)

            fd_res, results_path = tempfile.mkstemp(suffix=".json", prefix="practice_res_")
            os.close(fd_res)
            config["results_path"] = results_path

            # Update config with results path
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2)
        except Exception as exc:
            self._log_msg(f"ERROR writing temp config: {exc}", "err")
            return

        self._temp_config = config_path
        self._temp_results = results_path

        # Spawn subprocess (capture stderr so crashes are visible in the log)
        here = Path(__file__).parent.resolve()
        try:
            self._stderr_path = Path(tempfile.mktemp(suffix=".txt", prefix="practice_stderr_"))
            self._stderr_file = open(self._stderr_path, "w", encoding="utf-8")
            self._proc = subprocess.Popen(
                [sys.executable, str(here / "main.py"), config_path],
                cwd=str(here),
                stderr=self._stderr_file,
            )
            self._log_msg(f"Practice window launched (PID: {self._proc.pid}).", "ok")
            self._log_msg("Close the Practice window to return here.", "info")
            self._launch_disabled = True
        except Exception as exc:
            self._log_msg(f"ERROR launching Practice window: {exc}", "err")

    # ------------------------------------------------------------------
    # Poll subprocess
    # ------------------------------------------------------------------
    def _poll_proc(self):
        if self._proc is not None:
            ret = self._proc.poll()
            if ret is not None:
                self._launch_disabled = False
                # Flush and read any stderr output
                try:
                    if hasattr(self, "_stderr_file") and self._stderr_file:
                        self._stderr_file.close()
                        self._stderr_file = None
                    if hasattr(self, "_stderr_path") and self._stderr_path and self._stderr_path.is_file():
                        stderr_text = self._stderr_path.read_text(encoding="utf-8", errors="replace").strip()
                        if stderr_text:
                            for line in stderr_text.splitlines()[:20]:
                                self._log_msg(f"  [stderr] {line}", "warn")
                        self._stderr_path.unlink(missing_ok=True)
                        self._stderr_path = None
                except Exception:
                    pass
                if ret == 0:
                    self._log_msg(f"Practice window exited cleanly (code 0).", "ok")
                    self._read_results()
                else:
                    self._log_msg(f"Practice window exited with code {ret}.", "err")
                    if self._temp_results:
                        self._log_msg(f"Results file (may be partial): {self._temp_results}", "warn")
                self._proc = None
                self._cleanup_temp()
            else:
                pass  # still running
        self.after(500, self._poll_proc)

    def _read_results(self):
        if not self._temp_results or not os.path.isfile(self._temp_results):
            self._log_msg("No results file found.", "warn")
            return
        try:
            with open(self._temp_results, "r", encoding="utf-8") as f:
                res = json.load(f)
            hits = res.get("hits", 0)
            misses = res.get("misses", 0)
            acc = res.get("accuracy_pct", 0.0)
            streak = res.get("best_streak", 0)
            self._log_msg(
                f"Session complete. Hits: {hits}, Misses: {misses}, "
                f"Accuracy: {acc:.1f}%, Best Streak: {streak}", "ok")
        except Exception as exc:
            self._log_msg(f"Could not read results: {exc}", "warn")

    def _cleanup_temp(self):
        for p in (self._temp_config, self._temp_results):
            if p and os.path.isfile(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
        self._temp_config = None
        self._temp_results = None
        if hasattr(self, "_stderr_file") and self._stderr_file:
            try:
                self._stderr_file.close()
            except Exception:
                pass
            self._stderr_file = None
        if hasattr(self, "_stderr_path") and self._stderr_path and self._stderr_path.is_file():
            try:
                self._stderr_path.unlink()
            except Exception:
                pass
            self._stderr_path = None

    def destroy(self):
        self._cleanup_temp()
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass
        super().destroy()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> int:
    app = PracticeLauncher()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
