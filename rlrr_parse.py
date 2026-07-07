"""
rlrr_parse.py  (Extractor Mini App — mirror copy)
==================================================
Mirror of the parsing core from paradb_extract.py.

SYNC RULE: The five shared functions (parse_rlrr, event_to_class,
event_velocity, event_time, write_ground_truth_mid) must stay identical
to their counterparts in paradb_extract.py. Use sync_check.py at repo root
to detect and apply drift. Direction: paradb_extract.py -> this file only.

Mirror-only additions (extract_notes_from_rlrr, CLASS_TO_MIDI, LANE_NAMES,
the MIDI timing constants) exist here as support for the GUI; they never
flow back into the agent extractor.
"""
from __future__ import annotations

import json
from pathlib import Path

import mido

# ---------------------------------------------------------------------------
# MIDI timing constants (120 BPM fixed grid)
# ---------------------------------------------------------------------------
TICKS_PER_BEAT = 480
TEMPO_US = 500_000
TICKS_PER_SEC = TICKS_PER_BEAT * 1_000_000 / TEMPO_US   # 960
NOTE_DURATION_TICKS = 120

# ---------------------------------------------------------------------------
# Instrument class -> (lane_idx, midi_note, lane_name)
# ---------------------------------------------------------------------------
CLASS_TO_MIDI: dict[str, tuple[int, int, str]] = {
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

LANE_NAMES = ["Hi-Hat", "Crash", "Snare", "Tom 1", "Tom 2", "Tom 3", "Ride", "Kick"]


# ---------------------------------------------------------------------------
# Shared functions — kept in sync with paradb_extract.py via sync_check.py
# ---------------------------------------------------------------------------

def parse_rlrr(rlrr_path: Path):
    """Parse a .rlrr file. Returns (metadata, events, instruments, drum_files).

    Handles both event formats:
      - name-based  (events carry instrument name, e.g. "BP_Kick_C_1")
      - index-based (events carry instrumentIndex referencing instruments[])

    Raises RuntimeError on encoding failure or malformed JSON so callers
    get a clean message rather than a raw traceback.
    """
    text = None
    for enc in ("utf-8-sig", "utf-8", "utf-16", "cp1252"):
        try:
            text = rlrr_path.read_text(encoding=enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if text is None:
        raise RuntimeError(f"Could not decode {rlrr_path.name} with any known encoding")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Malformed JSON in {rlrr_path.name}: {exc}") from exc
    meta = data.get("recordingMetadata", {})
    events = data.get("events", [])
    instruments = data.get("instruments", [])
    audio_data = data.get("audioFileData", {})
    drum_files = audio_data.get("drumTracks", [])
    return meta, events, instruments, drum_files


def event_to_class(e, instruments):
    """Return the instrument class for an event, handling both formats."""
    if "name" in e:
        name = e["name"]
        for cls in CLASS_TO_MIDI:
            if name.startswith(cls):
                return cls
        return ""
    idx = e.get("instrumentIndex", -1)
    if 0 <= idx < len(instruments):
        return instruments[idx].get("class", "")
    return ""


def event_velocity(e):
    """Return normalised velocity (0.0-1.0)."""
    if "velocity" in e:
        v = e["velocity"]
        return float(v) if not isinstance(v, str) else float(v)
    if "vel" in e:
        v = e["vel"]
        raw = float(v) if not isinstance(v, str) else float(v)
        return raw / 127.0
    return 0.75


def event_time(e):
    """Return event time in seconds."""
    t = e.get("time", 0)
    return float(t) if not isinstance(t, str) else float(t)


def write_ground_truth_mid(notes, output_path: Path):
    """Write a Type-1 MIDI file. notes: list of (time_sec, midi_note, velocity_0_127)."""
    mid = mido.MidiFile(type=1, ticks_per_beat=TICKS_PER_BEAT)
    track0 = mido.MidiTrack()
    track0.append(mido.MetaMessage("set_tempo", tempo=TEMPO_US, time=0))
    track0.append(mido.MetaMessage("end_of_track", time=0))
    mid.tracks.append(track0)
    track1 = mido.MidiTrack()
    notes_sorted = sorted(notes, key=lambda x: x[0])
    # Group simultaneous events so that all note_ons at the same tick are
    # emitted first, then all note_offs. The earlier per-event "on then off"
    # emission pattern caused three bugs:
    #   1. Compound drift: last_tick = tick + NOTE_DURATION_TICKS under-reported
    #      the actual track end position by NOTE_DURATION_TICKS per simultaneous
    #      pair, accumulating multi-second drift across a full song.
    #   2. Per-pair offset: the second of each simultaneous pair landed +125 ms
    #      after the first because the previous note's note_off occupied that
    #      slot.
    #   3. Close-event push: if event B's source tick was within
    #      NOTE_DURATION_TICKS of event A's tick, B's note_on got pushed forward
    #      to wherever A's note_off landed. Drum hits at fast tempos (32nd notes,
    #      rolls) are routinely <125ms apart, so this drifted everything.
    # Bug discovered 2026-05-03; fix uses a 1-tick note duration so note_off
    # never blocks the next event. Note duration is decorative for drum data —
    # scoring only reads note_on times. Fix verified on Decode_Expert.rlrr.
    from collections import defaultdict
    NOTE_OFF_DELTA = 1  # 1 tick = ~1 ms; just enough to make the SMF parse
    events_by_time = defaultdict(list)
    for time_sec, note, vel in notes_sorted:
        tick = int(round(time_sec * TICKS_PER_SEC))
        events_by_time[tick].append((note, vel))

    last_tick = 0
    for tick in sorted(events_by_time.keys()):
        notes_at_t = events_by_time[tick]
        delta_to_first_on = max(0, tick - last_tick)
        # All note_ons at this tick (first carries delta; rest at delta=0
        # so they share the same absolute tick).
        for i, (note, vel) in enumerate(notes_at_t):
            track1.append(mido.Message(
                "note_on", channel=0, note=note, velocity=vel,
                time=(delta_to_first_on if i == 0 else 0)))
        # All note_offs 1 tick after (first carries the 1-tick delta;
        # rest at delta=0 so they're also simultaneous).
        for i, (note, vel) in enumerate(notes_at_t):
            track1.append(mido.Message(
                "note_on", channel=0, note=note, velocity=0,
                time=(NOTE_OFF_DELTA if i == 0 else 0)))
        last_tick = tick + NOTE_OFF_DELTA
    track1.append(mido.MetaMessage("end_of_track", time=0))
    mid.tracks.append(track1)
    mid.save(str(output_path))


# ---------------------------------------------------------------------------
# Mirror-only: helpers used by the GUI (never synced to paradb_extract.py)
# ---------------------------------------------------------------------------

def write_mid_with_metadata(
    notes: list,
    output_path: Path,
    title: str = "",
    artist: str = "",
) -> None:
    """Like write_ground_truth_mid but embeds title/artist as track_name MetaMessages."""
    label = f"{title} - {artist}".strip(" -") if (title and artist) else (title or artist or "")
    mid = mido.MidiFile(type=1, ticks_per_beat=TICKS_PER_BEAT)
    track0 = mido.MidiTrack()
    if label:
        track0.append(mido.MetaMessage("track_name", name=label, time=0))
    track0.append(mido.MetaMessage("set_tempo", tempo=TEMPO_US, time=0))
    track0.append(mido.MetaMessage("end_of_track", time=0))
    mid.tracks.append(track0)
    track1 = mido.MidiTrack()
    if label:
        track1.append(mido.MetaMessage("track_name", name=label, time=0))
    notes_sorted = sorted(notes, key=lambda x: x[0])
    # Same drift-fix logic as write_ground_truth_mid above. See that function's
    # comment for the full bug detail. Bug discovered 2026-05-03.
    from collections import defaultdict
    NOTE_OFF_DELTA = 1
    events_by_time = defaultdict(list)
    for time_sec, note, vel in notes_sorted:
        tick = int(round(time_sec * TICKS_PER_SEC))
        events_by_time[tick].append((note, vel))

    last_tick = 0
    for tick in sorted(events_by_time.keys()):
        notes_at_t = events_by_time[tick]
        delta_to_first_on = max(0, tick - last_tick)
        for i, (note, vel) in enumerate(notes_at_t):
            track1.append(mido.Message(
                "note_on", channel=0, note=note, velocity=vel,
                time=(delta_to_first_on if i == 0 else 0)))
        for i, (note, vel) in enumerate(notes_at_t):
            track1.append(mido.Message(
                "note_on", channel=0, note=note, velocity=0,
                time=(NOTE_OFF_DELTA if i == 0 else 0)))
        last_tick = tick + NOTE_OFF_DELTA
    track1.append(mido.MetaMessage("end_of_track", time=0))
    mid.tracks.append(track1)
    mid.save(str(output_path))


def extract_notes_from_rlrr(rlrr_path: Path) -> tuple[list, dict, str]:
    """Parse one .rlrr and return (midi_notes, metadata, error_msg).

    midi_notes: list of (time_sec, midi_note, velocity_0_127)
    error_msg:  empty string on success.
    """
    try:
        meta, events, instruments, _ = parse_rlrr(rlrr_path)
    except RuntimeError as exc:
        return [], {}, str(exc)

    notes = []
    try:
        for e in events:
            cls = event_to_class(e, instruments)
            mapping = CLASS_TO_MIDI.get(cls)
            if mapping:
                vel = min(127, max(1, int(round(event_velocity(e) * 127))))
                notes.append((event_time(e), mapping[1], vel))
    except Exception as exc:
        return [], {}, f"malformed event data: {exc.__class__.__name__}: {exc}"

    if not notes:
        return [], meta, "No mappable drum notes found in this .rlrr"
    return notes, meta, ""
