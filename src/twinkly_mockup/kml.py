"""Read and write the Track-limit KML `fastest-lap`'s preprocessor consumes.

One closed `LineString` per file, one file per edge — the format contract
`racetrack-mosaic`'s tracing doc spells out and the optimizer assumes.

Written in Python rather than in the tracer for three reasons: the export lands
in `configs/`, which the page cannot write to; there is then one implementation
of the format instead of one per caller; and the attribution comment cannot be
forgotten, because nothing else can produce one of these files.

**Round-tripping is exact.** Import is the only durable resume path — the
tracer's working store is `localStorage`, and clearing a cache would otherwise
discard hours of dragging — so a re-imported edge has to be the edge that was
exported, not a re-rendered approximation of it. Coordinates are therefore
written with `repr`, which is the shortest decimal that reads back as the same
float64, and the closing vertex is recognised by exact equality rather than by
a distance tolerance that would quietly eat a genuinely short final segment.
"""

from __future__ import annotations

import re
from pathlib import Path
from xml.sax.saxutils import escape

EDGES = ("left", "right")

ATTRIBUTION_MARKER = "Tomislav Bacinger"
"""What `contains_attribution` looks for, and what the export tests assert on."""


class KmlError(ValueError):
    """A KML that cannot be read as one track edge."""


def edge_filename(circuit: str, edge: str) -> str:
    if edge not in EDGES:
        raise KmlError(f"edge must be one of {', '.join(EDGES)}, got `{edge}`")
    return f"{circuit}_{edge}.kml"


def format_coord(value: float) -> str:
    """Shortest decimal that reads back as the same float64.

    `repr` rather than a fixed number of places: eight decimals is a millimetre
    and would look like plenty, but it is still a lossy round trip, and "lossy
    by less than a millimetre" compounds silently over repeated
    export/import/export cycles.
    """
    return repr(float(value))


def write_track_limit_kml(
    path: Path,
    coords: list[tuple[float, float]] | list[list[float]],
    *,
    circuit: str,
    edge: str,
    attribution: str,
    closed: bool = True,
) -> Path:
    """Write one edge as a single closed `LineString`, with attribution.

    `coords` are `(lon, lat)` **without** a repeated closing vertex; `closed`
    appends one. That split keeps the working store's invariant — every vertex
    is a vertex an operator can drag — separate from the file format's, where a
    closed ring repeats its first point.
    """
    if edge not in EDGES:
        raise KmlError(f"edge must be one of {', '.join(EDGES)}, got `{edge}`")
    pairs = [(float(lon), float(lat)) for lon, lat in coords]
    if len(pairs) < 2:
        raise KmlError(f"{circuit} {edge}: a track edge needs at least 2 vertices, got {len(pairs)}")
    ring = pairs + [pairs[0]] if closed else pairs

    name = f"{circuit}_{edge}"
    text = f"""<?xml version="1.0" encoding="UTF-8"?>
<!--
{_comment_body(circuit, edge, attribution, len(pairs), closed)}
-->
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>{escape(name)}</name>
    <Placemark><name>{escape(name)}</name>
      <LineString><tessellate>1</tessellate><altitudeMode>clampToGround</altitudeMode>
        <coordinates>{" ".join(f"{format_coord(lo)},{format_coord(la)},0" for lo, la in ring)}</coordinates>
      </LineString>
    </Placemark>
  </Document>
</kml>
"""
    path = Path(path)
    path.write_text(text)
    return path


