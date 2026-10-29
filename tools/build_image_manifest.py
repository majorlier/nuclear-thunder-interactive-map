"""Write images/manifest.json: the slot and portrait images the map ships.

Cards and hover tooltips build image candidates from unit and model names.
Many event-only units (AI SAM launchers and radars, carriers, convoy trucks)
have no art in the game, so without this list the browser requests files
that do not exist. ``tools/sync_datamine_assets.py`` rewrites the list after
copying art, and ``tools/check_frontend.js`` fails when it is out of date.

    python tools/build_image_manifest.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDERS = {"slots": "images/slots", "portraits": "images/portraits"}
MANIFEST = "images/manifest.json"


def build_manifest(root: Path = ROOT) -> dict[str, list[str]]:
    return {
        key: sorted(path.name for path in (root / folder).glob("*.png"))
        for key, folder in FOLDERS.items()
    }


def write_manifest(root: Path = ROOT) -> Path:
    path = root / MANIFEST
    path.write_text(json.dumps(build_manifest(root), indent=2) + "\n", encoding="utf-8")
    return path


def main() -> int:
    path = write_manifest()
    manifest = json.loads(path.read_text(encoding="utf-8"))
    counts = ", ".join(f"{len(names)} {key}" for key, names in manifest.items())
    print(f"Wrote {path} ({counts})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
