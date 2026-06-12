# ParaKit — Preview Track v2 (Web Edition)

**Watch your drum chart fall in time with the music — then fix what's wrong without ever leaving the view.**

Preview Track is the *review* half of ParaKit's Preview/Practice tab, rebuilt as a fast, self-contained web app. Notes scroll down 8 lanes synced to the audio so you can **catch detection problems** — a snare a hair early, a crash that should've been a ride, a doubled hit — and the headline of v2: a **live Edit Mode that lets you fix them right there on the falling chart**, then resume. The see-it → fix-it loop, closed, with no tab switch. One file, opens in any modern browser, no install. It shares a byte-identical core with the ParaKit MIDI editor, so charts round-trip between them.

## ✎ Edit Mode (press `E`)

Pause and the subdivision grid brightens into a precise ruler the notes cross; the green hit-line is the "now" anchor; bar numbers run down the left gutter. Then:

- **Click an empty spot in a lane** → place a note, snapped to the grid.
- **Drag a note — one gesture, two fixes:**
  - **vertically = move it in time** (snapped, with a yellow guide + snap to nearby notes) — nudge a mistimed hit onto the beat.
  - **horizontally = reclassify its lane** — drag a wrong-drum note sideways onto the correct one (e.g. a crash that should be a ride).
- **Right-click = delete; hold and sweep = eraser** to wipe a run of bad notes.
- **Wheel = scrub** the song · **Ctrl + wheel = zoom** the fall window (stretch / squeeze how much chart you see).
- **`Ctrl+Z` / `Ctrl+Y` = undo / redo** — shared history with the MIDI editor's chart model.

## Tap-along charting

- Keys **`1`–`8` drop a note at the hit line** — and this **works during playback too**, so you can play along and tap missing notes in live (v4's lane hotkey layout).
- **● Record** captures your live keyboard / MIDI hits while the chart plays, with an optional **1-bar Count-in** and **Metronome** — handy for re-charting a section by performance.

## Everything for precise review

- **Speed** — 0.5× / 0.75× / 1× / 1.25×; slow a busy passage down to inspect it.
- **Fall time**, **Grid** (1/4 · 1/8 · 1/16 · 1/32) and **Snap** (`G`) — how much chart is visible and how finely edits snap.
- **🥁 Pads** — on-screen drum pads for mouse / touch input.
- **⇪ Receive** — pull in the chart staged by the MIDI editor's "→ Preview" button.
- **MIDI in** — review / edit with a USB drum kit.
- Built-in **demo charts**, including a dense ~4.2k-note stress chart, so it does something the moment you open it.
- Live **fps / note** counters and a status line naming the note under your cursor (lane · time · velocity).

## Loading your own song

The built-in **demo chart + synth play by default**, so you'll always see a track without loading anything. To review **your own** song:

- **Audio** — click **Mix**, **Drums**, or **Stems** to load an audio file (the full mix or an isolated stem).
- **Chart** — click **⇪ Import** to load a chart (`parakit-chart-v1` JSON), or **⇪ Receive** to pull one straight from the MIDI editor. **⇪ Export** saves your edited chart back out.
- Charts round-trip with the **MIDI editor** and **Practice v2 (Web Edition)** — same `parakit-chart-v1` format.

## Lanes

8 lanes, coded by **color + shape + position**: Hi-Hat · Crash · Snare · Tom 1 · Tom 2 · Tom 3 · Ride · Kick.

## Screenshots

| Review mode | Edit Mode |
|---|---|
| ![Preview Track — review mode](../screenshots/preview-track-review.png) | ![Preview Track — Edit Mode](../screenshots/preview-track-edit-mode.png) |

---

*Part of [ParaKit](../README.md) — free forever, no ads. Released under GPLv3.*
