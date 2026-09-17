"""Copy map-referenced vehicle art from a local War Thunder datamine.

The datamine repository is the source for game assets and configuration, but
the map should serve its own local copies at runtime.  This tool uses the
model/unit names already extracted into ``unit_specs.json`` and copies only
matching assets:

* ``atlases.vromfs.bin_u/units`` -> ``images/slots`` (small hover icons)
* ``tex.vromfs.bin_u``            -> ``images/portraits`` (large card art)
* ``atlases.vromfs.bin_u/gameuiskin/def_*.svg`` -> the neutral/NATO/Pact
  folders under ``images/icons``
* ``atlases.vromfs.bin_u/gameuiskin/{b_52h,tu_95m}_ico.svg`` ->
  ``images/icons/aircraft`` for strategic-bomber spawn markers

It never overwrites an existing local file unless ``--force`` is supplied.
The JSON report is useful for finding event-only units that have no art in the
datamine or in the map's local portrait library.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def stem_variants(value: str | None) -> list[str]:
    """Return safe filename stems, preserving the most specific name first."""
    if not value:
        return []
    raw = str(value).replace("\\", "/").split("/")[-1]
    raw = re.sub(r"\.(?:blkx?|dag)$", "", raw, flags=re.I).lower()
    if not raw:
        return []
    values = [raw]
    # AI/EC mission suffixes normally point to the same playable artwork.
    for candidate in (re.sub(r"_ai$", "", raw), re.sub(r"_ec$", "", raw)):
        if candidate and candidate not in values:
            values.append(candidate)
    return values


def index_files(root: Path, suffixes: set[str] | None = None) -> dict[str, list[Path]]:
    result: dict[str, list[Path]] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if suffixes and path.suffix.lower() not in suffixes:
            continue
        result.setdefault(path.name.lower(), []).append(path)
    return result


def choose(index: dict[str, list[Path]], stems: list[str], suffix: str) -> Path | None:
    for stem in stems:
        matches = index.get(f"{stem}{suffix}".lower())
        if matches:
            # Prefer the extracted VROMFS directory that is intended for this
            # asset type when more than one copy exists.
            matches = sorted(matches, key=lambda p: ("atlases.vromfs.bin_u" not in p.parts, str(p)))
            return matches[0]
    return None


def copy_once(source: Path | None, destination: Path, force: bool) -> str:
    if source is None:
        return "missing"
    if destination.exists() and not force:
        return "existing"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return "copied"


def relative(path: Path | None, root: Path) -> str | None:
    return path.relative_to(root).as_posix() if path else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--datamine",
        required=True,
        type=Path,
        help="Root of the cloned War-Thunder-Datamine repository",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=ROOT,
        help="Map repository (defaults to the parent of this tools directory)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="JSON report path (defaults to generated/datamine_asset_sync_report.json)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace existing files; by default custom/local art is preserved",
    )
    args = parser.parse_args()

    datamine = args.datamine.resolve()
    repo = args.repo_root.resolve()
    specs_path = repo / "unit_specs.json"
    if not datamine.is_dir():
        parser.error(f"datamine directory does not exist: {datamine}")
    if not specs_path.is_file():
        parser.error(f"unit specs not found: {specs_path}")

    specs = load_json(specs_path)
    # Limit indexes to the relevant extracted asset trees. This keeps the
    # operation quick even on a full datamine checkout.
    atlas_root = datamine / "atlases.vromfs.bin_u" / "units"
    tex_root = datamine / "tex.vromfs.bin_u"
    skin_root = datamine / "atlases.vromfs.bin_u" / "gameuiskin"
    atlas_index = index_files(atlas_root, {".png"}) if atlas_root.is_dir() else {}
    tex_index = index_files(tex_root, {".png"}) if tex_root.is_dir() else {}

    slots_dir = repo / "images" / "slots"
    portraits_dir = repo / "images" / "portraits"
    vanilla_dir = repo / "images" / "icons" / "vanilla"
    nato_dir = repo / "images" / "icons" / "nato"
    pact_dir = repo / "images" / "icons" / "pact"
    aircraft_dir = repo / "images" / "icons" / "aircraft"

    # Copy the neutral and faction role-symbol library once. These are the
    # transparent ``def_*`` symbols used by the map, not vehicle ``*_ico``
    # artwork (which belongs to the slot/portrait pipeline).
    role_icons: list[dict[str, str]] = []
    for source in sorted(skin_root.glob("def_*.svg")) if skin_root.is_dir() else []:
        filename = source.name.lower()
        if filename.startswith("def_nato_"):
            destination = nato_dir / filename
            family = "nato"
        elif filename.startswith("def_ussr_"):
            destination = pact_dir / filename
            family = "pact"
        else:
            destination = vanilla_dir / filename
            family = "vanilla"
        status = copy_once(source, destination, args.force)
        role_icons.append({
            "family": family,
            "name": filename,
            "status": status,
            "source": relative(source, datamine) or "",
            "path": relative(destination, repo),
        })

    # Strategic-bomber spawn markers use the original unpainted game symbols;
    # the browser recolours these SVGs per team at render time.
    for filename in ("b_52h_ico.svg", "tu_95m_ico.svg"):
        source = skin_root / filename
        if source.is_file():
            copy_once(source, aircraft_dir / filename, args.force)

    report: list[dict[str, object]] = []

    for key in sorted(specs):
        spec = specs[key] or {}
        unit_class = str(spec.get("unit_class") or key)
        model = str(spec.get("model") or "")
        unit_stems = []
        for value in (unit_class, key, model):
            for stem in stem_variants(value):
                if stem not in unit_stems:
                    unit_stems.append(stem)
        model_stems = stem_variants(model)
        stems = model_stems + [stem for stem in unit_stems if stem not in model_stems]

        # Slots are keyed by the mission unit class first; portraits are keyed
        # by the extracted model first, with the mission class as a fallback.
        slot_source = choose(atlas_index, unit_stems, ".png")
        # A model may be generic (for example ``2s6``), while its extracted
        # texture is named after the full unit class. The ordered candidates
        # above therefore intentionally include both model and unit class.
        portrait_source = choose(tex_index, stems, ".png")

        unit_basename = Path(unit_class.replace("\\", "/")).name.lower()
        slot_name = f"{unit_basename}.png"
        model_stem = stem_variants(model)[0] if stem_variants(model) else unit_basename
        portrait_name = f"{model_stem}.png"
        slot_dest = slots_dir / slot_name
        model_slot_dest = slots_dir / f"{model_stem}.png"
        portrait_dest = portraits_dir / portrait_name
        slot_status = copy_once(slot_source, slot_dest, args.force)
        model_slot_status = "not_applicable"
        if model and model_stem != unit_basename:
            # Keep a model-keyed copy as well. Two mission/BLK classes that
            # share one model (for example the regular and early M192) then
            # resolve to exactly the same compact icon in the browser.
            model_slot_status = copy_once(slot_source, model_slot_dest, args.force)
        portrait_status = copy_once(portrait_source, portrait_dest, args.force)

        report.append(
            {
                "unit_class": unit_class,
                "model": model or None,
                "slot": {
                    "status": slot_status,
                    "source": relative(slot_source, datamine),
                    "path": relative(slot_dest, repo) if slot_status != "missing" else None,
                    "model_path": relative(model_slot_dest, repo)
                    if model_slot_status not in {"missing", "not_applicable"}
                    else None,
                    "model_status": model_slot_status,
                },
                "portrait": {
                    "status": portrait_status,
                    "source": relative(portrait_source, datamine),
                    "path": relative(portrait_dest, repo) if portrait_status != "missing" else None,
                },
            }
        )

    report_path = (args.report or (repo / "generated" / "datamine_asset_sync_report.json")).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def count(kind: str, status: str) -> int:
        return sum(item[kind]["status"] == status for item in report)  # type: ignore[index]

    # Keep a human-readable report beside the machine-readable one so the
    # missing-art list can be reviewed before a merge.
    markdown_path = report_path.with_suffix(".md")
    missing_slots = [item for item in report if item["slot"]["status"] == "missing"]
    missing_portraits = [
        item for item in report
        if item["portrait"]["status"] == "missing"
    ]
    lines = [
        "# Datamine asset sync report",
        "",
        f"Scanned **{len(report)}** map units from `{datamine}`.",
        "",
        "| Asset | Copied | Already local | Missing in datamine |",
        "| --- | ---: | ---: | ---: |",
        f"| Slot images | {count('slot', 'copied')} | {count('slot', 'existing')} | {count('slot', 'missing')} |",
        f"| Portraits | {count('portrait', 'copied')} | {count('portrait', 'existing')} | {count('portrait', 'missing')} |",
        f"| Role SVG library | {sum(item['status'] == 'copied' for item in role_icons)} | {sum(item['status'] == 'existing' for item in role_icons)} | {sum(item['status'] == 'missing' for item in role_icons)} |",
        "",
        "## Missing slot images",
        "",
    ]
    lines += [f"- `{item['unit_class']}`" for item in missing_slots] or ["- None"]
    lines += ["", "## Missing portraits (no datamine art)", ""]
    lines += [f"- `{item['unit_class']}` — model `{item['model'] or 'unknown'}`" for item in missing_portraits] or ["- None"]
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Scanned {len(report)} map units")
    print(f"Slots: {count('slot', 'copied')} copied, {count('slot', 'existing')} already local, {count('slot', 'missing')} missing")
    print(f"Portraits: {count('portrait', 'copied')} copied, {count('portrait', 'existing')} already local, {count('portrait', 'missing')} missing in datamine")
    print(f"Role SVG icons: {sum(item['status'] == 'copied' for item in role_icons)} copied, {sum(item['status'] == 'existing' for item in role_icons)} already local, {sum(item['status'] == 'missing' for item in role_icons)} missing")
    print(f"Report: {report_path}")
    print(f"Readable report: {markdown_path}")
    print("Units missing a slot image:")
    for item in report:
        if item["slot"]["status"] == "missing":  # type: ignore[index]
            print(f"  - {item['unit_class']}")
    print("Units missing a portrait:")
    for item in report:
        portrait = item["portrait"]
        if portrait["status"] == "missing":  # type: ignore[index]
            print(f"  - {item['unit_class']} (model: {item['model'] or 'unknown'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
