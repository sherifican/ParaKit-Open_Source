# -*- coding: utf-8 -*-
"""parakit_preview_sprites.py — HQ note-sprite cache for the Preview tab.

Pre-renders RGBA note sprites matching the Preview HTML prototype look
(Preview Track v2 - Web Edition/parakit-preview.html:1890-1966): radial /
vertical gradients, soft glow, ghost outlines, past opacity, flash overlay,
and ring variants. All work is supersampled 4× then LANCZOS-downsampled once
per cache key — never per frame.

Deps: Pillow (ImageTk), tkinter, stdlib. No canvas / tab imports.
"""
from __future__ import annotations

import math
import os
from collections import OrderedDict
from typing import Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageTk

# ── constants ────────────────────────────────────────────────────────────────
_SS = 4  # supersample factor
_CACHE_CAP = 512

# Glow: canvas shadowBlur 12 → GaussianBlur radius ≈ 6.5 @1x; kick blur 9 → ≈ 4.5
_GLOW_BLUR_12 = 6.5
_GLOW_BLUR_9 = 4.5
# Margin around body so blurred glow is not clipped (≈ 2× blur radius + 1)
_PAD_GLOW_12 = 14
_PAD_GLOW_9 = 12
_PAD_NO_GLOW = 3  # stroke / AA only (ghost, flash)

# "oval" = a wide ellipse (owner: cymbals-as-ovals, easier to read rapid runs).
# Renders like "circle" but the radial disc is squashed to the w×h body box.
_KINDS = frozenset({"circle", "oval", "bar", "kickbar", "kickline"})
_STATES = frozenset({"normal", "ghost", "past", "flash"})
# "hq" = current gradient+glow sprites (DEFAULT — byte-identical path).
# "classic" = flat charting-game highway: cymbals = filled circle + crisp white
# outline; drums/kick = filled rounded rect + crisp white outline. 4× SS.
_STYLES = frozenset({"hq", "classic"})
_DEFAULT_STYLE = "hq"

# Classic outline thickness @1× → ~2px after 4× SS + LANCZOS downsample.
# Solid near-opaque white ring (not glow / not faint).
_CLASSIC_OUTLINE_1X = 2.0
_CLASSIC_CORNER_1X = 2.0  # rounded-rect corner radius @1×
# Subtle top lighten only (≤ ~8–10%); rest is solid lane color.
_CLASSIC_TOP_LIGHTEN = 0.08
_PAD_CLASSIC = 4  # outline + AA margin (no glow)

# LRU: key -> ImageTk.PhotoImage (hard refs — Tk GC trap)
_CACHE: "OrderedDict[tuple, ImageTk.PhotoImage]" = OrderedDict()
# Parallel RGBA PIL cache for selftest / reuse of identical bitmaps (same keys)
_PIL_CACHE: "OrderedDict[tuple, Image.Image]" = OrderedDict()

# Selftest / proof: classic circle path ran through supersample+LANCZOS.
_CLASSIC_SS_HITS = 0


# ── public API ───────────────────────────────────────────────────────────────

def sprite_pad(kind: str, state: str, style: str = _DEFAULT_STYLE) -> int:
    """Glow margin in px added around the body box for that kind/state.

    Caller can center-blit with create_image(cx, cy) — the returned PhotoImage
    is body + 2*pad on each axis.
    """
    if kind not in _KINDS:
        raise ValueError(f"unknown kind {kind!r}; expected one of {sorted(_KINDS)}")
    if state not in _STATES:
        raise ValueError(f"unknown state {state!r}; expected one of {sorted(_STATES)}")
    style = _norm_style(style)
    if style == "classic":
        return _PAD_CLASSIC
    if state in ("ghost", "flash"):
        return _PAD_NO_GLOW
    if kind in ("kickbar", "kickline"):
        return _PAD_GLOW_9
    return _PAD_GLOW_12


def clear_cache() -> None:
    """Drop all cached PhotoImage / PIL entries."""
    _CACHE.clear()
    _PIL_CACHE.clear()


