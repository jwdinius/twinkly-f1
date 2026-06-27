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
        help="One or more layout YAML configs.",
    ),
) -> None:
    """Stub: render multiple configs in one invocation. Lands in a later slice."""
    _not_implemented("render-all")


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
