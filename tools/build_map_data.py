import argparse
import glob
import json
import os
from collections import Counter


ERAS = {
    "1970": 0,
    "1980": 31,
    "2018": 37,
}

BUILDING_SLOT_TYPES = {"ammo_storage", "fuel_storage"}


def load_json(filepath):
    with open(filepath, "r", encoding="utf-8", errors="ignore") as source:
        try:
            return json.load(source)
        except json.JSONDecodeError as exc:
            print(f"[!] Skipping {os.path.basename(filepath)}: {exc}")
            return None


def is_matrix(value):
    return (
        isinstance(value, list)
        and len(value) >= 4
        and all(isinstance(row, list) and len(row) >= 3 for row in value[:4])
        and all(isinstance(number, (int, float)) for row in value[:4] for number in row[:3])
    )


def transform_local_to_world(site_tm, local_pos):
    r0, r1, r2, center = site_tm[0], site_tm[1], site_tm[2], site_tm[3]
    lx, ly, lz = local_pos
    return [
        center[0] + lx * r0[0] + ly * r1[0] + lz * r2[0],
        center[1] + lx * r0[1] + ly * r1[1] + lz * r2[1],
        center[2] + lx * r0[2] + ly * r1[2] + lz * r2[2],
    ]


def position_key(position):
    return tuple(round(float(value), 5) for value in position[:3])


def build_layout(data):
    slots = []
    seen_positions = set()

    def add_slot(slot_type, matrix, source):
        if not is_matrix(matrix):
            return
        local_pos = matrix[3][:3]
        key = position_key(local_pos)
        if key in seen_positions:
            return
        seen_positions.add(key)
        slots.append(
            {
                "slot_type": str(slot_type).lower(),
                "tm": matrix,
                "local_pos": local_pos,
                "source": source,
            }
        )

    # ECS spawn positions are authoritative. The matching #position nodes found
    # in several layouts are editor representations of these same slots.
    ecs_spawns = data.get("ecsUnitSpawnPositions", {})
    if isinstance(ecs_spawns, dict):
        for slot_type, spawn_data in ecs_spawns.items():
            if is_matrix(spawn_data):
                add_slot(slot_type, spawn_data, "ecsUnitSpawnPositions")
            elif isinstance(spawn_data, list):
                for matrix in spawn_data:
                    add_slot(slot_type, matrix, "ecsUnitSpawnPositions")

    raw_nodes = data.get("node", [])
    if isinstance(raw_nodes, dict):
        raw_nodes = [raw_nodes]
    if isinstance(raw_nodes, list):
        for node in raw_nodes:
            if not isinstance(node, dict):
                continue
            node_type = node.get("type")
            if node_type:
                add_slot(node_type, node.get("tm"), "node")

    runway = None
    airfield = data.get("airfield")
    if isinstance(airfield, dict) and "start" in airfield and "end" in airfield:
        runway = {
            "start": airfield["start"],
            "end": airfield["end"],
            "width": airfield.get("width", 80.0),
        }

    if not slots and not runway:
        return None
    return {"slots": slots, "runway": runway}


