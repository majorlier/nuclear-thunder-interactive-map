"""Extract map-safe sensor and weapon summaries from local or public BLKX data."""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath


def as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def load_json(path):
    with open(path, "r", encoding="utf-8-sig") as source:
        return json.load(source)


class LocalSource:
    """Read from a locally extracted aces.vromfs.bin_u directory."""

    def __init__(self, root):
        self.index = {
            path.name.lower(): path
            for path in Path(root).rglob("*.blkx")
            if path.is_file()
        }

    def find_unit(self, unit_name):
        return self.index.get(unit_name.lower() + ".blkx")

    def reference(self, reference):
        filename = os.path.basename(reference.replace("\\", "/"))
        return self.index.get(os.path.splitext(filename)[0].lower() + ".blkx")

    def load(self, path):
        return load_json(path)


class DatamineSource:
    """Fetch just the unit files needed from a pinned public datamine commit."""

    unit_directories = (
        "tankmodels",
        "ships",
        "air_defence",
        "radars",
        "wheeled_vehicles",
        "tracked_vehicles",
        "structures",
        "phys_obj",
    )

    def __init__(self, repository, commit):
        self.repository = repository
        self.commit = commit
        self.cache = {}

    @staticmethod
    def datamine_path(reference):
        value = reference.replace("\\", "/").lstrip("/")
        lower = value.lower()
        if lower.startswith("aces.vromfs.bin_u/"):
            path = PurePosixPath(value)
        elif lower.startswith("gamedata/"):
            path = PurePosixPath("aces.vromfs.bin_u") / value
        else:
            path = PurePosixPath("aces.vromfs.bin_u/gamedata") / value
        if path.suffix.lower() == ".blk":
            path = path.with_suffix(".blkx")
        return path.as_posix().lower()

    def headers(self):
        headers = {"User-Agent": "nuclear-thunder-map-updater"}
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def load(self, path):
        path = str(path)
        if path in self.cache:
            return self.cache[path]
        encoded = urllib.parse.quote(path, safe="/")
        url = (
            f"https://raw.githubusercontent.com/{self.repository}/"
            f"{self.commit}/{encoded}"
        )
        request = urllib.request.Request(url, headers=self.headers())
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                document = json.loads(response.read().decode("utf-8-sig"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise FileNotFoundError(path) from exc
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Datamine returned HTTP {exc.code} for {path}: {detail}"
            ) from exc
        self.cache[path] = document
        return document

    def find_unit(self, unit_name):
        name = unit_name.lower() + ".blkx"
        directories = list(self.unit_directories)
        if any(
            token in unit_name.lower()
            for token in ("carrier", "cruiser", "destroyer", "corvette", "mpk")
        ):
            directories.remove("ships")
            directories.insert(0, "ships")
        for directory in directories:
            path = f"aces.vromfs.bin_u/gamedata/units/{directory}/{name}"
            try:
                self.load(path)
                return path
            except FileNotFoundError:
                continue
        return None

    def reference(self, reference):
        path = self.datamine_path(reference)
        try:
            self.load(path)
            return path
        except FileNotFoundError:
            return None


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


def sensor_specs(unit, source):
    sensor_root = unit.get("sensors", {}).get("sensor")
    readings = []
    for sensor in as_list(sensor_root):
        if not isinstance(sensor, dict) or not isinstance(sensor.get("blk"), str):
            continue
        path = source.reference(sensor["blk"])
        if not path:
            continue
        data = source.load(path)
        ai_variant = (
            sensor.get("human") is False
            or sensor.get("ai") is True
            or Path(path).stem.lower().endswith("_ai")
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
                    "sensor": Path(path).stem,
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


def weapon_specs(unit, source, allow_air_gun_range=False):
    weapons_root = unit.get("commonWeapons", {}).get("Weapon")
    results = []
    ai_gun_ranges = []

    for weapon in as_list(weapons_root):
        if not isinstance(weapon, dict):
            continue
        reference = weapon.get("blk")
        if not isinstance(reference, str):
            continue
        path = source.reference(reference)
        if not path:
            continue
        rocket = first_rocket(source.load(path))
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
            token in Path(path).stem.lower()
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
                "name": Path(path).stem,
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

    unique = {weapon["name"].lower(): weapon for weapon in results}
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


def build_spec(unit_name, path, source):
    unit = source.load(path)
    sensors = sensor_specs(unit, source)
    allow_air_gun_range = (
        str(unit.get("onRadarAs", "")).lower() == "sam"
        or str(unit.get("expClass", "")).lower() == "exp_spaa"
        or "ships" in {part.lower() for part in Path(path).parts}
    )
    weapons = weapon_specs(unit, source, allow_air_gun_range)
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
    parser.add_argument("--input", help="Path to a local aces.vromfs.bin_u folder")
    parser.add_argument("--repository", help="Public datamine repository in owner/name form")
    parser.add_argument("--commit", help="Pinned datamine commit used with --repository")
    parser.add_argument("--manifest", required=True, help="Collector manifest JSON")
    parser.add_argument("--output", default="unit_specs.json")
    args = parser.parse_args()

    if args.input and (args.repository or args.commit):
        parser.error("Use either --input or --repository/--commit, not both.")
    if args.input:
        source = LocalSource(args.input)
    elif args.repository and args.commit:
        source = DatamineSource(args.repository, args.commit)
    else:
        parser.error("Provide --input or both --repository and --commit.")

    manifest = load_json(args.manifest)
    specs = {}
    missing = []
    for unit_name in manifest.get("foundUnits", []):
        path = source.find_unit(unit_name)
        if not path:
            missing.append(unit_name)
            continue
        specs[unit_name.lower()] = build_spec(unit_name, path, source)

    with open(args.output, "w", encoding="utf-8") as destination:
        json.dump(specs, destination, indent=2)
    print(f"Wrote {len(specs)} unit specifications to {args.output}.")
    if missing:
        print("Missing unit files:", ", ".join(missing))


if __name__ == "__main__":
    main()
