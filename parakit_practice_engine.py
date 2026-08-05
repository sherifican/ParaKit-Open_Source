"""parakit_practice_engine -- the Practice tab's pure game core (faithful v5 port).

A 1:1 Python port of `parakit_v5/ui/widgets/practice/game_core.py` (the
Kitsmith "Practice Mode v3" core -- itself a port of the shipped, owner-
signed-off web app prototypes/practice-web/parakit-practice.html, lines
897-1519). Every constant, formula, and edge-case below is quoted from the
v5 module; where JS semantics differ from Python (Math.round half-up,
Math.trunc, Math.imul, charCodeAt) the JS behaviour is reproduced exactly.

This copy exists as a standalone, Tk-free sidecar for `ParaKit v4.0.py` --
the source v5 module already had zero Tk/Qt/numpy dependencies (stdlib
only: json, math, operator, re, typing), so this is a byte-for-byte logic
port with only the module docstring/header changed. Public API names are
kept IDENTICAL to the v5 module on purpose -- other legs (Sol's Play/
Results screens, the Home screen) import these names directly:
`Session`, `resolve_routing`, `build_lane_notes`, `windows_for`,
`song_duration_from_lanes`, `parse_rlrr`, `decode_chart_bytes`, `song_key`,
`demo_chart`, `LANE_DEFS`, `STANDARD_ORDER`, `SHAPES`, `PALETTES`,
`BUILTIN_PRESETS`, `PRESET_FOLDS`, `apply_layout_snapshot`,
`snapshot_layout`, `default_layout`, `CLASS_ROUTES`, `UNKNOWN_ROUTE`,
`DEFAULT_KEYBINDS`.

Contents: timing/scoring constants (KS), hit-window modes, the Session judge
(nearest-note matching, miss sweep, auto-kick, seek semantics), grades, the
kit layout + routing model (LANE_DEFS / palettes / presets / folds / per-song
instrument overrides), the .rlrr chart parser (warn-don't-fail), the
rename-proof songKey hash, and the built-in demo groove.

Pure module -- no Tk, no numpy, no Qt. stdlib only (`mido` is NOT imported
anywhere in this module; `.rlrr`/`.json` charts are plain JSON text, so no
MIDI library is needed for chart parsing). The highway renderer, audio, and
input layers sit on top of this module in the Home/Play screens.
"""
from __future__ import annotations

import json
import math
import operator
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

#: named sort keys (repo convention: operator.*getter, not lambdas)
_BY_TIME_KEY = operator.itemgetter("time")
_BY_NOTE_TIME = operator.attrgetter("time")

# ---------------------------------------------------------------------------
# JS numeric semantics helpers
# ---------------------------------------------------------------------------


def js_round(x: float) -> int:
    """JS Math.round: half-up toward +Infinity (Python round() is banker's)."""
    return math.floor(x + 0.5)


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def clamp_int(v: float, lo: int, hi: int) -> int:
    """Port of clampInt: Math.max(a, Math.min(b, Math.round(v)))."""
    return int(max(lo, min(hi, js_round(v))))


# ---------------------------------------------------------------------------
# KS -- timing / scoring constants (HTML lines 906-919, verbatim)
# ---------------------------------------------------------------------------

PERFECT_S = 0.035
HIT_S = 0.075
MISS_PAST_S = 0.120
SCORE = {"perfect": 100, "early": 70, "late": 50, "miss": -10}
GRADES: List[Tuple[float, str, str]] = [
    (95, "S", "FORGED"),
    (85, "A", "TEMPERED"),
    (70, "B", "SHAPED"),
    (50, "C", "HEATING UP"),
    (0, "D", "BACK TO THE ANVIL"),
]
COMBO_MIN = 5
MILESTONE_EVERY = 10
GHOST_VEL = 40      # vel <  40 -> ghost styling
ACCENT_VEL = 115    # vel >= 115 -> accent styling
MAX_LANES = 10
SECTION_S = 10      # rolling practice-section bucket size (seconds)

# Window modes multiply ALL THREE windows (lines 921-930).
WINDOW_MODES = {
    "relaxed": {"label": "Relaxed", "mul": 1.5},
    "standard": {"label": "Standard", "mul": 1.0},
    "strict": {"label": "Strict", "mul": 0.6},
}


class Windows:
    """The three hit windows in SONG-time seconds (speed never scales them)."""

    __slots__ = ("perfect", "hit", "miss_past")

    def __init__(self, perfect: float, hit: float, miss_past: float) -> None:
        self.perfect = perfect
        self.hit = hit
        self.miss_past = miss_past


def windows_for(mode: str) -> Windows:
    m = WINDOW_MODES.get(mode, WINDOW_MODES["standard"])["mul"]
    return Windows(PERFECT_S * m, HIT_S * m, MISS_PAST_S * m)


def compute_grade(acc: float) -> Tuple[str, str]:
    """Grade on ACCURACY % (not score): first KS.GRADES row with acc >= thr."""
    for thr, letter, sub in GRADES:
        if acc >= thr:
            return letter, sub
    return "D", "BACK TO THE ANVIL"


def velocity_flash_scale(velocity: float) -> Tuple[float, float]:
    """[alpha_mul, size_mul] for the receptor/burst flash (line 1409)."""
    if velocity <= GHOST_VEL:
        return (0.5, 0.85)
    if velocity >= 100:
        return (1.4, 1.2)
    return (1.0, 1.0)


# ---------------------------------------------------------------------------
# Lane catalog + palettes (lines 935-947, 2860; kit-studio spec section 1)
# ---------------------------------------------------------------------------

LANE_DEFS: Dict[str, Dict[str, str]] = {
    # v4.9.1 (owner) — DEFAULT shapes now match the Preview tab exactly
    # (cymbals = circle, snare + toms = bar, kick handled by the kick-line):
    # the old lozenge/roundrect/ring mix looked mismatched, and roundrect toms
    # read as too-thick bars. Kit Studio can still customize any lane.
    "hh": {"label": "Hi-Hat", "shape": "circle", "family": "hihat", "voice": "hihat"},
    "cr": {"label": "Crash", "shape": "circle", "family": "cymbal", "voice": "crash"},
    "sn": {"label": "Snare", "shape": "bar", "family": "snare", "voice": "snare"},
    "t1": {"label": "Tom 1", "shape": "bar", "family": "tom", "voice": "tom1"},
    "t2": {"label": "Tom 2", "shape": "bar", "family": "tom", "voice": "tom2"},
    "t3": {"label": "Tom 3", "shape": "bar", "family": "tom", "voice": "tom3"},
    "rd": {"label": "Ride", "shape": "circle", "family": "cymbal", "voice": "ride"},
    "kk": {"label": "Kick", "shape": "bar", "family": "kick", "voice": "kick"},
    "ax1": {"label": "Aux A", "shape": "diamond", "family": "aux", "voice": "neutral"},
    "ax2": {"label": "Aux B", "shape": "diamond", "family": "aux", "voice": "neutral"},
}
STANDARD_ORDER = ["hh", "cr", "sn", "t1", "t2", "t3", "rd", "kk"]

SHAPES = ["circle", "lozenge", "lozengeS", "roundrect", "bar", "diamond",
          "spike", "ring", "triangle"]

# The ParaKit lane palette -- hh, cr, sn, t1, t2, t3, rd, kk + 2 aux.
PALETTE = ["#00e5ff", "#ff8c00", "#e63946", "#1a3a8f", "#2e8b57",
           "#7b2d8b", "#ffd700", "#ff69b4", "#c4b5fd", "#5fd9e8"]

PALETTES = {
    "forge": PALETTE,
    "okabe": ["#E69F00", "#56B4E9", "#009E73", "#F0E442", "#0072B2",
              "#D55E00", "#CC79A7", "#FFFFFF", "#999999", "#7DD0B6"],
    "tritan": ["#E8A33D", "#D9543F", "#F3EFE6", "#8A8F98", "#C46A4A",
               "#F2C14E", "#A8442E", "#E6E1D5", "#6E6A60", "#D98E63"],
}

