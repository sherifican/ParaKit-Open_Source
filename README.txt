==============================================================================
  PARAKIT  --  v4.x SOURCE RELEASE
  An all-in-one drum-charting tool for Paradiddle (.rlrr),
  with Clone Hero (.chart) support.
==============================================================================

  Version in this release: 4.5.4-1
  Runtime:                 Python 3.12 (required)

  This is the plain-text companion to README.md. It has the same information
  without the Markdown formatting, so it reads cleanly in Notepad.
  The fix/change log lives in its own file:  CHANGELOG.txt

  ParaKit is actively developed and supported. A v5 major update / rebuild is
  in the works; until that ships, v4 keeps getting regular updates often.

  Paradiddle:  https://www.paradiddleapp.com/

------------------------------------------------------------------------------
  DOWNLOADS & LINKS
------------------------------------------------------------------------------

  Note: the compiled .exe version is a little behind the .py version right now
  (.exe = 4.4.52). The .py version gets far more engagement, so that is what is
  actively supported; the .exe is refreshed periodically when enough changes
  pile up or there is a significant fix. The v5 rebuild will be compiled too
  once it is finished, for those who want it.

  The old LimeWire links EXPIRE if they are not downloaded at least once every
  7 days. A website to host the .exe versions is in progress but is having
  server-side issues; the aim is to get a fix / the site up before the LimeWire
  links expire.

    .exe download (LimeWire bundle):
      https://limewire.com/d/UV9Zm#DHqxKgEtmn

    Jarredou model / Requirements bundle (LimeWire):
      https://limewire.com/d/HrcqC#lS73gPUpJa

    README for the .exe version:
      https://github.com/sherifican/ParaKit---Releases

  Note (v4.5.3.1-1): the in-app download button for the Jarredou neural
  stem-isolation model is now rewired to the official Hugging Face repo
  (https://huggingface.co/Politrees/UVR_resources), with a second Hugging Face
  mirror tried automatically if the first is down (the old GitHub source was
  removed upstream). The file is size- and SHA256-verified after download. If
  both mirrors are ever unavailable, the model is still on the LimeWire
  Requirements bundle above as a manual fallback.

  ParaKit official homepage: (site temporarily down)

------------------------------------------------------------------------------
  KNOWN QUIRK -- AUDIO -> MIDI CHART GENERATION  (read this first)
------------------------------------------------------------------------------

  As of v4.4.66-1, ParaKit de-duplicates kicks at 55 ms by default, so kicks
  that used to group together now come out clean on the first Convert for the
  vast majority of songs -- nothing to do.

  You will mainly see grouped kicks now if you have LOWERED the kick dedup gap
  for a fast-kick song (needed to keep its correct kicks -- but set it too small
  and the grouping creeps back in). If that happens, it is still quick to fix:

    Step 1. Zoom out all the way on your chart in the MIDI Editor tab.
    Step 2. Hold Shift + Left Click + drag to multi-select all the kicks.
    Step 3. Press the "Dedup x" button in the tool hot bar above your chart
            and set the ms slider to about 50 - 65 ms.

  Done -- this removes the layered extra kicks while leaving your correct kick
  placements nearly untouched.

  Be aware that on particularly fast double-bass songs this fix largely does
  NOT apply, since it will treat a large portion of your correct kicks as
  duplicates and remove them. A work-around to this is in progress.

  Want to tune it at convert time instead? See the per-instrument dedup gap
  settings in the Audio -> MIDI tab, documented in:
    docs/TROUBLESHOOTING.md  (section: "Kicks grouped together after Audio->MIDI")

------------------------------------------------------------------------------
  WHAT PARAKIT DOES
------------------------------------------------------------------------------

  Take a song, isolate the drums, turn them into a playable drum chart, refine
  it in a visual MIDI editor, practice it with falling notes, and export it.
  Use iTunes / MusicBrainz to find album art and metadata, turn sheet music
  into MIDI files, and create batches of songs at once -- all in one app.

  ParaKit always has been and always will be free of charge, and will NEVER
  host ads. This repository makes the full v4.x source code open under the
  GPLv3 license, so anyone can run it from source, learn from it, fix it, or
  build their own version.

  Note: the Practice and Preview modes that are currently HTML/web editions
  will be folded into ParaKit proper in a future update. For now the web
  versions work fine as a quick and easy substitute.

------------------------------------------------------------------------------
  WHICH PARAKIT IS THIS?
------------------------------------------------------------------------------

  ParaKit is mid-transition between two generations:

    ParaKit v4.x (this release)
      - UI framework:  Tkinter / TTK
      - Status:        the complete, stable, shipping app
      - Themeable with UI Studio?  No

    ParaKit v5 (future)
      - UI framework:  PySide6 / Qt
      - Status:        early rebuild, barely started -- NOT in this release
      - Themeable with UI Studio?  Yes (UI Studio is built for v5)

  UI Studio -- the visual UI/layout designer -- is built for the v5 (PySide6)
  rebuild and is NOT compatible with this v4.x (Tkinter) app. It is not included
  in this release because it cannot run without the v5 code, which is not ready
  yet. UI Studio and v5 will arrive in a later follow-up. You cannot use UI
  Studio to re-theme or edit this v4.x app -- but once it ships, you will be
  able to use it to design for v5 or build your own custom ParaKit from source.

  See docs/ROADMAP.md for the v5 / UI Studio / GPU-build plan.

------------------------------------------------------------------------------
  WHAT'S IN THIS REPOSITORY
------------------------------------------------------------------------------

  ParaKit v4.0.py
      The full ParaKit v4.x app (single file).
  Run ParaKit v4.0.bat
      Windows double-click launcher.
  requirements.txt
      Python dependencies for the main app.
  parakit_drum_model.onnx
      Neural drum-detection model loaded by the ML / Hybrid Audio->MIDI engines.
  parakit_separators/
      Neural drum-stem separator plug-ins (Jarredou MDX23C) for the Audio->MIDI
      detection pipeline.
  rlrr_parse.py
      The .rlrr parsing core, shared by the app's MIDI Extractor and extractor/.
  extractor/
      RLRR Extractor -- converts .rlrr charts back into .mid MIDI.
      (see extractor/README.md)
  Practice Window v3 - Web Edition/
      Practice Mode v3 -- Web Edition. Combined rebuild: v2's falling-note play
      + a built-in Kit Studio + song-library loading. Offered alongside v2.
  Practice Window v2 - Web Edition/
      Practice Mode v2 -- Web Edition. Self-contained browser rebuild of the
      falling-note practice game. Offered alongside v3 so you can compare.
  Preview Track v2 - Web Edition/
      Preview Track v2 -- Web Edition. Falling-note review + live Edit Mode for
      catching and fixing chart issues.
  Detection Research Notes - Web Edition/
      Detection Research Notes -- Web Edition. Offline research hub (one HTML):
      how detection works + the published literature and hands-on testing.
  practice_v2/
      Practice Window v2 (Python) -- standalone falling-note practice mini-game
      (ALPHA). (see practice_v2/README.md)
  docs/
      Building from source, troubleshooting, roadmap.
  LICENSE
      GNU GPL v3.

  Practice v1 vs v2: the STABLE Practice mode is v1, built into the main app.
  practice_v2/ is an in-development alpha -- included so you can build on it.

------------------------------------------------------------------------------
  FEATURES
------------------------------------------------------------------------------

  - MIDI editor -- visual note placement and refinement.
  - Audio -> MIDI detection -- automatic drum transcription with three engines:
      Spectral (traditional), ML / ONNX (neural net), and Hybrid (combined),
      plus genre presets (Pop / Rock / Metal / Funk).
  - Stem splitter -- isolate a drums-only track from any song (Demucs).
  - MusicXML -> MIDI -- convert sheet music into a chart.
  - YouTube -> FLAC -- download lossless audio to chart from YouTube.
  - Asset management -- metadata, album art, preview clips.
  - Preview & Practice -- falling notes synced to the audio, keyboard or USB
    MIDI kit.
  - Song Tester -- verify sync before export.
  - Export -- Paradiddle (.rlrr) and Clone Hero (.chart).

  (The web-based Practice & Preview tools are described under WEB EDITIONS,
  further down.)

------------------------------------------------------------------------------
  AUTO DIFFICULTY ADJUSTER UPDATE -- better Easy / Medium / Hard charts  (v4.5.3-1)
------------------------------------------------------------------------------

  The automatic difficulty reduction -- the Easy / Medium / Hard versions
  ParaKit builds from your full chart -- was rebuilt. The old version dropped
  the kick and toms entirely on Easy and Medium, so those charts were missing
  whole parts of the kit. It now keeps a thinned-down kick and tom line the way
  real charts do, matches the note density of human-made difficulties much more
  closely, and keeps the strongest beats first (downbeats before off-beats).
  Expert charts are unchanged. Instrument variants (alternate kicks/snares,
  china/splash cymbals) that used to vanish on lower difficulties are kept now
  too. Validated against the human-made Easy/Medium/Hard charts for 100+
  Paradiddle songs.

  Heads-up / caveats:
    - Hard comes out closest to human charts, Medium is good, and Easy is the
      roughest tier.
    - On the most heavily-thinned lanes -- especially the Easy kick and toms,
      where a human keeps only a handful of notes -- the reduction keeps the
      right lanes at about the right density, but which exact notes it keeps
      will not always match a human's feel (there are many equally-valid ways
      to thin an Easy chart).
    - Reduced charts run slightly busier than a human's (a bit more kept than
      dropped).
    - As always, give a reduced chart a quick once-over in the MIDI Editor --
      it is a strong starting point, not a finished hand-made difficulty.

