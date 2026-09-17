"""Refresh existing unit_specs.json fields from a local converted datamine.

This is useful when the mission data is already current but a local datamine
clone has newer unit metadata (for example ``type``/``onRadarAs`` used by HUD
icon selection). It preserves entries whose source BLKX is unavailable.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from extract_unit_specs import LocalSource, build_spec


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aces-root", required=True, type=Path)
    parser.add_argument("--input", type=Path, default=ROOT / "unit_specs.json")
    parser.add_argument("--output", type=Path, default=ROOT / "unit_specs.json")
    args = parser.parse_args()

    specs = json.loads(args.input.read_text(encoding="utf-8-sig"))
    source = LocalSource(args.aces_root)
    refreshed = 0
    missing = []
    result = {}
    for key, old_spec in specs.items():
        unit_name = str(old_spec.get("unit_class") or key)
        path = source.find_unit(unit_name)
        if path:
            result[key] = build_spec(unit_name, path, source)
            refreshed += 1
        else:
            result[key] = old_spec
            missing.append(unit_name)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Refreshed {refreshed} of {len(specs)} unit specifications")
    if missing:
        print("Local BLKX missing for:", ", ".join(sorted(missing)))
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
