# Building & customizing ParaKit from source

ParaKit v4.x is a single Python file (`ParaKit v4.0.py`) plus the bundled command-line
tools. Running and modifying it is intentionally simple.

## 1. Run from source

1. Install **Python 3.12** ([python.org](https://www.python.org/downloads/release/python-3120/)).
2. Install the Python dependencies:
   ```
   py -3.12 -m pip install -r requirements.txt
   ```
3. Get the **requirements bundle** (FFmpeg, yt-dlp, ADB) from the
   [releases page](https://github.com/sherifican/ParaKit---Releases) and place those tools
   next to `ParaKit v4.0.py` or in a `Requirements\` subfolder beside it.
4. Launch:
   ```
   py -3.12 "ParaKit v4.0.py"
   ```

## 2. Make your own version

Everything lives in `ParaKit v4.0.py`, so you can:
- **Add or remove features** — the file is organized into tabs/sections; search for a tab
  name (e.g. `_build_stem_tab`, `_build_a2m_tab`) to find where it's built.
- **Rearrange or restyle the UI** — the app uses **Tkinter / TTK** with `ttkbootstrap`
  theming. (Note: the separate **UI Studio** visual designer targets the future **v5 /
  PySide6** rebuild and cannot edit this v4.x Tkinter UI — see `ROADMAP.md`.)
- **Change detection behavior** — be careful around the core detection functions
  (`detect_onsets`, `detect_bpm_and_offset`, `_a2m_do_convert`, `build_rlrr`,
  `reduce_notes_for_difficulty`); they're the heart of the conversion pipeline.

If you distribute your modified version, remember it must remain **GPLv3** (source available).

## 3. Compile a standalone .exe (optional)

To produce a double-clickable build that doesn't require users to install Python, use
[PyInstaller](https://pyinstaller.org/):

```
py -3.12 -m pip install pyinstaller
py -3.12 -m PyInstaller --onedir --noconsole --name ParaKit "ParaKit v4.0.py"
```

Then place the bundled tools (FFmpeg / yt-dlp / ADB) and any data files ParaKit loads
(icons, models, assets) alongside the built executable. A `--onedir` build is usually more
reliable than `--onefile` for an app this size, and starts faster.

> Tip: ParaKit loads several resource files relative to the executable at runtime. If a build
> can't find an icon, model, or logo, check that those files were copied next to the `.exe`.
