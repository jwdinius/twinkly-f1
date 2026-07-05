# The fastest-lap solver runs only in its Docker container

## Status

accepted

## Context

`fastest-lap` is a C++ library (Ipopt + CppAD + lion-cpp + tinyxml2) with a
Python binding; running it requires compiling `libfastestlapc` and configuring
the Python wrapper for the local machine. Native builds — Ipopt's Fortran
toolchain in particular — are historically fragile and OS-specific, and this
project must stay reproducible across whatever machine drives the wall display.
The submodule ships a Linux Docker build environment for exactly this reason.

## Decision

Never build or run `fastest-lap` natively on the host. Both of its operations —
`circuit_preprocessor` (KML → discrete circuit XML) and `optimal_laptime` — run
**only inside its Docker container**. Orchestration (a two-stage compile→run
flow) and a mounted driver script live in the **twinkly-f1 repo**, not in
`fastest-lap` (zero source changes there) and not in `racetrack-mosaic`
(imagery-only). The container emits **raw, native-frame** artifacts
(`monaco.xml` + a `time,x,y,yaw` variables CSV) onto a mounted volume; all
interpretation happens host-side in `twinkly-mockup import-lap`.

## Consequences

- Portability: no host toolchain, no per-OS build breakage — the reason the
  Docker image was built in the first place.
- The shipped image is **compile-only** (no Python), so the twinkly repo adds a
  thin run image (python3 + numpy, built lib on `PYTHONPATH`) over it.
- The host `import-lap` bridge consumes **files**, not a live `fastest_lap`
  import — which also cleanly decouples the solver's coordinate frame from the
  mockup's (see the lat/lon frame crossing).
- A container round-trip is required to regenerate `monaco.xml`; it is therefore
  committed so the host bridge and still-frame smoke tests run without Docker.
