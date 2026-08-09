#!/usr/bin/env python3
"""Serve the track-limit tracer and the circuit manifest it boots from.

ADR-0003 puts track-limit authoring in an in-repo web app rather than Google
Earth Pro, with circuits declared in `configs/circuits.json`. This is the server
side of that: a plain checkout plus `uv run scripts/trace.py` gets you a working
tracer, with no build step and nothing generated into `configs/`.

Two reasons the app is served rather than opened off the filesystem:

* `trace_track_limits.html` imports `scripts/tracer/frame.js` as an ES module,
  and `file://` will not load a module import.
* The Sidecar is YAML and the Centerline is GeoJSON. Rather than vendor a YAML
  parser into the page or generate a JavaScript blob next to the configs (which
  is what this replaced), the server reads both and hands the page one JSON boot
  document per circuit. Python already owns the Sidecar schema, so the tracer
  cannot disagree with the renderer about what a Sidecar field means.

Routes:

    GET  /                                -> redirect to the tracer
    GET  /api/circuits                    -> the manifest, as a list
    GET  /api/circuits/<name>             -> one resolved circuit, or 404
    GET  /api/circuits/<name>/seed        -> both edges, offset from the centerline
    GET  /api/circuits/<name>/kml/<edge>  -> re-import a previously exported edge
    POST /api/circuits/<name>/kml/<edge>  -> write configs/<name>_<edge>.kml
    GET  /<anything else>                 -> static file from the repo root

Export writes into `configs/` next to the manifest rather than through the
browser's download directory, because that is where the file has to end up and
a step that consists of "now move this file" is a step that gets skipped. It is
also the only way the export can be guaranteed to carry its attribution
comment: the page never formats a KML.

A circuit that is not in the manifest 404s with a message naming the manifest
and the circuits that *are* in it. There is deliberately no fallback circuit:
silently booting Monaco when you asked for something else is how you spend an
afternoon tracing the wrong asphalt.

    uv run scripts/trace.py                 # serve and open a browser
    uv run scripts/trace.py --port 9000 --no-browser
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from collections.abc import Callable
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from twinkly_mockup.centerline import read_linestring_lonlat  # noqa: E402
from twinkly_mockup.circuits import (  # noqa: E402
    DEFAULT_MANIFEST,
    REQUIRED_KEYS,
    Circuit,
    ManifestError,
    load_manifest,
)
from twinkly_mockup.kml import (  # noqa: E402
    KmlError,
    read_track_limit_kml,
    write_track_limit_kml,
)
from twinkly_mockup.mosaic import MosaicSidecar  # noqa: E402
from twinkly_mockup.seed import drop_seam_vertex, seed_boundary  # noqa: E402

TRACER_PATH = "/scripts/trace_track_limits.html"

# The manifest itself lives in `twinkly_mockup.circuits` — it has a second
# consumer in scripts/derive_corner_poses.py. Re-exported above so this module's
# API is unchanged: `trace.load_manifest`, `trace.ManifestError`, `trace.Circuit`.


class NotFoundError(RuntimeError):
    """The circuit is declared; the thing asked about it does not exist yet."""


def resolve_circuit(circuit: Circuit, *, repo_root: Path = REPO_ROOT) -> dict:
    """Read a circuit's Sidecar and Centerline into the tracer's boot document.

    The Sidecar goes through `MosaicSidecar` rather than straight from YAML so
    the tracer is fed exactly the schema the renderer validates — including its
    defaults for the ADR-0004 grid fields, which `frame.js` mirrors.

    A missing mosaic PNG is reported, not raised: it is gitignored build output,
    and the honest response is to tell the operator which script rebuilds it
    rather than to refuse to start.
    """
    if not circuit.sidecar.exists():
        raise ManifestError(f"circuit `{circuit.name}`: no sidecar at {circuit.sidecar}")
    if not circuit.centerline.exists():
        raise ManifestError(f"circuit `{circuit.name}`: no centerline at {circuit.centerline}")

    raw = yaml.safe_load(circuit.sidecar.read_text())
    if not isinstance(raw, dict):
        raise ManifestError(f"circuit `{circuit.name}`: {circuit.sidecar} is not a YAML mapping")
    sidecar = MosaicSidecar.model_validate(raw)

    coords, closed = drop_seam_vertex(read_linestring_lonlat(circuit.centerline))

    return {
        "name": circuit.name,
        "title": circuit.title,
        "attribution": circuit.attribution,
        "half_width_m": circuit.half_width_m,
        "mosaic_url": _url_for(circuit.mosaic, repo_root),
        "mosaic_present": circuit.mosaic.exists(),
        "mosaic_hint": f"scripts/build_{circuit.name}_mosaic.sh",
        "sidecar_path": _relative(circuit.sidecar, repo_root),
        "sidecar": {
            "origin_lat": sidecar.origin_lat,
            "origin_lon": sidecar.origin_lon,
            "origin_px": list(sidecar.origin_px),
            "m_per_px": sidecar.m_per_px,
            "north_angle_deg": sidecar.north_angle_deg,
            "utm_epsg": sidecar.utm_epsg,
            "grid_scale": sidecar.grid_scale,
        },
        "centerline": [list(c) for c in coords],
        "centerline_closed": closed,
    }


def resolve_seed(circuit: Circuit) -> dict:
    """Seed both edges for a circuit, ready for the tracer's working store.

    Seeding is a server call rather than page arithmetic because it happens once
    per circuit, on geometry the server already has open, and because the miter
    clamp is the kind of thing that wants a `pytest` around it. Dragging is the
    part that has to be local, and dragging needs no geometry beyond the frame.
    """
    resolved = resolve_circuit(circuit)
    seed = seed_boundary(
        resolved["centerline"],
        circuit.half_width_m,
        closed=resolved["centerline_closed"],
    )
    return {
        "name": circuit.name,
        "half_width_m": seed.half_width_m,
        "miter_limit": seed.miter_limit,
        "closed": resolved["centerline_closed"],
        "left": [list(p) for p in seed.left],
        "right": [list(p) for p in seed.right],
        "clamped": list(seed.clamped),
    }


def read_edge(circuit: Circuit, edge: str) -> dict:
    """Re-import one previously exported edge.

    The tracer's working store is `localStorage`, so this is the only thing
    standing between a cleared cache and hours of lost dragging. It reads the
    file the export wrote, in `configs/`, rather than asking the operator to
    find it again — a resume path with a file picker in it is one people put off
    until after they have already lost the work.
    """
    path = circuit.kml_path(edge)
    if not path.exists():
        raise NotFoundError(
            f"`{circuit.name}` has no exported {edge} edge at "
            f"{_relative(path, REPO_ROOT)} — export one first"
        )
    coords, closed = read_track_limit_kml(path)
    return {
        "name": circuit.name,
        "edge": edge,
        "path": _relative(path, REPO_ROOT),
        "closed": closed,
        "coords": [list(c) for c in coords],
    }


def write_edge(circuit: Circuit, edge: str, body: dict) -> dict:
    """Export one edge straight into `configs/`, attribution and all."""
    coords = body.get("coords")
    if not isinstance(coords, list):
        raise KmlError("expected a `coords` array of [lon, lat] pairs")
    path = write_track_limit_kml(
        circuit.kml_path(edge),
        coords,
        circuit=circuit.name,
        edge=edge,
        attribution=circuit.attribution,
        closed=bool(body.get("closed", True)),
    )
    return {
        "name": circuit.name,
        "edge": edge,
        "path": _relative(path, REPO_ROOT),
        "vertices": len(coords),
        "closed": bool(body.get("closed", True)),
    }


def _relative(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _url_for(path: Path, repo_root: Path) -> str:
    return "/" + _relative(path, repo_root)


class TracerHandler(SimpleHTTPRequestHandler):
    """Static files from the repo root, plus the manifest API."""

    def __init__(self, *args, manifest_path: Path = DEFAULT_MANIFEST, **kwargs) -> None:
        # `BaseHTTPRequestHandler.__init__` serves the request before it
        # returns, so anything the handlers read must be set first.
        self.manifest_path = Path(manifest_path)
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:  # noqa: N802 - http.server's spelling
        route = self.path.split("?", 1)[0].rstrip("/") or "/"
        if route == "/":
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", TRACER_PATH)
            self.end_headers()
            return
        if route == "/api/circuits":
            self._api_index()
            return
        if route.startswith("/api/circuits/"):
            parts = route[len("/api/circuits/") :].split("/")
            if len(parts) == 1:
                self._with_circuit(parts[0], resolve_circuit)
            elif parts[1:] == ["seed"]:
                self._with_circuit(parts[0], resolve_seed)
            elif len(parts) == 3 and parts[1] == "kml":
                self._with_circuit(parts[0], lambda c: read_edge(c, parts[2]))
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": f"no such endpoint: {route}"})
            return
        if route.startswith("/api/"):
            self._json(HTTPStatus.NOT_FOUND, {"error": f"no such endpoint: {route}"})
            return
        super().do_GET()

    def _api_index(self) -> None:
        try:
            circuits = load_manifest(self.manifest_path)
        except ManifestError as e:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(e)})
            return
        self._json(
            HTTPStatus.OK,
            {
                "manifest": _relative(self.manifest_path, REPO_ROOT),
                "circuits": [
                    {"name": c.name, "title": c.title, "attribution": c.attribution}
                    for c in circuits.values()
                ],
            },
        )

    def do_POST(self) -> None:  # noqa: N802 - http.server's spelling
        route = self.path.split("?", 1)[0].rstrip("/") or "/"
        parts = route[len("/api/circuits/") :].split("/") if route.startswith(
            "/api/circuits/"
        ) else []
        if len(parts) != 3 or parts[1] != "kml":
            self._json(HTTPStatus.NOT_FOUND, {"error": f"nothing accepts POST at {route}"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError) as e:
            self._json(HTTPStatus.BAD_REQUEST, {"error": f"unreadable request body: {e}"})
            return
        if not isinstance(body, dict):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "expected a JSON object"})
            return
        self._with_circuit(parts[0], lambda c: write_edge(c, parts[2], body))

    def _with_circuit(self, name: str, build: Callable[[Circuit], dict]) -> None:
        """Look a circuit up, then answer with `build(circuit)`.

        Every per-circuit endpoint shares this so they share one 404: a name the
        manifest does not declare gets the same explicit refusal, naming the
        manifest and what *is* in it, whichever endpoint it arrived at.
        """
        try:
            circuits = load_manifest(self.manifest_path)
        except ManifestError as e:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(e)})
            return
        circuit = circuits.get(name)
        if circuit is None:
            self._json(
                HTTPStatus.NOT_FOUND,
                {
                    "error": (
                        f"`{name}` is not declared in "
                        f"{_relative(self.manifest_path, REPO_ROOT)}. "
                        f"Declared: {', '.join(circuits)}."
                    )
                },
            )
            return
        try:
            payload = build(circuit)
        except NotFoundError as e:
            self._json(HTTPStatus.NOT_FOUND, {"error": str(e)})
            return
        except KmlError as e:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return
        except (ManifestError, ValueError) as e:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(e)})
            return
        self._json(HTTPStatus.OK, payload)

    def _json(self, status: HTTPStatus, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # The boot document is derived from files on disk that the operator is
        # actively rebuilding; a cached one would show yesterday's frame.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def make_server(
    host: str, port: int, *, manifest_path: Path = DEFAULT_MANIFEST
) -> ThreadingHTTPServer:
    """Bind a server. `port=0` picks a free one — read it back off the socket."""
    handler = partial(
        TracerHandler, directory=str(REPO_ROOT), manifest_path=Path(manifest_path)
    )
    return ThreadingHTTPServer((host, port), handler)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--host", default="127.0.0.1", help="interface to bind (default: loopback)")
    p.add_argument("--port", type=int, default=8765, help="port to bind (0 picks a free one)")
    p.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST, help="circuit manifest")
    p.add_argument("--no-browser", action="store_true", help="do not open a browser")
    args = p.parse_args(argv)

    # Resolve everything up front: a broken sidecar path should be a line in the
    # terminal before you click, not a banner after.
    try:
        circuits = load_manifest(args.manifest)
    except ManifestError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    for circuit in circuits.values():
        try:
            resolved = resolve_circuit(circuit)
        except (ManifestError, ValueError) as e:
            print(f"  {circuit.name:<12} BROKEN — {e}", file=sys.stderr)
            continue
        n = len(resolved["centerline"])
        mosaic = "" if resolved["mosaic_present"] else f" — MISSING, run {resolved['mosaic_hint']}"
        print(f"  {circuit.name:<12} {n} centerline vertices, {circuit.half_width_m} m half-width{mosaic}")

    server = make_server(args.host, args.port, manifest_path=args.manifest)
    url = f"http://{args.host}:{server.server_address[1]}{TRACER_PATH}"
    print(f"\ntracer at {url}  (ctrl-c to stop)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
