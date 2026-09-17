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


def deep_merge(base, overlay):
    """Merge BLKX template dictionaries, preserving inherited arrays."""
    result = dict(base or {})
    for key, value in (overlay or {}).items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def resolve_template(name, templates, cache=None, stack=None):
    cache = cache if cache is not None else {}
    stack = stack if stack is not None else set()
    if name in cache:
        return cache[name]
    if name in stack or name not in templates:
        return templates.get(name, {})
    stack.add(name)
    raw = templates[name]
    uses = raw.get("_use", []) if isinstance(raw, dict) else []
    if isinstance(uses, str):
        uses = [uses]
    merged = {}
    for parent in uses:
        merged = deep_merge(merged, resolve_template(parent, templates, cache, stack))
    merged = deep_merge(merged, raw)
    stack.remove(name)
    cache[name] = merged
    return merged


def unit_entries(template, key):
    group = template.get("_group", {}) if isinstance(template, dict) else {}
    container = group.get(key, {})
    if isinstance(container, dict):
        entries = container.get("unit:object", [])
    else:
        entries = container
    if isinstance(entries, dict):
        entries = [entries]
    return entries if isinstance(entries, list) else []


def compact_convoy_entries(entries):
    result = []
    spaa_tokens = (
        "zsu", "zprk", "9a33", "flarak", "m163", "vulcan", "crotale",
        "gepard", "otomatic", "chaparral", "shilka"
    )
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        count = entry.get("count", 1)
        if isinstance(count, list):
            count = max((int(value) for value in count if str(value).isdigit()), default=1)
        try:
            count = int(count)
        except (TypeError, ValueError):
            count = 1
        result.append({
            "name": entry["name"],
            "count": count,
            "role": (
                "SPAA escort"
                if (
                    "spaa" in str(entry.get("templateName", "")).lower()
                    or any(token in str(entry["name"]).lower() for token in spaa_tokens)
                )
                else "vehicle"
            ),
        })
    return result


def extract_convoy_compositions(templates_dir):
    """Extract resource and armoured-column orders from reusable templates."""
    if not templates_dir:
        return {}
    templates_dir = os.path.abspath(templates_dir)
    all_templates = {}
    for filename in ("factory_equip.blkx", "factory_tank.blkx"):
        path = os.path.join(templates_dir, filename)
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as source:
            document = json.load(source)
        all_templates.update(
            {name: value for name, value in document.items() if isinstance(value, dict)}
        )
    if not all_templates:
        return {}

    cache = {}
    eras = ("1970_early", "1970_late", "1980", "2018")
    output = {"resource": {}, "armored": {}}
    for era in eras:
        fuel_name = f"fuel_factory_{era}"
        ammo_name = f"ammo_factory_{era}"
        resource = {}
        for resource_name, template_name in (("fuel", fuel_name), ("ammo", ammo_name)):
            template = resolve_template(template_name, all_templates, cache)
            resource[resource_name] = {
                "redfor": compact_convoy_entries(
                    unit_entries(template, "equip_factory__columnUnitsSettings1:array")
                ),
                "blufor": compact_convoy_entries(
                    unit_entries(template, "equip_factory__columnUnitsSettings2:array")
                ),
            }
        if any(resource[item][side] for item in resource for side in ("redfor", "blufor")):
            output["resource"][era] = resource

        template = resolve_template(f"tank_factory_{era}", all_templates, cache)
        if template:
            output["armored"][era] = {
                "standard": {
                    "redfor": compact_convoy_entries(
                        unit_entries(template, "equip_factory__columnUnitsSettings1:array")
                    ),
                    "blufor": compact_convoy_entries(
                        unit_entries(template, "equip_factory__columnUnitsSettings2:array")
                    ),
                },
                "spg": {
                    "redfor": compact_convoy_entries(
                        unit_entries(template, "tank_factory__spgColumnUnitsSettings1:array")
                    ),
                    "blufor": compact_convoy_entries(
                        unit_entries(template, "tank_factory__spgColumnUnitsSettings2:array")
                    ),
                },
                "spg_probability": template.get("_group", {}).get(
                    "tank_factory__spgColummProbability", 0.15
                ),
            }
    return output


