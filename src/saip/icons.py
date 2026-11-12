"""The SAIP mark, drawn in code so the tray, the page and the favicon all use the same shape.

The mark is a line that runs along, loops back over itself once, and runs on: work that flows, gets caught in a
loop, and carries on. It is defined once as cubic Bezier segments on a 24-unit grid (`SEGMENTS`); `svg()` writes
it for the browser and `render()` draws it with Pillow for the tray.

States change the mark, not just its colour, so they can be told apart without colour:

    flow      the mark
    stuck     the loop filled in: caught in it
    paused    the line broken after the loop
    offline   the mark drawn thin and faint

On macOS the tray uses a template image (black on transparent; the menu bar recolours it for light and dark).
Elsewhere it is a rounded tile in the state colour with the mark in white, which reads on any taskbar.
"""
from __future__ import annotations

FLOW, STUCK, PAUSED, WAITING, OFFLINE = "flow", "stuck", "paused", "waiting", "offline"
STATES = (FLOW, STUCK, PAUSED, WAITING, OFFLINE)

# Colours shared with the dashboard (frontend/src/styles.css uses the same values).
COLOURS = {FLOW: "#12A39A", STUCK: "#E0861A", PAUSED: "#7C8A99", WAITING: "#7C8A99", OFFLINE: "#5B6673"}

GRID = 24.0
STROKE = 2.3
# Start low on the left, rise into the loop, cross back over itself, run out low on the right.
SEGMENTS = (
    ((2.5, 19.3), (8.5, 19.3), (13.4, 17.4), (15.0, 12.4)),
    ((15.0, 12.4), (16.4, 8.0), (14.4, 4.6), (12.0, 4.6)),
    ((12.0, 4.6), (9.6, 4.6), (7.9, 7.8), (9.2, 12.0)),
    ((9.2, 12.0), (10.6, 16.7), (15.5, 19.3), (21.5, 19.3)),
)
GAP = (0.52, 0.80)               # paused: the part of the last segment left out (as a fraction of it)


def _bez(p0, p1, p2, p3, t):
    u = 1 - t
    return (u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
            u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1])