#: highway background the contrast check runs against
HIGHWAY_BG = "#17171B"


def lane_color(layout: dict, lane_id: str, palette_name: str = "forge") -> str:
    """ln.customColor if set, else pal[ln.color % len(pal)] (line 2865)."""
    ln = layout["lanes"][lane_id]
    if ln.get("customColor"):
        return ln["customColor"]
    pal = PALETTES.get(palette_name) or PALETTES["forge"]
    return pal[ln["color"] % len(pal)]


def _rel_luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))

    def f(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast_ratio(hex_fg: str, hex_bg: str = HIGHWAY_BG) -> float:
    """WCAG relative-luminance contrast ratio (UI warns when < 3)."""
    l1 = _rel_luminance(hex_fg)
    l2 = _rel_luminance(hex_bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


# ---------------------------------------------------------------------------
# Instrument-class routing (lines 948-976; prefix match in listed order)
# ---------------------------------------------------------------------------

CLASS_ROUTES: List[Tuple[str, Dict[str, Any]]] = [
    ("BP_HiHat", {"lane": "hh", "glyph": "lozenge", "voice": "hihat"}),
    ("BP_China", {"lane": "cr", "glyph": "spike", "voice": "china"}),
    ("BP_Splash", {"lane": "cr", "glyph": "lozengeS", "voice": "crash13"}),
    ("BP_Crash13", {"lane": "cr", "glyph": "lozengeS", "voice": "crash13"}),
    ("BP_Crash", {"lane": "cr", "glyph": "lozenge", "voice": "crash"}),
    ("BP_Snare", {"lane": "sn", "glyph": "circle", "voice": "snare"}),
    ("BP_Tom1", {"lane": "t1", "glyph": "roundrect", "voice": "tom1"}),
    ("BP_Tom2", {"lane": "t2", "glyph": "roundrect", "voice": "tom2"}),
    ("BP_FloorTom", {"lane": "t3", "glyph": "roundrect", "voice": "tom3"}),
    ("BP_Tom3", {"lane": "t3", "glyph": "roundrect", "voice": "tom3"}),
    ("BP_Triangle", {"lane": "rd", "glyph": "triangle", "voice": "triangle"}),
    ("BP_Cowbell", {"lane": "rd", "glyph": "diamond", "voice": "neutral"}),
    ("BP_Ride", {"lane": "rd", "glyph": "ring", "voice": "ride"}),
    ("BP_Kick", {"lane": "kk", "glyph": "bar", "voice": "kick"}),
    ("BP_Tambourine", {"lane": "hh", "glyph": "ring", "voice": "tambourine"}),
]
UNKNOWN_ROUTE = {"lane": "cr", "glyph": "diamond", "voice": "neutral", "unknown": True}

CORE_VOICES = {"hihat", "crash", "snare", "tom1", "tom2", "tom3", "ride", "kick"}


def route_for_class(cls: Any) -> Dict[str, Any]:
    if isinstance(cls, str):
        for prefix, route in CLASS_ROUTES:
            if cls.startswith(prefix):
                return dict(route)
    return dict(UNKNOWN_ROUTE)


# ---------------------------------------------------------------------------
# Chart text decoding (bytes -> str; utf-8-sig / utf-16le/be / latin1)
# ---------------------------------------------------------------------------


def decode_chart_bytes(data: bytes) -> str:
    """Port of decodeChartBytes (line 979): BOM sniff, BOM-less UTF-16
    heuristic, utf-8 strict, latin-1 fallback; strips a leading BOM."""
    if len(data) >= 2:
        if data[0] == 0xFF and data[1] == 0xFE:
            return data[2:].decode("utf-16-le", errors="replace")
        if data[0] == 0xFE and data[1] == 0xFF:
            return data[2:].decode("utf-16-be", errors="replace")
    if len(data) >= 64:
        even_nul = sum(1 for i in range(0, 64, 2) if data[i] == 0)
        odd_nul = sum(1 for i in range(1, 64, 2) if data[i] == 0)
        if odd_nul > 20 and even_nul == 0:
            return data.decode("utf-16-le", errors="replace")
        if even_nul > 20 and odd_nul == 0:
            return data.decode("utf-16-be", errors="replace")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    if text and text[0] == "﻿":
        text = text[1:]
    return text


# ---------------------------------------------------------------------------
# .rlrr parser (lines 1011-1121; field-presence, warn-don't-fail)
# ---------------------------------------------------------------------------


def _is_obj(v: Any) -> bool:
    return isinstance(v, dict)


def _str(v: Any) -> str:
    if isinstance(v, str):
        return v
    return "" if v is None else str(v)


def _num(v: Any, d: float) -> float:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return d
    return n if math.isfinite(n) else d


def _str_arr(v: Any) -> List[str]:
    if not isinstance(v, list):
        return []
    return [x for x in v if isinstance(x, str) and x]


def parse_rlrr(text: str) -> dict:
    """Parse .rlrr chart text. Raises ValueError ONLY on undecodable/non-JSON
    input (the caller quarantines); everything else degrades with warnings.

    Returns { meta, audio, instruments, events, bpm, warnings } exactly as the
    Kitsmith parser does (events carry an `inst` index into instruments).
    """
    warnings: List[str] = []
    try:
        data = json.loads(text)
    except ValueError as e:
        raise ValueError(f"Not valid JSON: {e}") from e
    if not _is_obj(data):
        raise ValueError("JSON root is not an object")

    md = data.get("recordingMetadata") if _is_obj(data.get("recordingMetadata")) else {}
    if not _is_obj(data.get("recordingMetadata")):
        warnings.append("recordingMetadata missing")
    meta = {
        "title": _str(md.get("title")),
        "artist": _str(md.get("artist")),
        "creator": _str(md.get("creator")),
        "description": _str(md.get("description")),
        "coverImagePath": _str(md.get("coverImagePath")),
        "lengthS": _num(md.get("length"), 0),
        "complexity": clamp_int(_num(md.get("complexity"), 0), 0, 5),
        "version": _num(data.get("version"), 0),
    }

    ad = data.get("audioFileData") if _is_obj(data.get("audioFileData")) else {}
    audio = {
        "songTracks": _str_arr(ad.get("songTracks")),
        "drumTracks": _str_arr(ad.get("drumTracks")),
        "songPreview": _str(ad.get("songPreview")),
        "calibrationOffsetS": _num(ad.get("calibrationOffset"), 0),
    }

    # instruments[] -- lane mapping derives from THIS (mandate)
    raw_inst = data.get("instruments") if isinstance(data.get("instruments"), list) else []
    if not raw_inst:
        warnings.append("no instruments[] — name-prefix routing only")
    instruments: List[dict] = []
    by_name: Dict[str, int] = {}
    for i, it in enumerate(raw_inst):
        it = it if _is_obj(it) else {}
        inst = {
            "idx": len(instruments),
            "name": _str(it.get("name")) or f"inst_{i}",
            "class": _str(it.get("class")),
            "location": ([_num(x, 0) for x in it["location"]]
                         if isinstance(it.get("location"), list) else None),
        }
        instruments.append(inst)
        if inst["name"] not in by_name:
            by_name[inst["name"]] = inst["idx"]

    # events[] -- authoritative clock; name- OR index-based; vel/velocity
    raw_ev = data.get("events") if isinstance(data.get("events"), list) else []
    if not raw_ev:
        warnings.append("no events[] — empty chart")
    events: List[dict] = []
    bad_time = 0
    unresolved = 0
    synth_by_prefix: Dict[str, int] = {}
    for e in raw_ev:
        if not _is_obj(e):
            continue
        try:
            t = float(e.get("time"))
        except (TypeError, ValueError):
            bad_time += 1
            continue
        if not math.isfinite(t):
            bad_time += 1
            continue
        inst_idx = -1
        if e.get("name") is not None:
            nm = _str(e.get("name"))
            if nm in by_name:
                inst_idx = by_name[nm]
            elif nm in synth_by_prefix:
                inst_idx = synth_by_prefix[nm]
            else:
                # No instance match -> synthesize one keyed by the event name
                # so class-prefix routing still applies (base-parity).
                inst = {"idx": len(instruments), "name": nm, "class": nm,
                        "location": None, "synthetic": True}
                instruments.append(inst)
                synth_by_prefix[nm] = inst["idx"]
                inst_idx = inst["idx"]
        else:
            # JS Number.isInteger(3.0) is true -- accept float-valued integers
            # too (gate follow-up 11: such charts lost notes silently)
            ii = e.get("instrumentIndex")
            if isinstance(ii, bool):
                ii = None
            elif isinstance(ii, float) and ii.is_integer():
                ii = int(ii)
            if isinstance(ii, int) and 0 <= ii < len(instruments):
                inst_idx = ii
        if inst_idx < 0:
            unresolved += 1
            continue
        if e.get("vel") is not None:
            vel = _num(e.get("vel"), 80)
        elif e.get("velocity") is not None:
            vel = _num(e.get("velocity"), 80 / 127) * 127
        else:
            vel = 80.0
        events.append({"time": max(0.0, t), "inst": inst_idx,
                       "vel": clamp_int(vel, 1, 127)})
    if bad_time:
        warnings.append(f"{bad_time} event(s) had unparseable time (skipped)")
    if unresolved:
        warnings.append(f"{unresolved} event(s) referenced no resolvable instrument (skipped)")
    events.sort(key=_BY_TIME_KEY)

    # bpmEvents -- DECORATION ONLY (grid/metronome); sanity-gated 30-300
    bpm: dict = {"events": None, "primary": None, "sane": False}
    raw_bpm = data.get("bpmEvents")
    if isinstance(raw_bpm, list) and raw_bpm:
        evs = []
        for b in raw_bpm:
            if not _is_obj(b):
                continue
            try:
                bv, tv = float(b.get("bpm")), float(b.get("time"))
            except (TypeError, ValueError):
                continue
            if math.isfinite(bv) and math.isfinite(tv):
                evs.append({"bpm": bv, "time": tv})
        evs.sort(key=_BY_TIME_KEY)
        if evs:
            sane = (all(30 <= b["bpm"] <= 300 for b in evs)
                    and all(evs[i]["time"] >= evs[i - 1]["time"]
                            for i in range(1, len(evs))))
            bpm = {"events": evs, "primary": evs[0]["bpm"], "sane": sane}
            if not sane:
                warnings.append("bpmEvents present but not sane (ignored for grid)")

    return {"meta": meta, "audio": audio, "instruments": instruments,
            "events": events, "bpm": bpm, "warnings": warnings}


# ---------------------------------------------------------------------------
# Difficulty filename convention + songKey (lines 1124-1144)
# ---------------------------------------------------------------------------

DIFFICULTIES = ["Easy", "Medium", "Hard", "Expert"]
_DIFF_RE = re.compile(r"_(Easy|Medium|Hard|Expert)\.rlrr$", re.IGNORECASE)


def difficulty_from_filename(name: str) -> Optional[str]:
    m = _DIFF_RE.search(name)
    if not m:
        return None
    d = m.group(1).lower()
    for x in DIFFICULTIES:
        if x.lower() == d:
            return x
    return None


def song_key(title: str, artist: str, difficulty_filenames: List[str]) -> str:
    """Rename-proof records key: dual FNV-1a 32-bit over UTF-16 code units
    -> 16 hex chars (byte-for-byte parity with the JS songKey)."""
    s = f"{title}|{artist}|{','.join(sorted(difficulty_filenames or []))}"
    h1 = 0x811C9DC5
    h2 = (0x01000193 ^ 0x5BD1E995) & 0xFFFFFFFF
    # charCodeAt walks UTF-16 code units (surrogate pairs = 2 units)
    units = s.encode("utf-16-le")
    for i in range(0, len(units), 2):
        c = units[i] | (units[i + 1] << 8)
        h1 = ((h1 ^ c) * 0x01000193) & 0xFFFFFFFF
        h2 = ((h2 ^ c) * 0x85EBCA6B) & 0xFFFFFFFF
    return f"{h1:08x}{h2:08x}"


# ---------------------------------------------------------------------------
# Kit layout model (lines 1146-1206; kit-studio spec sections 1, 7, 8)
# ---------------------------------------------------------------------------


def default_layout() -> dict:
    lanes: Dict[str, dict] = {}
    for i, lane_id in enumerate(STANDARD_ORDER + ["ax1", "ax2"]):
        lanes[lane_id] = {
            "label": LANE_DEFS[lane_id]["label"],
            "shape": LANE_DEFS[lane_id]["shape"],
            "color": i % len(PALETTE),
            "widthW": 1.0,
            "visible": True,
            "voice": LANE_DEFS[lane_id]["voice"],
        }
    return {"order": list(STANDARD_ORDER), "lanes": lanes, "lefty": False}


def _preset_compact6() -> dict:
    lo = default_layout()
    lo["order"] = ["hh", "cr", "sn", "t1", "t3", "kk"]
    return lo


def _preset_starter4() -> dict:
    lo = default_layout()
    lo["order"] = ["hh", "sn", "t2", "kk"]
    lo["lanes"]["t2"]["label"] = "Toms+Cym"
    return lo


def _preset_lefty() -> dict:
    lo = default_layout()
    lo["lefty"] = True
    return lo


def _preset_cymbal_split10() -> dict:
    lo = default_layout()
    lo["order"] = STANDARD_ORDER + ["ax1", "ax2"]
    lo["lanes"]["ax1"]["label"] = "Crash B"
    lo["lanes"]["ax2"]["label"] = "Ride B"
    return lo


BUILTIN_PRESETS: Dict[str, Callable[[], dict]] = {
    "Standard 8": default_layout,
    "Lefty": _preset_lefty,
    "Compact 6": _preset_compact6,
    "Starter 4": _preset_starter4,
    "Cymbal Split 10": _preset_cymbal_split10,
}

PRESET_FOLDS: Dict[str, Dict[str, str]] = {
    "Compact 6": {"t2": "t1", "rd": "cr"},
    "Starter 4": {"cr": "t2", "t1": "t2", "t3": "t2", "rd": "t2"},
}


def folds_for_order(order: Optional[List[str]]) -> Dict[str, str]:
    """The fold table for a lane order that matches a built-in preset.

    PRESET_FOLDS existed and was imported by both UI modules, but every
    resolve_routing() call passed routing=None, so it was never consulted. Without
    it, a note whose lane is absent from the preset's order hits the
    "folded-to-hidden safety" in resolve_routing and lands on active_order[0] --
    the hi-hat. Measured: Compact 6 sent Tom-2 and Ride to hh (should be t1/cr),
    and Starter 4 sent crash, Tom-1, Tom-3 and Ride to hh while its t2 lane, the
    one actually labelled "Toms+Cym", received none of them.

    Matched on the ORDER rather than a preset NAME on purpose: it works at every
    call site without threading the kit choice through the session config, and it
    stops applying by itself once a user edits the kit away from the preset shape,
    at which point the preset's fold TARGETS may no longer exist as lanes.
    """
    key = list(order or [])
    if not key:
        return {}
    for _name, _folds in PRESET_FOLDS.items():
        _make = BUILTIN_PRESETS.get(_name)
        if _make and list(_make()["order"]) == key:
            return dict(_folds)
    return {}

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def snapshot_layout(layout: dict) -> dict:
    """snapshotLayout = JSON round-trip deep copy."""
    return json.loads(json.dumps(layout))


def apply_layout_snapshot(snap: Any) -> dict:
    """Sanitize + apply a layout snapshot (lines 3071-3088 semantics):
    back-fill missing lanes from default, drop bad customColor, clamp widthW
    0.5-2, label <= 24 chars, order filtered to known ids and <= MAX_LANES."""
    base = default_layout()
    if not _is_obj(snap):
        return base
    lanes = dict(base["lanes"])
    snap_lanes = snap.get("lanes") if _is_obj(snap.get("lanes")) else {}
    for lane_id, base_ln in base["lanes"].items():
        ln = snap_lanes.get(lane_id)
        if not _is_obj(ln):
            lanes[lane_id] = dict(base_ln)
            continue
        merged = dict(base_ln)
        merged.update(ln)
        cc = merged.get("customColor")
        if cc is not None and not (isinstance(cc, str) and _HEX_COLOR_RE.match(cc)):
            merged.pop("customColor", None)
        if merged.get("shape") not in SHAPES:
            merged["shape"] = base_ln["shape"]
        try:
            w = float(merged.get("widthW"))
        except (TypeError, ValueError):
            w = 1.0
        if not math.isfinite(w) or w == 0:
            w = 1.0
        merged["widthW"] = max(0.5, min(2.0, w))
        merged["label"] = _str(merged.get("label"))[:24] or base_ln["label"]
        try:
            merged["color"] = int(merged.get("color", base_ln["color"])) % len(PALETTE)
        except (TypeError, ValueError):
            merged["color"] = base_ln["color"]
        merged["visible"] = merged.get("visible", True) is not False
        if not isinstance(merged.get("voice"), str) or not merged["voice"]:
            merged["voice"] = base_ln["voice"]
        lanes[lane_id] = merged
    order_in = snap.get("order") if isinstance(snap.get("order"), list) else []
    # Only string lane IDs are hashable dict keys -- a corrupt snapshot whose
    # order held a dict/list element crashed `x in lanes` (unhashable type) and
    # took down the whole Practice tab build (breaker fix F1, 2026-07-21).
    order = [x for x in order_in if isinstance(x, str) and x in lanes][:MAX_LANES]
    if not order:
        order = list(base["order"])
    return {"order": order, "lanes": lanes, "lefty": bool(snap.get("lefty"))}


def resolve_routing(chart: dict, layout: dict, routing: Optional[dict]) -> dict:
    """Port of resolveRouting (line 1176). Lefty is applied HERE only --
    it mirrors both rendering and judging (lane index is post-reversal)."""
    routing = routing or {}
    by_name = routing.get("byInstName") or {}
    folds = routing.get("folds") or {}
    active_order = (list(reversed(layout["order"])) if layout.get("lefty")
                    else list(layout["order"]))
    lane_idx_of = {lane_id: i for i, lane_id in enumerate(active_order)}

    def follow_fold(lane_id: str, depth: int = 0) -> str:
        if depth > 4:
            return lane_id
        f = folds.get(lane_id)
        return follow_fold(f, depth + 1) if f and f != lane_id else lane_id

    inst_routes: List[dict] = []
    lane_of = [-1] * len(chart["instruments"])
    extras: List[dict] = []
    for inst in chart["instruments"]:
        route = route_for_class(inst["class"])
        lane_id = by_name[inst["name"]] if by_name.get(inst["name"]) is not None \
            else route["lane"]
        if lane_id == "_off":
            inst_routes.append({"inst": inst, "laneId": "_off",
                                "glyph": route["glyph"], "voice": route["voice"],
                                "unknown": bool(route.get("unknown"))})
            continue
        if lane_id not in lane_idx_of:
            lane_id = route["lane"]                    # stale override
        lane_id = follow_fold(lane_id)
        if lane_id not in lane_idx_of:
            lane_id = active_order[0]                  # folded-to-hidden safety
        r = {"inst": inst, "laneId": lane_id, "glyph": route["glyph"],
             "voice": route["voice"], "unknown": bool(route.get("unknown"))}
        inst_routes.append(r)
        lane_of[inst["idx"]] = lane_idx_of[lane_id]
        if route.get("unknown") or route["voice"] not in CORE_VOICES:
            extras.append(r)
    return {"laneOf": lane_of, "instRoutes": inst_routes, "extras": extras,
            "activeOrder": active_order}


class PNote:
    """A playable note (post-routing): {id, time, vel, lane, glyph, voice,
    hit, grade} -- exactly the JS note object, as __slots__ for density."""

    __slots__ = ("id", "time", "vel", "lane", "glyph", "voice", "hit", "grade")

    def __init__(self, nid: int, time: float, vel: int, lane: int,
                 glyph: str, voice: str) -> None:
        self.id = nid
        self.time = time
        self.vel = vel
        self.lane = lane
        self.glyph = glyph
        self.voice = voice
        self.hit = False
        self.grade = ""


def build_lane_notes(chart: dict, resolved: dict) -> List[List[PNote]]:
    """chart.events + resolved routing -> per-ordered-lane sorted note lists."""
    lane_count = len(resolved["activeOrder"])
    lanes: List[List[PNote]] = [[] for _ in range(lane_count)]
    glyph_of: Dict[int, str] = {}
    voice_of: Dict[int, str] = {}
    for r in resolved["instRoutes"]:
        glyph_of[r["inst"]["idx"]] = r["glyph"]
        voice_of[r["inst"]["idx"]] = r["voice"]
    nid = 1
    for e in chart["events"]:
        lane = resolved["laneOf"][e["inst"]]
        if lane < 0 or lane >= lane_count:
            continue
        lanes[lane].append(PNote(nid, e["time"], e["vel"], lane,
                                 glyph_of.get(e["inst"]) or "circle",
                                 voice_of.get(e["inst"]) or "neutral"))
        nid += 1
    for ln in lanes:
        ln.sort(key=_BY_NOTE_TIME)
    return lanes


def song_duration_from_lanes(lane_notes: List[List[PNote]]) -> float:
    mx = 0.0
    for ln in lane_notes:
        if ln:
            mx = max(mx, ln[-1].time)
    return mx + 2.0 if mx > 0 else 60.0


# ---------------------------------------------------------------------------
# Session -- judge + scoring + stats (lines 1238-1403; timing-only,
# velocity NEVER affects judgment -- the "consensus M10" contract)
# ---------------------------------------------------------------------------


class Session:
    """Faithful port of the Kitsmith Session (judge/miss-sweep/auto-kick)."""

    def __init__(self, *, lane_notes: List[List[PNote]], lane_visible: List[bool],
                 auto_kick: bool, kick_lane_idx: int, input_latency_ms: float,
                 song_duration: float, windows: Optional[Windows] = None) -> None:
        self.lane_notes = lane_notes
        self.lane_visible = lane_visible
        self.auto_kick = bool(auto_kick)
        self.kick_lane_idx = kick_lane_idx
        self.input_latency_ms = input_latency_ms or 0
        self.song_duration = song_duration
        self.windows = windows or windows_for("standard")
        self.lane_count = len(lane_notes)
        self.reset()

    def reset(self) -> None:
        self.song_time = 0.0
        self.hits = 0
        self.misses = 0
        self.streak = 0
        self.best_streak = 0
        self.perfect = 0
        self.early = 0
        self.late = 0
        self.miss_count = 0
        self.timing_offsets: List[float] = []
        self.last_milestone_streak = 0
        self.milestone_pulse_at = -1.0
        self.last_lane_hit_time = [-99.0] * self.lane_count
        self.last_hit_velocity = [80] * self.lane_count
        self.events: List[dict] = []
        self.lane_stats = [{"hits": 0, "miss": 0, "offSumMs": 0.0}
                           for _ in range(self.lane_count)]
        self.sections: Dict[int, dict] = {}
        self._cursor = [0] * self.lane_count
        for ln in self.lane_notes:
            for n in ln:
                n.hit = False
                n.grade = ""

    # ----- derived stats ----------------------------------------------------
    @property
    def accuracy_pct(self) -> float:
        total = self.hits + self.misses
        return 0.0 if total == 0 else js_round((self.hits / total) * 1000) / 10

    @property
    def score(self) -> int:
        return math.trunc(self.perfect * SCORE["perfect"] + self.early * SCORE["early"]
                          + self.late * SCORE["late"] + self.miss_count * SCORE["miss"])

    @property
    def total_notes(self) -> int:
        return sum(len(self.lane_notes[i]) for i in range(self.lane_count)
                   if self.lane_visible[i])

    def _section(self, t: float) -> dict:
        idx = int(max(0.0, t) // SECTION_S)
        s = self.sections.get(idx)
        if s is None:
            s = {"hits": 0, "miss": 0}
            self.sections[idx] = s
        return s

    # ----- judging ----------------------------------------------------------
    def judge_hit(self, lane: int, hit_time: float, velocity: int = 100) -> Optional[str]:
        """Nearest unconsumed note within +/-W.hit wins (strict <, ties keep
        the earlier note). No match -> None: the stray input is swallowed --
        NO miss penalty, no combo break."""
        if lane < 0 or lane >= self.lane_count:
            return None
        if not self.lane_visible[lane]:
            return None
        self.last_lane_hit_time[lane] = hit_time
        self.last_hit_velocity[lane] = velocity
        best: Optional[PNote] = None
        best_diff = math.inf
        notes = self.lane_notes[lane]
        w = self.windows
        for note in notes:
            if note.time > hit_time + w.hit:
                break                      # sorted -- past window
            if note.hit or note.grade == "miss":
                continue
            diff = abs(hit_time - note.time)
            if diff <= w.hit and diff < best_diff:
                best_diff = diff
                best = note
        if best is None:
            return None
        signed = hit_time - best.time
        if abs(signed) <= w.perfect:
            grade = "perfect"
        elif signed < 0:
            grade = "early"
        else:
            grade = "late"
        best.hit = True
        best.grade = grade
        self.timing_offsets.append(signed * 1000)
        self.hits += 1
        self.streak += 1
        if self.streak > self.best_streak:
            self.best_streak = self.streak
        if grade == "perfect":
            self.perfect += 1
        elif grade == "early":
            self.early += 1
        else:
            self.late += 1
        ls = self.lane_stats[lane]
        ls["hits"] += 1
        ls["offSumMs"] += signed * 1000
        self._section(best.time)["hits"] += 1
        self._milestone()
        self.events.append({"lane": lane, "grade": grade, "t": hit_time,
                            "vel": velocity, "off": signed * 1000})
        return grade

    def process_auto_kick(self, song_time: float) -> None:
        """Auto-hit pending kick notes within the hit window as PERFECTs
        (vel 100, off 0, auto flag; NOT added to timing_offsets)."""
        k = self.kick_lane_idx
        if not self.auto_kick or k < 0 or k >= self.lane_count or not self.lane_visible[k]:
            return
        w = self.windows
        for note in self.lane_notes[k]:
            if note.time > song_time + w.hit:
                break
            if note.hit or note.grade == "miss":
                continue
            if abs(song_time - note.time) <= w.hit:
                note.hit = True
                note.grade = "perfect"
                self.hits += 1
                self.streak += 1
                if self.streak > self.best_streak:
                    self.best_streak = self.streak
                self.perfect += 1
                self.last_lane_hit_time[k] = song_time
                self.lane_stats[k]["hits"] += 1
                self._section(note.time)["hits"] += 1
                self._milestone()
                self.events.append({"lane": k, "grade": "perfect", "t": song_time,
                                    "vel": 100, "off": 0, "auto": True})

    def process_misses(self, song_time: float) -> None:
        """Monotonic per-lane cursors sweep notes past the miss horizon.
        Hidden lanes are never swept -- invisible notes cost nothing."""
        w = self.windows
        for lane in range(self.lane_count):
            if not self.lane_visible[lane]:
                continue
            notes = self.lane_notes[lane]
            c = self._cursor[lane]
            while c < len(notes):
                note = notes[c]
                if song_time - note.time <= w.miss_past:
                    break
                if not note.hit and note.grade != "miss":
                    note.grade = "miss"
                    self.misses += 1
                    self.streak = 0
                    self.miss_count += 1
                    self.lane_stats[lane]["miss"] += 1
                    self._section(note.time)["miss"] += 1
                    self.events.append({"lane": lane, "grade": "miss",
                                        "t": song_time, "miss": True})
                c += 1
            self._cursor[lane] = c

    def seek(self, new_time: float) -> None:
        """Notes strictly before the seek point are consumed (skipped, not
        scored, not miss-swept) -- prevents phantom misses on seek/loop wrap.
        Stats are NOT reset (only reset() does that)."""
        self.song_time = new_time
        for lane in range(self.lane_count):
            notes = self.lane_notes[lane]
            for note in notes:
                if note.time < new_time:
                    note.hit = True
                    note.grade = ""
                else:
                    note.hit = False
                    note.grade = ""
            c = 0
            while c < len(notes) and notes[c].time < new_time:
                c += 1
            self._cursor[lane] = c

    def _milestone(self) -> None:
        if (self.streak > 0 and self.streak % MILESTONE_EVERY == 0
                and self.streak != self.last_milestone_streak):
            self.last_milestone_streak = self.streak
            self.milestone_pulse_at = self.song_time

    def worst_section(self) -> Optional[dict]:
        """Lowest hits/(hits+miss) among 10 s buckets with >= 4 judged notes."""
        worst: Optional[dict] = None
        for idx, s in self.sections.items():
            total = s["hits"] + s["miss"]
            if total < 4:
                continue
            acc = s["hits"] / total
            if worst is None or acc < worst["acc"]:
                worst = {"idx": idx, "acc": acc, "t0": idx * SECTION_S,
                         "t1": (idx + 1) * SECTION_S}
        return worst

    def results(self, exit_reason: str, duration_secs: float) -> dict:
        return {
            "hits": self.hits, "misses": self.misses,
            "best_streak": self.best_streak, "accuracy_pct": self.accuracy_pct,
            "perfect_count": self.perfect, "early_count": self.early,
            "late_count": self.late, "miss_count": self.miss_count,
            "exit_reason": exit_reason,
            "duration_secs": js_round(max(0.0, duration_secs) * 1000) / 1000,
        }


# ---------------------------------------------------------------------------
# BPM estimate from note inter-onset intervals (metronome fallback, line 1439)
# ---------------------------------------------------------------------------


def bpm_from_times(times: List[float], ref_bpm: float = 120) -> Optional[float]:
    ts = sorted(times)
    if len(ts) < 8:
        return None
    intervals = [ts[i] - ts[i - 1] for i in range(1, len(ts))]
    intervals = [x for x in intervals if x > 0.05]
    if not intervals:
        return None
    intervals.sort()
    base = intervals[int(len(intervals) * 0.15)]

    def score(b: float) -> int:
        return sum(1 for x in intervals if abs(x / b - js_round(x / b)) < 0.12)

    best_base, best_score = base, score(base)
    for mult in (0.5, 0.75, 1.0, 1.333, 1.5, 2.0):
        c = base * mult
        if c > 0.05 and score(c) > best_score:
            best_score = score(c)
            best_base = c
    candidates = [60 / (best_base * d) for d in (1, 2, 4, 3, 6)]
    candidates = [b for b in candidates if 60 <= b <= 240]
    if not candidates:
        return None
    best = candidates[0]
    for b in candidates:
        if abs(b - ref_bpm) < abs(best - ref_bpm):
            best = b
    return best


# ---------------------------------------------------------------------------
# Built-in demo chart (line 1462 verbatim -- "Forge Groove")
# ---------------------------------------------------------------------------


def demo_chart() -> dict:
    bpm = 118
    beat = 60 / bpm
    bar = beat * 4
    bars = 16
    instruments = [
        {"idx": 0, "name": "demo_hh", "class": "BP_HiHat_C"},
        {"idx": 1, "name": "demo_cr", "class": "BP_Crash17_C"},
        {"idx": 2, "name": "demo_sn", "class": "BP_Snare_C"},
        {"idx": 3, "name": "demo_t1", "class": "BP_Tom1_C"},
        {"idx": 4, "name": "demo_t2", "class": "BP_Tom2_C"},
        {"idx": 5, "name": "demo_t3", "class": "BP_FloorTom_C"},
        {"idx": 6, "name": "demo_rd", "class": "BP_Ride17_C"},
        {"idx": 7, "name": "demo_kk", "class": "BP_Kick_C"},
    ]
    hh, cr, sn, t1, t2, t3, rd, kk = 0, 1, 2, 3, 4, 5, 6, 7
    events: List[dict] = []

    def push(t: float, inst: int, vel: int) -> None:
        events.append({"time": round(t, 4), "inst": inst, "vel": vel})

    for b in range(bars):
        t0 = 1.0 + b * bar
        fill = (b % 8) == 7
        ride = 8 <= b < 12
        for e in range(8):
            if fill and e >= 4:
                continue
            push(t0 + e * beat / 2, rd if ride else hh, 96 if e % 2 == 0 else 56)
        if not fill:
            push(t0, kk, 112)
            push(t0 + 1.5 * beat, kk, 88)
            push(t0 + 2 * beat, kk, 108)
            push(t0 + beat, sn, 110)
            push(t0 + 3 * beat, sn, 112)
            if b % 4 == 2:
                push(t0 + 3.5 * beat, sn, 30)          # ghost
        else:
            push(t0, kk, 112)
            push(t0 + beat, sn, 110)
            seq = [sn, sn, t1, t1, t2, t2, t3, t3]
            for s in range(8, 16):
                push(t0 + s * beat / 4, seq[s - 8], 104 if s % 2 == 0 else 86)
        if b % 8 == 0:
            push(t0, cr, 122)
    push(1.0 + bars * bar, cr, 127)
    push(1.0 + bars * bar, kk, 120)
    events.sort(key=_BY_TIME_KEY)
    return {
        "meta": {"title": "Forge Groove", "artist": "ParaKit", "creator": "built-in",
                 "description": "", "coverImagePath": "",
                 "lengthS": 1.0 + bars * bar + 3, "complexity": 2, "version": 0},
        "audio": {"songTracks": [], "drumTracks": [], "songPreview": "",
                  "calibrationOffsetS": 0},
        "instruments": instruments,
        "events": events,
        "bpm": {"events": [{"bpm": bpm, "time": 1.0}], "primary": bpm, "sane": True},
        "warnings": [],
        "builtin": True,
    }


# ---------------------------------------------------------------------------
# Default prefs (HTML DEFAULT_PREFS, lines 2730-2761) -- persisted via
# ConfigManager under the "practice_prefs" key ({**DEFAULT_PREFS, **stored}).
# ---------------------------------------------------------------------------

DEFAULT_PREFS: Dict[str, Any] = {
    "v": 1, "windowMode": "standard", "inputLatencyMs": 0, "countIn": 4,
    "fallTime": 2.6, "noteSize": 1.0, "hitLineFrac": 0.78, "hwWidth": 0.82,
    "hwPos": 0.5, "laneGap": 4,
    "kickLine": True, "square": False, "compact": False, "beatGrid": True,
    "autoKick": False, "classicStyle": False,
    "youDrum": True, "metronome": False, "speed": 1.0, "palette": "forge",
    "uiScale": 1.0,
    "fx": True, "reduceMotion": False, "perfHud": False, "sort": "title",
    "kbPreset": "standard", "binds": None,
    "busSong": 1.0, "busDrums": 1.0, "busSynth": 0.9, "busMaster": 0.92,
    "muteSynth": False, "muteOnMiss": True,
    "preferredDifficulty": "Expert", "bootDone": False, "midiAuto": False,
}

#: keyboard accent velocities (input.js line 1965)
KB_ACCENT_VEL = 120
KB_BASE_VEL = 100
#: velocity a "+ghost" extra binding fires at
KB_GHOST_VEL = 32

#: default keyboard binds: JS KeyboardEvent.code -> lane id
DEFAULT_KEYBINDS: List[Dict[str, Any]] = [
    {"code": "KeyA", "lane": "hh"},
    {"code": "KeyS", "lane": "cr"},
    {"code": "KeyD", "lane": "sn"},
    {"code": "KeyF", "lane": "t1"},
    {"code": "KeyJ", "lane": "t2"},
    {"code": "KeyK", "lane": "t3"},
    {"code": "KeyL", "lane": "rd"},
    {"code": "Space", "lane": "kk"},
]

#: keyboard-preset bind tables — VERBATIM port of the web reference's
#: KEYBIND_PRESETS (parakit-practice-v3.html:1950-1965). The port stored the
#: `kbPreset` pref but never applied it (the HTML wires
#: `KeyboardInput(prefs.binds || KEYBIND_PRESETS[kbPreset].binds)` at :5525 and,
#: on combo change, setBinds + persist + rebuild chips at :5019-5024) — so
#: Sticking Focus / Lefty Home Row silently behaved as Standard. This table +
#: the tab-side apply restore parity.
KEYBIND_PRESETS: Dict[str, Dict[str, Any]] = {
    "standard": {"label": "Standard", "binds": DEFAULT_KEYBINDS},
    "sticking": {"label": "Sticking Focus", "binds": [
        {"code": "KeyF", "lane": "sn"}, {"code": "KeyJ", "lane": "sn"},
        {"code": "KeyD", "lane": "hh"}, {"code": "KeyK", "lane": "hh"},
        {"code": "KeyS", "lane": "t1"}, {"code": "KeyL", "lane": "rd"},
        {"code": "KeyA", "lane": "cr"}, {"code": "Space", "lane": "kk"},
    ]},
    "leftyRow": {"label": "Lefty Home Row", "binds": [
        {"code": "Semicolon", "lane": "hh"}, {"code": "KeyL", "lane": "cr"},
        {"code": "KeyK", "lane": "sn"}, {"code": "KeyJ", "lane": "t1"},
        {"code": "KeyF", "lane": "t2"}, {"code": "KeyD", "lane": "t3"},
        {"code": "KeyS", "lane": "rd"}, {"code": "Space", "lane": "kk"},
    ]},
}
#: the TTK port's Settings combo persists the id "lefty" (predates this table);
#: alias it to the HTML's "leftyRow" so both vocabularies resolve.
KEYBIND_PRESETS["lefty"] = KEYBIND_PRESETS["leftyRow"]


# ===========================================================================
# __main__ smoke + mutation harness
# ===========================================================================
#
# This block is the honesty-preamble-required verification: it (1) exercises
# scoring end-to-end on demo_chart() (perfect/early/late/miss classification,
# combo milestones, per-lane bias, grade thresholds), (2) mutation-tests each
# guard named in DISPATCH §3a/§7 (window-mode multipliers, the swallow-vs-
# miss branch, the grade thresholds) by flipping a constant/branch and
# showing the smoke goes RED, then restoring and showing GREEN, and (3) runs
# parse_rlrr on a real on-disk .rlrr chart. It imports nothing beyond stdlib.

if __name__ == "__main__":
    import glob
    import sys

    FAILURES: List[str] = []

    def check(label: str, cond: bool, detail: str = "") -> None:
        status = "PASS" if cond else "FAIL"
        print(f"  [{status}] {label}" + (f" -- {detail}" if detail else ""))
        if not cond:
            FAILURES.append(label)

    # -----------------------------------------------------------------
    # 1) End-to-end scoring smoke on demo_chart()
    # -----------------------------------------------------------------
    print("=" * 70)
    print("1) SCORING SMOKE -- demo_chart(), standard window mode")
    print("=" * 70)

    def run_demo_session(window_mode: str = "standard", auto_kick: bool = False,
                          perturb_hit_lane: Optional[int] = None,
                          perturb_offset: float = 0.0) -> Tuple[Session, dict]:
        """Build a Session from demo_chart() and drive every note through
        judge_hit() at a controlled offset from its true time, so the run is
        deterministic and exercises perfect/early/late/miss + auto-kick."""
        chart = demo_chart()
        layout = default_layout()
        resolved = resolve_routing(chart, layout, None)
        lane_notes = build_lane_notes(chart, resolved)
        lane_visible = [True] * len(resolved["activeOrder"])
        kick_lane_idx = resolved["activeOrder"].index("kk")
        windows = windows_for(window_mode)
        duration = song_duration_from_lanes(lane_notes)
        sess = Session(lane_notes=lane_notes, lane_visible=lane_visible,
                       auto_kick=auto_kick, kick_lane_idx=kick_lane_idx,
                       input_latency_ms=0, song_duration=duration, windows=windows)

        # Flatten (lane, note) pairs sorted by time so we can drive a single
        # monotonic clock through judge/auto-kick/miss-sweep like a real run.
        flat: List[Tuple[float, int, PNote]] = []
        for lane_idx, notes in enumerate(lane_notes):
            for n in notes:
                flat.append((n.time, lane_idx, n))
        flat.sort(key=lambda x: x[0])

        # Deterministic per-note offset pattern, sparse enough that the
        # combo survives long runs (so milestone/best-streak checks are
        # meaningful): mostly perfect, occasional early/late (still hits,
        # do not break streak), and a miss only every 37th note (skip
        # judging entirely so it falls to the miss-sweep). Kick lane is
        # skipped when auto_kick is on so the auto-kick path gets
        # exercised instead.
        i = 0
        for t, lane_idx, note in flat:
            if auto_kick and lane_idx == kick_lane_idx:
                continue  # let process_auto_kick handle it below
            if i % 37 == 0 and i > 0:
                kind = "miss"
            elif i % 11 == 0:
                kind = "early"
            elif i % 13 == 0:
                kind = "late"
            else:
                kind = "perfect"
            i += 1
            if lane_idx == perturb_hit_lane:
                # extra deterministic bias injected on one lane for the
                # per-lane-bias check
                hit_time = t + perturb_offset
                sess.judge_hit(lane_idx, hit_time, velocity=100)
                sess.process_misses(hit_time + windows.miss_past + 0.001)
                continue
            if kind == "perfect":
                hit_time = t
            elif kind == "early":
                hit_time = t - windows.perfect - 0.005  # inside hit window, outside perfect
            elif kind == "late":
                hit_time = t + windows.perfect + 0.005
            else:  # miss -- do not judge; let the sweep catch it
                sess.process_misses(t + windows.miss_past + 0.001)
                continue
            sess.judge_hit(lane_idx, hit_time, velocity=100)
            sess.process_misses(hit_time + 0.001)

        if auto_kick:
            for note in lane_notes[kick_lane_idx]:
                sess.process_auto_kick(note.time)
        # final sweep to the end of the song
        sess.process_misses(duration + 10)
        results = sess.results("test", duration)
        return sess, results

    sess, results = run_demo_session()
    print(f"  hits={sess.hits} misses={sess.misses} perfect={sess.perfect} "
          f"early={sess.early} late={sess.late} miss_count={sess.miss_count}")
    print(f"  accuracy_pct={sess.accuracy_pct} score={sess.score} "
          f"best_streak={sess.best_streak}")
    grade = compute_grade(sess.accuracy_pct)
    print(f"  grade={grade} results={results}")

    check("perfect/early/late/miss all occurred",
          sess.perfect > 0 and sess.early > 0 and sess.late > 0 and sess.miss_count > 0,
          f"perfect={sess.perfect} early={sess.early} late={sess.late} miss={sess.miss_count}")
    check("accuracy_pct matches hand formula",
          sess.accuracy_pct == (0.0 if sess.hits + sess.misses == 0 else
                                 js_round((sess.hits / (sess.hits + sess.misses)) * 1000) / 10))
    check("score matches SCORE table (trunc'd)",
          sess.score == math.trunc(sess.perfect * SCORE["perfect"] + sess.early * SCORE["early"]
                                    + sess.late * SCORE["late"] + sess.miss_count * SCORE["miss"]))
    check("best_streak >= COMBO_MIN somewhere in a 16-bar groove",
          sess.best_streak >= COMBO_MIN, f"best_streak={sess.best_streak}")
    check("at least one milestone fired (streak crossed a multiple of 10)",
          sess.last_milestone_streak > 0 and sess.last_milestone_streak % MILESTONE_EVERY == 0,
          f"last_milestone_streak={sess.last_milestone_streak}")
    check("grade is one of the 5 letters", grade[0] in {"S", "A", "B", "C", "D"}, str(grade))

    # -----------------------------------------------------------------
    # 1b) Per-lane bias -- push one lane consistently late and confirm its
    #     lane_stats offSumMs comes out positive (later == more positive ms)
    #     while another lane run "clean" is not biased that way.
    # -----------------------------------------------------------------
    print()
    print("1b) PER-LANE BIAS")
    chart = demo_chart()
    layout = default_layout()
    resolved = resolve_routing(chart, layout, None)
    lane_notes = build_lane_notes(chart, resolved)
    sn_lane = resolved["activeOrder"].index("sn")
    biased_sess, _ = run_demo_session(perturb_hit_lane=sn_lane, perturb_offset=0.02)
    sn_stats = biased_sess.lane_stats[sn_lane]
    avg_off = sn_stats["offSumMs"] / sn_stats["hits"] if sn_stats["hits"] else 0
    print(f"  snare lane offSumMs={sn_stats['offSumMs']:.2f} hits={sn_stats['hits']} "
          f"avg_off_ms={avg_off:.2f}")
    check("biased lane's average offset is positive (consistently late)",
          avg_off > 0, f"avg_off_ms={avg_off:.2f}")

    # -----------------------------------------------------------------
    # 2) MUTATION TESTS -- flip a guard, show RED, restore, show GREEN
    # -----------------------------------------------------------------
    print()
    print("=" * 70)
    print("2) MUTATION TESTS")
    print("=" * 70)

    # ---- 2a. Window-mode multipliers (relaxed x1.5 / standard x1.0 / strict x0.6)
    print("\n2a) window-mode multipliers")
    w_relaxed = windows_for("relaxed")
    w_standard = windows_for("standard")
    w_strict = windows_for("strict")
    print(f"  relaxed.hit={w_relaxed.hit:.4f} standard.hit={w_standard.hit:.4f} "
          f"strict.hit={w_strict.hit:.4f}")
    ok_before = (abs(w_relaxed.hit - HIT_S * 1.5) < 1e-9
                 and abs(w_standard.hit - HIT_S * 1.0) < 1e-9
                 and abs(w_strict.hit - HIT_S * 0.6) < 1e-9)
    check("GREEN (before mutation): windows scale by the documented multipliers", ok_before)

    _orig_relaxed_mul = WINDOW_MODES["relaxed"]["mul"]
    WINDOW_MODES["relaxed"]["mul"] = 1.0          # MUTATE: relaxed == standard
    w_relaxed_mut = windows_for("relaxed")
    mutated_broke_it = abs(w_relaxed_mut.hit - HIT_S * 1.5) > 1e-9
    check("RED (after mutation): relaxed multiplier flip is now detectably wrong",
          mutated_broke_it, f"relaxed.hit={w_relaxed_mut.hit:.4f} (expected != {HIT_S*1.5:.4f})")
    WINDOW_MODES["relaxed"]["mul"] = _orig_relaxed_mul   # RESTORE
    w_relaxed_restored = windows_for("relaxed")
    check("GREEN (restored): relaxed multiplier back to 1.5x",
          abs(w_relaxed_restored.hit - HIT_S * 1.5) < 1e-9)

    # ---- 2b. Swallow-vs-miss branch (stray hit with no in-window note ->
    #          swallowed: no penalty, no combo break)
    print("\n2b) swallow-vs-miss branch")
    chart2 = demo_chart()
    resolved2 = resolve_routing(chart2, default_layout(), None)
    lane_notes2 = build_lane_notes(chart2, resolved2)
    lane_visible2 = [True] * len(resolved2["activeOrder"])
    kick_idx2 = resolved2["activeOrder"].index("kk")
    sess2 = Session(lane_notes=lane_notes2, lane_visible=lane_visible2, auto_kick=False,
                    kick_lane_idx=kick_idx2, input_latency_ms=0,
                    song_duration=song_duration_from_lanes(lane_notes2))
    sess2.streak = 7  # pretend we're mid-combo
    stray_lane = resolved2["activeOrder"].index("hh")
    # a time far from any hi-hat note (well past MISS_PAST_S from anything)
    stray_time = 999.0
    misses_before, streak_before = sess2.misses, sess2.streak
    grade_result = sess2.judge_hit(stray_lane, stray_time, velocity=100)
    check("GREEN (before mutation): stray hit with no nearby note is swallowed "
          "(returns None, no miss, no combo break)",
          grade_result is None and sess2.misses == misses_before and sess2.streak == streak_before,
          f"grade_result={grade_result} misses={sess2.misses} streak={sess2.streak}")

    # MUTATE: monkeypatch judge_hit to treat "no match" as a miss+combo-break,
    # then show the same check now reports RED.
    _orig_judge_hit = Session.judge_hit

    def _mutant_judge_hit(self, lane, hit_time, velocity=100):
        if lane < 0 or lane >= self.lane_count or not self.lane_visible[lane]:
            return None
        notes = self.lane_notes[lane]
        w = self.windows
        best = None
        best_diff = math.inf
        for note in notes:
            if note.time > hit_time + w.hit:
                break
            if note.hit or note.grade == "miss":
                continue
            diff = abs(hit_time - note.time)
            if diff <= w.hit and diff < best_diff:
                best_diff = diff
                best = note
        if best is None:
            # BUG INJECTED: treat stray input as a miss + combo break
            self.misses += 1
            self.streak = 0
            return "miss"
        return _orig_judge_hit(self, lane, hit_time, velocity)

    Session.judge_hit = _mutant_judge_hit
    sess3 = Session(lane_notes=lane_notes2, lane_visible=lane_visible2, auto_kick=False,
                    kick_lane_idx=kick_idx2, input_latency_ms=0,
                    song_duration=song_duration_from_lanes(lane_notes2))
    sess3.streak = 7
    misses_before3, streak_before3 = sess3.misses, sess3.streak
    mutant_result = sess3.judge_hit(stray_lane, stray_time, velocity=100)
    mutant_broke_invariant = not (mutant_result is None and sess3.misses == misses_before3
                                   and sess3.streak == streak_before3)
    check("RED (after mutation): swallow-vs-miss bug is now detectable",
          mutant_broke_invariant,
          f"mutant_result={mutant_result} misses={sess3.misses} streak={sess3.streak}")
    Session.judge_hit = _orig_judge_hit  # RESTORE
    sess4 = Session(lane_notes=lane_notes2, lane_visible=lane_visible2, auto_kick=False,
                    kick_lane_idx=kick_idx2, input_latency_ms=0,
                    song_duration=song_duration_from_lanes(lane_notes2))
    sess4.streak = 7
    restored_result = sess4.judge_hit(stray_lane, stray_time, velocity=100)
    check("GREEN (restored): swallow behaviour back to spec",
          restored_result is None and sess4.misses == 0 and sess4.streak == 7)

    # ---- 2c. Grade thresholds (>=95 S / >=85 A / >=70 B / >=50 C / else D)
    print("\n2c) grade thresholds")
    boundary_checks = [
        (95.0, "S"), (94.9, "A"), (85.0, "A"), (84.9, "B"),
        (70.0, "B"), (69.9, "C"), (50.0, "C"), (49.9, "D"), (0.0, "D"),
    ]
    all_ok = True
    for acc, expected in boundary_checks:
        letter, _ = compute_grade(acc)
        ok = letter == expected
        all_ok = all_ok and ok
        print(f"  acc={acc:>5} -> {letter} (expected {expected}) {'OK' if ok else 'MISMATCH'}")
    check("GREEN (before mutation): all grade boundaries land on the documented letter", all_ok)

    _orig_grades = GRADES[:]
    GRADES[:] = [(90, "S", "FORGED")] + GRADES[1:]   # MUTATE: S now needs 90 not 95
    letter_94, _ = compute_grade(94.0)
    mutation_detected = letter_94 != "A"   # spec says 94 should be A; mutant now returns S
    check("RED (after mutation): S-threshold shift (95->90) is now detectable at acc=94",
          mutation_detected, f"acc=94 -> {letter_94} (spec expects A)")
    GRADES[:] = _orig_grades   # RESTORE
    letter_94_restored, _ = compute_grade(94.0)
    check("GREEN (restored): acc=94 -> A again", letter_94_restored == "A")

    # -----------------------------------------------------------------
    # 3) parse_rlrr on a REAL .rlrr file on disk
    # -----------------------------------------------------------------
    print()
    print("=" * 70)
    print("3) parse_rlrr ON A REAL .rlrr FILE")
    print("=" * 70)
    import os
    search_roots = [
        os.path.join(os.path.expanduser("~"), "OneDrive", "Desktop", "Paradiddle Songs"),
        os.path.dirname(os.path.abspath(__file__)),
    ]
    found_file = None
    for root in search_roots:
        hits = glob.glob(root + r"\**\*.rlrr", recursive=True)
        if hits:
            found_file = hits[0]
            break
    if found_file is None:
        check("found a real .rlrr file to parse", False, "none found under search roots")
    else:
        print(f"  file: {found_file}")
        with open(found_file, "rb") as f:
            raw = f.read()
        text = decode_chart_bytes(raw)
        try:
            parsed = parse_rlrr(text)
            print(f"  meta.title={parsed['meta']['title']!r} "
                  f"meta.artist={parsed['meta']['artist']!r}")
            print(f"  instruments={len(parsed['instruments'])} "
                  f"events={len(parsed['events'])} warnings={parsed['warnings']}")
            check("parse_rlrr returned events for a real chart", len(parsed["events"]) > 0)
            check("parse_rlrr returned instruments for a real chart",
                  len(parsed["instruments"]) > 0)
            # exercise routing + lane building on the real chart too
            real_resolved = resolve_routing(parsed, default_layout(), None)
            real_lanes = build_lane_notes(parsed, real_resolved)
            real_duration = song_duration_from_lanes(real_lanes)
            print(f"  routed lane note counts: "
                  f"{[len(l) for l in real_lanes]} duration={real_duration:.2f}s")
            check("resolve_routing + build_lane_notes work on the real chart",
                  sum(len(l) for l in real_lanes) > 0)
        except ValueError as e:
            check("parse_rlrr on a real .rlrr file", False, f"raised {e!r}")

    # -----------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------
    print()
    print("=" * 70)
    if FAILURES:
        print(f"SMOKE RESULT: FAIL -- {len(FAILURES)} check(s) failed: {FAILURES}")
        sys.exit(1)
    else:
        print("SMOKE RESULT: PASS -- all checks green")
        sys.exit(0)
