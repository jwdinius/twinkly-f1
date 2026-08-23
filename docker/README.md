# Solver container (fastest-lap)

The fastest-lap optimal-lap solver runs **only** inside Docker
([ADR-0002](../docs/adr/0002-solver-runs-only-in-container.md)). This directory
holds the two pieces the twinkly repo adds on top of the vendored submodule:

- **`Dockerfile`** — the *run* image. fastest-lap's shipped image
  (`submodules/fastest-lap/src/scripts/linux/Dockerfile`) is compile-only
  (ubuntu + build deps, no Python); this layers `python3` + `numpy` +
  `matplotlib` over it so the compiled `libfastestlapc` and its generated
  `fastest_lap.py` wrapper can be driven headless (`MPLBACKEND=Agg`).
  `matplotlib` is required because the wrapper imports `matplotlib.pyplot` at
  module top — a superset of ADR-0002's "python3 + numpy".

- **`solve_lap.py`** — the *driver*, run inside the container. It calls
  `circuit_preprocessor` (KMLs → circuit XML) then `optimal_laptime`, and emits
  the circuit XML + a native `time,x,y,yaw` CSV onto the mounted `/out`. It must
  not import `twinkly_mockup` — the container has no copy of this repo's package.
  Its `import fastest_lap` is deferred so the pure builders/transforms stay
  unit-testable off-container.

## Running

Driven from the host, not by hand — see `twinkly-mockup solve-lap` (implemented
in [`src/twinkly_mockup/solver.py`](../src/twinkly_mockup/solver.py)). The
orchestration builds both images, compiles the lib, and runs the driver over
these mounts:

| Host                          | Container       | Mode | Purpose                          |
| ----------------------------- | --------------- | ---- | -------------------------------- |
| `submodules/fastest-lap`      | `/fastest-lap`  | rw   | wrapper + compiled lib + database |
| config dir (KMLs)             | `/config`       | ro   | hand-traced track limits         |
| `docker/`                     | `/work`         | ro   | the driver script                |
| output dir                    | `/out`          | rw   | emitted circuit XML + native CSV |

The compile step uses `-DPYTHON_API_ABSOLUTE_PATH=off`, so the generated
wrapper's library path is relative to the submodule and survives the mount.