------------------------------------------------------------------------------
  DETECTION UPDATE -- Hi-Hat Recovery  (v4.5.1-1)
------------------------------------------------------------------------------

  ParaKit was quietly dropping hi-hats it had actually detected. In Hybrid mode
  a hit is normally "confirmed" by the spectral engine -- but spectral detects
  almost no hi-hats, so every hat had to clear a stricter confidence bar on its
  own, and softer / faster hats (intros, fast hat patterns) were being thrown
  out. v4.5.1-1 retunes that hi-hat confidence gate so the real hits survive --
  hi-hat only; kick, snare, crash, ride and toms are untouched.

  Validated on the final chart (after the cleanup pass, the way it actually
  runs) two ways: a 60-song set scored against human charts (hi-hat recall
  +0.17, F +0.03, every other lane effectively unchanged), and 14 fresh songs
  across genres scored against the audio itself (every one improved, including
  the cymbal-heavy metalcore tracks, with no false-hat blowups).

  Full methodology -- and the experiments that did NOT pan out -- live in the
  Detection Research Notes hub (below).

------------------------------------------------------------------------------
  HOW DETECTION WORKS -- RESEARCH NOTES
------------------------------------------------------------------------------

  Curious how ParaKit turns audio into a drum chart, or want to build on the
  detection work yourself? There is a self-contained, OFFLINE research hub (one
  HTML file, dark/light mode, three reports in tabs) documenting the
  detection-cleanup research behind the app -- published drum-transcription
  literature paired with hands-on testing on real songs, with sources listed,
  and the experiments that did not work kept in.

  Open it: download and open this file in any browser (no internet needed):
    Detection Research Notes - Web Edition/parakit-detection-research.html