def note_sprite(
    color: str,
    kind: str,
    w: int,
    h: int,
    state: str = "normal",
    ring: Optional[str] = None,
    master=None,
    style: str = _DEFAULT_STYLE,
) -> "ImageTk.PhotoImage":
    """Return a cached note PhotoImage.

    kind in {circle, oval, bar, kickbar, kickline};
    state in {normal, ghost, past, flash}.
    style in {hq, classic} — default ``hq`` keeps the pre-existing gradient+glow
    look byte-identical; ``classic`` is a flat filled shape + crisp white outline
    (cymbals = smooth supersampled circle; drums/kick = rounded rect).
    w/h = TARGET pixel box of the note BODY (glow margin is added internally —
    the returned PhotoImage is bigger; use sprite_pad() for half-extents).
    Cached by (color, kind, w, h, state, ring, style) after even-up size bucketing.
    LRU-capped at 512. Hard-refs PhotoImages. ``master`` passed to PhotoImage.
    """
    if kind not in _KINDS:
        raise ValueError(f"unknown kind {kind!r}; expected one of {sorted(_KINDS)}")
    if state not in _STATES:
        raise ValueError(f"unknown state {state!r}; expected one of {sorted(_STATES)}")
    style = _norm_style(style)

    w = _even_up(w)
    h = _even_up(h)
    color = _norm_hex(color)
    ring_key = _norm_hex(ring) if ring else None
    key = (color, kind, w, h, state, ring_key, style)

    hit = _CACHE.get(key)
    if hit is not None:
        _CACHE.move_to_end(key)
        _PIL_CACHE.move_to_end(key)
        return hit

    if style == "classic":
        rgba = _render_classic_rgba(color, kind, w, h, state, ring_key)
    else:
        # Unchanged HQ path — must stay byte-identical to pre-style work.
        rgba = _render_rgba(color, kind, w, h, state, ring_key)
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


def _scale_alpha(img: Image.Image, factor: float) -> Image.Image:
    if factor >= 0.999:
        return img
    r, g, b, a = img.split()
    a = a.point(lambda p, f=factor: int(p * f + 0.5))
    return Image.merge("RGBA", (r, g, b, a))


def _rounded_rect_mask(
    w: int, h: int, radius: int
) -> Image.Image:
    m = Image.new("L", (w, h), 0)
    if w < 1 or h < 1:
        return m
    rad = max(0, min(radius, w // 2, h // 2))
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], radius=rad, fill=255)
    return m


