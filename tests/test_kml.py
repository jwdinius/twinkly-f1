"""Track-limit KML: the format the solver reads and the only durable resume path.

Two properties carry real weight here.

**Round-tripping is exact.** The tracer's working store is `localStorage`, so
re-importing an exported file is all that stands between a cleared cache and
hours of dragging. "Close enough" would mean every export/import cycle nudged
the edge, and nothing downstream could tell that from a deliberate edit.

**Every export carries its attribution.** ADR-0003 puts the Bacinger credit in
three places, and this is the one that matters most: an exported KML is the copy
most likely to be separated from this repo and its license.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from twinkly_mockup.kml import (
    KmlError,
    contains_attribution,
    edge_filename,
    format_coord,
    read_track_limit_kml,
    write_track_limit_kml,
)

ATTRIBUTION = "Centerline geometry © Tomislav Bacinger, MIT — bacinger/f1-circuits, mc-1929.geojson"

# Deliberately awkward: full float64 precision, negative longitudes, and a
# southern-hemisphere latitude. A formatter that rounds or a parser that assumes
# a sign fails on these and passes on tidy round numbers.
COORDS = [
    (7.4206123456789, 43.7394987654321),
    (-1.0123456789012, 52.0714123456789),
    (7.42061, -43.73949),
    (0.0, 0.0),
]


def write(tmp_path: Path, coords=COORDS, *, edge="left", closed=True) -> Path:
    return write_track_limit_kml(
        tmp_path / edge_filename("monaco", edge),
        coords,
        circuit="monaco",
        edge=edge,
        attribution=ATTRIBUTION,
        closed=closed,
    )


# ----------------------------------------------------------------- round trip


@pytest.mark.parametrize("closed", [True, False])
def test_export_then_import_reproduces_the_vertices_exactly(tmp_path: Path, closed: bool) -> None:
    """Bit-for-bit, not approximately — see the module docstring."""
    path = write(tmp_path, closed=closed)
    back, back_closed = read_track_limit_kml(path)
    assert back == COORDS
    assert back_closed is closed


def test_repeated_round_trips_never_drift(tmp_path: Path) -> None:
    """A tolerance-based reader would creep a little on each cycle."""
    coords = COORDS
    for _ in range(5):
        coords, _ = read_track_limit_kml(write(tmp_path, coords))
    assert coords == COORDS


def test_a_closed_edge_repeats_its_first_vertex_in_the_file_only(tmp_path: Path) -> None:
    """The file closes the ring; the working store does not.

    Every vertex the tracer holds is one an operator can drag, and a duplicate
    of vertex 0 sitting invisibly under vertex 0 is not.
    """
    path = write(tmp_path, closed=True)
    written = re.search(r"<coordinates>(.*?)</coordinates>", path.read_text(), re.S).group(1)
    assert len(written.split()) == len(COORDS) + 1
    assert written.split()[0] == written.split()[-1]

    back, _ = read_track_limit_kml(path)
    assert len(back) == len(COORDS)


def test_coordinates_are_written_at_full_float_precision() -> None:
    assert float(format_coord(7.4206123456789)) == 7.4206123456789
    assert format_coord(0.0) == "0.0"


def test_a_short_final_segment_is_not_mistaken_for_a_closing_vertex(tmp_path: Path) -> None:
    """A distance tolerance would silently eat a real vertex ~1 cm from the start."""
    coords = [(7.0, 43.0), (7.001, 43.0), (7.0, 43.001), (7.0000001, 43.0)]
    path = write(tmp_path, coords, closed=False)
    back, closed = read_track_limit_kml(path)
    assert closed is False
    assert back == coords


# --------------------------------------------------------------- attribution


@pytest.mark.parametrize("edge", ["left", "right"])
def test_every_export_carries_the_bacinger_attribution(tmp_path: Path, edge: str) -> None:
    text = write(tmp_path, edge=edge).read_text()
    assert contains_attribution(text)
    assert "MIT" in text
    assert re.search(r"<!--[\s\S]*Tomislav Bacinger[\s\S]*-->", text), (
        "the attribution must be an XML comment, so it survives a reformat"
    )


def test_the_comment_cannot_break_the_xml(tmp_path: Path) -> None:
    """`--` is illegal inside an XML comment; an em dash in an attribution is not
    unusual, and producing unparseable XML would be worse than reflowing it."""
    from xml.etree import ElementTree

    path = write_track_limit_kml(
        tmp_path / "x.kml",
        COORDS,
        circuit="monaco",
        edge="left",
        attribution="dashes -- here --- and ---- there",
    )
    comment = path.read_text().split("<!--")[1].split("-->")[0]
    assert "--" not in comment
    ElementTree.parse(path)  # raises if the comment broke the document


def test_the_export_names_where_it_came_from(tmp_path: Path) -> None:
    """Separated from this repo, the file should still say what made it."""
    text = write(tmp_path).read_text()
    assert "ADR-0003" in text
    assert "trace_track_limits.html" in text


# ------------------------------------------------------------------ refusals


def test_edge_must_be_left_or_right() -> None:
    with pytest.raises(KmlError, match="edge must be one of"):
        edge_filename("monaco", "middle")


def test_writing_an_edge_with_one_vertex_is_refused(tmp_path: Path) -> None:
    with pytest.raises(KmlError, match="at least 2 vertices"):
        write(tmp_path, [(7.0, 43.0)])


def test_a_kml_with_no_linestring_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "empty.kml"
    path.write_text("<kml><Document></Document></kml>")
    with pytest.raises(KmlError, match="no <coordinates>"):
        read_track_limit_kml(path)


def test_a_kml_with_two_paths_is_refused(tmp_path: Path) -> None:
    """One path per file is the contract; guessing which one is the edge is a
    coin flip the operator would never see resolved."""
    path = tmp_path / "two.kml"
    path.write_text(
        "<kml><coordinates>7,43,0 7.1,43,0</coordinates>"
        "<coordinates>7,44,0 7.1,44,0</coordinates></kml>"
    )
    with pytest.raises(KmlError, match="one path per file"):
        read_track_limit_kml(path)


def test_a_non_numeric_coordinate_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "bad.kml"
    path.write_text("<kml><coordinates>7,43,0 seven,43,0</coordinates></kml>")
    with pytest.raises(KmlError, match="not numeric"):
        read_track_limit_kml(path)
