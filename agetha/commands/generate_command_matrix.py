"""Render the checked-in mechanical command policy reference."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from agetha.commands.specs import COMMAND_SPECS


DEFAULT_OUTPUT = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "generated"
    / "command_matrix.md"
)

_ORIGIN_ORDER = (
    "user",
    "touch",
    "file_drop",
    "reminder",
    "ambient",
    "tool_result",
    "terminal_sentinel",
)


def _format_origins(origins: frozenset[str]) -> str:
    return ", ".join(origin for origin in _ORIGIN_ORDER if origin in origins)


def _format_gates(gates: tuple[str, ...]) -> str:
    return ", ".join(f"`{gate}`" for gate in gates) if gates else "-"


def render_command_matrix() -> str:
    """Return deterministic Markdown derived only from the command registry."""
    lines = [
        "# Generated command matrix",
        "",
        "> Generated from `agetha.commands.specs.COMMAND_SPECS`. Do not edit",
        "> this file by hand; run `python -m agetha.commands.generate_command_matrix`.",
        "",
        "This reference contains static policy facts only. Dynamic target, process,",
        "generation, confirmation, and effect-time decisions remain in their runtime owners.",
        "",
        "| Command | Base risk | Capability | Execution required | Allowed origins | Dispatch | Handler | Command-specific feature gates |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name in sorted(COMMAND_SPECS):
        spec = COMMAND_SPECS[name]
        handler = f"`{spec.handler_key}`" if spec.handler_key else "-"
        lines.append(
            f"| `{name}` | {spec.base_risk.value} | {spec.capability.value} | "
            f"{'yes' if spec.requires_execution else 'no'} | "
            f"{_format_origins(spec.allowed_origins)} | {spec.dispatch_kind.value} | "
            f"{handler} | {_format_gates(spec.feature_gates)} |"
        )
    return "\n".join(lines) + "\n"


def command_matrix_matches(path: Path = DEFAULT_OUTPUT) -> bool:
    """Return whether a checked-in matrix equals a fresh registry render."""
    try:
        return path.read_text(encoding="utf-8") == render_command_matrix()
    except OSError:
        return False


def write_command_matrix(path: Path = DEFAULT_OUTPUT) -> None:
    """Write the deterministic reference to an explicit source-tree path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_command_matrix(), encoding="utf-8", newline="\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the checked-in command matrix differs from COMMAND_SPECS",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="matrix path (defaults to docs/generated/command_matrix.md)",
    )
    args = parser.parse_args(argv)
    if args.check:
        if command_matrix_matches(args.output):
            return 0
        print(f"stale generated command matrix: {args.output}")
        return 1
    write_command_matrix(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
