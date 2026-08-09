"""Seed boundary: offsetting one Centerline into both track edges (ADR-0003).

The point of seeding is that two failure modes become impossible rather than
merely discouraged — edges in opposite directions, and edges starting at
far-apart points. That claim is only worth anything if it is *asserted*, so the
inheritance tests here check the property directly on the shipped circuits
rather than trusting that one source implies one answer.

The rest is geometry with exact answers: a straight offsets by exactly the
half-width, a right angle by exactly `√2` times it, and a hairpin by exactly the
clamp. Synthetic shapes pin those, and Monaco's Fairmont hairpin proves the
clamp fires on real geometry at the corner it was written for.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from twinkly_mockup.enu import FlatEnu
from twinkly_mockup.seed import MITER_LIMIT, drop_seam_vertex, seed_boundary

REPO = Path(__file__).resolve().parent.parent
CONFIGS = REPO / "configs"

SHIPPED = ["monaco", "silverstone"]

# Turn 6, the Fairmont hairpin — F1's slowest corner and the reason the miter
# clamp exists. Same hint `scripts/derive_corner_poses.py` anchors its snapshot
# on, so the two agree about where the corner is.
LOEWS_LAT, LOEWS_LON = 43.74033, 7.42971

ORIGIN_LAT, ORIGIN_LON = 43.7, 7.4


def enu() -> FlatEnu:
    return FlatEnu.at(ORIGIN_LAT, ORIGIN_LON)


def ring(points_m: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Build a lon/lat centerline from ENU metres, so tests can think in metres."""
    frame = enu()
    return [tuple(float(v) for v in frame.to_lonlat(e, n)) for e, n in points_m]


def metres(lon: float, lat: float) -> tuple[float, float]:
    e, n = enu().to_enu(lat, lon)
    return float(e), float(n)


