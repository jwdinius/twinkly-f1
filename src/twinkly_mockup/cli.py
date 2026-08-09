"""Typer CLI: `twinkly-mockup`."""

from __future__ import annotations

import shlex
from pathlib import Path

import typer

from .compose import render_to_png
from .config import load_config
from .import_lap import DEFAULT_DT_S, import_lap
from .solver import load_solver_config, solve_lap
from .trajectory import Trajectory

app = typer.Typer(
    help="Twinkly Squares wall-display mockup renderer.",
    no_args_is_help=True,
)


@app.command()
def render(
    config_path: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        readable=True,
        help="Path to a layout YAML config.",
    ),
) -> None:
    """Render a single config to a PNG."""
    config = load_config(config_path)
    if config.output_path is None:
        typer.echo(
            f"config {config_path} has no `output_path`; either add one or use "
            f"`render-all {config_path} --out <dir>`.",
            err=True,
        )
        raise typer.Exit(code=2)
    out_path = render_to_png(config)
    typer.echo(f"wrote {out_path}")


def _not_implemented(name: str) -> None:
    typer.echo(f"{name} is not implemented in the walking skeleton", err=True)
    raise typer.Exit(code=2)


@app.command("render-all")
def render_all(
    config_paths: list[Path] = typer.Argument(  # noqa: B008 — typer DI
        ...,
        exists=True,
        dir_okay=False,
        readable=True,
        help="One or more snapshot YAML configs.",
    ),
    out: Path = typer.Option(
        ...,
        "--out",
        file_okay=False,
        help="Output directory; created if missing.",
    ),
    layout_paths: list[Path] = typer.Option(  # noqa: B008 — typer DI
        None,
        "--layout",
        exists=True,
        dir_okay=False,
        readable=True,
        help=(
            "Layout YAML override(s). Repeat to sweep one snapshot across "
            "multiple layouts (cross-product). When given, each output is "
            "named `<snapshot_stem>__<layout_stem>.png`."
        ),
    ),
) -> None:
    """Render many configs to a directory.

    Without `--layout`, renders each config to `<out>/<config_stem>.png`.
    With `--layout L1 --layout L2 ...`, applies the cross product: each
    snapshot config is rendered under each layout, named
    `<out>/<snapshot_stem>__<layout_stem>.png`. The single 3×3 sizing sweep
    that drives the MVP layout decision is one such invocation — see
    `scripts/sizing_sweep.sh`.
    """
    out.mkdir(parents=True, exist_ok=True)
    layout_choices: list[Path | None] = list(layout_paths) if layout_paths else [None]

    for cfg_path in config_paths:
        for layout_path in layout_choices:
            stem = (
                f"{cfg_path.stem}__{layout_path.stem}"
                if layout_path is not None
                else cfg_path.stem
            )
            out_path = out / f"{stem}.png"
            config = load_config(cfg_path, layout_override=layout_path)
            config = config.model_copy(update={"output_path": out_path})
            render_to_png(config)
            typer.echo(f"wrote {out_path}")


@app.command("import-lap")
def import_lap_cmd(
    native_csv: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        readable=True,
        help="Native fastest-lap lap CSV with columns time,x,y,yaw.",
    ),
    circuit: Path = typer.Option(
        ...,
        "--circuit",
        exists=True,
        dir_okay=False,
        readable=True,
        help="fastest-lap circuit XML carrying <GPS_parameters> (e.g. silverstone.xml).",
    ),
    mosaic: Path = typer.Option(
        ...,
        "--mosaic",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Mosaic sidecar YAML defining the mockup ENU origin.",
    ),
    out: Path = typer.Option(
        ...,
        "--out",
        dir_okay=False,
        help="Output trajectory CSV path.",
    ),
    dt: float = typer.Option(
        DEFAULT_DT_S,
        "--dt",
        min=1e-6,
        help="Uniform timestep (seconds) for the resampled trajectory.",
    ),
) -> None:
    """Bridge a fastest-lap native lap into the renderer trajectory CSV.

    Crosses the solver's coordinate frame into the mockup ENU frame via lat/lon
    (from the circuit XML's GPS_parameters + the mosaic origin) and resamples to
    a uniform `dt`, then validates the result against the trajectory schema.
    """
    out_path = import_lap(native_csv, circuit, mosaic, out, dt=dt)
    traj = Trajectory.load(out_path)
    typer.echo(
        f"wrote {out_path} ({len(traj.t)} samples, dt={traj.dt:.4g}s, "
        f"duration={traj.duration:.3f}s)"
    )


@app.command("solve-lap")
def solve_lap_cmd(
    config_path: Path = typer.Option(
        ...,
        "--config",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Solver config YAML (e.g. configs/silverstone_solver.yaml).",
    ),
    out: Path = typer.Option(
        ...,
        "--out",
        file_okay=False,
        help="Output directory for the circuit XML + native lap CSV.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print the Docker commands that would run, without executing them.",
    ),
    build: bool = typer.Option(
        True,
        "--build/--no-build",
        help="Build the base + run images first (skip if already built).",
    ),
    compile: bool = typer.Option(
        True,
        "--compile/--no-compile",
        help="Compile libfastestlapc first (skip if already compiled).",
    ),
) -> None:
    """Solve a circuit's optimal lap in Docker (ADR-0002).

    Runs the fastest-lap solver entirely inside its container and writes the
    circuit XML + a native `time,x,y,yaw` CSV into `--out`. Feed those to
    `import-lap` to produce the renderer trajectory CSV. Requires Docker and the
    hand-traced track-limit KMLs referenced by the config.
    """
    config = load_solver_config(config_path)
    config_dir = config_path.resolve().parent

    if dry_run:
        typer.echo("# dry run — commands that would execute:")
        runner = lambda cmd: typer.echo(shlex.join(cmd))  # noqa: E731
        solve_lap(config, config_dir, out, runner=runner, build=build, compile=compile)
        return

    artifacts = solve_lap(config, config_dir, out, build=build, compile=compile)
    typer.echo(f"wrote {artifacts.circuit_xml} and {artifacts.native_csv}")
    typer.echo(
        "next: twinkly-mockup import-lap "
        f"{artifacts.native_csv} --circuit {artifacts.circuit_xml} "
        f"--mosaic configs/{config.circuit}_mosaic.yaml --out <trajectory.csv>"
    )


@app.command()
def stream() -> None:
    """Stub for phase-2 streaming preview."""
    _not_implemented("stream")


@app.command()
def push() -> None:
    """Stub for phase-3 hardware push."""
    _not_implemented("push")


if __name__ == "__main__":
    app()
