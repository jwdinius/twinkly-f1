"""The seed fixture must stay cut from the centerlines it claims to describe.

`tests/fixtures/seed_boundary.json` is what gives `tests/js/geometry.test.js`
real circuit geometry to find folds in. A fixture that has drifted from
`configs/` is worse than no fixture: `node --test` would keep passing against
edges nothing seeds any more, and both the seeder and the detector could move
underneath it.

So the generator runs here and its output is compared byte-for-byte with the
committed file. Changing a centerline, a manifest half-width, or the seeding
maths therefore fails this test until the fixture is regenerated in the same
change.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "seed_boundary.json"


def _generator():
    spec = importlib.util.spec_from_file_location(
        "gen_seed_fixture", REPO / "scripts" / "gen_seed_fixture.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gen = _generator()
PAYLOAD = json.loads(FIXTURE.read_text())


def test_committed_fixture_is_what_python_generates_today() -> None:
    assert FIXTURE.read_text() == gen.render(gen.build()), (
        "tests/fixtures/seed_boundary.json is stale — "
        "re-run scripts/gen_seed_fixture.py and commit the result"
    )


def test_check_mode_agrees() -> None:
    assert gen.main(["--check"]) == 0


def test_every_manifest_circuit_has_a_shipped_case() -> None:
    """A newly declared circuit joins the fold suite rather than skipping it."""
    declared = {c["name"] for c in gen.manifest_circuits()}
    shipped = {c["circuit"] for c in PAYLOAD["cases"] if c["shipped"]}
    assert declared == shipped


@pytest.mark.parametrize("case", PAYLOAD["cases"], ids=lambda c: f"{c['circuit']}@{c['half_width_m']}")
def test_each_case_has_one_seed_vertex_per_centerline_vertex(case: dict) -> None:
    n = len(case["centerline_m"])
    assert len(case["left_m"]) == n
    assert len(case["right_m"]) == n
    assert len(case["miter_factors"]) == n


def test_the_fixture_carries_a_case_that_actually_folds() -> None:
    """Without one, the JS fold detector could return `[]` forever and pass.

    Not a geometric check — that is `tests/js/geometry.test.js`'s job. This only
    guards the fixture's *reason for existing*, so nobody trims the wide case
    away as redundant with the shipped ones.
    """
    wide = [c for c in PAYLOAD["cases"] if not c["shipped"]]
    assert wide, "the fixture has no over-wide case left to detect folds in"
    for case in wide:
        shipped_width = next(
            c["half_width_m"] for c in gen.manifest_circuits() if c["name"] == case["circuit"]
        )
        assert case["half_width_m"] > shipped_width
