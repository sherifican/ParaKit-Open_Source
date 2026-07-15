"""
midi_io.py — mido-based MIDI <-> {class: onsets} I/O for the cleanup sidecar.

Responsibilities
----------------
1. parse_midi(path)  -> (est_by_class {lane: sorted np.array of onset secs},
                         notes [NoteRec(time, pitch, velocity, lane)...])
   Reads every drum note, maps its GM pitch -> ParaKit lane via the SAME
   GM_DRUM_MAP as tools/detection_harness/loaders.py (so it round-trips the
   detector's own output and ParaDB ground truth identically).

2. apply_cleanup(notes, cleaned_est, do_cymbal, do_kick) -> new note list
   Rewrites the note list to match the cleaned onset dict while PRESERVING each
   surviving note's velocity + timing:
     - cymbal relabel: a cymbal note whose onset moved to a different cymbal lane
       keeps its time+velocity, only its PITCH changes to the new lane's pitch.
     - kick removal:   a kick note whose onset is gone from the cleaned kick lane
       is DELETED. No notes are ever created.

3. write_midi(path, notes, ticks_per_beat, tempo) -> writes a GM drum MIDI on
   channel 9 using the APP note mapping (kick 36 / snare 38 / floor_tom 41 /
   hihat 42 / crash 49 / ride 51 / tom_mid 48).

Imports: numpy + mido. No sklearn / joblib / onnx.

NOTE-MAPPING CONTRACT (matches ParaKit v4.0.py + loaders.GM_DRUM_MAP)
--------------------------------------------------------------------
WRITE (one canonical pitch per lane — the app/detector's own output notes):
    kick=36, snare=38, hihat=42, crash=49, ride=51, tom_mid=48, floor_tom=41
READ (GM_DRUM_MAP, verbatim from loaders.py — many pitches fold into a lane):
    35/36->kick, 37/38/40->snare, 42/44/46->hihat, 49/57/52/55->crash,
    51/59/53->ride, 41/43->floor_tom, 45/47/48/50->tom_mid
The write set is a strict subset of the read set, so write->read is lossless for
every lane and the gate's round-trip is exact.
"""
from __future__ import annotations

import os
import tempfile

import numpy as np
import mido

# ---- READ map: GM percussion pitch -> ParaKit lane (verbatim loaders.py) -----
GM_DRUM_MAP = {
    35: "kick", 36: "kick",
    37: "snare", 38: "snare", 40: "snare",
    42: "hihat", 44: "hihat", 46: "hihat",
    49: "crash", 57: "crash", 52: "crash", 55: "crash",
    51: "ride", 59: "ride", 53: "ride",
    41: "floor_tom", 43: "floor_tom",
    45: "tom_mid", 47: "tom_mid", 48: "tom_mid", 50: "tom_mid",
}

# ---- WRITE map: ParaKit lane -> canonical GM pitch (app/detector output) ------
LANE_TO_PITCH = {
    "kick": 36,
    "snare": 38,
    "hihat": 42,
    "crash": 49,
    "ride": 51,
    "tom_mid": 48,
    "floor_tom": 41,
}

# lane order for the est dict — the 7 ParaKit lanes (loaders.CLASSES order)
GM_DRUM_MAP_LANES = ("kick", "snare", "hihat", "crash", "ride", "tom_mid", "floor_tom")

CYM_LANES = ("hihat", "crash", "ride")
DRUM_CHANNEL = 9  # GM percussion channel (0-indexed)
# HIGH-RESOLUTION grid so seconds<->ticks round-trips to sub-audio-sample
# precision. The SMF header stores ticks_per_beat as a SIGNED int16 (max 32767),
# so we use TPB == tempo_us == 32767: seconds/tick = (tempo/1e6)/TPB =
# 32767e-6 / 32767 = 1e-6 s = 1 us/tick (~0.044 sample @ 44.1 kHz). A 177 s track
# -> ~1.77e8 ticks, within MIDI's 28-bit VLQ.
DEFAULT_TPB = 32767          # max legal ticks_per_beat (int16)
DEFAULT_TEMPO = 32767        # us per beat -> 1 us / tick

# Audio sample rate the onset feature extractor uses (features.SR). Onset times
# are SNAPPED to this sample grid on write so a round-tripped onset's int(t*sr)
# sample index is BIT-IDENTICAL to the harness's int(t_raw*sr). The feature
# extractor windows on sample int(t*sr); the harness floors the RAW onset to
# sample n = int(t_raw*sr). We snap the onset to the CENTER of that same bucket,
# (n+0.5)/sr, so after the +-0.5 us MIDI round-trip jitter the value still floors
# to exactly n (a center is 0.5 sample = ~11 us from either edge, far outside the
# jitter). Without this, the 0.5 us MIDI rounding flips int(t*sr) by 1 sample for
# an onset within ~0.022 sample of a bucket edge, which can flip a single
# borderline gate decision (observed: 1 kick onset out of ~700). Snapping is
# sub-perceptual (1/44100 s) and makes the sidecar EXACTLY reproduce the harness.
SNAP_SR = 44100