def extract_template_restore_metadata(templates_dir):
    """Describe timer-based restoration inherited by reusable unit templates.

    The mission gives explicit timers to MLRS, TBMs and long-range radar
    squads. Other spawned defenders can inherit the shared restore component.
    The component itself lives in the generic units template, so it must be
    fetched and locked with the event templates instead of being guessed in
    the browser.
    """
    if not templates_dir:
        return {
            "templates": [], "components": [], "restore_seconds": None,
            "depot_repair_seconds": None,
        }
    templates_dir = os.path.abspath(templates_dir)
    all_templates = {}
    for filename in (
        "common.blkx", "factory_equip.blkx", "factory_tank.blkx",
        "sam_sites.blkx", "units.blkx",
    ):
        path = os.path.join(templates_dir, filename)
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as source:
            document = json.load(source)
        all_templates.update(
            {name: value for name, value in document.items() if isinstance(value, dict)}
        )
    if not all_templates:
        return {
            "templates": [], "components": [], "restore_seconds": None,
            "depot_repair_seconds": None,
        }

    cache = {}
    restoring_components = ["restore_unit_by_timer"] if "restore_unit_by_timer" in all_templates else []

    def inherits_restore(name, stack=None):
        stack = set() if stack is None else stack
        if name in stack or name not in all_templates:
            return False
        stack.add(name)
        raw = all_templates[name]
        uses = raw.get("_use", []) if isinstance(raw, dict) else []
        if isinstance(uses, str):
            uses = [uses]
        result = "restore_unit_by_timer" in uses or any(
            inherits_restore(parent, stack) for parent in uses
        )
        stack.remove(name)
        return result

    # Record templates that contain the restore component directly or through
    # a parent.  Unit template strings often look like ``tank+template``;
    # the UI checks each component against this list.
    restoring_templates = []
    for name in all_templates:
        if inherits_restore(name):
            restoring_templates.append(name)

    depot_repair_seconds = None
    equip_storage = resolve_template("equip_storage", all_templates, cache)
    if isinstance(equip_storage, dict):
        value = equip_storage.get("equip_storage__repairTime")
        try:
            depot_repair_seconds = float(value) if value is not None else None
        except (TypeError, ValueError):
            depot_repair_seconds = None
    restore_seconds = None
    restore_component = resolve_template("restore_unit_by_timer", all_templates, cache)
    if isinstance(restore_component, dict):
        value = restore_component.get("restore_unit_by_timer__restoreTime")
        try:
            restore_seconds = float(value) if value is not None else None
        except (TypeError, ValueError):
            restore_seconds = None
    # The converted generic template has appeared both as a top-level block
    # and wrapped in a document node across datamine revisions. Keep the
    # source value authoritative in either representation.
    if restore_seconds is None:
        units_path = os.path.join(templates_dir, "units.blkx")
        if os.path.exists(units_path):
            units_text = open(units_path, "r", encoding="utf-8", errors="ignore").read()
            match = re.search(
                r'"restore_unit_by_timer__restoreTime"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
                units_text,
            )
            if match:
                restore_seconds = float(match.group(1))
    return {
        "templates": sorted(restoring_templates),
        "components": sorted(restoring_components),
        "restore_seconds": restore_seconds,
        "depot_repair_seconds": depot_repair_seconds,
    }


def extract_runtime_constants(templates_dir):
    """Read small, displayable runtime constants from the checked source.

    DAS is intentionally not treated as BLKX. We only expose values with a
    clear static assignment, leaving state-dependent behaviour documented in
    the mission report rather than fabricating a number.
    """
    result = {"mobile_fire_rearm_seconds": None}
    if not templates_dir:
        return result
    path = os.path.join(os.path.abspath(templates_dir), "mission_custom_logic.das")
    if not os.path.exists(path):
        return result
    source = open(path, "r", encoding="utf-8", errors="ignore").read()
    match = re.search(r"\bREARM_TIME\s*=\s*([0-9]+(?:\.[0-9]+)?)", source)
    if match:
        result["mobile_fire_rearm_seconds"] = float(match.group(1))
    return result


