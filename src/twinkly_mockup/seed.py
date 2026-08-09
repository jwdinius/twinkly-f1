"""Seed boundary: both track-limit edges, offset from one Centerline (ADR-0003).

The documented alternative is to hand-trace two polylines independently in
Google Earth Pro, and that workflow's own troubleshooting table names the two
worst things that go wrong: edges traced in **opposite directions**, and edges
starting at **far-apart points** on the lap. Both warp the reference line the
optimizer fits and neither shows up until the solver runs.

Generating both edges from one source removes them by construction. There is
only one direction to inherit and one index 0 to inherit, so the two edges
cannot disagree about either.

Two properties of the source are load-bearing and are why the vertices are taken
**as-is**, with no resampling and no smoothing:

* `bacinger/f1-circuits` centerlines are dense through corners and sparse on
  straights — the distribution the optimizer wants, and the one the tracing doc
  asks a human to reproduce by hand. Resampling would throw it away and then
  ask for it back.
* ~135 vertices per edge is small enough that every vertex is reachable by
  hand. The rejected pixel extractor emitted ~2,900, which is an editing
  surface nobody can drag.

**A seed is not a Track-limit KML.** It is a constant-width oval: a real circuit
is not constant-width, and its edges are the painted lines, not an offset of the
middle. Exporting an undragged seed would feed the solver that oval. The whole
design rests on nobody mistaking one for the other, which is why `CONTEXT.md`
defines the term by what it is *not*.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .enu import FlatEnu

MITER_LIMIT: float = 3.0
"""Cap on the miter extension, as a multiple of the half-width.

The angle bisector runs away as a turn tightens: two nearly-antiparallel
segments have offsets that meet at `half_width / cos(θ/2)` from the vertex,
which is unbounded. Clamping to 3× keeps every seed vertex within dragging
distance of the asphalt it belongs to — a corner that clamps is *visibly* wrong
right there, which is the honest outcome, rather than invisibly wrong somewhere
off the mosaic.

It is a guard, not a routine event, and on the shipped circuits it never fires:
3× needs a 141° turn at a **single** vertex, and f1-circuits geometry is dense
enough through corners that even Monaco's Fairmont hairpin shares its ~180° out
over a dozen vertices, peaking at a factor of ~1.10. What the clamp actually
protects against is a coarse or hand-edited centerline. `tests/test_seed.py`
pins both halves of that — the clamp is exact on synthetic geometry, and the
shipped circuits stay far below it.
"""

_DEGENERATE_M = 1e-9
"""Two centerline vertices closer than this are the same point, so the segment
between them has no direction to take a normal from."""

SEAM_TOL_DEG: float = 1e-6
"""How close the first and last vertex must be to count as the same seam.

~1e-6° is ~0.1 m — far below the vertex spacing of any circuit here and far
above float noise, so it separates "closed lap, seam repeated" from "open
polyline that happens to end near its start" without ambiguity.
"""


@dataclass(frozen=True)
class SeedBoundary:
    """Both edges of one circuit, plus what the miter clamp had to do.

    `left` and `right` are lon/lat and are the same length as the Centerline
    they came from — one seed vertex per source vertex, by construction.
    `factors[i]` is the miter extension actually applied at vertex `i`, so it is
    `1.0` on a straight and `MITER_LIMIT` wherever the clamp bit.
    """

    left: tuple[tuple[float, float], ...]
    right: tuple[tuple[float, float], ...]
    factors: tuple[float, ...]
    half_width_m: float
    miter_limit: float

    @property
    def clamped(self) -> tuple[int, ...]:
        """Indices where the miter hit the limit — the circuit's tightest corners."""
        return tuple(i for i, f in enumerate(self.factors) if f >= self.miter_limit)


def drop_seam_vertex(
    centerline_lonlat: list[tuple[float, float]] | list[list[float]],
    *,
    tol_deg: float = SEAM_TOL_DEG,
) -> tuple[list[tuple[float, float]], bool]:
    """Split a raw centerline into `(vertices, closed)`, dropping a repeated seam.

    `bacinger/f1-circuits` closes a lap by repeating vertex 0 at the end. That
    repeat is exactly what `seed_boundary` cannot survive: it leaves index 0
    with two identical neighbours and therefore no angle bisector — at the one
    vertex whose inheritance the whole design depends on.

    So the fix belongs next to the requirement rather than in whatever code
    happened to read the file, and every caller gets it.
    """
    coords = [(float(lon), float(lat)) for lon, lat in centerline_lonlat]
    closed = (
        len(coords) > 1
        and abs(coords[0][0] - coords[-1][0]) < tol_deg
        and abs(coords[0][1] - coords[-1][1]) < tol_deg
    )
    if closed:
        coords.pop()
    return coords, closed


