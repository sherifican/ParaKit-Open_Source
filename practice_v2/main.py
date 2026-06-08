#!/usr/bin/env python3
"""Pygame-CE entry point for ParaKit's new Practice mini-game."""

from __future__ import annotations

import dataclasses
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

# Import pygame at module level so helpers can reference it.
try:
    import pygame

    _HAS_PYGAME = True
except Exception:  # pragma: no cover
    pygame = None  # type: ignore[assignment]
    _HAS_PYGAME = False

# ── ParaKit theme colours ───────────────────────────────────────────────
PASTEL_PURPLE = (0xC4, 0xB5, 0xFD)
PARAKIT_PURPLE = (0x7C, 0x3A, 0xED)
MAGENTA = (0xFF, 0x69, 0xB4)

# ── Lane spec (preserved from ParaKit v4.4.5x) ──────────────────────────
LANE_COLORS_HEX = [
    "#00e5ff",  # 0 Hi-Hat
    "#ff8c00",  # 1 Crash
    "#e63946",  # 2 Snare
    "#1a3a8f",  # 3 Tom 1
    "#2e8b57",  # 4 Tom 2
    "#7b2d8b",  # 5 Tom 3
    "#ffd700",  # 6 Ride
    "#ff69b4",  # 7 Kick
]

LANE_SHAPES = [
    "circle",  # Hi-Hat
    "circle",  # Crash
    "bar",     # Snare
    "bar",     # Tom 1
    "bar",     # Tom 2
    "bar",     # Tom 3
    "circle",  # Ride
    "kick",    # Kick
]

CYMBAL_LANES = {0, 1, 6}  # Hi-Hat, Crash, Ride

LANE_NAMES = [
    "HI-HAT",
    "CRASH",
    "SNARE",
    "TOM 1",
    "TOM 2",
    "TOM 3",
    "RIDE",
    "KICK",
]

# MIDI lane mapping (mirrors ParaKit parent VIZ_LANES_MIDI_IN)
VIZ_LANES_MIDI_IN: dict[str, set[int]] = {
    "Hi-Hat": {42, 22, 23, 44},
    "Open Hi-Hat": {46, 26, 21},
    "Crash": {49, 52, 55, 57},
    "Snare": {38, 40, 37},
    "Tom 1": {48, 50},
    "Tom 2": {45, 47},
    "Tom 3": {43, 58},
    "Ride": {51, 53, 59},
    "Kick": {36, 35},
}

# ── Debug / stub flags ──────────────────────────────────────────────────
DEBUG_STUB_MIDI = False

# Grade flash colours
GRADE_PERFECT_COLOR = (0x00, 0xFF, 0x88)
GRADE_EARLY_COLOR = PASTEL_PURPLE
GRADE_LATE_COLOR = PASTEL_PURPLE
GRADE_MISS_COLOR = (0xE9, 0x45, 0x60)

# Timing windows (seconds)
PERFECT_WINDOW_S = 0.035
HIT_WINDOW_S = 0.075
MISS_PAST_S = 0.120

# HUD constants
PANEL_BG = (0x0D, 0x0D, 0x1A)
PANEL_ALPHA = int(0.70 * 255)
PANEL_BORDER = PARAKIT_PURPLE
PANEL_RADIUS = 8
PANEL_PAD = 10
STATS_LINE_H = 22
FLASH_LINE_H = 24

# Combo
COMBO_MIN_STREAK = 5
MILESTONE_PULSE_S = 0.30
COMBO_PARTICLE_COUNT = 24

# Progress bar
PROGRESS_BAR_H = 4
PROGRESS_BAR_MARGIN_BOTTOM = 24
PROGRESS_BAR_MARGIN_SIDE = 24


# ── Data structures ─────────────────────────────────────────────────────
@dataclasses.dataclass
class Note:
    time: float
    lane: int
    hit: bool = False
    grade: str = ""  # "perfect", "early", "late", "miss", or ""
    velocity: int = 80  # MIDI velocity (chart notes default to 80)


@dataclasses.dataclass
class GradeFlash:
    lane: int
    text: str
    color: tuple[int, int, int]
    start_time: float
    duration: float = 0.35
    bump_duration: float = 0.10


@dataclasses.dataclass
class GameState:
    # Config-derived
    lane_notes: list[list[Note]]
    lane_visible: list[bool]
    auto_kick: bool
    fall_time_secs: float
    offset_secs: float
    input_latency_ms: float
    keybinds: dict[int, int]  # pygame_key_const -> lane_idx
    all_notes_as_bars: bool
    kick_full_line: bool
    note_size_scale: float
    song_duration: float
    session_latency_ms: float
    session_midi_name: str
    session_auto_kick: bool
    song_display_name: str = ""

    # MIDI runtime state
    midi_hh_pedal_pos: int = 127
    midi_hh_mode: str = "auto"
    midi_first_cc4_seen: bool = False
    midi_open_lane_enabled: bool = False
    midi_user_lane_overrides: dict[str, list[int]] = dataclasses.field(
        default_factory=dict
    )
    last_hit_velocity: list[int] = dataclasses.field(
        default_factory=lambda: [80] * 8
    )

    # Runtime
    song_time: float = 0.0
    start_wall_time: float = 0.0
    start_song_time: float = 0.0
    started: bool = False
    ended: bool = False

    # Stats
    hits: int = 0
    misses: int = 0
    streak: int = 0
    best_streak: int = 0
    perfect_count: int = 0
    early_count: int = 0
    late_count: int = 0
    miss_count: int = 0

    # Combo milestone
    last_milestone_streak: int = 0
    milestone_pulse_start: float = -1.0

    # Results data
    hit_timing_offsets: list[float] = dataclasses.field(default_factory=list)
    # ms, negative = early, positive = late

    grade_flashes: list[GradeFlash] = dataclasses.field(default_factory=list)
    last_lane_hit_time: list[float] = dataclasses.field(
        default_factory=lambda: [0.0] * 8
    )

    # Pause / settings / compact mode
    paused: bool = False
    compact_mode: bool = False
    pause_start_time: float = 0.0
    settings_rects: dict[str, pygame.Rect] = dataclasses.field(
        default_factory=dict
    )
    playfield_surf: Any | None = None
    bpm: float = 120.0
    beat_grid: bool = False

    # Audio-clock sync (drift fix)
    audio_start_offset_secs: float = 0.0
    last_known_song_time: float = 0.0
    song_time_anchor: float = 0.0

    # Scrubbing UI state
    scrub_knob_rect: Any = None
    scrub_bar_rect: Any = None
    scrub_dragging: bool = False

    # Unpause countdown
    unpause_countdown_enabled: bool = True
    countdown_remaining_secs: float = 0.0
    countdown_start_wall_time: float = 0.0

    # Audio track switching
    audio_mix_path: str = ""
    audio_drum_path: str = ""
    audio_track: str = "mix"  # "mix" or "drums"

    # Timing fields (play_wall_time / pause_wall_time are legacy, unused)
    play_wall_time: float = 0.0      # perf_counter() at last play() call
    pause_wall_time: float = 0.0     # perf_counter() at pause moment

    def accuracy_pct(self) -> float:
        total = self.hits + self.misses
        if total == 0:
            return 0.0
        return round(self.hits / total * 100, 1)

    def results(self, exit_reason: str, duration_secs: float) -> dict[str, Any]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "best_streak": self.best_streak,
            "accuracy_pct": self.accuracy_pct(),
            "perfect_count": self.perfect_count,
            "early_count": self.early_count,
            "late_count": self.late_count,
            "miss_count": self.miss_count,
            "exit_reason": exit_reason,
            "duration_secs": round(max(0.0, float(duration_secs)), 3),
        }


# ── Config / results helpers ────────────────────────────────────────────
def _load_config(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("Practice config must be a JSON object.")
    return data


def _write_results(config: dict[str, Any], results: dict[str, Any]) -> None:
    results_path = config.get("results_path")
    if not results_path:
        return
    path = Path(results_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, indent=2), encoding="utf-8")


# ── Keybind parsing ─────────────────────────────────────────────────────
def _keysym_to_pygame(keysym: str) -> int:
    keysym = keysym.lower().strip()
    if len(keysym) == 1 and keysym.isalpha():
        return getattr(pygame, f"K_{keysym}", pygame.K_UNKNOWN)
    mapping = {
        "space": pygame.K_SPACE,
        "return": pygame.K_RETURN,
        "enter": pygame.K_RETURN,
        "tab": pygame.K_TAB,
        "escape": pygame.K_ESCAPE,
        "up": pygame.K_UP,
        "down": pygame.K_DOWN,
        "left": pygame.K_LEFT,
        "right": pygame.K_RIGHT,
        "shift": pygame.K_LSHIFT,
        "ctrl": pygame.K_LCTRL,
        "alt": pygame.K_LALT,
    }
    return mapping.get(keysym, pygame.K_UNKNOWN)


def _parse_keybinds(raw: Any) -> dict[int, int]:
    result: dict[int, int] = {}
    if not isinstance(raw, list):
        return result
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        lane_idx = int(item[0])
        keysym = str(item[1])
        key = _keysym_to_pygame(keysym)
        if key != pygame.K_UNKNOWN:
            result[key] = lane_idx
    return result


# ── Colour helpers ──────────────────────────────────────────────────────
def _hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    h = hex_str.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _lerp_color(
    c1: tuple[int, int, int], c2: tuple[int, int, int], t: float
) -> tuple[int, int, int]:
    return (
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t),
    )


def _lighten(
    color: tuple[int, int, int], amount: float = 0.3
) -> tuple[int, int, int]:
    return tuple(min(255, int(c + (255 - c) * amount)) for c in color)


