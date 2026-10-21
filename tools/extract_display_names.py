"""Extract in-game display names for every language the client ships.

The mission files use internal unit identifiers (for example
``us_ai_sam_launcher_m192``), while the client gets readable labels from
``lang/units.csv``. The ``_0`` row is the full name, ``_1`` the short name,
``_2`` the class/category and ``_shop`` the store label; the map uses ``_0``
and falls back to ``_shop``.

``units.csv`` carries one column per language, so the same pass exports every
locale instead of only English. Chinese values separate words with zero-width
spaces, which would leak into sorting and copy/paste, so invisible characters
are stripped before the JSON is written. Locales and units without a row are
simply omitted; the browser then falls back to the English table and finally
to the identifier cleanup, which keeps event-only units readable.

Output shape::

    {"en": {"cn_hq_11": "HQ11"}, "zh-CN": {"cn_hq_11": "红旗-11"}}
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Language column in ``units.csv`` -> locale id used by i18n/<locale>.json.
LOCALE_COLUMNS = {
    "en": "<English>",
    "zh-CN": "<Chinese>",
    "zh-TW": "<TChinese>",
    "zh-HK": "<HChinese>",
}

INVISIBLE = re.compile(r"[\u200b\u200c\u200d\ufeff]")


def clean(value: str) -> str:
    """Normalize localization whitespace without changing the display text."""
    text = INVISIBLE.sub("", value or "").replace("\u00a0", " ")
    return re.sub(r"\s+", " ", text).strip()


def load_names(csv_path: Path) -> tuple[dict[str, dict[str, str]], list[str]]:
    """Return ``{locale: {key: label}}`` plus any language column we lost."""
    tables: dict[str, dict[str, str]] = {}
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
            raise ValueError(f"units.csv has no English column: {csv_path}")
        tables = {locale: {} for locale in columns}
        for row in reader:
            if len(row) < 2:
                continue
            key = row[0].strip().lower()
            if not key:
                continue
            for locale, index in columns.items():
                if index >= len(row):
                    continue
                label = clean(row[index])
                if label:
                    tables[locale][key] = label
    return tables, missing_columns


def lookup(names: dict[str, str], unit_id: str,
           suffixes: tuple[str, ...]) -> str | None:
    raw = str(unit_id or "").replace("\\", "/").strip().lower()
    if not raw:
        return None
    tail = raw.rsplit("/", 1)[-1]
    for suffix in suffixes:
        for value in (raw, tail):
            label = names.get(f"{value}_{suffix}")
            if label:
                return label
    return None


def build_table(specs: dict, names: dict[str, str]) -> dict[str, str]:
    """Full names for every unit, plus ``#short`` rows where they differ.

    The site popup lists units as compact buttons, so it reads the ``_1`` short
    name; the unit card behind it keeps the full ``_0`` name. Rows where the
    client ships no shorter label are omitted, which makes the browser fall back
    to the full name instead of duplicating it in the JSON.
    """
    output: dict[str, str] = {}
    for key, spec in sorted(specs.items()):
        if not isinstance(spec, dict):
            continue
        unit_id = spec.get("unit_class") or key
        full = lookup(names, unit_id, ("0", "shop"))
        if not full:
            continue
        index = str(key).lower()
        tail = str(key).replace("\\", "/").rsplit("/", 1)[-1].lower()
        output[index] = full
        output.setdefault(tail, full)
        short = lookup(names, unit_id, ("1", "shop"))
        if short and short != full:
            output[f"{index}#short"] = short
            output.setdefault(f"{tail}#short", short)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--units-csv", type=Path, required=True,
                        help="Path to lang.vromfs.bin_u/lang/units.csv")
    parser.add_argument("--specs", type=Path, default=ROOT / "unit_specs.json")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "unit_display_names.json")
    args = parser.parse_args()

    names, missing_columns = load_names(args.units_csv)
    specs = json.loads(args.specs.read_text(encoding="utf-8-sig"))

    output: dict[str, dict[str, str]] = {}
    for locale, table in names.items():
        built = build_table(specs, table)
        if built:
            output[locale] = built

    english = output.get("en", {})
    missing_units = [
        key for key, spec in sorted(specs.items())
        if isinstance(spec, dict) and str(key).lower() not in english
    ]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    counts = ", ".join(f"{locale}={len(table)}" for locale, table in output.items())
    print(f"Wrote {counts} for {len(specs)} units to {args.output}")
    if missing_columns:
        print(f"Missing language columns: {', '.join(missing_columns)}")
    if missing_units:
        print(f"Missing {len(missing_units)} unit rows: {', '.join(missing_units)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