def gap_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Ground distance between two lon/lat points, via the shared test frame."""
    ae, an = metres(*a)
    be, bn = metres(*b)
    return math.hypot(ae - be, an - bn)


def signed_area(points: list[tuple[float, float]]) -> float:
    """Shoelace area in lon/lat degrees — only its *sign* is used, as direction."""
    n = len(points)
    return 0.5 * sum(
        points[i][0] * points[(i + 1) % n][1] - points[(i + 1) % n][0] * points[i][1]
        for i in range(n)
    )


def shipped_centerline(name: str) -> tuple[list[tuple[float, float]], bool]:
    raw = json.loads((CONFIGS / f"{name}_centerline.geojson").read_text())
    return drop_seam_vertex(raw["features"][0]["geometry"]["coordinates"])


def half_width_of(name: str) -> float:
    manifest = json.loads((CONFIGS / "circuits.json").read_text())
    return next(c["half_width_m"] for c in manifest["circuits"] if c["name"] == name)


# ------------------------------------------------------------- exact geometry


def test_a_straight_offsets_by_exactly_the_half_width() -> None:
    """No turn, no miter: the extension factor is 1 and the gap is the half-width."""
    seed = seed_boundary(ring([(0.0, 0.0), (100.0, 0.0), (200.0, 0.0)]), 5.0, closed=False)
    assert seed.factors == pytest.approx((1.0, 1.0, 1.0))
    for i in range(3):
        assert gap_m(seed.left[i], seed.right[i]) == pytest.approx(10.0, abs=1e-6)


def test_left_is_left_of_travel() -> None:
    """Heading east, the left edge is the northern one.

    Getting this backwards would swap the two exported KMLs, and the solver has
    no way to notice — it would fit a reference line to a track turned inside
    out.
    """
    seed = seed_boundary(ring([(0.0, 0.0), (100.0, 0.0), (200.0, 0.0)]), 5.0, closed=False)
    _, left_n = metres(*seed.left[1])
    _, right_n = metres(*seed.right[1])
    assert left_n > 0.0 > right_n


def test_a_right_angle_extends_the_miter_by_root_two() -> None:
    """The miter point of a 90° turn sits at `half_width / cos 45°` from the vertex."""
    seed = seed_boundary(
        ring([(0.0, 0.0), (100.0, 0.0), (100.0, 100.0)]), 4.0, closed=False
    )
    assert seed.factors[1] == pytest.approx(math.sqrt(2.0))
    corner = metres(*ring([(100.0, 0.0)])[0])
    for edge in (seed.left[1], seed.right[1]):
        assert math.dist(metres(*edge), corner) == pytest.approx(4.0 * math.sqrt(2.0), abs=1e-6)


def test_both_edges_share_one_miter_point_so_the_band_stays_constant_width() -> None:
    """Left and right are the same offset negated, so a corner does not pinch."""
    seed = seed_boundary(
        ring([(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]), 6.0
    )
    for i in range(4):
        assert gap_m(seed.left[i], seed.right[i]) == pytest.approx(
            2.0 * 6.0 * seed.factors[i], abs=1e-6
        )


def test_a_hairpin_is_clamped_to_three_times_the_half_width() -> None:
    """Unclamped, a 170° turn would throw the vertex ~23 half-widths out."""
    spike = ring([(0.0, 0.0), (100.0, 0.0), (100.0 - 100.0 * math.cos(math.radians(10.0)),
                                             100.0 * math.sin(math.radians(10.0)))])
    seed = seed_boundary(spike, 5.0, closed=False)
    assert 1.0 / math.cos(math.radians(85.0)) > 11.0, "fixture assumption: unclamped is huge"
    assert seed.factors[1] == MITER_LIMIT
    corner = metres(*spike[1])
    assert math.dist(metres(*seed.left[1]), corner) == pytest.approx(15.0, abs=1e-6)
    assert seed.clamped == (1,)


def test_seeding_refuses_a_centerline_too_short_to_have_a_bisector() -> None:
    with pytest.raises(ValueError, match="at least 3 vertices"):
        seed_boundary(ring([(0.0, 0.0), (100.0, 0.0)]), 5.0)


@pytest.mark.parametrize("half_width", [0.0, -1.0])
def test_seeding_refuses_a_non_positive_half_width(half_width: float) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        seed_boundary(ring([(0.0, 0.0), (100.0, 0.0), (100.0, 100.0)]), half_width)


def test_a_repeated_vertex_does_not_stop_a_lap_from_seeding() -> None:
    """A duplicate has no direction of its own; the walk steps past it."""
    coords = ring([(0.0, 0.0), (100.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)])
    seed = seed_boundary(coords, 5.0)
    assert len(seed.left) == len(coords)
    assert all(math.isfinite(v) for p in seed.left + seed.right for v in p)


# ---------------------------------------------------------------- the seam


@pytest.mark.parametrize("name", SHIPPED)
def test_the_shipped_laps_are_closed_with_a_repeated_seam(name: str) -> None:
    raw = json.loads((CONFIGS / f"{name}_centerline.geojson").read_text())
    source = raw["features"][0]["geometry"]["coordinates"]
    coords, closed = drop_seam_vertex(source)
    assert closed is True
    assert len(coords) == len(source) - 1
    assert coords[0] == tuple(source[0])


def test_an_open_polyline_keeps_every_vertex() -> None:
    coords, closed = drop_seam_vertex([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)])
    assert closed is False
    assert len(coords) == 3


# ------------------------------------------------- inheritance, on real circuits


@pytest.mark.parametrize("name", SHIPPED)
def test_one_seed_vertex_per_centerline_vertex(name: str) -> None:
    """No resampling and no smoothing: the density is inherited, not recomputed."""
    coords, closed = shipped_centerline(name)
    seed = seed_boundary(coords, half_width_of(name), closed=closed)
    assert len(seed.left) == len(coords)
    assert len(seed.right) == len(coords)
    # ADR-0003's stated counts, after the repeated seam comes off.
    assert 130 <= len(coords) <= 165, f"{name} has {len(coords)} vertices"


@pytest.mark.parametrize("name", SHIPPED)
def test_both_edges_inherit_the_centerline_direction(name: str) -> None:
    """Opposite-direction edges are failure mode one; assert it cannot happen."""
    coords, closed = shipped_centerline(name)
    seed = seed_boundary(coords, half_width_of(name), closed=closed)
    source = signed_area(coords)
    assert source != 0.0
    assert math.copysign(1.0, signed_area(list(seed.left))) == math.copysign(1.0, source)
    assert math.copysign(1.0, signed_area(list(seed.right))) == math.copysign(1.0, source)


@pytest.mark.parametrize("name", SHIPPED)
def test_both_edges_inherit_index_zero(name: str) -> None:
    """Far-apart start points are failure mode two; assert it cannot happen.

    Nearest-vertex rather than a distance bound: a seed vertex could sit within
    a half-width of the source and still belong to a different part of the lap
    where the track doubles back on itself.
    """
    coords, closed = shipped_centerline(name)
    seed = seed_boundary(coords, half_width_of(name), closed=closed)
    for edge in (seed.left, seed.right):
        distances = [gap_m(edge[0], c) for c in coords]
        assert distances.index(min(distances)) == 0
        assert min(distances) <= MITER_LIMIT * half_width_of(name)


@pytest.mark.parametrize("name", SHIPPED)
def test_every_seed_vertex_stays_within_the_clamp(name: str) -> None:
    """The clamp's purpose: no vertex is ever flung off the mosaic."""
    coords, closed = shipped_centerline(name)
    half_width = half_width_of(name)
    seed = seed_boundary(coords, half_width, closed=closed)
    limit = MITER_LIMIT * half_width + 1e-6
    for i, source in enumerate(coords):
        assert gap_m(seed.left[i], source) <= limit
        assert gap_m(seed.right[i], source) <= limit