------------------------------------------------------------------------------
  SCREENSHOTS
------------------------------------------------------------------------------

  Screenshots of every tab are in the screenshots/ folder, and render inline on
  the GitHub page (README.md). The tabs:

    1.  Single Song Creator
    2.  Create Multiple Songs
    3.  Audio -> .ogg Converter
    4.  Stem Splitter
    5.  Audio -> MIDI
    6.  MIDI Editor
    7.  Sheet Music -> MIDI
    8.  YouTube -> FLAC
    9.  Asset Manager
    10. Song Tester
    11. Preview / Practice Track
    12. Quick Start & FAQ

------------------------------------------------------------------------------
  REQUIREMENTS
------------------------------------------------------------------------------

  1. Python 3.12
     ParaKit targets Python 3.12 specifically. Get it from
     https://www.python.org/downloads/  (check "Add to PATH" / use the py
     launcher).

  2. Python packages
       py -3.12 -m pip install -r requirements.txt
     (See requirements.txt -- note the Stem Splitter pulls in demucs + torch,
     a large ~2-3 GB download you can skip if you will not split stems.)

  3. Bundled command-line tools (the "requirements bundle")
     ParaKit shells out to several tools that are NOT Python packages:
       - FFmpeg (ffmpeg / ffplay / ffprobe) -- audio conversion / pydub
       - yt-dlp (+ deno, its JS signature runtime) -- YouTube -> FLAC downloads
       - ADB (+ AdbWinApi.dll, AdbWinUsbApi.dll) -- "push to Quest" / transfer

     These are distributed separately as the Requirements.zip bundle
     (about 174 MB -- too large for the Git repo). Download it here:
       https://limewire.com/d/HrcqC#lS73gPUpJa

     The Jarredou model is on that same LimeWire page (its original repo is
     down; there are HuggingFace mirrors, but this saves digging through a
     giant repo and a 600+ page report).

     Extract it, then place the files next to "ParaKit v4.0.py", or keep them
     in the included Requirements\ subfolder beside it. They are kept out of
     the Git tree on purpose -- large binaries with their own licenses, well
     over GitHub's per-file size limit. Leave yt-dlp's auto-update on so it
     stays current with YouTube changes.

