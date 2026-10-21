"""Extract localized weapon names for the weapons the event units carry.

``unit_specs.json`` keeps the internal weapon file names
(``250mm_fm_3000_user_cannon``) that the mission BLKX references, while
``units_weaponry.csv`` holds the readable label under ``weapons/<file name>``.
Those keys keep their original capitalisation (``weapons/250mm_FM_3000_...``),
so every lookup is case-insensitive. The ``/short`` row is used when a weapon
has no full name, and weapons without any localization row are omitted: the
browser then falls back to the identifier cleanup, so a missing key never
blanks a card.

Output shape::

    {"en": {"250mm_fm_3000_user_cannon": "FM-3000 SAM"},
     "zh-CN": {"250mm_fm_3000_user_cannon": "FM-3000 防空导弹"}}
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

LOCALE_COLUMNS = {
    "en": "<English>",
    "zh-CN": "<Chinese>",
    "zh-TW": "<TChinese>",
    "zh-HK": "<HChinese>",
}

INVISIBLE = re.compile(r"[\u200b\u200c\u200d\ufeff]")


def clean(value: str) -> str:
    text = INVISIBLE.sub("", value or "").replace("\u00a0", " ")
    return re.sub(r"\s+", " ", text).strip()


def load_rows(csv_path: Path) -> tuple[dict[str, dict[str, str]], list[str]]:
    """Return ``{lowercased key: {locale: label}}`` plus missing columns."""
    rows: dict[str, dict[str, str]] = {}
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle, delimiter=";")
        header = next(reader, [])
        columns = {}
        missing_columns = []
        for locale, column in LOCALE_COLUMNS.items():
            if column in header:
                columns[locale] = header.index(column)
            else:
                missing_columns.append(column)
        if "en" not in columns:
            raise ValueError(
                f"units_weaponry.csv has no English column: {csv_path}"
            )
        for row in reader:
            if len(row) < 2:
                continue
            key = row[0].strip().lower()
            if not key:
                continue
            entry = {}
            for locale, index in columns.items():
                if index >= len(row):
                    continue
                label = clean(row[index])
                if label:
                    entry[locale] = label
            if entry:
                rows[key] = entry
    return rows, missing_columns


def weapon_names(specs: dict) -> list[str]:
    names = set()
    for spec in specs.values():
        if not isinstance(spec, dict):
            continue
        for weapon in spec.get("weapons") or []:
            if isinstance(weapon, dict) and weapon.get("name"):
                names.add(str(weapon["name"]).strip().lower())
    return sorted(names)


def build_tables(rows: dict[str, dict[str, str]],
                 names: list[str]) -> tuple[dict[str, dict[str, str]], list[str]]:
    output: dict[str, dict[str, str]] = {}
    missing = []
    for name in names:
        full = rows.get(f"weapons/{name}", {})
        short = rows.get(f"weapons/{name}/short", {})
        resolved = {
            locale: label
            for locale, label in (
                (locale, full.get(locale) or short.get(locale))
                for locale in LOCALE_COLUMNS
            )
            if label
        }
        if not resolved:
            missing.append(name)
            continue
        for locale, label in resolved.items():
            output.setdefault(locale, {})[name] = label
    return output, missing


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weaponry-csv", type=Path, required=True,
                        help="Path to lang.vromfs.bin_u/lang/units_weaponry.csv")
    parser.add_argument("--specs", type=Path, default=ROOT / "unit_specs.json")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "unit_weapon_display_names.json")
    args = parser.parse_args()

    rows, missing_columns = load_rows(args.weaponry_csv)
    specs = json.loads(args.specs.read_text(encoding="utf-8-sig"))
    names = weapon_names(specs)
    output, missing = build_tables(rows, names)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    counts = ", ".join(
        f"{locale}={len(table)}" for locale, table in sorted(output.items())
    )
    print(f"Wrote {counts} for {len(names)} weapons to {args.output}")
    if missing_columns:
        print(f"Missing language columns: {', '.join(missing_columns)}")
    if missing:
        print(f"Fell back to identifiers for {len(missing)} weapons: "
              f"{', '.join(missing)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
