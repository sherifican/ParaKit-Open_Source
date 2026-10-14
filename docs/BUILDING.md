# Building & customizing ParaKit from source

ParaKit v4.x runs from source as `ParaKit v4.0.py` plus the Python modules
and data files kept beside that file. Keep these for the features ParaKit
ships. Some have a fallback when they are missing, so the list is not
everything required at startup. Bundled command-line tools are a separate
download, covered in section 1.

Modules beside the main file:

- Spectral Comparison uses `parakit_spectral_tab.py` and
  `parakit_spectral_engine.py`.
- Preview uses `parakit_preview_tab.py`, `parakit_preview_engine.py`, and
  `parakit_preview_sprites.py`.
- Practice uses `parakit_practice_tab.py`, `parakit_practice_engine.py`,
  `parakit_practice_home.py`, `parakit_practice_home_widgets.py`,
  `parakit_practice_widgets.py`, and `parakit_practice_sprites.py`.
- Song Tester uses `parakit_song_tester_tab.py` and
  `parakit_song_tester_widgets.py`.
- The main file also loads `parakit_synth_voices.py`,
  `parakit_onset_align.py`, `rlrr_parse.py`, and the packages
  `parakit_cleanup/` and `parakit_separators/`.

Data beside the main file:

- The folders `icons/`, `assets/`, and `templates/`.
- The model `parakit_drum_model.onnx`.
- The images `parakit.ico`, `parakit_header_logo.png`, and
  `parakit_logo_FINAL.png`.
- `CHANGELOG.txt`, which the app reads for What's New. Without it, What's
  New shows a missing-file note.

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

`ParaKit v4.0.py` is the main file. The modules and data files named above
sit beside it. You can:
- **Add or remove features** — older tabs are built in the main file. Spectral
  Comparison, Preview, Practice, and Song Tester are built in the sidecar tab
  modules named above. Search for a tab name (e.g. `_build_stem_tab`,
  `_build_a2m_tab`) to find where an older tab is built.
- **Rearrange or restyle the UI** — the app uses **Tkinter / TTK** with `ttkbootstrap`
  theming. (Note: the separate **UI Studio** visual designer targets the future **v5 /
  PySide6** rebuild and cannot edit this v4.x Tkinter UI — see `ROADMAP.md`.)
- **Change detection behavior** — be careful around the core detection functions
  (`detect_onsets`, `detect_bpm_and_offset`, `_a2m_do_convert`, `build_rlrr`,
  `reduce_notes_for_difficulty`); they're the heart of the conversion pipeline.

If you distribute your modified version, remember it must remain **GPLv3** (source available).

## 3. Compile a standalone .exe (unsupported)

ParaKit does not ship a compiled .exe and does not support running from one.
The supported way to run ParaKit is from source (section 1).

A local [PyInstaller](https://pyinstaller.org/) build can be tried. This page
does not promise that the result starts, or that it matches the source app.

```
py -3.12 -m pip install pyinstaller
py -3.12 -m PyInstaller --onedir --noconsole --name ParaKit "ParaKit v4.0.py"
```

The folder from that attempt must also contain the Python modules and data
files listed at the top of this page, plus the bundled command-line tools
(FFmpeg, yt-dlp, and ADB), next to the built program.
