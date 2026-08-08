"""The golden fixture must stay cut from the sidecars it claims to describe.

`tests/fixtures/frame_transform.json` is what binds `scripts/tracer/frame.js` to
the Python projection (ADR-0004). A fixture that has drifted from `configs/` is
worse than no fixture: `node --test` would keep passing against a frame nothing
ships with, and the JS could diverge freely underneath it.

So the generator is run here and its output compared byte-for-byte with the
committed file. Re-registering a circuit therefore fails this test until the
fixture is regenerated, in the same change.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "frame_transform.json"


def _generator():
    spec = importlib.util.spec_from_file_location(
        "gen_frame_fixture", REPO / "scripts" / "gen_frame_fixture.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gen = _generator()


def test_committed_fixture_is_what_python_generates_today() -> None:
    """Regenerating an unchanged frame produces no diff."""
    assert FIXTURE.read_text() == gen.render(gen.build()), (
        "tests/fixtures/frame_transform.json is stale — "
        "re-run scripts/gen_frame_fixture.py and commit the result"
    )


def test_check_mode_agrees() -> None:
    assert gen.main(["--check"]) == 0


def test_fixture_spans_the_image_corners() -> None:
    """Near the origin every sign convention agrees; the corners are the test."""
    payload = json.loads(FIXTURE.read_text())
    assert payload["circuits"], "no circuits in the fixture"
    for circuit in payload["circuits"]:
        ox, oy = circuit["sidecar"]["origin_px"]
        corners = {(0.0, 0.0), (2 * ox, 0.0), (0.0, 2 * oy), (2 * ox, 2 * oy)}
        for cx, cy in corners:
            assert any(
                abs(s["px"] - cx) < 1e-3 and abs(s["py"] - cy) < 1e-3
                for s in circuit["samples"]
            ), f"{circuit['name']}: no sample at corner ({cx}, {cy})"


def test_fixture_covers_every_shipped_circuit() -> None:
    payload = json.loads(FIXTURE.read_text())
    names = {c["name"] for c in payload["circuits"]}
    assert {"monaco", "silverstone"} <= names


@pytest.mark.parametrize("key", ["north_angle_deg", "grid_scale", "utm_epsg"])
def test_fixture_carries_the_registration_keys(key: str) -> None:
    """A fixture cut from an unregistered sidecar would pin the old flat model."""
    payload = json.loads(FIXTURE.read_text())
    for circuit in payload["circuits"]:
        assert key in circuit["sidecar"], f"{circuit['name']} fixture lacks {key}"
