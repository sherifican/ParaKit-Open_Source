> ## ⚠️ ALPHA / IN DEVELOPMENT — please read first
>
> **Practice Window v2 is not finished.** It is in an early **alpha/beta** state
> and is still being actively developed. The **stable, consistent Practice mode is
> v1**, which is built into the main ParaKit app (`ParaKit v4.0.py`) — that is the
> version to rely on for everyday use.
>
> v2 is included here because the source is now open: if you'd like to help push
> it forward — or just make your own version — you can improve **either** v1 (inside
> the main app) or v2 (this folder) without waiting for the official finished
> release. Expect rough edges, missing polish, and changing behavior in v2.

---

ParaKit Practice Window v2 -- Standalone Launcher
==================================================

What this is
------------
A standalone launcher for the new Pygame-CE Practice mini-game that
shipped with ParaKit v4.4.55+. You do NOT need the full ParaKit app
to use it -- just this folder, Python 3.12, and pygame-ce.

This launcher reads a MIDI file, parses the drum notes, and opens the
Practice window where notes fall down eight lanes in time with the
audio. You can play along with keyboard controls or a connected USB
MIDI drum kit.

Requirements
------------
- Python 3.12 (tested with 3.12.10)
- pygame-ce >= 2.5 (for the Practice window)
- mido (for MIDI file parsing and optional MIDI input)
- Optional: python-rtmidi (for real-time MIDI drum-kit input)

Install dependencies:
    py -3.12 -m pip install pygame-ce mido python-rtmidi

How to launch
-------------
Double-click:
    Run Practice Window v2.bat

Or from the command line:
    cd practice_v2
    py -3.12 practice_window_v2_launcher.py

How to use
----------
1. Click "Browse..." next to "MIDI File" and pick a .mid or .midi file.
   This is REQUIRED -- the Practice window needs notes to play.

2. (Optional) Pick an audio file under "Audio".
   - Full Mix: the complete song
   - Drum Stem: a drums-only track
   - Audio track radio: Auto picks Drum Stem if set, otherwise Full Mix

3. Adjust settings:
   - Auto-Kick: automatically hits kick notes for you
   - Square notes: render all notes as rectangles
   - Kick full-width line: draw kick as a full-width bar
   - Beat grid: show timing grid lines
   - Fall time: how many seconds notes take to reach the hit line
   - Note size: scale factor for note size
   - Lane visibility: uncheck lanes you do not want to practice

4. (Optional) Pick a MIDI input device under "MIDI Input".
   - Click "Refresh" to scan for connected USB drum kits
   - Select your device, or leave as "(None / Keyboard only)" for keyboard play

5. Click "Launch Practice Window".
   - The Pygame window opens in a separate process
   - Close the Practice window to return to the launcher
   - Session results (hits, misses, accuracy, best streak) appear in the log

Keyboard controls during play
-----------------------------
A = Hi-Hat    W = Crash     S = Snare     D = Tom 1
F = Tom 2     C = Tom 3     R = Ride      Space = Kick

Window controls
---------------
- Resize by dragging the corner (minimum 800x600)
- Alt+Enter or F11 to toggle fullscreen
- Portrait monitors (e.g. 1440x2560) work as a first-class layout
- High-DPI displays render crisply at 125% / 150% / 200% scaling

Files in this folder
--------------------
__init__.py                  -- package marker
main.py                      -- the Pygame Practice window (from ParaKit)
practice_window_v2_launcher.py -- Tkinter launcher GUI (this file)
assets/, note icons/, falling note icons/ -- Practice window art
Run Practice Window v2.bat   -- Windows double-click launcher
README.md                    -- this file

Troubleshooting
---------------
- "mido is required to parse MIDI files": install mido with pip
- "pygame-ce is required": install pygame-ce with pip
- "No MIDI input devices found": your drum kit may need drivers, or
  you can play with keyboard only
- Practice window opens but shows no notes: check that your MIDI file
  contains drum notes in the standard GM mapping (35/36 kick, 38 snare,
  42/44/46 hi-hat, etc.)
