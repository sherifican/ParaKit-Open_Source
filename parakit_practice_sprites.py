# -*- coding: utf-8 -*-
"""parakit_practice_sprites.py — HQ glyph-sprite cache for the Practice highway.

Pre-renders the 9-shape Practice glyph vocabulary with the same HQ treatment
as the Preview tab (parakit_preview_sprites.py): white-hot core → lane color →
translucent edge, ~12 px-equivalent color glow, 4× supersampled then LANCZOS-
downsampled, LRU-512 PhotoImage cache with hard refs (Tk GC trap).

Silhouettes are 1:1 with parakit_practice_tab._glyph_draw (oval / rounded-
rect / diamond / spike / ring / triangle vertex math). Color is an arbitrary
hex (Kit Studio recolors any lane) — nothing is hardcoded per-lane.

Deps: Pillow (ImageTk), tkinter, stdlib. Module ONLY builds sprites — no
highway/tab imports, no gameplay code.
"""
from __future__ import annotations

import math
import os
from collections import OrderedDict
from typing import List, Optional, Sequence, Tuple

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageTk

# ── constants ────────────────────────────────────────────────────────────────
_SS = 4  # supersample factor
_CACHE_CAP = 512

# Glow: canvas shadowBlur 12 → GaussianBlur radius ≈ 6.5 @1x (match preview)
_GLOW_BLUR_12 = 6.5
# Margin around body so blurred glow is not clipped (≈ 2× blur radius + 1)
_PAD_GLOW_12 = 14
_PAD_NO_GLOW = 3  # stroke / AA only (ghost)
# Extra pad for accent hot-pink ring outside the body
_PAD_ACCENT_EXTRA = 4

_GLYPHS = frozenset({
    "circle", "lozenge", "lozengeS", "roundrect", "bar",
    "diamond", "spike", "ring", "triangle",
})
_STATES = frozenset({"normal", "ghost", "accent"})
# "hq" = current gradient+glow sprites (DEFAULT — byte-identical path).
# "classic" = flat charting-game highway: cymbals (circle family) = filled
# circle + crisp white outline; drums (roundrect/bar) = filled rounded rect +
# white outline; other silhouettes keep shape with the same flat+outline look.
_STYLES = frozenset({"hq", "classic"})
_DEFAULT_STYLE = "hq"

# Classic outline @1× → ~2px after 4× SS + LANCZOS downsample.
# Solid near-opaque white ring (not glow / not faint).
_CLASSIC_OUTLINE_1X = 2.0
_CLASSIC_CORNER_1X = 2.0
# Subtle top lighten only (≤ ~8–10%); rest is solid lane color.
_CLASSIC_TOP_LIGHTEN = 0.08
_PAD_CLASSIC = 4

# Hot-pink accent ring (matches task spec rgba(255,105,180,…))
_ACCENT_RGB = (255, 105, 180)

# LRU: key -> ImageTk.PhotoImage (hard refs — Tk GC trap)
_CACHE: "OrderedDict[tuple, ImageTk.PhotoImage]" = OrderedDict()
# Parallel RGBA PIL cache for selftest / reuse of identical bitmaps
_PIL_CACHE: "OrderedDict[tuple, Image.Image]" = OrderedDict()

# Selftest / proof: classic circle path ran through supersample+LANCZOS.
_CLASSIC_SS_HITS = 0


# ── public API ───────────────────────────────────────────────────────────────

def glyph_pad(glyph: str, state: str, style: str = _DEFAULT_STYLE) -> int:
    """Glow margin px added around the body, so the caller can center-blit
    with create_image(cx, cy) — no geometry math.

    Returned PhotoImage is body + 2*pad on each axis.
    """
    if glyph not in _GLYPHS:
        raise ValueError(f"unknown glyph {glyph!r}; expected one of {sorted(_GLYPHS)}")
    if state not in _STATES:
        raise ValueError(f"unknown state {state!r}; expected one of {sorted(_STATES)}")
    style = _norm_style(style)
    if style == "classic":
        if state == "accent":
            return _PAD_CLASSIC + _PAD_ACCENT_EXTRA
        return _PAD_CLASSIC
    if state == "ghost":
        return _PAD_NO_GLOW
    if state == "accent":
        return _PAD_GLOW_12 + _PAD_ACCENT_EXTRA
    return _PAD_GLOW_12


def clear_cache() -> None:
    """Drop all cached PhotoImage / PIL entries."""
    _CACHE.clear()
    _PIL_CACHE.clear()


def glyph_sprite(
    color: str,
    glyph: str,
    r: int,
    w: int,
    state: str = "normal",
    master=None,
    style: str = _DEFAULT_STYLE,
) -> "ImageTk.PhotoImage":
    """Sprite for a highway note.

    glyph in the 9 names (circle, lozenge, lozengeS, roundrect, bar, diamond,
    spike, ring, triangle); r = note radius px, w = bar width px; state in
    {"normal", "ghost", "accent"}. ``color`` is an ARBITRARY hex (Kit Studio
    lets users recolor any lane), so nothing may be hardcoded per-lane.

    style in {hq, classic} — default ``hq`` keeps the pre-existing gradient+glow
    look byte-identical; ``classic`` is a flat filled shape + crisp white outline
    (cymbals = smooth supersampled circle; drums = rounded rect).

    normal = gradient fill + glow (hq) / flat fill + white outline (classic);
    ghost  = hollow (no fill, no glow, dashed-look outline in color);
    accent = normal + a bright rgba(255,105,180,…) accent ring.

    Cached by (color, glyph, r, w, state, style), LRU-512, keeps hard PhotoImage
    refs. Round r/w UP to even before caching. ``master`` passed to PhotoImage.
    """
    if glyph not in _GLYPHS:
        raise ValueError(f"unknown glyph {glyph!r}; expected one of {sorted(_GLYPHS)}")
    if state not in _STATES:
        raise ValueError(f"unknown state {state!r}; expected one of {sorted(_STATES)}")
    style = _norm_style(style)

    r = _even_up(r)
    w = _even_up(w)
    color = _norm_hex(color)
    key = (color, glyph, r, w, state, style)

    hit = _CACHE.get(key)
    if hit is not None:
        _CACHE.move_to_end(key)
        _PIL_CACHE.move_to_end(key)
        return hit

    if style == "classic":
        rgba = _render_classic_rgba(color, glyph, r, w, state)
    else:
        # Unchanged HQ path — must stay byte-identical to pre-style work.
        rgba = _render_rgba(color, glyph, r, w, state)
    photo = ImageTk.PhotoImage(rgba, master=master)

    _CACHE[key] = photo
    _PIL_CACHE[key] = rgba
    _CACHE.move_to_end(key)
    _PIL_CACHE.move_to_end(key)
    while len(_CACHE) > _CACHE_CAP:
        old = next(iter(_CACHE))
        del _CACHE[old]
        _PIL_CACHE.pop(old, None)

    return photo


