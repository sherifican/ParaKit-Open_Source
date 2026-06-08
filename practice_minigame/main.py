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
    square_notes: bool
    kick_full_line: bool
    note_size_scale: float
    song_duration: float
    session_latency_ms: float
    session_midi_name: str
    session_auto_kick: bool

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
                grade = _process_lane_hit(state, lane, state.song_time, velocity=d2)
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
    grade = _process_lane_hit(state, lane, state.song_time, velocity=velocity)
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
    shape: str,
    lane_w: float,
    color: tuple[int, int, int],
    size_scale: float = 1.0,
) -> None:
    if shape == "circle":
        radius = int(lane_w * 0.09 * size_scale)
        glow_size = radius * 2 + 24
        glow_surf = pygame.Surface((glow_size, glow_size), pygame.SRCALPHA)
        pygame.draw.circle(
            glow_surf, (*color, 64), (glow_size // 2, glow_size // 2), radius + 12
        )
        screen.blit(glow_surf, (x_center - glow_size // 2, y - glow_size // 2))
        pygame.draw.circle(screen, color, (x_center, y), radius)
        pygame.draw.circle(screen, (255, 255, 255), (x_center, y), radius, 2)
        inner = _lighten(color, 0.4)
        pygame.draw.circle(screen, inner, (x_center, y), int(radius * 0.6))
    elif shape == "bar":
        bw = int(lane_w * 0.22 * size_scale)
        bh = int(lane_w * 0.08 * size_scale)
        rect = pygame.Rect(x_center - bw // 2, y - bh // 2, bw, bh)
        glow_rect = rect.inflate(16, 16)
        glow_surf = pygame.Surface((glow_rect.width, glow_rect.height), pygame.SRCALPHA)
        pygame.draw.rect(
            glow_surf,
            (*color, 64),
            (0, 0, glow_rect.width, glow_rect.height),
            border_radius=4,
        )
        screen.blit(glow_surf, glow_rect.topleft)
        pygame.draw.rect(screen, color, rect, border_radius=3)
        pygame.draw.rect(screen, (255, 255, 255), rect, 2, border_radius=3)
        inner = _lighten(color, 0.3)
        pygame.draw.rect(screen, inner, rect.inflate(-6, -6), border_radius=2)
    elif shape == "kick":
        kw = screen.get_width()
        kh = max(4, int(lane_w * 0.04 * size_scale))
        rect = pygame.Rect(0, y - kh // 2, kw, kh)
        trail_surf = pygame.Surface((kw, 80), pygame.SRCALPHA)
        trail_surf.fill((*color, 38))
        screen.blit(trail_surf, (0, y - 40))
        pygame.draw.rect(screen, color, rect, border_radius=2)
        pygame.draw.rect(screen, (255, 255, 255), rect, 2, border_radius=2)
        inner = _lighten(color, 0.3)
        pygame.draw.rect(screen, inner, rect.inflate(0, -4), border_radius=1)


def _draw_circle_receptor(
    screen: Any,
    x: int,
    y: int,
    radius: int,
    color: tuple[int, int, int],
) -> None:
    halo_size = radius * 2 + 16
    halo_surf = pygame.Surface((halo_size, halo_size), pygame.SRCALPHA)
    pygame.draw.circle(
        halo_surf,
        (*color, int(0.6 * 255)),
        (halo_size // 2, halo_size // 2),
        radius + 8,
    )
    screen.blit(halo_surf, (x - halo_size // 2, y - halo_size // 2))
    pygame.draw.circle(screen, color, (x, y), radius + 3, 2)
    pygame.draw.circle(screen, color, (x, y), radius)
    pygame.draw.circle(screen, (255, 255, 255), (x, y), max(1, radius - 3), 2)


def _draw_bar_receptor(
    screen: Any,
    rect: pygame.Rect,
    color: tuple[int, int, int],
) -> None:
    halo_rect = rect.inflate(12, 12)
    halo_surf = pygame.Surface((halo_rect.width, halo_rect.height), pygame.SRCALPHA)
    pygame.draw.rect(
        halo_surf,
        (*color, int(0.5 * 255)),
        (0, 0, halo_rect.width, halo_rect.height),
        border_radius=5,
    )
    screen.blit(halo_surf, halo_rect.topleft)
    pygame.draw.rect(screen, color, rect, border_radius=3)
    pygame.draw.rect(screen, (255, 255, 255), rect, 2, border_radius=3)


def _draw_receptor(
    screen: Any,
    x_center: int,
    y: int,
    shape: str,
    lane_w: float,
    color: tuple[int, int, int],
    state: str = "idle",
    t: float = 0.0,
    hit_velocity: int = 80,
) -> None:
    alpha_mult, size_mult = _velocity_flash_scale(hit_velocity)
    if shape == "circle":
        radius = int(lane_w * 0.09)
        if state == "approaching":
            pulse = 0.8 + 0.2 * math.sin(t * 4)
            bright = _lighten(color, 0.5)
            glow_size = radius * 2 + 24
            glow_surf = pygame.Surface((glow_size, glow_size), pygame.SRCALPHA)
            pygame.draw.circle(
                glow_surf,
                (*bright, int(204 * pulse)),
                (glow_size // 2, glow_size // 2),
                radius + 12,
            )
            screen.blit(
                glow_surf,
                (x_center - glow_size // 2, y - glow_size // 2),
            )
            _draw_circle_receptor(screen, x_center, y, radius, color)
        elif state == "just_hit":
            flash_radius = int((radius + 4) * size_mult)
            flash = pygame.Rect(
                x_center - flash_radius,
                y - flash_radius,
                flash_radius * 2,
                flash_radius * 2,
            )
            flash_alpha = min(255, int(255 * alpha_mult))
            pygame.draw.rect(
                screen, (flash_alpha, flash_alpha, flash_alpha), flash, border_radius=flash_radius
            )
            spike_len = int(25 * size_mult)
            spike_w = max(1, int(3 * size_mult))
            for angle in range(0, 360, 45):
                rad = math.radians(angle)
                end_x = x_center + math.cos(rad) * spike_len
                end_y = y + math.sin(rad) * spike_len
                pygame.draw.line(
                    screen, (255, 255, 255), (x_center, y), (end_x, end_y), spike_w
                )
            _draw_circle_receptor(screen, x_center, y, radius, color)
        else:
            _draw_circle_receptor(screen, x_center, y, radius, color)
    elif shape == "bar":
        bw = int(lane_w * 0.22)
        bh = int(lane_w * 0.08)
        rect = pygame.Rect(x_center - bw // 2, y - bh // 2, bw, bh)
        if state == "approaching":
            pulse = 0.8 + 0.2 * math.sin(t * 4)
            bright = _lighten(color, 0.5)
            glow_rect = rect.inflate(24, 24)
            glow_surf = pygame.Surface(
                (glow_rect.width, glow_rect.height), pygame.SRCALPHA
            )
            pygame.draw.rect(
                glow_surf,
                (*bright, int(204 * pulse)),
                (0, 0, glow_rect.width, glow_rect.height),
                border_radius=6,
            )
            screen.blit(glow_surf, glow_rect.topleft)
            _draw_bar_receptor(screen, rect, color)
        elif state == "just_hit":
            inflate = int(4 * size_mult)
            pygame.draw.rect(
                screen, (255, 255, 255), rect.inflate(inflate, inflate), border_radius=4
            )
            spike_len = int(25 * size_mult)
            spike_w = max(1, int(3 * size_mult))
            for angle in range(0, 360, 45):
                rad = math.radians(angle)
                end_x = x_center + math.cos(rad) * spike_len
                end_y = y + math.sin(rad) * spike_len
                pygame.draw.line(
                    screen, (255, 255, 255), (x_center, y), (end_x, end_y), spike_w
                )
            _draw_bar_receptor(screen, rect, color)
        else:
            _draw_bar_receptor(screen, rect, color)


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
    """Top-right session info panel."""
    panel_w = 280
    lines = 2
    if state.session_auto_kick:
        lines = 3
    panel_h = PANEL_PAD + lines * STATS_LINE_H + PANEL_PAD
    panel = _draw_rounded_panel(screen, panel_w, panel_h)
    panel_x = screen.get_width() - 24 - panel_w
    panel_y = 24
    screen.blit(panel, (panel_x, panel_y))

    x = panel_x + PANEL_PAD
    y = panel_y + PANEL_PAD + STATS_LINE_H // 2

    _draw_text_line(
        screen,
        f"Input Latency: {state.session_latency_ms:.0f} ms",
        font_regular,
        (255, 255, 255),
        x,
        y,
    )
    y += STATS_LINE_H

    midi_color = PASTEL_PURPLE if state.session_midi_name != "(none)" else (0x88, 0x88, 0x88)
    _draw_text_line(
        screen, f"MIDI: {state.session_midi_name}", font_regular, midi_color, x, y
    )
    y += STATS_LINE_H

    if state.session_auto_kick:
        _draw_text_line(
            screen, "Auto-Kick: ON", font_regular, (255, 255, 255), x, y
        )


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
    label_font: Any,
) -> None:
    """Persistent lane-name labels at the bottom of each lane."""
    label_y = int(hit_y) + 30
    for i in range(8):
        x_center = int(i * lane_w + lane_w / 2.0)
        surf = label_font.render(LANE_NAMES[i], True, (0x66, 0x66, 0x66))
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
    state.last_lane_hit_time[lane_idx] = song_time
    state.last_hit_velocity[lane_idx] = velocity
    # Milestone pulse
    if state.streak > 0 and state.streak % 10 == 0:
        if state.streak != state.last_milestone_streak:
            state.last_milestone_streak = state.streak
            state.milestone_pulse_start = song_time
    return grade


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


def _draw_results_screen(
    screen: Any,
    state: GameState,
    fonts: dict[str, Any],
    t: float,
) -> None:
    """Render the end-of-song results overlay."""
    current_w, current_h = screen.get_size()
    cw, ch = current_w, current_h

    # Dark overlay
    overlay = pygame.Surface((cw, ch), pygame.SRCALPHA)
    overlay.fill((0x08, 0x08, 0x10, 0xE0))
    screen.blit(overlay, (0, 0))

    grade_letter, grade_sub = _compute_grade(state.accuracy_pct())

    # ── Grade letter (top center) ──
    grade_font = fonts.get("grade_font")
    if grade_font is None:
        grade_font = pygame.font.SysFont("Segoe UI", min(280, cw // 4), bold=True)
        fonts["grade_font"] = grade_font
    grade_surf = grade_font.render(grade_letter, True, PASTEL_PURPLE)
    # Gradient-ish glow by rendering twice
    glow_surf = grade_font.render(grade_letter, True, MAGENTA)
    grade_rect = grade_surf.get_rect(center=(cw // 2, ch * 0.18))
    for offset in [(3, 3), (-3, -3), (3, -3), (-3, 3)]:
        screen.blit(glow_surf, grade_rect.move(*offset))
    screen.blit(grade_surf, grade_rect)

    sub_font = fonts.get("sub_font")
    if sub_font is None:
        sub_font = pygame.font.SysFont("Segoe UI", max(16, cw // 40), bold=True)
        fonts["sub_font"] = sub_font
    sub_surf = sub_font.render(grade_sub, True, (255, 255, 255))
    sub_rect = sub_surf.get_rect(center=(cw // 2, ch * 0.30))
    screen.blit(sub_surf, sub_rect)

    # ── Stat cards (middle band) ──
    score = _compute_score(state)
    acc = state.accuracy_pct()
    acc_color = PASTEL_PURPLE if acc >= 95 else (0xFF, 0x8C, 0x00) if acc >= 85 else GRADE_MISS_COLOR

    cards = [
        ("Score", f"{score}"),
        ("Accuracy", f"{acc:.1f}%", acc_color),
        ("Max Combo", f"{state.best_streak}"),
        ("Breakdown", f"P:{state.perfect_count}  E:{state.early_count}  L:{state.late_count}  M:{state.miss_count}"),
    ]

    card_font = fonts.get("card_font")
    if card_font is None:
        card_font = pygame.font.SysFont("Segoe UI", max(14, cw // 55))
        fonts["card_font"] = card_font
    card_title_font = fonts.get("card_title_font")
    if card_title_font is None:
        card_title_font = pygame.font.SysFont("Segoe UI", max(12, cw // 70))
        fonts["card_title_font"] = card_title_font

    n_cards = len(cards)
    card_gap = 16
    card_w = min(200, (cw - (n_cards + 1) * card_gap) // n_cards)
    total_cards_w = n_cards * card_w + (n_cards - 1) * card_gap
    start_x = (cw - total_cards_w) // 2
    card_y = int(ch * 0.38)
    card_h = 70

    for i, card in enumerate(cards):
        label = card[0]
        value = card[1]
        val_color = card[2] if len(card) > 2 else (255, 255, 255)
        cx = start_x + i * (card_w + card_gap) + card_w // 2
        # Card bg
        card_rect = pygame.Rect(start_x + i * (card_w + card_gap), card_y, card_w, card_h)
        pygame.draw.rect(screen, (*PANEL_BG, 200), card_rect, border_radius=8)
        pygame.draw.rect(screen, (*PANEL_BORDER, 180), card_rect, 1, border_radius=8)
        # Title
        title_surf = card_title_font.render(label, True, (0xAA, 0xAA, 0xBB))
        title_rect = title_surf.get_rect(center=(cx, card_y + 18))
        screen.blit(title_surf, title_rect)
        # Value
        val_surf = card_font.render(value, True, val_color)
        val_rect = val_surf.get_rect(center=(cx, card_y + 45))
        screen.blit(val_surf, val_rect)

    # ── Timing histogram ──
    hist_y = int(ch * 0.56)
    hist_w = min(800, cw - 48)
    hist_h = 80
    hist_x = (cw - hist_w) // 2

    # Background
    hist_rect = pygame.Rect(hist_x, hist_y, hist_w, hist_h)
    pygame.draw.rect(screen, (0x11, 0x11, 0x22), hist_rect, border_radius=6)
    pygame.draw.rect(screen, (0x33, 0x33, 0x44), hist_rect, 1, border_radius=6)

    # Center line (perfect)
    cx_line = hist_x + hist_w // 2
    pygame.draw.line(screen, GRADE_PERFECT_COLOR, (cx_line, hist_y), (cx_line, hist_y + hist_h), 2)

    # Histogram bars
    if state.hit_timing_offsets:
        bucket_ms = 10  # 10ms buckets
        max_ms = 75
        n_buckets = (max_ms * 2) // bucket_ms + 1
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
            # Color by distance from center
            dist = abs(i - n_buckets // 2)
            if dist <= 3:
                bar_color = GRADE_PERFECT_COLOR
            elif dist <= 7:
                bar_color = PASTEL_PURPLE
            else:
                bar_color = GRADE_MISS_COLOR
            pygame.draw.rect(screen, bar_color, (bx, by, max(1, int(bar_w) - 1), bar_h), border_radius=1)

    # Labels
    label_font = fonts.get("hist_label_font")
    if label_font is None:
        label_font = pygame.font.SysFont("Segoe UI", max(12, cw // 70))
        fonts["hist_label_font"] = label_font
    perfect_label = label_font.render("PERFECT", True, GRADE_PERFECT_COLOR)
    pl_rect = perfect_label.get_rect(center=(cx_line, hist_y - 10))
    screen.blit(perfect_label, pl_rect)

    if state.hit_timing_offsets:
        avg_ms = sum(state.hit_timing_offsets) / len(state.hit_timing_offsets)
        early_late = "slightly early" if avg_ms < 0 else "slightly late" if avg_ms > 0 else "on time"
        avg_text = f"Average offset: {avg_ms:+.0f} ms ({early_late})"
    else:
        avg_text = "Average offset: —"
    avg_surf = label_font.render(avg_text, True, (0xAA, 0xAA, 0xBB))
    avg_rect = avg_surf.get_rect(center=(cw // 2, hist_y + hist_h + 14))
    screen.blit(avg_surf, avg_rect)

    # ── Buttons ──
    btn_y = int(ch * 0.76)
    btn_h = 44
    btn_gap = 24

    # Play Again
    again_w = min(220, cw // 4)
    again_x = cw // 2 - btn_gap // 2 - again_w
    again_rect = pygame.Rect(again_x, btn_y, again_w, btn_h)
    pygame.draw.rect(screen, PASTEL_PURPLE, again_rect, border_radius=8)
    again_font = fonts.get("btn_font")
    if again_font is None:
        again_font = pygame.font.SysFont("Segoe UI", max(14, cw // 55), bold=True)
        fonts["btn_font"] = again_font
    again_text = again_font.render("▶  PLAY AGAIN", True, (0x0D, 0x0D, 0x1A))
    again_text_rect = again_text.get_rect(center=again_rect.center)
    screen.blit(again_text, again_text_rect)

    # Back to ParaKit
    back_w = min(260, cw // 3)
    back_x = cw // 2 + btn_gap // 2
    back_rect = pygame.Rect(back_x, btn_y, back_w, btn_h)
    pygame.draw.rect(screen, PASTEL_PURPLE, back_rect, 2, border_radius=8)
    back_text = again_font.render("←  BACK TO PARAKIT", True, PASTEL_PURPLE)
    back_text_rect = back_text.get_rect(center=back_rect.center)
    screen.blit(back_text, back_text_rect)

    # Store rects for click detection
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

    pygame.mixer.pre_init(frequency=44100, size=-16, channels=2, buffer=256)
    pygame.init()
    midi_input = None
    try:
        try:
            pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=256)
        except Exception:
            pass

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
            square_notes=bool(config.get("square_notes", False)),
            kick_full_line=bool(config.get("kick_full_line", False)),
            note_size_scale=float(config.get("note_size_scale", 1.0)),
            song_duration=song_dur,
            session_latency_ms=float(config.get("input_latency_ms", 0.0)),
            session_midi_name=midi_name,
            session_auto_kick=bool(config.get("auto_kick", False)),
            midi_hh_mode=str(config.get("midi_hh_mode", "auto")),
            midi_open_lane_enabled=bool(config.get("midi_open_hihat_lane", False)),
            midi_user_lane_overrides=dict(
                config.get("midi_user_lane_overrides") or {}
            ),
        )

        # Pre-rendered background
        bg_surface = _make_bg_gradient(width, height)
        divider_color = _hex_to_rgb("#1a1a2e")
        divider_surf = pygame.Surface((1, height), pygame.SRCALPHA)
        divider_surf.fill((*divider_color, int(0.6 * 255)))

        running = True
        exit_reason = "normal_close"
        t = 0.0
        in_results = False
        results_fonts: dict[str, Any] = {}

        state.start_wall_time = time.perf_counter()
        state.started = True

        while running:
            dt = clock.tick(60) / 1000.0
            t += dt

            now_wall = time.perf_counter()
            state.song_time = (
                state.start_song_time
                + (now_wall - state.start_wall_time)
                - state.offset_secs
                - state.input_latency_ms / 1000.0
            )

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
                        is_fullscreen = not is_fullscreen
                        if is_fullscreen:
                            screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN | pygame.SCALED)
                        else:
                            screen = pygame.display.set_mode(windowed_size, pygame.RESIZABLE | pygame.SCALED)
                    elif event.key == pygame.K_F11:
                        is_fullscreen = not is_fullscreen
                        if is_fullscreen:
                            screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN | pygame.SCALED)
                        else:
                            screen = pygame.display.set_mode(windowed_size, pygame.RESIZABLE | pygame.SCALED)
                    elif not in_results:
                        lane = state.keybinds.get(event.key)
                        if lane is not None:
                            grade = _process_lane_hit(state, lane, state.song_time)
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
                            state.start_song_time = 0.0
                            state.start_wall_time = time.perf_counter()
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
                # MIDI input
                if midi_input is not None:
                    _drain_midi_input(midi_input, state)
                elif DEBUG_STUB_MIDI:
                    _stub_midi_input(state, t)

                _process_auto_kick(state, state.song_time)
                _process_misses(state, state.song_time)

                state.grade_flashes = [
                    f
                    for f in state.grade_flashes
                    if state.song_time - f.start_time < f.duration
                ]

                # End-of-song detection
                if state.song_time > state.song_duration + 3.0:
                    in_results = True

            current_w, current_h = screen.get_size()
            lane_w = current_w / 8.0
            hit_y = current_h * 0.88

            # ── Render ──
            screen.blit(bg_surface, (0, 0))

            for i in range(1, 8):
                x = int(i * lane_w)
                screen.blit(divider_surf, (x, 0))

            hit_line_color = _hex_to_rgb("#333344")
            pygame.draw.line(
                screen, hit_line_color, (0, int(hit_y)), (current_w, int(hit_y)), 1
            )

            # Kick bar
            kick_color = _hex_to_rgb("#ff69b4")
            kick_h = max(4, int(lane_w * 0.04))
            glow_rect = pygame.Rect(
                0, int(hit_y) - kick_h // 2 - 4, current_w, kick_h + 8
            )
            glow_surf = pygame.Surface((glow_rect.width, glow_rect.height), pygame.SRCALPHA)
            glow_surf.fill((*kick_color, 77))
            screen.blit(glow_surf, glow_rect.topleft)
            kick_rect = pygame.Rect(0, int(hit_y) - kick_h // 2, current_w, kick_h)
            pygame.draw.rect(screen, kick_color, kick_rect, border_radius=2)

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
                    color = _hex_to_rgb(LANE_COLORS_HEX[lane_idx])
                    shape = LANE_SHAPES[lane_idx]
                    if state.square_notes and shape != "kick":
                        shape = "bar"
                    x_center = int(lane_idx * lane_w + lane_w / 2.0)
                    _draw_falling_note(
                        screen, x_center, note_y, shape, lane_w, color,
                        size_scale=state.note_size_scale,
                    )

            # Receptors
            for i in range(8):
                if i == 7:
                    continue
                x_center = int(i * lane_w + lane_w / 2.0)
                color = _hex_to_rgb(LANE_COLORS_HEX[i])
                shape = LANE_SHAPES[i]
                if state.square_notes and shape != "kick":
                    shape = "bar"
                rstate = _receptor_state_for_lane(state, i, state.song_time)
                _draw_receptor(
                    screen, x_center, int(hit_y), shape, lane_w, color,
                    state=rstate, t=t,
                    hit_velocity=state.last_hit_velocity[i],
                )

            # ── HUD overlays ──
            if not in_results:
                _draw_stats_panel(screen, state, stats_bold, stats_regular, flash_font)
                _draw_session_panel(screen, state, stats_regular)
                _draw_combo_counter(screen, state, combo_label_font, combo_number_font, hit_y, t)
                _draw_progress_bar(screen, state, time_font)
                _draw_lane_labels(screen, lane_w, hit_y, lane_label_font)

                # Hint text
                hint = hint_font.render(
                    "Press Esc to return to ParaKit", True, (190, 190, 210)
                )
                hint_rect = hint.get_rect(center=(current_w // 2, current_h - 40))
                screen.blit(hint, hint_rect)
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
