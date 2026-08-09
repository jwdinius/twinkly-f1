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

    GET /                       -> redirect to the tracer
    GET /api/circuits           -> the manifest, as a list
    GET /api/circuits/<name>    -> one resolved circuit, or 404
    GET /<anything else>        -> static file from the repo root

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
from dataclasses import dataclass
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from twinkly_mockup.centerline import read_linestring_lonlat  # noqa: E402
from twinkly_mockup.mosaic import MosaicSidecar  # noqa: E402

DEFAULT_MANIFEST = REPO_ROOT / "configs" / "circuits.json"
TRACER_PATH = "/scripts/trace_track_limits.html"

# A centerline whose first and last vertex agree to this many degrees is a
# closed lap with a duplicated seam vertex — the f1-circuits convention. ~1e-6°
# is ~0.1 m, far below the vertex spacing and far above float noise. The
# duplicate is dropped so that vertex 0 has two distinct neighbours, which is
# what the seeding step's angle bisector needs (ADR-0003).
SEAM_TOL_DEG = 1e-6

REQUIRED_KEYS = ("name", "title", "mosaic", "sidecar", "centerline", "half_width_m", "attribution")


class ManifestError(RuntimeError):
    """The manifest, or a file it names, cannot back a circuit."""


@dataclass(frozen=True)
class Circuit:
    """One manifest entry. Paths are resolved against the manifest's directory."""

    name: str
    title: str
    mosaic: Path
    sidecar: Path
    centerline: Path
    half_width_m: float
    attribution: str


def load_manifest(manifest_path: Path = DEFAULT_MANIFEST) -> dict[str, Circuit]:
    """Parse the manifest into `{name: Circuit}`, preserving declaration order.

    Structure only — this does not touch the files an entry names, so a manifest
    still loads on a machine where the (gitignored) mosaic PNGs have not been
    built. `resolve_circuit` is where a missing file becomes an error.
    """
    manifest_path = Path(manifest_path)
    try:
        raw = json.loads(manifest_path.read_text())
    except FileNotFoundError as e:
        raise ManifestError(f"no circuit manifest at {manifest_path}") from e
    except json.JSONDecodeError as e:
        raise ManifestError(f"{manifest_path} is not valid JSON: {e}") from e

    if not isinstance(raw, dict) or not isinstance(raw.get("circuits"), list):
        raise ManifestError(f"{manifest_path} must be an object with a `circuits` array")

    base = manifest_path.parent
    circuits: dict[str, Circuit] = {}
    for i, entry in enumerate(raw["circuits"]):
        if not isinstance(entry, dict):
            raise ManifestError(f"{manifest_path} circuit #{i} is not an object")
        missing = [k for k in REQUIRED_KEYS if k not in entry]
        if missing:
            raise ManifestError(
                f"{manifest_path} circuit #{i} ({entry.get('name', 'unnamed')}) "
                f"is missing {', '.join(missing)}"
            )
        name = str(entry["name"])
        if name in circuits:
            raise ManifestError(f"{manifest_path} declares `{name}` twice")
        half_width = float(entry["half_width_m"])
        if half_width <= 0.0:
            raise ManifestError(
                f"{manifest_path} circuit `{name}` has half_width_m={half_width}; "
                f"the seed offset must be positive"
            )
        circuits[name] = Circuit(
            name=name,
            title=str(entry["title"]),
            mosaic=base / str(entry["mosaic"]),
            sidecar=base / str(entry["sidecar"]),
            centerline=base / str(entry["centerline"]),
            half_width_m=half_width,
            attribution=str(entry["attribution"]),
        )

    if not circuits:
        raise ManifestError(f"{manifest_path} declares no circuits")
    return circuits


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

    coords = [list(c) for c in read_linestring_lonlat(circuit.centerline)]
    closed = (
        abs(coords[0][0] - coords[-1][0]) < SEAM_TOL_DEG
        and abs(coords[0][1] - coords[-1][1]) < SEAM_TOL_DEG
    )
    if closed:
        coords.pop()

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
        "centerline": coords,
        "centerline_closed": closed,
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
            self._api_circuit(route[len("/api/circuits/") :])
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

    def _api_circuit(self, name: str) -> None:
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
            payload = resolve_circuit(circuit)
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
