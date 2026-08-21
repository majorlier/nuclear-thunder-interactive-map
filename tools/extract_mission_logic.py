import argparse
import json
import math
import os
import re


def matrix_position(matrix):
    return [float(value) for value in matrix[3][:3]]


def matrix_radius(matrix):
    return math.sqrt(sum(float(value) ** 2 for value in matrix[0][:3]))


def contains_target_tag(value):
    if isinstance(value, dict):
        if value.get("nuclear_escalation_target__tag") == "mlrs_tbm_target":
            return True
        return any(contains_target_tag(child) for child in value.values())
    if isinstance(value, list):
        return any(contains_target_tag(child) for child in value)
    return False


def site_category(unit):
    unit_class = unit.get("unit_class", "")
    if unit_class == "nt_fuel_factory_foundation":
        return "fuel_factory"
    if unit_class == "nt_ammo_factory_foundation":
        return "ammo_factory"
    if unit_class == "nt_assembly_area_foundation":
        return "assembly_area"
    if unit_class == "nt_stronghold":
        return "strongpoint"
    if unit_class.startswith("dynaf_"):
        return "airfield"
    return None


def nearest_links(factories, assemblies, count=3):
    links = []
    for factory in factories:
        candidates = [
            assembly
            for assembly in assemblies
            if assembly["team"] == factory["team"]
        ]
        candidates.sort(
            key=lambda assembly: math.dist(
                (factory["world_pos"][0], factory["world_pos"][2]),
                (assembly["world_pos"][0], assembly["world_pos"][2]),
            )
        )
        for rank, assembly in enumerate(candidates[:count], start=1):
            links.append(
                {
                    "factory": factory["name"],
                    "assembly": assembly["name"],
                    "team": factory["team"],
                    "resource": factory["category"].removesuffix("_factory"),
                    "rank_by_distance": rank,
                    "distance": round(
                        math.dist(
                            (factory["world_pos"][0], factory["world_pos"][2]),
                            (assembly["world_pos"][0], assembly["world_pos"][2]),
                        ),
                        1,
                    ),
                    "from": factory["world_pos"],
                    "to": assembly["world_pos"],
                }
            )
    return links


def nearest_armored_links(assemblies, sites, count=3):
    """Initial tank-column candidates from the mission's three-nearest rule.

    The runtime can also target a friendly strongpoint after all of its spawned
    defenders are gone. That state is not knowable from the static mission, so
    this overlay deliberately represents the initially eligible enemy sites.
    """
    links = []
    eligible_categories = {"assembly_area", "strongpoint", "airfield"}
    for assembly in assemblies:
        candidates = [
            site
            for site in sites
            if (
                site["team"] != assembly["team"]
                and site["category"] in eligible_categories
            )
        ]
        candidates.sort(
            key=lambda site: math.dist(
                (assembly["world_pos"][0], assembly["world_pos"][2]),
                (site["world_pos"][0], site["world_pos"][2]),
            )
        )
        for rank, target in enumerate(candidates[:count], start=1):
            links.append(
                {
                    "assembly": assembly["name"],
                    "target": target["name"],
                    "team": assembly["team"],
                    "target_team": target["team"],
                    "target_category": target["category"],
                    "rank_by_distance": rank,
                    "distance": round(
                        math.dist(
                            (
                                assembly["world_pos"][0],
                                assembly["world_pos"][2],
                            ),
                            (target["world_pos"][0], target["world_pos"][2]),
                        ),
                        1,
                    ),
                    "from": assembly["world_pos"],
                    "to": target["world_pos"],
                }
            )
    return links


