# ParaKit RLRR Extractor (mini-app)

A small standalone utility that does the **reverse** of charting: it reads a
Paradiddle **`.rlrr`** chart and extracts the drum notes back out to a standard
**`.mid`** MIDI file.

Useful for:
- Re-opening an existing `.rlrr` chart in a MIDI editor to tweak or re-difficulty it
- Pulling "ground-truth" MIDI out of charts you (or others) already made
- Batch-converting a whole folder of `.rlrr` files at once

## Features
- **Single file** or **batch folder** extraction
- **Per-difficulty filter** (Easy / Medium / Hard / Expert / Expert+)
- **Preview + metadata edit** before writing (single-file mode)
- Output-folder picker, progress bar, clean **Stop/cancel**, scrollable log
- ParaKit dark theme

## Requirements
- **Python 3.12**
- No third-party packages — uses only the Python standard library (`tkinter`)

## Run
Double-click **`Run RLRR Extractor.bat`**, or from a terminal:

```
cd extractor
py -3.12 rlrr_extract_gui.py
```

## Files
| File | Purpose |
|------|---------|
| `rlrr_extract_gui.py` | The Tkinter GUI (entry point) |
| `rlrr_parse.py` | `.rlrr` parsing + MIDI writing (note-class → MIDI mapping) |
| `Run RLRR Extractor.bat` | Windows double-click launcher |

> Note: this began as an internal developer utility, so it's deliberately
> lightweight. It's included in the open-source release so you can build on it.
