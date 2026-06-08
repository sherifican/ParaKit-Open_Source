"""
rlrr_extract_gui.py
===================
RLRR -> MIDI extractor mini-app (v3).

Owner-only scope. Launch via "Run RLRR Extractor.bat" or:
    py -3.12 rlrr_extract_gui.py

v1: single-file picker, extract button, scrollable log.
v2: folder picker, output dir picker, progress bar, red error highlighting,
    stop button (clean cancel).
v3: per-difficulty filter (batch), pre-extraction preview + metadata edit
    (single), ParaKit dark theme.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path
from tkinter import filedialog
import tkinter as tk
import tkinter.ttk as ttk

from rlrr_parse import (
    CLASS_TO_MIDI,
    LANE_NAMES,
    extract_notes_from_rlrr,
    parse_rlrr,
    write_ground_truth_mid,
    write_mid_with_metadata,
)

# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------
C = {
    "bg":       "#222222",
    "deep":     "#0d1117",
    "fg":       "#e0e0e0",
    "muted":    "#aaaaaa",
    "btn_bg":   "#2a1235",
    "btn_fg":   "#f7efff",
    "btn_bdr":  "#8a35a6",
    "accent":   "#e94560",
    "purple":   "#b388ff",
    "ok":       "#00cc66",
    "err":      "#ff6b81",
    "warn":     "#ffb347",
    "info":     "#58a6ff",
}

DIFFICULTIES = ["Easy", "Medium", "Hard", "Expert", "Expert+"]


def _safe_stem(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", s).strip() or "output"


def _apply_style():
    s = ttk.Style()
    s.theme_use("clam")
    s.configure("TNotebook",      background=C["bg"],     borderwidth=0)
    s.configure("TNotebook.Tab",  background=C["btn_bg"], foreground=C["btn_fg"],
                padding=[12, 5])
    s.map("TNotebook.Tab",
          background=[("selected", C["btn_bdr"])],
          foreground=[("selected", C["bg"])])
    s.configure("TFrame",         background=C["bg"])
    s.configure("TLabel",         background=C["bg"],     foreground=C["fg"])
    s.configure("TCheckbutton",   background=C["bg"],     foreground=C["fg"])
    s.map("TCheckbutton",         background=[("active", C["bg"])])
    s.configure("TEntry",         fieldbackground=C["deep"], foreground=C["fg"],
                insertcolor=C["fg"])
    s.configure("Horizontal.TProgressbar", troughcolor=C["deep"],
                background=C["accent"], borderwidth=0)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("RLRR Extractor")
        self.geometry("660x560")
        self.resizable(True, True)
        self.configure(bg=C["bg"])

        _apply_style()
        self.option_add("*Background",       C["bg"])
        self.option_add("*Foreground",       C["fg"])
        self.option_add("*Label.Background", C["bg"])
        self.option_add("*Label.Foreground", C["fg"])

        self._output_dir:  Path | None = None
        self._stop_event = threading.Event()
        self._running = False

        # single-file state
        self._single_path: Path | None = None
        self._single_notes: list = []

        # metadata edit vars (single mode)
        self._meta_title   = tk.StringVar()
        self._meta_artist  = tk.StringVar()
        self._meta_creator = tk.StringVar()

        # batch state
        self._folder_path: Path | None = None
        self._diff_vars = {d: tk.BooleanVar(value=True) for d in DIFFICULTIES}

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _mk_btn(self, parent, text, cmd, width=None):
        kw = dict(text=text, command=cmd,
                  bg=C["btn_bg"], fg=C["btn_fg"],
                  activebackground=C["btn_bdr"], activeforeground=C["fg"],
                  relief="flat", bd=0, padx=10, pady=4,
                  cursor="hand2")
        if width:
            kw["width"] = width
        return tk.Button(parent, **kw)

    def _build_ui(self):
        P = {"padx": 8, "pady": 3}

        # ---- Notebook ----
        nb = ttk.Notebook(self)
        nb.pack(fill="x", padx=8, pady=(8, 4))

        self._tab_single = ttk.Frame(nb)
        self._tab_batch  = ttk.Frame(nb)
        nb.add(self._tab_single, text="  Single File  ")
        nb.add(self._tab_batch,  text="  Batch Folder  ")

        self._build_single_tab()
        self._build_batch_tab()

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=8, pady=4)

        # ---- Output dir ----
        f_out = tk.Frame(self, bg=C["bg"])
        f_out.pack(fill="x", **P)
        tk.Label(f_out, text="Output dir:", bg=C["bg"], fg=C["muted"],
                 width=11, anchor="w").pack(side="left")
        self._outdir_var = tk.StringVar(value="Same as source")
        tk.Label(f_out, textvariable=self._outdir_var, bg=C["bg"],
                 fg=C["fg"], anchor="w").pack(side="left", fill="x", expand=True, padx=(4, 0))
        self._mk_btn(f_out, "Clear",    self._clear_output_dir).pack(side="right", padx=(2, 0))
        self._mk_btn(f_out, "Browse…",  self._pick_output_dir).pack(side="right")

        # ---- Action row ----
        f_act = tk.Frame(self, bg=C["bg"])
        f_act.pack(**P)
        self._extract_btn = self._mk_btn(f_act, "Extract MIDI", self._start_extract, width=14)
        self._extract_btn.pack(side="left", padx=(0, 8))
        self._extract_btn.config(state="disabled")
        self._stop_btn = self._mk_btn(f_act, "Stop", self._request_stop, width=8)
        self._stop_btn.pack(side="left")
        self._stop_btn.config(state="disabled")
        self._status_var = tk.StringVar()
        tk.Label(f_act, textvariable=self._status_var,
                 bg=C["bg"], fg=C["muted"]).pack(side="left", padx=(12, 0))

        # ---- Progress ----
        self._progress = ttk.Progressbar(self, style="Horizontal.TProgressbar",
                                         mode="determinate")
        self._progress.pack(fill="x", padx=8, pady=(2, 0))
        self._prog_var = tk.StringVar()
        tk.Label(self, textvariable=self._prog_var,
                 bg=C["bg"], fg=C["muted"], anchor="e").pack(fill="x", padx=8)

        # ---- Log ----
        tk.Label(self, text="Log", bg=C["bg"], fg=C["purple"],
                 anchor="w").pack(fill="x", padx=8)
        self._log = tk.Text(self, height=9, state="disabled", wrap="word",
                            bg=C["deep"], fg=C["info"],
                            insertbackground=C["fg"],
                            selectbackground=C["btn_bdr"],
                            relief="flat", bd=0, padx=6, pady=4)
        _sb = ttk.Scrollbar(self, orient="vertical", command=self._log.yview)
        self._log.configure(yscrollcommand=_sb.set)
        _sb.pack(side="right", fill="y", padx=(0, 8), pady=(0, 8))
        self._log.pack(fill="both", expand=True, padx=(8, 0), pady=(0, 8))
        self._log.tag_config("ok",   foreground=C["ok"])
        self._log.tag_config("err",  foreground=C["err"])
        self._log.tag_config("warn", foreground=C["warn"])
        self._log.tag_config("info", foreground=C["info"])

    def _build_single_tab(self):
        P = {"padx": 8, "pady": 4}
        # File picker row
        f = tk.Frame(self._tab_single, bg=C["bg"])
        f.pack(fill="x", **P)
        self._mk_btn(f, "Select .rlrr…", self._pick_file).pack(side="left")
        self._single_var = tk.StringVar(value="No file selected")
        tk.Label(f, textvariable=self._single_var, bg=C["bg"],
                 fg=C["muted"], anchor="w").pack(side="left", fill="x",
                                                  expand=True, padx=(8, 0))

        # Preview frame
        self._preview_frame = tk.Frame(self._tab_single, bg=C["bg"])
        self._preview_frame.pack(fill="x", padx=8, pady=(0, 4))
        self._preview_var = tk.StringVar()
        tk.Label(self._preview_frame, textvariable=self._preview_var,
                 bg=C["bg"], fg=C["purple"], anchor="w",
                 justify="left", wraplength=580).pack(fill="x")

        # Metadata edit
        meta_frame = tk.LabelFrame(self._tab_single, text="Metadata",
                                   bg=C["bg"], fg=C["muted"],
                                   relief="flat", bd=1)
        meta_frame.pack(fill="x", padx=8, pady=(0, 6))
        for label, var in [("Title",   self._meta_title),
                            ("Artist",  self._meta_artist),
                            ("Creator", self._meta_creator)]:
            row = tk.Frame(meta_frame, bg=C["bg"])
            row.pack(fill="x", pady=1)
            tk.Label(row, text=f"{label}:", bg=C["bg"], fg=C["muted"],
                     width=8, anchor="w").pack(side="left")
            e = ttk.Entry(row, textvariable=var, style="TEntry")
            e.pack(side="left", fill="x", expand=True, padx=(4, 8))

    def _build_batch_tab(self):
        P = {"padx": 8, "pady": 4}
        # Folder picker row
        f = tk.Frame(self._tab_batch, bg=C["bg"])
        f.pack(fill="x", **P)
        self._mk_btn(f, "Select folder…", self._pick_folder).pack(side="left")
        self._folder_var = tk.StringVar(value="No folder selected")
        tk.Label(f, textvariable=self._folder_var, bg=C["bg"],
                 fg=C["muted"], anchor="w").pack(side="left", fill="x",
                                                  expand=True, padx=(8, 0))

        # Difficulty filter
        df = tk.LabelFrame(self._tab_batch, text="Difficulty filter",
                           bg=C["bg"], fg=C["muted"], relief="flat", bd=1)
        df.pack(fill="x", padx=8, pady=(0, 6))
        inner = tk.Frame(df, bg=C["bg"])
        inner.pack(anchor="w", padx=4, pady=2)
        for d in DIFFICULTIES:
            ttk.Checkbutton(inner, text=d, variable=self._diff_vars[d],
                            style="TCheckbutton").pack(side="left", padx=(0, 12))

        self._batch_count_var = tk.StringVar()
        tk.Label(self._tab_batch, textvariable=self._batch_count_var,
                 bg=C["bg"], fg=C["muted"], anchor="w").pack(fill="x", padx=8)

    # ------------------------------------------------------------------
    # Pickers
    # ------------------------------------------------------------------

    def _pick_file(self):
        path = filedialog.askopenfilename(
            title="Select a .rlrr file",
            filetypes=[("Paradiddle chart", "*.rlrr"), ("All files", "*.*")])
        if not path:
            return
        self._single_path = Path(path)
        self._single_var.set(self._single_path.name)
        self._log_clear()
        self._load_preview(self._single_path)
        self._extract_btn.config(state="normal")
        self._reset_progress()

    def _pick_folder(self):
        path = filedialog.askdirectory(title="Select folder of .rlrr files")
        if not path:
            return
        self._folder_path = Path(path)
        self._folder_var.set(str(self._folder_path))
        self._refresh_batch_count()
        self._extract_btn.config(state="normal")
        self._log_clear()
        self._reset_progress()

    def _pick_output_dir(self):
        path = filedialog.askdirectory(title="Select output directory")
        if not path:
            return
        self._output_dir = Path(path)
        self._outdir_var.set(str(self._output_dir))

    def _clear_output_dir(self):
        self._output_dir = None
        self._outdir_var.set("Same as source")

    # ------------------------------------------------------------------
    # Preview (single-file mode, runs on main thread — parse only, fast)
    # ------------------------------------------------------------------

    def _load_preview(self, rlrr_path: Path):
        self._preview_var.set("Parsing…")
        self._meta_title.set("")
        self._meta_artist.set("")
        self._meta_creator.set("")
        self._single_notes = []
        self.update_idletasks()

        notes, meta, err = extract_notes_from_rlrr(rlrr_path)
        if err:
            self._preview_var.set(f"Preview error: {err}")
            return

        self._single_notes = notes
        self._meta_title.set(meta.get("title", ""))
        self._meta_artist.set(meta.get("artist", ""))
        self._meta_creator.set(meta.get("creator", ""))

        duration = meta.get("length", 0.0)
        mins, secs = divmod(int(duration), 60)
        from collections import Counter
        note_to_lane = {v[1]: v[2] for v in CLASS_TO_MIDI.values()}
        lane_counts = Counter(note_to_lane.get(note, "?") for _, note, _ in notes)
        lane_str = "  ".join(
            f"{ln}:{lane_counts[ln]}" for ln in LANE_NAMES if lane_counts.get(ln))
        bpm = meta.get("bpm", "?")
        self._preview_var.set(
            f"{len(notes)} notes  |  {mins}:{secs:02d}  |  BPM ~{bpm}\n{lane_str}")

    def _refresh_batch_count(self):
        if not self._folder_path:
            return
        rlrrs = self._filtered_rlrrs()
        total = sum(1 for _ in self._folder_path.rglob("*.rlrr"))
        self._batch_count_var.set(
            f"{len(rlrrs)} of {total} .rlrr file(s) match current difficulty filter")

    def _filtered_rlrrs(self) -> list[Path]:
        if not self._folder_path:
            return []
        enabled = {d for d, v in self._diff_vars.items() if v.get()}
        out = []
        for p in sorted(self._folder_path.rglob("*.rlrr")):
            stem = p.stem
            matched = any(stem.endswith(f"_{d}") or stem.endswith(f"_{d.replace('+', 'Plus')}")
                          for d in enabled)
            # also include files with no recognised difficulty suffix when all are checked
            has_any = any(stem.endswith(f"_{d}") or stem.endswith(f"_{d.replace('+', 'Plus')}")
                          for d in DIFFICULTIES)
            if matched or (not has_any and enabled == set(DIFFICULTIES)):
                out.append(p)
        return out

    # ------------------------------------------------------------------
    # Extract entry point
    # ------------------------------------------------------------------

    def _start_extract(self):
        if self._running:
            return
        self._log_clear()
        self._stop_event.clear()
        self._set_running(True)

        # Determine active tab: 0 = single, 1 = batch
        if self._single_path and not self._folder_path:
            t = threading.Thread(target=self._run_single, daemon=True)
        elif self._folder_path and not self._single_path:
            rlrrs = self._filtered_rlrrs()
            t = threading.Thread(target=self._run_batch, args=(rlrrs,), daemon=True)
        elif self._single_path:
            t = threading.Thread(target=self._run_single, daemon=True)
        else:
            self._set_running(False)
            return
        t.start()

    def _request_stop(self):
        self._stop_event.set()
        self._status_var.set("Stopping…")

    # ------------------------------------------------------------------
    # Workers
    # ------------------------------------------------------------------

    def _run_single(self):
        path = self._single_path
        self.after(0, lambda: self._log_write(f"Extracting {path.name} …\n", "info"))

        title   = self._meta_title.get().strip()
        artist  = self._meta_artist.get().strip()
        notes   = self._single_notes  # already parsed in _load_preview

        if not notes:
            notes, _, err = extract_notes_from_rlrr(path)
            if err:
                self.after(0, lambda: self._log_write(f"ERR {err}\n", "err"))
                self.after(0, self._on_done)
                return

        stem     = _safe_stem(title) if title else path.stem
        out_name = stem + ".mid"
        dest_dir = self._output_dir or path.parent
        out_path = dest_dir / out_name

        try:
            tmp = out_path.with_suffix(".mid.tmp")
            write_mid_with_metadata(notes, tmp, title=title, artist=artist)
            tmp.replace(out_path)
        except Exception as exc:
            msg = f"Write failed: {exc}"
            self.after(0, lambda: self._log_write(f"ERR {msg}\n", "err"))
            self.after(0, self._on_done)
            return

        label = f"{title} - {artist}".strip(" -") if artist else (title or path.stem)
        self.after(0, lambda: self._log_write(
            f"OK  {label} -> {out_path.name} ({len(notes)} notes)\n", "ok"))
        self.after(0, self._on_done)

    def _run_batch(self, rlrr_files: list[Path]):
        total = len(rlrr_files)
        self.after(0, lambda: self._progress.config(maximum=max(total, 1), value=0))
        ok_count = err_count = skip_count = 0

        for i, rlrr_path in enumerate(rlrr_files, 1):
            if self._stop_event.is_set():
                skip_count = total - (i - 1)
                self.after(0, lambda s=i-1: self._log_write(
                    f"Stopped after {s}/{total} files.\n", "warn"))
                break

            out_path = self._resolve_output(rlrr_path)
            ok, msg = self._extract_one_batch(rlrr_path, out_path)
            tag  = "ok" if ok else "err"
            pfx  = "OK " if ok else "ERR"
            line = f"{pfx} [{i}/{total}] {msg}\n"
            if ok:
                ok_count += 1
            else:
                err_count += 1

            self.after(0, lambda l=line, t=tag, v=i: (
                self._log_write(l, t),
                self._progress.config(value=v),
                self._prog_var.set(f"{v}/{total}"),
            ))

        self.after(0, lambda: self._log_write(
            f"\nDone — {ok_count} OK, {err_count} error(s), {skip_count} skipped\n"))
        self.after(0, self._on_done)

    # ------------------------------------------------------------------
    # Core extraction (worker thread — no direct UI access)
    # ------------------------------------------------------------------

    def _extract_one_batch(self, rlrr_path: Path, out_path: Path) -> tuple[bool, str]:
        notes, meta, err = extract_notes_from_rlrr(rlrr_path)
        if err:
            return False, f"{rlrr_path.name}: {err}"
        try:
            tmp = out_path.with_suffix(".mid.tmp")
            write_ground_truth_mid(notes, tmp)
            tmp.replace(out_path)
        except Exception as exc:
            return False, f"{rlrr_path.name}: write failed — {exc}"
        title  = meta.get("title", rlrr_path.stem)
        artist = meta.get("artist", "")
        label  = f"{title} - {artist}".strip(" -") if artist else title
        return True, f"{label} -> {out_path.name} ({len(notes)} notes)"

    def _resolve_output(self, rlrr_path: Path) -> Path:
        dest_dir = self._output_dir if self._output_dir else rlrr_path.parent
        return dest_dir / rlrr_path.with_suffix(".mid").name

    # ------------------------------------------------------------------
    # State helpers (main thread)
    # ------------------------------------------------------------------

    def _set_running(self, running: bool):
        self._running = running
        s_main = "disabled" if running else "normal"
        s_stop = "normal"   if running else "disabled"
        self._extract_btn.config(state=s_main)
        self._stop_btn.config(state=s_stop)
        self._status_var.set("Running…" if running else "")

    def _on_done(self):
        self._set_running(False)
        self._status_var.set("")

    def _reset_progress(self):
        self._progress.config(value=0, maximum=100)
        self._prog_var.set("")

    # ------------------------------------------------------------------
    # Log helpers (main thread)
    # ------------------------------------------------------------------

    def _log_clear(self):
        self._log.config(state="normal")
        self._log.delete("1.0", "end")
        self._log.config(state="disabled")

    def _log_write(self, text: str, tag: str | None = None):
        self._log.config(state="normal")
        self._log.insert("end", text, tag or "")
        self._log.see("end")
        self._log.config(state="disabled")


if __name__ == "__main__":
    App().mainloop()
