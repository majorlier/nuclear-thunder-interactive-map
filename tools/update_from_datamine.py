"""Regenerate the map's event data from the public War Thunder datamine."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
DEFAULT_MANIFEST = TOOLS / "datamine_sources.json"
def load_json(path: Path):
    with path.open("r", encoding="utf-8") as source:
        return json.load(source)


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "nuclear-thunder-map-updater",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def github_json(url: str):
    request = urllib.request.Request(url, headers=github_headers())
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub returned HTTP {exc.code} for {url}: {detail}") from exc


def download_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers=github_headers())
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def git_blob_sha(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content).hexdigest()


def resolve_commit(repository: str, ref: str) -> str:
    encoded_ref = urllib.parse.quote(ref, safe="")
    value = github_json(f"https://api.github.com/repos/{repository}/commits/{encoded_ref}")
    return value["sha"]


def run(command: list[str]) -> None:
    print("+", " ".join(command))
    subprocess.run(command, cwd=ROOT, check=True)


def collect_unit_names(
    map_data_paths: list[Path], mission_logic_paths: list[Path] | None = None
) -> list[str]:
    names: set[str] = set()
    for map_data_path in map_data_paths:
        for site in load_json(map_data_path):
            for units in site.get("units_by_era", {}).values():
                for unit in units:
                    unit_name = unit.get("name") or unit.get("unit_class")
                    if unit_name:
                        names.add(unit_name)
    # Convoys are generated from mission logic rather than represented as
    # ordinary map-data sites. Include every convoy entry so their unit files
    # receive the same model/sensor/weapon extraction as static units.
    def collect_convoy_names(value):
        if isinstance(value, dict):
            name = value.get("name")
            if isinstance(name, str) and name.strip():
                names.add(name.strip())
            for child in value.values():
                collect_convoy_names(child)
        elif isinstance(value, list):
            for child in value:
                collect_convoy_names(child)

    for mission_logic_path in mission_logic_paths or []:
        logic = load_json(mission_logic_path)
        collect_convoy_names(logic.get("convoys", {}))
    return sorted(names)


def same_json(left: Path, right: Path) -> bool:
    return left.exists() and load_json(left) == load_json(right)


def rounded_position(position) -> tuple[float, ...] | None:
    if not isinstance(position, list):
        return None
    return tuple(round(float(value), 3) for value in position)


def canonical_mission_import_path(value: str) -> str | None:
    """Convert a mission import record into a datamine repository path."""
    if not isinstance(value, str) or not value.strip():
        return None
    path = value.replace("\\", "/").lstrip("/")
    lower = path.lower()
    if lower.startswith("gamedata/"):
        path = "mis.vromfs.bin_u/" + path
    elif not lower.startswith("mis.vromfs.bin_u/"):
        return None
    if path.lower().endswith(".blk"):
        path = path[:-4] + ".blkx"
    # Mission records historically used mixed-case `gameData` and `Tdm`,
    # while repository paths are lowercase and case-sensitive.
    return path.lower()


def mission_import_paths(document: dict) -> list[str]:
    """Return event mission imports, including nested imports."""
    result: list[str] = []
    seen: set[str] = set()
    queue = [document]
    while queue:
        current = queue.pop()
        if not isinstance(current, dict):
            continue
        imports = current.get("imports", {})
        records = imports.get("import_record", []) if isinstance(imports, dict) else []
        if isinstance(records, dict):
            records = [records]
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            path = canonical_mission_import_path(record.get("file", ""))
            if not path or path in seen or "nuclear_escalation" not in path:
                continue
            seen.add(path)
            result.append(path)
    return result


def unit_loadout(site: dict) -> dict[str, tuple[tuple, ...]]:
    """Return a compact comparison signature for every detected scenario."""
    result = {}
    for preset_id, units in site.get("units_by_era", {}).items():
        entries = Counter(
            (
                unit.get("name") or unit.get("unit_class") or "unknown",
                unit.get("role") or "unknown",
                unit.get("slot_type") or "unknown",
                unit.get("preset") or "unknown",
                unit.get("count", 1),
                rounded_position(unit.get("local_pos")) or (),
                bool(unit.get("unplaced")),
            )
            for unit in units
        )
        result[preset_id] = tuple(sorted(entries.items()))
    return result


def map_change_summary(previous_path: Path, current_path: Path) -> dict:
    """Describe the human-reviewable changes for one generated mission variant."""
    current_sites = {site["name"]: site for site in load_json(current_path)}
    if not previous_path.exists():
        return {
            "previously_missing": True,
            "added": sorted(current_sites),
            "removed": [],
            "moved": [],
            "changed_loadouts": [],
        }

    previous_sites = {site["name"]: site for site in load_json(previous_path)}
    shared_names = previous_sites.keys() & current_sites.keys()
    return {
        "previously_missing": False,
        "added": sorted(current_sites.keys() - previous_sites.keys()),
        "removed": sorted(previous_sites.keys() - current_sites.keys()),
        "moved": sorted(
            name
            for name in shared_names
            if rounded_position(previous_sites[name].get("world_pos"))
            != rounded_position(current_sites[name].get("world_pos"))
        ),
        "changed_loadouts": sorted(
            name
            for name in shared_names
            if unit_loadout(previous_sites[name]) != unit_loadout(current_sites[name])
        ),
    }


def markdown_names(names: list[str], limit: int = 18) -> str:
    if not names:
        return "none"
    shown = ", ".join(f"`{name}`" for name in names[:limit])
    remainder = len(names) - limit
    return f"{shown} (+{remainder} more)" if remainder > 0 else shown


def write_update_report(
    path: Path,
    repository: str,
    commit: str,
    presets: list[dict],
    mission_variants: list[dict],
    missing_object_groups: list[str],
    summaries: dict[str, dict],
) -> None:
    """Write a short, ignored report for the Action summary and PR review."""
    lines = [
        "# Nuclear Escalation datamine update report",
        "",
        f"Source: `{repository}@{commit}`.",
        "",
        "## Detected scenarios",
        "",
    ]
    for preset in presets:
        rank_range = preset.get("rank_range", [])
        rank_text = (
            f"{rank_range[0]}–{rank_range[1]}"
            if isinstance(rank_range, list) and len(rank_range) == 2
            else "unknown"
        )
        lines.append(
            f"- **{preset.get('label', preset['id'])}** — "
            f"rank indices {rank_text} "
            f"(`{preset['id']}`)"
        )

    lines.extend(["", "## Mission-location variants", ""])
    for variant in mission_variants:
        variant_id = variant["id"]
        summary = summaries[variant_id]
        label = variant.get("label", variant_id)
        lines.extend([f"### {label}", ""])
        if summary["previously_missing"]:
            lines.append(
                f"- New generated dataset: {len(summary['added'])} mission objects."
            )
        else:
            lines.extend(
                [
                    f"- Added objects: {len(summary['added'])} — {markdown_names(summary['added'])}",
                    f"- Removed objects: {len(summary['removed'])} — {markdown_names(summary['removed'])}",
                    f"- Moved objects: {len(summary['moved'])} — {markdown_names(summary['moved'])}",
                    "- Changed unit layouts/loadouts: "
                    f"{len(summary['changed_loadouts'])} — "
                    f"{markdown_names(summary['changed_loadouts'])}",
                ]
            )
        lines.append("")

    lines.extend(["## Object-group transform sources", ""])
    if missing_object_groups:
        lines.append(
            "- Missing reusable object-group BLKX files: "
            f"{markdown_names(missing_object_groups)}. The static fallback layout was used."
        )
    else:
        lines.append(
            "- All mission object groups had a datamined BLKX transform source; no static fallback was needed."
        )
    lines.extend(
        [
            "",
            "Review the map preview before merging; this report does not replace in-game testing.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def set_github_output(path: str | None, changed: bool, commit: str) -> None:
    if not path:
        return
    with open(path, "a", encoding="utf-8") as destination:
        destination.write(f"changed={'true' if changed else 'false'}\n")
        destination.write(f"datamine_commit={commit}\n")


def object_group_classes(mission: dict) -> set[str]:
    units = mission.get("units", {})
    groups = units.get("objectGroups", []) if isinstance(units, dict) else []
    return {
        unit["unit_class"]
        for unit in groups
        if isinstance(unit, dict) and isinstance(unit.get("unit_class"), str)
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download the configured datamine inputs and regenerate map data."
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--commit",
        help="Use a known upstream commit instead of resolving the manifest ref.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report differences without changing repository files.",
    )
    parser.add_argument(
        "--aces-root",
        type=Path,
        help="Optional local aces.vromfs.bin_u folder used to refresh unit_specs.json.",
    )
    parser.add_argument(
        "--github-output",
        help="GitHub Actions output file (normally the GITHUB_OUTPUT environment value).",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "datamine-update-report.md",
        help="Ignored Markdown review report path.",
    )
    args = parser.parse_args()

    manifest = load_json(args.manifest)
    repository = manifest["repository"]
    ref = manifest.get("ref", "master")
    commit = args.commit or resolve_commit(repository, ref)
    print(f"Datamine: {repository}@{commit}")

    source_records: list[dict] = []
    generator_by_path = {
        item["path"]: item for item in manifest.get("generator_files", [])
    }
    runtime_by_path = {
        item["path"]: item for item in manifest.get("runtime_files", [])
    }
    mission_variants = manifest.get("mission_variants", [])
    if not mission_variants:
        raise RuntimeError("The manifest must define at least one mission variant.")
    mission_by_path = {item["path"]: item for item in mission_variants}
    all_paths = list(generator_by_path)
    all_paths.extend(
        path for path in runtime_by_path if path not in generator_by_path
    )
    all_paths.extend(
        path for path in mission_by_path if path not in generator_by_path
    )
    all_paths.extend(
        path
        for path in manifest.get("watch_files", [])
        if path not in generator_by_path
        and path not in runtime_by_path
        and path not in mission_by_path
    )

    with tempfile.TemporaryDirectory(prefix="nuclear-thunder-update-") as temp_name:
        temp = Path(temp_name)
        inputs = temp / "inputs"
        generated = temp / "generated"
        inputs.mkdir()
        generated.mkdir()
        missions: dict[str, Path] = {}
        mission_documents: dict[str, dict] = {}
        localization_file: Path | None = None

        for source_path in all_paths:
            encoded_path = urllib.parse.quote(source_path, safe="/")
            raw_url = (
                f"https://raw.githubusercontent.com/{repository}/{commit}/{encoded_path}"
            )
            try:
                content = download_bytes(raw_url)
            except urllib.error.HTTPError as exc:
                # Watch files are useful change indicators but are not inputs
                # to the generator.  Gaijin can remove or relocate them; that
                # must not prevent a valid mission update from being prepared.
                if (
                    exc.code == 404
                    and source_path not in generator_by_path
                    and source_path not in mission_by_path
                ):
                    print(f"WARNING: Optional watch file is absent: {source_path}")
                    continue
                raise
            record = {
                "path": source_path,
                "sha": git_blob_sha(content),
                "used_for_generation": (
                    source_path in generator_by_path
                    or source_path in runtime_by_path
                    or source_path in mission_by_path
                ),
            }
            source_records.append(record)
            if source_path in generator_by_path:
                destination = inputs / generator_by_path[source_path]["name"]
                destination.write_bytes(content)
            if source_path in runtime_by_path:
                destination = inputs / runtime_by_path[source_path]["name"]
                destination.write_bytes(content)
            if source_path in mission_by_path:
                variant = mission_by_path[source_path]
                destination = inputs / variant["name"]
                destination.write_bytes(content)
                missions[variant["id"]] = destination
                mission_documents[variant["id"]] = json.loads(content)
            print(f"Downloaded {source_path}")

        # Mission imports are part of the mission definition, not a stable
        # global manifest. Resolve them recursively so newly split resources
        # (such as today's map-specific carrier files) cannot silently vanish
        # from the generated map.
        pending_imports = []
        queued_imports: set[str] = set()
        for document in mission_documents.values():
            for path in mission_import_paths(document):
                if path not in queued_imports:
                    queued_imports.add(path)
                    pending_imports.append(path)
        while pending_imports:
            source_path = pending_imports.pop(0)
            encoded_path = urllib.parse.quote(source_path, safe="/")
            raw_url = f"https://raw.githubusercontent.com/{repository}/{commit}/{encoded_path}"
            try:
                content = download_bytes(raw_url)
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    raise RuntimeError(
                        f"Mission import is missing from the datamine: {source_path}"
                    ) from exc
                raise
            source_records.append(
                {
                    "path": source_path,
                    "sha": git_blob_sha(content),
                    "used_for_generation": True,
                }
            )
            destination = inputs / Path(source_path).name
            destination.write_bytes(content)
            try:
                document = json.loads(content)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"Mission import is not valid converted BLKX JSON: {source_path}"
                ) from exc
            for child_path in mission_import_paths(document):
                if child_path not in queued_imports:
                    queued_imports.add(child_path)
                    pending_imports.append(child_path)
            print(f"Downloaded mission import {source_path}")

        # The client-facing unit labels live in the localization table rather
        # than in vehicle BLKs. Download it at the same upstream commit so the
        # generated map can use the game's readable names without hardcoding.
        localization_path = "lang.vromfs.bin_u/lang/units.csv"
        encoded_path = urllib.parse.quote(localization_path, safe="/")
        localization_url = f"https://raw.githubusercontent.com/{repository}/{commit}/{encoded_path}"
        try:
            localization_content = download_bytes(localization_url)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                print(f"WARNING: Unit localization table is absent: {localization_path}")
            else:
                raise
        else:
            localization_file = inputs / "units.csv"
            localization_file.write_bytes(localization_content)
            source_records.append(
                {
                    "path": localization_path,
                    "sha": git_blob_sha(localization_content),
                    "used_for_generation": True,
                }
            )
            print(f"Downloaded {localization_path}")

        # Each mission object references a reusable object-group BLKX by
        # unit_class. Those blocks contain the exact local launcher/radar/etc.
        # transforms, so they replace the former hand-maintained layout cache
        # whenever the public datamine supplies them.
        missing_object_groups = []
        for unit_class in sorted(
            set().union(*(object_group_classes(document) for document in mission_documents.values()))
        ):
            source_path = f"aces.vromfs.bin_u/gamedata/objectgroups/{unit_class}.blkx"
            encoded_path = urllib.parse.quote(source_path, safe="/")
            raw_url = f"https://raw.githubusercontent.com/{repository}/{commit}/{encoded_path}"
            try:
                content = download_bytes(raw_url)
            except urllib.error.HTTPError as exc:
                if exc.code != 404:
                    raise
                missing_object_groups.append(unit_class)
                print(f"WARNING: No object-group BLKX for {unit_class}")
                continue
            source_records.append(
                {
                    "path": source_path,
                    "sha": git_blob_sha(content),
                    "used_for_generation": True,
                }
            )
            (inputs / f"{unit_class}.blkx").write_bytes(content)
            print(f"Downloaded {source_path}")

        generated_presets = None
        for variant in mission_variants:
            variant_id = variant["id"]
            is_standard = variant_id == "standard"
            map_name = "map_data.json" if is_standard else f"map_data_{variant_id}.json"
            logic_name = (
                "mission_logic.json"
                if is_standard
                else f"mission_logic_{variant_id}.json"
            )
            temporary_presets = generated / f"presets_{variant_id}.json"
            run(
                [
                    sys.executable,
                    str(TOOLS / "build_map_data.py"),
                    "--input",
                    str(inputs),
                    "--mission",
                    str(missions[variant_id]),
                    "--layout-cache",
                    str(TOOLS / "layouts.json"),
                    "--output",
                    str(generated / map_name),
                    "--presets-output",
                    str(temporary_presets),
                ]
            )
            run(
                [
                    sys.executable,
                    str(TOOLS / "extract_mission_logic.py"),
                    str(missions[variant_id]),
                    "--templates-dir",
                    str(inputs),
                    "--output",
                    str(generated / logic_name),
                ]
            )
            variant_presets = load_json(temporary_presets)
            if generated_presets is None:
                generated_presets = variant_presets
            elif variant_presets != generated_presets:
                raise RuntimeError(
                    f"Scenario presets differ between standard and {variant_id} missions"
                )

        write_json(
            generated / "presets.json",
            {
                "presets": generated_presets or [],
                "variants": [
                    {"id": item["id"], "label": item.get("label", item["id"])}
                    for item in mission_variants
                ],
            },
        )
        collector = generated / "unit_manifest.json"
        map_data_paths = [
            generated / (
                "map_data.json"
                if item["id"] == "standard"
                else f"map_data_{item['id']}.json"
            )
            for item in mission_variants
        ]
        mission_logic_paths = [
            generated / (
                "mission_logic.json"
                if item["id"] == "standard"
                else f"mission_logic_{item['id']}.json"
            )
            for item in mission_variants
        ]
        write_json(
            collector,
            {
                "foundUnits": collect_unit_names(map_data_paths, mission_logic_paths)
            },
        )
        unit_spec_command = [
            sys.executable,
            str(TOOLS / "extract_unit_specs.py"),
            "--manifest",
            str(collector),
            "--output",
            str(generated / "unit_specs.json"),
        ]
        if args.aces_root:
            unit_spec_command.extend(["--input", str(args.aces_root)])
        else:
            unit_spec_command.extend(
                ["--repository", repository, "--commit", commit]
            )
        run(unit_spec_command)
        generated_specs = load_json(generated / "unit_specs.json")
        if not generated_specs:
            if args.aces_root and (ROOT / "unit_specs.json").exists():
                # A normal local VROMFS dump contains native `.blk` files;
                # the card extractor consumes the datamine's converted JSON
                # `.blkx` files. Never replace a valid checked-in card set
                # with `{}` just because that optional local shortcut cannot
                # read the source format.
                print(
                    "WARNING: local unit source produced no JSON BLKX specs; "
                    "preserving the checked-in unit_specs.json"
                )
                shutil.copyfile(ROOT / "unit_specs.json", generated / "unit_specs.json")
            else:
                raise RuntimeError(
                    "Unit specification extraction produced no data. "
                    "Refusing to overwrite unit_specs.json."
                )
        missing_models = sorted(
            name for name, spec in generated_specs.items()
            if not isinstance(spec, dict) or not str(spec.get("model") or "").strip()
        )
        if missing_models:
            raise RuntimeError(
                "Unit specification extraction produced entries without model names: "
                + ", ".join(missing_models)
            )

        generated_names = generated / "unit_display_names.json"
        if localization_file:
            run(
                [
                    sys.executable,
                    str(TOOLS / "extract_display_names.py"),
                    "--units-csv",
                    str(localization_file),
                    "--specs",
                    str(generated / "unit_specs.json"),
                    "--output",
                    str(generated_names),
                ]
            )
        elif (ROOT / "unit_display_names.json").exists():
            shutil.copyfile(ROOT / "unit_display_names.json", generated_names)
        else:
            write_json(generated_names, {})

        lock = {
            "repository": repository,
            "ref": ref,
            "accepted_commit": commit,
            "sources": source_records,
            "missing_object_groups": missing_object_groups,
        }
        generated_lock = generated / "datamine-lock.json"
        write_json(generated_lock, lock)

        generated_outputs = ["presets.json"]
        for variant in mission_variants:
            variant_id = variant["id"]
            generated_outputs.extend(
                [
                    "map_data.json"
                    if variant_id == "standard"
                    else f"map_data_{variant_id}.json",
                    "mission_logic.json"
                    if variant_id == "standard"
                    else f"mission_logic_{variant_id}.json",
                ]
            )
        candidates = generated_outputs + [
            "unit_specs.json",
            "unit_display_names.json",
            "datamine-lock.json",
        ]
        changed_files = [
            name for name in candidates if not same_json(ROOT / name, generated / name)
        ]
        summaries = {}
        for variant in mission_variants:
            variant_id = variant["id"]
            map_name = "map_data.json" if variant_id == "standard" else f"map_data_{variant_id}.json"
            summaries[variant_id] = map_change_summary(
                ROOT / map_name, generated / map_name
            )
        write_update_report(
            args.report,
            repository,
            commit,
            generated_presets or [],
            mission_variants,
            missing_object_groups,
            summaries,
        )
        changed = bool(changed_files)

        if changed_files:
            print("Changed:", ", ".join(changed_files))
        else:
            print("The checked-in data already matches the datamine.")

        if changed and not args.check:
            for name in changed_files:
                shutil.copyfile(generated / name, ROOT / name)
            print("Updated repository files. Review and validate them before publishing.")
        elif changed:
            print("Check-only mode: repository files were not changed.")

    set_github_output(args.github_output, changed, commit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
