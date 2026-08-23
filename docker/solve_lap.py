#!/usr/bin/env python3
"""In-container fastest-lap solver driver (ADR-0002).

Runs *inside* the twinkly run image, where the only things on hand are python3,
numpy/matplotlib (pulled in transitively by fastest-lap's wrapper) and the
compiled ``fastest_lap`` module on ``PYTHONPATH``. It must NOT import
``twinkly_mockup`` — the container has no copy of this repo's package.

Two solver operations run here, both from fastest-lap's Python API:

1. ``circuit_preprocessor`` — hand-traced left/right track-limit KMLs → a
   discrete circuit XML (carrying the ``<GPS_parameters>`` block that
   ``import-lap`` later reads to cross frames).
2. ``optimal_laptime`` — the minimum-time line for the given vehicle over that
   circuit.

The driver emits exactly what the host-side ``twinkly-mockup import-lap`` bridge
consumes: the circuit XML plus a ``time,x,y,yaw`` CSV in fastest-lap's own
native frame. All frame interpretation happens host-side, never here.

``import fastest_lap`` is deferred into :func:`run_solver` so the pure builders
and transforms below stay importable (and unit-testable) off-container.
"""

from __future__ import annotations

import argparse
import csv
from collections.abc import Mapping, Sequence
from pathlib import Path

# fastest-lap `download_variables` keys for the channels we emit. Confirmed
# against examples/python/f1/optimal-laptime/1-simple-lap.
VAR_TIME = "time"
VAR_X = "chassis.position.x"
VAR_Y = "chassis.position.y"
VAR_YAW = "chassis.attitude.yaw"

# Native CSV contract expected by `twinkly-mockup import-lap`.
NATIVE_HEADER: tuple[str, ...] = ("time", "x", "y", "yaw")

# fastest-lap variable-name prefixes (kept out of the downloaded dict keys).
_TRACK_PREFIX = "track/"
_RUN_PREFIX = "run/"


class SolverDriverError(RuntimeError):
    """Raised when the solver produces artifacts we can't emit."""


def build_preprocessor_options(
    left_kml: str,
    right_kml: str,
    circuit_xml_out: str,
    *,
    is_closed: bool,
    number_of_elements: int,
    mode: str,
) -> str:
    """Build the ``circuit_preprocessor`` options XML.

    Mirrors examples/python/circuit_preprocessor. Paths are *container* paths;
    ``circuit_xml_out`` is where the discrete circuit XML (with GPS_parameters)
    is written.
    """
    closed = "true" if is_closed else "false"
    return (
        "<options>"
        "<kml_files>"
        f"<left>{left_kml}</left>"
        f"<right>{right_kml}</right>"
        "</kml_files>"
        f"<mode>{mode}</mode>"
        f"<is_closed>{closed}</is_closed>"
        f"<number_of_elements>{int(number_of_elements)}</number_of_elements>"
        f"<xml_file_name>{circuit_xml_out}</xml_file_name>"
        "<output_variables>"
        f"<prefix>{_TRACK_PREFIX}</prefix>"
        "</output_variables>"
        "</options>"
    )


def build_optimal_laptime_options(*, print_level: int = 5) -> str:
    """Build the ``optimal_laptime`` options XML.

    No explicit ``<variables>`` block, so fastest-lap downloads its full default
    channel set for the vehicle — which includes the four we read below.
    """
    return (
        "<options>"
        "<output_variables>"
        f"<prefix>{_RUN_PREFIX}</prefix>"
        "</output_variables>"
        f"<print_level>{int(print_level)}</print_level>"
        "</options>"
    )


def optimal_lap_to_rows(
    run: Mapping[str, Sequence[float]],
) -> list[tuple[float, float, float, float]]:
    """Zip the solver's ``time/x/y/yaw`` channels into native CSV rows.

    ``run`` is the dict returned by ``fastest_lap.download_variables`` (keys are
    bare variable names, no prefix). Raises if a channel is missing or the
    channels disagree in length.
    """
    missing = [k for k in (VAR_TIME, VAR_X, VAR_Y, VAR_YAW) if k not in run]
    if missing:
        raise SolverDriverError(
            f"solver output is missing channel(s): {', '.join(missing)}; "
            f"got: {', '.join(sorted(run))}"
        )
    time, x, y, yaw = run[VAR_TIME], run[VAR_X], run[VAR_Y], run[VAR_YAW]
    lengths = {len(time), len(x), len(y), len(yaw)}
    if len(lengths) != 1:
        raise SolverDriverError(
            f"solver channels disagree in length: time={len(time)}, "
            f"x={len(x)}, y={len(y)}, yaw={len(yaw)}"
        )
    if not time:
        raise SolverDriverError("solver returned an empty lap (no samples)")
    return [
        (float(t), float(px), float(py), float(pyaw))
        for t, px, py, pyaw in zip(time, x, y, yaw, strict=True)
    ]


def write_native_csv(
    out_csv_path: str | Path,
    rows: Sequence[tuple[float, float, float, float]],
) -> None:
    """Write native ``time,x,y,yaw`` rows — the `import-lap` input contract."""
    out_csv_path = Path(out_csv_path)
    out_csv_path.parent.mkdir(parents=True, exist_ok=True)
    with out_csv_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(NATIVE_HEADER)
        for row in rows:
            writer.writerow(f"{v:.12g}" for v in row)


def run_solver(args: argparse.Namespace) -> None:
    """Run both solver stages and emit the native artifacts (impure)."""
    import fastest_lap  # deferred: only present inside the container

    fastest_lap.circuit_preprocessor(
        build_preprocessor_options(
            args.left,
            args.right,
            args.circuit_xml,
            is_closed=args.closed,
            number_of_elements=args.n_elements,
            mode=args.mode,
        )
    )

    vehicle, track = "car", "track"
    fastest_lap.create_vehicle_from_xml(vehicle, args.vehicle)
    fastest_lap.create_track_from_xml(track, args.circuit_xml)
    s = fastest_lap.track_download_data(track, "arclength")
    run = fastest_lap.download_variables(
        *fastest_lap.optimal_laptime(
            vehicle, track, s, build_optimal_laptime_options()
        )
    )

    rows = optimal_lap_to_rows(run)
    write_native_csv(args.out_csv, rows)
    print(f"wrote {args.circuit_xml} and {args.out_csv} ({len(rows)} samples)")


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="fastest-lap in-container driver")
    parser.add_argument("--left", required=True, help="left track-limit KML")
    parser.add_argument("--right", required=True, help="right track-limit KML")
    parser.add_argument("--vehicle", required=True, help="vehicle XML")
    parser.add_argument(
        "--circuit-xml",
        dest="circuit_xml",
        required=True,
        help="output discrete circuit XML (preprocessor writes here)",
    )
    parser.add_argument(
        "--out-csv", dest="out_csv", required=True, help="output native lap CSV"
    )
    parser.add_argument("--n-elements", dest="n_elements", type=int, default=750)
    parser.add_argument("--mode", default="equally-spaced")
    parser.add_argument(
        "--closed",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="closed circuit (default) or --no-closed for an open track",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    run_solver(_parse_args(argv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
