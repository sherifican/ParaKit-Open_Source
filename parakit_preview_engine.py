"""parakit_preview_engine.py -- pure, Tk-free chart model for the Preview tab.

1:1 port of the v5 BETA "Preview" tab's data layer (NOT the Qt renderer -- that
lives in ``parakit_preview_tab.py``'s ``FallingCanvas``). Ported from:
  parakit_v5/ui/widgets/piano_roll/editor_note.py   -- EdNote, generate_demo_chart
  parakit_v5/ui/widgets/piano_roll/chart_model.py   -- ChartModel, Selection, snap_time
  parakit_v5/ui/widgets/piano_roll/time_index.py    -- TimeIndex (bucketed window index)
  parakit_v5/ui/widgets/piano_roll/lanes.py         -- lane spec / hotkeys / velocity bands
  parakit_v5/core/chart_import.py                   -- .mid / .rlrr readers
  parakit_v5/core/chart_json.py                     -- parakit-chart-v1 JSON codec
  parakit_v5/core/midi_export.py                    -- GM format-0 .mid writer

Deliberately dropped from the v5 core (not needed by the Preview tab, and out
of the dispatch's parity checklist): dedup/tempo-warp/review-issues/markers
Tools ops, the core.models.note.Note bridge type (this module returns/accepts
plain EdNote objects everywhere so the Tk tab needs nothing else imported).

No Tk, no numpy, no third-party GUI deps. ``mido`` is imported LAZILY and only
if present -- both the MIDI reader and writer fall back to a small hand-rolled
SMF0 parser/writer (``_read_midi_minimal`` / ``_write_midi_minimal``) so a
mido-less install still gets full Import/Export.
"""
from __future__ import annotations

import itertools
import json
import math
import operator
import random
import struct
from dataclasses import dataclass, field
from typing import Dict, Iterable, Iterator, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Lane spec -- SAME order/colours as parakit_spectral_tab.py's LANES (v4
# MIDI_EDITOR_LANES convention), so Preview and Spectral read identically.
# ---------------------------------------------------------------------------
LANE_NAMES = ("Hi-Hat", "Crash", "Snare", "Tom 1", "Tom 2", "Tom 3", "Ride", "Kick")
LANE_COLORS = ("#00e5ff", "#ff8c00", "#e63946", "#1a3a8f", "#2e8b57", "#7b2d8b",
               "#ffd700", "#ff69b4")
#: display hint: "circle" | "bar" | "kick" (kick can also render as a
#: full-width line when the Kick-as-line setting is on; see the tab's
#: ``_note_geom``).
LANE_SHAPES = ("circle", "circle", "bar", "bar", "bar", "bar", "circle", "kick")
LANE_COUNT = 8

#: v4's 1-8 place-at-playhead hotkey layout (key digit -> lane index).
HOTKEY_LANES = {"1": 7, "2": 2, "3": 0, "4": 1, "5": 6, "6": 3, "7": 4, "8": 5}

GHOST_VEL = 40      # vel < this renders hollow ("ghost")
ACCENT_VEL = 115    # vel >= this gets an accent ring
BUCKET_S = 0.25      # TimeIndex bucket width (seconds)

# ---------------------------------------------------------------------------
# GM lane <-> MIDI-note maps (v4 MIDI_EDITOR_LANES import convention, ported
# from parakit_v5/core/chart_import.py + midi_export.py CORE_LANE_MIDI).
# ---------------------------------------------------------------------------
_LANE_MIDI_IMPORT: List[List[int]] = [
    [42, 44, 46, 26, 21, 22, 23],   # 0 Hi-Hat (incl. electronic-kit hat notes)
    [49, 55, 57],                   # 1 Crash
    [37, 38, 40],                   # 2 Snare
    [48, 50],                       # 3 Tom 1
    [45, 47],                       # 4 Tom 2
    [41, 43],                       # 5 Tom 3
    [51, 53, 59],                   # 6 Ride
    [35, 36],                       # 7 Kick
]
#: GM percussion note -> editor lane (file-import convention).
MIDI_TO_LANE: Dict[int, int] = {n: i for i, lst in enumerate(_LANE_MIDI_IMPORT)
                                for n in lst}
#: lane index -> the canonical GM note EXPORT writes (every one re-imports to
#: its own lane -- byte-lossless round trip).
CORE_LANE_MIDI: Tuple[int, ...] = (42, 49, 38, 48, 45, 41, 51, 36)

#: Paradiddle drum class -> editor lane (DEFAULT_INSTRUMENTS + lane order).
CLASS_TO_LANE: Dict[str, int] = {
    "BP_HiHat_C": 0,
    "BP_Crash15_C": 1, "BP_Crash17_C": 1,
    "BP_Snare_C": 2,
    "BP_Tom1_C": 3,
    "BP_Tom2_C": 4,
    "BP_FloorTom_C": 5,
    "BP_Ride17_C": 6, "BP_Ride20_C": 6,
    "BP_Kick_C": 7,
}

