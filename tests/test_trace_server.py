"""The circuit manifest and the server that boots the tracer from it (ADR-0003).

The manifest is the single declaration of a circuit, so the thing worth pinning
is that it *stays* the single declaration: a circuit resolves from files on
disk, an undeclared one fails loudly, and the page carries no circuit of its
own to fall back to. The last of those is checked against the HTML directly —
the literal that used to live there was invisible until you noticed you had
been tracing Monaco while asking for Silverstone.

The frame maths is not retested here; `tests/test_frame_registration.py` and
`tests/js/frame.test.js` own it. What matters at this boundary is that the
sidecar values the page receives are the ones `configs/` ships.
"""

from __future__ import annotations

import http.client
import importlib.util
import json
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from twinkly_mockup.mosaic import MosaicSidecar

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
CONFIGS = REPO / "configs"
MANIFEST = CONFIGS / "circuits.json"
TRACER_HTML = SCRIPTS / "trace_track_limits.html"


def _load_trace_module():
    # Imported as `trace_app`, not `trace`: the stdlib owns that name and the
    # dataclasses in here resolve their annotations through `sys.modules`.
    spec = importlib.util.spec_from_file_location("trace_app", SCRIPTS / "trace.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


trace = _load_trace_module()

SHIPPED = ["monaco", "silverstone"]


# ---------------------------------------------------------------- the manifest


def test_manifest_declares_the_shipped_circuits() -> None:
    assert list(trace.load_manifest(MANIFEST)) == SHIPPED


@pytest.mark.parametrize("name", SHIPPED)
def test_every_entry_binds_files_that_exist(name: str) -> None:
    """A manifest entry is a promise about four files; three must be on disk.

    The mosaic PNG is the exception — it is gitignored build output, so it is
    reported rather than required.
    """
    circuit = trace.load_manifest(MANIFEST)[name]
    assert circuit.sidecar.exists(), circuit.sidecar
    assert circuit.centerline.exists(), circuit.centerline
    assert circuit.mosaic.name.endswith(".png")
    assert circuit.half_width_m > 0.0


@pytest.mark.parametrize("name", SHIPPED)
def test_resolved_sidecar_is_the_one_configs_ships(name: str) -> None:
    """The page must be handed the shipped registration, not a copy of it."""
    circuit = trace.load_manifest(MANIFEST)[name]
    resolved = trace.resolve_circuit(circuit)
    shipped = MosaicSidecar.model_validate(yaml.safe_load(circuit.sidecar.read_text()))
    assert resolved["sidecar"] == {
        "origin_lat": shipped.origin_lat,
        "origin_lon": shipped.origin_lon,
        "origin_px": list(shipped.origin_px),
        "m_per_px": shipped.m_per_px,
        "north_angle_deg": shipped.north_angle_deg,
        "utm_epsg": shipped.utm_epsg,
        "grid_scale": shipped.grid_scale,
    }


@pytest.mark.parametrize("name", SHIPPED)
def test_closed_laps_lose_their_duplicated_seam_vertex(name: str) -> None:
    """f1-circuits repeats vertex 0 at the end; the seeder needs it gone.

    An angle-bisector normal at a vertex whose two neighbours are the same point
    is undefined, which is exactly what a duplicated seam produces at index 0.
    """
    circuit = trace.load_manifest(MANIFEST)[name]
    raw = json.loads(circuit.centerline.read_text())
    source = raw["features"][0]["geometry"]["coordinates"]
    assert source[0] == source[-1], "fixture assumption: the source lap is closed"

    resolved = trace.resolve_circuit(circuit)
    assert resolved["centerline_closed"] is True
    assert len(resolved["centerline"]) == len(source) - 1
    assert resolved["centerline"][0] == list(source[0])
    assert resolved["centerline"][-1] != resolved["centerline"][0]


@pytest.mark.parametrize("name", SHIPPED)
def test_every_entry_credits_bacinger(name: str) -> None:
    """ADR-0003's first attribution site: the manifest entry itself."""
    circuit = trace.load_manifest(MANIFEST)[name]
    assert "Bacinger" in circuit.attribution
    assert "MIT" in circuit.attribution
    # The attribution has to name *which* upstream file, or it credits nothing
    # in particular once the geometry is separated from the manifest.
    assert ".geojson" in circuit.attribution


def test_adding_a_circuit_is_a_manifest_edit_only(tmp_path: Path) -> None:
    """A circuit the code has never heard of must resolve like any other."""
    manifest = tmp_path / "circuits.json"
    manifest.write_text(
        json.dumps(
            {
                "circuits": [
                    {
                        "name": "imola",
                        "title": "Autodromo Enzo e Dino Ferrari",
                        "mosaic": "imola_mosaic.png",
                        "sidecar": "monaco_mosaic.yaml",
                        "centerline": "monaco_centerline.geojson",
                        "half_width_m": 6.0,
                        "attribution": "Centerline © Tomislav Bacinger, MIT — it-1953.geojson",
                    }
                ]
            }
        )
    )
    # Point the borrowed filenames at the real configs directory.
    (tmp_path / "monaco_mosaic.yaml").write_text((CONFIGS / "monaco_mosaic.yaml").read_text())
    (tmp_path / "monaco_centerline.geojson").write_text(
        (CONFIGS / "monaco_centerline.geojson").read_text()
    )

    circuits = trace.load_manifest(manifest)
    assert list(circuits) == ["imola"]
    resolved = trace.resolve_circuit(circuits["imola"])
    assert resolved["title"] == "Autodromo Enzo e Dino Ferrari"
    assert resolved["half_width_m"] == 6.0
    assert resolved["mosaic_present"] is False
    assert resolved["mosaic_hint"] == "scripts/build_imola_mosaic.sh"


# ------------------------------------------------------------------ refusals


@pytest.mark.parametrize(
    ("body", "match"),
    [
        ("not json at all", "not valid JSON"),
        ('{"circuits": {}}', "`circuits` array"),
        ('{"circuits": []}', "declares no circuits"),
        ('{"circuits": ["monaco"]}', "not an object"),
        ('{"circuits": [{"name": "a", "title": "A"}]}', "is missing mosaic"),
        (
            '{"circuits": [{"name": "a", "title": "A", "mosaic": "m.png", "sidecar": "s.yaml",'
            ' "centerline": "c.geojson", "half_width_m": 0, "attribution": "x"}]}',
            "must be positive",
        ),
    ],
)
def test_a_broken_manifest_is_refused_with_its_reason(
    tmp_path: Path, body: str, match: str
) -> None:
    manifest = tmp_path / "circuits.json"
    manifest.write_text(body)
    with pytest.raises(trace.ManifestError, match=match):
        trace.load_manifest(manifest)


def test_a_circuit_declared_twice_is_refused(tmp_path: Path) -> None:
    """Silent last-wins would make one of the two entries a lie."""
    entry = {
        "name": "monaco",
        "title": "A",
        "mosaic": "m.png",
        "sidecar": "s.yaml",
        "centerline": "c.geojson",
        "half_width_m": 5.0,
        "attribution": "x",
    }
    manifest = tmp_path / "circuits.json"
    manifest.write_text(json.dumps({"circuits": [entry, entry]}))
    with pytest.raises(trace.ManifestError, match="twice"):
        trace.load_manifest(manifest)


def test_a_missing_manifest_names_the_path_it_looked_for(tmp_path: Path) -> None:
    with pytest.raises(trace.ManifestError, match="no circuit manifest at"):
        trace.load_manifest(tmp_path / "nope.json")


@pytest.mark.parametrize("field", ["sidecar", "centerline"])
def test_a_dangling_path_names_the_file_and_the_circuit(tmp_path: Path, field: str) -> None:
    entry = {
        "name": "ghost",
        "title": "Ghost",
        "mosaic": "m.png",
        "sidecar": "monaco_mosaic.yaml",
        "centerline": "monaco_centerline.geojson",
        "half_width_m": 5.0,
        "attribution": "x",
    }
    entry[field] = "absent" + Path(entry[field]).suffix
    for keep in ("sidecar", "centerline"):
        if keep != field:
            (tmp_path / entry[keep]).write_text((CONFIGS / entry[keep]).read_text())
    manifest = tmp_path / "circuits.json"
    manifest.write_text(json.dumps({"circuits": [entry]}))

    circuit = trace.load_manifest(manifest)["ghost"]
    with pytest.raises(trace.ManifestError, match=rf"ghost.*no {field}"):
        trace.resolve_circuit(circuit)


# -------------------------------------------------------------- the served app


@pytest.fixture(scope="module")
def server() -> Iterator[int]:
    """The real server on a free port, serving the real repo."""
    srv = trace.make_server("127.0.0.1", 0, manifest_path=MANIFEST)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv.server_address[1]
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


def get(port: int, path: str) -> tuple[int, dict[str, str], bytes]:
    """GET one path. Header names are lowercased — `http.server` sends its own
    in mixed case (`Content-type`) and ours in another (`Content-Type`)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("GET", path)
        res = conn.getresponse()
        return res.status, {k.lower(): v for k, v in res.getheaders()}, res.read()
    finally:
        conn.close()


def test_root_redirects_to_the_tracer(server: int) -> None:
    status, headers, _ = get(server, "/")
    assert status == 302
    assert headers["location"] == trace.TRACER_PATH


def test_the_tracer_and_its_module_import_are_both_served(server: int) -> None:
    """The page is an ES module import away from being blank; serve both halves."""
    status, _, body = get(server, trace.TRACER_PATH)
    assert status == 200
    assert b"<title>Track-limit tracer</title>" in body

    status, headers, body = get(server, "/scripts/tracer/frame.js")
    assert status == 200
    # A module import is rejected outright on a non-JavaScript MIME type, so
    # this is load-bearing rather than cosmetic.
    assert "javascript" in headers["content-type"]
    assert b"export function createFrame" in body


def test_the_index_lists_every_declared_circuit(server: int) -> None:
    status, _, body = get(server, "/api/circuits")
    assert status == 200
    payload = json.loads(body)
    assert [c["name"] for c in payload["circuits"]] == SHIPPED
    assert payload["manifest"] == "configs/circuits.json"
    for entry in payload["circuits"]:
        assert "Bacinger" in entry["attribution"]


@pytest.mark.parametrize("name", SHIPPED)
def test_a_declared_circuit_serves_its_boot_document(server: int, name: str) -> None:
    status, _, body = get(server, f"/api/circuits/{name}")
    assert status == 200
    payload = json.loads(body)
    assert payload == trace.resolve_circuit(trace.load_manifest(MANIFEST)[name])
    assert payload["mosaic_url"] == f"/configs/{name}_mosaic.png"


@pytest.mark.parametrize("name", SHIPPED)
def test_a_declared_circuit_serves_a_seed_for_both_edges(server: int, name: str) -> None:
    """The geometry is `test_seed.py`'s; what matters here is that it arrives."""
    status, _, body = get(server, f"/api/circuits/{name}/seed")
    assert status == 200
    seed = json.loads(body)

    boot = trace.resolve_circuit(trace.load_manifest(MANIFEST)[name])
    assert len(seed["left"]) == len(boot["centerline"])
    assert len(seed["right"]) == len(boot["centerline"])
    assert seed["half_width_m"] == boot["half_width_m"]
    assert seed["closed"] is boot["centerline_closed"]
    # lon/lat, not pixels — the working store and the export both speak it.
    for lon, lat in seed["left"] + seed["right"]:
        assert -180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0


def test_seeding_an_undeclared_circuit_404s_like_every_other_endpoint(server: int) -> None:
    status, _, body = get(server, "/api/circuits/spa/seed")
    assert status == 404
    assert "not declared" in json.loads(body)["error"]


def test_an_unknown_sub_endpoint_does_not_fall_through_to_the_boot_document(
    server: int,
) -> None:
    """`/monaco/typo` must not quietly answer as if it were `/monaco`."""
    status, _, body = get(server, "/api/circuits/monaco/typo")
    assert status == 404
    assert "no such endpoint" in json.loads(body)["error"]


def test_an_undeclared_circuit_404s_and_says_what_is_declared(server: int) -> None:
    """The criterion this file exists for: an explicit failure, never a fallback."""
    status, _, body = get(server, "/api/circuits/spa")
    assert status == 404
    error = json.loads(body)["error"]
    assert "spa" in error
    assert "configs/circuits.json" in error
    assert "monaco" in error and "silverstone" in error


def test_the_api_does_not_fall_through_to_static_files(server: int) -> None:
    status, _, body = get(server, "/api/nonsense")
    assert status == 404
    assert "no such endpoint" in json.loads(body)["error"]


def test_paths_above_the_repo_root_are_not_served(server: int) -> None:
    status, _, _ = get(server, "/../../../../etc/passwd")
    assert status == 404


# ---------------------------------------------------------- the page's own copy


def test_the_page_carries_no_circuit_of_its_own() -> None:
    """No hardcoded Monaco, no generated blob loader, no fallback.

    These are string checks because the failure they guard is a *reappearance*:
    the page booting from something other than the manifest, which looks
    completely normal until the geometry is wrong.
    """
    html = TRACER_HTML.read_text()
    assert "_tracer_data.js" not in html
    assert "43.739404" not in html, "a Monaco centerline vertex is back in the page"
    assert "43.736871113" not in html, "Monaco's sidecar origin is back in the page"
    assert "falling back to Monaco" not in html
    assert "/api/circuits" in html
