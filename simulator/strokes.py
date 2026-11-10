"""Real human mouse strokes, from the Balabit Mouse Dynamics Challenge recordings.

A stroke is the pointer path between two rests. Each one is normalised (start at the
origin, end at (1, 0)) so it can be replayed between any two points on a page while
keeping what a person's hand actually did: curvature, overshoot and speed profile.

The recordings are not redistributed with this repository; see data/balabit/.
"""

from __future__ import annotations

import csv
import json
import math
import random
from pathlib import Path

SOURCE = Path("data/balabit")
CACHE = SOURCE / "strokes.json"

Stroke = tuple[float, list[tuple[float, float, float]]]  # duration_s, [(t_frac, u, v)]


def _extract(path: Path) -> list[Stroke]:
    out: list[Stroke] = []
    cur: list[tuple[float, float, float]] = []

    def close() -> None:
        if len(cur) >= 6:
            (t0, x0, y0), (t1, x1, y1) = cur[0], cur[-1]
            dist, dur = math.hypot(x1 - x0, y1 - y0), t1 - t0
            if dist >= 60 and 0.2 <= dur <= 3.0:
                cos, sin = (x1 - x0) / dist, (y1 - y0) / dist
                pts = []
                for t, x, y in cur:
                    dx, dy = x - x0, y - y0
                    pts.append(
                        (
                            round((t - t0) / dur, 4),
                            round((dx * cos + dy * sin) / dist, 4),
                            round((-dx * sin + dy * cos) / dist, 4),
                        )
                    )
                # discard recording glitches (teleports far off the straight line)
                if all(-0.5 <= u <= 1.5 and abs(v) <= 0.8 for _, u, v in pts):
                    out.append((round(dur, 3), pts))
        cur.clear()

    with path.open(newline="", encoding="utf-8") as fh:
        last_t = None
        for row in csv.DictReader(fh):
            try:
                t, x, y = float(row["client timestamp"]), float(row["x"]), float(row["y"])
            except (KeyError, ValueError):
                continue
            # a click or a pause ends the stroke; 65535 marks an off-screen sample
            if row.get("state") != "Move" or x >= 65535 or y >= 65535:
                close()
                last_t = None
                continue
            if last_t is not None and t - last_t > 0.35:
                close()
            cur.append((t, x, y))
            last_t = t
    close()
    return out


def load() -> list[Stroke]:
    if CACHE.exists():
        return [(d, [tuple(p) for p in pts]) for d, pts in json.loads(CACHE.read_text())]
    files = sorted(SOURCE.glob("*.csv"))
    if not files:
        raise SystemExit(
            "no Balabit sessions in data/balabit/. Download a few training sessions from "
            "https://github.com/balabit/Mouse-Dynamics-Challenge (see docs/data.md)."
        )
    strokes = [s for f in files for s in _extract(f)]
    CACHE.write_text(json.dumps(strokes))
    return strokes


def path_between(
    stroke: Stroke, start: tuple[float, float], end: tuple[float, float], step_s: float = 0.016
) -> list[tuple[float, float, float]]:
    """Replay ``stroke`` from ``start`` to ``end``: [(delay_s, x, y)] at ~60 Hz.

    The recording is sampled at ~10 Hz, so points are interpolated along the recorded
    polyline; the shape and timing between samples are the person's.
    """
    dur, pts = stroke
    dx, dy = end[0] - start[0], end[1] - start[1]
    dist = math.hypot(dx, dy) or 1.0
    cos, sin = dx / dist, dy / dist
    n = max(2, int(dur / step_s))
    out, j = [], 0
    for i in range(1, n + 1):
        f = i / n
        while j < len(pts) - 2 and pts[j + 1][0] < f:
            j += 1
        (t0, u0, v0), (t1, u1, v1) = pts[j], pts[j + 1]
        k = 0.0 if t1 <= t0 else min(max((f - t0) / (t1 - t0), 0.0), 1.0)
        u, v = u0 + (u1 - u0) * k, v0 + (v1 - v0) * k
        out.append(
            (dur / n, start[0] + (u * cos - v * sin) * dist, start[1] + (u * sin + v * cos) * dist)
        )
    out[-1] = (out[-1][0], end[0], end[1])
    return out


if __name__ == "__main__":
    all_strokes = load()
    durations = sorted(d for d, _ in all_strokes)
    print(f"{len(all_strokes)} strokes; median duration {durations[len(durations) // 2]:.2f} s")
    print(len(path_between(random.choice(all_strokes), (100, 100), (600, 400))), "steps")