# ---------------------------------------------------------------------------
# BPM sanity clamp -- an absurd/corrupt tempo turns the per-beat grid into a
# per-frame near-infinite loop (v5 gate finding; import_chart / receive()
# both clamp identically).
# ---------------------------------------------------------------------------
def safe_bpm(bpm) -> float:
    try:
        b = float(bpm)
    except (TypeError, ValueError):
        return 120.0
    if not math.isfinite(b):
        return 120.0
    return min(1000.0, max(10.0, b))


# ---------------------------------------------------------------------------
# EdNote -- the editor-local note. `id` is stable across snapshot/undo so
# selection survives undo/redo (id-based selection, not index-based).
# ---------------------------------------------------------------------------
_id_seq = itertools.count(1)


def _next_id() -> int:
    return next(_id_seq)


@dataclass
class EdNote:
    time: float
    lane: int
    vel: int = 100
    flag: str = ""
    conf: Optional[float] = None
    id: int = field(default_factory=_next_id)


def _copy_notes(notes: Iterable[EdNote]) -> List[EdNote]:
    # explicit id= preserves identity across snapshot/restore (selection-safe)
    return [EdNote(time=n.time, lane=n.lane, vel=n.vel, flag=n.flag,
                    conf=n.conf, id=n.id) for n in notes]


def chart_duration(notes: List[EdNote]) -> float:
    """Last note time (seconds); 0 for an empty chart."""
    return max((n.time for n in notes), default=0.0)


# ---------------------------------------------------------------------------
# Demo chart generator -- deterministic (seeded), verbatim port of the v5
# _generateDemoChart bar-pattern algorithm (verse/chorus/blast/groove
# sections + descending tom fills). Same seed/algorithm as v5 -> equivalent
# note counts per density tier.
# ---------------------------------------------------------------------------
_DENSITY_BARS = {"light": 56, "dense": 200, "insane": 460}
DEMO_BPM = 184
_DEMO_SEED = 0x9A7A11
_HH, _CR, _SN, _T1, _T2, _T3, _RD, _KK = 0, 1, 2, 3, 4, 5, 6, 7


