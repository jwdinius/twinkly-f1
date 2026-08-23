"""Host-side orchestration for the fastest-lap solver (ADR-0002).

The solver never runs natively. This module drives a two-stage Docker flow
entirely from the host:

1. **build** — build fastest-lap's compile-only base image, then the twinkly run
   image (python3 + numpy/matplotlib) over it (see `docker/Dockerfile`).
2. **compile** — compile `libfastestlapc` + generate the Python wrapper into the
   mounted submodule (`-DPYTHON_API_ABSOLUTE_PATH=off`, so the wrapper's lib path
   is relative to the submodule and survives any mount point).
3. **run** — run the in-container driver (`docker/solve_lap.py`), which emits the
   circuit XML + native `time,x,y,yaw` CSV onto the mounted output dir — exactly
   what `twinkly-mockup import-lap` consumes.

The Docker command builders are pure and unit-tested; :func:`solve_lap` wires
them together through an injectable `runner` so the sequencing is testable
without Docker. Actually *running* the flow needs Docker + the hand-traced
track-limit KMLs (see the racetrack-mosaic tracing docs), which is why the
builders — not a live end-to-end — are what the tests pin down.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

# Repo-root-relative defaults (this file is src/twinkly_mockup/solver.py).
_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FASTEST_LAP_DIR = _REPO_ROOT / "submodules" / "fastest-lap"
DEFAULT_DOCKER_DIR = _REPO_ROOT / "docker"

# Image tags built and consumed by this orchestration.
BASE_IMAGE_TAG = "twinkly-fastestlap-base"
RUN_IMAGE_TAG = "twinkly-fastestlap-run"

# Container mount points (must match docker/Dockerfile's PYTHONPATH).
SUBMODULE_MOUNT = "/fastest-lap"
CONFIG_MOUNT = "/config"
OUT_MOUNT = "/out"
DRIVER_MOUNT = "/work"

# Working directory for the driver. The generated wrapper loads the compiled lib
# by a path relative to the *process CWD*, not to its own file
# (`-DPYTHON_API_ABSOLUTE_PATH=off` bakes in `../../build/lib/libfastestlapc.so.0.5`),
# so the driver must run from the wrapper's own directory. Everything the driver
# is handed is an absolute container path, so nothing else depends on the CWD.
WRAPPER_DIR = f"{SUBMODULE_MOUNT}/examples/python"

DRIVER_NAME = "solve_lap.py"
# Artifact names the driver writes into the output dir. The circuit XML is named
# after the circuit being solved, not fixed — an `artifacts/` dir holding a
# `monaco.xml` from a Silverstone solve is a trap, and the XML is the file
# `import-lap` is later pointed at by hand.
NATIVE_CSV_NAME = "lap.csv"


def circuit_xml_name(config: "SolverConfig") -> str:
    """Filename the driver writes the fastest-lap circuit XML under."""
    return f"{config.circuit}.xml"

# fastest-lap's compile-only base image lives here; we build it, then layer
# docker/Dockerfile over it.
_BASE_IMAGE_CONTEXT = ("src", "scripts", "linux")

Runner = Callable[[Sequence[str]], None]


class SolverError(ValueError):
    """Raised when the solver can't be configured or orchestrated."""


class SolverConfig(BaseModel):
    """An optimal-lap solve request (see configs/<circuit>_solver.yaml)."""

    model_config = ConfigDict(extra="forbid")

    circuit: str = Field(min_length=1)
    left_kml: Path
    right_kml: Path
    vehicle_xml: Path
    number_of_elements: int = Field(default=750, ge=2)
    is_closed: bool = True
    mode: str = "equally-spaced"


@dataclass(frozen=True)
class SolverArtifacts:
    """Host paths of what the driver emitted into the output dir."""

    circuit_xml: Path
    native_csv: Path


def load_solver_config(config_path: Path) -> SolverConfig:
    """Load a solver config YAML; relative paths resolve against its directory."""
    config_path = Path(config_path)
    with config_path.open("r") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise SolverError(
            f"solver config at {config_path} must be a YAML mapping, "
            f"got {type(raw).__name__}"
        )
    config = SolverConfig.model_validate(raw)
    base = config_path.resolve().parent
    return config.model_copy(
        update={
            "left_kml": _resolve(config.left_kml, base),
            "right_kml": _resolve(config.right_kml, base),
            "vehicle_xml": _resolve(config.vehicle_xml, base),
        }
    )


def _resolve(path: Path, base: Path) -> Path:
    return path if path.is_absolute() else base / path


def _container_path(host: Path, host_root: Path, mount: str) -> str:
    """Translate a host path under `host_root` to its `mount` container path."""
    try:
        rel = host.resolve().relative_to(host_root.resolve())
    except ValueError as e:
        raise SolverError(
            f"{host} is not under {host_root}, so it cannot be mounted at {mount}"
        ) from e
    return f"{mount}/{rel.as_posix()}"


def _user_flag(uid: int | None, gid: int | None) -> list[str]:
    return ["--user", f"{uid}:{gid}"] if uid is not None and gid is not None else []


def build_base_image_cmd(fastest_lap_dir: Path = DEFAULT_FASTEST_LAP_DIR) -> list[str]:
    """`docker build` for fastest-lap's compile-only base image."""
    context = Path(fastest_lap_dir).joinpath(*_BASE_IMAGE_CONTEXT)
    return ["docker", "build", "-t", BASE_IMAGE_TAG, str(context)]