def _draw_radial_disc(
    size: int, lc: Tuple[int, int, int]
) -> Image.Image:
    """Radial gradient disc: 0=#fff, 0.35=lc, 1.0=lc@0.55a. ``size`` is diameter."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    if size < 1:
        return img
    cx = (size - 1) * 0.5
    cy = (size - 1) * 0.5
    r = size * 0.5
    if r < 0.5:
        r = 0.5
    px = img.load()
    white = (255, 255, 255)
    a_edge = int(round(0.55 * 255))
    for y in range(size):
        dy = y - cy
        for x in range(size):
            dx = x - cx
            dist = math.hypot(dx, dy)
            if dist > r:
                continue
            t = dist / r
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


def _draw_flat_rr(
    bw: int, bh: int, radius: int, rgba: Tuple[int, int, int, int]
) -> Image.Image:
    img = Image.new("RGBA", (max(1, bw), max(1, bh)), (0, 0, 0, 0))
    if bw < 1 or bh < 1:
        return img
    rad = max(0, min(radius, bw // 2, bh // 2))
    ImageDraw.Draw(img).rounded_rectangle(
        [0, 0, bw - 1, bh - 1], radius=rad, fill=rgba
    )
    return img


def _stroke_circle(
    canvas: Image.Image,
    cx: float,
    cy: float,
    r: float,
    color: Tuple[int, int, int, int],
    width: float,
) -> None:
    if r <= 0 or width <= 0:
        return
    d = ImageDraw.Draw(canvas)
    # Pillow ellipse outline is centered on the path
    x0, y0 = cx - r, cy - r
    x1, y1 = cx + r, cy + r
    d.ellipse([x0, y0, x1, y1], outline=color, width=max(1, int(round(width))))


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


def _glow_layer(
    ss_w: int,
    ss_h: int,
    shape: Image.Image,
    ox: int,
    oy: int,
    lc: Tuple[int, int, int],
    blur_r_1x: float,
    strength: float = 0.85,
) -> Image.Image:
    """Tint ``shape`` with lane color, blur, place at (ox,oy) on ss canvas."""
    layer = Image.new("RGBA", (ss_w, ss_h), (0, 0, 0, 0))
    # solid lane-color version of the shape alpha
    solid = Image.new("RGBA", shape.size, (*lc, 255))
    tinted = Image.new("RGBA", shape.size, (0, 0, 0, 0))
    # use shape alpha (any channel that has coverage)
    if shape.mode != "RGBA":
        shape = shape.convert("RGBA")
    _, _, _, a = shape.split()
    a = a.point(lambda p, s=strength: int(p * s + 0.5))
    tinted.paste(solid, (0, 0), a)
    blur_r = max(0.5, blur_r_1x * _SS)
    tinted = tinted.filter(ImageFilter.GaussianBlur(radius=blur_r))
    layer.alpha_composite(tinted, (ox, oy))
    return layer


def _render_rgba(
    color: str,
    kind: str,
    w: int,
    h: int,
    state: str,
    ring: Optional[str],
) -> Image.Image:
    """Render one sprite at target resolution (with pad), supersampled then downsampled."""
    pad = sprite_pad(kind, state)
    out_w = w + 2 * pad
    out_h = h + 2 * pad
    ss_w = out_w * _SS
    ss_h = out_h * _SS
    ss_pad = pad * _SS
    ss_bw = w * _SS
    ss_bh = h * _SS
    lc = _rgb(color)
    ring_rgb = _rgb(ring) if ring else None

    # body origin in supersampled canvas
    bx, by = ss_pad, ss_pad
    cx = bx + ss_bw * 0.5
    cy = by + ss_bh * 0.5

    base = Image.new("RGBA", (ss_w, ss_h), (0, 0, 0, 0))

    # ── FLASH: solid white shape, no glow ────────────────────────────────────
    if state == "flash":
        if kind == "circle":
            diam = min(ss_bw, ss_bh)
            disc = Image.new("RGBA", (diam, diam), (0, 0, 0, 0))
            ImageDraw.Draw(disc).ellipse([0, 0, diam - 1, diam - 1], fill=(255, 255, 255, 255))
            ox = int(round(cx - diam * 0.5))
            oy = int(round(cy - diam * 0.5))
            base.alpha_composite(disc, (ox, oy))
        elif kind == "oval":
            ImageDraw.Draw(base).ellipse(
                [bx, by, bx + ss_bw - 1, by + ss_bh - 1], fill=(255, 255, 255, 255))
        else:
            rad = 2 * _SS if kind in ("kickbar", "kickline") else 5 * _SS
            shape = _draw_flat_rr(ss_bw, ss_bh, rad, (255, 255, 255, 255))
            base.alpha_composite(shape, (bx, by))
        return base.resize((out_w, out_h), Image.Resampling.LANCZOS)

    # ── GHOST: outline only, no gradient, no glow ────────────────────────────
    if state == "ghost":
        stroke_c = (*ring_rgb, 255) if ring_rgb else (*lc, 255)
        sw = 2.0 * _SS
        if kind == "circle":
            r = min(ss_bw, ss_bh) * 0.5 - sw * 0.5
            r = max(1.0, r)
            _stroke_circle(base, cx, cy, r, stroke_c, sw)
        elif kind == "oval":
            inset = sw * 0.5
            ImageDraw.Draw(base).ellipse(
                [bx + inset, by + inset, bx + ss_bw - 1 - inset, by + ss_bh - 1 - inset],
                outline=stroke_c, width=max(1, int(round(sw))))
        else:
            rad = 2 * _SS if kind in ("kickbar", "kickline") else 5 * _SS
            # inset slightly so stroke stays inside body+pad
            inset = max(0, int(round(sw * 0.5)))
            box = (bx + inset, by + inset, bx + ss_bw - 1 - inset, by + ss_bh - 1 - inset)
            _stroke_rr(base, box, max(0, rad - inset), stroke_c, sw)
        return base.resize((out_w, out_h), Image.Resampling.LANCZOS)

    # ── NORMAL / PAST (past = normal then bake 45% opacity) ──────────────────
    want_glow = True
    is_kick = kind in ("kickbar", "kickline")

    if kind == "circle":
        diam = min(ss_bw, ss_bh)
        disc = _draw_radial_disc(diam, lc)
        ox = int(round(cx - diam * 0.5))
        oy = int(round(cy - diam * 0.5))
        if want_glow:
            glow = _glow_layer(ss_w, ss_h, disc, ox, oy, lc, _GLOW_BLUR_12, strength=0.75)
            base = Image.alpha_composite(base, glow)
        base.alpha_composite(disc, (ox, oy))
        # stroke
        r = diam * 0.5
        if ring_rgb:
            _stroke_circle(base, cx, cy, r - 1.25 * _SS, (*ring_rgb, 255), 2.5 * _SS)
        else:
            _stroke_circle(
                base, cx, cy, r - 0.75 * _SS, (255, 255, 255, int(0.55 * 255)), 1.5 * _SS
            )

    elif kind == "oval":
        # Squash a full-width radial disc into the w x h body box -> a wide
        # ellipse that keeps the white-core gradient. Same glow + white/ring
        # stroke as the circle, drawn as an ellipse.
        disc = _draw_radial_disc(ss_bw, lc).resize(
            (ss_bw, ss_bh), Image.Resampling.LANCZOS)
        if want_glow:
            glow = _glow_layer(ss_w, ss_h, disc, bx, by, lc, _GLOW_BLUR_12, strength=0.75)
            base = Image.alpha_composite(base, glow)
        base.alpha_composite(disc, (bx, by))
        _dr = ImageDraw.Draw(base)
        if ring_rgb:
            _dr.ellipse([bx + 1.25 * _SS, by + 1.25 * _SS,
                         bx + ss_bw - 1 - 1.25 * _SS, by + ss_bh - 1 - 1.25 * _SS],
                        outline=(*ring_rgb, 255), width=max(1, int(round(2.5 * _SS))))
        else:
            _dr.ellipse([bx + 0.75 * _SS, by + 0.75 * _SS,
                         bx + ss_bw - 1 - 0.75 * _SS, by + ss_bh - 1 - 0.75 * _SS],
                        outline=(255, 255, 255, int(0.55 * 255)),
                        width=max(1, int(round(1.5 * _SS))))

    elif kind == "bar":
        rad = 5 * _SS
        body = _draw_bar_gradient(ss_bw, ss_bh, rad, lc)
        if want_glow:
            glow = _glow_layer(ss_w, ss_h, body, bx, by, lc, _GLOW_BLUR_12, strength=0.75)
            base = Image.alpha_composite(base, glow)
        base.alpha_composite(body, (bx, by))
        if ring_rgb:
            _stroke_rr(
                base,
                (bx, by, bx + ss_bw - 1, by + ss_bh - 1),
                rad,
                (*ring_rgb, 255),
                2.5 * _SS,
            )
        else:
            _stroke_rr(
                base,
                (bx, by, bx + ss_bw - 1, by + ss_bh - 1),
                rad,
                (255, 255, 255, int(0.35 * 255)),
                1.2 * _SS,
            )

    else:  # kickbar / kickline — flat lc @ 85% alpha, glow blur 9
        rad = 2 * _SS
        fill_a = int(round(0.85 * 255))
        body = _draw_flat_rr(ss_bw, ss_bh, rad, (*lc, fill_a))
        if want_glow:
            glow = _glow_layer(ss_w, ss_h, body, bx, by, lc, _GLOW_BLUR_9, strength=0.80)
            base = Image.alpha_composite(base, glow)
        base.alpha_composite(body, (bx, by))
        if ring_rgb:
            _stroke_rr(
                base,
                (bx, by, bx + ss_bw - 1, by + ss_bh - 1),
                rad,
                (*ring_rgb, 255),
                2.5 * _SS,
            )
        else:
            # Thin white outline (matches the design note + the 'bar' kind): plain
            # kick bars are wide and stack close together, so without a separating
            # stroke adjacent kicks blend into one block (owner-reported).
            _stroke_rr(
                base,
                (bx, by, bx + ss_bw - 1, by + ss_bh - 1),
                rad,
                (255, 255, 255, int(0.35 * 255)),
                1.2 * _SS,
            )

    out = base.resize((out_w, out_h), Image.Resampling.LANCZOS)
    if state == "past":
        out = _scale_alpha(out, 0.45)
    return out


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


def _classic_ellipse_mask(bw: int, bh: int) -> Image.Image:
    m = Image.new("L", (max(1, bw), max(1, bh)), 0)
    if bw < 1 or bh < 1:
        return m
    ImageDraw.Draw(m).ellipse([0, 0, bw - 1, bh - 1], fill=255)
    return m


def _render_classic_rgba(
    color: str,
    kind: str,
    w: int,
    h: int,
    state: str,
    ring: Optional[str],
) -> Image.Image:
    """Classic highway style: flat fill + solid white outline, 4× supersampled.

    Cymbals (circle/oval) are filled ellipses with a crisp anti-aliased white
    ring. Drums/kick (bar/kickbar/kickline) are filled rounded rects with the
    same white outline. No HQ glow / radial gradient.
    """
    global _CLASSIC_SS_HITS
    pad = sprite_pad(kind, state, "classic")
    out_w = w + 2 * pad
    out_h = h + 2 * pad
    ss_w = out_w * _SS
    ss_h = out_h * _SS
    ss_pad = pad * _SS
    ss_bw = w * _SS
    ss_bh = h * _SS
    lc = _rgb(color)
    ring_rgb = _rgb(ring) if ring else None
    outline_rgb = ring_rgb if ring_rgb else (255, 255, 255)
    sw = _CLASSIC_OUTLINE_1X * _SS
    corner = max(1, int(round(_CLASSIC_CORNER_1X * _SS)))
    if kind in ("kickbar", "kickline"):
        corner = max(1, int(round(2 * _SS)))  # slightly tighter for thin bars

    bx, by = ss_pad, ss_pad
    cx = bx + ss_bw * 0.5
    cy = by + ss_bh * 0.5
    base = Image.new("RGBA", (ss_w, ss_h), (0, 0, 0, 0))
    is_cymbal = kind in ("circle", "oval")

    if state == "flash":
        fill = (255, 255, 255, 255)
        if is_cymbal:
            ImageDraw.Draw(base).ellipse(
                [bx, by, bx + ss_bw - 1, by + ss_bh - 1], fill=fill
            )
        else:
            body = _draw_flat_rr(ss_bw, ss_bh, corner, fill)
            base.alpha_composite(body, (bx, by))
        _CLASSIC_SS_HITS += 1 if is_cymbal else 0
        return base.resize((out_w, out_h), Image.Resampling.LANCZOS)

    if state == "ghost":
        stroke_c = (*outline_rgb, 255)
        if is_cymbal:
            inset = sw * 0.5
            ImageDraw.Draw(base).ellipse(
                [bx + inset, by + inset,
                 bx + ss_bw - 1 - inset, by + ss_bh - 1 - inset],
                outline=stroke_c, width=max(1, int(round(sw))),
            )
            _CLASSIC_SS_HITS += 1
        else:
            inset = max(0, int(round(sw * 0.5)))
            box = (bx + inset, by + inset,
                   bx + ss_bw - 1 - inset, by + ss_bh - 1 - inset)
            _stroke_rr(base, box, max(0, corner - inset), stroke_c, sw)
        return base.resize((out_w, out_h), Image.Resampling.LANCZOS)

    # normal / past — solid-ish body + crisp ~2px white outline (even all around)
    outline_rgba = (*outline_rgb, 255)  # near-opaque; white unless ring override
    stroke_w = max(1, int(round(sw)))  # sw = _CLASSIC_OUTLINE_1X * _SS ≈ 8 @4×
    if is_cymbal:
        mask = _classic_ellipse_mask(ss_bw, ss_bh)
        body = _classic_flat_fill(ss_bw, ss_bh, lc, mask)
        base.alpha_composite(body, (bx, by))
        # Center stroke on the rim: half inside / half outside → bright outer ring.
        inset = sw * 0.5
        ImageDraw.Draw(base).ellipse(
            [bx + inset, by + inset,
             bx + ss_bw - 1 - inset, by + ss_bh - 1 - inset],
            outline=outline_rgba,
            width=stroke_w,
        )
        _CLASSIC_SS_HITS += 1
    else:
        mask = _rounded_rect_mask(ss_bw, ss_bh, corner)
        body = _classic_flat_fill(ss_bw, ss_bh, lc, mask)
        base.alpha_composite(body, (bx, by))
        # Full-width kick lines get NO white outline in classic mode: a
        # full-bleed stroked bar reads as a boxed/outlined line (owner-
        # reported). A ring override (selection/flag) still strokes.
        if kind != "kickline" or ring_rgb is not None:
            _stroke_rr(
                base,
                (bx, by, bx + ss_bw - 1, by + ss_bh - 1),
                corner,
                outline_rgba,
                float(stroke_w),
            )

    out = base.resize((out_w, out_h), Image.Resampling.LANCZOS)
    if state == "past":
        out = _scale_alpha(out, 0.45)
    return out


# ── selftest ─────────────────────────────────────────────────────────────────

def _selftest() -> None:
    import hashlib
    import tkinter as tk

    global _CLASSIC_SS_HITS

    root = tk.Tk()
    root.withdraw()

    colors = [
        "#00e5ff",
        "#ff8c00",
        "#e63946",
        "#3a5fc8",
        "#2e8b57",
        "#9b4fc0",
        "#ffd700",
        "#ff69b4",
    ]
    kinds = ["circle", "oval", "bar", "kickbar", "kickline"]
    states = ["normal", "ghost", "past"]
    sizes = {
        "circle": (24, 24),
        "oval": (32, 16),
        "bar": (28, 14),
        "kickbar": (36, 10),
        "kickline": (80, 6),
    }

    clear_cache()

    # ── cache hit returns same object ────────────────────────────────────────
    a = note_sprite("#00e5ff", "circle", 24, 24, "normal", master=root)
    b = note_sprite("#00e5ff", "circle", 24, 24, "normal", master=root)
    assert a is b, "cache hit must return the SAME PhotoImage object"
    # even-up bucketing: 23→24 shares with 24
    c = note_sprite("#00e5ff", "circle", 23, 23, "normal", master=root)
    assert c is a, "even-up size bucket must share cache entry"
    # default style is hq — explicit style="hq" shares the same cache entry
    d = note_sprite("#00e5ff", "circle", 24, 24, "normal", master=root, style="hq")
    assert d is a, "default style must share cache with style='hq'"

    # ── pad sanity ───────────────────────────────────────────────────────────
    assert sprite_pad("circle", "normal") == _PAD_GLOW_12
    assert sprite_pad("kickline", "normal") == _PAD_GLOW_9
    assert sprite_pad("bar", "ghost") == _PAD_NO_GLOW
    assert sprite_pad("circle", "flash") == _PAD_NO_GLOW
    assert sprite_pad("circle", "normal", style="classic") == _PAD_CLASSIC
    assert sprite_pad("bar", "normal", style="classic") == _PAD_CLASSIC

    # ── HQ default path byte-identical to explicit style='hq' ────────────────
    clear_cache()
    note_sprite("#e63946", "circle", 24, 24, "normal", master=root)
    note_sprite("#e63946", "circle", 24, 24, "normal", master=root, style="hq")
    k_def = (_norm_hex("#e63946"), "circle", 24, 24, "normal", None, "hq")
    assert k_def in _PIL_CACHE
    # single entry (same key) — prove default and style='hq' collide
    assert sum(1 for k in _PIL_CACHE if k[:6] == k_def[:6]) == 1
    hq_bytes = _PIL_CACHE[k_def].tobytes()
    hq_hash = hashlib.sha256(hq_bytes).hexdigest()

    # classic is a distinct cache key + different pixels from HQ
    note_sprite("#e63946", "circle", 24, 24, "normal", master=root, style="classic")
    k_cl = (_norm_hex("#e63946"), "circle", 24, 24, "normal", None, "classic")
    assert k_cl in _PIL_CACHE
    cl_bytes = _PIL_CACHE[k_cl].tobytes()
    assert cl_bytes != hq_bytes, "classic circle must differ from HQ circle"
    assert len(cl_bytes) > 0 and any(cl_bytes), "classic image non-empty"

    # ── classic: every kind × sizes, both styles, non-empty ──────────────────
    _CLASSIC_SS_HITS = 0
    clear_cache()
    test_sizes = [(24, 24), (16, 16), (28, 14), (36, 10)]
    for style in ("hq", "classic"):
        for kind in kinds:
            for bw, bh in test_sizes:
                if kind == "kickline":
                    bw, bh = max(bw, 40), min(bh, 8) if bh > 2 else 6
                ph = note_sprite(
                    "#00e5ff", kind, bw, bh, "normal",
                    master=root, style=style,
                )
                assert ph is not None
                assert ph.width() > 0 and ph.height() > 0
                key = (
                    _norm_hex("#00e5ff"), kind, _even_up(bw), _even_up(bh),
                    "normal", None, style,
                )
                pil = _PIL_CACHE[key]
                assert pil.width > 0 and pil.height > 0
                # non-empty alpha somewhere
                extrema = pil.getextrema()
                assert extrema[3][1] > 0, f"empty alpha for {kind}/{style}"

    assert _CLASSIC_SS_HITS > 0, (
        "classic cymbal path must hit supersample+LANCZOS "
        f"(hits={_CLASSIC_SS_HITS})"
    )
    # anti-aliased edge: classic circle should have partial-alpha rim pixels
    note_sprite("#ff8c00", "circle", 24, 24, "normal", master=root, style="classic")
    ckey = (_norm_hex("#ff8c00"), "circle", 24, 24, "normal", None, "classic")
    cimg = _PIL_CACHE[ckey]
    partial = 0
    for yy in range(cimg.height):
        for xx in range(cimg.width):
            a = cimg.getpixel((xx, yy))[3]
            if 1 <= a <= 254:
                partial += 1
    assert partial > 0, "classic circle must have AA partial-alpha edge pixels"

    # classic fill must read as solid lane color (not washed white→lc gradient)
    note_sprite("#e63946", "circle", 24, 24, "normal", master=root, style="classic")
    flat_key = (_norm_hex("#e63946"), "circle", 24, 24, "normal", None, "classic")
    flat_img = _PIL_CACHE[flat_key]
    fcx, fcy = flat_img.width // 2, flat_img.height // 2
    fr, fg, fb, fa = flat_img.getpixel((fcx, fcy))
    lc_r, lc_g, lc_b = _rgb("#e63946")
    assert fa >= 250, f"classic center alpha too low: {fa}"
    assert abs(fr - lc_r) <= 20 and abs(fg - lc_g) <= 20 and abs(fb - lc_b) <= 20, (
        f"classic fill washed out: center=({fr},{fg},{fb}) expected near ({lc_r},{lc_g},{lc_b})"
    )
    # white outline present near rim (sample a mid-right edge pixel region)
    white_hits = 0
    for yy in range(flat_img.height):
        for xx in range(flat_img.width):
            pr, pg, pb, pa = flat_img.getpixel((xx, yy))
            if pa >= 200 and pr >= 240 and pg >= 240 and pb >= 240:
                white_hits += 1
    assert white_hits > 8, f"classic white outline too faint/missing (hits={white_hits})"

    # ── compose composite PNG via PIL (not screen capture) ───────────────────
    cell_w, cell_h = 100, 56
    # layout: rows = colors, cols = kind×state (HQ grid, as before)
    cols = [(k, s) for k in kinds if k != "oval" for s in states]
    n_cols = len(cols)
    n_rows = len(colors)
    label_h = 18
    left_label_w = 72
    img_w = left_label_w + n_cols * cell_w + 16
    img_h = 28 + n_rows * cell_h + label_h + 16
    canvas = Image.new("RGBA", (img_w, img_h), (0x0D, 0x0D, 0x1A, 255))
    draw = ImageDraw.Draw(canvas)

    # column headers
    for ci, (kind, state) in enumerate(cols):
        x = left_label_w + ci * cell_w + cell_w // 2
        label = f"{kind[:4]}/{state[:1]}"
        draw.text((x - 18, 6), label, fill=(180, 180, 200, 255))

    for ri, col in enumerate(colors):
        y0 = 28 + ri * cell_h
        draw.text((8, y0 + cell_h // 2 - 6), col, fill=(200, 200, 220, 255))
        for ci, (kind, state) in enumerate(cols):
            bw, bh = sizes[kind]
            # render via the same path note_sprite uses (populate cache + PIL)
            note_sprite(col, kind, bw, bh, state, master=root)
            key = (
                _norm_hex(col), kind, _even_up(bw), _even_up(bh),
                state, None, "hq",
            )
            sprite = _PIL_CACHE[key]
            # center in cell
            cx = left_label_w + ci * cell_w + cell_w // 2
            cy = y0 + cell_h // 2
            sx = int(cx - sprite.width // 2)
            sy = int(cy - sprite.height // 2)
            canvas.alpha_composite(sprite, (sx, sy))

    # also sample flash + ring + classic variants in a footer strip
    footer_y = 28 + n_rows * cell_h + 4
    draw.text((8, footer_y), "flash+ring+classic samples", fill=(160, 160, 180, 255))
    samples = [
        ("#00e5ff", "circle", 24, 24, "flash", None, "hq"),
        ("#e63946", "bar", 28, 14, "flash", None, "hq"),
        ("#3a5fc8", "circle", 24, 24, "normal", "#ffffff", "hq"),
        ("#2e8b57", "bar", 28, 14, "normal", "#ff8c00", "hq"),
        ("#ffd700", "circle", 24, 24, "normal", "#ff69b4", "hq"),
        ("#9b4fc0", "kickbar", 36, 10, "normal", None, "hq"),
        ("#ff69b4", "kickline", 80, 6, "past", None, "hq"),
        ("#00e5ff", "circle", 24, 24, "normal", None, "classic"),
        ("#ff8c00", "oval", 32, 16, "normal", None, "classic"),
        ("#e63946", "bar", 28, 14, "normal", None, "classic"),
        ("#3a5fc8", "kickbar", 36, 10, "normal", None, "classic"),
    ]
    sx0 = left_label_w
    for col, kind, bw, bh, state, ring, style in samples:
        note_sprite(col, kind, bw, bh, state, ring=ring, master=root, style=style)
        key = (
            _norm_hex(col),
            kind,
            _even_up(bw),
            _even_up(bh),
            state,
            _norm_hex(ring) if ring else None,
            style,
        )
        sprite = _PIL_CACHE[key]
        canvas.alpha_composite(sprite, (sx0, footer_y + 2))
        sx0 += sprite.width + 12

    # classic multi-size inspect strip (every kind × two sizes)
    classic_y = footer_y + 36
    classic_sizes = {
        "circle": [(16, 16), (24, 24), (32, 32)],
        "oval": [(24, 12), (32, 16), (40, 20)],
        "bar": [(20, 10), (28, 14), (36, 18)],
        "kickbar": [(28, 8), (36, 10), (48, 12)],
        "kickline": [(48, 4), (80, 6), (96, 8)],
    }
    classic_colors = ["#00e5ff", "#ff8c00", "#e63946", "#3a5fc8", "#2e8b57"]
    classic_row_h = 44
    need_h = classic_y + 20 + len(kinds) * classic_row_h + 8
    if need_h > canvas.height or canvas.width < left_label_w + 520:
        bigger = Image.new(
            "RGBA",
            (max(canvas.width, left_label_w + 520), max(canvas.height, need_h)),
            (0x0D, 0x0D, 0x1A, 255),
        )
        bigger.alpha_composite(canvas, (0, 0))
        canvas = bigger
        # re-blit footer samples after resize
        sx0 = left_label_w
        for col, kind, bw, bh, state, ring, style in samples:
            key = (
                _norm_hex(col),
                kind,
                _even_up(bw),
                _even_up(bh),
                state,
                _norm_hex(ring) if ring else None,
                style,
            )
            sprite = _PIL_CACHE[key]
            canvas.alpha_composite(sprite, (sx0, footer_y + 2))
            sx0 += sprite.width + 12
        draw = ImageDraw.Draw(canvas)
    else:
        draw = ImageDraw.Draw(canvas)
    draw.text((8, classic_y), "classic multi-size (flat fill + white outline)", fill=(160, 160, 180, 255))
    for ki, kind in enumerate(kinds):
        y0 = classic_y + 18 + ki * classic_row_h
        draw.text((8, y0 + 12), kind, fill=(200, 200, 220, 255))
        sx0 = left_label_w
        for si, (bw, bh) in enumerate(classic_sizes[kind]):
            col = classic_colors[si % len(classic_colors)]
            note_sprite(col, kind, bw, bh, "normal", master=root, style="classic")
            key = (
                _norm_hex(col), kind, _even_up(bw), _even_up(bh),
                "normal", None, "classic",
            )
            sprite = _PIL_CACHE[key]
            canvas.alpha_composite(sprite, (sx0, y0 + (classic_row_h - sprite.height) // 2))
            sx0 += sprite.width + 10

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_sprites_selftest.png")
    canvas.convert("RGB").save(out_path, "PNG")
    assert os.path.isfile(out_path), f"missing {out_path}"
    print(f"wrote {out_path} ({canvas.width}x{canvas.height})")
    print(f"hq_default_sha256={hq_hash} (default path shared with style=hq)")

    # ── LRU eviction past 512 ────────────────────────────────────────────────
    clear_cache()
    first_key_photo = note_sprite("#010101", "circle", 10, 10, "normal", master=root)
    first_key = ("#010101", "circle", 10, 10, "normal", None, "hq")
    assert first_key in _CACHE
    # fill to capacity with unique colors/sizes
    n_extra = _CACHE_CAP  # + the first = CAP+1 → first evicted
    for i in range(n_extra):
        # unique keys via color channel + size
        r = (i * 3) % 256
        g = (i * 7) % 256
        b = (i * 11) % 256
        hexc = f"#{r:02x}{g:02x}{b:02x}"
        # vary size to avoid even-up collisions collapsing keys
        ww = 8 + (i % 40) * 2
        hh = 8 + ((i // 40) % 20) * 2
        kind = kinds[i % len(kinds)]
        state = states[i % len(states)]
        note_sprite(hexc, kind, ww, hh, state, master=root)
    assert len(_CACHE) <= _CACHE_CAP, f"cache size {len(_CACHE)} > {_CACHE_CAP}"
    # After CAP+1 inserts, the oldest (#010101…) must be gone unless it was
    # re-touched — it was not.
    if first_key in _CACHE:
        # possible if a later key collided after even-up/color wrap — force more
        for j in range(600, 600 + _CACHE_CAP + 8):
            hexc = f"#{(j*13)%256:02x}{(j*17)%256:02x}{(j*19)%256:02x}"
            note_sprite(hexc, "circle", 12 + (j % 30) * 2, 12 + (j % 20) * 2, "normal", master=root)
    assert len(_CACHE) <= _CACHE_CAP
    assert first_key not in _CACHE, "LRU eviction failed — oldest key still present"
    # touch a live entry and ensure it is not the next eviction victim immediately
    live = next(iter(_CACHE))
    live_photo = _CACHE[live]
    note_sprite(
        live[0], live[1], live[2], live[3], live[4], live[5],
        master=root, style=live[6] if len(live) > 6 else "hq",
    )
    assert _CACHE[live] is live_photo

    # ── PhotoImage dimensions include pad ────────────────────────────────────
    clear_cache()
    ph = note_sprite("#00e5ff", "circle", 24, 24, "normal", master=root)
    pad = sprite_pad("circle", "normal")
    assert ph.width() == 24 + 2 * pad, f"width {ph.width()} != {24 + 2 * pad}"
    assert ph.height() == 24 + 2 * pad, f"height {ph.height()} != {24 + 2 * pad}"
    ph_c = note_sprite("#00e5ff", "circle", 24, 24, "normal", master=root, style="classic")
    pad_c = sprite_pad("circle", "normal", style="classic")
    assert ph_c.width() == 24 + 2 * pad_c
    assert ph_c.height() == 24 + 2 * pad_c

    root.destroy()
    print("SELFTEST OK")
    print("SELFTEST PASS  parakit_preview_sprites  (hq + classic)")


if __name__ == "__main__":
    _selftest()