# ── helpers ──────────────────────────────────────────────────────────────────

def _even_up(n: int) -> int:
    n = max(1, int(n))
    return n if (n % 2 == 0) else n + 1


def _norm_style(style: Optional[str]) -> str:
    if style is None or str(style).strip() == "":
        return _DEFAULT_STYLE
    s = str(style).strip().lower()
    if s not in _STYLES:
        raise ValueError(f"unknown style {style!r}; expected one of {sorted(_STYLES)}")
    return s


def _norm_hex(c: Optional[str]) -> str:
    if c is None:
        return ""
    s = str(c).strip().lower()
    if not s.startswith("#"):
        s = "#" + s
    if len(s) == 4:  # #rgb
        s = "#" + "".join(ch * 2 for ch in s[1:])
    if len(s) != 7:
        raise ValueError(f"bad color {c!r}")
    return s


def _rgb(hex_color: str) -> Tuple[int, int, int]:
    h = _norm_hex(hex_color)
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _lerp_rgb(
    c0: Tuple[int, int, int], c1: Tuple[int, int, int], t: float
) -> Tuple[int, int, int]:
    return (
        int(round(_lerp(c0[0], c1[0], t))),
        int(round(_lerp(c0[1], c1[1], t))),
        int(round(_lerp(c0[2], c1[2], t))),
    )


def _body_dims(glyph: str, r: int, w: int) -> Tuple[int, int]:
    """1× body bounding box (px) matching _glyph_draw extents."""
    if glyph in ("circle", "ring"):
        return (2 * r, 2 * r)
    if glyph == "lozenge":
        return (
            max(1, int(math.ceil(2 * r * 1.35))),
            max(1, int(math.ceil(2 * r * 0.62))),
        )
    if glyph == "lozengeS":
        return (
            max(1, int(math.ceil(2 * r * 1.05))),
            max(1, int(math.ceil(2 * r * 0.5))),
        )
    if glyph == "roundrect":
        return (max(1, w), max(1, int(math.ceil(2 * r * 0.62))))
    if glyph == "bar":
        return (max(1, w), max(1, int(math.ceil(2 * r * 0.42))))
    if glyph == "diamond":
        return (max(1, int(math.ceil(2 * r * 0.95))), max(1, 2 * r))
    if glyph == "spike":
        # k = r * 1.2 → width 2k; y from −0.55r to +0.5r
        return (
            max(1, int(math.ceil(2 * r * 1.2))),
            max(1, int(math.ceil(r * 0.55 + r * 0.5))),
        )
    if glyph == "triangle":
        # y from −r to +0.8r; width 1.9r
        return (
            max(1, int(math.ceil(2 * r * 0.95))),
            max(1, int(math.ceil(r + r * 0.8))),
        )
    return (2 * r, 2 * r)