def build_run_image_cmd(docker_dir: Path = DEFAULT_DOCKER_DIR) -> list[str]:
    """`docker build` for the twinkly run image, layered over the base."""
    return [
        "docker",
        "build",
        "-t",
        RUN_IMAGE_TAG,
        "--build-arg",
        f"BASE_IMAGE={BASE_IMAGE_TAG}",
        str(Path(docker_dir)),
    ]


def compile_cmd(
    fastest_lap_dir: Path = DEFAULT_FASTEST_LAP_DIR,
    *,
    uid: int | None = None,
    gid: int | None = None,
) -> list[str]:
    """Compile `libfastestlapc` + generate the wrapper inside the base image.

    Runs cmake/make directly (rather than fastest-lap's `compile.sh`, whose
    `mkdir build` is not rerun-safe) so the flow is idempotent — zero source
    changes to the submodule. Uses `-DPYTHON_API_ABSOLUTE_PATH=off` so the
    generated wrapper references the lib by a path relative to the submodule.
    """
    script = (
        f"mkdir -p {SUBMODULE_MOUNT}/build && cd {SUBMODULE_MOUNT}/build && "
        "cmake .. -DPYTHON_API_ABSOLUTE_PATH=off && make -j"
    )
    return [
        "docker",
        "run",
        "--rm",
        *_user_flag(uid, gid),
        "-v",
        f"{Path(fastest_lap_dir).resolve()}:{SUBMODULE_MOUNT}",
        "-w",
        SUBMODULE_MOUNT,
        BASE_IMAGE_TAG,
        "sh",
        "-c",
        script,
    ]


def driver_cmd(
    config: SolverConfig,
    config_dir: Path,
    out_dir: Path,
    *,
    fastest_lap_dir: Path = DEFAULT_FASTEST_LAP_DIR,
    docker_dir: Path = DEFAULT_DOCKER_DIR,
    uid: int | None = None,
    gid: int | None = None,
) -> list[str]:
    """`docker run` the in-container driver over the mounted volumes.

    `config_dir` (holding the KMLs) mounts read-only at /config, the submodule
    at /fastest-lap, the driver dir at /work, and `out_dir` read-write at /out.
    The working directory is the wrapper's own dir (see :data:`WRAPPER_DIR`), not
    /out — the wrapper resolves its lib path against the CWD.
    """
    fastest_lap_dir = Path(fastest_lap_dir).resolve()
    config_dir = Path(config_dir).resolve()
    out_dir = Path(out_dir).resolve()
    docker_dir = Path(docker_dir).resolve()

    left = _container_path(config.left_kml, config_dir, CONFIG_MOUNT)
    right = _container_path(config.right_kml, config_dir, CONFIG_MOUNT)
    vehicle = _container_path(config.vehicle_xml, fastest_lap_dir, SUBMODULE_MOUNT)
    circuit_xml = f"{OUT_MOUNT}/{circuit_xml_name(config)}"
    out_csv = f"{OUT_MOUNT}/{NATIVE_CSV_NAME}"

    return [
        "docker",
        "run",
        "--rm",
        *_user_flag(uid, gid),
        "-v",
        f"{fastest_lap_dir}:{SUBMODULE_MOUNT}",
        "-v",
        f"{config_dir}:{CONFIG_MOUNT}:ro",
        "-v",
        f"{docker_dir}:{DRIVER_MOUNT}:ro",
        "-v",
        f"{out_dir}:{OUT_MOUNT}",
        "-w",
        WRAPPER_DIR,
        RUN_IMAGE_TAG,
        "python3",
        f"{DRIVER_MOUNT}/{DRIVER_NAME}",
        "--left",
        left,
        "--right",
        right,
        "--vehicle",
        vehicle,
        "--circuit-xml",
        circuit_xml,
        "--out-csv",
        out_csv,
        "--n-elements",
        str(config.number_of_elements),
        "--mode",
        config.mode,
        "--closed" if config.is_closed else "--no-closed",
    ]


def _default_runner(cmd: Sequence[str]) -> None:
    subprocess.run(list(cmd), check=True)


def solve_lap(
    config: SolverConfig,
    config_dir: Path,
    out_dir: Path,
    *,
    fastest_lap_dir: Path = DEFAULT_FASTEST_LAP_DIR,
    docker_dir: Path = DEFAULT_DOCKER_DIR,
    runner: Runner = _default_runner,
    build: bool = True,
    compile: bool = True,
    use_host_user: bool = True,
) -> SolverArtifacts:
    """Run the full build → compile → solve flow; return the emitted artifacts.

    `runner` receives each command's argv; the default runs it via subprocess.
    Inject a recorder to dry-run or test. `build`/`compile` can be skipped once
    the images and compiled lib already exist (they are the slow stages).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    uid, gid = (os.getuid(), os.getgid()) if use_host_user else (None, None)

    if build:
        runner(build_base_image_cmd(fastest_lap_dir))
        runner(build_run_image_cmd(docker_dir))
    if compile:
        runner(compile_cmd(fastest_lap_dir, uid=uid, gid=gid))
    runner(
        driver_cmd(
            config,
            config_dir,
            out_dir,
            fastest_lap_dir=fastest_lap_dir,
            docker_dir=docker_dir,
            uid=uid,
            gid=gid,
        )
    )
    return SolverArtifacts(
        circuit_xml=out_dir / circuit_xml_name(config),
        native_csv=out_dir / NATIVE_CSV_NAME,
    )