def snap_to_sample(t, sr=SNAP_SR):
    """Snap an onset (seconds) to the CENTER of the audio-sample bucket the
    feature extractor floors it into: n = int(t*sr) -> (n + 0.5)/sr.

    int(snap_to_sample(t)*sr) == int(t*sr) for any t, and stays equal under the
    sub-microsecond MIDI tick round-trip, so the sidecar's feature window lands on
    the exact same sample as the harness."""
    n = int(float(t) * sr)
    return (n + 0.5) / sr

# match-tolerance when re-associating a cleaned onset back to its source note.
# Cleaned onsets are the SAME float seconds the detector emitted (post-passes
# never perturb a kept/relabeled onset's time), so this only guards float noise.
_MATCH_TOL = 1e-6


class NoteRec:
    """One drum note: onset seconds, GM pitch, velocity, and resolved lane."""
    __slots__ = ("time", "pitch", "velocity", "lane")

    def __init__(self, time, pitch, velocity, lane):
        self.time = float(time)
        self.pitch = int(pitch)
        self.velocity = int(velocity)
        self.lane = lane

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"NoteRec(t={self.time:.4f}, p={self.pitch}, v={self.velocity}, lane={self.lane})"


def parse_midi(path):
    """Parse a drum MIDI -> (est_by_class, notes).

    est_by_class: {lane: sorted np.array of onset seconds} over the 7 ParaKit
    lanes (mirrors loaders.load_midi output shape).
    notes: list[NoteRec] for every mapped note-on, in absolute-time order, each
    tagged with its resolved lane. Unmapped pitches are skipped (as in loaders)."""
    mid = mido.MidiFile(path)
    tpb = mid.ticks_per_beat or DEFAULT_TPB
    notes = []
    # Walk merged absolute ticks while tracking tempo (handles set_tempo events).
    abs_ticks = 0
    cur_tempo = DEFAULT_TEMPO
    # mido.merge_tracks yields delta-time messages in tempo-map order.
    for msg in mido.merge_tracks(mid.tracks):
        abs_ticks += msg.time
        if msg.type == "set_tempo":
            cur_tempo = msg.tempo
        elif msg.type == "note_on" and msg.velocity > 0:
            lane = GM_DRUM_MAP.get(msg.note)
            if lane is None:
                continue
            t = mido.tick2second(abs_ticks, tpb, cur_tempo)
            notes.append(NoteRec(t, msg.note, msg.velocity, lane))
    notes.sort(key=lambda n: (n.time, n.pitch))
    est = {lane: [] for lane in GM_DRUM_MAP_LANES}
    for n in notes:
        est[n.lane].append(n.time)
    est = {lane: np.sort(np.asarray(v, dtype=float)) for lane, v in est.items()}
    return est, notes


def _onset_multiset(arr):
    """Rounded-onset multiset (Counter) for tolerant membership tests."""
    from collections import Counter
    return Counter(round(float(t) / _MATCH_TOL) for t in np.asarray(arr, dtype=float))


def apply_cleanup(notes, cleaned_est, do_cymbal=True, do_kick=True):
    """Rewrite ``notes`` to match ``cleaned_est`` preserving velocity + timing.

    - KICK (if do_kick): drop every kick NoteRec whose onset is no longer in
      cleaned_est["kick"]. Never adds a kick.
    - CYMBAL (if do_cymbal): the three cymbal lanes are a relabel — the multiset
      of cymbal ONSETS is identical before/after, only lane assignment changes.
      For each cymbal NoteRec we look up which cymbal lane its onset now belongs
      to in cleaned_est and rewrite its pitch to that lane's canonical pitch,
      keeping time + velocity. Non-cymbal, non-kick notes pass through untouched.

    Returns a NEW list (input ``notes`` is not mutated)."""
    out = []

    # Pre-build per-lane remaining-onset multisets we consume as we assign, so
    # duplicate onsets in a lane are matched one-for-one.
    kick_keep = _onset_multiset(cleaned_est.get("kick", [])) if do_kick else None
    cym_pools = None
    if do_cymbal:
        cym_pools = {lane: _onset_multiset(cleaned_est.get(lane, [])) for lane in CYM_LANES}

    for n in notes:
        if do_kick and n.lane == "kick":
            key = round(n.time / _MATCH_TOL)
            if kick_keep.get(key, 0) > 0:
                kick_keep[key] -= 1
                out.append(NoteRec(n.time, n.pitch, n.velocity, n.lane))
            # else: phantom — dropped (no append)
            continue

        if do_cymbal and n.lane in CYM_LANES:
            key = round(n.time / _MATCH_TOL)
            new_lane = None
            # Prefer keeping the same lane if this onset still lives there.
            if cym_pools[n.lane].get(key, 0) > 0:
                new_lane = n.lane
            else:
                for lane in CYM_LANES:
                    if cym_pools[lane].get(key, 0) > 0:
                        new_lane = lane
                        break
            if new_lane is None:
                # Onset not found in any cymbal lane (should not happen — cymbal
                # is a count-preserving relabel). Keep the note as-is to avoid
                # silently dropping it.
                out.append(NoteRec(n.time, n.pitch, n.velocity, n.lane))
                continue
            cym_pools[new_lane][key] -= 1
            out.append(NoteRec(n.time, LANE_TO_PITCH[new_lane], n.velocity, new_lane))
            continue

        # untouched lane
        out.append(NoteRec(n.time, n.pitch, n.velocity, n.lane))

    out.sort(key=lambda x: (x.time, x.pitch))
    return out


