"""Bundle local role SVGs for vector recolouring in the browser."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FAMILIES = ("nato", "vanilla", "pact", "aircraft")


def main() -> int:
    payload = {}
    for family in FAMILIES:
        folder = ROOT / "images" / "icons" / family
        payload[family] = {
            path.name: path.read_text(encoding="utf-8")
            for path in sorted(folder.glob("*.svg"))
        }
    output = ROOT / "icon_templates.json"
    output.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {sum(len(items) for items in payload.values())} SVG templates to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