def write_optimal_trajectory_kml(
    path: Path,
    coords: list[tuple[float, float]],
    *,
    circuit: str,
    lap_time_s: float | None = None,
) -> Path:
    """Write an Optimal trajectory as one closed `LineString`, for viewing.

    A *viewing* format, not an interchange one, and deliberately not a
    Track-limit KML: this is solver **output**, the two are different concepts
    (see CONTEXT.md), and feeding one back into `circuit_preprocessor` as an
    edge would be a category error. The comment says so inside the file, since
    that is the copy most likely to be found on its own.

    KML carries position only, so `t` and `yaw` do not survive — which is why
    this is an extra emitted alongside the trajectory CSV rather than a
    replacement for it. `coords` are `(lon, lat)` without a repeated closing
    vertex; the ring is closed here, as a lap returns to its start.
    """
    pairs = [(float(lon), float(lat)) for lon, lat in coords]
    if len(pairs) < 2:
        raise KmlError(
            f"{circuit}: a trajectory needs at least 2 samples, got {len(pairs)}"
        )
    ring = pairs + [pairs[0]]

    name = f"{circuit}_optimal_trajectory"
    lap = "" if lap_time_s is None else f", {lap_time_s:.3f} s"
    body = re.sub(
        r"-{2,}",
        "-",
        "\n".join(
            [
                f"{circuit} optimal trajectory — {len(pairs)} samples{lap}.",
                "",
                "Minimum-time line computed by fastest-lap (ADR-0002) and crossed",
                "into WGS84 by twinkly-mockup import-lap. Derived output: it is NOT",
                "a track limit and must not be fed back to circuit_preprocessor.",
                "Position only — the trajectory CSV alongside it carries t and yaw.",
            ]
        ),
    )
    text = f"""<?xml version="1.0" encoding="UTF-8"?>
<!--
{body}
-->
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>{escape(name)}</name>
    <Placemark><name>{escape(name)}</name>
      <LineString><tessellate>1</tessellate><altitudeMode>clampToGround</altitudeMode>
        <coordinates>{" ".join(f"{format_coord(lo)},{format_coord(la)},0" for lo, la in ring)}</coordinates>
      </LineString>
    </Placemark>
  </Document>
</kml>
"""
    path = Path(path)
    path.write_text(text)
    return path


def _comment_body(
    circuit: str, edge: str, attribution: str, vertices: int, closed: bool
) -> str:
    """ADR-0003's third attribution site, and the one that matters most.

    An exported KML is the copy most likely to be separated from this repo and
    from its license — it gets mailed, dropped into a solver's input directory,
    committed somewhere else. So the credit travels inside the file.

    `--` is collapsed because it cannot appear inside an XML comment; an em dash
    in an attribution string is the likely source and silently producing
    unparseable XML would be worse than reflowing the punctuation.
    """
    seam = "closed (first vertex repeated at the end)" if closed else "open"
    body = "\n".join(
        [
            f"{circuit} {edge} track limit — {vertices} vertices, {seam}.",
            "",
            "Derived by offsetting a circuit centerline and refining it by hand in",
            "scripts/trace_track_limits.html (twinkly-f1, ADR-0003).",
            "",
            attribution,
        ]
    )
    return re.sub(r"-{2,}", "-", body)


def contains_attribution(text: str) -> bool:
    """Does this KML carry its centerline credit? Used by the export tests."""
    return ATTRIBUTION_MARKER in text


_COORDS_RE = re.compile(r"<coordinates>([\s\S]*?)</coordinates>")


def read_track_limit_kml(path: Path) -> tuple[list[tuple[float, float]], bool]:
    """Read one edge back as `(coords, closed)`, with the closing vertex removed.

    The inverse of `write_track_limit_kml`, exactly: what comes out is what went
    in. A repeated closing vertex is detected by exact equality — a tolerance
    would be guessing about whether a very short final segment was real.
    """
    path = Path(path)
    text = path.read_text()
    matches = _COORDS_RE.findall(text)
    if not matches:
        raise KmlError(f"{path} has no <coordinates> — is it a KML with a LineString?")
    if len(matches) > 1:
        # One path per file is the format contract; two means the operator saved
        # a folder rather than a path, and guessing which one is the edge would
        # be a coin flip resolved silently.
        raise KmlError(
            f"{path} holds {len(matches)} LineStrings; a track edge is one path per file"
        )

    coords: list[tuple[float, float]] = []
    for token in matches[0].split():
        parts = token.split(",")
        if len(parts) < 2:
            raise KmlError(f"{path}: `{token}` is not a lon,lat[,alt] triple")
        try:
            coords.append((float(parts[0]), float(parts[1])))
        except ValueError as e:
            raise KmlError(f"{path}: `{token}` is not numeric") from e

    if len(coords) < 2:
        raise KmlError(f"{path}: a track edge needs at least 2 vertices, got {len(coords)}")
    closed = coords[0] == coords[-1]
    if closed:
        coords.pop()
    return coords, closed