def write_midi(path, notes, ticks_per_beat=DEFAULT_TPB, tempo=DEFAULT_TEMPO):
    """Write ``notes`` as a single-track GM drum MIDI on channel 9.

    Each NoteRec becomes a note_on (its velocity) + a short note_off. Times are
    converted seconds->ticks with the given tempo (symmetric with parse_midi's
    tick2second, so the round-trip onset is exact). Pitch is taken VERBATIM from
    the NoteRec: apply_cleanup already stamps the canonical app pitch on every
    note it RELABELS (only the cymbal lanes), and leaves every untouched note's
    original pitch intact. We must NOT re-canonicalize here — folding e.g. a
    surviving tom_mid note read at pitch 45 to the lane's canonical 48 would be a
    pitch change on a lane the cleanup never acts on (it destroys the app's 48/45
    tom alternation, ParaKit v4.0.py:12629-12630) and is invisible to the
    faithfulness gate (45 and 48 both re-read as tom_mid)."""
    mid = mido.MidiFile(ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=tempo, time=0))

    # Build (abs_tick, on/off, pitch, vel) event stream, then delta-encode.
    NOTE_LEN_TICKS = max(1, ticks_per_beat // 8)
    events = []  # (abs_tick, order, msg_kwargs)
    for n in sorted(notes, key=lambda x: (x.time, x.pitch)):
        pitch = n.pitch
        # Snap to the audio-sample-bucket center so the cleanup round-trip lands
        # the feature window on the exact same sample as the harness (see
        # snap_to_sample). Sub-perceptual (<0.5 sample @ 44.1 kHz).
        t = snap_to_sample(n.time)
        on_tick = int(round(mido.second2tick(t, ticks_per_beat, tempo)))
        off_tick = on_tick + NOTE_LEN_TICKS
        events.append((on_tick, 1, ("note_on", pitch, n.velocity)))
        events.append((off_tick, 0, ("note_off", pitch, 0)))
    # order tie-break: note_off (0) before note_on (1) at the same tick.
    events.sort(key=lambda e: (e[0], e[1]))

    prev = 0
    for abs_tick, _order, (mtype, pitch, vel) in events:
        delta = abs_tick - prev
        prev = abs_tick
        track.append(mido.Message(mtype, note=pitch, velocity=vel,
                                  channel=DRUM_CHANNEL, time=delta))
    # ATOMIC in-place write: serialize to a temp file in the same dir, then
    # os.replace() it over the target. A crash / disk-full DURING the save can
    # then never leave the user's MIDI truncated -- the original stays intact
    # until the fully-written replacement is swapped in atomically (same-fs).
    #
    # v4.7.22 -- a UNIQUE, SHORT temp name. This line was `path + ".pkcleanup.tmp"`
    # from 4.5.0 until now, and carried TWO bugs that the tom-OFF strip took three
    # versions to shed -- because the strip COPIED this pattern in 4.7.19 (citing
    # this function by name as its standard of correctness), then found both bugs in
    # the copy and fixed them THERE ONLY. Nobody came back to the original:
    #   * FIXED NAME -> a conversion killed between the save and the replace orphans
    #     that exact file forever; every later cleanup of THAT SONG targets it, so an
    #     un-writable orphan (AV scan / OneDrive upload / backup tool holding a
    #     handle, or a read-only flag) makes the cleanup raise PermissionError and
    #     silently no-op FOREVER for that song, while the chart is perfectly writable.
    #   * THE CHART'S PATH INSIDE A PATH COMPONENT -> NTFS caps every component at 255
    #     bytes (LongPathsEnabled does NOT lift it), so a long song title overran it:
    #     basename 241 works, 242 raises OSError. Both were reported "skipped".
    # Both were breaker-verified against this real function (INV19/INV20).
    # Atomicity lives in the os.replace, never in the name; the name only has to be
    # unique and identifiable. The component is now a fixed 23 bytes, whatever the song
    # is called. NOTE: none of this changes a single output byte -- the sidecar stays
    # bit-exact -- it only changes which scratch file the bytes pass through.
    _fd, tmp = tempfile.mkstemp(prefix=".pkcleanup.", suffix=".tmp",
                                dir=os.path.dirname(path) or ".")
    os.close(_fd)          # mido reopens by name
    try:
        mid.save(tmp)
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        raise
    return path
