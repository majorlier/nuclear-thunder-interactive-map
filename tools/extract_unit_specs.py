import argparse
import json
import os
from pathlib import Path


def as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def load_json(path):
    with open(path, "r", encoding="utf-8-sig") as source:
        return json.load(source)


def index_blkx(root):
    return {
        path.name.lower(): path
        for path in Path(root).rglob("*.blkx")
        if path.is_file()
    }


def referenced_file(index, reference):
    filename = os.path.basename(reference.replace("\\", "/"))
    return index.get(os.path.splitext(filename)[0].lower() + ".blkx")


def first_rocket(value):
    if isinstance(value, dict):
        rocket = value.get("rocket")
        if isinstance(rocket, dict):
            return rocket
        for child in value.values():
            found = first_rocket(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = first_rocket(child)
            if found:
                return found
    return None


def sensor_specs(unit, file_index):
    sensor_root = unit.get("sensors", {}).get("sensor")
    readings = []
    for sensor in as_list(sensor_root):
        if not isinstance(sensor, dict) or not isinstance(sensor.get("blk"), str):
            continue
        path = referenced_file(file_index, sensor["blk"])
        if not path:
            continue
        data = load_json(path)
        ai_variant = (
            sensor.get("human") is False
            or sensor.get("ai") is True
            or path.stem.lower().endswith("_ai")
        )
        for mode, transiver in data.get("transivers", {}).items():
            if not isinstance(transiver, dict):
                continue
            receiver = transiver.get("receiver", transiver)
            if not isinstance(receiver, dict):
                continue
            nominal = receiver.get("range")
            maximum = receiver.get("rangeMax")
            if not isinstance(nominal, (int, float)):
                continue
            mode_lower = mode.lower()
            if "irst" in mode_lower:
                kind = "irst"
            elif "track" in mode_lower:
                kind = "tracking"
            elif "search" in mode_lower or mode_lower == "common":
                kind = "detection"
            else:
                continue
            readings.append(
                {
                    "kind": kind,
                    "nominal": nominal,
                    "maximum": maximum if isinstance(maximum, (int, float)) else nominal,
                    "band": receiver.get("band"),
                    "ai": ai_variant,
                    "sensor": path.stem,
                }
            )

    result = {}
    for kind in ("detection", "tracking", "irst"):
        candidates = [reading for reading in readings if reading["kind"] == kind]
        ai_candidates = [reading for reading in candidates if reading["ai"]]
        if ai_candidates:
            candidates = ai_candidates
        if candidates:
            result[kind] = max(candidates, key=lambda reading: reading["nominal"])
    return result


def weapon_specs(unit, file_index, allow_air_gun_range=False):
    weapons_root = unit.get("commonWeapons", {}).get("Weapon")
    results = []
    ai_gun_ranges = []

    for weapon in as_list(weapons_root):
        if not isinstance(weapon, dict):
            continue
        reference = weapon.get("blk")
        if not isinstance(reference, str):
            continue
        path = referenced_file(file_index, reference)
        if not path:
            continue
        rocket = first_rocket(load_json(path))
        if not rocket:
            attack_range = weapon.get("AttackMaxRadius")
            if (
                allow_air_gun_range
                and isinstance(attack_range, (int, float))
                and weapon.get("accuracyAir", 0) > 0
            ):
                ai_gun_ranges.append(attack_range)
            continue

        bullet_type = str(rocket.get("bulletType", "")).lower()
        guidance = rocket.get("guidanceType")
        proximity_fuse = rocket.get("proximityFuse", {})
        is_air_weapon = (
            "sam" in bullet_type
            or "aam" in bullet_type
            or proximity_fuse.get("detectAirUnits") is True
        )
        is_strategic_rocket = any(
            token in path.stem.lower()
            for token in ("m270", "9k58", "9k79", "mgm_52")
        )
        if not is_air_weapon and not is_strategic_rocket:
            continue

        effective_max = rocket.get("rangeMax")
        physical_max = rocket.get("maxDistance")
        if not isinstance(effective_max, (int, float)):
            effective_max = physical_max
        if not isinstance(physical_max, (int, float)):
            physical_max = effective_max

        results.append(
            {
                "name": path.stem,
                "ammo": weapon.get("bullets")
                if isinstance(weapon.get("bullets"), (int, float))
                and weapon.get("bullets") >= 0
                else None,
                "minimum": rocket.get("minDistance", 0),
                "effective_maximum": effective_max,
                "physical_maximum": physical_max,
                "guidance": guidance,
            }
        )

    unique = {}
    for weapon in results:
        unique[weapon["name"].lower()] = weapon
    results = list(unique.values())

    engagement_candidates = [
        weapon["effective_maximum"]
        for weapon in results
        if isinstance(weapon.get("effective_maximum"), (int, float))
    ]
    if ai_gun_ranges:
        engagement_candidates.append(max(ai_gun_ranges))

    return {
        "weapons": sorted(
            results,
            key=lambda weapon: weapon.get("effective_maximum") or 0,
            reverse=True,
        ),
        "engagement_range": max(engagement_candidates)
        if engagement_candidates
        else None,
        "ai_gun_range": max(ai_gun_ranges) if ai_gun_ranges else None,
    }


def build_spec(unit_name, path, file_index):
    unit = load_json(path)
    sensors = sensor_specs(unit, file_index)
    allow_air_gun_range = (
        str(unit.get("onRadarAs", "")).lower() == "sam"
        or str(unit.get("expClass", "")).lower() == "exp_spaa"
        or "ships" in {part.lower() for part in path.parts}
    )
    weapons = weapon_specs(unit, file_index, allow_air_gun_range)
    channels = unit.get("targetChannelsMax")
    if not isinstance(channels, (int, float)):
        channels = unit.get("commonWeapons", {}).get("remotelyGuidedWeaponsMax")

    return {
        "unit_class": unit_name,
        "maximum_speed_kph": unit.get("maxFwdSpeed"),
        "target_channels": channels,
        "sensors": sensors,
        "weapons": weapons["weapons"],
        "engagement_range": weapons["engagement_range"],
        "ai_gun_range": weapons["ai_gun_range"],
    }


def main():
    parser = argparse.ArgumentParser(
        description="Extract map-safe sensor and weapon summaries from BLKX data."
    )
    parser.add_argument("--input", required=True, help="Path to aces.vromfs.bin_u")
    parser.add_argument("--manifest", required=True, help="Collector manifest JSON")
    parser.add_argument("--output", default="unit_specs.json")
    args = parser.parse_args()

    file_index = index_blkx(args.input)
    manifest = load_json(args.manifest)
    specs = {}
    missing = []
    for unit_name in manifest.get("foundUnits", []):
        path = file_index.get(unit_name.lower() + ".blkx")
        if not path:
            missing.append(unit_name)
            continue
        specs[unit_name.lower()] = build_spec(unit_name, path, file_index)

    with open(args.output, "w", encoding="utf-8") as destination:
        json.dump(specs, destination, indent=2)
    print(f"Wrote {len(specs)} unit specifications to {args.output}.")
    if missing:
        print("Missing unit files:", ", ".join(missing))


if __name__ == "__main__":
    main()