def generate_demo_chart(density: str = "dense") -> List[EdNote]:
    bars = _DENSITY_BARS.get(density, _DENSITY_BARS["dense"])
    rng = random.Random(_DEMO_SEED)
    notes: List[EdNote] = []
    beat = 60.0 / DEMO_BPM
    bar = 4.0 * beat
    s16 = beat / 4.0

    def push(t: float, lane: int, vel: int) -> None:
        notes.append(EdNote(time=t, lane=lane, vel=vel))

    for b in range(bars):
        t0 = b * bar
        section = (b // 16) % 4  # 0 verse, 1 chorus, 2 blast, 3 groove
        is_fill = (b % 4 == 3)

        if is_fill and section != 2:
            for i in range(12):
                push(t0 + i * s16, _SN,
                     112 if i % 4 == 0 else (34 if rng.random() < 0.3 else 88))
            fill_lanes = [_T1, _T2, _T3, _T3]
            for i in range(12, 16):
                push(t0 + i * s16, fill_lanes[i - 12], 105)
            push(t0, _KK, 118)
            push(t0 + 2 * beat, _KK, 110)
            continue

        if section == 0:  # verse -- 16th hats, back-beat snare, syncopated kick
            for i in range(16):
                push(t0 + i * s16, _HH, 96 if i % 4 == 0 else 64)
            push(t0 + beat, _SN, 102)
            push(t0 + 3 * beat, _SN, 102)
            if rng.random() < 0.5:
                push(t0 + 2.5 * beat, _SN, 30)  # ghost
            push(t0, _KK, 116)
            push(t0 + 0.75 * beat, _KK, 100)
            push(t0 + 2 * beat, _KK, 112)
            if rng.random() < 0.6:
                push(t0 + 3.5 * beat, _KK, 98)
        elif section == 1:  # chorus -- crash on 1, ride 8ths, double-kick 16ths
            push(t0, _CR, 120)
            for i in range(8):
                push(t0 + i * beat / 2, _RD, 98 if i % 2 == 0 else 72)
            push(t0 + beat, _SN, 108)
            push(t0 + 3 * beat, _SN, 108)
            for i in range(16):
                push(t0 + i * s16, _KK, 112 if i % 4 == 0 else 92)
        elif section == 2:  # blast -- alternating 16th snare/kick, hat 8ths
            for i in range(16):
                push(t0 + i * s16, _SN if i % 2 == 0 else _KK,
                     118 if i % 8 == 0 else 96)
            for i in range(8):
                push(t0 + i * beat / 2, _HH, 60)
            if b % 2 == 0:
                push(t0, _CR, 122)
        else:  # groove -- half-time, tom accents
            for i in range(8):
                push(t0 + i * beat / 2, _HH, 92 if i % 2 == 0 else 58)
            push(t0 + 2 * beat, _SN, 110)
            push(t0, _KK, 118)
            push(t0 + 1.25 * beat, _KK, 96)
            push(t0 + 2.75 * beat, _KK, 96)
            if rng.random() < 0.4:
                push(t0 + 3.5 * beat, _T2, 86)
            if rng.random() < 0.25:
                push(t0 + 3.75 * beat, _T3, 90)

    # sprinkle review flags on ~0.4% of notes (deterministic)
    for n in notes:
        if rng.random() < 0.004:
            n.flag = "dt_review"

    notes.sort(key=operator.attrgetter("time", "lane"))
    return notes


# ---------------------------------------------------------------------------
# TimeIndex -- bucketed [t0,t1] query. Renders/hit-tests touch O(buckets in
# the visible window), not O(N); this is what keeps a 9.7k-note "Insane"
# chart exactly as fast to draw as a 1.2k-note one -- only the notes
# currently between the hit-line and the fall-window edge are ever visited.
# ---------------------------------------------------------------------------
class TimeIndex:
    def __init__(self, bucket_s: float = BUCKET_S) -> None:
        self._bucket_s = bucket_s
        self._buckets: Dict[int, List[EdNote]] = {}

    def rebuild(self, notes: Iterable[EdNote]) -> None:
        self._buckets.clear()
        for n in notes:
            b = math.floor(n.time / self._bucket_s)
            self._buckets.setdefault(b, []).append(n)

    def iter_window(self, t0: float, t1: float) -> Iterator[EdNote]:
        """Yield notes with time in [t0, t1] (inclusive), bucket order."""
        if t1 < t0:
            return
        b0 = math.floor(max(0.0, t0) / self._bucket_s)
        b1 = math.floor(max(0.0, t1) / self._bucket_s)
        for b in range(int(b0), int(b1) + 1):
            arr = self._buckets.get(b)
            if not arr:
                continue
            for n in arr:
                if t0 <= n.time <= t1:
                    yield n

    def note_at(self, t: float, lane: int, tol_s: float) -> Optional[EdNote]:
        best: Optional[EdNote] = None
        best_d = tol_s
        for n in self.iter_window(t - tol_s, t + tol_s):
            if n.lane != lane:
                continue
            d = abs(n.time - t)
            if d <= best_d:
                best_d = d
                best = n
        return best


# ---------------------------------------------------------------------------
# Selection -- id-based (immune to delete/sort reindexing).
# ---------------------------------------------------------------------------
@dataclass
class Selection:
    ids: Set[int] = field(default_factory=set)

    def clear(self) -> None:
        self.ids.clear()

    def has(self, note_id: int) -> bool:
        return note_id in self.ids

    def toggle(self, note_id: int) -> None:
        self.ids.discard(note_id) if note_id in self.ids else self.ids.add(note_id)

    def add(self, note_id: int) -> None:
        self.ids.add(note_id)

    @property
    def size(self) -> int:
        return len(self.ids)

    def prune(self, model: "ChartModel") -> None:
        live = {n.id for n in model.notes}
        self.ids = {i for i in self.ids if i in live}


# ---------------------------------------------------------------------------
# ChartModel -- the editable document: notes + bucketed index + snapshot undo.
# ---------------------------------------------------------------------------
UNDO_DEPTH = 100


class ChartModel:
    def __init__(self, bpm: float = 120.0) -> None:
        self.notes: List[EdNote] = []
        self.bpm = float(bpm) if bpm > 0 else 120.0
        self.duration = 0.0
        self._index = TimeIndex()
        self._index_dirty = True
        self._undo: List[List[EdNote]] = []
        self._redo: List[List[EdNote]] = []

    @classmethod
    def from_notes(cls, notes: List[EdNote], bpm: float) -> "ChartModel":
        m = cls(bpm)
        m.notes = list(notes)
        m.sort_notes()
        m.recalc_duration()
        return m

    # ----- index maintenance -------------------------------------------
    def touch(self) -> None:
        self._index_dirty = True

    def get_index(self) -> TimeIndex:
        if self._index_dirty:
            self._index.rebuild(self.notes)
            self._index_dirty = False
        return self._index

    def sort_notes(self) -> None:
        self.notes.sort(key=operator.attrgetter("time"))
        self.touch()

    def recalc_duration(self) -> None:
        self.duration = max(self.duration, chart_duration(self.notes) + 2.0)

    # ----- undo / redo (whole-chart snapshot; correct + simple at <=20k) --
    def push_undo(self) -> None:
        self._undo.append(_copy_notes(self.notes))
        if len(self._undo) > UNDO_DEPTH:
            self._undo.pop(0)
        self._redo.clear()

    def discard_last_undo(self) -> None:
        if self._undo:
            self._undo.pop()

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(_copy_notes(self.notes))
        self.notes = _copy_notes(self._undo.pop())
        self.touch()
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(_copy_notes(self.notes))
        self.notes = _copy_notes(self._redo.pop())
        self.touch()
        return True

    # ----- edit ops (caller decides when to push_undo) ------------------
    def add_note(self, time: float, lane: int, vel: int = 100) -> EdNote:
        n = EdNote(time=max(0.0, time), lane=lane, vel=vel)
        self.notes.append(n)
        self.sort_notes()
        self.duration = max(self.duration, n.time + 0.5)
        return n

    def by_id(self, note_id: int) -> Optional[EdNote]:
        for n in self.notes:
            if n.id == note_id:
                return n
        return None

    def delete_by_ids(self, ids: Set[int]) -> int:
        before = len(self.notes)
        self.notes = [n for n in self.notes if n.id not in ids]
        self.touch()
        return before - len(self.notes)

    def set_positions(self, mapping: Dict[int, Tuple[float, int]]) -> None:
        """Set absolute (time, lane) for ids -- drift-free drag-move (caller
        maps from drag-start originals each frame)."""
        for n in self.notes:
            pos = mapping.get(n.id)
            if pos is None:
                continue
            t, lane = pos
            n.time = max(0.0, t)
            n.lane = min(LANE_COUNT - 1, max(0, lane))
        self.sort_notes()


# ---------------------------------------------------------------------------
# snap_time -- grid snap, with an optional nearest-onset snap tried FIRST
# (the Preview drag path; drives the yellow snap-guide line).
# ---------------------------------------------------------------------------
@dataclass
class SnapResult:
    t: float
    snapped: bool
    #: onset time a NOTE-snap locked onto (drives the yellow guide); None for
    #: grid-snap / no-snap.
    guide_t: Optional[float] = None


def snap_time(model: ChartModel, t: float, snap_on: bool, subs_per_beat: int,
              note_snap_tol_s: float = 0.0,
              skip_ids: Optional[Set[int]] = None) -> SnapResult:
    t = max(0.0, t)
    if not snap_on or model.bpm <= 0:
        return SnapResult(t, False)
    if note_snap_tol_s > 0.0:
        best_t: Optional[float] = None
        best_d = note_snap_tol_s
        for n in model.get_index().iter_window(t - note_snap_tol_s,
                                                t + note_snap_tol_s):
            if skip_ids and n.id in skip_ids:
                continue
            d = abs(n.time - t)
            if d <= best_d:
                best_d = d
                best_t = n.time
        if best_t is not None:
            return SnapResult(best_t, True, guide_t=best_t)
    sub = 60.0 / model.bpm / max(1, subs_per_beat)
    return SnapResult(max(0.0, round(t / sub) * sub), True)


# ---------------------------------------------------------------------------
# parakit-chart-v1 JSON codec (Preview tab's native round-trip format; ALSO
# the MIDI-Editor -> Preview handoff payload shape).
#   { "format": "parakit-chart-v1", "bpm": <number>, "notes": [[t, lane, vel?], ...] }
# ---------------------------------------------------------------------------
FORMAT_TAG = "parakit-chart-v1"
_DEFAULT_VEL = 100


def _clamp_vel(vel) -> int:
    try:
        v = int(vel)
    except (TypeError, ValueError):
        v = _DEFAULT_VEL
    return max(1, min(127, v))


def parse_parakit_chart_v1_text(text: str) -> Tuple[List[EdNote], float]:
    """Parse a parakit-chart-v1 JSON string into (notes, bpm). Malformed rows
    are skipped defensively (never raise on bad data); an explicit non-
    matching "format" field raises ValueError."""
    obj = json.loads(text)
    if not isinstance(obj, dict):
        raise ValueError(
            "Import failed: unsupported format (%s expects a JSON object)"
            % FORMAT_TAG)
    fmt = obj.get("format")
    if fmt is not None and fmt != FORMAT_TAG:
        raise ValueError("Import failed: unsupported format (%r != %r)"
                          % (fmt, FORMAT_TAG))
    try:
        bpm = float(obj.get("bpm", 120.0)) or 120.0
    except (TypeError, ValueError):
        bpm = 120.0

    notes: List[EdNote] = []
    for row in obj.get("notes", []) or []:
        if not isinstance(row, (list, tuple)) or len(row) < 2 or len(row) > 3:
            continue
        try:
            t = float(row[0])
        except (TypeError, ValueError):
            continue
        if not math.isfinite(t):
            continue
        try:
            lane = int(row[1])
        except (TypeError, ValueError):
            continue
        if lane < 0 or lane >= LANE_COUNT:
            continue
        vel = _DEFAULT_VEL if len(row) < 3 or row[2] is None else _clamp_vel(row[2])
        notes.append(EdNote(time=max(0.0, t), lane=lane, vel=vel))

    notes.sort(key=operator.attrgetter("time"))
    return notes, safe_bpm(bpm)


def load_parakit_chart_v1(path: str) -> Tuple[List[EdNote], float]:
    with open(path, encoding="utf-8") as f:
        text = f.read()
    return parse_parakit_chart_v1_text(text)


def dump_parakit_chart_v1(notes: Iterable[EdNote], bpm: float) -> str:
    out_notes = []
    for n in notes:
        t = getattr(n, "time")
        lane = getattr(n, "lane")
        vel = getattr(n, "vel", _DEFAULT_VEL)
        t_rounded = round(float(t), 6)
        lane_i = int(lane)
        vel_i = _clamp_vel(vel)
        row = [t_rounded, lane_i] if vel_i == 100 else [t_rounded, lane_i, vel_i]
        out_notes.append(row)
    bpm_f = float(bpm)
    bpm_out = int(bpm_f) if bpm_f.is_integer() else bpm_f
    payload = {"format": FORMAT_TAG, "bpm": bpm_out, "notes": out_notes}
    return json.dumps(payload, separators=(",", ":"))


# ---------------------------------------------------------------------------
# .rlrr reader (Paradiddle) -- event "name" -> class (via the file's OWN
# instruments block, robust to any pack) -> lane via CLASS_TO_LANE.
# ---------------------------------------------------------------------------
def load_rlrr_as_notes(path: str) -> Tuple[List[EdNote], float]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    name_to_class: Dict[str, str] = {}
    for inst in data.get("instruments", []) or []:
        nm, cl = inst.get("name"), inst.get("class")
        if nm and cl:
            name_to_class[nm] = cl

    notes: List[EdNote] = []
    for ev in data.get("events", []) or []:
        name = ev.get("name")
        cls = name_to_class.get(name, name)
        lane = CLASS_TO_LANE.get(cls)
        if lane is None:
            continue
        try:
            t = float(ev.get("time"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(t):
            continue
        try:
            vel = int(ev.get("vel", 100))
        except (TypeError, ValueError):
            vel = 100
        notes.append(EdNote(time=max(0.0, t), lane=lane, vel=max(1, min(127, vel))))
    notes.sort(key=operator.attrgetter("time"))

    bpm = 120.0
    bpm_events = data.get("bpmEvents") or []
    if bpm_events:
        try:
            bpm = float(bpm_events[0].get("bpm", 120.0)) or 120.0
        except (TypeError, ValueError):
            pass
    return notes, safe_bpm(bpm)


# ---------------------------------------------------------------------------
# .mid reader/writer -- mido used when importable (more robust against
# real-world files); otherwise a hand-rolled minimal SMF0/1 reader + SMF0
# writer so Import/Export work with ZERO third-party deps.
# ---------------------------------------------------------------------------
def _read_vlq(data: bytes, i: int) -> Tuple[int, int]:
    value = 0
    while True:
        b = data[i]
        i += 1
        value = (value << 7) | (b & 0x7F)
        if not (b & 0x80):
            break
    return value, i


def _write_vlq(value: int) -> bytes:
    value = max(0, int(value))
    buf = [value & 0x7F]
    value >>= 7
    while value:
        buf.append((value & 0x7F) | 0x80)
        value >>= 7
    return bytes(reversed(buf))


def _read_midi_minimal(path: str) -> Tuple[List[Tuple[float, int, int]], float]:
    """Hand-rolled SMF reader: returns [(time_sec, midi_note, vel), ...] onset
    rows (note-offs / vel==0 note-ons are consumed but not returned) + the
    first tempo as BPM. No sysex/meta payload other than Set Tempo is
    interpreted -- everything else is skipped by its declared length, so an
    unsupported meta/sysex event never desyncs the byte stream."""
    with open(path, "rb") as f:
        data = f.read()
    if data[0:4] != b"MThd":
        raise ValueError("not a Standard MIDI File (missing MThd)")
    header_len = struct.unpack(">I", data[4:8])[0]
    fmt, ntrks, division = struct.unpack(">HHH", data[8:8 + 6])
    if division & 0x8000:
        raise ValueError("SMPTE-timecode MIDI files are not supported")
    ticks_per_beat = division or 480
    pos = 8 + header_len

    tempo_events: List[Tuple[int, int]] = []   # (abs_tick, usec_per_beat)
    note_events: List[Tuple[int, int, int]] = []  # (abs_tick, note, vel)

    for _ in range(ntrks):
        if data[pos:pos + 4] != b"MTrk":
            raise ValueError("corrupt MIDI file (missing MTrk)")
        pos += 4
        track_len = struct.unpack(">I", data[pos:pos + 4])[0]
        pos += 4
        track_end = pos + track_len
        i = pos
        abs_tick = 0
        running_status: Optional[int] = None
        while i < track_end:
            delta, i = _read_vlq(data, i)
            abs_tick += delta
            status = data[i]
            if status < 0x80:
                if running_status is None:
                    raise ValueError("corrupt MIDI file (running status "
                                      "with no prior status byte)")
                status = running_status
            else:
                i += 1
                if status < 0xF0:
                    running_status = status
            if status == 0xFF:
                meta_type = data[i]
                i += 1
                length, i = _read_vlq(data, i)
                meta_data = data[i:i + length]
                i += length
                if meta_type == 0x51 and length == 3:
                    usec = (meta_data[0] << 16) | (meta_data[1] << 8) | meta_data[2]
                    if usec > 0:
                        tempo_events.append((abs_tick, usec))
                if meta_type == 0x2F:
                    break
            elif status in (0xF0, 0xF7):
                length, i = _read_vlq(data, i)
                i += length
            else:
                hi = status & 0xF0
                if hi in (0xC0, 0xD0):
                    i += 1   # one data byte
                else:
                    d1, d2 = data[i], data[i + 1]
                    i += 2
                    if hi == 0x90 and d2 > 0:   # note-on, real velocity
                        note_events.append((abs_tick, d1, d2))
                    # note-off (0x80) / note-on-vel-0 -- consumed, not kept
        pos = track_end

    if not tempo_events:
        tempo_events = [(0, 500000)]
    tempo_events.sort(key=lambda e: e[0])
    if tempo_events[0][0] != 0:
        tempo_events.insert(0, (0, tempo_events[0][1]))

    def tick_to_sec(tick: int) -> float:
        sec = 0.0
        prev_tick = 0
        prev_usec = tempo_events[0][1]
        for t_tick, usec in tempo_events[1:]:
            if tick <= t_tick:
                break
            sec += (t_tick - prev_tick) * prev_usec / 1_000_000.0 / ticks_per_beat
            prev_tick, prev_usec = t_tick, usec
        sec += (tick - prev_tick) * prev_usec / 1_000_000.0 / ticks_per_beat
        return sec

    bpm = 60_000_000.0 / tempo_events[0][1]
    rows = sorted((tick_to_sec(t), note, vel) for t, note, vel in note_events)
    return rows, safe_bpm(bpm)


def load_midi_as_notes(path: str) -> Tuple[List[EdNote], float]:
    """Read a .mid into (EdNotes, bpm); notes outside the 8 drum lanes are
    skipped. Tries ``mido`` first (more robust vs. real-world files with
    exotic meta/sysex layouts), falls back to the hand-rolled reader."""
    rows: Optional[List[Tuple[float, int, int]]] = None
    bpm = 120.0
    try:
        import mido  # lazy, optional
        mid = mido.MidiFile(path)
        ticks_per_beat = mid.ticks_per_beat or 480
        for track in mid.tracks:
            abs_tick = 0
            tempo_us = 500000
            got_tempo = False
            r: List[Tuple[float, int, int]] = []
            cur_sec = 0.0
            for msg in track:
                abs_tick += msg.time
                cur_sec += mido.tick2second(msg.time, ticks_per_beat, tempo_us)
                if msg.is_meta and msg.type == "set_tempo":
                    tempo_us = msg.tempo
                    if not got_tempo:
                        bpm = 60_000_000.0 / tempo_us
                        got_tempo = True
                elif msg.type == "note_on" and msg.velocity > 0:
                    r.append((cur_sec, msg.note, msg.velocity))
            if r:
                rows = (rows or []) + r
        if rows is None:
            rows = []
        rows.sort(key=lambda r: r[0])
    except Exception:
        rows, bpm = _read_midi_minimal(path)

    notes: List[EdNote] = []
    for t, note, vel in rows:
        lane = MIDI_TO_LANE.get(note)
        if lane is None:
            continue
        notes.append(EdNote(time=max(0.0, t), lane=lane,
                             vel=max(1, min(127, int(vel)))))
    notes.sort(key=operator.attrgetter("time"))
    return notes, safe_bpm(bpm)


def load_chart_as_notes(path: str) -> Tuple[List[EdNote], float]:
    """Dispatch on extension: .json -> parakit-chart-v1, .rlrr -> Paradiddle,
    else -> .mid/.midi."""
    low = path.lower()
    if low.endswith(".json"):
        return load_parakit_chart_v1(path)
    if low.endswith(".rlrr"):
        return load_rlrr_as_notes(path)
    return load_midi_as_notes(path)


def _as_rows(notes: Iterable) -> List[Tuple[float, int, int]]:
    rows: List[Tuple[float, int, int]] = []
    for n in notes:
        t = getattr(n, "time", None)
        if t is not None:
            lane = getattr(n, "lane")
            vel = getattr(n, "vel", 100)
        else:
            t, lane, *rest = n
            vel = rest[0] if rest else 100
        lane = int(lane)
        if 0 <= lane < len(CORE_LANE_MIDI):
            rows.append((max(0.0, float(t)), lane, max(1, min(127, int(vel)))))
    rows.sort(key=lambda r: r[0])
    return rows


def _write_midi_minimal(notes: Iterable, bpm: float, path: str) -> int:
    """Hand-rolled SMF format-0 writer -- no third-party deps. Matches
    write_smf0's semantics exactly (480 tpb, channel 9, 1/8-beat note length,
    canonical per-lane GM note)."""
    bpm_f = float(bpm) if bpm and bpm > 0 else 120.0
    tpb = 480
    channel = 9
    note_len_ticks = tpb >> 3
    sec_per_tick = 60.0 / bpm_f / tpb
    rows = _as_rows(notes)

    events: List[Tuple[int, int, int, int]] = []   # (tick, kind, note, vel)
    for t, lane, vel in rows:
        tick = int(round(t / sec_per_tick))
        note = CORE_LANE_MIDI[lane]
        events.append((tick, 1, note, vel))
        events.append((tick + note_len_ticks, 0, note, 0))
    events.sort(key=lambda e: (e[0], e[1]))

    track = bytearray()
    usec = max(1, min(16777215, int(round(60_000_000.0 / bpm_f))))
    track += _write_vlq(0) + bytes([0xFF, 0x51, 0x03]) + usec.to_bytes(3, "big")
    prev_tick = 0
    for tick, kind, note, vel in events:
        delta = max(0, tick - prev_tick)
        prev_tick = tick
        track += _write_vlq(delta)
        if kind == 1:
            track += bytes([0x90 | channel, note & 0x7F, vel & 0x7F])
        else:
            track += bytes([0x80 | channel, note & 0x7F, 0])
    track += _write_vlq(0) + bytes([0xFF, 0x2F, 0x00])

    header = b"MThd" + struct.pack(">IHHH", 6, 0, 1, tpb)
    chunk = b"MTrk" + struct.pack(">I", len(track)) + bytes(track)
    with open(path, "wb") as f:
        f.write(header + chunk)
    return len(rows)


def write_smf0(notes: Iterable, bpm: float, path: str) -> int:
    """Write notes+bpm as an SMF format-0 .mid. Tries mido first, falls back
    to the hand-rolled writer on any failure (including mido absent)."""
    try:
        import mido
        bpm_f = float(bpm) if bpm and bpm > 0 else 120.0
        rows = _as_rows(notes)
        tpb = 480
        sec_per_tick = 60.0 / bpm_f / tpb
        note_len_ticks = tpb >> 3
        events: List[Tuple[int, int, int, int]] = []
        for t, lane, vel in rows:
            tick = int(round(t / sec_per_tick))
            note = CORE_LANE_MIDI[lane]
            events.append((tick, 1, note, vel))
            events.append((tick + note_len_ticks, 0, note, 0))
        events.sort(key=lambda e: (e[0], e[1]))
        mid = mido.MidiFile(type=0, ticks_per_beat=tpb)
        track = mido.MidiTrack()
        mid.tracks.append(track)
        track.append(mido.MetaMessage("set_tempo",
                                       tempo=mido.bpm2tempo(bpm_f), time=0))
        prev_tick = 0
        for tick, kind, note, vel in events:
            delta = max(0, tick - prev_tick)
            prev_tick = tick
            if kind == 1:
                track.append(mido.Message("note_on", note=note, velocity=vel,
                                           channel=9, time=delta))
            else:
                track.append(mido.Message("note_off", note=note, velocity=0,
                                           channel=9, time=delta))
        track.append(mido.MetaMessage("end_of_track", time=0))
        mid.save(path)
        return len(rows)
    except Exception:
        return _write_midi_minimal(notes, bpm, path)


# ---------------------------------------------------------------------------
# Self-test -- pure engine, no Tk/display needed.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import os
    import sys
    import tempfile

    ok = True

    # 1) demo chart generator -- deterministic + non-trivial size per tier
    d_light = generate_demo_chart("light")
    d_dense = generate_demo_chart("dense")
    d_insane = generate_demo_chart("insane")
    g1 = len(d_light) < len(d_dense) < len(d_insane) and len(d_insane) > 5000
    # determinism check (separate calls must reproduce identical note count)
    g1b = len(generate_demo_chart("dense")) == len(d_dense)
    print("SELFTEST demo chart: light=%d dense=%d insane=%d monotonic=%s "
          "deterministic=%s -> %s"
          % (len(d_light), len(d_dense), len(d_insane), g1, g1b,
             "OK" if (g1 and g1b) else "FAIL"))
    ok = ok and g1 and g1b

    # 2) ChartModel + TimeIndex windowing + undo/redo
    m = ChartModel.from_notes(d_dense, DEMO_BPM)
    idx = m.get_index()
    window_notes = list(idx.iter_window(10.0, 12.0))
    g2 = all(10.0 <= n.time <= 12.0 for n in window_notes) and len(window_notes) > 0
    before = len(m.notes)
    m.push_undo()
    n = m.add_note(1.0, 7, 100)
    g3 = len(m.notes) == before + 1
    ok_undo = m.undo()
    g4 = ok_undo and len(m.notes) == before
    ok_redo = m.redo()
    g5 = ok_redo and len(m.notes) == before + 1
    print("SELFTEST model: window=%d in-range=%s add=%s undo=%s redo=%s -> %s"
          % (len(window_notes), g2, g3, g4, g5,
             "OK" if (g2 and g3 and g4 and g5) else "FAIL"))
    ok = ok and g2 and g3 and g4 and g5

    # 3) snap_time -- grid snap + note snap w/ guide
    m2 = ChartModel(120.0)
    m2.add_note(1.001, 2, 100)
    res_grid = snap_time(m2, 0.501, True, 4)   # 1/16 @120bpm = 0.125s steps
    g6 = abs(res_grid.t - 0.5) < 1e-9 and res_grid.guide_t is None
    res_note = snap_time(m2, 0.98, True, 4, note_snap_tol_s=0.05)
    g7 = res_note.guide_t is not None and abs(res_note.t - 1.001) < 1e-9
    print("SELFTEST snap_time: grid=%.4f(%s) note=%.4f guide=%s -> %s"
          % (res_grid.t, g6, res_note.t, g7, "OK" if (g6 and g7) else "FAIL"))
    ok = ok and g6 and g7

    # 4) parakit-chart-v1 JSON round trip
    src = [EdNote(time=0.0, lane=7, vel=100), EdNote(time=1.234567, lane=0, vel=64),
           EdNote(time=2.5, lane=2, vel=127)]
    dumped = dump_parakit_chart_v1(src, 172.5)
    back, bpm_back = parse_parakit_chart_v1_text(dumped)
    g8 = (len(back) == len(src) and abs(bpm_back - 172.5) < 1e-9
          and all(abs(a.time - b.time) < 1e-6 and a.lane == b.lane and a.vel == b.vel
                  for a, b in zip(sorted(src, key=lambda x: x.time), back)))
    print("SELFTEST chart_json round-trip: %s" % ("OK" if g8 else "FAIL"))
    ok = ok and g8

    # 5) MIDI round trip via the MINIMAL writer/reader (force no-mido path by
    # calling the private functions directly -- proves the zero-dependency
    # fallback works end to end, independent of whether mido is installed).
    tmp = os.path.join(tempfile.gettempdir(), "parakit_preview_engine_selftest.mid")
    src_midi = [EdNote(time=0.25 * i, lane=i, vel=30 + 10 * i) for i in range(8)]
    count = _write_midi_minimal(src_midi, 184.0, tmp)
    rows_back, bpm_midi = _read_midi_minimal(tmp)
    lane_back = [MIDI_TO_LANE.get(note) for _t, note, _v in rows_back]
    g9 = (count == 8 and len(rows_back) == 8
          and lane_back == list(range(8))
          and abs(bpm_midi - 184.0) < 0.01)
    try:
        os.remove(tmp)
    except OSError:
        pass
    print("SELFTEST midi minimal round-trip: count=%d lanes=%r bpm=%.2f -> %s"
          % (count, lane_back, bpm_midi, "OK" if g9 else "FAIL"))
    ok = ok and g9

    # 6) MIDI round trip via write_smf0/load_midi_as_notes (mido path if
    # installed, else falls through to the same minimal path as #5).
    tmp2 = os.path.join(tempfile.gettempdir(),
                         "parakit_preview_engine_selftest2.mid")
    count2 = write_smf0(src_midi, 184.0, tmp2)
    notes_back, bpm2 = load_midi_as_notes(tmp2)
    g10 = (count2 == 8 and len(notes_back) == 8
           and [n.lane for n in notes_back] == list(range(8))
           and abs(bpm2 - 184.0) < 0.01)
    try:
        os.remove(tmp2)
    except OSError:
        pass
    print("SELFTEST midi write_smf0/load_midi_as_notes round-trip: "
          "count=%d lanes=%r bpm=%.2f -> %s"
          % (count2, [n.lane for n in notes_back], bpm2, "OK" if g10 else "FAIL"))
    ok = ok and g10

    # 7) rlrr reader on a minimal synthetic fixture
    rlrr_tmp = os.path.join(tempfile.gettempdir(),
                             "parakit_preview_engine_selftest.rlrr")
    rlrr_payload = {
        "instruments": [{"name": "kick1", "class": "BP_Kick_C"},
                         {"name": "snare1", "class": "BP_Snare_C"}],
        "bpmEvents": [{"time": 0.0, "bpm": 140.0}],
        "events": [{"name": "kick1", "time": 0.0, "vel": 100},
                   {"name": "snare1", "time": 0.5, "vel": 90},
                   {"name": "unknown_thing", "time": 1.0, "vel": 50}],
    }
    with open(rlrr_tmp, "w", encoding="utf-8") as f:
        json.dump(rlrr_payload, f)
    rlrr_notes, rlrr_bpm = load_rlrr_as_notes(rlrr_tmp)
    g11 = (len(rlrr_notes) == 2 and rlrr_notes[0].lane == 7
           and rlrr_notes[1].lane == 2 and abs(rlrr_bpm - 140.0) < 1e-9)
    try:
        os.remove(rlrr_tmp)
    except OSError:
        pass
    print("SELFTEST rlrr reader: notes=%d bpm=%.1f -> %s"
          % (len(rlrr_notes), rlrr_bpm, "OK" if g11 else "FAIL"))
    ok = ok and g11

    # 8) safe_bpm guard
    g12 = (safe_bpm(0) == 10.0 and safe_bpm(float("nan")) == 120.0
           and safe_bpm(float("inf")) == 120.0 and safe_bpm(50000) == 1000.0
           and safe_bpm(184) == 184.0 and safe_bpm("garbage") == 120.0)
    print("SELFTEST safe_bpm guard: %s" % ("OK" if g12 else "FAIL"))
    ok = ok and g12

    print("SELFTEST %s" % ("OK" if ok else "FAIL"))
    sys.exit(0 if ok else 1)
