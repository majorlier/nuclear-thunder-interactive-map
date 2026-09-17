"""Run inexpensive consistency checks over the map's generated data files."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
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


def same_position(left, right, tolerance=0.1) -> bool:
    return valid_position(left) and valid_position(right) and all(
        abs(float(a) - float(b)) <= tolerance for a, b in zip(left[:3], right[:3])
    )


def validate_variant(name, map_data, preset_ids, errors):
    if not isinstance(map_data, list) or not map_data:
        errors.append(f"{name} map data must contain a non-empty list")
        return {}, set()

    names = {}
    units_by_preset: dict[str, set[str]] = {}
    for index, site in enumerate(map_data):
        site_name = site.get("name")
        if not site_name:
            errors.append(f"{name}/map_data[{index}] has no name")
            continue
        if site_name in names:
            errors.append(f"{name} has duplicate map object name: {site_name}")
        names[site_name] = site
        if site.get("team") not in (1, 2):
            errors.append(f"{name}/{site_name} has invalid team")
        if not valid_position(site.get("world_pos")):
            errors.append(f"{name}/{site_name} has invalid world_pos")
        era_units = site.get("units_by_era", {})
        era_buildings = site.get("buildings_by_era", {})
        if set(era_units) != set(era_buildings):
            errors.append(f"{name}/{site_name} has mismatched unit/building presets")
        if set(era_units) != preset_ids:
            errors.append(
                f"{name}/{site_name} preset mismatch: {sorted(era_units)}"
            )
        for preset_id, units in era_units.items():
            unit_set = units_by_preset.setdefault(preset_id, set())
            for unit in units:
                if not valid_position(unit.get("world_pos")):
                    errors.append(
                        f"{name}/{site_name}/{preset_id} contains an invalid unit position"
                    )
                unit_name = unit.get("name") or unit.get("unit_class")
                if unit_name:
                    unit_set.add(unit_name.lower())
                lifecycle = unit.get("lifecycle")
                if unit.get("template") and not isinstance(lifecycle, dict):
                    errors.append(
                        f"{name}/{site_name}/{preset_id}/{unit_name or '?'} has no generated lifecycle"
                    )
                if isinstance(lifecycle, dict) and lifecycle.get("kind") not in {
                    "none", "restore", "repair_with_site", "mission_rule"
                }:
                    errors.append(
                        f"{name}/{site_name}/{preset_id}/{unit_name or '?'} has invalid lifecycle kind"
                    )
    return names, set().union(*units_by_preset.values()) if units_by_preset else set()


def git_tracked(root: Path, relative_path: str) -> bool:
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", relative_path],
        cwd=root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def validate_runtime_assets(root: Path, errors: list[str]) -> None:
    manifest_path = root / "tools" / "runtime_assets.json"
    if not manifest_path.exists():
        errors.append("tools/runtime_assets.json is missing")
        return
    required = load_json(manifest_path).get("required", [])
    if not isinstance(required, list):
        errors.append("tools/runtime_assets.json must contain a required list")
        return
    for relative_path in required:
        if not isinstance(relative_path, str) or not relative_path:
            errors.append("tools/runtime_assets.json contains an invalid path")
            continue
        path = root / relative_path
        if not path.is_file():
            errors.append(f"required runtime asset is missing: {relative_path}")
        elif not git_tracked(root, relative_path):
            errors.append(
                f"required runtime asset is not tracked by Git: {relative_path}"
            )


def validate_sensor_ranges(unit_specs, errors: list[str]) -> None:
    for unit_name, specs in unit_specs.items():
        sensors = specs.get("sensors", {}) if isinstance(specs, dict) else {}
        for kind, sensor in sensors.items():
            if not isinstance(sensor, dict):
                continue
            nominal = sensor.get("nominal")
            scopes = sensor.get("scope_ranges", [])
            contextual_max = max(
                [value for value in scopes if isinstance(value, (int, float))] + [
                    value for value in [sensor.get("maximum"), sensor.get("designation_range")]
                    if isinstance(value, (int, float))
                ],
                default=0,
            )
            if (
                isinstance(nominal, (int, float))
                and 0 < nominal < 1000
                and contextual_max >= max(10000, nominal * 20)
                and sensor.get("range_status") != "unresolved_internal"
            ):
                errors.append(
                    f"{unit_name}/{kind} exposes an implausible internal sensor range"
                )


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Nuclear Thunder map data")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    errors: list[str] = []
    warnings: list[str] = []

    presets_document = load_json(root / "presets.json")
    map_data = load_json(root / "map_data.json")
    map_data_mirror = load_json(root / "map_data_mirror.json")
    map_data_city = load_json(root / "map_data_city.json")
    mission_logic = load_json(root / "mission_logic.json")
    mission_logic_mirror = load_json(root / "mission_logic_mirror.json")
    mission_logic_city = load_json(root / "mission_logic_city.json")
    road_network = load_json(root / "road_network.json")
    unit_specs = load_json(root / "unit_specs.json")
    html = (root / "index.html").read_text(encoding="utf-8")

    validate_runtime_assets(root, errors)
    validate_sensor_ranges(unit_specs, errors)

    presets = presets_document.get("presets", []) if isinstance(presets_document, dict) else []
    preset_ids = {
        preset.get("id") for preset in presets if isinstance(preset, dict) and preset.get("id")
    }
    if not preset_ids or len(preset_ids) != len(presets):
        errors.append("presets.json must contain uniquely identified scenario presets")

    standard_sites, standard_units = validate_variant(
        "standard", map_data, preset_ids, errors
    )
    mirror_sites, mirror_units = validate_variant(
        "mirror", map_data_mirror, preset_ids, errors
    )
    city_sites, city_units = validate_variant(
        "city", map_data_city, preset_ids, errors
    )
    if set(standard_sites) != set(mirror_sites):
        errors.append("standard and mirror maps contain different site names")
    for site_name, site in standard_sites.items():
        if not site_name.startswith(("t1_", "t2_")):
            continue
        opposite = ("t2_" if site_name.startswith("t1_") else "t1_") + site_name[3:]
        mirror_site = mirror_sites.get(site_name)
        standard_opposite = standard_sites.get(opposite)
        if mirror_site and standard_opposite and not same_position(
            mirror_site.get("world_pos"), standard_opposite.get("world_pos")
        ):
            errors.append(f"mirror position does not match opposite side: {site_name}")

    for variant_name, logic in (
        ("standard", mission_logic),
        ("mirror", mission_logic_mirror),
        ("city", mission_logic_city),
    ):
        for site in logic.get("sites", []):
            if not valid_position(site.get("world_pos")):
                errors.append(f"{variant_name} mission site {site.get('name', '?')} has invalid position")

    if isinstance(road_network, list):
        roads = road_network
    elif isinstance(road_network, dict):
        roads = road_network.get("roads", [])
    else:
        roads = []
    if not isinstance(roads, list) or not roads:
        errors.append("road_network.json contains no roads")

    known_specs = {name.lower() for name in unit_specs}
    missing = sorted((standard_units | mirror_units | city_units) - known_specs)
    if missing:
        warnings.append(
            f"{len(missing)} unit classes have no optional detail card: "
            + ", ".join(missing)
        )
    missing_models = sorted(
        name for name, specs in unit_specs.items()
        if not isinstance(specs, dict) or not str(specs.get("model") or "").strip()
    )
    if missing_models:
        errors.append(
            f"{len(missing_models)} unit classes have no extracted model name: "
            + ", ".join(missing_models)
        )

    for required_fetch in (
        'fetch("map_data_mirror.json")',
        'fetch("mission_logic_mirror.json")',
        'fetch("map_data_city.json")',
        'fetch("mission_logic_city.json")',
        'fetch("presets.json")',
    ):
        if required_fetch not in html:
            errors.append(f"index.html does not load {required_fetch}")

    for warning in warnings:
        print("WARNING:", warning)
    for error in errors:
        print("ERROR:", error)
    print(
        f"Checked {len(map_data)} standard, {len(map_data_mirror)} mirrored, and {len(map_data_city)} City map objects, "
        f"{len(roads) if isinstance(roads, list) else 0} roads, and {len(unit_specs)} unit specs."
    )
    if errors:
        print(f"Validation failed with {len(errors)} error(s).")
        return 1
    print("Validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
