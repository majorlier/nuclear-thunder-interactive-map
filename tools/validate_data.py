"""Run inexpensive consistency checks over the map's generated data files."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as source:
        return json.load(source)


def valid_position(value) -> bool:
    return (
        isinstance(value, list)
        and len(value) >= 3
        and all(isinstance(number, (int, float)) for number in value[:3])
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Nuclear Thunder map data")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    errors: list[str] = []
    warnings: list[str] = []

    map_data = load_json(root / "map_data.json")
    mission_logic = load_json(root / "mission_logic.json")
    road_network = load_json(root / "road_network.json")
    unit_specs = load_json(root / "unit_specs.json")
    html = (root / "index.html").read_text(encoding="utf-8")

    if not isinstance(map_data, list) or not map_data:
        errors.append("map_data.json must contain a non-empty list")
        map_data = []

    names: set[str] = set()
    eras: set[str] = set()
    units_by_era: dict[str, set[str]] = {}
    for index, site in enumerate(map_data):
        name = site.get("name")
        if not name:
            errors.append(f"map_data[{index}] has no name")
        elif name in names:
            errors.append(f"duplicate map object name: {name}")
        else:
            names.add(name)
        if site.get("team") not in (1, 2):
            errors.append(f"{name or index} has invalid team")
        if not valid_position(site.get("world_pos")):
            errors.append(f"{name or index} has invalid world_pos")
        era_units = site.get("units_by_era", {})
        era_buildings = site.get("buildings_by_era", {})
        if set(era_units) != set(era_buildings):
            errors.append(f"{name or index} has mismatched unit/building eras")
        eras.update(era_units)
        for era, units in era_units.items():
            unit_set = units_by_era.setdefault(era, set())
            for unit in units:
                if not valid_position(unit.get("world_pos")):
                    errors.append(f"{name or index}/{era} contains an invalid unit position")
                unit_name = unit.get("name") or unit.get("unit_class")
                if unit_name:
                    unit_set.add(unit_name.lower())

    ui_eras = set(
        re.findall(r'<input[^>]+name="era"[^>]+value="([^"]+)"', html)
    )
    if eras != ui_eras:
        errors.append(
            f"era mismatch: data={sorted(eras)}, interface={sorted(ui_eras)}"
        )

    mission_sites = mission_logic.get("sites", [])
    for site in mission_sites:
        if not valid_position(site.get("world_pos")):
            errors.append(f"mission site {site.get('name', '?')} has invalid position")

    if isinstance(road_network, list):
        roads = road_network
    elif isinstance(road_network, dict):
        roads = road_network.get("roads", [])
    else:
        roads = []
    if not isinstance(roads, list) or not roads:
        errors.append("road_network.json contains no roads")

    known_specs = {name.lower() for name in unit_specs}
    for era, unit_classes in sorted(units_by_era.items()):
        missing = sorted(unit_classes - known_specs)
        if missing:
            warnings.append(
                f"{era}: {len(missing)} unit classes have no optional detail card: "
                + ", ".join(missing)
            )

    for warning in warnings:
        print("WARNING:", warning)
    for error in errors:
        print("ERROR:", error)
    print(
        f"Checked {len(map_data)} map objects, {len(mission_sites)} mission sites, "
        f"{len(roads) if isinstance(roads, list) else 0} roads, and {len(unit_specs)} unit specs."
    )
    if errors:
        print(f"Validation failed with {len(errors)} error(s).")
        return 1
    print("Validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