def main():
    parser = argparse.ArgumentParser(
        description="Extract Nuclear Escalation logistics and mobile-fire overlays"
    )
    parser.add_argument("mission", help="Path to nuclear_escalation_tdm.blkx")
    parser.add_argument(
        "--output",
        default="mission_logic.json",
        help="Output JSON path (default: mission_logic.json)",
    )
    args = parser.parse_args()

    with open(args.mission, "r", encoding="utf-8") as source:
        mission = json.load(source)

    object_groups = mission.get("units", {}).get("objectGroups", [])
    sites = []
    mlrs_tbm_targets = []
    for unit in object_groups:
        category = site_category(unit)
        if not category:
            continue
        site = {
            "name": unit["name"],
            "unit_class": unit["unit_class"],
            "team": unit.get("props", {}).get("army"),
            "category": category,
            "world_pos": matrix_position(unit["tm"]),
        }
        sites.append(site)
        if contains_target_tag(unit.get("additionalEcsTemplates", {})):
            mlrs_tbm_targets.append(site)

    area_definitions = mission.get("areas", {})
    tank_models = mission.get("units", {}).get("tankModels", [])
    mobile_fire_units = []
    for unit in tank_models:
        name = unit.get("name", "")
        if "_mlrs_" in name:
            unit_type = "mlrs"
            spawn_area_name = name.replace("_mlrs_", "_mlrs_spawn_area_")
        elif "_tactical_missile_launcher_" in name:
            unit_type = "tbm"
            spawn_area_name = name.replace(
                "_tactical_missile_launcher_", "_tactical_missile_spawn_area_"
            )
        else:
            continue

        spawn_area = area_definitions.get(spawn_area_name)
        move_areas = []
        for index in range(1, 10):
            area_name = f"{name}__move_area_0{index}"
            area = area_definitions.get(area_name)
            if not area:
                break
            move_areas.append(
                {
                    "name": area_name,
                    "center": matrix_position(area["tm"]),
                    "radius": round(matrix_radius(area["tm"]), 2),
                }
            )

        mobile_fire_units.append(
            {
                "name": name,
                "unit_class": unit["unit_class"],
                "team": unit.get("props", {}).get("army"),
                "unit_type": unit_type,
                "spawn_area": (
                    {
                        "name": spawn_area_name,
                        "center": matrix_position(spawn_area["tm"]),
                        "radius": round(matrix_radius(spawn_area["tm"]), 2),
                    }
                    if spawn_area
                    else None
                ),
                "move_areas": move_areas,
            }
        )

    bomber_spawns = []
    bomber_pattern = re.compile(r"^t([12])_nuclear_strategic_bomber_spawn_(\d+)$")
    for area_name, area in area_definitions.items():
        match = bomber_pattern.match(area_name)
        if not match or not isinstance(area, dict) or "tm" not in area:
            continue
        team = int(match.group(1))
        bomber_spawns.append(
            {
                "name": area_name,
                "team": team,
                "aircraft": "tu_95m" if team == 1 else "b_52h",
                "world_pos": matrix_position(area["tm"]),
            }
        )

    factories = [
        site
        for site in sites
        if site["category"] in {"fuel_factory", "ammo_factory"}
    ]
    assemblies = [site for site in sites if site["category"] == "assembly_area"]

    capture_radii = {
        "fuel_factory": 40.0,
        "ammo_factory": 40.0,
        "assembly_area": 40.0,
        "strongpoint": 100.0,
        "airfield": 50.0,
    }
    for site in sites:
        site["capture_radius"] = capture_radii[site["category"]]

    output = {
        "sites": sites,
        "likely_convoy_links": nearest_links(factories, assemblies),
        "likely_armored_links": nearest_armored_links(assemblies, sites),
        "mobile_fire_units": mobile_fire_units,
        "bomber_spawns": bomber_spawns,
        "mlrs_tbm_targets": mlrs_tbm_targets,
        "rules": {
            "resource_factory": {
                "capacity": 1200,
                "initial_fraction": [0.2, 0.4],
                "production_per_second": 2,
                "dispatch_threshold": 600,
                "column_vehicle_count": 5,
                "resource_per_vehicle": 120,
                "delivery_radius": 20,
                "destination_choice": "random among top 3 by free_capacity / distance",
                "travel_speed": 20,
            },
            "tank_factory": {
                "spawn_threshold": 500,
                "initial_fraction": [0.5, 0.8],
                "base_production_per_second": 0.3,
                "fuel_per_second": 1,
                "ammo_per_second": 1,
                "spg_probability": 0.15,
                "target_choice": "random among 3 nearest eligible targets",
                "travel_speed": 20,
            },
            "mobile_fire": {
                "destination": "center of a randomly chosen non-repeating move area",
                "travel_speed": 20,
                "rearm_seconds": 60,
                "target_choice": "nearest living enemy with mlrs_tbm_target tag",
            },
        },
    }

    output_path = os.path.abspath(args.output)
    with open(output_path, "w", encoding="utf-8") as destination:
        json.dump(output, destination, separators=(",", ":"))

    print(
        f"Saved {len(sites)} logistics/capture sites, "
        f"{len(mobile_fire_units)} MLRS/TBM units, "
        f"{len(bomber_spawns)} strategic-bomber spawns, "
        f"{len(mlrs_tbm_targets)} tagged targets, and "
        f"{len(output['likely_convoy_links'])} likely convoy links plus "
        f"{len(output['likely_armored_links'])} likely armored-column links "
        f"to {output_path}"
    )


if __name__ == "__main__":
    main()
