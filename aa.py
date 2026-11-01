"""Antialiased drop-in replacements for pygame.draw (same arguments).

Opaque shapes get smooth edges: filled polygons and circles are drawn as usual
and then traced with an antialiased outline in the same colour; slanted lines
and thick lines become filled, antialiased quads. Anything with transparency
(shadows, erasing with (0, 0, 0, 0)) is drawn exactly as pygame.draw does, so
cut-outs and alpha tricks behave as before - except see-through filled
ellipses on a per-pixel-alpha surface (the soft shadows), which are
supersampled. Axis-aligned lines are left crisp.

Smooths edges on pygame-ce 2.4+; on classic pygame it falls back to pygame.draw.
"""
import math
import pygame
import pygame.gfxdraw as gfx

_D = pygame.draw
SS = 4      # supersampling factor for see-through ellipses


def _alpha(col):
    if isinstance(col, pygame.Color):
        return col.a
    return col[3] if len(col) > 3 else 255


def _opaque(col):
    return _alpha(col) == 255


def _rgb(col):
    return tuple(int(v) for v in tuple(col)[:3])


def polygon(s, col, pts, width=0):
    pts = [(float(x), float(y)) for x, y in pts]
    if len(pts) < 3 or not _opaque(col):
        return _D.polygon(s, col, pts, width)
    c = _rgb(col)
    if width == 0:
        r = _D.polygon(s, c, pts)
        _D.aalines(s, c, True, pts)
        return r
    if width == 1:
        return _D.aalines(s, c, True, pts)
    return lines(s, c, True, pts, width)


def line(s, col, a, b, width=1):
    a = (float(a[0]), float(a[1]))
    b = (float(b[0]), float(b[1]))
    dx, dy = b[0] - a[0], b[1] - a[1]
    if not _opaque(col) or abs(dx) < 0.01 or abs(dy) < 0.01 or width < 1:
        return _D.line(s, col, a, b, width)
    c = _rgb(col)
    if width == 1:
        return _D.aaline(s, c, a, b)
    ln = math.hypot(dx, dy)
    # pygame measures a thick line's width along the minor axis; keep its weight
    # (the antialiased rim adds about half a pixel each side, so trim the core)
    t = max(0.5, width * max(abs(dx), abs(dy)) / ln / 2 - 0.5)
    nx, ny = -dy / ln * t, dx / ln * t
    q = [(a[0] + nx, a[1] + ny), (b[0] + nx, b[1] + ny), (b[0] - nx, b[1] - ny), (a[0] - nx, a[1] - ny)]
    r = _D.polygon(s, c, q)
    _D.aalines(s, c, True, q)
    return r


def lines(s, col, closed, pts, width=1):
    pts = [(float(x), float(y)) for x, y in pts]
    if len(pts) < 2 or not _opaque(col):
        return _D.lines(s, col, closed, pts, width)
    c = _rgb(col)
    if width <= 1:
        return _D.aalines(s, c, closed, pts)
    segs = list(zip(pts, pts[1:])) + ([(pts[-1], pts[0])] if closed else [])
    r = None
    for p, q in segs:
        rr = line(s, c, p, q, width)
        r = rr if r is None else r.union(rr)
    if width >= 3:      # round the joints so corners don't notch
        for p in (pts if closed else pts[1:-1]):
            circle(s, c, p, (width - 1) / 2)
    return r


def circle(s, col, center, radius, width=0, **kw):
    cx, cy = round(center[0]), round(center[1])
    rad = round(radius)
    if kw or rad < 2 or not _opaque(col):
        return _D.circle(s, col, center, radius, width, **kw)
    c = _rgb(col)
    if hasattr(_D, "aacircle"):          # pygame-ce 2.5+: respects per-pixel alpha
        if width > 0 and rad - width < 0:
            width = 0
        return _D.aacircle(s, c, (cx, cy), rad, width)
    if s.get_flags() & pygame.SRCALPHA:   # gfxdraw would punch see-through rims
        return _D.circle(s, col, center, radius, width)
    if width == 0:
        r = _D.circle(s, c, (cx, cy), rad)
        gfx.aacircle(s, cx, cy, rad, c)
        return r
    if width == 1:
        gfx.aacircle(s, cx, cy, rad, c)
        return pygame.Rect(cx - rad, cy - rad, 2 * rad + 1, 2 * rad + 1)
    r = _D.circle(s, c, (cx, cy), rad, width)
    gfx.aacircle(s, cx, cy, rad, c)
    if rad - width >= 1:
        gfx.aacircle(s, cx, cy, rad - width + 1, c)
    return r


def ellipse(s, col, rect, width=0):
    rect = pygame.Rect(rect)
    if rect.w < 4 or rect.h < 4:
        return _D.ellipse(s, col, rect, width)
    if not _opaque(col):
        area = s.get_clip().clip(rect)
        if (width == 0 and _alpha(col) > 0 and area == rect and s.get_flags() & pygame.SRCALPHA
                and s.subsurface(area).get_bounding_rect(min_alpha=1).w == 0):
            # a soft shadow on a clear sheet: supersample it for a smooth rim
            big = pygame.Surface((rect.w * SS, rect.h * SS), pygame.SRCALPHA)
            _D.ellipse(big, col, big.get_rect())
            s.blit(pygame.transform.smoothscale(big, rect.size), rect.topleft, special_flags=pygame.BLEND_RGBA_MAX)
            return rect
        return _D.ellipse(s, col, rect, width)
    if s.get_flags() & pygame.SRCALPHA:   # gfxdraw would punch see-through rims
        return _D.ellipse(s, col, rect, width)
    c = _rgb(col)
    cx, cy = rect.x + rect.w // 2, rect.y + rect.h // 2
    rx, ry = rect.w // 2, rect.h // 2
    if width == 0:
        r = _D.ellipse(s, c, rect)
        gfx.aaellipse(s, cx, cy, rx, ry, c)
        return r
    if width == 1:
        gfx.aaellipse(s, cx, cy, rx, ry, c)
        return rect
    r = _D.ellipse(s, c, rect, width)
    gfx.aaellipse(s, cx, cy, rx, ry, c)
    if rx - width >= 1 and ry - width >= 1:
        gfx.aaellipse(s, cx, cy, rx - width + 1, ry - width + 1, c)
    return r


def arc(s, col, rect, start, stop, width=1):
    rect = pygame.Rect(rect)
    if not _opaque(col) or rect.w < 4 or rect.h < 4:
        return _D.arc(s, col, rect, start, stop, width)
    if stop < start:
        stop += 2 * math.pi
    rx, ry = rect.w / 2 - width / 2, rect.h / 2 - width / 2
    cx, cy = rect.x + rect.w / 2, rect.y + rect.h / 2
    n = max(6, int((stop - start) * max(rx, ry) / 3))
    pts = [(cx + rx * math.cos(start + (stop - start) * i / n), cy - ry * math.sin(start + (stop - start) * i / n))
           for i in range(n + 1)]
    lines(s, col, False, pts, width)
    return rect


# Classic pygame 2.6's aalines can crash on shapes that run off the surface, so
# outside pygame-ce (the version in requirements.txt) just draw as before.
if not getattr(pygame, "IS_CE", False):
    polygon, line, lines = _D.polygon, _D.line, _D.lines
    circle, ellipse, arc = _D.circle, _D.ellipse, _D.arc
