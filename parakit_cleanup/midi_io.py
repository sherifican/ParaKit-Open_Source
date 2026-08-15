"""
midi_io.py — mido-based MIDI <-> {class: onsets} I/O for the cleanup sidecar.

Responsibilities
----------------
1. parse_midi(path)  -> (est_by_class {lane: sorted np.array of onset secs},
                         notes [NoteRec(time, pitch, velocity, lane)...])
   Reads every drum note, maps its GM pitch -> ParaKit lane via the SAME
   GM_DRUM_MAP the detection-research harness's loader used (so it round-trips
   the detector's own output and ParaDB ground truth identically). ⛔ That
   harness is NOT in this repo — an earlier revision cited
   `tools/detection_harness/loaders.py` as if it were openable here; it never
   was (see the provenance note atop parakit_cleanup/passes.py). The map below
   is the live authority.

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


#: Two cymbal notes in the SAME lane closer than this are a doubling artifact,
#: not playing. The observed population sits at 16-24 ms (see the module note on
#: `_drop_cymbal_doubles`); 25 ms leaves headroom below any real subdivision —
#: a 32nd note is 31 ms at 240 BPM and 25 ms only at 300 BPM. Raising it past
#: ~30 ms starts eating genuine fast hi-hat work, which is why it is not wider.
CYM_DOUBLE_GAP_S = 0.025


def apply_cleanup(notes, cleaned_est, do_cymbal=True, do_kick=True,
                  drop_doubles=True, double_gap_s=CYM_DOUBLE_GAP_S):
    """Rewrite ``notes`` to match ``cleaned_est`` preserving velocity + timing.

    - KICK (if do_kick): drop every kick NoteRec whose onset is no longer in
      cleaned_est["kick"]. Never adds a kick.
    - CYMBAL (if do_cymbal): the three cymbal lanes are a relabel. For each
      cymbal NoteRec we look up which cymbal lane its onset now belongs to in
      cleaned_est and rewrite its pitch to that lane's canonical pitch, keeping
      time + velocity. Non-cymbal, non-kick notes pass through untouched.
    - DOUBLES (if do_cymbal and drop_doubles): same-lane cymbal notes within
      ``double_gap_s`` are collapsed. See `_drop_cymbal_doubles`.

    ⛔ THE CYMBAL RELABEL IS NO LONGER COUNT-PRESERVING, AND THAT IS THE POINT.
    It used to be, and the docstring advertised it: "the multiset of cymbal
    ONSETS is identical before/after, only lane assignment changes." That
    invariant is exactly what produced the bug `_drop_cymbal_doubles` fixes —
    folding two lanes onto one instant while promising to preserve every onset
    means the collision is preserved too. Callers that compare cymbal counts
    before/after must read the returned note list, not assume equality;
    `cleanup.py` computes `cymbal_relabeled` from ``cleaned_est`` and is
    unaffected, while `n_notes_after` is taken from the returned list and
    tracks removals correctly.

    Returns a NEW list (input ``notes`` is not mutated)."""
    out = []

    # Pre-build per-lane remaining-onset multisets we consume as we assign, so
    # duplicate onsets in a lane are matched one-for-one.
    kick_keep = _onset_multiset(cleaned_est.get("kick", [])) if do_kick else None
    cym_pools = None
    cym_assign = {}
    if do_cymbal:
        cym_pools = {lane: _onset_multiset(cleaned_est.get(lane, [])) for lane in CYM_LANES}
        # ⛔ TWO PASSES, AND THE ORDER OF THEM IS THE WHOLE FIX. "Prefer keeping the
        # same lane" used to be evaluated one note at a time while walking the list,
        # which made it FIRST-COME-FIRST-SERVED: an earlier note that could not stay
        # put grabbed the first lane with a free slot, and that slot was sometimes
        # the one a LATER note needed in order to stay where it already was.
        #
        # Concretely, a hi-hat and a crash struck together - among the most ordinary
        # things in a drum chart - relabelled to ride+crash came out as crash+ride:
        # the hi-hat took the crash's slot, and the crash, having none left, was
        # pushed into ride. Lane COUNTS were right, so every count-based check passed,
        # while each surviving note carried the other one's velocity and the crash
        # moved lanes for no reason. In VR a lane is a physical object in space, so
        # that is a wrong arm movement plus a wrong loudness, on a note the cleanup
        # had no opinion about.
        #
        # Pass A: every note that CAN keep its own lane claims that slot first, so
        # the preference is global rather than positional. Pass B: whatever is left
        # over takes whatever remains. Notes that were genuinely relabelled are
        # unaffected; this only stops them from displacing notes that were not.
        idxs = [i for i, n in enumerate(notes) if n.lane in CYM_LANES]
        for i in idxs:                                   # pass A - stay put
            key = round(notes[i].time / _MATCH_TOL)
            if cym_pools[notes[i].lane].get(key, 0) > 0:
                cym_pools[notes[i].lane][key] -= 1
                cym_assign[i] = notes[i].lane
        for i in idxs:                                   # pass B - the movers
            if i in cym_assign:
                continue
            key = round(notes[i].time / _MATCH_TOL)
            for lane in CYM_LANES:
                if cym_pools[lane].get(key, 0) > 0:
                    cym_pools[lane][key] -= 1
                    cym_assign[i] = lane
                    break

    #: parallel to ``out``: was this note RELABELLED into its lane, or did it
    #: already live there? Only meaningful for cymbals. `_drop_cymbal_doubles`
    #: uses it to break an exact tie in favour of the note that was already
    #: home, so a collision cannot silently rewrite a hi-hat articulation the
    #: cleanup had no opinion about.
    moved = []

    for i, n in enumerate(notes):
        if do_kick and n.lane == "kick":
            key = round(n.time / _MATCH_TOL)
            if kick_keep.get(key, 0) > 0:
                kick_keep[key] -= 1
                out.append(NoteRec(n.time, n.pitch, n.velocity, n.lane))
                moved.append(False)
            # else: phantom — dropped (no append)
            continue

        if do_cymbal and n.lane in CYM_LANES:
            # Lane already decided by the two-pass assignment above; the pools were
            # consumed there, so nothing is decremented here.
            new_lane = cym_assign.get(i)
            if new_lane is None:
                # Onset not found in any cymbal lane (should not happen — the
                # relabel hands out one slot per cymbal onset). Keep the note
                # as-is to avoid silently dropping it.
                out.append(NoteRec(n.time, n.pitch, n.velocity, n.lane))
                moved.append(False)
                continue
            # A note that kept its lane keeps its ORIGINAL pitch: the cleanup had no
            # opinion about it, and re-stamping the canonical pitch here would flatten
            # e.g. a hi-hat read at 46 to 42 on a note nothing acted on — the same
            # class of gratuitous rewrite write_midi's docstring warns about for toms.
            pitch = n.pitch if new_lane == n.lane else LANE_TO_PITCH[new_lane]
            out.append(NoteRec(n.time, pitch, n.velocity, new_lane))
            moved.append(new_lane != n.lane)
            continue

        # untouched lane
        out.append(NoteRec(n.time, n.pitch, n.velocity, n.lane))
        moved.append(False)

    order = sorted(range(len(out)), key=lambda i: (out[i].time, out[i].pitch))
    out = [out[i] for i in order]
    moved = [moved[i] for i in order]

    if do_cymbal and drop_doubles:
        out = _drop_cymbal_doubles(out, moved, double_gap_s)
    return out


def _drop_cymbal_doubles(notes, moved, gap_s=CYM_DOUBLE_GAP_S):
    """Collapse same-lane cymbal notes that land on top of each other.

    ⛔ WHY THIS EXISTS. Owner report 2026-08-14, with a MIDI-Editor screenshot:
    the detector places a cymbal — "pretty much only hi-hats" — right on top of
    another note in the same lane. It is easy to miss in review and only shows
    up in game, where the player has to go back and clean it by hand.

    Measured on the 290-pack corpus dump, the cymbal lanes carry TWO distinct
    doubling populations, and they need different treatment:

    A. EXACT duplicates, gap ~ 0, IDENTICAL velocity. 57 pairs (25 hi-hat,
       17 crash, 15 ride) — and all 57 have matching velocities, 0 exceptions.
       That signature is the cross-lane collision this relabel can create: one
       audio onset present in two cymbal lanes, both folded into the same lane.
       Per-lane dedup ran BEFORE the relabel and never saw across lanes, and
       nothing dedupes after it, so the pair survives to the chart.

    B. NEAR doubles, 78% DIFFERENT velocity. At the shipped 25 ms gap: 125 pairs
       spanning 9.7-24.5 ms, 115 of them hi-hat. (An earlier version of this note
       said "129 pairs, 119 hi-hat, 16-24 ms" - those came from a 30 ms survey
       window, not the rule that ships.) Two genuinely distinct detections that squeaked past the
       per-lane hi-hat gap (20 ms spectral / 12 ms hybrid). Clustered by song —
       51 of 290 packs, and an affected song usually has several.

    WHICH ONE IS DROPPED:

    - A (exact): the notes are the same instant at the same velocity, so the
      only real choice is which ARTICULATION survives. Keep the note that was
      already in this lane over one relabelled into it — the cleanup expressed
      no opinion about the stayer's pitch, and discarding it would flatten e.g.
      a hi-hat read at 46 down to 42 for no reason. With no stayer, or two, keep
      the earlier.
    - B (near): drop the LATER note. Measured, not assumed - see the block at
      the decision itself. The pair is one strike seen by two producers: the
      earlier note carries the ML timestamp and the later one the spectral
      timestamp, 81 times out of 81 in the corpus, and the app's own hybrid
      merge already prefers ML for exactly this case.

    ⚠ WHY THE OBVIOUS CHECKS CANNOT SETTLE THIS, so they are not attempted:
    per-hit velocity is `band_energy[fr:fr+3].max()`, a ~35 ms FORWARD window at
    11.6 ms/frame, so two notes 16-24 ms apart share frames and read the same
    peak (median delta -3; member velocity medians 82-89, IQR ~78-98; 25 of 125
    pairs exactly equal). An
    onset-envelope test is circular for the same reason - a rise-fire plus a
    peak-fire puts the later note on the envelope max by construction. The chart
    is blind: pairs sit ~20 ms apart inside a +/-50 ms match window, so both
    members match the same charted note. The clocks are the only non-circular
    evidence available.

    ⚠ It also runs AFTER the relabel deliberately: pass A of the assignment
    needs both notes present to hand out lane slots correctly. Dropping earlier
    would change which note keeps its lane.

    ``moved[i]`` marks whether ``notes[i]`` was relabelled into its lane.
    ``notes`` must already be sorted by (time, pitch). Returns a NEW list."""
    if not notes:
        return list(notes)

    drop = set()
    by_lane = {}
    for i, n in enumerate(notes):
        if n.lane in CYM_LANES:
            by_lane.setdefault(n.lane, []).append(i)

    for _lane, idxs in by_lane.items():
        # Greedy left-to-right against the last SURVIVOR, so a run of three
        # collapses to one rather than alternating kept/dropped.
        keep = idxs[0]
        for j in idxs[1:]:
            gap = notes[j].time - notes[keep].time
            # `- _MATCH_TOL` because the boundary is not float-exact: two notes
            # a nominal 25 ms apart subtract to 0.024999999999999911, which
            # would put a gap sitting exactly ON the threshold inside it and
            # delete a note the rule means to keep.
            if gap >= gap_s - _MATCH_TOL:
                keep = j
                continue
            if gap <= _MATCH_TOL:
                # A — identical instant. Prefer the note already in this lane.
                if moved[keep] and not moved[j]:
                    drop.add(keep)
                    keep = j
                else:
                    drop.add(j)
            else:
                # B — two producers, one strike. Keep the EARLIER note.
                #
                # ⛔ THIS DIRECTION WAS REVERSED ON 2026-08-14 AFTER MEASURING IT.
                # It first shipped as drop-the-earlier, from a report that the
                # phantom is "95% of the time the note on the left" when reviewed
                # against the waveform in the editor. The provenance measurement
                # (`tools/cymbal_double_provenance_2026-08-14.py`) says otherwise,
                # and says it unanimously: of the 81 corpus pairs whose members sit
                # on distinguishable clocks, the earlier note is on the ML grid and
                # the later on the spectral grid in 81 cases and the reverse in 0.
                #
                # The app already resolves this exact pair the same way 3 ms lower
                # down: `_smart_merge` prefers ML timing "for hits both engines saw"
                # because the model "was trained on precisely timed annotations",
                # and DISCARDS the spectral twin inside its 18 ms window.
                #
                # The twin survives to here for TWO reasons, and an earlier version
                # of this comment gave only the first: (a) part of the population is
                # wider than 18 ms, but (b) 23 of the 81 sit INSIDE it, at
                # 12.2-18.9 ms, and survived because `_smart_merge` matches by class
                # NAME - those members were in different cymbal classes until this
                # relabel folded them into one lane, so the merge never compared
                # them. Dropping the earlier member here would delete the ML
                # timestamp the merge deliberately keeps, making the pipeline
                # resolve identical pairs oppositely depending on which class they
                # happened to start in.
                #
                # ⚠ A RESIDUAL BAND IS LEFT ON PURPOSE: two-producer pairs 25-30 ms
                # apart escape the merge, this rule (gap 25 ms) and the ML cymbal
                # min-gap (30 ms). Four exist in the corpus. Closing it would need a
                # gap above 30 ms, which starts eating real 32nd-note hi-hats.
                #
                # The eyeball report is not evidence against this, because it is not
                # independent of it: spectral flux peaks after the attack, so the
                # later note sits on the visible energy blob and the earlier one
                # looks misplaced. Reading the waveform, per-hit velocity, and an
                # onset envelope are all the same measurement, and all three favour
                # the blob over the true leading edge.
                drop.add(j)

    if not drop:
        return list(notes)
    return [n for i, n in enumerate(notes) if i not in drop]


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
    tom alternation — the `_tom_alt_idx % 2` pitch pick in the app's convert
    worker; symbol anchor, the line number this cited drifted) and is invisible to the
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