def seed_boundary(
    centerline_lonlat: list[tuple[float, float]] | list[list[float]],
    half_width_m: float,
    *,
    closed: bool = True,
    miter_limit: float = MITER_LIMIT,
) -> SeedBoundary:
    """Offset a Centerline to both edges along its miter (angle-bisector) normals.

    `centerline_lonlat` must already have any duplicated seam vertex dropped —
    a closed lap that repeats vertex 0 at the end leaves index 0 with two
    identical neighbours and no bisector. `scripts/trace.py` does that on the
    way out of the GeoJSON.

    The offset is computed in flat-ENU metres about the Centerline's own first
    vertex. That frame is a *linear* map of lon/lat, so it needs no sidecar and
    introduces no dependency on which mosaic the circuit is being traced over;
    over a 1 km lap the curvature it ignores is far below the metre the
    half-width is guessed to.
    """
    coords = [(float(lon), float(lat)) for lon, lat in centerline_lonlat]
    n = len(coords)
    if n < 3:
        raise ValueError(f"a centerline needs at least 3 vertices to seed from, got {n}")
    if half_width_m <= 0.0:
        raise ValueError(f"half_width_m must be positive, got {half_width_m}")
    if miter_limit < 1.0:
        raise ValueError(f"miter_limit must be at least 1.0, got {miter_limit}")

    frame = FlatEnu.at(coords[0][1], coords[0][0])
    pts = [_to_enu(frame, lon, lat) for lon, lat in coords]

    left: list[tuple[float, float]] = []
    right: list[tuple[float, float]] = []
    factors: list[float] = []
    for i in range(n):
        mx, my, factor = _miter_at(pts, i, closed=closed)
        d = half_width_m * min(factor, miter_limit)
        e, north = pts[i]
        left.append(_to_lonlat(frame, e + mx * d, north + my * d))
        right.append(_to_lonlat(frame, e - mx * d, north - my * d))
        factors.append(min(factor, miter_limit))

    return SeedBoundary(
        left=tuple(left),
        right=tuple(right),
        factors=tuple(factors),
        half_width_m=float(half_width_m),
        miter_limit=float(miter_limit),
    )


def _miter_at(
    pts: list[tuple[float, float]], i: int, *, closed: bool
) -> tuple[float, float, float]:
    """Unit left-miter direction at vertex `i`, and its extension factor.

    The miter point is where the two neighbouring segments' offset lines meet.
    Its direction is the normalised sum of their left normals and its distance
    is `half_width / cos(θ/2)` — recovered here as `1 / (m · n_in)`, which is
    that same secant without ever forming the angle.

    Returned as the *left* direction; the right edge is the exact negation, so
    both edges share one miter point per vertex and the band they bound has the
    constant width it advertises.
    """
    n = len(pts)
    t_in = _direction(pts, i, step=-1, closed=closed)
    t_out = _direction(pts, i, step=+1, closed=closed)
    if t_in is None and t_out is None:
        raise ValueError(f"centerline vertex {i} has no distinct neighbour in {n} vertices")
    if t_in is None:
        t_in = t_out
    if t_out is None:
        t_out = t_in

    nin = (-t_in[1], t_in[0])          # left of the incoming heading
    nout = (-t_out[1], t_out[0])       # left of the outgoing heading
    bx, by = nin[0] + nout[0], nin[1] + nout[1]
    length = math.hypot(bx, by)
    if length < 1e-12:
        # The segments double back on themselves exactly. The offset lines are
        # then parallel and distinct, so there is no miter point at any
        # distance, and which way it runs off depends on which side the
        # centerline was approaching from — genuinely undefined rather than
        # merely large. A plain perpendicular offset is bounded and visibly
        # wrong at that vertex, which is what the operator needs to see.
        return nin[0], nin[1], 1.0
    mx, my = bx / length, by / length
    cos_half = mx * nin[0] + my * nin[1]
    # `cos_half` is cos(θ/2) ≥ 0 by construction — `m` bisects `nin` and `nout`.
    factor = 1.0 / cos_half if cos_half > 1e-12 else math.inf
    return mx, my, factor


def _direction(
    pts: list[tuple[float, float]], i: int, *, step: int, closed: bool
) -> tuple[float, float] | None:
    """Unit heading at vertex `i` towards its neighbour `step` away, or `None`.

    Walks past coincident vertices rather than dividing by their zero-length
    segment: a repeated vertex is a defect in the source geometry, not a reason
    to refuse to seed a 160-vertex lap. `None` means the walk ran out of
    vertices — an open polyline at its first or last vertex, where one of the
    two segments simply does not exist.
    """
    n = len(pts)
    ex, ny = pts[i]
    for k in range(1, n):
        j = i + step * k
        if not closed and not (0 <= j < n):
            return None
        jx, jy = pts[j % n]
        dx, dy = (jx - ex) * step, (jy - ny) * step
        length = math.hypot(dx, dy)
        if length > _DEGENERATE_M:
            return dx / length, dy / length
    return None


def _to_enu(frame: FlatEnu, lon: float, lat: float) -> tuple[float, float]:
    e, n = frame.to_enu(lat, lon)
    return float(e), float(n)


def _to_lonlat(frame: FlatEnu, e: float, n: float) -> tuple[float, float]:
    lon, lat = frame.to_lonlat(e, n)
    return float(lon), float(lat)
