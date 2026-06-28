"""Typer CLI: `twinkly-mockup`."""

from __future__ import annotations

from pathlib import Path

import typer

from .compose import render_to_png
from .config import load_config

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