------------------------------------------------------------------------------
  RUN IT
------------------------------------------------------------------------------

    py -3.12 "ParaKit v4.0.py"

  ...or just double-click "Run ParaKit v4.0.bat".

  Typical workflow:
    1. Stem Splitter     -- isolate drums from the backing track
    2. Audio -> MIDI     -- transcribe the drums-only stem to MIDI
    3. MIDI editor       -- clean up and refine the chart
    4. Preview / Practice-- watch it as falling notes
    5. Song Tester       -- confirm sync
    6. Assets            -- set metadata, album art, preview clip
    7. Export            -- Paradiddle .rlrr or Clone Hero .chart

------------------------------------------------------------------------------
  WEB EDITIONS -- Practice (v2 / v3) and Preview Track v2
------------------------------------------------------------------------------

  What these web tools are for -- and what they are NOT:
  The Practice and Preview web editions are not meant to replace Paradiddle --
  or any other rhythm game you play your charts on. They are AUTHORING & QA
  tools. The whole point is to make charting easier: to spot and fix errors
  fast, and to confirm a chart "feels" right to actually play -- before it goes
  into the real game. Catch the snare that's a hair early, the crash that
  should be a ride, the part that just doesn't groove; fix it, and verify the
  fix, in seconds. They stay deeply customizable (Kit Studio, lane layouts,
  note shapes/sizes, palettes, and more).

  Two editions are available -- v2 and the new v3 -- on purpose. v3 is a
  from-scratch combined rebuild: the falling-note play PLUS a full Kit Studio
  and a polished, everything-up-front home. Both are kept up while it is still
  being decided which parts of each are best -- so try both and use whichever
  you prefer.

  USB-MIDI needs the right browser.
  These editions run in any modern browser, but direct USB drum-kit input uses
  the Web MIDI API, which is not supported everywhere. Playing on your KEYBOARD
  works in every browser; only the MIDI-kit input depends on this.

  TL;DR -- for a USB drum kit, open the page in Chrome or Edge.
  Per-platform USB-MIDI input support (from MDN's requestMIDIAccess data):

    Desktop  Chrome ............... Yes (v43)
    Desktop  Edge ................. Yes (v79)
    Desktop  Opera / Brave / etc .. Yes (v30)
    Desktop  Firefox ............. Yes, with caveats (v108)
    Desktop  Safari .............. No (*)
    Mobile   Chrome for Android .. Yes (v43)
    Mobile   Opera for Android ... Yes (v30)
    Mobile   Samsung Internet .... Yes (v4)
    Mobile   Android WebView ..... Yes (v43)
    Mobile   Firefox for Android . No
    Mobile   Safari on iOS ....... No (*)
    Mobile   WebView on iOS ...... No (*)
    Other    Node.js ............. No

    (*) Some otherwise-unsupported / limited cases can be enabled with extra
        configuration (a permission prompt, a browser flag, or a polyfill).
        Firefox works since v108 but MDN still flags the whole API "not
        Baseline."
        Sources:
          https://developer.mozilla.org/en-US/docs/Web/API/Web_MIDI_API#browser_compatibility
          https://caniuse.com/midi

  Practice Mode v3 -- the combined rebuild (with Kit Studio)
    Kit Studio is the built-in customization studio: change the order of lanes,
    change note sizes and shapes, and much more. v3 does everything v2 does --
    notes down 8 lanes, keyboard + USB-MIDI play, latency calibration, a results
    screen with a timing histogram -- and adds:
      - Kit Studio (the headline) -- rearrange lanes; set each lane's
        color / shape / width; add aux lanes; lefty-flip the whole kit; save
        kit presets; pin a kit to a song. Edit it live mid-song or from home.
      - A polished Song / Setup / Input home -- every option up front: your
        songs folder (a searchable library of .rlrr packages) or a single-chart
        load, the play toggles + fall-time / note-size sliders, and your MIDI
        device + key bindings shown inline.
      - Native .rlrr -- point it at a Paradiddle songs folder and play any
        chart, or load a single .rlrr (+ optional audio; a synth fills in if
        there is none).
      - In-play live-settings dock (toggle with H), loop A/B, speed control,
        on-screen pads, colorblind-safe palettes, and per-song calibration.
    Try it: open this file in a modern browser (Chrome or Edge for USB-MIDI):
      Practice Window v3 - Web Edition/parakit-practice-v3.html

  Practice Mode v2
    The falling-note Practice experience, rebuilt from scratch as a fast,
    self-contained web app. Notes fall down 8 lanes in time with the music while
    you play along on a USB MIDI drum kit or your keyboard.
      - Rock-solid timing -- a sample-accurate Web Audio clock, no drift.
      - One file, zero setup -- open it in any modern browser; includes a
        built-in demo plus a synth that makes any chart audible with no audio
        file.
      - Latency calibration, mid-song mix/stem switching, touch support, a
        results screen with a timing histogram, full keyboard + MIDI play.
      - Loading your own song -- click Choose MIDI or Choose .rlrr to load a
        chart, and Full Mix / Drum Stem to load the audio (no audio file? the
        chart is synthesized so it is still audible).
    Try it: open this file in a modern browser (Chrome or Edge for USB-MIDI):
      Practice Window v2 - Web Edition/parakit-practice.html

  Both v2 and v3 are complete and ready to play today. A single best-of-both
  native (.py) version folded back into the app is planned for a later release.

  Preview Track v2 -- Web Edition
    Watch your drum chart fall in time with the music -- then fix what's wrong
    without ever leaving the view. Preview Track is the REVIEW half of ParaKit's
    Preview/Practice tab, rebuilt as a fast, self-contained web app. Notes
    scroll down 8 lanes synced to the audio so you can catch detection problems,
    and the headline of v2 is a live Edit Mode that lets you fix them right
    there on the falling chart, then resume.
      - Edit Mode (press E) -- pause and the subdivision grid becomes a precise
        ruler. Click an empty spot to place a note (snapped to grid); drag a
        note vertically to move it in time, horizontally to reclassify its lane.
        Right-click deletes (hold & sweep = eraser); wheel scrubs, Ctrl+wheel
        zooms; Ctrl+Z / Ctrl+Y undo/redo.
      - Tap-along charting -- keys 1-8 drop a note at the hit line, even during
        playback. Record captures live keyboard/MIDI hits with an optional
        Count-in + Metronome.
      - Review controls -- Speed 0.5x-1.25x, Fall time, Grid (1/4-1/32) + Snap,
        Pads for mouse/touch, Receive a chart from the MIDI editor, MIDI in for
        a USB kit, and built-in demo charts.
      - Hide notes past the hit line -- a toggle (off by default) that hides
        notes once they cross the hit line / playhead.
      - Loading your own song -- click Mix / Drums / Stems to load audio, and
        Import to load a chart (parakit-chart-v1 JSON/MIDI); Export saves your
        edits. Charts round-trip with the MIDI editor and Practice v2.
    Try it: open this file in a modern browser (Chrome or Edge for USB-MIDI):
      Preview Track v2 - Web Edition/parakit-preview.html

------------------------------------------------------------------------------
  RTX 50-SERIES GPUs AND STEM SPLITTING
------------------------------------------------------------------------------

  The stem splitter uses GPU acceleration on NVIDIA GTX 10-series through
  RTX 50-series. As of v4.4.58-12, ParaKit detects your GPU's architecture and
  uses CUDA whenever your installed PyTorch supports it -- including RTX
  50-series (Blackwell -- 5070/5080/5090), which needs a CUDA 12.8+ (cu128)
  PyTorch build. If your PyTorch does not include your GPU's architecture, the
  split log reports which architectures it DOES support, and the app falls back
  to CPU (still works, just slower). AMD / Intel GPUs are CPU-only too (Demucs
  needs CUDA).

  A working GPU fix exists (CUDA 12.8 / cu128 PyTorch + a save-path tweak) --
  see docs/TROUBLESHOOTING.md. A separate, creator-verified RTX 50-series build
  with GPU acceleration configured out of the box is also being prepared, to
  ship as a follow-up. The CPU fallback always stays in the code regardless --
  the point is that the feature works on every machine, even if a bit slower.

------------------------------------------------------------------------------
  BUILD YOUR OWN VERSION
------------------------------------------------------------------------------

  Because this is the full source, you can add features, remove them, rearrange
  the UI, or make your own personal ParaKit. See docs/BUILDING.md for running
  from source and compiling a standalone .exe.

------------------------------------------------------------------------------
  LICENSE
------------------------------------------------------------------------------

  ParaKit is released under the GNU General Public License v3.0 (see LICENSE).
  In short: you are free to use, study, modify, and share it -- but if you
  distribute a modified version, you must also make your source available under
  the same license. This keeps ParaKit and everything built from it free and
  open.

  Bundled third-party tools (FFmpeg, yt-dlp, ADB) and Python dependencies each
  carry their own licenses.

==============================================================================
  ParaKit -- free forever, no ads.
  Full fix/change log: CHANGELOG.txt
==============================================================================
