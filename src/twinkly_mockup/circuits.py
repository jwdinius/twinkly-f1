"""Circuit manifest: `configs/circuits.json` → `{name: Circuit}`.

The manifest is the single declaration of a circuit (CONTEXT.md, ADR-0003) —
it binds a name to its Mosaic, its Sidecar, its Centerline, and the constant
half-width the seeding step offsets by. A circuit absent from it is described
nowhere else.

This lives in the package rather than beside its first consumer because it now
has more than one: the tracer server (`scripts/trace.py`) boots circuits from
it, and the snapshot-authoring step (`scripts/derive_corner_poses.py`) resolves
a circuit's Sidecar and Centerline through it. Two scripts importing each other
would put a module named `trace` ahead of the standard library's on `sys.path`.

Structure only — loading does not touch the files an entry names, so a manifest
still loads on a machine where the (gitignored) mosaic PNGs have not been built.
Callers that need the files check for themselves.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .kml import edge_filename

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_MANIFEST = REPO_ROOT / "configs" / "circuits.json"

REQUIRED_KEYS = ("name", "title", "mosaic", "sidecar", "centerline", "half_width_m", "attribution")


class ManifestError(RuntimeError):
    """The manifest, or a file it names, cannot back a circuit."""


@dataclass(frozen=True)
class Circuit:
    """One manifest entry. Paths are resolved against the manifest's directory."""

    name: str
    title: str
    base: Path
    mosaic: Path
    sidecar: Path
    centerline: Path
    half_width_m: float
    attribution: str

    def kml_path(self, edge: str) -> Path:
        """Where this circuit's `edge` is exported to — beside the manifest."""
        return self.base / edge_filename(self.name, edge)


def load_manifest(manifest_path: Path = DEFAULT_MANIFEST) -> dict[str, Circuit]:
    """Parse the manifest into `{name: Circuit}`, preserving declaration order."""
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
            base=base,
            mosaic=base / str(entry["mosaic"]),
            sidecar=base / str(entry["sidecar"]),
            centerline=base / str(entry["centerline"]),
            half_width_m=half_width,
            attribution=str(entry["attribution"]),
        )

    if not circuits:
        raise ManifestError(f"{manifest_path} declares no circuits")
    return circuits
