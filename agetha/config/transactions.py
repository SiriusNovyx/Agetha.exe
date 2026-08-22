"""Pure structural operations for user-readable config documents."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping


_CONFIG_DOCUMENT_LINE_RE = re.compile(
    r"^(?P<prefix>\s*)(?P<key>[A-Za-z0-9_]+)(?P<separator>\s*=\s*)(?P<value>.*)$"
)


def normalise_config_updates(
    updates: Mapping[str, object],
    *,
    is_secret_key: Callable[[str], bool],
    on_secret_rejected: Callable[[str], None] | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Validate structural updates without parsing or normalising values."""
    clean: dict[str, str] = {}
    failed: list[str] = []
    for raw_key, raw_value in updates.items():
        key = str(raw_key).strip().upper()
        if not key or not key.replace("_", "").isalnum():
            failed.append(key or "<empty>")
            continue
        if is_secret_key(key):
            if on_secret_rejected is not None:
                on_secret_rejected(key)
            failed.append(key)
            continue
        value = str(raw_value)
        if "\r" in value or "\n" in value:
            failed.append(key)
            continue
        clean[key] = value
    return clean, failed


def render_config_document(
    text: str,
    updates: Mapping[str, object] | None = None,
    remove_keys: Iterable[str] = (),
    *,
    is_secret_key: Callable[[str], bool],
    on_secret_rejected: Callable[[str], None] | None = None,
) -> str:
    """Patch config text while retaining comments, order, blanks, and unknowns."""
    clean, failed = normalise_config_updates(
        updates or {},
        is_secret_key=is_secret_key,
        on_secret_rejected=on_secret_rejected,
    )
    if failed:
        raise ValueError(f"Invalid or forbidden config keys: {', '.join(failed)}")

    removals: set[str] = set()
    for raw_key in remove_keys:
        key = str(raw_key).strip().upper()
        if not key or not key.replace("_", "").isalnum():
            raise ValueError(f"Invalid config key for removal: {raw_key!r}")
        removals.add(key)
    removals.difference_update(clean)

    newline = "\r\n" if "\r\n" in text else "\n"
    seen: set[str] = set()
    output: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.endswith("\r\n"):
            body, ending = line[:-2], "\r\n"
        elif line.endswith(("\n", "\r")):
            body, ending = line[:-1], line[-1]
        else:
            body, ending = line, ""
        match = _CONFIG_DOCUMENT_LINE_RE.match(body)
        if not match:
            output.append(line)
            continue
        key = match.group("key").upper()
        if key in removals:
            continue
        if key not in clean:
            output.append(line)
            continue
        output.append(
            f"{match.group('prefix')}{match.group('key')}"
            f"{match.group('separator')}{clean[key]}{ending}"
        )
        seen.add(key)

    rendered = "".join(output)
    missing = [(key, value) for key, value in clean.items() if key not in seen]
    if missing:
        if rendered and not rendered.endswith(("\n", "\r")):
            rendered += newline
        rendered += "".join(f"{key} = {value}{newline}" for key, value in missing)
    return rendered