def test_monacos_tightest_vertex_is_the_fairmont_hairpin() -> None:
    """The miter factor finds the hairpin — once the vertices are coarse enough.

    At the shipped density it cannot: f1-circuits spends ~8 m per vertex through
    Monaco's corners, so the Fairmont's ~180° is shared out over a dozen
    vertices and no single one of them turns sharply. Decimating to a spacing a
    hand-tracer would plausibly produce concentrates the turn back onto one
    vertex, and that vertex is the hairpin — which is what says the factor is
    measuring corner tightness and not something incidental.
    """
    coords, _ = shipped_centerline("monaco")
    coarse = coords[::6]
    seed = seed_boundary(coarse, half_width_of("monaco"))

    tightest = max(range(len(coarse)), key=lambda i: seed.factors[i])
    assert gap_m(coarse[tightest], (LOEWS_LON, LOEWS_LAT)) < 20.0
    assert seed.factors[tightest] > 2.0


@pytest.mark.parametrize(("name", "ceiling"), [("monaco", 1.11), ("silverstone", 1.05)])
def test_the_clamp_is_a_guard_the_shipped_circuits_never_reach(
    name: str, ceiling: float
) -> None:
    """No shipped vertex comes close to `MITER_LIMIT`, and that is worth pinning.

    Monaco peaks at ~1.10 and Silverstone at ~1.04 — a 3× miter needs a 141°
    turn at a *single* vertex, which dense-through-corners geometry never
    produces. So the clamp protects against a coarse or hand-edited centerline
    rather than against these two, and nobody should read a passing seed as
    evidence that it works.

    Pinning the ceiling means a future centerline that *does* approach the clamp
    fails here and gets looked at, instead of quietly acquiring flung-out
    vertices at a corner.
    """
    coords, closed = shipped_centerline(name)
    seed = seed_boundary(coords, half_width_of(name), closed=closed)
    assert seed.clamped == ()
    assert max(seed.factors) < ceiling < MITER_LIMIT