def iter_dict_items(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, child
            yield from iter_dict_items(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_dict_items(child)


class TemplateRegistry:
    def __init__(self, definitions):
        self.definitions = definitions

    def components(self, template_name):
        if not isinstance(template_name, str):
            return []
        return [part for part in template_name.split("+") if part in self.definitions]

    def closure(self, template_names):
        ordered = []
        visited = set()

        def visit(template_name):
            for component in self.components(template_name):
                if component in visited:
                    continue
                visited.add(component)
                definition = self.definitions[component]
                bases = definition.get("_use", [])
                if isinstance(bases, str):
                    bases = [bases]
                if isinstance(bases, list):
                    for base in bases:
                        visit(base)
                ordered.append((component, definition))

                # Assembly areas point at their era-specific tank-factory
                # template indirectly; that factory owns the three SPAA units.
                for key, value in iter_dict_items(definition):
                    if isinstance(value, str) and key.endswith("Templ"):
                        visit(value)

        for name in template_names:
            visit(name)
        return ordered

    def unit_settings(self, template_names, team):
        settings = []
        seen = set()
        for preset_name, definition in self.closure(template_names):
            for key, value in iter_dict_items(definition):
                is_spawn_settings = (
                    key.startswith("units_spawn_on_init__unitSettings")
                    or key == "sam_site__unitSettings:array"
                )
                if not is_spawn_settings:
                    continue
                if "Team1" in key and team != 1:
                    continue
                if "Team2" in key and team != 2:
                    continue
                if not isinstance(value, dict):
                    continue
                units = value.get("unit:object", [])
                if isinstance(units, dict):
                    units = [units]
                if not isinstance(units, list):
                    continue
                for unit in units:
                    if not isinstance(unit, dict) or not unit.get("name"):
                        continue
                    signature = json.dumps(unit, sort_keys=True)
                    if signature in seen:
                        continue
                    seen.add(signature)
                    copied = dict(unit)
                    copied["_preset"] = preset_name
                    settings.append(copied)
        return settings

    def find_values(self, template_names, wanted_keys):
        values = {}
        for _, definition in self.closure(template_names):
            for key, value in iter_dict_items(definition):
                if key in wanted_keys:
                    values[key] = value
        return values


def config_is_active(config, era_rank):
    if not isinstance(config, dict):
        return True
    rank_range = config.get("rankRange")
    if not isinstance(rank_range, list) or len(rank_range) < 2:
        return True
    return rank_range[0] <= era_rank <= rank_range[1]


def active_templates(site, era):
    era_rank = ERAS[era]
    active = []
    configs = []
    additional = site.get("additionalEcsTemplates", {})
    if not isinstance(additional, dict):
        return active, configs

    for template_name, value in additional.items():
        variants = value if isinstance(value, list) else [value]
        for variant in variants:
            if config_is_active(variant, era_rank):
                active.append(template_name)
                configs.append(variant if isinstance(variant, dict) else {})
    return active, configs


def role_for_unit(setting, slot_type):
    spawn_name = str(setting.get("spawnName", "")).lower()
    if spawn_name:
        return spawn_name
    if slot_type == "air_def":
        return "spaa"
    template_name = str(setting.get("templateName", "")).lower()
    unit_name = str(setting.get("name", "")).lower()
    if "radar" in unit_name:
        return "radar"
    if "spaa" in template_name:
        return "spaa"
    if "launcher" in unit_name:
        return "launcher"
    return "vehicle"


def expand_settings(settings):
    expanded = []
    for setting in settings:
        count = setting.get("count", 1)
        try:
            count = max(0, int(count))
        except (TypeError, ValueError):
            count = 1
        for index in range(count):
            item = dict(setting)
            item["_number"] = index + 1
            item["_count"] = count
            expanded.append(item)
    return expanded


def create_buildings(site, layout, template_names, inline_configs, registry):
    buildings = []
    used_slot_ids = set()
    site_pos = site["tm"][3][:3]

    for config in inline_configs:
        unit_class = config.get("unit__className")
        if unit_class:
            buildings.append(
                {
                    "name": unit_class,
                    "role": "building",
                    "world_pos": site_pos,
                    "local_pos": [0.0, 0.0, 0.0],
                }
            )

    if site.get("unit_class") == "nt_assembly_area_foundation":
        keys = {
            "assembly_area__tankFactoryUnitName",
            "assembly_area__fuelStorageUnitName",
            "assembly_area__ammoStorageUnitName",
        }
        names = registry.find_values(template_names, keys)
        main_name = names.get("assembly_area__tankFactoryUnitName")
        if main_name:
            buildings.append(
                {
                    "name": main_name,
                    "role": "building",
                    "world_pos": site_pos,
                    "local_pos": [0.0, 0.0, 0.0],
                }
            )

        slot_names = {
            "fuel_storage": names.get("assembly_area__fuelStorageUnitName"),
            "ammo_storage": names.get("assembly_area__ammoStorageUnitName"),
        }
        for index, slot in enumerate(layout.get("slots", [])):
            building_name = slot_names.get(slot["slot_type"])
            if not building_name:
                continue
            used_slot_ids.add(index)
            buildings.append(
                {
                    "name": building_name,
                    "role": "building",
                    "world_pos": transform_local_to_world(site["tm"], slot["local_pos"]),
                    "local_pos": slot["local_pos"],
                }
            )

    return buildings, used_slot_ids


def place_spawned_units(site, layout, settings, used_slot_ids):
    slots = layout.get("slots", [])
    available = [
        (index, slot)
        for index, slot in enumerate(slots)
        if index not in used_slot_ids and slot["slot_type"] not in BUILDING_SLOT_TYPES
    ]
    occupied = set()
    placed = []

    for setting in expand_settings(settings):
        desired = str(setting.get("spawnName", "")).lower()
        candidates = []
        if desired:
            candidates = [
                (index, slot)
                for index, slot in available
                if index not in occupied and slot["slot_type"] == desired
            ]
        else:
            candidates = [(index, slot) for index, slot in available if index not in occupied]

        slot_index = None
        slot = None
        if candidates:
            slot_index, slot = candidates[0]
            occupied.add(slot_index)

        local_pos = slot["local_pos"] if slot else [0.0, 0.0, 0.0]
        world_pos = (
            transform_local_to_world(site["tm"], local_pos)
            if slot
            else site["tm"][3][:3]
        )
        slot_type = slot["slot_type"] if slot else "origin"
        placed.append(
            {
                "name": setting["name"],
                "role": role_for_unit(setting, slot_type),
                "slot_type": slot_type,
                "preset": setting.get("_preset", ""),
                "world_pos": world_pos,
                "local_pos": local_pos,
                "number": setting.get("_number", 1),
                "count": setting.get("_count", 1),
                "threat_range": setting.get("threatSearchRad"),
                "template": setting.get("templateName", ""),
                "unplaced": bool(available) and slot is None,
            }
        )

    return placed


def direct_unit(site):
    unit_class = site.get("unit_class", "unit")
    lowered = unit_class.lower()
    if "radar" in lowered:
        role = "radar"
    elif "launcher" in lowered or "_sam_" in lowered:
        role = "launcher"
    elif site.get("_source_group") == "ships":
        role = "ship"
    else:
        role = "vehicle"
    return {
        "name": unit_class,
        "role": role,
        "slot_type": "origin",
        "preset": "mission unit",
        "world_pos": site.get("_runtime_world_pos", site["tm"][3][:3]),
        "local_pos": [0.0, 0.0, 0.0],
        "number": 1,
        "count": 1,
        "threat_range": site.get("threatSearchRad"),
        "template": "",
        "unplaced": False,
    }


def extract_route(site):
    raw_waypoints = site.get("way", {})
    if not isinstance(raw_waypoints, dict) or not raw_waypoints:
        return None

    points = []
    speeds = []
    for waypoint_name, waypoint in raw_waypoints.items():
        if not isinstance(waypoint, dict) or not is_matrix(waypoint.get("tm")):
            continue
        points.append(
            {
                "name": waypoint_name,
                "world_pos": waypoint["tm"][3][:3],
                "move_type": waypoint.get("props", {}).get("moveType"),
                "speed": waypoint.get("props", {}).get("speed"),
            }
        )
        speed = waypoint.get("props", {}).get("speed")
        if isinstance(speed, (int, float)):
            speeds.append(speed)

    if not points:
        return None
    return {
        "points": points,
        "closed": bool(site.get("closed_waypoints")),
        "spline": bool(site.get("isShipSpline")),
        "speed": speeds[0] if speeds and len(set(speeds)) == 1 else None,
    }


def main():
    parser = argparse.ArgumentParser(description="Build War Thunder Leaflet map data")
    parser.add_argument("--input", default=".", help="Directory containing BLKX files")
    parser.add_argument("--output", default="map_data.json", help="Output JSON path")
    parser.add_argument(
        "--layout-cache",
        help="Optional JSON file containing pre-extracted static template layouts",
    )
    args = parser.parse_args()

    paths = sorted(
        glob.glob(os.path.join(args.input, "*.blkx"))
        + glob.glob(os.path.join(args.input, "*.blk"))
    )
    documents = {}
    definitions = {}
    layouts = {}

    if args.layout_cache:
        cached_layouts = load_json(args.layout_cache)
        if not isinstance(cached_layouts, dict):
            raise SystemExit(f"Invalid layout cache: {args.layout_cache}")
        layouts.update(cached_layouts)

    for path in paths:
        data = load_json(path)
        if not isinstance(data, dict):
            continue
        filename = os.path.splitext(os.path.basename(path))[0]
        documents[filename] = data
        for key, value in data.items():
            if isinstance(value, dict):
                definitions[key] = value
        layout = build_layout(data)
        if layout:
            layouts[filename] = layout

    registry = TemplateRegistry(definitions)
    sites = []
    source_counts = Counter()

    # Delayed radar units use dummy editor coordinates. At mission start the
    # trigger assigns each squad member to one of the named foundation objects.
    all_mission_units = {}
    squad_members = {}
    for data in documents.values():
        units_root = data.get("units")
        if not isinstance(units_root, dict):
            continue
        for group_units in units_root.values():
            if not isinstance(group_units, list):
                continue
            for unit in group_units:
                if not isinstance(unit, dict) or not unit.get("name"):
                    continue
                all_mission_units[unit["name"]] = unit
                members = unit.get("props", {}).get("squad_members")
                if isinstance(members, list):
                    squad_members[unit["name"]] = members

    runtime_positions = {}
    for data in documents.values():
        triggers = data.get("triggers")
        if not isinstance(triggers, dict):
            continue
        for trigger in triggers.values():
            if not isinstance(trigger, dict):
                continue
            actions = trigger.get("actions", {})
            if not isinstance(actions, dict):
                continue
            spawn_actions = actions.get("unitSpawnOnObjectGroup", [])
            if isinstance(spawn_actions, dict):
                spawn_actions = [spawn_actions]
            if not isinstance(spawn_actions, list):
                continue
            for action in spawn_actions:
                if not isinstance(action, dict):
                    continue
                members = squad_members.get(action.get("object"), [])
                targets = action.get("target", [])
                if isinstance(targets, str):
                    targets = [targets]
                for member_name, target_name in zip(members, targets):
                    target = all_mission_units.get(target_name)
                    if target and is_matrix(target.get("tm")):
                        runtime_positions[member_name] = target["tm"][3][:3]

    # MLRS/TBM editor transforms are off-map holding positions. The mission
    # respawns each vehicle randomly inside its named 600 m sphere; use the
    # sphere centre as the honest representative marker position.
    all_areas = {}
    for data in documents.values():
        areas = data.get("areas")
        if isinstance(areas, dict):
            all_areas.update(areas)
    for unit_name in all_mission_units:
        if "_mlrs_" in unit_name:
            spawn_area_name = unit_name.replace("_mlrs_", "_mlrs_spawn_area_")
        elif "_tactical_missile_launcher_" in unit_name:
            spawn_area_name = unit_name.replace(
                "_tactical_missile_launcher_", "_tactical_missile_spawn_area_"
            )
        else:
            continue
        area = all_areas.get(spawn_area_name)
        if isinstance(area, dict) and is_matrix(area.get("tm")):
            runtime_positions[unit_name] = area["tm"][3][:3]

    for document_name, data in documents.items():
        units_root = data.get("units")
        if not isinstance(units_root, dict):
            continue
        for source_group, group_units in units_root.items():
            if not isinstance(group_units, list):
                continue
            for raw_site in group_units:
                if (
                    not isinstance(raw_site, dict)
                    or not raw_site.get("unit_class")
                    or not is_matrix(raw_site.get("tm"))
                ):
                    continue

                site = dict(raw_site)
                site["_source_group"] = source_group
                if site.get("name") in runtime_positions:
                    site["_runtime_world_pos"] = runtime_positions[site["name"]]
                source_counts[source_group] += 1
                layout = layouts.get(site["unit_class"], {"slots": [], "runway": None})
                editor_world_pos = site["tm"][3][:3]
                runtime_world_pos = site.get("_runtime_world_pos", editor_world_pos)
                output_site = {
                    "name": site.get("name", "unnamed_site"),
                    "unit_class": site["unit_class"],
                    "team": site.get("props", {}).get("army"),
                    "source_group": source_group,
                    "world_pos": runtime_world_pos,
                    "editor_world_pos": editor_world_pos,
                    "runtime_relocated": runtime_world_pos != editor_world_pos,
                    "runtime_position_kind": (
                        "random_spawn_area_center"
                        if (
                            "_mlrs_" in site.get("name", "")
                            or "_tactical_missile_launcher_" in site.get("name", "")
                        )
                        else "fixed"
                    ),
                    "tm": site["tm"],
                    "runways": [],
                    "route": extract_route(site),
                    "buildings_by_era": {},
                    "units_by_era": {},
                }

                if layout.get("runway"):
                    runway = layout["runway"]
                    output_site["runways"].append(
                        {
                            "start": transform_local_to_world(site["tm"], runway["start"]),
                            "end": transform_local_to_world(site["tm"], runway["end"]),
                            "width": runway["width"],
                        }
                    )

                for era in ERAS:
                    if source_group in {"tankModels", "ships"}:
                        output_site["buildings_by_era"][era] = []
                        output_site["units_by_era"][era] = (
                            []
                            if era == "1970" and "_mlrs_" in site.get("name", "")
                            else [direct_unit(site)]
                        )
                        continue

                    template_names, configs = active_templates(site, era)
                    buildings, building_slot_ids = create_buildings(
                        site, layout, template_names, configs, registry
                    )
                    settings = registry.unit_settings(
                        template_names, output_site["team"]
                    )
                    output_site["buildings_by_era"][era] = buildings
                    output_site["units_by_era"][era] = place_spawned_units(
                        site, layout, settings, building_slot_ids
                    )

                sites.append(output_site)

    output_path = args.output
    if not os.path.isabs(output_path):
        output_path = os.path.join(args.input, output_path)
    with open(output_path, "w", encoding="utf-8") as destination:
        json.dump(sites, destination, indent=2)

    print(f"Loaded {len(documents)} BLK/BLKX files and {len(layouts)} layouts.")
    print(f"Extracted {len(sites)} mission objects: {dict(source_counts)}")
    for era in ERAS:
        unit_count = sum(len(site["units_by_era"][era]) for site in sites)
        building_count = sum(len(site["buildings_by_era"][era]) for site in sites)
        unplaced_count = sum(
            unit.get("unplaced", False)
            for site in sites
            for unit in site["units_by_era"][era]
        )
        print(
            f"{era}: {unit_count} actual units, {building_count} buildings, "
            f"{unplaced_count} overflow placements"
        )
    print(f"Saved {output_path}")


if __name__ == "__main__":
    main()