def extract_respawn_rules(mission):
    rules = []

    def walk(value, trigger_name=""):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "unitRespawnAfterTime" and isinstance(child, list):
                    for item in child:
                        if not isinstance(item, dict) or "object_name" not in item:
                            continue
                        seconds = item.get("time_to_respawn")
                        if seconds is None:
                            continue
                        name = str(item["object_name"])
                        if "mlrs" in name:
                            system = "MLRS"
                        elif "tactical_missile" in name:
                            system = "TBM"
                        elif "longrange_radar" in name:
                            system = "long-range radar"
                        else:
                            system = "unit"
                        rules.append({
                            "object": name,
                            "system": system,
                            "seconds": float(seconds),
                            "mode": "restore" if item.get("just_restore") else "respawn",
                            "trigger": trigger_name,
                        })
                walk(child, key if key in mission.get("triggers", {}) else trigger_name)
        elif isinstance(value, list):
            for child in value:
                walk(child, trigger_name)

    walk(mission.get("triggers", {}))
    unique = {}
    for rule in rules:
        unique[(rule["object"], rule["seconds"], rule["mode"])] = rule
    return sorted(unique.values(), key=lambda rule: rule["object"])


def extract_airfield_rules(mission):
    values = {}
    for action in mission.get("triggers", {}).get("init", {}).get("actions", {}).get("varSetReal", []):
        if isinstance(action, dict) and action.get("var") and action.get("value") is not None:
            values[action["var"]] = float(action["value"])
    output = {}
    actions = mission.get("triggers", {}).get("set_up_airfields", {}).get("actions", {})
    for item in actions.get("airfieldSetProperties", []):
        if not isinstance(item, dict):
            continue
        hp_var = item.get("bombing_zone", {}).get("hp_var")
        hp = values.get(hp_var)
        for name in item.get("object", []):
            output[name] = {
                "hp": hp,
                "hp_var": hp_var,
                "enemy_surrender_on_landing": bool(item.get("enemySurrenderOnLanding")),
            }
    return output


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
    parser.add_argument(
        "--templates-dir",
        help="Directory containing reusable nuclear escalation templates",
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

    mission_settings = mission.get("mission_settings", {}).get("mission", {})
    respawn_rules = extract_respawn_rules(mission)
    airfield_rules = extract_airfield_rules(mission)
    template_restore = extract_template_restore_metadata(args.templates_dir)
    runtime_constants = extract_runtime_constants(args.templates_dir)
    static_no_respawn = [
        "SAM-site launchers and radars (tank+sam_site_unit)",
        "Airfield AA unless its selected template inherits a restore timer",
        "Strongpoint vehicles",
        "Airfields (destruction is a victory condition)",
        "Ships (no mission-level respawn timer found)",
        "Dynamically produced resource convoys and armoured columns",
    ]

    output = {
        "sites": sites,
        "likely_convoy_links": nearest_links(factories, assemblies),
        "likely_armored_links": nearest_armored_links(assemblies, sites),
        "mobile_fire_units": mobile_fire_units,
        "bomber_spawns": bomber_spawns,
        "mlrs_tbm_targets": mlrs_tbm_targets,
        "mission_settings": {
            "score_limit": mission_settings.get("scoreLimit"),
            "time_limit_seconds": mission_settings.get("timeLimit"),
            "escalation_thresholds": [
                mission_settings.get("nuclearEscalationStage1"),
                mission_settings.get("nuclearEscalationStage2"),
                mission_settings.get("nuclearEscalationStage3"),
            ],
            "allowed_unit_types": mission_settings.get("allowedUnitTypes", {}),
        },
        "airfield_rules": airfield_rules,
        "respawn_rules": respawn_rules,
        "template_restore": template_restore,
        "runtime_constants": runtime_constants,
        "no_fixed_respawn": static_no_respawn,
        "convoys": extract_convoy_compositions(args.templates_dir),
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
                "repair_seconds": template_restore.get("depot_repair_seconds"),
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
                "rearm_seconds": runtime_constants.get("mobile_fire_rearm_seconds"),
                "target_choice": "nearest living enemy with mlrs_tbm_target tag",
            },
            "respawn": {
                "description": "Mission-level timers; dynamically produced columns have no fixed respawn timer",
                "fixed": respawn_rules,
                "no_fixed_timer": static_no_respawn,
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
