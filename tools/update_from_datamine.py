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
BRACKET_CONFIG = ROOT / "br_brackets.json"
GENERATED_OUTPUTS = (
    "map_data.json",
    "map_data_mirror.json",
    "mission_logic.json",
    "mission_logic_mirror.json",
    "presets.json",
)


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


def collect_unit_names(map_data_path: Path) -> list[str]:
    names: set[str] = set()
    for site in load_json(map_data_path):
        for units in site.get("units_by_era", {}).values():
            for unit in units:
                unit_name = unit.get("name") or unit.get("unit_class")
                if unit_name:
                    names.add(unit_name)
    return sorted(names)


def same_json(left: Path, right: Path) -> bool:
    return left.exists() and load_json(left) == load_json(right)


def rounded_position(position) -> tuple[float, ...] | None:
    if not isinstance(position, list):
        return None
    return tuple(round(float(value), 3) for value in position)


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


def unconfigured_brackets(presets: list[dict], bracket_config: dict) -> list[dict]:
    """Return generated scenarios without a human-confirmed BR bracket."""
    configured = bracket_config.get("presets", {})
    if not isinstance(configured, dict):
        return presets
    return [
        preset
        for preset in presets
        if not isinstance(configured.get(preset.get("id")), list)
        or not any(
            isinstance(value, str) and value.strip()
            for value in configured.get(preset.get("id"), [])
        )
    ]


def write_update_report(
    path: Path,
    repository: str,
    commit: str,
    presets: list[dict],
    mission_variants: list[dict],
    missing_object_groups: list[str],
    summaries: dict[str, dict],
    missing_brackets: list[dict],
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

    lines.extend(["", "## BR brackets to review", ""])
    if missing_brackets:
        lines.append(
            "- Add or confirm the manual BR brackets in `br_brackets.json` for: "
            + ", ".join(
                f"**{preset.get('label', preset['id'])}** (`{preset['id']}`)"
                for preset in missing_brackets
            )
            + "."
        )
    else:
        lines.append(
            "- Every detected scenario has a manual BR-bracket label. The datamine does not provide BR values, so confirm them before merging."
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
    mission_variants = manifest.get("mission_variants", [])
    if not mission_variants:
        raise RuntimeError("The manifest must define at least one mission variant.")
    mission_by_path = {item["path"]: item for item in mission_variants}
    all_paths = list(generator_by_path)
    all_paths.extend(
        path for path in mission_by_path if path not in generator_by_path
    )
    all_paths.extend(
        path
        for path in manifest.get("watch_files", [])
        if path not in generator_by_path and path not in mission_by_path
    )

    with tempfile.TemporaryDirectory(prefix="nuclear-thunder-update-") as temp_name:
        temp = Path(temp_name)
        inputs = temp / "inputs"
        generated = temp / "generated"
        inputs.mkdir()
        generated.mkdir()
        missions: dict[str, Path] = {}
        mission_documents: dict[str, dict] = {}

        for source_path in all_paths:
            encoded_path = urllib.parse.quote(source_path, safe="/")
            raw_url = (
                f"https://raw.githubusercontent.com/{repository}/{commit}/{encoded_path}"
            )
            content = download_bytes(raw_url)
            record = {
                "path": source_path,
                "sha": git_blob_sha(content),
                "used_for_generation": (
                    source_path in generator_by_path or source_path in mission_by_path
                ),
            }
            source_records.append(record)
            if source_path in generator_by_path:
                destination = inputs / generator_by_path[source_path]["name"]
                destination.write_bytes(content)
            if source_path in mission_by_path:
                variant = mission_by_path[source_path]
                destination = inputs / variant["name"]
                destination.write_bytes(content)
                missions[variant["id"]] = destination
                mission_documents[variant["id"]] = json.loads(content)
            print(f"Downloaded {source_path}")

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
        bracket_config = load_json(BRACKET_CONFIG) if BRACKET_CONFIG.exists() else {}
        missing_brackets = unconfigured_brackets(
            generated_presets or [], bracket_config
        )

        collector = generated / "unit_manifest.json"
        write_json(
            collector,
            {"foundUnits": collect_unit_names(generated / "map_data.json")},
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

        lock = {
            "repository": repository,
            "ref": ref,
            "accepted_commit": commit,
            "sources": source_records,
            "missing_object_groups": missing_object_groups,
        }
        generated_lock = generated / "datamine-lock.json"
        write_json(generated_lock, lock)

        candidates = list(GENERATED_OUTPUTS) + [
            "unit_specs.json",
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
            missing_brackets,
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
