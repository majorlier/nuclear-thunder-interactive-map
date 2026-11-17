"""Set the published release number: <game version>-<revision>.

The page requests its data as ``file?v=<release>`` and open tabs poll
``version.json`` for a newer release, so every published change needs a new
number:

    python tools/bump_version.py 2.61.0.25   # new game version -> 2.61.0.25-1
    python tools/bump_version.py             # same game version, next revision

``version.json`` and ``APP_VERSION`` in ``index.html`` are kept equal;
``tools/check_frontend.js`` fails when they differ.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GAME_VERSION = re.compile(r"^\d+\.\d+\.\d+\.\d+$")
RELEASE = re.compile(r"^(\d+\.\d+\.\d+\.\d+)-(\d+)$")


def current_release(root: Path = ROOT) -> str:
    return json.loads((root / "version.json").read_text(encoding="utf-8"))["version"]


def next_release(current: str, game_version: str | None = None) -> str:
    match = RELEASE.match(current)
    if not match:
        raise ValueError(f"version.json holds {current!r}, expected e.g. 2.61.0.25-1")
    current_game, revision = match.group(1), int(match.group(2))
    if game_version and not GAME_VERSION.match(game_version):
        raise ValueError(f"{game_version!r} is not a game version like 2.61.0.25")
    if game_version and game_version != current_game:
        return f"{game_version}-1"
    return f"{current_game}-{revision + 1}"


def write_release(release: str, root: Path = ROOT) -> None:
    (root / "version.json").write_text(
        json.dumps({"version": release}, indent=2) + "\n", encoding="utf-8"
    )
    page = root / "index.html"
    html = page.read_text(encoding="utf-8")
    html, count = re.subn(
        r'const APP_VERSION = "[^"]*";', f'const APP_VERSION = "{release}";', html
    )
    if count != 1:
        raise RuntimeError("index.html must contain exactly one APP_VERSION line")
    # The no-JavaScript fallback text of the "Updated" label.
    html = re.sub(
        r"""data-i18n-args='\{"version":"[^"]*"\}'>Updated [^<]*<""",
        f"""data-i18n-args='{{"version":"{release}"}}'>Updated {release}<""",
        html,
    )
    page.write_text(html, encoding="utf-8")


def bump(game_version: str | None = None, root: Path = ROOT) -> tuple[str, str]:
    previous = current_release(root)
    release = next_release(previous, game_version)
    write_release(release, root)
    return previous, release


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "game_version",
        nargs="?",
        help="Game version of the data, e.g. 2.61.0.25. Omit to publish another "
        "revision of the current game version.",
    )
    args = parser.parse_args()
    previous, release = bump(args.game_version)
    print(f"Release {previous} -> {release}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
