# twinkly-f1 fastest-lap *run* image (ADR-0002).
#
# fastest-lap's shipped Docker image is compile-only (ubuntu + build deps, no
# Python). This image layers the Python runtime over it so the compiled
# `libfastestlapc` + generated `fastest_lap.py` wrapper can be driven headless.
#
# BASE_IMAGE must be a locally-built tag of
# submodules/fastest-lap/src/scripts/linux/Dockerfile — the host orchestration
# (`twinkly_mockup.solver`) builds it first, then builds this over it.
ARG BASE_IMAGE=twinkly-fastestlap-base
FROM ${BASE_IMAGE}

ARG DEBIAN_FRONTEND=noninteractive

# fastest_lap.py imports `matplotlib.pyplot` and `numpy` at module top, so both
# are required just to `import fastest_lap` — a superset of ADR-0002's "python3
# + numpy". Agg keeps matplotlib headless (no display in the container).
RUN apt-get update && apt-get -y --no-install-recommends install \
    python3 \
    python3-numpy \
    python3-matplotlib \
    && rm -rf /var/lib/apt/lists/*

ENV MPLBACKEND=Agg
# The container runs as the host uid (so emitted artifacts aren't root-owned),
# which has no writable HOME — point matplotlib's cache somewhere it can write.
ENV MPLCONFIGDIR=/tmp/matplotlib
# The submodule is mounted here at run time; its generated wrapper lives under
# examples/python (see compile.sh's -DPYTHON_API_ABSOLUTE_PATH=off).
ENV PYTHONPATH=/fastest-lap/examples/python

# The run command overrides this with -w: the wrapper resolves its compiled-lib
# path against the CWD, so the driver must run from /fastest-lap/examples/python.
WORKDIR /out