def _rounded_rect_mask(bw: int, bh: int, radius: int) -> Image.Image:
    m = Image.new("L", (bw, bh), 0)
    if bw < 1 or bh < 1:
        return m
    rad = max(0, min(radius, bw // 2, bh // 2))
    ImageDraw.Draw(m).rounded_rectangle([0, 0, bw - 1, bh - 1], radius=rad, fill=255)
    return m


def _ellipse_mask(bw: int, bh: int) -> Image.Image:
    m = Image.new("L", (max(1, bw), max(1, bh)), 0)
    if bw < 1 or bh < 1:
        return m
    ImageDraw.Draw(m).ellipse([0, 0, bw - 1, bh - 1], fill=255)
    return m


def _polygon_mask(bw: int, bh: int, pts: Sequence[Tuple[float, float]]) -> Image.Image:
    m = Image.new("L", (max(1, bw), max(1, bh)), 0)
    if bw < 1 or bh < 1 or len(pts) < 3:
        return m
    flat = [(float(x), float(y)) for x, y in pts]
    ImageDraw.Draw(m).polygon(flat, fill=255)
    return m


def _draw_radial_disc(size: int, lc: Tuple[int, int, int]) -> Image.Image:
    """Radial gradient disc: 0=#fff, 0.35=lc, 1.0=lc@0.55a. ``size`` is diameter."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    if size < 1:
        return img
    cx = (size - 1) * 0.5
    cy = (size - 1) * 0.5
    rad = size * 0.5
    if rad < 0.5:
        rad = 0.5
    px = img.load()
    white = (255, 255, 255)
    a_edge = int(round(0.55 * 255))
    for y in range(size):
        dy = y - cy
        for x in range(size):
            dx = x - cx
            dist = math.hypot(dx, dy)
            if dist > rad:
                continue
            t = dist / rad
            if t <= 0.35:
                u = t / 0.35
                rr, gg, bb = _lerp_rgb(white, lc, u)
                aa = 255
            else:
                u = (t - 0.35) / 0.65
                rr, gg, bb = lc
                aa = int(round(_lerp(255, a_edge, u)))
            px[x, y] = (rr, gg, bb, aa)
    return img


def _draw_radial_in_box(
    bw: int, bh: int, lc: Tuple[int, int, int], mask: Image.Image
) -> Image.Image:
    """Radial gradient over a body box, clipped by ``mask`` (mode L)."""
    if bw < 1 or bh < 1:
        return Image.new("RGBA", (max(1, bw), max(1, bh)), (0, 0, 0, 0))
    # Generate disc large enough to cover the box diagonal half
    diam = max(bw, bh)
    disc = _draw_radial_disc(diam, lc)
    # Center-crop / pad disc into body box
    out = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    ox = (bw - diam) // 2
    oy = (bh - diam) // 2
    if ox >= 0 and oy >= 0:
        out.paste(disc, (ox, oy))
    else:
        # disc larger than box on one axis — crop
        sx = max(0, -ox)
        sy = max(0, -oy)
        ex = sx + min(bw, diam - sx)
        ey = sy + min(bh, diam - sy)
        crop = disc.crop((sx, sy, ex, ey))
        out.paste(crop, (max(0, ox), max(0, oy)))
    # Apply silhouette mask
    if mask.mode != "L":
        mask = mask.convert("L")
    r_ch, g_ch, b_ch, a_ch = out.split()
    # a = disc_a * mask/255
    a_ch = Image.composite(
        a_ch, Image.new("L", (bw, bh), 0), mask
    )
    return Image.merge("RGBA", (r_ch, g_ch, b_ch, a_ch))


def _draw_bar_gradient(
    bw: int, bh: int, radius: int, lc: Tuple[int, int, int]
) -> Image.Image:
    """Vertical linear gradient rounded rect: 0=#fff, 0.25=lc, 1.0=lc."""
    if bw < 1 or bh < 1:
        return Image.new("RGBA", (max(1, bw), max(1, bh)), (0, 0, 0, 0))
    grad = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    px = grad.load()
    white = (255, 255, 255)
    denom = max(1, bh - 1)
    for y in range(bh):
        t = y / denom
        if t <= 0.25:
            u = t / 0.25
            rr, gg, bb = _lerp_rgb(white, lc, u)
        else:
            rr, gg, bb = lc
        row = (rr, gg, bb, 255)
        for x in range(bw):
            px[x, y] = row
    mask = _rounded_rect_mask(bw, bh, radius)
    out = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    out.paste(grad, (0, 0), mask)
    return out


def _glow_layer(
    ss_w: int,
    ss_h: int,
    shape: Image.Image,
    ox: int,
    oy: int,
    lc: Tuple[int, int, int],
    blur_r_1x: float,
    strength: float = 0.75,
) -> Image.Image:
    """Tint ``shape`` with lane color, blur, place at (ox,oy) on ss canvas."""
    layer = Image.new("RGBA", (ss_w, ss_h), (0, 0, 0, 0))
    solid = Image.new("RGBA", shape.size, (*lc, 255))
    tinted = Image.new("RGBA", shape.size, (0, 0, 0, 0))
    if shape.mode != "RGBA":
        shape = shape.convert("RGBA")
    _, _, _, a = shape.split()
    a = a.point(lambda p, s=strength: int(p * s + 0.5))
    tinted.paste(solid, (0, 0), a)
    blur_r = max(0.5, blur_r_1x * _SS)
    tinted = tinted.filter(ImageFilter.GaussianBlur(radius=blur_r))
    layer.alpha_composite(tinted, (ox, oy))
    return layer


def _stroke_ellipse(
    canvas: Image.Image,
    cx: float,
    cy: float,
    rx: float,
    ry: float,
    color: Tuple[int, int, int, int],
    width: float,
) -> None:
    if rx <= 0 or ry <= 0 or width <= 0:
        return
    d = ImageDraw.Draw(canvas)
    d.ellipse(
        [cx - rx, cy - ry, cx + rx, cy + ry],
        outline=color,
        width=max(1, int(round(width))),
    )


def _stroke_rr(
    canvas: Image.Image,
    box: Tuple[int, int, int, int],
    radius: int,
    color: Tuple[int, int, int, int],
    width: float,
) -> None:
    x0, y0, x1, y1 = box
    if x1 <= x0 or y1 <= y0 or width <= 0:
        return
    bw = x1 - x0 + 1
    bh = y1 - y0 + 1
    rad = max(0, min(radius, bw // 2, bh // 2))
    ImageDraw.Draw(canvas).rounded_rectangle(
        [x0, y0, x1, y1],
        radius=rad,
        outline=color,
        width=max(1, int(round(width))),
    )


def _stroke_polygon(
    canvas: Image.Image,
    pts: Sequence[Tuple[float, float]],
    color: Tuple[int, int, int, int],
    width: float,
) -> None:
    if len(pts) < 2 or width <= 0:
        return
    d = ImageDraw.Draw(canvas)
    # close the loop
    closed = list(pts) + [pts[0]]
    d.line(closed, fill=color, width=max(1, int(round(width))), joint="curve")


def _perimeter_points(
    pts: Sequence[Tuple[float, float]], n_samples: int = 64
) -> List[Tuple[float, float]]:
    """Resample a closed polygon perimeter into ~n_samples points."""
    if len(pts) < 2:
        return list(pts)
    segs: List[Tuple[Tuple[float, float], Tuple[float, float], float]] = []
    total = 0.0
    n = len(pts)
    for i in range(n):
        a = pts[i]
        b = pts[(i + 1) % n]
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        segs.append((a, b, length))
        total += length
    if total < 1e-6:
        return list(pts)
    out: List[Tuple[float, float]] = []
    step = total / max(1, n_samples)
    acc = 0.0
    si = 0
    seg_pos = 0.0
    for k in range(n_samples):
        target = k * step
        while si < len(segs) - 1 and acc + segs[si][2] < target:
            acc += segs[si][2]
            si += 1
            seg_pos = 0.0
        a, b, length = segs[si]
        t = 0.0 if length < 1e-9 else (target - acc) / length
        t = max(0.0, min(1.0, t))
        out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return out


def _dashed_stroke_poly(
    canvas: Image.Image,
    pts: Sequence[Tuple[float, float]],
    color: Tuple[int, int, int, int],
    width: float,
    dash: float,
    gap: float,
) -> None:
    """Dashed outline along a closed polygon (ghost look)."""
    if len(pts) < 2 or width <= 0:
        return
    samples = _perimeter_points(pts, n_samples=max(48, int(round(
        sum(
            math.hypot(
                pts[(i + 1) % len(pts)][0] - pts[i][0],
                pts[(i + 1) % len(pts)][1] - pts[i][1],
            )
            for i in range(len(pts))
        ) / max(1.0, (dash + gap) * 0.35)
    ))))
    if len(samples) < 2:
        return
    d = ImageDraw.Draw(canvas)
    sw = max(1, int(round(width)))
    # walk samples; toggle dash/gap by distance
    drawing = True
    dist_in = 0.0
    for i in range(len(samples)):
        a = samples[i]
        b = samples[(i + 1) % len(samples)]
        seg_len = math.hypot(b[0] - a[0], b[1] - a[1])
        if seg_len < 1e-9:
            continue
        # subdivide long segments so dash/gap transitions land cleanly
        n_sub = max(1, int(math.ceil(seg_len / 2.0)))
        for s in range(n_sub):
            t0 = s / n_sub
            t1 = (s + 1) / n_sub
            p0 = (a[0] + (b[0] - a[0]) * t0, a[1] + (b[1] - a[1]) * t0)
            p1 = (a[0] + (b[0] - a[0]) * t1, a[1] + (b[1] - a[1]) * t1)
            sub_len = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
            if drawing:
                d.line([p0, p1], fill=color, width=sw)
            dist_in += sub_len
            limit = dash if drawing else gap
            if dist_in >= limit:
                dist_in = 0.0
                drawing = not drawing


def _dashed_stroke_ellipse(
    canvas: Image.Image,
    cx: float,
    cy: float,
    rx: float,
    ry: float,
    color: Tuple[int, int, int, int],
    width: float,
    dash: float,
    gap: float,
) -> None:
    """Dashed ellipse outline (ghost look for circle / lozenge / ring)."""
    if rx <= 0 or ry <= 0 or width <= 0:
        return
    # approximate perimeter
    peri = math.pi * (3 * (rx + ry) - math.sqrt((3 * rx + ry) * (rx + 3 * ry)))
    n = max(48, int(round(peri / 2.0)))
    pts = [
        (cx + rx * math.cos(2 * math.pi * i / n),
         cy + ry * math.sin(2 * math.pi * i / n))
        for i in range(n)
    ]
    _dashed_stroke_poly(canvas, pts, color, width, dash, gap)


def _dashed_stroke_rr(
    canvas: Image.Image,
    box: Tuple[int, int, int, int],
    radius: int,
    color: Tuple[int, int, int, int],
    width: float,
    dash: float,
    gap: float,
) -> None:
    x0, y0, x1, y1 = box
    if x1 <= x0 or y1 <= y0:
        return
    bw = x1 - x0
    bh = y1 - y0
    rad = max(0, min(radius, bw // 2, bh // 2))
    # approximate rounded-rect perimeter as polyline (corner arcs as short segs)
    pts: List[Tuple[float, float]] = []
    # top edge
    pts.append((x0 + rad, float(y0)))
    pts.append((x1 - rad, float(y0)))
    # top-right arc
    for i in range(1, 6):
        a = -math.pi / 2 + (math.pi / 2) * (i / 5)
        pts.append((x1 - rad + rad * math.cos(a), y0 + rad + rad * math.sin(a)))
    pts.append((float(x1), y0 + rad))
    pts.append((float(x1), y1 - rad))
    for i in range(1, 6):
        a = 0 + (math.pi / 2) * (i / 5)
        pts.append((x1 - rad + rad * math.cos(a), y1 - rad + rad * math.sin(a)))
    pts.append((x1 - rad, float(y1)))
    pts.append((x0 + rad, float(y1)))
    for i in range(1, 6):
        a = math.pi / 2 + (math.pi / 2) * (i / 5)
        pts.append((x0 + rad + rad * math.cos(a), y1 - rad + rad * math.sin(a)))
    pts.append((float(x0), y1 - rad))
    pts.append((float(x0), y0 + rad))
    for i in range(1, 6):
        a = math.pi + (math.pi / 2) * (i / 5)
        pts.append((x0 + rad + rad * math.cos(a), y0 + rad + rad * math.sin(a)))
    _dashed_stroke_poly(canvas, pts, color, width, dash, gap)


# ── silhouette geometry (1:1 with _glyph_draw, local body coords) ─────────────

def _shape_mask_and_meta(
    glyph: str, ss_r: float, ss_w: float, bw: int, bh: int
) -> Tuple[Image.Image, dict]:
    """Build L-mask of the glyph body and metadata for stroke paths.

    Coordinates are body-local (0..bw, 0..bh); center at (bw/2, bh/2).
    """
    cx = (bw - 1) * 0.5
    cy = (bh - 1) * 0.5
    meta: dict = {"cx": cx, "cy": cy, "kind": "ellipse", "rx": 0.0, "ry": 0.0}

    if glyph == "circle":
        mask = _ellipse_mask(bw, bh)
        meta.update(kind="ellipse", rx=ss_r, ry=ss_r)
        return mask, meta

    if glyph == "lozenge":
        mask = _ellipse_mask(bw, bh)
        meta.update(kind="ellipse", rx=ss_r * 1.35, ry=ss_r * 0.62)
        return mask, meta

    if glyph == "lozengeS":
        mask = _ellipse_mask(bw, bh)
        meta.update(kind="ellipse", rx=ss_r * 1.05, ry=ss_r * 0.5)
        return mask, meta

    if glyph == "roundrect":
        rad = 5 * _SS
        mask = _rounded_rect_mask(bw, bh, rad)
        meta.update(kind="rr", radius=rad, box=(0, 0, bw - 1, bh - 1))
        return mask, meta

    if glyph == "bar":
        rad = 4 * _SS
        mask = _rounded_rect_mask(bw, bh, rad)
        meta.update(kind="rr", radius=rad, box=(0, 0, bw - 1, bh - 1))
        return mask, meta

    if glyph == "diamond":
        # (x, y−r), (x+r*0.95, y), (x, y+r), (x−r*0.95, y)
        pts = [
            (cx, cy - ss_r),
            (cx + ss_r * 0.95, cy),
            (cx, cy + ss_r),
            (cx - ss_r * 0.95, cy),
        ]
        mask = _polygon_mask(bw, bh, pts)
        meta.update(kind="poly", pts=pts)
        return mask, meta

    if glyph == "spike":
        # k = r*1.2; five-point zigzag
        k = ss_r * 1.2
        pts = [
            (cx - k, cy + ss_r * 0.5),
            (cx - k * 0.3, cy - ss_r * 0.55),
            (cx, cy + ss_r * 0.1),
            (cx + k * 0.3, cy - ss_r * 0.55),
            (cx + k, cy + ss_r * 0.5),
        ]
        mask = _polygon_mask(bw, bh, pts)
        meta.update(kind="poly", pts=pts)
        return mask, meta

    if glyph == "triangle":
        # (x, y−r), (x+r*0.95, y+r*0.8), (x−r*0.95, y+r*0.8)
        pts = [
            (cx, cy - ss_r),
            (cx + ss_r * 0.95, cy + ss_r * 0.8),
            (cx - ss_r * 0.95, cy + ss_r * 0.8),
        ]
        mask = _polygon_mask(bw, bh, pts)
        meta.update(kind="poly", pts=pts)
        return mask, meta

    if glyph == "ring":
        # annular mask: outer ellipse minus inner (stroke ~0.45r)
        ow = max(2.0 * _SS, ss_r * 0.45)
        outer = _ellipse_mask(bw, bh)
        # inner radius so stroke thickness ≈ ow (path-centered in Tk; approx half in/out)
        inner_r = max(0.0, ss_r - ow)
        inner_d = max(1, int(round(2 * inner_r)))
        if inner_d >= 2 and inner_r > 0.5:
            inner = Image.new("L", (bw, bh), 0)
            ix0 = int(round(cx - inner_r))
            iy0 = int(round(cy - inner_r))
            ImageDraw.Draw(inner).ellipse(
                [ix0, iy0, ix0 + inner_d - 1, iy0 + inner_d - 1], fill=255
            )
            # outer − inner
            mask = Image.new("L", (bw, bh), 0)
            op = outer.load()
            ip = inner.load()
            mp = mask.load()
            for y in range(bh):
                for x in range(bw):
                    if op[x, y] and not ip[x, y]:
                        mp[x, y] = 255
        else:
            mask = outer
        meta.update(kind="ring", rx=ss_r, ry=ss_r, stroke=ow)
        return mask, meta

    # fallback circle
    mask = _ellipse_mask(bw, bh)
    meta.update(kind="ellipse", rx=ss_r, ry=ss_r)
    return mask, meta


def _build_body(
    glyph: str,
    ss_r: float,
    ss_w: float,
    bw: int,
    bh: int,
    lc: Tuple[int, int, int],
) -> Tuple[Image.Image, dict]:
    """Gradient-filled body (or hollow ring gradient) + stroke metadata."""
    mask, meta = _shape_mask_and_meta(glyph, ss_r, ss_w, bw, bh)

    if glyph == "ring":
        # Hollow glowing ring: radial gradient over annular mask only
        body = _draw_radial_in_box(bw, bh, lc, mask)
        return body, meta

    if glyph in ("roundrect", "bar"):
        rad = int(meta.get("radius", 5 * _SS))
        body = _draw_bar_gradient(bw, bh, rad, lc)
        return body, meta

    # circle / lozenge / lozengeS / diamond / spike / triangle — radial + mask
    if glyph in ("circle", "lozenge", "lozengeS"):
        # true elliptical radial: build disc then squash into ellipse box
        diam = max(bw, bh)
        disc = _draw_radial_disc(diam, lc)
        # scale disc to elliptical body
        ell = disc.resize((bw, bh), Image.Resampling.LANCZOS)
        # ellipse mask already fills the box
        r_ch, g_ch, b_ch, a_ch = ell.split()
        a_ch = Image.composite(a_ch, Image.new("L", (bw, bh), 0), mask)
        body = Image.merge("RGBA", (r_ch, g_ch, b_ch, a_ch))
        return body, meta

    body = _draw_radial_in_box(bw, bh, lc, mask)
    return body, meta


def _offset_meta(meta: dict, ox: int, oy: int) -> dict:
    """Shift stroke metadata from body-local into canvas coords."""
    m = dict(meta)
    m["cx"] = meta["cx"] + ox
    m["cy"] = meta["cy"] + oy
    if meta.get("kind") == "rr":
        x0, y0, x1, y1 = meta["box"]
        m["box"] = (x0 + ox, y0 + oy, x1 + ox, y1 + oy)
    if meta.get("kind") == "poly":
        m["pts"] = [(x + ox, y + oy) for x, y in meta["pts"]]
    return m


def _draw_accent_ring(
    base: Image.Image,
    meta: dict,
    ss_scale: float = 1.0,
) -> None:
    """Bright hot-pink accent ring just outside the body silhouette."""
    accent = (*_ACCENT_RGB, int(round(0.92 * 255)))
    sw = 2.0 * _SS
    inflate = 2.5 * _SS * ss_scale
    kind = meta.get("kind")
    if kind == "ellipse" or kind == "ring":
        _stroke_ellipse(
            base, meta["cx"], meta["cy"],
            meta["rx"] + inflate, meta["ry"] + inflate,
            accent, sw,
        )
    elif kind == "rr":
        x0, y0, x1, y1 = meta["box"]
        inset = -int(round(inflate))
        box = (x0 + inset, y0 + inset, x1 - inset, y1 - inset)
        _stroke_rr(
            base, box,
            int(meta["radius"]) + int(round(inflate * 0.5)),
            accent, sw,
        )
    elif kind == "poly":
        cx, cy = meta["cx"], meta["cy"]
        pts = []
        for x, y in meta["pts"]:
            dx, dy = x - cx, y - cy
            dist = math.hypot(dx, dy)
            if dist < 1e-6:
                pts.append((x, y))
            else:
                s = (dist + inflate) / dist
                pts.append((cx + dx * s, cy + dy * s))
        _stroke_polygon(base, pts, accent, sw)


def _draw_soft_white_stroke(base: Image.Image, meta: dict) -> None:
    """Subtle white edge stroke (normal/accent fill notes)."""
    stroke = (255, 255, 255, int(round(0.55 * 255)))
    sw = 1.5 * _SS
    kind = meta.get("kind")
    if kind == "ellipse":
        _stroke_ellipse(
            base, meta["cx"], meta["cy"],
            max(1.0, meta["rx"] - 0.75 * _SS),
            max(1.0, meta["ry"] - 0.75 * _SS),
            stroke, sw,
        )
    elif kind == "rr":
        _stroke_rr(
            base, meta["box"], int(meta["radius"]),
            (255, 255, 255, int(round(0.35 * 255))),
            1.2 * _SS,
        )
    elif kind == "poly":
        _stroke_polygon(base, meta["pts"], stroke, sw)
    # ring: already gradient-filled annulus; light outer/inner rim
    elif kind == "ring":
        _stroke_ellipse(
            base, meta["cx"], meta["cy"],
            meta["rx"] - 0.5 * _SS, meta["ry"] - 0.5 * _SS,
            (255, 255, 255, int(round(0.45 * 255))),
            1.2 * _SS,
        )


def _draw_ghost_outline(
    base: Image.Image, meta: dict, lc: Tuple[int, int, int]
) -> None:
    """Hollow dashed-look outline in lane color (no fill, no glow)."""
    stroke = (*lc, 255)
    sw = 2.0 * _SS
    dash = 4.0 * _SS
    gap = 3.0 * _SS
    kind = meta.get("kind")
    if kind == "ellipse" or kind == "ring":
        _dashed_stroke_ellipse(
            base, meta["cx"], meta["cy"],
            meta["rx"], meta["ry"],
            stroke, sw, dash, gap,
        )
    elif kind == "rr":
        _dashed_stroke_rr(
            base, meta["box"], int(meta["radius"]),
            stroke, sw, dash, gap,
        )
    elif kind == "poly":
        _dashed_stroke_poly(base, meta["pts"], stroke, sw, dash, gap)


def _render_rgba(
    color: str,
    glyph: str,
    r: int,
    w: int,
    state: str,
) -> Image.Image:
    """Render one sprite at target resolution (with pad), supersampled then down."""
    pad = glyph_pad(glyph, state)
    body_w, body_h = _body_dims(glyph, r, w)
    out_w = body_w + 2 * pad
    out_h = body_h + 2 * pad
    ss_w = out_w * _SS
    ss_h = out_h * _SS
    ss_pad = pad * _SS
    ss_bw = body_w * _SS
    ss_bh = body_h * _SS
    ss_r = float(r * _SS)
    ss_wparam = float(w * _SS)
    lc = _rgb(color)

    bx, by = ss_pad, ss_pad
    base = Image.new("RGBA", (ss_w, ss_h), (0, 0, 0, 0))

    # Build body (or just mask meta for ghost)
    body, meta_local = _build_body(glyph, ss_r, ss_wparam, ss_bw, ss_bh, lc)
    meta = _offset_meta(meta_local, bx, by)

    # ── GHOST: outline only, no gradient, no glow ────────────────────────────
    if state == "ghost":
        _draw_ghost_outline(base, meta, lc)
        return base.resize((out_w, out_h), Image.Resampling.LANCZOS)

    # ── NORMAL / ACCENT: glow + gradient body + soft stroke ──────────────────
    want_glow = True
    if want_glow:
        glow = _glow_layer(
            ss_w, ss_h, body, bx, by, lc, _GLOW_BLUR_12, strength=0.75
        )
        base = Image.alpha_composite(base, glow)
    base.alpha_composite(body, (bx, by))
    _draw_soft_white_stroke(base, meta)

    if state == "accent":
        _draw_accent_ring(base, meta)

    # Ring must stay hollow: glow blur bleeds inward — punch the center clear
    # so the final center pixel is transparent (selftest + visual fidelity).
    if glyph == "ring":
        ow = max(2.0 * _SS, ss_r * 0.45)
        # Keep a clear hole inside the stroke band (slightly inside inner edge).
        hole_r = max(0.0, ss_r - ow * 0.85)
        if hole_r > 1.0:
            hole = Image.new("L", (ss_w, ss_h), 255)
            ImageDraw.Draw(hole).ellipse(
                [
                    meta["cx"] - hole_r,
                    meta["cy"] - hole_r,
                    meta["cx"] + hole_r,
                    meta["cy"] + hole_r,
                ],
                fill=0,
            )
            r_ch, g_ch, b_ch, a_ch = base.split()
            a_ch = ImageChops.multiply(a_ch, hole)
            base = Image.merge("RGBA", (r_ch, g_ch, b_ch, a_ch))

    return base.resize((out_w, out_h), Image.Resampling.LANCZOS)


def _classic_flat_fill(
    bw: int, bh: int, lc: Tuple[int, int, int], mask: Image.Image
) -> Image.Image:
    """Mostly-solid lane-color fill; optional ≤~8% top lighten (no washed gradient)."""
    if bw < 1 or bh < 1:
        return Image.new("RGBA", (max(1, bw), max(1, bh)), (0, 0, 0, 0))
    # Lighten toward white by _CLASSIC_TOP_LIGHTEN only — not white→lc (that washed out).
    top = _lerp_rgb(lc, (255, 255, 255), _CLASSIC_TOP_LIGHTEN)
    bot = lc
    grad = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    px = grad.load()
    denom = max(1, bh - 1)
    for y in range(bh):
        t = y / denom
        rr, gg, bb = _lerp_rgb(top, bot, t)
        row = (rr, gg, bb, 255)
        for x in range(bw):
            px[x, y] = row
    if mask.mode != "L":
        mask = mask.convert("L")
    out = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    out.paste(grad, (0, 0), mask)
    return out


def _render_classic_rgba(
    color: str,
    glyph: str,
    r: int,
    w: int,
    state: str,
) -> Image.Image:
    """Classic highway style: flat fill + solid white outline, 4× supersampled.

    Cymbal-family glyphs (circle / lozenge / lozengeS / ring) get smooth
    elliptical fills with a crisp white ring. Drum glyphs (roundrect / bar)
    get filled rounded rects. Polygons keep their silhouette with the same
    flat + outline treatment. No HQ glow / radial gradient.
    """
    global _CLASSIC_SS_HITS
    pad = glyph_pad(glyph, state, "classic")
    body_w, body_h = _body_dims(glyph, r, w)
    out_w = body_w + 2 * pad
    out_h = body_h + 2 * pad
    ss_w = out_w * _SS
    ss_h = out_h * _SS
    ss_pad = pad * _SS
    ss_bw = body_w * _SS
    ss_bh = body_h * _SS
    ss_r = float(r * _SS)
    ss_wparam = float(w * _SS)
    lc = _rgb(color)
    sw = _CLASSIC_OUTLINE_1X * _SS  # ≈ 8 @4× → ~2px after LANCZOS
    stroke_w = max(1, int(round(sw)))
    outline = (255, 255, 255, 255)  # crisp bright white, full alpha

    bx, by = ss_pad, ss_pad
    base = Image.new("RGBA", (ss_w, ss_h), (0, 0, 0, 0))

    mask, meta_local = _shape_mask_and_meta(glyph, ss_r, ss_wparam, ss_bw, ss_bh)
    meta = _offset_meta(meta_local, bx, by)
    is_cymbal = glyph in ("circle", "lozenge", "lozengeS", "ring")

    if state == "ghost":
        # Hollow solid outline (classic is cleaner than dashed HQ ghost).
        stroke = (*lc, 255)
        kind = meta.get("kind")
        if kind in ("ellipse", "ring"):
            _stroke_ellipse(
                base, meta["cx"], meta["cy"], meta["rx"], meta["ry"],
                stroke, float(stroke_w),
            )
            _CLASSIC_SS_HITS += 1 if is_cymbal else 0
        elif kind == "rr":
            _stroke_rr(base, meta["box"], int(meta["radius"]), stroke, float(stroke_w))
        elif kind == "poly":
            _stroke_polygon(base, meta["pts"], stroke, float(stroke_w))
        return base.resize((out_w, out_h), Image.Resampling.LANCZOS)

    # normal / accent — solid-ish body + crisp ~2px white outline
    if glyph == "ring":
        # Hollow ring: fill annular mask, then white outer/inner rim.
        body = _classic_flat_fill(ss_bw, ss_bh, lc, mask)
        base.alpha_composite(body, (bx, by))
        # Half-stroke inset so the bright white ring sits on the outer rim.
        half = stroke_w * 0.5
        _stroke_ellipse(
            base, meta["cx"], meta["cy"],
            max(1.0, meta["rx"] - half * 0.25),
            max(1.0, meta["ry"] - half * 0.25),
            outline, float(stroke_w),
        )
        inner_r = max(0.0, meta["rx"] - float(meta.get("stroke", ss_r * 0.45)))
        if inner_r > 1.0:
            _stroke_ellipse(
                base, meta["cx"], meta["cy"], inner_r, inner_r,
                outline, float(max(1, int(round(sw * 0.85)))),
            )
        _CLASSIC_SS_HITS += 1
    else:
        body = _classic_flat_fill(ss_bw, ss_bh, lc, mask)
        base.alpha_composite(body, (bx, by))
        kind = meta.get("kind")
        if kind == "ellipse":
            # Center stroke on rim (half-in / half-out) for an even bright ring.
            half = stroke_w * 0.5
            _stroke_ellipse(
                base, meta["cx"], meta["cy"],
                max(1.0, meta["rx"] - half * 0.15),
                max(1.0, meta["ry"] - half * 0.15),
                outline, float(stroke_w),
            )
            _CLASSIC_SS_HITS += 1 if is_cymbal else 0
        elif kind == "rr":
            # Prefer classic ~2px corner when the HQ radius is large.
            rad = min(int(meta["radius"]), int(round(_CLASSIC_CORNER_1X * _SS * 2)))
            # Keep HQ-ish rounding for thicker bars if larger than classic default.
            rad = max(rad, int(round(_CLASSIC_CORNER_1X * _SS)))
            rad = min(rad, int(meta["radius"]))
            _stroke_rr(base, meta["box"], rad, outline, float(stroke_w))
        elif kind == "poly":
            _stroke_polygon(base, meta["pts"], outline, float(stroke_w))

    if state == "accent":
        _draw_accent_ring(base, meta)

    return base.resize((out_w, out_h), Image.Resampling.LANCZOS)


# ── selftest ─────────────────────────────────────────────────────────────────

def _selftest() -> None:
    import hashlib
    import tkinter as tk

    global _CLASSIC_SS_HITS

    root = tk.Tk()
    root.withdraw()

    glyphs = [
        "circle", "lozenge", "lozengeS", "roundrect", "bar",
        "diamond", "spike", "ring", "triangle",
    ]
    colors = [
        "#00e5ff",
        "#ff8c00",
        "#e63946",
        "#3a5fc8",
        "#33ddaa",  # non-standard arbitrary hex (Kit Studio recolor)
        "#9b4fc0",
    ]
    states = ["normal", "ghost", "accent"]

    # typical practice sizes
    r0, w0 = 12, 28

    clear_cache()

    # ── cache hit returns same object ────────────────────────────────────────
    a = glyph_sprite("#00e5ff", "circle", 12, 28, "normal", master=root)
    b = glyph_sprite("#00e5ff", "circle", 12, 28, "normal", master=root)
    assert a is b, "cache hit must return the SAME PhotoImage object"
    # even-up bucketing: 11→12, 27→28 share with 12/28
    c = glyph_sprite("#00e5ff", "circle", 11, 27, "normal", master=root)
    assert c is a, "even-up size bucket must share cache entry"
    d = glyph_sprite("#00e5ff", "circle", 12, 28, "normal", master=root, style="hq")
    assert d is a, "default style must share cache with style='hq'"

    # ── pad sanity ───────────────────────────────────────────────────────────
    assert glyph_pad("circle", "normal") == _PAD_GLOW_12
    assert glyph_pad("ring", "ghost") == _PAD_NO_GLOW
    assert glyph_pad("diamond", "accent") == _PAD_GLOW_12 + _PAD_ACCENT_EXTRA
    assert glyph_pad("circle", "normal", style="classic") == _PAD_CLASSIC
    assert glyph_pad("circle", "accent", style="classic") == _PAD_CLASSIC + _PAD_ACCENT_EXTRA

    # ── HQ default path byte-identical to explicit style='hq' ────────────────
    clear_cache()
    glyph_sprite("#e63946", "circle", 12, 28, "normal", master=root)
    k_def = (_norm_hex("#e63946"), "circle", 12, 28, "normal", "hq")
    assert k_def in _PIL_CACHE
    hq_bytes = _PIL_CACHE[k_def].tobytes()
    hq_hash = hashlib.sha256(hq_bytes).hexdigest()
    glyph_sprite("#e63946", "circle", 12, 28, "normal", master=root, style="hq")
    assert _PIL_CACHE[k_def].tobytes() == hq_bytes

    # classic is a distinct cache key + different pixels from HQ
    glyph_sprite("#e63946", "circle", 12, 28, "normal", master=root, style="classic")
    k_cl = (_norm_hex("#e63946"), "circle", 12, 28, "normal", "classic")
    assert k_cl in _PIL_CACHE
    cl_bytes = _PIL_CACHE[k_cl].tobytes()
    assert cl_bytes != hq_bytes, "classic circle must differ from HQ circle"
    assert len(cl_bytes) > 0 and any(cl_bytes), "classic image non-empty"

    # ── classic: every glyph × sizes, both styles, non-empty ─────────────────
    _CLASSIC_SS_HITS = 0
    clear_cache()
    size_pairs = [(12, 28), (10, 20), (16, 32)]
    for style in ("hq", "classic"):
        for glyph in glyphs:
            for rv, wv in size_pairs:
                ph = glyph_sprite(
                    "#00e5ff", glyph, rv, wv, "normal",
                    master=root, style=style,
                )
                assert ph is not None
                assert ph.width() > 0 and ph.height() > 0
                key = (
                    _norm_hex("#00e5ff"), glyph, _even_up(rv), _even_up(wv),
                    "normal", style,
                )
                pil = _PIL_CACHE[key]
                assert pil.width > 0 and pil.height > 0
                extrema = pil.getextrema()
                assert extrema[3][1] > 0, f"empty alpha for {glyph}/{style}"

    assert _CLASSIC_SS_HITS > 0, (
        "classic cymbal path must hit supersample+LANCZOS "
        f"(hits={_CLASSIC_SS_HITS})"
    )
    # anti-aliased edge: classic circle should have partial-alpha rim pixels
    glyph_sprite("#ff8c00", "circle", 12, 28, "normal", master=root, style="classic")
    ckey = (_norm_hex("#ff8c00"), "circle", 12, 28, "normal", "classic")
    cimg = _PIL_CACHE[ckey]
    partial = 0
    for yy in range(cimg.height):
        for xx in range(cimg.width):
            a = cimg.getpixel((xx, yy))[3]
            if 1 <= a <= 254:
                partial += 1
    assert partial > 0, "classic circle must have AA partial-alpha edge pixels"

    # classic fill must read as solid lane color (not washed white→lc gradient)
    glyph_sprite("#e63946", "circle", 12, 28, "normal", master=root, style="classic")
    flat_key = (_norm_hex("#e63946"), "circle", 12, 28, "normal", "classic")
    flat_img = _PIL_CACHE[flat_key]
    fcx, fcy = flat_img.width // 2, flat_img.height // 2
    fr, fg, fb, fa = flat_img.getpixel((fcx, fcy))
    lc_r, lc_g, lc_b = _rgb("#e63946")
    assert fa >= 250, f"classic center alpha too low: {fa}"
    assert abs(fr - lc_r) <= 20 and abs(fg - lc_g) <= 20 and abs(fb - lc_b) <= 20, (
        f"classic fill washed out: center=({fr},{fg},{fb}) expected near ({lc_r},{lc_g},{lc_b})"
    )
    white_hits = 0
    for yy in range(flat_img.height):
        for xx in range(flat_img.width):
            pr, pg, pb, pa = flat_img.getpixel((xx, yy))
            if pa >= 200 and pr >= 240 and pg >= 240 and pb >= 240:
                white_hits += 1
    assert white_hits > 8, f"classic white outline too faint/missing (hits={white_hits})"

    # ── ring is hollow (center pixel transparent) — HQ ───────────────────────
    glyph_sprite("#33ddaa", "ring", 14, 28, "normal", master=root)
    ring_key = (
        _norm_hex("#33ddaa"), "ring", _even_up(14), _even_up(28), "normal", "hq",
    )
    ring_pil = _PIL_CACHE[ring_key]
    cx_px = ring_pil.width // 2
    cy_px = ring_pil.height // 2
    center_a = ring_pil.getpixel((cx_px, cy_px))[3]
    assert center_a < 20, f"ring center must be hollow/transparent, alpha={center_a}"

    # ── compose composite PNG via PIL ────────────────────────────────────────
    cell_w, cell_h = 72, 56
    cols = [(g, s) for g in glyphs for s in states]
    n_cols = len(cols)
    n_rows = len(colors)
    label_h = 18
    left_label_w = 72
    header_h = 36
    img_w = left_label_w + n_cols * cell_w + 16
    img_h = header_h + n_rows * cell_h + 16
    canvas = Image.new("RGBA", (img_w, img_h), (0x0D, 0x0D, 0x1A, 255))
    draw = ImageDraw.Draw(canvas)

    # column headers (glyph/state)
    for ci, (glyph, state) in enumerate(cols):
        x = left_label_w + ci * cell_w + 4
        short = {
            "circle": "circ", "lozenge": "loz", "lozengeS": "lozS",
            "roundrect": "rr", "bar": "bar", "diamond": "dia",
            "spike": "spk", "ring": "ring", "triangle": "tri",
        }.get(glyph, glyph[:4])
        draw.text((x, 4), f"{short}/{state[0]}", fill=(180, 180, 200, 255))

    for ri, col in enumerate(colors):
        y0 = header_h + ri * cell_h
        draw.text((6, y0 + cell_h // 2 - 6), col, fill=(200, 200, 220, 255))
        for ci, (glyph, state) in enumerate(cols):
            glyph_sprite(col, glyph, r0, w0, state, master=root)
            key = (
                _norm_hex(col), glyph, _even_up(r0), _even_up(w0), state, "hq",
            )
            sprite = _PIL_CACHE[key]
            cx = left_label_w + ci * cell_w + cell_w // 2
            cy = y0 + cell_h // 2
            sx = int(cx - sprite.width // 2)
            sy = int(cy - sprite.height // 2)
            canvas.alpha_composite(sprite, (sx, sy))

    # classic multi-size strip under the HQ grid (every glyph × a couple sizes)
    classic_y = header_h + n_rows * cell_h + 8
    size_pairs_inspect = [(10, 20), (12, 28), (16, 32)]
    classic_row_h = 48
    classic_h = 18 + len(size_pairs_inspect) * classic_row_h + 8
    need_h = classic_y + classic_h
    need_w = max(canvas.width, left_label_w + len(glyphs) * 56 + 16)
    if need_h > canvas.height or need_w > canvas.width:
        bigger = Image.new(
            "RGBA",
            (max(canvas.width, need_w), max(canvas.height, need_h)),
            (0x0D, 0x0D, 0x1A, 255),
        )
        bigger.alpha_composite(canvas, (0, 0))
        canvas = bigger
        draw = ImageDraw.Draw(canvas)
    draw.text(
        (6, classic_y),
        "classic multi-size (flat fill + white outline)",
        fill=(160, 160, 180, 255),
    )
    classic_colors = ["#00e5ff", "#ff8c00", "#e63946"]
    for si, (rv, wv) in enumerate(size_pairs_inspect):
        y0 = classic_y + 16 + si * classic_row_h
        draw.text((6, y0 + 14), f"r{rv}/w{wv}", fill=(180, 180, 200, 255))
        sx0 = left_label_w
        col = classic_colors[si % len(classic_colors)]
        for glyph in glyphs:
            glyph_sprite(col, glyph, rv, wv, "normal", master=root, style="classic")
            key = (
                _norm_hex(col), glyph, _even_up(rv), _even_up(wv),
                "normal", "classic",
            )
            sprite = _PIL_CACHE[key]
            canvas.alpha_composite(
                sprite, (sx0, y0 + (classic_row_h - sprite.height) // 2)
            )
            sx0 += max(48, sprite.width + 6)

    out_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "_practice_sprites_selftest.png",
    )
    canvas.convert("RGB").save(out_path, "PNG")
    assert os.path.isfile(out_path), f"missing {out_path}"
    print(f"wrote {out_path} ({canvas.width}x{canvas.height})")
    print(f"hq_default_sha256={hq_hash} (default path shared with style=hq)")

    # ── LRU eviction past 512 ────────────────────────────────────────────────
    clear_cache()
    first_key_photo = glyph_sprite("#010101", "circle", 10, 20, "normal", master=root)
    first_key = ("#010101", "circle", 10, 20, "normal", "hq")
    assert first_key in _CACHE
    assert first_key_photo is _CACHE[first_key]
    n_extra = _CACHE_CAP  # + the first = CAP+1 → first evicted
    for i in range(n_extra):
        rr = (i * 3) % 256
        gg = (i * 7) % 256
        bb = (i * 11) % 256
        hexc = f"#{rr:02x}{gg:02x}{bb:02x}"
        # vary r/w (even) + glyph/state to avoid key collisions
        rv = 8 + (i % 20) * 2
        wv = 16 + ((i // 20) % 15) * 2
        gl = glyphs[i % len(glyphs)]
        st = states[i % len(states)]
        glyph_sprite(hexc, gl, rv, wv, st, master=root)
    assert len(_CACHE) <= _CACHE_CAP, f"cache size {len(_CACHE)} > {_CACHE_CAP}"
    if first_key in _CACHE:
        # collision path — force more unique inserts
        for j in range(600, 600 + _CACHE_CAP + 8):
            hexc = f"#{(j * 13) % 256:02x}{(j * 17) % 256:02x}{(j * 19) % 256:02x}"
            glyph_sprite(
                hexc, "circle",
                12 + (j % 30) * 2, 20 + (j % 20) * 2,
                "normal", master=root,
            )
    assert len(_CACHE) <= _CACHE_CAP
    assert first_key not in _CACHE, "LRU eviction failed — oldest key still present"

    # touch a live entry and ensure identity is preserved
    live = next(iter(_CACHE))
    live_photo = _CACHE[live]
    glyph_sprite(
        live[0], live[1], live[2], live[3], live[4],
        master=root, style=live[5] if len(live) > 5 else "hq",
    )
    assert _CACHE[live] is live_photo

    # ── PhotoImage dimensions include pad ────────────────────────────────────
    clear_cache()
    ph = glyph_sprite("#00e5ff", "circle", 12, 28, "normal", master=root)
    pad = glyph_pad("circle", "normal")
    bw, bh = _body_dims("circle", 12, 28)
    assert ph.width() == bw + 2 * pad, f"width {ph.width()} != {bw + 2 * pad}"
    assert ph.height() == bh + 2 * pad, f"height {ph.height()} != {bh + 2 * pad}"
    ph_c = glyph_sprite(
        "#00e5ff", "circle", 12, 28, "normal", master=root, style="classic",
    )
    pad_c = glyph_pad("circle", "normal", style="classic")
    assert ph_c.width() == bw + 2 * pad_c
    assert ph_c.height() == bh + 2 * pad_c

    root.destroy()
    print("SELFTEST OK")
    print("SELFTEST PASS  parakit_practice_sprites  (hq + classic)")


if __name__ == "__main__":
    _selftest()
