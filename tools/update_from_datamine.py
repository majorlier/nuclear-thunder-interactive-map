"""Regenerate the map's event data from the public War Thunder datamine."""

from __future__ import annotations

import argparse
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
GENERATED_OUTPUTS = ("map_data.json", "mission_logic.json")


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


def set_github_output(path: str | None, changed: bool, commit: str) -> None:
    if not path:
        return
    with open(path, "a", encoding="utf-8") as destination:
        destination.write(f"changed={'true' if changed else 'false'}\n")
        destination.write(f"datamine_commit={commit}\n")


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
    all_paths = list(generator_by_path)
    all_paths.extend(
        path for path in manifest.get("watch_files", []) if path not in generator_by_path
    )

    with tempfile.TemporaryDirectory(prefix="nuclear-thunder-update-") as temp_name:
        temp = Path(temp_name)
        inputs = temp / "inputs"
        generated = temp / "generated"
        inputs.mkdir()
        generated.mkdir()

        for source_path in all_paths:
            encoded_path = urllib.parse.quote(source_path, safe="/")
            raw_url = (
                f"https://raw.githubusercontent.com/{repository}/{commit}/{encoded_path}"
            )
            content = download_bytes(raw_url)
            record = {
                "path": source_path,
                "sha": git_blob_sha(content),
                "used_for_generation": source_path in generator_by_path,
            }
            source_records.append(record)
            if source_path in generator_by_path:
                destination = inputs / generator_by_path[source_path]["name"]
                destination.write_bytes(content)
            print(f"Downloaded {source_path}")

        run(
            [
                sys.executable,
                str(TOOLS / "build_map_data.py"),
                "--input",
                str(inputs),
                "--layout-cache",
                str(TOOLS / "layouts.json"),
                "--output",
                str(generated / "map_data.json"),
            ]
        )
        run(
            [
                sys.executable,
                str(TOOLS / "extract_mission_logic.py"),
                str(inputs / "nuclear_escalation_tdm.blkx"),
                "--output",
                str(generated / "mission_logic.json"),
            ]
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