def _format_time(seconds: float) -> str:
    minutes = max(0, int(seconds) // 60)
    secs = max(0, int(seconds) % 60)
    return f"{minutes}:{secs:02d}"


# ── MIDI helpers ────────────────────────────────────────────────────────
def _lane_index_by_name(name: str) -> int | None:
    name_upper = name.upper()
    for i, ln in enumerate(LANE_NAMES):
        if ln == name_upper:
            return i
    return None


def _midi_hh_mode_effective(hh_mode: str, first_cc4_seen: bool) -> str:
    if hh_mode == "auto":
        return "cc4" if first_cc4_seen else "note"
    return hh_mode


def _midi_note_to_lane(
    note: int,
    overrides: dict[str, list[int]],
    hh_pedal_pos: int,
    hh_mode: str,
    first_cc4_seen: bool,
    open_lane_enabled: bool,
) -> int | None:
    """Map a MIDI note number to a lane index, matching parent logic."""
    # User overrides for non-hi-hat lanes first
    for lane_name, override_notes in overrides.items():
        if lane_name in ("Hi-Hat", "Open Hi-Hat"):
            continue
        if note in override_notes:
            idx = _lane_index_by_name(lane_name)
            if idx is not None:
                return idx

    hihat_open_notes = {46, 26, 21}
    hihat_close_notes = {42, 22, 23, 44}

    hihat_override = overrides.get("Hi-Hat")
    open_hihat_override = overrides.get("Open Hi-Hat")
    if hihat_override:
        try:
            hihat_close_notes = set(hihat_override)
        except Exception:
            pass
    if open_hihat_override:
        try:
            hihat_open_notes = set(open_hihat_override)
        except Exception:
            pass

    is_hihat = note in (hihat_open_notes | hihat_close_notes)
    if is_hihat:
        effective_mode = _midi_hh_mode_effective(hh_mode, first_cc4_seen)
        if effective_mode == "cc4":
            is_open = hh_pedal_pos < 64
        else:
            is_open = note in hihat_open_notes
        if open_lane_enabled and is_open:
            open_idx = _lane_index_by_name("Open Hi-Hat")
            if open_idx is not None:
                return open_idx
        return _lane_index_by_name("Hi-Hat")

    for lane_name, notes in VIZ_LANES_MIDI_IN.items():
        if lane_name in ("Hi-Hat", "Open Hi-Hat"):
            continue
        if lane_name in overrides:
            continue
        if note in notes:
            return _lane_index_by_name(lane_name)

    return None


def _velocity_flash_scale(velocity: int) -> tuple[float, float]:
    """Return (alpha_multiplier, size_multiplier) for a given hit velocity.

    Ghost  ≤ 40  → subtle flash
    Normal 41-99 → standard flash
    Accent ≥100  → intense flash
    """
    if velocity <= 40:
        return (0.5, 0.85)
    if velocity >= 100:
        return (1.4, 1.2)
    return (1.0, 1.0)


def _init_midi_input(config: dict[str, Any]) -> tuple[Any, str]:
    """Open a pygame.midi input device from config. Returns (Input, name)."""
    try:
        import pygame.midi as _pmidi
        _pmidi.init()
    except Exception as exc:
        print(f"[MIDI] pygame.midi init failed: {exc}")
        return (None, "(init failed)")

    port_name = config.get("midi_port_name") or ""
    device_idx = None
    try:
        count = _pmidi.get_count()
        for i in range(count):
            info = _pmidi.get_device_info(i)
            if info is None:
                continue
            # info = (interf, name, input, output, opened)
            if info[2]:  # is input
                name_str = info[1].decode("utf-8", "replace")
                if port_name and port_name.lower() in name_str.lower():
                    device_idx = i
                    break
        if device_idx is None:
            # fallback: first available input
            for i in range(count):
                info = _pmidi.get_device_info(i)
                if info and info[2]:
                    device_idx = i
                    break
    except Exception as exc:
        print(f"[MIDI] device enumeration failed: {exc}")

    if device_idx is None:
        return (None, "(no device)")

    try:
        midi_in = _pmidi.Input(device_idx)
        info = _pmidi.get_device_info(device_idx)
        actual_name = info[1].decode("utf-8", "replace") if info else "(unknown)"
        print(f"[MIDI] opened input: {actual_name} (idx={device_idx})")
        return (midi_in, actual_name)
    except Exception as exc:
        print(f"[MIDI] failed to open device {device_idx}: {exc}")
        return (None, "(open failed)")


def _drain_midi_input(
    midi_in: Any,
    state: GameState,
) -> None:
    """Poll MIDI input and route note-ons to lane hits."""
    if midi_in is None:
        return
    try:
        events = midi_in.read(1024)
    except Exception:
        return
    for event in events:
        # event format: [[status, data1, data2, data3], timestamp]
        data = event[0]
        status = data[0]
        d1 = data[1]
        d2 = data[2]

        # Control Change (0xB0–0xBF)
        if 0xB0 <= status <= 0xBF:
            if d1 == 4:  # CC4 = hi-hat pedal
                state.midi_hh_pedal_pos = d2
                if state.midi_hh_mode == "auto" and not state.midi_first_cc4_seen:
                    state.midi_first_cc4_seen = True
            continue

        # Note On (0x90–0x9F) with velocity > 0
        if 0x90 <= status <= 0x9F and d2 > 0:
            lane = _midi_note_to_lane(
                note=d1,
                overrides=state.midi_user_lane_overrides,
                hh_pedal_pos=state.midi_hh_pedal_pos,
                hh_mode=state.midi_hh_mode,
                first_cc4_seen=state.midi_first_cc4_seen,
                open_lane_enabled=state.midi_open_lane_enabled,
            )
            if lane is not None and state.lane_visible[lane]:
                hit_time = state.song_time + (state.input_latency_ms / 1000.0)
                grade = _process_lane_hit(state, lane, hit_time, velocity=d2)
                if grade == "perfect":
                    _spawn_flash(state, lane, "PERFECT", GRADE_PERFECT_COLOR)
                elif grade == "early":
                    _spawn_flash(state, lane, "EARLY", GRADE_EARLY_COLOR)
                elif grade == "late":
                    _spawn_flash(state, lane, "LATE", GRADE_LATE_COLOR)


def _stub_midi_input(state: GameState, t: float) -> None:
    """Generate synthetic MIDI hits for testing without hardware."""
    if int(t * 10) % 60 != 0 or int(t * 10) == 0:
        return
    lane = int(t) % 7
    if not state.lane_visible[lane]:
        return
    velocity = 80 + int(40 * math.sin(t * 3))
    hit_time = state.song_time + (state.input_latency_ms / 1000.0)
    grade = _process_lane_hit(state, lane, hit_time, velocity=velocity)
    if grade == "perfect":
        _spawn_flash(state, lane, "PERFECT", GRADE_PERFECT_COLOR)
    elif grade == "early":
        _spawn_flash(state, lane, "EARLY", GRADE_EARLY_COLOR)
    elif grade == "late":
        _spawn_flash(state, lane, "LATE", GRADE_LATE_COLOR)


# ── Background ──────────────────────────────────────────────────────────
def _make_bg_gradient(width: int, height: int) -> Any:
    top = _hex_to_rgb("#080810")
    bottom = _hex_to_rgb("#0d0d1a")
    col = pygame.Surface((1, height))
    for y in range(height):
        t = y / max(height - 1, 1)
        c = _lerp_color(top, bottom, t)
        col.set_at((0, y), c)
    return pygame.transform.scale(col, (width, height))


# ── Drawing helpers ─────────────────────────────────────────────────────
def _draw_falling_note(
    screen: Any,
    x_center: int,
    y: int,
    lane_idx: int,
    lane_w: float,
    size_scale: float = 1.0,
    icon: Any | None = None,
    all_bars: bool = False,
    color: tuple[int, int, int] = (200, 200, 200),
) -> None:
    # Compensate for empirical icon-centering offset:
    # Solid-pixel bbox measurement shows circle icons (hi_hat, crash, ride)
    # center at y=64 of 128px image (exactly centered), while bar icons
    # (snare, tom1-3, kick) center at y=61 — exactly 3px above geometric
    # center. Shift bar icons down by 3/128 * target_h so all visual
    # centers land on the hit line.
    is_bar = LANE_SHAPES[lane_idx] in ("bar", "kick")
    is_cymbal = lane_idx in CYMBAL_LANES

    if all_bars and is_cymbal:
        bar_w = max(8, int(lane_w * 0.18 * size_scale))
        bar_h = max(4, int(bar_w * 0.5))
        bar_offset = round(bar_h * 3 / 128)
        rect = pygame.Rect(x_center - bar_w // 2, y - bar_h // 2 + bar_offset, bar_w, bar_h)
        pygame.draw.rect(screen, color, rect, border_radius=max(2, bar_h // 3))
    elif icon is not None:
        target_w = int(lane_w * 0.18 * size_scale)
        orig_w, orig_h = icon.get_size()
        target_h = int(target_w * orig_h / orig_w)
        bar_offset = round(target_h * 3 / 128) if is_bar else 0
        scaled = pygame.transform.smoothscale(icon, (target_w, target_h))
        rect = scaled.get_rect(center=(x_center, y + bar_offset))
        screen.blit(scaled, rect)
    else:
        # Fallback: small outline circle — already centered, no offset needed
        radius = max(3, int(lane_w * 0.06 * size_scale))
        pygame.draw.circle(screen, color, (x_center, y), radius, 2)


def _draw_receptor(
    screen: Any,
    x_center: int,
    y: int,
    lane_idx: int,
    lane_w: float,
    color: tuple[int, int, int],
    state: str = "idle",
    t: float = 0.0,
    hit_velocity: int = 80,
    time_since_hit: float = 1.0,
    icon: Any | None = None,
    size_scale: float = 1.0,
    all_bars: bool = False,
) -> None:
    alpha_mult, size_mult = _velocity_flash_scale(hit_velocity)

    # Compensate for empirical icon-centering offset:
    # Solid-pixel bbox measurement shows circle icons (hi_hat, crash, ride)
    # center at y=64 of 128px image (exactly centered), while bar icons
    # (snare, tom1-3, kick) center at y=61 — exactly 3px above geometric
    # center. Shift bar icons down by 3/128 * target_h so all visual
    # centers land on the hit line.
    is_bar = LANE_SHAPES[lane_idx] in ("bar", "kick")
    is_cymbal = lane_idx in CYMBAL_LANES
    y_off = 0

    if all_bars and is_cymbal:
        bar_w = max(8, int(lane_w * 0.20 * size_scale * size_mult))
        bar_h = max(4, int(bar_w * 0.5))
        bar_offset = round(bar_h * 3 / 128)
        rect = pygame.Rect(x_center - bar_w // 2, y - bar_h // 2 + bar_offset, bar_w, bar_h)
        pygame.draw.rect(screen, color, rect, border_radius=max(2, bar_h // 3))
        inner = rect.inflate(-3, -3)
        pygame.draw.rect(screen, (255, 255, 255), inner, 1, border_radius=max(1, bar_h // 3 - 1))
        y_off = bar_offset
    elif icon is not None:
        target_w = int(lane_w * 0.20 * size_scale)
        orig_w, orig_h = icon.get_size()
        target_h = int(target_w * orig_h / orig_w)
        bar_offset = round(target_h * 3 / 128) if is_bar else 0
        scaled = pygame.transform.smoothscale(icon, (target_w, target_h))
        rect = scaled.get_rect(center=(x_center, y + bar_offset))
        screen.blit(scaled, rect)
        y_off = bar_offset
    else:
        # Fallback circle outline — already centered, no offset needed
        radius = max(4, int(lane_w * 0.08 * size_scale))
        pygame.draw.circle(screen, color, (x_center, y), radius, 2)
        pygame.draw.circle(screen, (255, 255, 255), (x_center, y), max(1, radius - 2), 1)

    # Brief input-triggered glow — small + subtle
    if 0 <= time_since_hit < 0.08:
        alpha = int(120 * (1.0 - time_since_hit / 0.08))
        if all_bars and is_cymbal:
            glow_w = int(lane_w * 0.14 * size_scale)
            glow_h = int(glow_w * 0.5)
            glow_surf = pygame.Surface((glow_w, glow_h), pygame.SRCALPHA)
            pygame.draw.rect(glow_surf, (*color, alpha), glow_surf.get_rect(), border_radius=max(2, glow_h // 3))
            screen.blit(glow_surf, (x_center - glow_w // 2, y - glow_h // 2 + y_off))
        else:
            glow_radius = int(lane_w * 0.08 * size_scale)
            glow_surf = pygame.Surface((glow_radius * 2, glow_radius * 2), pygame.SRCALPHA)
            pygame.draw.circle(glow_surf, (*color, alpha), (glow_radius, glow_radius), glow_radius)
            screen.blit(glow_surf, (x_center - glow_radius, y - glow_radius))

    # Subtle colored ring on successful hit — keep but smaller
    if state == "just_hit":
        flash_alpha = min(140, int(100 * alpha_mult))
        glow_radius = int(lane_w * 0.08 * size_mult * size_scale)
        glow_surf = pygame.Surface((glow_radius * 2, glow_radius * 2), pygame.SRCALPHA)
        pygame.draw.circle(
            glow_surf, (*color, flash_alpha), (glow_radius, glow_radius), glow_radius
        )
        screen.blit(glow_surf, (x_center - glow_radius, y - glow_radius))


# ── HUD drawing ─────────────────────────────────────────────────────────
def _draw_rounded_panel(
    surface: Any, width: int, height: int
) -> Any:
    """Create a semi-transparent rounded panel surface."""
    panel = pygame.Surface((width, height), pygame.SRCALPHA)
    pygame.draw.rect(
        panel,
        (*PANEL_BG, PANEL_ALPHA),
        (0, 0, width, height),
        border_radius=PANEL_RADIUS,
    )
    pygame.draw.rect(
        panel,
        (*PANEL_BORDER, int(0.5 * 255)),
        (0, 0, width, height),
        1,
        border_radius=PANEL_RADIUS,
    )
    return panel


def _draw_text_line(
    screen: Any,
    text: str,
    font: Any,
    color: tuple[int, int, int],
    x: int,
    y: int,
    align_left: bool = True,
) -> None:
    surf = font.render(text, True, color)
    if align_left:
        rect = surf.get_rect(midleft=(x, y))
    else:
        rect = surf.get_rect(midright=(x, y))
    screen.blit(surf, rect)


def _draw_stats_panel(
    screen: Any,
    state: GameState,
    font_bold: Any,
    font_regular: Any,
    flash_font: Any,
) -> None:
    """Top-left stats + grade-flash dock."""
    panel_w = 280
    max_flashes = 3
    panel_h = PANEL_PAD + 5 * STATS_LINE_H + 8 + PANEL_PAD + max_flashes * FLASH_LINE_H + PANEL_PAD
    panel = _draw_rounded_panel(screen, panel_w, panel_h)
    panel_x = 24
    panel_y = 24
    screen.blit(panel, (panel_x, panel_y))

    x = panel_x + PANEL_PAD
    y = panel_y + PANEL_PAD + STATS_LINE_H // 2

    # Streak
    _draw_text_line(screen, f"Streak: {state.streak}", font_bold, PASTEL_PURPLE, x, y)
    y += STATS_LINE_H
    # Best
    _draw_text_line(screen, f"Best: {state.best_streak}", font_regular, (255, 255, 255), x, y)
    y += STATS_LINE_H
    # Hits
    _draw_text_line(screen, f"Hits: {state.hits}", font_regular, (255, 255, 255), x, y)
    y += STATS_LINE_H
    # Misses
    _draw_text_line(screen, f"Misses: {state.misses}", font_regular, GRADE_MISS_COLOR, x, y)
    y += STATS_LINE_H
    # Accuracy
    acc = state.accuracy_pct()
    if acc >= 95:
        acc_color = PASTEL_PURPLE
    elif acc >= 85:
        acc_color = (0xFF, 0x8C, 0x00)
    else:
        acc_color = GRADE_MISS_COLOR
    _draw_text_line(screen, f"Accuracy: {acc:.1f}%", font_regular, acc_color, x, y)
    y += STATS_LINE_H + 4

    # Divider
    pygame.draw.line(
        screen,
        (*PANEL_BORDER, int(0.4 * 255)),
        (x, y),
        (panel_x + panel_w - PANEL_PAD, y),
        1,
    )
    y += 10

    # Grade flash stack (most recent on top)
    active = [f for f in state.grade_flashes
              if state.song_time - f.start_time < f.duration]
    active.sort(key=lambda f: f.start_time, reverse=True)
    for idx, flash in enumerate(active[:max_flashes]):
        flash_y = y + idx * FLASH_LINE_H + FLASH_LINE_H // 2
        _draw_grade_flash_mini(screen, flash, state.song_time, x, flash_y, flash_font)


def _draw_grade_flash_mini(
    screen: Any,
    flash: GradeFlash,
    now: float,
    x: int,
    y: int,
    font: Any,
) -> None:
    elapsed = now - flash.start_time
    if elapsed >= flash.duration:
        return
    alpha = int(255 * (1.0 - elapsed / flash.duration))
    surf = font.render(flash.text, True, flash.color)
    surf.set_alpha(alpha)
    rect = surf.get_rect(midleft=(x, y))
    screen.blit(surf, rect)


def _draw_session_panel(
    screen: Any,
    state: GameState,
    font_regular: Any,
) -> None:
    """Top-right interactive settings panel."""
    panel_w = 300
    settings = [
        ("Auto-Kick", "auto_kick", "1"),
        ("All Notes as Bars", "all_notes_as_bars", "2"),
        ("Kick Line", "kick_full_line", "3"),
        ("Beat Grid", "beat_grid", "4"),
        ("Compact Mode", "compact_mode", "5"),
        ("Unpause Countdown", "unpause_countdown_enabled", "6"),
    ]
    row_h = 28
    panel_h = PANEL_PAD + (len(settings) + 3) * row_h + PANEL_PAD
    panel = _draw_rounded_panel(screen, panel_w, panel_h)
    sw, sh = screen.get_size()
    panel_x = sw - 24 - panel_w
    panel_y = 24
    screen.blit(panel, (panel_x, panel_y))

    state.settings_rects.clear()
    x = panel_x + PANEL_PAD
    y = panel_y + PANEL_PAD + row_h // 2

    for label, attr, key in settings:
        val = getattr(state, attr)
        on_off = "[ON]" if val else "[OFF]"
        color = PASTEL_PURPLE if val else (0x88, 0x88, 0x88)

        key_surf = font_regular.render(f"[{key}]", True, (0xAA, 0xAA, 0xBB))
        key_rect = key_surf.get_rect(midleft=(x, y))
        screen.blit(key_surf, key_rect)

        label_surf = font_regular.render(label, True, (255, 255, 255))
        label_rect = label_surf.get_rect(midleft=(x + 50, y))
        screen.blit(label_surf, label_rect)

        val_surf = font_regular.render(on_off, True, color)
        val_rect = val_surf.get_rect(midright=(panel_x + panel_w - PANEL_PAD, y))
        screen.blit(val_surf, val_rect)

        row_rect = pygame.Rect(panel_x, y - row_h // 2, panel_w, row_h)
        state.settings_rects[attr] = row_rect

        y += row_h

    # Audio track row (toggle with [7] or click)
    key_surf = font_regular.render("[7]", True, (0xAA, 0xAA, 0xBB))
    key_rect = key_surf.get_rect(midleft=(x, y))
    screen.blit(key_surf, key_rect)
    track_label = f"Audio Track"
    track_surf = font_regular.render(track_label, True, (255, 255, 255))
    track_rect = track_surf.get_rect(midleft=(x + 50, y))
    screen.blit(track_surf, track_rect)
    track_val = state.audio_track.upper()
    track_val_surf = font_regular.render(track_val, True, PASTEL_PURPLE)
    track_val_rect = track_val_surf.get_rect(midright=(panel_x + panel_w - PANEL_PAD, y))
    screen.blit(track_val_surf, track_val_rect)
    row_rect = pygame.Rect(panel_x, y - row_h // 2, panel_w, row_h)
    state.settings_rects["audio_track"] = row_rect
    y += row_h

    # Note size row (read-only, adjusted with +/-)
    key_surf = font_regular.render("[+/-]", True, (0xAA, 0xAA, 0xBB))
    key_rect = key_surf.get_rect(midleft=(x, y))
    screen.blit(key_surf, key_rect)
    size_label = f"Note Size: {state.note_size_scale:.1f}x"
    size_surf = font_regular.render(size_label, True, (255, 255, 255))
    size_rect = size_surf.get_rect(midleft=(x + 50, y))
    screen.blit(size_surf, size_rect)
    y += row_h

    # Fall time row (read-only, adjusted with [/])
    key_surf = font_regular.render("[/]", True, (0xAA, 0xAA, 0xBB))
    key_rect = key_surf.get_rect(midleft=(x, y))
    screen.blit(key_surf, key_rect)
    fall_label = f"Fall Time: {state.fall_time_secs:.1f}s"
    fall_surf = font_regular.render(fall_label, True, (255, 255, 255))
    fall_rect = fall_surf.get_rect(midleft=(x + 50, y))
    screen.blit(fall_surf, fall_rect)


def _draw_combo_counter(
    screen: Any,
    state: GameState,
    combo_label_font: Any,
    combo_number_font: Any,
    hit_y: float,
    t: float,
) -> None:
    """Center-bottom combo counter with particle ring."""
    if state.streak < COMBO_MIN_STREAK:
        return

    center_x = screen.get_width() // 2
    center_y = int(hit_y - 100)

    # Milestone scale pulse
    scale = 1.0
    if state.milestone_pulse_start >= 0:
        pulse_elapsed = state.song_time - state.milestone_pulse_start
        if pulse_elapsed < MILESTONE_PULSE_S:
            scale = 1.0 + 0.3 * math.sin(
                (pulse_elapsed / MILESTONE_PULSE_S) * math.pi
            )

    # Particle ring
    for i in range(COMBO_PARTICLE_COUNT):
        angle = (t * 2 + i * (2 * math.pi / COMBO_PARTICLE_COUNT)) % (2 * math.pi)
        rx = 60 * math.cos(angle)
        ry = 30 * math.sin(angle)
        dot_x = center_x + rx
        dot_y = center_y + ry
        alpha = int(128 + 64 * math.sin(t * 3 + i))
        dot_surf = pygame.Surface((5, 5), pygame.SRCALPHA)
        pygame.draw.circle(dot_surf, (*PASTEL_PURPLE, alpha), (2, 2), 2)
        screen.blit(dot_surf, (int(dot_x) - 2, int(dot_y) - 2))

    # Glow behind number
    glow_text = f"{state.streak}x"
    glow_surf = combo_number_font.render(glow_text, True, PASTEL_PURPLE)
    if scale != 1.0:
        gw = int(glow_surf.get_width() * scale)
        gh = int(glow_surf.get_height() * scale)
        glow_surf = pygame.transform.scale(glow_surf, (gw, gh))
    glow_surf.set_alpha(128)
    glow_rect = glow_surf.get_rect(center=(center_x, center_y + 10))
    screen.blit(glow_surf, glow_rect)

    # Number
    num_surf = combo_number_font.render(glow_text, True, (255, 255, 255))
    if scale != 1.0:
        nw = int(num_surf.get_width() * scale)
        nh = int(num_surf.get_height() * scale)
        num_surf = pygame.transform.scale(num_surf, (nw, nh))
    num_rect = num_surf.get_rect(center=(center_x, center_y + 10))
    screen.blit(num_surf, num_rect)

    # COMBO label
    label_surf = combo_label_font.render("COMBO", True, (0xAA, 0xAA, 0xAA))
    label_rect = label_surf.get_rect(center=(center_x, center_y - 30))
    screen.blit(label_surf, label_rect)


def _draw_progress_bar(
    screen: Any,
    state: GameState,
    time_font: Any,
) -> None:
    """Bottom-edge progress bar with purple→magenta gradient fill."""
    bar_x = PROGRESS_BAR_MARGIN_SIDE
    bar_y = screen.get_height() - PROGRESS_BAR_MARGIN_BOTTOM
    bar_w = screen.get_width() - 2 * PROGRESS_BAR_MARGIN_SIDE

    # Background
    bg_surf = pygame.Surface((bar_w, PROGRESS_BAR_H), pygame.SRCALPHA)
    bg_surf.fill((*_hex_to_rgb("#1a1a2e"), int(0.4 * 255)))
    screen.blit(bg_surf, (bar_x, bar_y))

    # Fill
    if state.song_duration > 0:
        fill_ratio = min(1.0, max(0.0, state.song_time / state.song_duration))
        fill_w = int(bar_w * fill_ratio)
        if fill_w > 0:
            col = pygame.Surface((fill_w, 1))
            for i in range(fill_w):
                t = i / max(fill_w - 1, 1)
                c = _lerp_color(PASTEL_PURPLE, MAGENTA, t)
                col.set_at((i, 0), c)
            strip = pygame.transform.scale(col, (fill_w, PROGRESS_BAR_H))
            screen.blit(strip, (bar_x, bar_y))

    # Border
    pygame.draw.rect(
        screen,
        PARAKIT_PURPLE,
        (bar_x, bar_y, bar_w, PROGRESS_BAR_H),
        1,
        border_radius=2,
    )

    # Knob (only when paused)
    if state.paused:
        knob_x = int(bar_x + (state.song_time / state.song_duration) * bar_w) if state.song_duration > 0 else bar_x
        knob_y = int(bar_y + PROGRESS_BAR_H / 2)
        knob_radius = max(8, int(PROGRESS_BAR_H * 0.8))
        # Soft outer glow
        glow_surf = pygame.Surface((knob_radius * 4, knob_radius * 4), pygame.SRCALPHA)
        pygame.draw.circle(glow_surf, (*PASTEL_PURPLE, 60), (knob_radius * 2, knob_radius * 2), knob_radius * 2)
        screen.blit(glow_surf, (knob_x - knob_radius * 2, knob_y - knob_radius * 2))
        # Solid knob
        pygame.draw.circle(screen, PASTEL_PURPLE, (knob_x, knob_y), knob_radius)
        pygame.draw.circle(screen, (255, 255, 255), (knob_x, knob_y), knob_radius, 2)
        # Track rects for hit-testing
        state.scrub_knob_rect = pygame.Rect(
            knob_x - knob_radius * 2, knob_y - knob_radius * 2,
            knob_radius * 4, knob_radius * 4
        )
        state.scrub_bar_rect = pygame.Rect(bar_x, bar_y, bar_w, PROGRESS_BAR_H)
    else:
        state.scrub_knob_rect = None
        state.scrub_bar_rect = None

    # Time labels
    current_str = _format_time(state.song_time)
    total_str = _format_time(state.song_duration)
    _draw_text_line(screen, current_str, time_font, (255, 255, 255), bar_x, bar_y - 8)
    _draw_text_line(
        screen, total_str, time_font, (255, 255, 255), bar_x + bar_w, bar_y - 8, align_left=False
    )


def _draw_lane_labels(
    screen: Any,
    lane_w: float,
    hit_y: float,
    kick_full_line: bool = False,
    visible_lanes: list[int] | None = None,
    lane_to_idx: dict[int, int] | None = None,
) -> None:
    """Persistent lane-name labels at the bottom of each lane."""
    receptor_radius = lane_w * 0.09
    label_y = int(hit_y + receptor_radius + 14)
    label_font = pygame.font.SysFont("Segoe UI", max(18, min(32, int(lane_w * 0.12))), bold=True)
    lanes = visible_lanes if visible_lanes is not None else range(8)
    for lane_idx in lanes:
        if kick_full_line and lane_idx == 7:
            continue
        if lane_to_idx is not None and lane_idx not in lane_to_idx:
            continue
        x_center = int(lane_to_idx.get(lane_idx, lane_idx) * lane_w + lane_w / 2.0) if lane_to_idx else int(lane_idx * lane_w + lane_w / 2.0)
        surf = label_font.render(LANE_NAMES[lane_idx], True, (0x66, 0x66, 0x66))
        rect = surf.get_rect(center=(x_center, label_y))
        screen.blit(surf, rect)


# ── Game logic ──────────────────────────────────────────────────────────
def _build_lane_notes(raw_notes: Any) -> list[list[Note]]:
    lane_notes: list[list[Note]] = [[] for _ in range(8)]
    if not isinstance(raw_notes, list):
        return lane_notes
    for item in raw_notes:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        t = float(item[0])
        lane = int(item[1])
        vel = int(item[2]) if len(item) >= 3 else 80
        if 0 <= lane < 8:
            lane_notes[lane].append(Note(time=t, lane=lane, velocity=vel))
    for ln in lane_notes:
        ln.sort(key=lambda n: n.time)
    return lane_notes


def _song_duration_from_notes(lane_notes: list[list[Note]]) -> float:
    max_time = 0.0
    for notes in lane_notes:
        if notes:
            max_time = max(max_time, notes[-1].time)
    return max_time + 2.0 if max_time > 0 else 60.0


def _process_lane_hit(
    state: GameState, lane_idx: int, song_time: float, velocity: int = 80
) -> str | None:
    if not state.lane_visible[lane_idx]:
        return None
    # Always record input time for visual feedback, even if no note is hit
    state.last_lane_hit_time[lane_idx] = song_time
    state.last_hit_velocity[lane_idx] = velocity
    notes = state.lane_notes[lane_idx]
    best_note: Note | None = None
    best_diff = float("inf")
    for note in notes:
        if note.hit or note.grade == "miss":
            continue
        diff = song_time - note.time
        if abs(diff) <= HIT_WINDOW_S and abs(diff) < best_diff:
            best_diff = abs(diff)
            best_note = note
    if best_note is None:
        return None
    diff = song_time - best_note.time
    if abs(diff) <= PERFECT_WINDOW_S:
        grade = "perfect"
    elif diff < 0:
        grade = "early"
    else:
        grade = "late"
    best_note.hit = True
    best_note.grade = grade
    best_note.velocity = velocity
    offset_ms = (song_time - best_note.time) * 1000.0
    state.hit_timing_offsets.append(offset_ms)
    state.hits += 1
    state.streak += 1
    if state.streak > state.best_streak:
        state.best_streak = state.streak
    if grade == "perfect":
        state.perfect_count += 1
    elif grade == "early":
        state.early_count += 1
    else:
        state.late_count += 1
    # Milestone pulse
    if state.streak > 0 and state.streak % 10 == 0:
        if state.streak != state.last_milestone_streak:
            state.last_milestone_streak = state.streak
            state.milestone_pulse_start = song_time
    return grade


def _seek_song(state: GameState, new_time: float) -> None:
    """Jump to a new song time, resetting note hit states up to that point."""
    state.song_time = new_time
    state.last_known_song_time = new_time
    for lane_notes in state.lane_notes:
        for note in lane_notes:
            if note.time < new_time:
                note.hit = True
                note.grade = ""
            else:
                note.hit = False
                note.grade = ""
    # Reset stats for a fair practice session
    state.hits = 0
    state.misses = 0
    state.streak = 0
    state.best_streak = 0
    state.perfect_count = 0
    state.early_count = 0
    state.late_count = 0
    state.miss_count = 0
    state.hit_timing_offsets.clear()
    state.grade_flashes.clear()


def _apply_scrub_from_x(state: GameState, mouse_x: int) -> None:
    """Update state.song_time (preview only) based on mouse x position on the bar."""
    if state.scrub_bar_rect is None or state.song_duration <= 0:
        return
    rel_x = mouse_x - state.scrub_bar_rect.x
    rel_x = max(0, min(rel_x, state.scrub_bar_rect.width))
    ratio = rel_x / state.scrub_bar_rect.width
    state.song_time = ratio * state.song_duration
    state.last_known_song_time = state.song_time


def _commit_scrub(state: GameState) -> None:
    """Apply the scrubbed-to position to the audio and reset hit state for the new position."""
    new_time = state.song_time
    audio_seek = max(0.0, new_time - state.offset_secs)
    try:
        pygame.mixer.music.play(start=audio_seek)
        # Music auto-resumes — re-pause immediately since we're still in pause state
        pygame.mixer.music.pause()
    except Exception:
        pass
    state.audio_start_offset_secs = audio_seek
    state.song_time_anchor = audio_seek
    state.last_known_song_time = new_time
    # Reset hit state for notes after the new position
    for lane_notes in state.lane_notes:
        for note in lane_notes:
            if note.time < new_time:
                note.hit = True
                note.grade = ""
            else:
                note.hit = False
                note.grade = ""
    state.grade_flashes.clear()


def _toggle_audio_track(state: GameState) -> None:
    """Switch between mix and drum stem audio, preserving playback position."""
    mix_path = state.audio_mix_path
    drum_path = state.audio_drum_path
    if not mix_path or not drum_path:
        return
    new_track = "drums" if state.audio_track == "mix" else "mix"
    new_path = mix_path if new_track == "mix" else drum_path
    if not Path(new_path).is_file():
        return
    # Compute current audio file position
    anchor = max(0.0, state.song_time - state.offset_secs)
    try:
        pygame.mixer.music.load(new_path)
        pygame.mixer.music.play(start=anchor)
        state.audio_track = new_track
        state.audio_start_offset_secs = anchor
        state.song_time_anchor = anchor
    except Exception:
        pass


def _spawn_flash(state: GameState, lane_idx: int, text: str, color: tuple[int, int, int]) -> None:
    state.grade_flashes.append(
        GradeFlash(
            lane=lane_idx,
            text=text,
            color=color,
            start_time=state.song_time,
        )
    )


def _process_auto_kick(state: GameState, song_time: float) -> None:
    if not state.auto_kick:
        return
    if not state.lane_visible[7]:
        return
    for note in state.lane_notes[7]:
        if note.hit or note.grade == "miss":
            continue
        diff = song_time - note.time
        if abs(diff) <= HIT_WINDOW_S:
            note.hit = True
            note.grade = "perfect"
            state.hits += 1
            state.streak += 1
            if state.streak > state.best_streak:
                state.best_streak = state.streak
            state.perfect_count += 1
            state.last_lane_hit_time[7] = song_time
            if state.streak > 0 and state.streak % 10 == 0:
                if state.streak != state.last_milestone_streak:
                    state.last_milestone_streak = state.streak
                    state.milestone_pulse_start = song_time
            _spawn_flash(state, 7, "PERFECT", GRADE_PERFECT_COLOR)


def _process_misses(state: GameState, song_time: float) -> None:
    for lane_idx, notes in enumerate(state.lane_notes):
        if not state.lane_visible[lane_idx]:
            continue
        for note in notes:
            if note.hit or note.grade == "miss":
                continue
            diff = song_time - note.time
            if diff > MISS_PAST_S:
                note.grade = "miss"
                state.misses += 1
                state.streak = 0
                state.miss_count += 1
                _spawn_flash(state, lane_idx, "MISS", GRADE_MISS_COLOR)


def _receptor_state_for_lane(state: GameState, lane_idx: int, song_time: float) -> str:
    for flash in state.grade_flashes:
        if flash.lane == lane_idx and flash.text != "MISS":
            if song_time - flash.start_time < 0.10:
                return "just_hit"
    if not state.lane_visible[lane_idx]:
        return "idle"
    for note in state.lane_notes[lane_idx]:
        if note.hit or note.grade == "miss":
            continue
        diff = abs(song_time - note.time)
        if diff <= HIT_WINDOW_S:
            return "approaching"
    return "idle"


# ── Results screen ──────────────────────────────────────────────────────
def _compute_grade(accuracy_pct: float) -> tuple[str, str]:
    if accuracy_pct >= 95:
        return ("S", "EXCELLENT")
    if accuracy_pct >= 85:
        return ("A", "GREAT")
    if accuracy_pct >= 70:
        return ("B", "GOOD")
    if accuracy_pct >= 50:
        return ("C", "KEEP PRACTICING")
    return ("D", "GIVE IT ANOTHER GO")


def _compute_score(state: GameState) -> int:
    return int(
        state.perfect_count * 100
        + state.early_count * 70
        + state.late_count * 50
        - state.miss_count * 10
    )


def _load_result_asset(
    fonts: dict[str, Any], name: str, scale: tuple[int, int] | None = None
) -> Any:
    """Lazy-load a PNG asset from assets/ui/. Caches in fonts dict."""
    cache_key = f"_asset_{name}"
    if cache_key in fonts:
        return fonts[cache_key]
    here = Path(__file__).parent.resolve()
    path = here / "assets" / "ui" / name
    if not path.is_file():
        fonts[cache_key] = None
        return None
    surf = pygame.image.load(str(path)).convert_alpha()
    if scale:
        surf = pygame.transform.smoothscale(surf, scale)
    fonts[cache_key] = surf
    return surf


def _draw_results_screen(
    screen: Any,
    state: GameState,
    fonts: dict[str, Any],
    t: float,
) -> None:
    """Render the end-of-song results overlay using custom assets."""
    cw, ch = screen.get_size()
    mx, my = pygame.mouse.get_pos()

    # ── Background overlay ──
    bg_overlay = _load_result_asset(fonts, "results/results_bg_overlay.png", (cw, ch))
    if bg_overlay:
        screen.blit(bg_overlay, (0, 0))
    else:
        overlay = pygame.Surface((cw, ch), pygame.SRCALPHA)
        overlay.fill((0x08, 0x08, 0x10, 0xE0))
        screen.blit(overlay, (0, 0))

    grade_letter, grade_sub = _compute_grade(state.accuracy_pct())
    acc = state.accuracy_pct()

    # ── Grade letter with glow spritesheet ──
    grade_font = fonts.get("grade_font")
    if grade_font is None:
        grade_font = pygame.font.SysFont("Segoe UI", min(260, cw // 2), bold=True)
        fonts["grade_font"] = grade_font

    grade_surf = grade_font.render(grade_letter, True, PASTEL_PURPLE)
    grade_rect = grade_surf.get_rect(center=(cw // 2, ch * 0.08))

    glow_sheet = _load_result_asset(fonts, "results/grade_glow_spritesheet.png")
    if glow_sheet:
        frame_w, frame_h = 320, 320
        glow_frame = glow_sheet.subsurface(pygame.Rect(0, 0, frame_w, frame_h))
        glow_scale = max(grade_surf.get_width() + 40, 120)
        glow_surf = pygame.transform.smoothscale(glow_frame, (glow_scale, glow_scale))
        glow_rect = glow_surf.get_rect(center=grade_rect.center)
        screen.blit(glow_surf, glow_rect)
    else:
        # Fallback procedural glow
        for glow_off, glow_alpha in [(8, 30), (4, 70)]:
            glow_surf = grade_font.render(grade_letter, True, MAGENTA)
            glow_surf.set_alpha(glow_alpha)
            screen.blit(glow_surf, grade_rect.move(glow_off, glow_off))
    screen.blit(grade_surf, grade_rect)

    # ── Subtitle ──
    sub_font = fonts.get("sub_font")
    if sub_font is None:
        sub_font = pygame.font.SysFont("Segoe UI", max(16, cw // 38), bold=True)
        fonts["sub_font"] = sub_font
    sub_surf = sub_font.render(grade_sub, True, (230, 230, 230))
    sub_rect = sub_surf.get_rect(center=(cw // 2, ch * 0.16))
    screen.blit(sub_surf, sub_rect)

    # ── Song info ──
    if state.song_display_name:
        info_font = fonts.get("info_font")
        if info_font is None:
            info_font = pygame.font.SysFont("Segoe UI", max(14, cw // 48))
            fonts["info_font"] = info_font
        info_surf = info_font.render(state.song_display_name, True, (0xCC, 0xCC, 0xDD))
        info_rect = info_surf.get_rect(center=(cw // 2, ch * 0.22))
        screen.blit(info_surf, info_rect)

    # ── 4 Stat cards ──
    score = _compute_score(state)
    acc_color = PASTEL_PURPLE if acc >= 95 else (0xFF, 0x8C, 0x00) if acc >= 85 else GRADE_MISS_COLOR

    n_cards = 4
    card_gap = 16
    card_w = min(220, (cw - (n_cards + 1) * card_gap) // n_cards)
    card_h = max(50, int(card_w * 100 / 220))
    total_cards_w = n_cards * card_w + (n_cards - 1) * card_gap
    start_x = (cw - total_cards_w) // 2
    card_y = int(ch * 0.30)

    card_title_font = fonts.get("card_title_font")
    if card_title_font is None:
        card_title_font = pygame.font.SysFont("Segoe UI", max(11, cw // 78))
        fonts["card_title_font"] = card_title_font
    card_value_font = fonts.get("card_value_font")
    if card_value_font is None:
        card_value_font = pygame.font.SysFont("Segoe UI", max(20, cw // 42), bold=True)
        fonts["card_value_font"] = card_value_font

    card_bg = _load_result_asset(fonts, "results/results_card_bg.png", (card_w, card_h))

    def _draw_card(i: int, title: str, value: str, val_color: tuple[int, int, int], icon_name: str) -> None:
        cx = start_x + i * (card_w + card_gap) + card_w // 2
        rect = pygame.Rect(start_x + i * (card_w + card_gap), card_y, card_w, card_h)
        if card_bg:
            screen.blit(card_bg, rect)
        else:
            pygame.draw.rect(screen, (0x14, 0x14, 0x22), rect, border_radius=10)
            pygame.draw.rect(screen, (0x3A, 0x2A, 0x55), rect, 1, border_radius=10)

        title_surf = card_title_font.render(title, True, (0x99, 0x99, 0xAA))
        screen.blit(title_surf, title_surf.get_rect(center=(cx, card_y + 18)))

        val_surf = card_value_font.render(value, True, val_color)
        screen.blit(val_surf, val_surf.get_rect(center=(cx, card_y + 48)))

        icon = _load_result_asset(fonts, f"common/{icon_name}.png")
        if icon:
            icon_rect = icon.get_rect(center=(cx, card_y + card_h - 16))
            screen.blit(icon, icon_rect)

    _draw_card(0, "SCORE", f"{score:,}", (255, 255, 255), "icon_star")
    _draw_card(1, "ACCURACY", f"{acc:.1f}%", acc_color, "icon_target")
    _draw_card(2, "MAX COMBO", f"{state.best_streak}x", (255, 255, 255), "icon_crown")

    # Breakdown card
    breakdown_rect = pygame.Rect(start_x + 3 * (card_w + card_gap), card_y, card_w, card_h)
    if card_bg:
        screen.blit(card_bg, breakdown_rect)
    else:
        pygame.draw.rect(screen, (0x14, 0x14, 0x22), breakdown_rect, border_radius=10)
        pygame.draw.rect(screen, (0x3A, 0x2A, 0x55), breakdown_rect, 1, border_radius=10)
    bd_title = card_title_font.render("BREAKDOWN", True, (0x99, 0x99, 0xAA))
    screen.blit(bd_title, bd_title.get_rect(center=(start_x + 3 * (card_w + card_gap) + card_w // 2, card_y + 18)))

    bd_rows = [
        (GRADE_PERFECT_COLOR, "PERFECT", state.perfect_count),
        (PASTEL_PURPLE, "EARLY", state.early_count),
        ((0x58, 0xA6, 0xFF), "LATE", state.late_count),
        (GRADE_MISS_COLOR, "MISS", state.miss_count),
    ]
    bd_val_font = fonts.get("bd_val_font")
    if bd_val_font is None:
        bd_val_font = pygame.font.SysFont("Segoe UI", max(12, cw // 72))
        fonts["bd_val_font"] = bd_val_font
    row_y = card_y + 34
    row_x_label = start_x + 3 * (card_w + card_gap) + 14
    row_x_val = start_x + 3 * (card_w + card_gap) + card_w - 14
    for color, label, count in bd_rows:
        pygame.draw.circle(screen, color, (row_x_label + 6, row_y + 7), 5)
        lbl_surf = bd_val_font.render(label, True, (0xCC, 0xCC, 0xDD))
        screen.blit(lbl_surf, (row_x_label + 18, row_y))
        cnt_surf = bd_val_font.render(str(count), True, (255, 255, 255))
        cnt_rect = cnt_surf.get_rect(right=row_x_val, top=row_y)
        screen.blit(cnt_surf, cnt_rect)
        row_y += 18

    # ── Timing Accuracy histogram (card) ──
    hist_card_y = card_y + card_h + 28
    hist_card_w = min(800, cw - 80)
    hist_card_h = max(100, int(hist_card_w * 180 / 800))
    hist_card_rect = pygame.Rect((cw - hist_card_w) // 2, hist_card_y, hist_card_w, hist_card_h)

    hist_bg = _load_result_asset(fonts, "results/results_histogram_card_bg.png", (hist_card_w, hist_card_h))
    if hist_bg:
        screen.blit(hist_bg, hist_card_rect)
    else:
        pygame.draw.rect(screen, (0x14, 0x14, 0x22), hist_card_rect, border_radius=10)
        pygame.draw.rect(screen, (0x3A, 0x2A, 0x55), hist_card_rect, 1, border_radius=10)

    hist_title_font = fonts.get("hist_title_font")
    if hist_title_font is None:
        hist_title_font = pygame.font.SysFont("Segoe UI", max(11, cw // 78))
        fonts["hist_title_font"] = hist_title_font
    hist_title = hist_title_font.render("TIMING ACCURACY", True, (0x99, 0x99, 0xAA))
    screen.blit(hist_title, hist_title.get_rect(center=(cw // 2, hist_card_y + 18)))

    plot_pad_x = 24
    plot_pad_top = 38
    plot_pad_bot = 50
    hist_x = hist_card_rect.x + plot_pad_x
    hist_y = hist_card_y + plot_pad_top
    hist_w = hist_card_rect.width - 2 * plot_pad_x
    hist_h = hist_card_rect.height - plot_pad_top - plot_pad_bot

    axis_font = fonts.get("axis_font")
    if axis_font is None:
        axis_font = pygame.font.SysFont("Segoe UI", max(10, cw // 90))
        fonts["axis_font"] = axis_font

    bucket_ms = 10
    max_ms = 120
    n_buckets = (max_ms * 2) // bucket_ms + 1

    if state.hit_timing_offsets:
        counts = [0] * n_buckets
        for offset in state.hit_timing_offsets:
            bucket = int((offset + max_ms) / bucket_ms)
            if 0 <= bucket < n_buckets:
                counts[bucket] += 1
        max_count = max(counts) if counts else 1
        bar_w = hist_w / n_buckets
        for i, count in enumerate(counts):
            if count == 0:
                continue
            bar_h = int((count / max_count) * (hist_h - 8))
            bx = int(hist_x + i * bar_w)
            by = hist_y + hist_h - 4 - bar_h
            ms_center = (i - n_buckets // 2) * bucket_ms
            abs_ms = abs(ms_center)
            if abs_ms <= 30:
                bar_color = GRADE_PERFECT_COLOR
            elif abs_ms <= 90:
                bar_color = PASTEL_PURPLE
            else:
                bar_color = GRADE_MISS_COLOR
            pygame.draw.rect(screen, bar_color, (bx, by, max(1, int(bar_w) - 1), bar_h), border_radius=1)

    tick_labels = [-120, -90, -60, -30, 0, 30, 60, 90, 120]
    for ms in tick_labels:
        frac = (ms + max_ms) / (max_ms * 2)
        tx = int(hist_x + frac * hist_w)
        lbl = axis_font.render(f"{ms:+d}ms" if ms != 0 else "0ms", True, (0x77, 0x77, 0x88))
        screen.blit(lbl, lbl.get_rect(center=(tx, hist_y + hist_h + 14)))
        pygame.draw.line(screen, (0x33, 0x33, 0x44), (tx, hist_y + hist_h), (tx, hist_y + hist_h + 4), 1)

    perfect_label = axis_font.render("PERFECT", True, GRADE_PERFECT_COLOR)
    screen.blit(perfect_label, perfect_label.get_rect(center=(cw // 2, hist_y + hist_h + 30)))

    if state.hit_timing_offsets:
        avg_ms = sum(state.hit_timing_offsets) / len(state.hit_timing_offsets)
        if avg_ms < -20:
            early_late = "early"
        elif avg_ms > 20:
            early_late = "late"
        elif avg_ms < -5:
            early_late = "slightly early"
        elif avg_ms > 5:
            early_late = "slightly late"
        else:
            early_late = "on time"
        avg_text = f"Average offset: {avg_ms:+.0f} ms ({early_late})"
    else:
        avg_text = "Average offset: —"
    avg_surf = axis_font.render(avg_text, True, (0xAA, 0xAA, 0xBB))
    screen.blit(avg_surf, avg_surf.get_rect(center=(cw // 2, hist_y + hist_h + 48)))

    # ── Buttons (image assets with hover) ──
    btn_scale = max(0.6, cw / 1920)
    again_w = max(180, int(240 * btn_scale))
    again_h = max(39, int(52 * btn_scale))
    back_w = max(210, int(280 * btn_scale))
    back_h = again_h
    btn_gap = max(16, int(20 * btn_scale))
    total_btn_w = again_w + btn_gap + back_w
    btn_start_x = (cw - total_btn_w) // 2
    btn_y = int(ch * 0.86)

    again_rect = pygame.Rect(btn_start_x, btn_y, again_w, again_h)
    back_rect = pygame.Rect(btn_start_x + again_w + btn_gap, btn_y, back_w, back_h)

    again_key = "btn_play_again_hover" if again_rect.collidepoint(mx, my) else "btn_play_again"
    again_img = _load_result_asset(fonts, f"results/{again_key}.png", (again_w, again_h))
    if again_img:
        screen.blit(again_img, again_rect)
    else:
        pygame.draw.rect(screen, PASTEL_PURPLE, again_rect, border_radius=10)
        again_text = axis_font.render("PLAY AGAIN", True, (0x14, 0x14, 0x22))
        screen.blit(again_text, again_text.get_rect(center=again_rect.center))

    back_key = "btn_back_hover" if back_rect.collidepoint(mx, my) else "btn_back"
    back_img = _load_result_asset(fonts, f"results/{back_key}.png", (back_w, back_h))
    if back_img:
        screen.blit(back_img, back_rect)
    else:
        pygame.draw.rect(screen, (0x14, 0x14, 0x22), back_rect, border_radius=10)
        pygame.draw.rect(screen, PASTEL_PURPLE, back_rect, 2, border_radius=10)
        back_text = axis_font.render("BACK TO PARAKIT", True, PASTEL_PURPLE)
        screen.blit(back_text, back_text.get_rect(center=back_rect.center))

    fonts["again_rect"] = again_rect
    fonts["back_rect"] = back_rect


# ── Main entry point ────────────────────────────────────────────────────
def run(config_path: str) -> int:
    config = _load_config(config_path)
    started = time.perf_counter()

    if not _HAS_PYGAME or pygame is None:
        _write_results(
            config,
            {
                "hits": 0,
                "misses": 0,
                "best_streak": 0,
                "accuracy_pct": 0.0,
                "perfect_count": 0,
                "early_count": 0,
                "late_count": 0,
                "miss_count": 0,
                "exit_reason": "launch_failed",
                "duration_secs": 0.0,
            },
        )
        return 2

    pygame.mixer.pre_init(frequency=44100, size=-16, channels=2, buffer=512)
    pygame.init()
    midi_input = None
    mixer_ok = False
    try:
        try:
            pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
            mixer_ok = True
        except Exception:
            pass

        # Load audio if mixer init succeeded and an audio path was provided
        _audio_path = config.get("audio_path", "")
        if mixer_ok and _audio_path and Path(_audio_path).is_file():
            try:
                pygame.mixer.music.load(str(_audio_path))
            except Exception as exc:
                print(f"[AUDIO] Could not load {_audio_path}: {exc}")

        size = config.get("window_size") or [1920, 1080]
        try:
            width = max(640, int(size[0]))
            height = max(480, int(size[1]))
        except Exception:
            width, height = 1920, 1080

        flags = pygame.RESIZABLE | pygame.SCALED
        is_fullscreen = bool(config.get("fullscreen"))
        if is_fullscreen:
            flags |= pygame.FULLSCREEN
        screen = pygame.display.set_mode((width, height), flags)
        pygame.display.set_caption("ParaKit Practice (BETA)")
        windowed_size = (width, height)

        clock = pygame.time.Clock()

        # Fonts
        stats_bold = pygame.font.SysFont("Segoe UI", 18, bold=True)
        stats_regular = pygame.font.SysFont("Segoe UI", 16)
        flash_font = pygame.font.SysFont("Segoe UI", 18, bold=True)
        combo_label_font = pygame.font.SysFont("Segoe UI", 16)
        combo_number_font = pygame.font.SysFont("Segoe UI", 48, bold=True)
        time_font = pygame.font.SysFont("Segoe UI", 12)
        lane_label_font = pygame.font.SysFont("Segoe UI", 10)
        hint_font = pygame.font.SysFont("Segoe UI", max(16, height // 54))

        # Load icon images
        here = Path(__file__).parent.resolve()
        _receptor_icons: dict[int, Any] = {}
        _falling_icons: dict[int, Any] = {}
        icon_map = {
            0: "hi_hat_cyan", 1: "crash_orange", 2: "snare_red",
            3: "tom1_blue", 4: "tom2_green", 5: "tom3_purple",
            6: "ride_yellow", 7: "kick_pink",
        }
        for lane_idx, base_name in icon_map.items():
            r_path = here / "note icons" / f"{base_name}.png"
            f_path = here / "falling note icons" / f"{base_name}_falling.png"
            if r_path.is_file():
                _receptor_icons[lane_idx] = pygame.image.load(str(r_path)).convert_alpha()
            if f_path.is_file():
                _falling_icons[lane_idx] = pygame.image.load(str(f_path)).convert_alpha()

        # Build game state
        raw_notes = config.get("notes", [])
        lane_notes = _build_lane_notes(raw_notes)
        lane_visible = config.get("lane_visible", [True] * 8)
        if len(lane_visible) < 8:
            lane_visible += [True] * (8 - len(lane_visible))
        keybinds = _parse_keybinds(config.get("keybinds"))

        # MIDI setup
        midi_profile = config.get("midi_device_profile")
        midi_name = "(none)"
        if isinstance(midi_profile, dict):
            midi_name = midi_profile.get("display_name", "(none)")
        elif isinstance(midi_profile, str) and midi_profile:
            midi_name = midi_profile

        midi_input = None
        if not DEBUG_STUB_MIDI:
            midi_input, midi_name = _init_midi_input(config)
        else:
            midi_name = "DEBUG STUB"

        song_dur = _song_duration_from_notes(lane_notes)

        state = GameState(
            lane_notes=lane_notes,
            lane_visible=lane_visible,
            auto_kick=bool(config.get("auto_kick", False)),
            fall_time_secs=float(config.get("fall_time_secs", 4.0)),
            offset_secs=float(config.get("offset_secs", 0.0)),
            input_latency_ms=float(config.get("input_latency_ms", 0.0)),
            keybinds=keybinds,
            all_notes_as_bars=bool(config.get("all_notes_as_bars", False)),
            kick_full_line=bool(config.get("kick_full_line", False)),
            note_size_scale=float(config.get("note_size_scale", 1.0)),
            song_duration=song_dur,
            session_latency_ms=float(config.get("input_latency_ms", 0.0)),
            session_midi_name=midi_name if midi_name != "(none)" else "None/Keyboard",
            session_auto_kick=bool(config.get("auto_kick", False)),
            song_display_name=str(config.get("song_display_name", "")),
            midi_hh_mode=str(config.get("midi_hh_mode", "auto")),
            midi_open_lane_enabled=bool(config.get("midi_open_hihat_lane", False)),
            midi_user_lane_overrides=dict(
                config.get("midi_user_lane_overrides") or {}
            ),
            bpm=float(config.get("bpm", 120.0)),
            compact_mode=bool(config.get("compact_mode", False)),
            beat_grid=bool(config.get("beat_grid", False)),
            audio_mix_path=str(config.get("audio_mix_path", "")),
            audio_drum_path=str(config.get("audio_drum_path", "")),
            audio_track=str(config.get("audio_track", "mix")),
        )

        # Pre-rendered background
        bg_surface = _make_bg_gradient(width, height)
        divider_color = _hex_to_rgb("#1a1a2e")
        divider_surf = pygame.Surface((1, height), pygame.SRCALPHA)
        divider_surf.fill((*divider_color, int(0.6 * 255)))

        def _toggle_fullscreen() -> None:
            nonlocal is_fullscreen, screen, bg_surface, divider_surf
            is_fullscreen = not is_fullscreen
            try:
                ok = pygame.display.toggle_fullscreen()
                if not ok:
                    raise RuntimeError("toggle_fullscreen() returned False")
            except Exception as exc:
                print(f"[FULLSCREEN] toggle_fullscreen() failed ({exc}), falling back to set_mode")
                try:
                    if is_fullscreen:
                        screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN | pygame.SCALED)
                    else:
                        screen = pygame.display.set_mode(windowed_size, pygame.RESIZABLE | pygame.SCALED)
                except Exception as exc2:
                    print(f"[FULLSCREEN] set_mode fallback also failed: {exc2}")
                    is_fullscreen = not is_fullscreen
                    return
            new_w, new_h = screen.get_size()
            bg_surface = _make_bg_gradient(new_w, new_h)
            divider_surf = pygame.Surface((1, new_h), pygame.SRCALPHA)
            divider_surf.fill((*divider_color, int(0.6 * 255)))
            state.playfield_surf = None

        running = True
        exit_reason = "normal_close"
        t = 0.0
        in_results = False
        results_fonts: dict[str, Any] = {}

        state.started = True

        # Start music playback (matches v1: audio from 0, offset added in timing)
        if mixer_ok and pygame.mixer.music.get_busy() is False:
            try:
                pygame.mixer.music.play(start=0.0)
                state.audio_start_offset_secs = 0.0
                state.song_time_anchor = 0.0
                state.last_known_song_time = 0.0
            except Exception:
                pass

        while running:
            dt = clock.tick(60) / 1000.0
            t += dt

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    exit_reason = "normal_close"
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        if in_results:
                            exit_reason = "normal_close"
                            running = False
                        else:
                            exit_reason = "user_quit"
                            running = False
                    elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER) and event.mod & pygame.KMOD_ALT:
                        _toggle_fullscreen()
                    elif event.key in (pygame.K_F10, pygame.K_F11):
                        _toggle_fullscreen()
                    elif not in_results and event.key == pygame.K_p and event.key not in state.keybinds:
                        if state.countdown_remaining_secs > 0:
                            # Cancel countdown — stay paused
                            state.countdown_remaining_secs = 0.0
                        elif state.paused:
                            # About to unpause
                            if state.unpause_countdown_enabled:
                                state.countdown_remaining_secs = 5.0
                                state.countdown_start_wall_time = time.perf_counter()
                            else:
                                # Resume immediately
                                if midi_input is not None:
                                    try:
                                        midi_input.read(1024)
                                    except Exception:
                                        pass
                                pygame.mixer.music.unpause()
                                state.paused = False
                                state.last_known_song_time = state.song_time
                        else:
                            # Pause: do NOT touch song_time_anchor — get_pos() resumes
                            # correctly after unpause, so anchor must stay at the play()
                            # start position (same as scrub seek).
                            state.last_known_song_time = state.song_time
                            pygame.mixer.music.pause()
                            state.paused = True
                    elif not in_results and event.key in (
                        pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4, pygame.K_5, pygame.K_6, pygame.K_7,
                    ) and event.key not in state.keybinds:
                        key_map = {
                            pygame.K_1: "auto_kick",
                            pygame.K_2: "all_notes_as_bars",
                            pygame.K_3: "kick_full_line",
                            pygame.K_4: "beat_grid",
                            pygame.K_5: "compact_mode",
                        }
                        if event.key == pygame.K_6:
                            state.unpause_countdown_enabled = not state.unpause_countdown_enabled
                        elif event.key == pygame.K_7:
                            _toggle_audio_track(state)
                        else:
                            attr = key_map.get(event.key)
                            if attr:
                                current = getattr(state, attr)
                                setattr(state, attr, not current)
                                if attr == "compact_mode" and not state.compact_mode:
                                    state.playfield_surf = None
                    elif not in_results and event.key == pygame.K_EQUALS and event.key not in state.keybinds:
                        state.note_size_scale = min(3.0, state.note_size_scale + 0.1)
                    elif not in_results and event.key == pygame.K_MINUS and event.key not in state.keybinds:
                        state.note_size_scale = max(0.3, state.note_size_scale - 0.1)
                    elif not in_results and event.key == pygame.K_LEFTBRACKET and event.key not in state.keybinds:
                        state.fall_time_secs = min(8.0, state.fall_time_secs + 0.5)
                    elif not in_results and event.key == pygame.K_RIGHTBRACKET and event.key not in state.keybinds:
                        state.fall_time_secs = max(1.0, state.fall_time_secs - 0.5)
                    elif not in_results and not state.paused and event.key == pygame.K_LEFT and event.key not in state.keybinds:
                        # Scrub backward 5s
                        new_time = max(0.0, state.song_time - 5.0)
                        _seek_song(state, new_time)
                        if mixer_ok:
                            try:
                                audio_seek = max(0.0, new_time - state.offset_secs)
                                pygame.mixer.music.play(start=audio_seek)
                                state.audio_start_offset_secs = audio_seek
                                state.song_time_anchor = audio_seek
                                state.last_known_song_time = new_time
                            except Exception:
                                pass
                    elif not in_results and not state.paused and event.key == pygame.K_RIGHT and event.key not in state.keybinds:
                        # Scrub forward 5s
                        new_time = min(state.song_duration, state.song_time + 5.0)
                        _seek_song(state, new_time)
                        if mixer_ok:
                            try:
                                audio_seek = max(0.0, new_time - state.offset_secs)
                                pygame.mixer.music.play(start=audio_seek)
                                state.audio_start_offset_secs = audio_seek
                                state.song_time_anchor = audio_seek
                                state.last_known_song_time = new_time
                            except Exception:
                                pass
                    elif not in_results and not state.paused:
                        lane = state.keybinds.get(event.key)
                        if lane is not None:
                            hit_time = state.song_time + (state.input_latency_ms / 1000.0)
                            grade = _process_lane_hit(state, lane, hit_time)
                            if grade == "perfect":
                                _spawn_flash(state, lane, "PERFECT", GRADE_PERFECT_COLOR)
                            elif grade == "early":
                                _spawn_flash(state, lane, "EARLY", GRADE_EARLY_COLOR)
                            elif grade == "late":
                                _spawn_flash(state, lane, "LATE", GRADE_LATE_COLOR)
                elif event.type == pygame.VIDEORESIZE:
                    if not is_fullscreen:
                        width = max(800, event.w)
                        height = max(600, event.h)
                        windowed_size = (width, height)
                        screen = pygame.display.set_mode(windowed_size, pygame.RESIZABLE | pygame.SCALED)
                        bg_surface = _make_bg_gradient(width, height)
                        divider_surf = pygame.Surface((1, height), pygame.SRCALPHA)
                        divider_surf.fill((*divider_color, int(0.6 * 255)))
                        state.playfield_surf = None
                elif event.type == pygame.MOUSEBUTTONDOWN and not in_results and not state.paused and event.button == 1:
                    mx, my = event.pos
                    for attr, rect in state.settings_rects.items():
                        if rect.collidepoint(mx, my):
                            if attr == "audio_track":
                                _toggle_audio_track(state)
                            else:
                                current = getattr(state, attr)
                                setattr(state, attr, not current)
                                if attr == "compact_mode" and not state.compact_mode:
                                    state.playfield_surf = None
                            break
                elif event.type == pygame.MOUSEBUTTONDOWN and not in_results and state.paused and event.button == 1:
                    mx, my = event.pos
                    if (state.scrub_knob_rect and state.scrub_knob_rect.collidepoint(mx, my)) or \
                       (state.scrub_bar_rect and state.scrub_bar_rect.collidepoint(mx, my)):
                        state.scrub_dragging = True
                        _apply_scrub_from_x(state, mx)
                elif event.type == pygame.MOUSEMOTION and state.scrub_dragging:
                    mx, _ = event.pos
                    _apply_scrub_from_x(state, mx)
                elif event.type == pygame.MOUSEBUTTONUP and event.button == 1 and state.scrub_dragging:
                    state.scrub_dragging = False
                    _commit_scrub(state)
                elif event.type == pygame.MOUSEBUTTONDOWN and in_results:
                    if event.button == 1:
                        mx, my = event.pos
                        again_rect = results_fonts.get("again_rect")
                        back_rect = results_fonts.get("back_rect")
                        if again_rect and again_rect.collidepoint(mx, my):
                            # Reset state for Play Again
                            in_results = False
                            results_fonts.clear()
                            state.song_time = 0.0
                            state.last_known_song_time = 0.0
                            state.paused = False
                            if mixer_ok:
                                try:
                                    pygame.mixer.music.rewind()
                                    pygame.mixer.music.play(start=0.0)
                                    state.audio_start_offset_secs = 0.0
                                    state.song_time_anchor = 0.0
                                    state.last_known_song_time = 0.0
                                except Exception:
                                    pass
                            state.hits = 0
                            state.misses = 0
                            state.streak = 0
                            state.best_streak = 0
                            state.perfect_count = 0
                            state.early_count = 0
                            state.late_count = 0
                            state.miss_count = 0
                            state.last_milestone_streak = 0
                            state.milestone_pulse_start = -1.0
                            state.hit_timing_offsets.clear()
                            state.grade_flashes.clear()
                            state.last_lane_hit_time = [0.0] * 8
                            state.last_hit_velocity = [80] * 8
                            for ln in state.lane_notes:
                                for note in ln:
                                    note.hit = False
                                    note.grade = ""
                                    note.velocity = 80
                            t = 0.0
                        elif back_rect and back_rect.collidepoint(mx, my):
                            exit_reason = "normal_close"
                            running = False

            if not in_results:
                # Unpause countdown handling
                if state.countdown_remaining_secs > 0:
                    elapsed = time.perf_counter() - state.countdown_start_wall_time
                    state.countdown_remaining_secs = max(0.0, 5.0 - elapsed)
                    if state.countdown_remaining_secs <= 0:
                        # Countdown done — actually unpause now
                        if midi_input is not None:
                            try:
                                midi_input.read(1024)
                            except Exception:
                                pass
                        pygame.mixer.music.unpause()
                        state.paused = False
                        state.countdown_remaining_secs = 0.0
                        state.last_known_song_time = state.song_time

                # Audio-position-anchored song_time (matches v1's _viz_actual_play_pos)
                if not state.paused:
                    pos_ms = pygame.mixer.music.get_pos()
                    if pos_ms >= 0:
                        audio_elapsed = pos_ms / 1000.0
                        state.song_time = state.song_time_anchor + audio_elapsed + state.offset_secs
                        state.last_known_song_time = state.song_time
                    else:
                        state.song_time = state.last_known_song_time

                if not state.paused:
                    # MIDI input
                    if midi_input is not None:
                        _drain_midi_input(midi_input, state)
                    elif DEBUG_STUB_MIDI:
                        _stub_midi_input(state, t)

                    # Hit detection with latency compensation
                    hit_time = state.song_time + (state.input_latency_ms / 1000.0)
                    _process_auto_kick(state, hit_time)
                    _process_misses(state, state.song_time)

                    state.grade_flashes = [
                        f
                        for f in state.grade_flashes
                        if state.song_time - f.start_time < f.duration
                    ]

                    # End-of-song detection (get_busy() is reliable; get_pos() is not)
                    if not pygame.mixer.music.get_busy() and state.song_time > 1.0:
                        in_results = True

            current_w, current_h = screen.get_size()
            hit_y = current_h * 0.88

            # Build visible lane mapping (shifts lanes left when some are hidden)
            _visible_lanes = [i for i in range(8) if state.lane_visible[i]]
            if state.kick_full_line and 7 in _visible_lanes:
                n_lanes = len([i for i in _visible_lanes if i != 7])
            else:
                n_lanes = len(_visible_lanes)
            n_lanes_visible = max(1, n_lanes)
            lane_w = current_w / float(n_lanes_visible)
            lane_to_visible_idx: dict[int, int] = {}
            vidx = 0
            for i in range(8):
                if not state.lane_visible[i]:
                    continue
                if state.kick_full_line and i == 7:
                    continue
                lane_to_visible_idx[i] = vidx
                vidx += 1

            if state.compact_mode:
                if state.playfield_surf is None or state.playfield_surf.get_size() != (current_w, current_h):
                    state.playfield_surf = pygame.Surface((current_w, current_h))
                target = state.playfield_surf
            else:
                target = screen

            # Playfield rendering
            if not state.compact_mode or state.playfield_surf is not None:
                target.blit(bg_surface, (0, 0))
                for i in range(1, n_lanes_visible):
                    x = int(i * lane_w)
                    target.blit(divider_surf, (x, 0))

                hit_line_color = (80, 80, 90)
                pygame.draw.line(
                    target, hit_line_color, (0, int(hit_y)), (current_w, int(hit_y)), 1
                )

                # Beat grid
                if state.beat_grid:
                    beat_interval = 60.0 / state.bpm
                    first_visible = state.song_time - state.fall_time_secs * 0.1
                    last_visible = state.song_time + state.fall_time_secs
                    n_start = int(first_visible / beat_interval)
                    n_end = int(last_visible / beat_interval) + 1
                    grid_color = (60, 60, 80)
                    for n in range(n_start, n_end + 1):
                        beat_time = n * beat_interval
                        time_until = beat_time - state.song_time
                        y = int(hit_y - (time_until / state.fall_time_secs) * hit_y)
                        if 0 <= y <= current_h:
                            pygame.draw.line(target, grid_color, (0, y), (current_w, y), 1)

                # Falling notes
                for lane_idx, notes in enumerate(state.lane_notes):
                    if not state.lane_visible[lane_idx]:
                        continue
                    for note in notes:
                        if note.hit or note.grade == "miss":
                            continue
                        time_until = note.time - state.song_time
                        note_y = int(hit_y - (time_until / state.fall_time_secs) * hit_y)
                        if note_y < -60 or note_y > current_h + 60:
                            continue
                        if state.kick_full_line and lane_idx == 7:
                            # Full-width falling kick bar (thin, no note size scaling)
                            kick_color = _hex_to_rgb("#ff69b4")
                            bar_h = max(2, int(lane_w * 0.018))
                            bar_surf = pygame.Surface((current_w, bar_h), pygame.SRCALPHA)
                            bar_surf.fill((*kick_color, 200))
                            target.blit(bar_surf, (0, note_y - bar_h // 2))
                        elif lane_idx in lane_to_visible_idx:
                            x_center = int(lane_to_visible_idx[lane_idx] * lane_w + lane_w / 2.0)
                            note_color = _hex_to_rgb(LANE_COLORS_HEX[lane_idx])
                            _draw_falling_note(
                                target, x_center, note_y, lane_idx, lane_w,
                                size_scale=state.note_size_scale,
                                icon=_falling_icons.get(lane_idx),
                                all_bars=state.all_notes_as_bars,
                                color=note_color,
                            )

                # Receptors
                for lane_idx in _visible_lanes:
                    if state.kick_full_line and lane_idx == 7:
                        continue
                    if lane_idx not in lane_to_visible_idx:
                        continue
                    x_center = int(lane_to_visible_idx[lane_idx] * lane_w + lane_w / 2.0)
                    color = _hex_to_rgb(LANE_COLORS_HEX[lane_idx])
                    rstate = _receptor_state_for_lane(state, lane_idx, state.song_time)
                    time_since_hit = state.song_time - state.last_lane_hit_time[lane_idx]
                    _draw_receptor(
                        target, x_center, int(hit_y), lane_idx, lane_w, color,
                        state=rstate, t=t,
                        hit_velocity=state.last_hit_velocity[lane_idx],
                        time_since_hit=time_since_hit,
                        icon=_receptor_icons.get(lane_idx),
                        size_scale=state.note_size_scale,
                        all_bars=state.all_notes_as_bars,
                    )

                # Lane labels
                _draw_lane_labels(target, lane_w, hit_y, state.kick_full_line, _visible_lanes, lane_to_visible_idx)

            if state.compact_mode:
                # Centered with ~18% empty margin on all sides
                margin_x = int(current_w * 0.18)
                margin_y = int(current_h * 0.18)
                target_w = current_w - 2 * margin_x
                target_h = current_h - 2 * margin_y
                scaled = pygame.transform.smoothscale(state.playfield_surf, (target_w, target_h))
                screen.blit(bg_surface, (0, 0))
                blit_x = margin_x
                blit_y = margin_y
                screen.blit(scaled, (blit_x, blit_y))

            # HUD overlays (always to screen)
            if not in_results:
                _draw_stats_panel(screen, state, stats_bold, stats_regular, flash_font)
                _draw_session_panel(screen, state, stats_regular)
                _draw_combo_counter(screen, state, combo_label_font, combo_number_font, hit_y, t)
                _draw_progress_bar(screen, state, time_font)

                # Hint text
                hint = hint_font.render(
                    "Press Esc to return to ParaKit", True, (190, 190, 210)
                )
                hint_rect = hint.get_rect(center=(current_w // 2, current_h - 40))
                screen.blit(hint, hint_rect)

                # Pause overlay
                if state.paused:
                    pause_font = pygame.font.SysFont("Segoe UI", max(24, current_h // 20), bold=True)
                    pause_surf = pause_font.render("PAUSED", True, (255, 255, 255))
                    pause_rect = pause_surf.get_rect(center=(current_w // 2, current_h // 2 - 30))
                    screen.blit(pause_surf, pause_rect)
                    resume_font = pygame.font.SysFont("Segoe UI", max(16, current_h // 40))
                    resume_surf = resume_font.render("Press P to resume", True, (200, 200, 200))
                    resume_rect = resume_surf.get_rect(center=(current_w // 2, current_h // 2 + 20))
                    screen.blit(resume_surf, resume_rect)

                # Countdown overlay
                if state.countdown_remaining_secs > 0:
                    number = int(state.countdown_remaining_secs) + 1
                    number = min(5, max(1, number))
                    countdown_font = pygame.font.SysFont(None, 240, bold=True)
                    text_surf = countdown_font.render(str(number), True, PASTEL_PURPLE)
                    text_rect = text_surf.get_rect(center=(current_w // 2, current_h // 2))
                    shadow_surf = countdown_font.render(str(number), True, (0, 0, 0))
                    shadow_rect = shadow_surf.get_rect(center=(current_w // 2 + 4, current_h // 2 + 4))
                    screen.blit(shadow_surf, shadow_rect)
                    screen.blit(text_surf, text_rect)
                    hint_font_cd = pygame.font.SysFont(None, 36)
                    hint_surf = hint_font_cd.render("Get ready...", True, (200, 200, 220))
                    hint_rect = hint_surf.get_rect(center=(current_w // 2, current_h // 2 + 140))
                    screen.blit(hint_surf, hint_rect)
            else:
                _draw_results_screen(screen, state, results_fonts, t)

            pygame.display.flip()

        _write_results(
            config,
            state.results(exit_reason, time.perf_counter() - started),
        )
        return 0
    finally:
        if midi_input is not None:
            try:
                midi_input.close()
            except Exception:
                pass
            try:
                import pygame.midi as _pmidi
                _pmidi.quit()
            except Exception:
                pass
        pygame.quit()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(
            "Usage: python -m practice_minigame.main <config.json>",
            file=sys.stderr,
        )
        return 2
    try:
        return run(argv[0])
    except Exception as exc:
        try:
            config = _load_config(argv[0])
            _write_results(
                config,
                {
                    "hits": 0,
                    "misses": 0,
                    "best_streak": 0,
                    "accuracy_pct": 0.0,
                    "perfect_count": 0,
                    "early_count": 0,
                    "late_count": 0,
                    "miss_count": 0,
                    "exit_reason": "crash",
                    "duration_secs": 0.0,
                },
            )
        except Exception:
            pass
        print(f"Practice mini-game failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