def _crossing(steps: int = 400) -> tuple[float, float]:
    """Where the line crosses itself: (t on the first segment, t on the last)."""
    a = [_bez(*SEGMENTS[0], k / steps) for k in range(steps + 1)]
    b = [_bez(*SEGMENTS[-1], k / steps) for k in range(steps + 1)]
    best = min(((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2, i, j) for i, p in enumerate(a) for j, q in enumerate(b)
               if i > steps // 4 and j < 3 * steps // 4)
    return best[1] / steps, best[2] / steps


def loop_outline(steps: int = 48) -> list[tuple[float, float]]:
    """The inside of the loop as a closed polygon in grid units (filled for the stuck state)."""
    t1, t4 = _crossing()
    pts = [_bez(*SEGMENTS[0], t1 + (1 - t1) * k / steps) for k in range(steps + 1)]
    for seg in SEGMENTS[1:3]:
        pts += [_bez(*seg, k / steps) for k in range(1, steps + 1)]
    pts += [_bez(*SEGMENTS[-1], t4 * k / steps) for k in range(1, steps + 1)]
    return pts


def _runs(state: str, steps: int = 48) -> list[list[tuple[float, float]]]:
    """The mark as polylines in grid units. Paused splits it in two around the gap."""
    pts: list[tuple[float, float]] = []
    runs: list[list[tuple[float, float]]] = []
    for i, seg in enumerate(SEGMENTS):
        for k in range(steps + 1):
            t = k / steps
            if state == PAUSED and i == len(SEGMENTS) - 1 and GAP[0] < t < GAP[1]:
                if pts:
                    runs.append(pts)
                pts = []
                continue
            pts.append(_bez(*seg, t))
    runs.append(pts)
    return [r for r in runs if len(r) > 1]


def svg(state: str = FLOW, colour: str = "currentColor", size: int | None = None) -> str:
    """The mark as an SVG document (used for the favicon and for checking the shape)."""
    d = ""
    if state == PAUSED:
        for run in _runs(state, 24):
            d += "M" + " L".join(f"{x:.2f} {y:.2f}" for x, y in run) + " "
    else:
        s0 = SEGMENTS[0][0]
        d = f"M{s0[0]} {s0[1]} " + " ".join(f"C{a[0]} {a[1]} {b[0]} {b[1]} {c[0]} {c[1]}" for _, a, b, c in SEGMENTS)
    width = STROKE * (0.6 if state == OFFLINE else 1)
    opacity = ' opacity="0.55"' if state == OFFLINE else ""
    dims = f' width="{size}" height="{size}"' if size else ""
    dot = ('<path d="M' + " L".join(f"{x:.2f} {y:.2f}" for x, y in loop_outline(24)) + f'Z" fill="{colour}"/>'
           if state == STUCK else "")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"{dims}>'
            f'<path d="{d.strip()}" fill="none" stroke="{colour}" stroke-width="{width}" stroke-linecap="round" '
            f'stroke-linejoin="round"{opacity}/>{dot}</svg>')


def _hex(c: str, alpha: int = 255) -> tuple[int, int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), alpha


def _draw_mark(img, state: str, ink, box: tuple[float, float, float], scale: float) -> None:
    """Draw the mark onto `img` (already supersampled) inside `box` = (left, top, side) in pixels."""
    from PIL import ImageDraw
    d = ImageDraw.Draw(img)
    left, top, side = box
    k = side / GRID
    r = STROKE * (0.6 if state == OFFLINE else 1) * k / 2
    for run in _runs(state):                                       # a round brush stamped along the path
        xy = [(left + x * k, top + y * k) for x, y in run]
        for (x0, y0), (x1, y1) in zip(xy, xy[1:]):
            n = max(1, int(((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5 / max(r / 4, 0.5)))
            for i in range(n + 1):
                x, y = x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n
                d.ellipse((x - r, y - r, x + r, y + r), fill=ink)
    if state == STUCK:
        d.polygon([(left + x * k, top + y * k) for x, y in loop_outline()], fill=ink)


def render(state: str = FLOW, size: int = 64, template: bool = False):
    """The tray image for a state, as a Pillow RGBA image of `size` pixels.

    template=True: black mark on transparent for the macOS menu bar (pystray hands it over as a template image).
    Otherwise a rounded tile in the state's colour with the mark in white.
    """
    from PIL import Image, ImageDraw
    ss = 8                                                         # draw large, then shrink: smooth edges
    big = size * ss
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    if template:
        ink = (0, 0, 0, 140 if state == OFFLINE else 255)
        _draw_mark(img, state, ink, (0, 0, big), ss)
    else:
        pad = big * 0.04
        tile = _hex(COLOURS[state], 255)
        ImageDraw.Draw(img).rounded_rectangle((pad, pad, big - pad, big - pad), radius=big * 0.24, fill=tile)
        inset = big * 0.12
        _draw_mark(img, state, (255, 255, 255, 170 if state == OFFLINE else 255), (inset, inset, big - 2 * inset), ss)
    return img.resize((size, size), Image.LANCZOS)


def preview(path: str) -> None:
    """A sheet of every state at tray sizes, on light and dark menu bars, for checking the mark by eye."""
    from PIL import Image, ImageDraw
    sizes, states = (16, 22, 32, 64), (FLOW, STUCK, PAUSED, OFFLINE)
    cell, rowh = 96, 96
    sheet = Image.new("RGBA", (cell * (len(sizes) * 2 + 1), rowh * len(states) * 2 + 40), (255, 255, 255, 255))
    d = ImageDraw.Draw(sheet)
    for half, (bg, fg) in enumerate((((236, 236, 236, 255), (0, 0, 0)), ((40, 40, 44, 255), (255, 255, 255)))):
        y0 = 40 + half * rowh * len(states)
        d.rectangle((0, y0, sheet.width, y0 + rowh * len(states)), fill=bg)
        for r, st in enumerate(states):
            y = y0 + r * rowh
            d.text((6, y + 40), st, fill=fg + (255,))
            for c, sz in enumerate(sizes):
                tpl = render(st, sz * 2, template=True).resize((sz, sz), Image.LANCZOS)
                if half:                                           # the menu bar turns a template white in dark mode
                    tpl = Image.merge("RGBA", (*Image.new("RGB", tpl.size, (255, 255, 255)).split(), tpl.split()[3]))
                sheet.alpha_composite(tpl, (cell * (c + 1) + (cell - sz) // 2, y + (rowh - sz) // 2))
                tile = render(st, sz)
                sheet.alpha_composite(tile, (cell * (c + 1 + len(sizes)) + (cell - sz) // 2, y + (rowh - sz) // 2))
    d.text((cell + 6, 12), "macOS template: 16 / 22 / 32 / 64 px", fill=(0, 0, 0, 255))
    d.text((cell * (1 + len(sizes)) + 6, 12), "Windows / Linux tile: 16 / 22 / 32 / 64 px", fill=(0, 0, 0, 255))
    sheet.save(path)
