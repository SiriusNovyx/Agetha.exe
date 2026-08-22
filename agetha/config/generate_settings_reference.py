"""Render the checked-in mechanical reference for canonical setting specs."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from agetha.config.schema import SETTING_SPECS, SettingKind


DEFAULT_OUTPUT = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "generated"
    / "settings_reference.md"
)


def _constraint(spec) -> str:
    if spec.kind is SettingKind.ENUM:
        return ", ".join(f"`{choice}`" for choice in sorted(spec.choices))
    if spec.minimum is not None:
        return f"{spec.minimum} .. {spec.maximum}"
    return "-"


def render_settings_reference() -> str:
    lines = [
        "# Generated stable settings reference",
        "",
        "> Generated from `agetha.config.schema.SETTING_SPECS`. Do not edit",
        "> this file by hand; run `python -m agetha.config.generate_settings_reference`.",
        "",
        "This is the conservative canonical subset only. Settings with special",
        "transactional, secret, or security semantics remain explicitly implemented.",
        "",
        "| Setting | Default | Kind | Constraint | Group | Restart required |",
        "|---|---|---|---|---|---|",
    ]
    for key in sorted(SETTING_SPECS):
        spec = SETTING_SPECS[key]
        lines.append(
            f"| `{key}` | `{spec.default}` | {spec.kind.value} | {_constraint(spec)} | "
            f"{spec.group} | {'yes' if spec.restart_required else 'no'} |"
        )
    return "\n".join(lines) + "\n"


def settings_reference_matches(path: Path = DEFAULT_OUTPUT) -> bool:
    try:
        return path.read_text(encoding="utf-8") == render_settings_reference()
    except OSError:
        return False


def write_settings_reference(path: Path = DEFAULT_OUTPUT) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_settings_reference(), encoding="utf-8", newline="\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    if args.check:
        if settings_reference_matches(args.output):
            return 0
        print(f"stale generated settings reference: {args.output}")
        return 1
    write_settings_reference(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
